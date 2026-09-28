import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from . import __version__
from .config import (
    AllModelsConfig,
    BackboneConfig,
    BothModelsConfig,
    BudgetConfig,
    Experiment,
    InputConfig,
    NaMPNNConfig,
    ReconstructionConfig,
    RuntimeConfig,
    SelectionConfig,
    Struct2SeqConfig,
    load_config,
)
from .errors import DesignError
from .pipeline import create_run, execute, validate
from .reporting import collect, export
from .runtime import LOCK, doctor, local_verification
from .selection import select
from .storage import atomic_write, event, immutable_json, load_manifest, run_lock


def parser():
    root = argparse.ArgumentParser(
        prog="rna-design", description="Local RNA design with gRNAde, Struct2SeQ and NA-MPNN"
    )
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    commands = root.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    def experiment(p):
        p.add_argument("--config", type=Path, help="YAML paths resolve relative to this file")
        p.add_argument("--sequence", type=Path, help="Raw canonical RNA or single-record FASTA")
        p.add_argument("--target", type=Path, help="Optional extended dot-bracket target")
        p.add_argument("--runtime-root", type=Path)
        p.add_argument("--device", choices=["cpu", "cuda"])
        p.add_argument("--model", choices=["grnade", "struct2seq", "na_mpnn", "both", "all"])
        p.add_argument("--backbone", type=Path, help="PDB/mmCIF backbone for NA-MPNN")
        p.add_argument("--chain", help="Explicit backbone chain ID")
        p.add_argument(
            "--mapping", choices=["exact_sequence"], help="Explicit backbone mapping strategy"
        )
        p.add_argument("--missing-atoms", choices=["error", "mask_phosphate"], default="error")
        p.add_argument(
            "--design-sequence", type=Path, help="Shorter design input for explicit reconstruction"
        )
        p.add_argument(
            "--reconstruction-map", type=Path, help="JSON mapping design positions into --sequence"
        )
        p.add_argument(
            "--proposals", type=int, help="Returned proposals per model (all: three times total)"
        )
        p.add_argument("--batch-size", type=int)
        p.add_argument("--seed", type=int)
        p.add_argument("--top-k", type=int, help="Maximum selected from the combined unique pool")
        p.add_argument("--max-c-percent", type=float)
        p.add_argument("--wall-time-seconds", type=float)
        p.add_argument(
            "--smoke", action="store_true", help="10 proposals per model, 10 selected total"
        )
        common(p)

    init = commands.add_parser("init", help="Write an editable experiment configuration")
    experiment(init)
    init.add_argument("--output", type=Path, default=Path("experiment.yaml"))
    check = commands.add_parser(
        "validate", help="Validate inputs without model imports or downloads"
    )
    experiment(check)
    run = commands.add_parser("run", help="Generate, score, and export a bounded campaign")
    experiment(run)
    run.add_argument("--out", type=Path)
    run.add_argument("--dry-run", action="store_true", help="Show plan without inference or writes")
    models = commands.add_parser("models", help="Inspect pinned model capabilities")
    models.add_argument("action", choices=["list"])
    models.add_argument("--runtime-root", type=Path)
    common(models)
    diagnostics = commands.add_parser("doctor", help="Check source, weights, and isolated runtime")
    diagnostics.add_argument("--config", type=Path)
    diagnostics.add_argument("--runtime-root", type=Path)
    diagnostics.add_argument("--device", choices=["cpu", "cuda"])
    diagnostics.add_argument("--model", choices=["grnade", "struct2seq", "na_mpnn", "both", "all"])
    diagnostics.add_argument(
        "--smoke", action="store_true", help="Run 10 real proposals; requires --config and --out"
    )
    diagnostics.add_argument("--out", type=Path)
    common(diagnostics)
    inspect = commands.add_parser(
        "inspect-backbone", help="Inspect exact RNA sequence and backbone coverage"
    )
    inspect.add_argument("backbone", type=Path)
    inspect.add_argument("--chain", required=True)
    inspect.add_argument("--sequence", type=Path)
    inspect.add_argument("--missing-atoms", choices=["error", "mask_phosphate"], default="error")
    inspect.add_argument("--out", type=Path)
    common(inspect)
    for name in ("status", "resume"):
        p = commands.add_parser(name)
        p.add_argument("run_dir", type=Path)
        common(p)
    choose = commands.add_parser("select", help="Create an immutable selection using saved scores")
    choose.add_argument("run_dir", type=Path)
    choose.add_argument("--top-k", type=int)
    choose.add_argument("--max-c-percent", type=float)
    choose.add_argument("--min-openknot-score", type=float)
    choose.add_argument("--min-pair-f1", type=float)
    choose.add_argument("--out", required=True, type=Path)
    common(choose)
    output = commands.add_parser("export", help="Export saved results without any model runtime")
    output.add_argument("run_dir", type=Path)
    output.add_argument("--out", required=True, type=Path)
    output.add_argument("--formats", default="fasta,csv,json")
    common(output)
    return root


