from .config import SelectionConfig
from .storage import identity


def select(
    candidates: list[dict], evaluations: dict, settings: SelectionConfig, revision: str
) -> dict:
    ranked = []
    exclusions = {}
    for candidate in candidates:
        cid = candidate["candidate_id"]
        metrics = evaluations.get(cid, {}).get("metrics", {})
        reasons = []
        if candidate["is_reference"]:
            reasons.append("identical_to_input")
        c_percent = 100 * candidate["sequence"].count("C") / len(candidate["sequence"])
        if settings.max_c_percent is not None and c_percent > settings.max_c_percent:
            reasons.append("max_c_percent")
        for name, threshold in (
            ("predicted_openknot_score", settings.min_openknot_score),
            ("pair_f1", settings.min_pair_f1),
        ):
            metric = metrics.get(name, {})
            if metric.get("status") != "ok":
                reasons.append(f"{name}_unavailable")
            elif threshold is not None and metric["value"] < threshold:
                reasons.append(f"below_{name}")
        if reasons:
            exclusions[cid] = reasons
        else:
            ranked.append(cid)
    ranked.sort(
        key=lambda cid: (
            -evaluations[cid]["metrics"]["predicted_openknot_score"]["value"],
            -evaluations[cid]["metrics"]["pair_f1"]["value"],
            cid,
        )
    )
    value = {
        "evaluation_revision": revision,
        "settings": settings.model_dump(),
        "ranking": ["predicted_openknot_score:desc", "pair_f1:desc", "candidate_id:asc"],
        "ranked_ids": ranked,
        "selected_ids": ranked[: settings.top_k],
        "exclusions": exclusions,
    }
    return {"selection_id": identity(value), **value}
