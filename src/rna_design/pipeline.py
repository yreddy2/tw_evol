import hashlib
import platform
import sys
import time
from pathlib import Path

from . import __version__
from .config import Experiment, generator_names, generators
from .errors import DesignError, ExecutionError
from .evaluation import evaluate
from .folding import fold
from .inputs import (
    crossing_pairs,
    normalize_sequence,
    parse_structure,
    read_sequence,
    validate_length,
)
from .reporting import collect, export
from .runtime import record_verification, require_runtime
from .selection import select
from .storage import (
    atomic_write,
    committed,
    event,
    identity,
    immutable_json,
    load_manifest,
    now,
    run_lock,
    write_json,
)


def model_runtime(config):
    if config.model.name in {"na_mpnn", "all"}:
        from .na_mpnn_runtime import require_runtime as require_na_mpnn

        return require_na_mpnn(config.runtime, include_struct2seq=config.model.name == "all")
    if config.model.name in {"struct2seq", "both"}:
        from .struct2seq_runtime import require_runtime as require_struct2seq

        return require_struct2seq(config.runtime)
    return require_runtime(config.runtime)


def validate(config: Experiment) -> dict:
    raw, sequence, name = read_sequence(config.input.sequence)
    design_sequence = sequence
    reconstruction = None
    if config.input.reconstruction:
        from .reconstruction import validate_mapping

        _, design_sequence, _ = read_sequence(config.input.reconstruction.design_sequence)
        reconstruction = validate_mapping(
            config.input.reconstruction.mapping, sequence, design_sequence
        )
        validate_length(sequence)  # Shared full-construct scoring envelope.
    models = generators(config.model)
    for model in models:
        validate_length(design_sequence, model.name)
    backbone = None
    if any(m.name == "na_mpnn" for m in models):
        from .backbone import validate_backbone

        if config.input.backbone is None:
            raise DesignError(
                "NA-MPNN requires input.backbone with an explicit chain and mapping strategy"
            )
        backbone = validate_backbone(config.input.backbone, design_sequence)
    target = None
    if config.input.target:
        target, _ = parse_structure(config.input.target.read_text(), len(sequence))
        if (
            not reconstruction
            and any(m.name == "grnade" for m in models)
            and set(target) - set(".()[]{}<>")
        ):
            raise DesignError(
                "gRNAde 2D targets support .()[]{}<>; alphabetic bracket levels are unsupported"
            )
    return {
        "sequence": sequence,
        "name": name,
        "length": len(sequence),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "provided_target": target,
        "target_source": config.input.target_source,
        "policy": "mapped_segment_with_fixed_context" if reconstruction else "full_redesign",
        "design_sequence": design_sequence,
        "reconstruction": reconstruction,
        "backbone": backbone,
        "requested_proposals": config.budget.proposals * len(models),
        "attempt_cap": config.budget.attempt_cap * len(models),
        "proposals_per_model": {model.name: config.budget.proposals for model in models},
        "budget_scope": "proposal and attempt quotas per model; wall-time and top-k per run",
        "config": config.model_dump(mode="json"),
    }


def candidate_record(sequence: str, context: dict) -> dict:
    return {
        "candidate_id": identity([context["construct_id"], sequence]),
        "construct_id": context["construct_id"],
        "sequence": sequence,
        "length": len(sequence),
        "is_reference": sequence == context["sequence"],
    }