def resolve(args):
    if args.config:
        if (
            args.sequence
            or args.target
            or args.backbone
            or args.chain
            or args.mapping
            or args.design_sequence
            or args.reconstruction_map
            or args.missing_atoms != "error"
        ):
            raise DesignError("Use --config or explicit input flags, not both")
        config = load_config(args.config.resolve())
    else:
        if not args.sequence:
            raise DesignError("Supply --sequence or --config")
        config = Experiment(
            input=InputConfig(
                sequence=args.sequence.resolve(),
                target=args.target.resolve() if args.target else None,
            )
        )
        config.runtime.root = Path(os.environ.get("RNA_DESIGN_RUNTIME", ".runtime")).resolve()
        if args.backbone or args.chain or args.mapping or args.missing_atoms != "error":
            if not all([args.backbone, args.chain, args.mapping]):
                raise DesignError("Backbone input requires --backbone, --chain and --mapping")
            config.input.backbone = BackboneConfig(
                path=args.backbone.resolve(),
                chain=args.chain,
                mapping=args.mapping,
                missing_atoms=args.missing_atoms,
            )
        if args.design_sequence or args.reconstruction_map:
            if not all([args.design_sequence, args.reconstruction_map]):
                raise DesignError(
                    "Reconstruction requires --design-sequence and --reconstruction-map"
                )
            config.input.reconstruction = ReconstructionConfig(
                design_sequence=args.design_sequence.resolve(),
                mapping=args.reconstruction_map.resolve(),
            )
    data = config.model_dump(mode="json")
    if args.model and args.model != config.model.name:
        data["model"] = {"name": args.model}
    if args.smoke:
        data["budget"].update(proposals=10, attempt_cap=20)
        data["selection"]["top_k"] = 10
    for arg, section, key in [
        ("proposals", "budget", "proposals"),
        ("seed", "budget", "seed"),
        ("batch_size", "budget", "batch_size"),
        ("device", "runtime", "device"),
        ("top_k", "selection", "top_k"),
        ("max_c_percent", "selection", "max_c_percent"),
        ("wall_time_seconds", "budget", "wall_time_seconds"),
    ]:
        if getattr(args, arg, None) is not None:
            data[section][key] = getattr(args, arg)
    if args.proposals is not None:
        data["budget"]["attempt_cap"] = 2 * args.proposals
    if args.runtime_root:
        data["runtime"]["root"] = str(args.runtime_root.resolve())
    return Experiment.model_validate(data)


def summary(manifest, directory):
    return {
        "run": str(directory.resolve()),
        "status": manifest["status"],
        "counts": manifest["counts"],
        "elapsed_seconds": manifest["elapsed_seconds"],
        "peak_rss_bytes": manifest["peak_rss_bytes"],
        "error": manifest.get("error"),
        "report": str((directory / "exports/report.md").resolve()),
        "ranked_csv": str((directory / "exports/ranked.csv").resolve()),
    }


