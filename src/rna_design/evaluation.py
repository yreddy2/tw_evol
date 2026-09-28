from pathlib import Path

from .folding import fold
from .inputs import parse_structure
from .metrics import (
    levenshtein,
    measurement,
    pair_metrics,
    predicted_openknot,
    sequence_metrics,
)
from .runtime import LOCK
from .storage import identity, immutable_json, now, read_json


def evaluate(candidate: dict, context: dict, worker, directory: Path, remaining) -> dict:
    """Commit independent stages; resume never repeats a completed predictor request."""
    sequence = candidate["sequence"]
    reference = context["sequence"]
    target = context["target"]["dot_bracket"]
    metrics = sequence_metrics(sequence, reference)
    base = directory / "stages" / candidate["candidate_id"]

    def stage(name, calculate):
        path = base / f"{name}.json"
        if path.exists():
            return read_json(path)
        # Deadline errors stop the run instead of permanently caching missing predictions.
        remaining()
        try:
            value = {"status": "ok", "output": calculate(), "error": None}
        except Exception as error:
            if worker.process.poll() is not None:
                raise
            value = {
                "status": "failed",
                "output": None,
                "error": f"{type(error).__name__}: {error}",
            }
        value["time"] = now()
        immutable_json(path, value)
        return value

    folded = stage(
        "vienna", lambda: fold(sequence, context["config"]["evaluation"]["physical_temperature_c"])
    )
    reference_fold = context["reference_fold"]
    if folded["status"] == "ok":
        data = folded["output"]
        provenance = {
            "fold": data["provenance"],
            "comparator_fold": reference_fold["provenance"],
            "candidate_string": data["dot_bracket"],
            "comparator_string": reference_fold["dot_bracket"],
            "comparator": "original_input_vienna_mfe",
        }
        metrics["mfe_structure_levenshtein_to_input"] = measurement(
            levenshtein(data["dot_bracket"], reference_fold["dot_bracket"]),
            "levenshtein-unit-v1",
            **provenance,
        )
        for key in ("mfe_kcal_mol", "mfe_frequency"):
            metrics[key] = measurement(data[key], "vienna-mfe-pf-v1", fold=data["provenance"])
    else:
        for key in ("mfe_structure_levenshtein_to_input", "mfe_kcal_mol", "mfe_frequency"):
            metrics[key] = measurement(None, "vienna-mfe-pf-v1", error=folded["error"])

    def reactivity():
        prediction = worker.request("reactivity", timeout=remaining(), sequence=sequence)
        channels = prediction["result"]["channels"]
        if len(channels) != len(sequence) or any(len(row) != 2 for row in channels):
            raise ValueError("RNet must return two channels for the complete RNA")
        score = predicted_openknot(target, [row[0] for row in channels])
        score["provenance"]["checkpoint_sha256"] = LOCK["checkpoints"]["rnet"]["sha256"]
        return {"prediction": prediction, "score": score}

    cap = context["config"]["selection"]["max_c_percent"]
    filtered = (
        not candidate["is_reference"]
        and cap is not None
        and 100 * sequence.count("C") / len(sequence) > cap
    )
    skipped = {
        "status": "not_applicable",
        "output": None,
        "error": "Excluded by configured composition gate before neural screening",
    }
    rnet = skipped if filtered else stage("rnet", reactivity)
    metrics["predicted_openknot_score"] = (
        rnet["output"]["score"]
        if rnet["status"] == "ok"
        else measurement(None, "oksp-target-ecs-cpq-v1", error=rnet["error"])
    )

    def structure():
        prediction = worker.request("structure", timeout=remaining(), sequence=sequence)
        dot_bracket, pairs = parse_structure(prediction["result"]["dot_bracket"], len(sequence))
        _, target_pairs = parse_structure(target)
        values = pair_metrics(pairs, target_pairs)
        for metric in values.values():
            metric["provenance"].update(
                checkpoint_sha256=LOCK["checkpoints"]["rnet_ss"]["sha256"],
                decoder=LOCK["decoder"],
                target_id=context["target"]["id"],
            )
        return {
            "prediction": prediction,
            "pairs": sorted(pairs),
            "dot_bracket": dot_bracket,
            "metrics": values,
        }

    ss = skipped if filtered else stage("rnet_ss", structure)
    if ss["status"] == "ok":
        metrics.update(ss["output"]["metrics"])
    else:
        for key in (
            "pair_precision",
            "pair_recall",
            "pair_f1",
            "base_pair_distance",
            "crossed_pair_recall",
        ):
            metrics[key] = measurement(None, "pair-set-v1", error=ss["error"])
    if filtered:
        for key in (
            "predicted_openknot_score",
            "pair_precision",
            "pair_recall",
            "pair_f1",
            "base_pair_distance",
            "crossed_pair_recall",
        ):
            metrics[key]["status"] = "not_applicable"
    return {
        "schema_version": 1,
        "candidate_id": candidate["candidate_id"],
        "evaluation_revision": context["evaluation_revision"],
        "target_id": context["target"]["id"],
        "context_sha256": identity(context),
        "metrics": metrics,
        "fold": folded,
        "rnet": rnet,
        "rnet_ss": ss,
        "time": now(),
    }