def create_run(config: Experiment, out: Path) -> dict:
    plan = validate(config)
    runtime = model_runtime(config)
    reference_fold = fold(plan["sequence"], config.evaluation.physical_temperature_c)
    target = plan["provided_target"] or reference_fold["dot_bracket"]
    _, pairs = parse_structure(target)
    target_record = {
        "dot_bracket": target,
        "source": config.input.target_source,
        "pairs": sorted(pairs),
        "coordinates": "1-based inclusive",
        "pseudoknotted": bool(crossing_pairs(pairs)),
    }
    target_record["id"] = identity(target_record)
    # Never clobber a run or mix new inputs into an existing directory.
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise DesignError(f"Output already exists: {out}; use resume or a new directory") from error
    raw = config.input.sequence.read_bytes()
    snapshots = {"sequence.original": hashlib.sha256(raw).hexdigest()}
    atomic_write(out / "inputs/sequence.original", raw)
    atomic_write(out / "inputs/normalized.fasta", f">input\n{plan['sequence']}\n")
    if config.input.target:
        raw_target = config.input.target.read_bytes()
        atomic_write(out / "inputs/target.original", raw_target)
        snapshots["target.original"] = hashlib.sha256(raw_target).hexdigest()
    for name, path in [
        ("backbone.original", config.input.backbone.path if plan["backbone"] else None),
        (
            "design-sequence.original",
            config.input.reconstruction.design_sequence if plan["reconstruction"] else None,
        ),
        (
            "reconstruction-map.original",
            config.input.reconstruction.mapping if plan["reconstruction"] else None,
        ),
    ]:
        if path:
            data = path.read_bytes()
            atomic_write(out / "inputs" / name, data)
            snapshots[name] = hashlib.sha256(data).hexdigest()
    context = {
        "config": config.model_dump(mode="json"),
        "sequence": plan["sequence"],
        "construct_id": identity(plan["sequence"]),
        "target": target_record,
        "reference_fold": reference_fold,
        "runtime": runtime,
        "snapshots": snapshots,
        "software": {
            "rna_design": __version__,
            "python": sys.version,
            "platform": platform.platform(),
        },
    }
    if plan["backbone"]:
        context["backbone"] = plan["backbone"]
        immutable_json(out / "backbone.json", plan["backbone"])
    if plan["reconstruction"]:
        context["reconstruction"] = plan["reconstruction"]
        context["design_sequence"] = plan["design_sequence"]
        design_fold = fold(plan["design_sequence"], config.evaluation.physical_temperature_c)
        design_target = {
            "dot_bracket": design_fold["dot_bracket"],
            "source": "design_sequence_mfe",
            "pairs": sorted(parse_structure(design_fold["dot_bracket"])[1]),
            "fold": design_fold,
        }
        context["generation_target"] = {**design_target, "id": identity(design_target)}
        immutable_json(out / "reconstruction.json", plan["reconstruction"])
        immutable_json(out / "generation-target.json", context["generation_target"])
    context["reference_id"] = candidate_record(plan["sequence"], context)["candidate_id"]
    context["evaluation_revision"] = identity(
        {
            "input": context["construct_id"],
            "target": target_record,
            "evaluation": config.evaluation.model_dump(),
            "runtime": runtime,
            "reference_fold": reference_fold,
            "software": context["software"],
        }
    )
    manifest = {
        "schema_version": 1,
        "created": now(),
        "status": "created",
        "immutable": context,
        "immutable_sha256": identity(context),
        "elapsed_seconds": 0,
        "peak_rss_bytes": 0,
        "error": None,
        "counts": {"requested": plan["requested_proposals"]},
    }
    write_json(out / "manifest.json", manifest)
    immutable_json(out / "resolved-config.json", context["config"])
    immutable_json(out / "target.json", target_record)
    event(out, "created", target_id=target_record["id"])
    return manifest


def materialize_candidates(directory, context):
    reference = candidate_record(context["sequence"], context)
    immutable_json(directory / "candidates" / f"{reference['candidate_id']}.json", reference)
    for batch in committed(directory, "batches"):
        for proposal in batch["events"]:
            if proposal["valid"]:
                candidate = candidate_record(proposal["sequence"], context)
                immutable_json(
                    directory / "candidates" / f"{candidate['candidate_id']}.json", candidate
                )