def dispatch(args):
    if args.command == "inspect-backbone":
        from .backbone import inspect_backbone
        from .inputs import read_sequence

        sequence = read_sequence(args.sequence)[1] if args.sequence else None
        report = inspect_backbone(args.backbone.resolve(), args.chain, sequence, args.missing_atoms)
        if args.out:
            if args.out.exists():
                raise DesignError(f"Refusing to overwrite {args.out}")
            atomic_write(args.out, json.dumps(report, indent=2) + "\n")
        return report, 0 if report["compatible"] else 3
    if args.command == "models":
        from .na_mpnn_runtime import LOCK as NA_MPNN_LOCK
        from .na_mpnn_runtime import local_verification as na_mpnn_verification
        from .struct2seq_runtime import LOCK as STRUCT2SEQ_LOCK
        from .struct2seq_runtime import local_verification as struct2seq_verification

        root = args.runtime_root or Path(os.environ.get("RNA_DESIGN_RUNTIME", ".runtime"))
        verified = local_verification(root.resolve())
        second_verified = struct2seq_verification(root.resolve())
        third_verified = na_mpnn_verification(root.resolve())
        return {
            "generator": "grnade",
            "mode": "2d",
            "evaluators": ["rnet", "rnet_ss"],
            **LOCK,
            "local_verification": verified,
            "tested_lengths": verified["tested_lengths"],
            "generators": [
                {
                    "name": "grnade",
                    "mode": "2d",
                    **LOCK,
                    "local_verification": verified,
                    "tested_lengths": verified["tested_lengths"],
                },
                {
                    "name": "struct2seq",
                    **STRUCT2SEQ_LOCK,
                    "local_verification": second_verified,
                    "tested_lengths": second_verified["tested_lengths"],
                },
                {
                    "name": "na_mpnn",
                    **NA_MPNN_LOCK,
                    "local_verification": third_verified,
                    "tested_lengths": third_verified["tested_lengths"],
                },
            ],
        }, 0
    if args.command in {"init", "validate", "run"}:
        config = resolve(args)
        plan = validate(config)
        if args.command == "init":
            if args.output.exists():
                raise DesignError(f"Refusing to overwrite {args.output}")
            atomic_write(
                args.output, yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False)
            )
            return {"config": str(args.output.resolve())}, 0
        if args.command == "validate" or args.dry_run:
            return {
                "valid": True,
                "inference_performed": False,
                **plan,
                "out": str(args.out.resolve()) if getattr(args, "out", None) else None,
                "note": "Target prediction occurs at execution. Reconstruction, when configured, preserves the explicitly unmapped reference positions. NA-MPNN consumes the mapped backbone; the other generators consume a secondary target.",
            }, 0
        if not args.out:
            raise DesignError("run requires --out for durable artifacts")
        directory = args.out.resolve()
        create_run(config, directory)
        manifest = execute(directory)
        return summary(manifest, directory), 0 if manifest["status"].startswith("completed") else 4
    if args.command == "doctor":
        config = load_config(args.config.resolve()) if args.config else None
        runtime = (
            config.runtime
            if config
            else RuntimeConfig(
                root=Path(os.environ.get("RNA_DESIGN_RUNTIME", ".runtime")).resolve()
            )
        )
        if args.runtime_root:
            runtime.root = args.runtime_root.resolve()
        if args.device:
            runtime.device = args.device
        model = args.model or (config.model.name if config else "grnade")
        if config and args.model and args.model != config.model.name:
            from .config import ModelConfig

            config.model = {
                "grnade": ModelConfig,
                "struct2seq": Struct2SeqConfig,
                "both": BothModelsConfig,
                "na_mpnn": NaMPNNConfig,
                "all": AllModelsConfig,
            }[model]()
        if model in {"na_mpnn", "all"}:
            from .na_mpnn_runtime import check_runtime
            from .na_mpnn_runtime import local_verification as na_mpnn_verification

            status = check_runtime(runtime, include_struct2seq=model == "all")
            status["local_verification"] = na_mpnn_verification(runtime.root)
            if model == "all":
                from .struct2seq_runtime import local_verification as struct2seq_verification

                status["local_verification"] = {
                    "grnade": local_verification(runtime.root),
                    "struct2seq": struct2seq_verification(runtime.root),
                    "na_mpnn": na_mpnn_verification(runtime.root),
                }
        elif model in {"struct2seq", "both"}:
            from .struct2seq_runtime import check_runtime
            from .struct2seq_runtime import local_verification as struct2seq_verification

            status = check_runtime(runtime)
            status["local_verification"] = struct2seq_verification(runtime.root)
            if model == "both":
                status["local_verification"] = {
                    "grnade": local_verification(runtime.root),
                    "struct2seq": struct2seq_verification(runtime.root),
                }
        else:
            status = doctor(runtime)
            status["local_verification"] = local_verification(runtime.root)
        status["model"] = model
        if args.smoke:
            if not config or not args.out:
                raise DesignError("doctor --smoke requires --config and --out")
            config.budget = BudgetConfig(proposals=10, attempt_cap=20)
            config.selection.top_k = 10
            create_run(config, args.out.resolve())
            manifest = execute(args.out.resolve())
            return summary(manifest, args.out), 0 if manifest["status"].startswith(
                "completed"
            ) else 4
        return status, 0 if status["ready"] else 3
    directory = args.run_dir.resolve()
    if args.command == "status":
        return summary(load_manifest(directory), directory), 0
    if args.command == "resume":
        manifest = execute(directory)
        return summary(manifest, directory), 0 if manifest["status"].startswith("completed") else 4
    with run_lock(directory):
        if args.out.exists():
            raise DesignError(f"Export/selection output already exists: {args.out}")
        if args.command == "export":
            formats = set(args.formats.split(","))
            if not formats or formats - {"fasta", "csv", "json"}:
                raise DesignError("Formats must be a comma-separated subset of fasta,csv,json")
            return export(directory, args.out.resolve(), formats=formats), 0
        manifest, candidates, evaluations = collect(directory)
        settings = manifest["immutable"]["config"]["selection"].copy()
        for key in settings:
            if getattr(args, key, None) is not None:
                settings[key] = getattr(args, key)
        choice = select(
            candidates,
            evaluations,
            SelectionConfig.model_validate(settings),
            manifest["immutable"]["evaluation_revision"],
        )
        immutable_json(directory / "selections" / f"{choice['selection_id']}.json", choice)
        event(directory, "reselected", selection_id=choice["selection_id"])
        return export(directory, args.out.resolve(), choice), 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        value, code = dispatch(args)
        if args.json:
            print(json.dumps(value, sort_keys=True, allow_nan=False))
        else:
            print(yaml.safe_dump(value, sort_keys=False).rstrip())
        return code
    except KeyboardInterrupt:
        print("Interrupted; resume the run to reuse committed work.", file=sys.stderr)
        return 130
    except (DesignError, ValidationError, OSError, UnicodeError, yaml.YAMLError) as error:
        code = error.exit_code if isinstance(error, DesignError) else 2
        if args.json:
            print(json.dumps({"error": str(error), "exit_code": code}))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
