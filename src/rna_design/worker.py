"""Isolated real-model worker; JSON lines on stdout, upstream messages on stderr.

Executed by its file path under the pinned research Python, without importing the CLI.
No alternate sampler, synthetic predictions, model edits, or reference logit bias.
"""

import contextlib
import json
import os
import random
import resource
import sys
import time
import traceback
from pathlib import Path


class Models:
    def __init__(self, root, device, threads):
        source = root / "sources" / "grnade"
        sys.path.insert(0, str(source))
        os.environ.update(
            PROJECT_PATH=str(source),
            DATA_PATH=str(source / "data"),
            X3DNA=str(source / "unused-x3dna"),
            ETERNAFOLD=str(source / "unused-eternafold"),
            WANDB_MODE="disabled",
        )
        import torch

        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; no implicit device fallback")
        self.torch = torch
        self.device = device
        self.weights = root / "checkpoints"
        self.source = source
        self.generator = self.rnet = self.ss = None

    def check(self):
        import importlib.metadata

        from src.data.featurizer import RNAGraphFeaturizer  # noqa: F401
        from src.models import gRNAde  # noqa: F401
        from tools.ribonanzanet.network import RibonanzaNet  # noqa: F401
        from tools.ribonanzanet_sec_struct.network import RibonanzaNetSS  # noqa: F401

        return {
            "imports": "ok",
            "device": self.device,
            "packages": {
                key: importlib.metadata.version(key)
                for key in [
                    "torch",
                    "torch-geometric",
                    "torch-cluster",
                    "torch-scatter",
                    "numpy",
                    "scipy",
                    "arnie",
                    "biotite",
                    "einops",
                ]
            },
        }

    def generate(self, request):
        import numpy as np
        from src.constants import FILL_VALUE, NUM_TO_LETTER
        from src.data.featurizer import RNAGraphFeaturizer
        from src.models import gRNAde

        torch = self.torch
        if self.generator is None:
            self.generator = gRNAde()
            self.generator.load_state_dict(
                torch.load(
                    self.weights / "gRNAde_drop3d@0.75_maxlen@500.h5",
                    map_location="cpu",
                    weights_only=True,
                )
            )
            self.generator.to(self.device).eval()
        seed = request["seed"]
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if self.device == "cuda":
            torch.cuda.manual_seed_all(seed)
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
        target = request["target"]
        featurizer = RNAGraphFeaturizer(split="test_2d", radius=0.0)
        raw = {
            "sequence": request["sequence"],
            "coords_list": [torch.full((len(target), 3, 3), FILL_VALUE)],
            "sec_struct_list": [target],
        }
        graph = featurizer(raw).to(self.device)
        if not bool(graph.mask_seq.all()) or graph.node_s.shape[0] != len(target):
            raise ValueError("Upstream graph changed the full construct length")
        with torch.inference_mode():
            samples, logits = self.generator.sample(
                graph, request["count"], request["temperature"], return_logits=True
            )
            perplexity = (
                torch.nn.functional.cross_entropy(
                    logits.reshape(-1, 4), samples.reshape(-1).long(), reduction="none"
                )
                .reshape(request["count"], len(target))
                .mean(1)
                .exp()
                .cpu()
                .tolist()
            )
        return {
            "target_consumed": target,
            "mode": "test_2d",
            "logit_bias": None,
            "proposals": [
                {
                    "sequence": "".join(NUM_TO_LETTER[int(x)] for x in seq),
                    "native": {"grnade_perplexity": perp},
                }
                for seq, perp in zip(samples.cpu().tolist(), perplexity)
            ],
        }

    def reactivity(self, request):
        from tools.ribonanzanet.network import RibonanzaNet

        if self.rnet is None:
            self.rnet = RibonanzaNet(
                str(self.source / "tools/ribonanzanet/config.yaml"),
                str(self.weights / "ribonanzanet/ribonanzanet.pt"),
                self.device,
            )
            self.rnet.to(self.device).eval()
        with self.torch.inference_mode():
            values = self.rnet.predict([request["sequence"]]).cpu().numpy()
        expected = (1, len(request["sequence"]), 2)
        if values.shape != expected:
            raise ValueError(f"Unexpected RNet output shape {values.shape}, expected {expected}")
        return {"channels": values[0].tolist(), "channel_names": ["2A3", "DMS"]}

    def structure(self, request):
        from tools.ribonanzanet_sec_struct.network import RibonanzaNetSS

        if self.ss is None:
            self.ss = RibonanzaNetSS(
                str(self.source / "tools/ribonanzanet_sec_struct/config.yaml"),
                str(self.weights / "ribonanzanet_sec_struct/ribonanzanet_ss.pt"),
                self.device,
            )
            self.ss.to(self.device).eval()
        with self.torch.inference_mode():
            probabilities, structures = self.ss.predict([request["sequence"]])
        if probabilities.shape != (1, len(request["sequence"]), len(request["sequence"])):
            raise ValueError("RNet-SS contact output length mismatch")
        import numpy as np

        if not np.isfinite(probabilities).all():
            raise ValueError("Nonfinite RNet-SS probabilities")
        # Full matrix can be reproduced from pinned sequence/weights/decoder; save decoded structure.
        return {
            "dot_bracket": structures[0],
            "decoder": "hungarian",
            "theta": 0.5,
            "min_len_helix": 1,
            "mask_distance_lt": 4,
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
                result = models.check() if action == "check" else getattr(models, action)(request)
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