def accounting(directory, context, selection=None, model_name=None):
    batches = committed(directory, "batches")
    events = [p for batch in batches for p in batch["events"]]
    default_model = context["config"]["model"]["name"]
    invocations = committed(directory, "invocations") or committed(directory, "attempts")
    if model_name:
        events = [p for p in events if p["model"]["name"] == model_name]
        batches = [b for b in batches if b.get("model", default_model) == model_name]
        invocations = [b for b in invocations if b.get("model", default_model) == model_name]
    candidates = committed(directory, "candidates")
    revision = context["evaluation_revision"]
    evaluations = committed(directory, f"evaluations/{revision}/records")
    generated_ids = {p["candidate_id"] for p in events if p["valid"]}
    valid = sum(p["valid"] for p in events)
    cap = context["config"]["selection"]["max_c_percent"]
    names = generator_names(context["config"]["model"])
    combined = len(names) > 1 and model_name is None
    result = {
        "requested": context["config"]["budget"]["proposals"] * (len(names) if combined else 1),
        "attempted": sum(b["requested"] for b in invocations),
        "generation_invocations": len(invocations or batches),
        "returned": len(events),
        "invalid": len(events) - valid,
        "duplicate": valid - len(generated_ids),
        "unique_generated": len(generated_ids),
        "reference_copies": sum(
            p["valid"] and p["sequence"] == context["sequence"] for p in events
        ),
        "constraint_passing": sum(
            c["candidate_id"] in generated_ids
            and (cap is None or 100 * c["sequence"].count("C") / c["length"] <= cap)
            for c in candidates
        ),
        "scored": sum(
            e["candidate_id"] in generated_ids
            and e["metrics"]["predicted_openknot_score"]["status"] == "ok"
            for e in evaluations
        ),
        "eligible": len(set(selection["ranked_ids"]) & generated_ids) if selection else 0,
        "selected": len(set(selection["selected_ids"]) & generated_ids) if selection else 0,
    }
    if combined:
        result["per_model"] = {
            name: accounting(directory, context, selection, name) for name in names
        }
        provenance = {}
        for p in events:
            if p["valid"]:
                provenance.setdefault(p["candidate_id"], set()).add(p["model"]["name"])
        result["cross_model_overlap"] = sum(len(sources) > 1 for sources in provenance.values())
    return result


def generate_model(directory, context, config, model, worker, manifest, remaining, checkpoint):
    runtime = context["runtime"]
    generator_runtime = runtime["generator"] if model.name == "struct2seq" else runtime
    if model.name == "na_mpnn":
        generator_runtime = runtime["na_mpnn"]
    design_sequence = context.get("design_sequence", context["sequence"])
    generation_target = context.get("generation_target", context["target"])
    # Replay unfinished reserved attempts with the same seed; completed batches never repeat.
    while True:
        counts = accounting(directory, context, model_name=model.name)
        all_attempts = committed(directory, "attempts")
        attempts = [
            a
            for a in all_attempts
            if a.get("model", context["config"]["model"]["name"]) == model.name
        ]
        pending = [
            a for a in attempts if not (directory / "batches" / f"{a['batch']:06d}.json").exists()
        ]
        if not pending and (
            counts["returned"] >= config.budget.proposals
            or counts["attempted"] >= config.budget.attempt_cap
        ):
            break
        remaining()
        if pending:
            attempt = pending[0]
        else:
            batch = len(all_attempts)
            model_batch = len(attempts)
            count = min(
                config.budget.batch_size,
                config.budget.proposals - counts["returned"],
                config.budget.attempt_cap - counts["attempted"],
            )
            seed = int(identity([config.budget.seed, model_batch])[:8], 16)
            attempt = {
                "batch": batch,
                "model": model.name,
                "model_batch": model_batch,
                "seed": seed,
                "requested": count,
                "seed_derivation": "sha256-json-master-seed-batch-first32-v1",
            }
            immutable_json(directory / "attempts" / f"{batch:06d}.json", attempt)
        # Every call, including replay of an interrupted batch, consumes attempts.
        if counts["attempted"] + attempt["requested"] > config.budget.attempt_cap:
            break
        invocations = committed(directory, "invocations")
        immutable_json(
            directory / "invocations" / f"{len(invocations):06d}.json",
            {**attempt, "started": now(), "replay": bool(pending)},
        )
        print(
            f"Generating {model.name} batch {attempt['batch'] + 1}: {attempt['requested']} proposals",
            file=sys.stderr,
        )
        controls = (
            {"temperature": model.sampling_temperature}
            if model.name in {"grnade", "na_mpnn"}
            else {
                "sampling_probability": model.sampling_probability,
                "target_pairs": generation_target["pairs"],
            }
        )
        if model.name == "na_mpnn":
            controls["backbone"] = context["backbone"]
        result = worker.request(
            "generate",
            generator=model.name,
            timeout=remaining(),
            sequence=design_sequence,
            target=generation_target["dot_bracket"],
            count=attempt["requested"],
            seed=attempt["seed"],
            **controls,
        )
        payload = result["result"]
        if model.name == "na_mpnn":
            if (
                payload.get("target_consumed") is not None
                or payload.get("backbone_consumed_id") != context["backbone"]["id"]
            ):
                raise ExecutionError(
                    "NA-MPNN consumed an incompatible backbone or claimed secondary-target conditioning"
                )
        elif payload["target_consumed"] != generation_target["dot_bracket"]:
            raise ExecutionError("Generator consumed a different target")
        if model.name == "struct2seq" and (
            payload.get("target_pairs_consumed") != generation_target["pairs"]
        ):
            raise ExecutionError("Generator consumed different target pairs")
        if len(payload["proposals"]) > attempt["requested"]:
            raise ExecutionError("Generator exceeded requested proposal budget")
        events = []
        for ordinal, proposal in enumerate(payload["proposals"]):
            error = None
            sequence = proposal.get("sequence", "")
            designed_sequence = sequence
            try:
                sequence = normalize_sequence(sequence)
                designed_sequence = sequence
                if len(sequence) != len(design_sequence):
                    raise DesignError("Generated RNA has the wrong length")
                if context.get("reconstruction"):
                    from .reconstruction import reconstruct

                    sequence = reconstruct(sequence, context["reconstruction"])
            except (DesignError, TypeError, AttributeError) as exc:
                error = str(exc)
            events.append(
                {
                    "event_id": f"{attempt['batch']:06d}:{ordinal:06d}",
                    "batch": attempt["batch"],
                    "ordinal": ordinal,
                    "sequence": sequence,
                    "designed_sequence": designed_sequence,
                    "reconstruction_id": context.get("reconstruction", {}).get("id"),
                    "candidate_id": candidate_record(sequence, context)["candidate_id"]
                    if error is None
                    else None,
                    "valid": error is None,
                    "error": error,
                    "seed": attempt["seed"],
                    "model": model.model_dump(),
                    "native": proposal.get("native", {}),
                    "checkpoint_sha256": generator_runtime["assets"][model.name]["sha256"],
                    "source_revision": generator_runtime["source_revision"],
                    "target_id": context["target"]["id"],
                    "generation_target_id": generation_target["id"]
                    if model.name != "na_mpnn"
                    else None,
                    "backbone_id": context["backbone"]["id"] if model.name == "na_mpnn" else None,
                    "conditioning": "rna_backbone"
                    if model.name == "na_mpnn"
                    else "secondary_target",
                    "internal_search_effort": payload.get("internal_search_effort"),
                    "internal_search_effort_scope": "batch",
                }
            )
        immutable_json(
            directory / "batches" / f"{attempt['batch']:06d}.json",
            {
                **attempt,
                "events": events,
                "seconds": result["seconds"],
                "peak_rss_bytes": result["peak_rss_bytes"],
                "internal_search_effort": payload.get("internal_search_effort"),
            },
        )
        manifest["peak_rss_bytes"] = max(manifest["peak_rss_bytes"], result["peak_rss_bytes"])
        materialize_candidates(directory, context)
        event(
            directory,
            "generation_committed",
            batch=attempt["batch"],
            returned=len(events),
        )
        checkpoint()


