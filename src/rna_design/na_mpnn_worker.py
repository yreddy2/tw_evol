"""Official NA-MPNN design checkpoint with canonical RNA-only sampling on a mapped chain."""

import contextlib
import importlib.metadata
import json
import random
import resource
import sys
import time
import traceback
from pathlib import Path

ATOMS = [
    "N",
    "CA",
    "C",
    "O",
    "OP1",
    "OP2",
    "P",
    "O5'",
    "C5'",
    "C4'",
    "O4'",
    "C3'",
    "O3'",
    "C2'",
    "O2'",
    "C1'",
]
RESTYPES = "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL UNK DA DC DG DT DX A C G U RX MAS PAD".split()
POLYTYPES = "PP DNA RNA UNK MAS PAD".split()
RNA_TOKENS = {21: "A", 22: "C", 23: "G", 24: "U"}


def decode_tokens(tokens):
    if any(token not in RNA_TOKENS for token in tokens):
        raise ValueError("NA-MPNN returned a noncanonical/shared-RNA token")
    return "".join(RNA_TOKENS[token] for token in tokens)


def backbone_features(backbone, device):
    """Preserve author coordinates/numbers; use the unmodified upstream featurizer.

    The selected single canonical RNA chain has no protein, DNA or ligand features.
    Empty atom slots retain upstream zero coordinates AND zero validity masks.
    """
    import torch
    from data_utils import featurize

    residues = backbone["residues"]
    length = len(residues)
    xyz = torch.zeros((length, 65, 3), dtype=torch.float32, device=device)
    atom_mask = torch.zeros((length, 65), dtype=torch.int32, device=device)
    for i, residue in enumerate(residues):
        for name, position in residue["atoms"].items():
            index = ATOMS.index(name)
            xyz[i, index] = torch.tensor(position, device=device)
            atom_mask[i, index] = 1
    ones = torch.ones(length, dtype=torch.int32, device=device)
    zeros = torch.zeros_like(ones)
    data = {
        "X": xyz[:, :16],
        "X_m": atom_mask[:, :16],
        "xyz_65": xyz,
        "xyz_65_m": atom_mask,
        "R_idx": torch.tensor(
            [r["residue_number"] for r in residues], dtype=torch.int32, device=device
        ),
        "chain_labels": zeros,
        "S": torch.tensor(
            [21 + "ACGU".index(r["base"]) for r in residues], dtype=torch.int32, device=device
        ),
        "mask": ones,
        "chain_mask": ones,
        "protein_mask": zeros,
        "dna_mask": zeros,
        "rna_mask": ones,
        "rna_mask_for_token_conversion": ones,
        "R_polymer_type": torch.full((length,), 2, dtype=torch.int64, device=device),
    }
    return featurize(data)


class Models:
    def __init__(self, root, device, threads):
        sys.path.insert(0, str(root / "sources/na_mpnn/inference"))
        import torch

        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; no implicit device fallback")
        self.torch, self.device = torch, device
        self.weights = root / "checkpoints/na_mpnn/s_19137.pt"
        self.generator = None

    def check(self):
        from data_utils import featurize  # noqa: F401
        from model_utils import ProteinMPNN  # noqa: F401

        return {
            "imports": "ok",
            "device": self.device,
            "packages": {
                name: importlib.metadata.version(name)
                for name in ["torch", "numpy", "prody", "biopython"]
            },
        }

    def generate(self, request):
        import numpy as np
        from data_utils import get_score
        from model_utils import ProteinMPNN

        torch = self.torch
        backbone = request["backbone"]
        if not backbone["compatible"] or backbone["sequence"] != request["sequence"]:
            raise ValueError("NA-MPNN requires a validated exact sequence-to-backbone mapping")
        if self.generator is None:
            self.generator = ProteinMPNN(
                node_features=128,
                edge_features=128,
                hidden_dim=128,
                num_encoder_layers=3,
                num_decoder_layers=3,
                k_neighbors=32,
                model_type="na_mpnn",
                vocab=33,
                num_letters=33,
                atom_dict={a: i for i, a in enumerate(ATOMS)},
                restype_to_int={a: i for i, a in enumerate(RESTYPES)},
                polytype_to_int={a: i for i, a in enumerate(POLYTYPES)},
            )
            checkpoint = torch.load(self.weights, map_location="cpu", weights_only=True)
            self.generator.load_state_dict(checkpoint["model_state_dict"], strict=True)
            self.generator.to(self.device).eval()
        seed, count = request["seed"], request["count"]
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if self.device == "cuda":
            torch.cuda.manual_seed_all(seed)
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
        features = backbone_features(backbone, self.device)
        length = len(request["sequence"])
        features.update(
            batch_size=count,
            temperature=request["temperature"],
            symmetry_residues=[[]],
            symmetry_weights=[[]],
        )
        bias = torch.full((1, length, 33), -1e8, device=self.device)
        bias[:, :, 21:25] = 0
        features["bias"] = bias
        features["randn"] = torch.randn((count, length), device=self.device)
        with torch.inference_mode():
            output = self.generator.sample(features)
            loss, _ = get_score(
                output["S"], output["log_probs"], features["mask"] * features["chain_mask"], 33
            )
        if tuple(output["S"].shape) != (count, length):
            raise ValueError("NA-MPNN returned an unexpected sequence shape")
        return {
            "target_consumed": None,
            "backbone_consumed_id": backbone["id"],
            "mode": "fixed_backbone",
            "conditioning": "selected RNA chain backbone only",
            "internal_search_effort": {
                "sampled_sequences": count,
                "autoregressive_positions_per_sequence": length,
                "search_rounds": 0,
                "rescue_proposals": 0,
                "generation_scorer_calls": 0,
            },
            "proposals": [
                {
                    "sequence": decode_tokens(row),
                    "native": {
                        "negative_log_likelihood": nll,
                        "token_encoding": "upstream na_shared_tokens: 21=A,22=C,23=G,24=U",
                    },
                }
                for row, nll in zip(output["S"].cpu().tolist(), loss.cpu().tolist())
            ],
        }


def main():
    models = None
    for line in sys.stdin:
        start = time.monotonic()
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if models is None:
                    models = Models(Path(request["root"]), request["device"], request["threads"])
                action = request["action"]
                if action not in {"check", "generate"}:
                    raise ValueError(f"Unsupported NA-MPNN action: {action}")
                result = models.check() if action == "check" else models.generate(request)
            response = {"ok": True, "result": result}
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            response = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        response["seconds"] = time.monotonic() - start
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        response["peak_rss_bytes"] = peak if sys.platform == "darwin" else peak * 1024
        print(json.dumps(response, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
