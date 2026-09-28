"""Pinned upstream Struct2SeQ sampler, isolated from the gRNAde/evaluator environment."""

import contextlib
import importlib.metadata
import json
import random
import resource
import sys
import time
import traceback
from pathlib import Path


class Models:
    def __init__(self, root, device, threads):
        sys.path.insert(0, str(root / "sources/struct2seq"))
        # Upstream Functions imports Arnie even though generation does not fold RNA.
        # The process already runs in a private temporary directory.
        Path("arnie_file.txt").write_text("linearpartition: .\nTMP: .\n")
        import torch

        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; no implicit device fallback")
        self.torch = torch
        self.device = device
        self.weights = root / "checkpoints/struct2seq/Struct2SeQ_SHAPE.pt"
        self.generator = None

    def check(self):
        from Dataset import tokenize_dot_bracket  # noqa: F401
        from Encoder_Decoder import DotBracketRNATransformer  # noqa: F401
        from Functions import generate_sequence_batched_sample  # noqa: F401

        return {
            "imports": "ok",
            "device": self.device,
            "packages": {
                name: importlib.metadata.version(name)
                for name in ["torch", "numpy", "polars", "PyYAML", "arnie", "scipy"]
            },
        }

    def generate(self, request):
        import numpy as np
        from Dataset import tokenize_dot_bracket
        from Encoder_Decoder import DotBracketRNATransformer
        from Functions import delete_modules, generate_sequence_batched_sample

        torch = self.torch
        if self.generator is None:
            self.generator = DotBracketRNATransformer(100, 5, 384, 16, 6, 6, 1536, 0.1)
            # Official loader removes unused template-layer parameters before strict loading.
            delete_modules(self.generator)
            self.generator.load_state_dict(
                torch.load(self.weights, map_location="cpu", weights_only=True), strict=True
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
        tokens = tokenize_dot_bracket(target)
        count = request["count"]
        if len(tokens) != len(target) or len(target) != len(request["sequence"]):
            raise ValueError("Struct2SeQ tokenizer changed the full construct length")
        src = torch.tensor([tokens] * count, dtype=torch.long, device=self.device)
        contact = torch.eye(len(target), dtype=torch.float32, device=self.device)
        correspondence = {}
        for left, right in request["target_pairs"]:
            i, j = left - 1, right - 1
            if not 0 <= i < j < len(target) or i in correspondence or j in correspondence:
                raise ValueError("Invalid 1-based target pair coordinates")
            correspondence[i], correspondence[j] = j, i
            contact[i, j] = contact[j, i] = 1
        contact = contact.unsqueeze(0).repeat(count, 1, 1)
        samples = generate_sequence_batched_sample(
            self.generator,
            src,
            [correspondence] * count,
            contact,
            p=request["sampling_probability"],
            max_len=len(target),
            weight=None,
            bias=None,
        ).cpu()
        if tuple(samples.shape) != (count, len(target)):
            raise ValueError("Struct2SeQ returned an unexpected shape")
        return {
            "target_consumed": target,
            "target_pairs_consumed": request["target_pairs"],
            "mode": "native_stochastic",
            "logit_bias": None,
            "internal_search_effort": {
                "sampled_sequences": count,
                "autoregressive_positions_per_sequence": len(target),
                "search_rounds": 0,
                "rescue_proposals": 0,
                "generation_scorer_calls": 0,
            },
            "proposals": [
                {"sequence": "".join("ACGU"[int(token)] for token in row), "native": {}}
                for row in samples.tolist()
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
                    raise ValueError(f"Unsupported Struct2SeQ action: {action}")
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