def execute(directory: Path, worker_factory=None) -> dict:
    from .runtime import Worker

    with run_lock(directory):
        manifest = load_manifest(directory)
        context = manifest["immutable"]
        config = Experiment.model_validate(context["config"])
        if worker_factory is None:
            if config.model.name in {"struct2seq", "both", "na_mpnn", "all"}:
                from .struct2seq_runtime import PipelineWorker

                worker_factory = PipelineWorker
            else:
                worker_factory = Worker
        # Recovery must not silently change the predictor/software under cached records.
        runtime = model_runtime(config)
        if runtime != context["runtime"]:
            raise ExecutionError(
                "Runtime identity changed; restore the pinned runtime or create a new run"
            )
        current_fold = fold(context["sequence"], config.evaluation.physical_temperature_c)
        if current_fold != context["reference_fold"]:
            raise ExecutionError("ViennaRNA version/settings/prediction changed; create a new run")
        started = time.monotonic()
        elapsed_before = manifest["elapsed_seconds"]

        def remaining():
            seconds = (
                config.budget.wall_time_seconds - elapsed_before - (time.monotonic() - started)
            )
            if seconds <= 0:
                raise ExecutionError(
                    "Run wall-time budget exhausted; no constraints or budgets were relaxed"
                )
            return seconds

        def checkpoint():
            manifest["elapsed_seconds"] = elapsed_before + time.monotonic() - started
            manifest["counts"] = accounting(directory, context)
            write_json(directory / "manifest.json", manifest)

        manifest["status"] = "running"
        manifest["error"] = None
        if len(generators(config.model)) > 1:
            manifest["model_errors"] = {}
        event(directory, "execution_started")
        checkpoint()
        interrupted = False
        try:
            materialize_candidates(directory, context)
            with worker_factory(config.runtime, directory / "worker.log") as worker:
                for model in generators(config.model):
                    try:
                        generate_model(
                            directory,
                            context,
                            config,
                            model,
                            worker,
                            manifest,
                            remaining,
                            checkpoint,
                        )
                    except Exception as error:
                        if (
                            len(generators(config.model)) == 1
                            or config.model.failure_policy == "stop"
                        ):
                            raise
                        manifest["model_errors"][model.name] = f"{type(error).__name__}: {error}"
                        event(directory, "model_failed", model=model.name, error=str(error))
                        # Restart any crashed/timed-out worker before another model or scoring.
                        worker.close()
                        checkpoint()
                revision_dir = directory / "evaluations" / context["evaluation_revision"]
                candidates = committed(directory, "candidates")
                candidates.sort(key=lambda c: (not c["is_reference"], c["candidate_id"]))
                for index, candidate in enumerate(candidates, 1):
                    path = revision_dir / "records" / f"{candidate['candidate_id']}.json"
                    if path.exists():
                        continue
                    remaining()
                    print(
                        f"Evaluating {index}/{len(candidates)} {'reference' if candidate['is_reference'] else candidate['candidate_id'][:12]}",
                        file=sys.stderr,
                    )
                    result = evaluate(candidate, context, worker, revision_dir, remaining)
                    immutable_json(path, result)
                    for key in ("rnet", "rnet_ss"):
                        if result[key]["status"] == "ok":
                            manifest["peak_rss_bytes"] = max(
                                manifest["peak_rss_bytes"],
                                result[key]["output"]["prediction"]["peak_rss_bytes"],
                            )
                    event(directory, "evaluation_committed", candidate_id=candidate["candidate_id"])
                    checkpoint()
            _, candidates, evaluations = collect(directory)
            choice = select(
                candidates, evaluations, config.selection, context["evaluation_revision"]
            )
            counts = accounting(directory, context, choice)
            primary_failed = any(
                e["metrics"]["predicted_openknot_score"]["status"] == "failed"
                or e["metrics"]["pair_f1"]["status"] == "failed"
                for e in evaluations.values()
            )
            if (
                counts["returned"] < counts["requested"]
                or primary_failed
                or manifest.get("model_errors")
            ):
                manifest["status"] = "partial"
                manifest["error"] = (
                    "Proposal shortfall or failed primary evaluations; see report and records"
                )
            else:
                manifest["status"] = (
                    "completed" if choice["selected_ids"] else "completed_no_candidates"
                )
        except KeyboardInterrupt:
            manifest["status"] = "interrupted"
            manifest["error"] = "Interrupted; resume preserves committed stages and batches"
            interrupted = True
        except Exception as error:
            manifest["status"] = "partial"
            manifest["error"] = f"{type(error).__name__}: {error}"
        finally:
            materialize_candidates(directory, context)
            _, candidates, evaluations = collect(directory)
            choice = select(
                candidates, evaluations, config.selection, context["evaluation_revision"]
            )
            immutable_json(directory / "selections" / f"{choice['selection_id']}.json", choice)
            write_json(directory / "selection.json", choice)
            manifest["counts"] = accounting(directory, context, choice)
            manifest["elapsed_seconds"] = elapsed_before + time.monotonic() - started
            manifest["updated"] = now()
            write_json(directory / "manifest.json", manifest)
            event(directory, "execution_stopped", status=manifest["status"])
            export(directory, directory / "exports", choice)
        if interrupted:
            raise KeyboardInterrupt
        try:
            if config.model.name == "struct2seq":
                from .struct2seq_runtime import record_verification as record_struct2seq

                record_struct2seq(config.runtime.root, manifest)
            elif config.model.name == "grnade":
                record_verification(config.runtime.root, manifest)
            elif config.model.name in {"na_mpnn", "all"}:
                from .na_mpnn_runtime import record_verification as record_na_mpnn

                record_na_mpnn(config.runtime.root, manifest)
        except OSError as error:
            print(
                f"Could not update optional runtime verification metadata: {error}", file=sys.stderr
            )
        return manifest
