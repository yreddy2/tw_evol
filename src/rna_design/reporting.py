import csv
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

from .config import generator_names
from .errors import ExecutionError
from .metrics import sequence_metrics
from .storage import atomic_write, committed, identity, load_manifest, read_json, write_json

METRICS = [
    "sequence_levenshtein_to_input",
    "mfe_structure_levenshtein_to_input",
    "hamming_to_input",
    "identity_to_input",
    "c_percent",
    "u_percent",
    "gc_percent",
    "predicted_openknot_score",
    "pair_precision",
    "pair_recall",
    "pair_f1",
    "base_pair_distance",
    "crossed_pair_recall",
    "mfe_kcal_mol",
    "mfe_frequency",
]


def collect(directory: Path):
    manifest = load_manifest(directory)
    candidates = committed(directory, "candidates")
    revision = manifest["immutable"]["evaluation_revision"]
    evaluations = {
        e["candidate_id"]: e for e in committed(directory, f"evaluations/{revision}/records")
    }
    context = manifest["immutable"]
    for candidate in candidates:
        if (
            candidate["candidate_id"] != identity([context["construct_id"], candidate["sequence"]])
            or candidate["construct_id"] != context["construct_id"]
            or candidate["length"] != len(context["sequence"])
            or len(candidate["sequence"]) != candidate["length"]
        ):
            raise ExecutionError("Stored candidate identity/length does not match its construct")
    for result in evaluations.values():
        if (
            result["context_sha256"] != identity(context)
            or result["evaluation_revision"] != revision
            or result["target_id"] != context["target"]["id"]
        ):
            raise ExecutionError("Stored evaluation has an incompatible context/target/revision")
    return manifest, candidates, evaluations


def export(directory: Path, out: Path, selection: dict | None = None, formats=None):
    formats = set(formats or ["fasta", "csv", "json"])
    manifest, candidates, evaluations = collect(directory)
    context = manifest["immutable"]
    model = context["config"]["model"]
    names_in_run = generator_names(model)
    reconstruction = context.get("reconstruction")
    fixed_count = len(reconstruction["fixed_reference_positions"]) if reconstruction else 0
    events = [proposal for batch in committed(directory, "batches") for proposal in batch["events"]]
    sources = defaultdict(set)
    generation_counts = Counter()
    for proposal in events:
        if proposal["valid"]:
            sources[proposal["candidate_id"]].add(proposal["model"]["name"])
            generation_counts[proposal["candidate_id"]] += 1
    if selection is None:
        selection = (
            read_json(directory / "selection.json")
            if (directory / "selection.json").exists()
            else {
                "selected_ids": [],
                "ranked_ids": [],
                "exclusions": {},
                "settings": context["config"]["selection"],
            }
        )
    selected = set(selection["selected_ids"])
    rankings = {cid: rank for rank, cid in enumerate(selection["ranked_ids"], 1)}
    detailed = []
    rows = []
    for candidate in candidates:
        cid = candidate["candidate_id"]
        names = sorted(sources[cid]) if not candidate["is_reference"] else []
        generator = ";".join(names) or None
        evaluation = evaluations.get(cid)
        metrics = (
            evaluation["metrics"]
            if evaluation
            else sequence_metrics(candidate["sequence"], context["sequence"])
        )
        reasons = selection["exclusions"].get(cid, [])
        row = {
            **{k: candidate[k] for k in ["candidate_id", "sequence", "length", "is_reference"]},
            "designed_sequence": "".join(
                candidate["sequence"][p - 1] for p in reconstruction["reference_positions"]
            )
            if reconstruction and not candidate["is_reference"]
            else (candidate["sequence"] if not candidate["is_reference"] else None),
            "designed_length": reconstruction["designed_length"]
            if reconstruction
            else len(context["sequence"]),
            "fixed_context_length": fixed_count,
            "design_policy": "mapped_segment_with_fixed_context"
            if reconstruction
            else "full_redesign",
            "reconstruction_id": reconstruction["id"] if reconstruction else None,
            "generator": generator,
            "generation_count": generation_counts[cid],
            "run_status": manifest["status"],
            "rank": rankings.get(cid),
            "selected": cid in selected,
            "exclusion_reasons": ";".join(reasons),
            "evaluation_revision": context["evaluation_revision"],
            "mfe_dot_bracket": evaluation["fold"]["output"]["dot_bracket"]
            if evaluation and evaluation["fold"]["status"] == "ok"
            else None,
            "rnet_ss_dot_bracket": evaluation["rnet_ss"]["output"]["dot_bracket"]
            if evaluation and evaluation["rnet_ss"]["status"] == "ok"
            else None,
            **{name: metrics.get(name, {}).get("value") for name in METRICS},
        }
        rows.append(row)
        detailed.append(
            {
                **candidate,
                "generator": generator,
                "generators": names,
                "generation_count": generation_counts[cid],
                "run_status": manifest["status"],
                **{
                    k: row[k]
                    for k in [
                        "designed_sequence",
                        "designed_length",
                        "fixed_context_length",
                        "design_policy",
                        "reconstruction_id",
                    ]
                },
                "evaluation": evaluation,
                "metrics": metrics,
                "rank": rankings.get(cid),
                "selected": cid in selected,
                "exclusion_reasons": reasons,
            }
        )
    out.mkdir(parents=True, exist_ok=True)
    by_id = {row["candidate_id"]: row for row in rows}
    ranked_rows = [by_id[cid] for cid in selection["ranked_ids"]]
    selected_rows = [by_id[cid] for cid in selection["selected_ids"]]
    if "csv" in formats:
        fields = [
            "candidate_id",
            "sequence",
            "length",
            "is_reference",
            "generator",
            "designed_sequence",
            "designed_length",
            "fixed_context_length",
            "design_policy",
            "reconstruction_id",
            "generation_count",
            "run_status",
            "rank",
            "selected",
            "exclusion_reasons",
            "evaluation_revision",
            "mfe_dot_bracket",
            "rnet_ss_dot_bracket",
            *METRICS,
        ]
        for name, group in [
            ("candidates", rows),
            ("ranked", ranked_rows),
            ("selected", selected_rows),
            ("reference", [row for row in rows if row["is_reference"]]),
        ]:
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(group)
            atomic_write(out / f"{name}.csv", stream.getvalue())
    if "fasta" in formats:
        atomic_write(
            out / "selected.fasta",
            "".join(
                f">{row['candidate_id']} rank={row['rank']} predicted_openknot_score={row['predicted_openknot_score']}\n{row['sequence']}\n"
                for row in selected_rows
            ),
        )
        atomic_write(out / "reference.fasta", f">reference\n{context['sequence']}\n")
    if "json" in formats:
        atomic_write(
            out / "candidates.jsonl",
            "".join(json.dumps(row, allow_nan=False) + "\n" for row in detailed),
        )
        atomic_write(
            out / "generation.jsonl",
            "".join(json.dumps(row, allow_nan=False) + "\n" for row in events),
        )
        write_json(out / "run.json", manifest)
        write_json(out / "selection.json", selection)
        write_json(out / "target.json", context["target"])
        for key in ["backbone", "reconstruction", "generation_target"]:
            if key in context:
                write_json(out / f"{key}.json", context[key])
        write_json(
            out / "reference.json",
            {
                "sequence": context["sequence"],
                "fold": context["reference_fold"],
                "evaluation": evaluations.get(context["reference_id"]),
            },
        )
    exclusions = Counter(
        reason for reasons in selection["exclusions"].values() for reason in reasons
    )
    gate_text = (
        "Top-ranked predictions; no structural pass threshold configured."
        if all(selection["settings"][key] is None for key in ["min_openknot_score", "min_pair_f1"])
        else "Only explicitly configured structural gates were applied."
    )
    generator_parts = []
    if "grnade" in names_in_run:
        generator_parts.append(
            "gRNAde `test_2d` consumes the design sequence's secondary target; no reference logit bias or 3D input."
        )
    if "struct2seq" in names_in_run:
        struct = model["struct2seq"] if len(names_in_run) > 1 else model
        generator_parts.append(
            f"Struct2SeQ SHAPE checkpoint, native stochastic sampler, sampling probability {struct['sampling_probability']}; consumes the same secondary target as gRNAde. No SHAPE profile, reference bias, 3D input, internal RNet search or rescue; native pair/repeat/adenine heuristics retained."
        )
    if "na_mpnn" in names_in_run:
        backbone = context["backbone"]
        generator_parts.append(
            f"NA-MPNN official design checkpoint consumes only the {backbone['length']}-nt chain {backbone['chain']} backbone, ID `{backbone['id']}`. It does not consume a secondary-structure target. Missing-atom policy: `{backbone['missing_atom_policy']}`; missing slots retain native atom masks. No coordinates are reconstructed. Canonical RNA token constraints apply; no reference logit bias or internal search."
        )
    generator_text = " ".join(generator_parts)
    coupling = "Shared predictor scores are not independent experimental validation."
    if "struct2seq" in names_in_run:
        coupling += " Struct2SeQ was trained/guided using RNet; this comparison is not an unbiased leaderboard."
    reconstruction_text = "All positions were mutable."
    if reconstruction:
        reconstruction_text = (
            f"Mapped-segment design with fixed context: models design {reconstruction['designed_length']} nt; "
            f"{fixed_count} other nucleotides are copied from the original input before evaluating the full {len(context['sequence'])}-nt construct. "
            "`sequence` in CSV is the reconstructed full construct; `designed_sequence` is the raw model output. "
            "Sequence and secondary-structure comparisons use the original full input. "
            "The separate generation target is predicted from the short input and is saved in `generation_target.json`. "
            "This is sequence reconstruction, not 3D reconstruction or full-length backbone design. "
            f"Mapping ID `{reconstruction['id']}`; fixed positions and acknowledged construct differences are in `reconstruction.json`."
        )
    combined_text = ""
    if len(names_in_run) > 1:
        combined_text = (
            f"Each model has a {context['config']['budget']['proposals']}-proposal quota and "
            f"{context['config']['budget']['attempt_cap']}-attempt cap. Wall-time and top-k apply "
            "to the entire run. Unique sequences are evaluated once across models and ranked "
            "together by predicted OpenKnot score (descending), pair F1 (descending), then "
            "candidate ID. `ranked.csv` contains all eligible sequences; `selected.csv` "
            "contains the global top-k. The `generator` column retains all source names "
            "separated by semicolons for shared sequences; `generation_count` counts all "
            "valid proposal occurrences. Per-model unique/scored/selected counts can overlap. "
            f"Generation failure policy: `{model['failure_policy']}`; failures and shortfalls "
            "leave the campaign partial (also recorded in each CSV row's `run_status`). "
            f"Model errors: {json.dumps(manifest.get('model_errors', {}), sort_keys=True)}."
        )
    report = f"""# RNA design run

Status: **{manifest["status"]}**. {manifest.get("error") or ""}

## Input and target

- Original normalized input: {len(context["sequence"])} nt; input ID `{context["construct_id"]}`.
- Target source: `{context["target"]["source"]}`; target ID `{context["target"]["id"]}`.
- {reconstruction_text}
- {generator_text}
- Reference ViennaRNA fold and design target are stored separately in `reference.json` and `target.json`.
- Folding settings: {json.dumps(context["reference_fold"]["provenance"], sort_keys=True)}.
- Target contains crossing pairs: {context["target"]["pseudoknotted"]}.

## Accounting and selection

{json.dumps(manifest.get("counts", {}), indent=2)}

{combined_text}

Selected {len(selected_rows)} of up to {selection["settings"]["top_k"]}; {len(ranked_rows)} eligible unique sequences.
{gate_text} No gate is weakened to fill the shortlist.
Exclusions (a candidate may have several reasons): {dict(exclusions)}.
Primary evaluation failures prevent shortlisting. Auxiliary failures remain null.
The reference control is reported separately and never counts toward novel designs.

## Reading the measurements

`sequence_levenshtein_to_input` is unit-cost string edit distance from the original RNA.
`mfe_structure_levenshtein_to_input` is a separate edit distance between the independently
predicted candidate and original-input ViennaRNA dot-bracket strings. Both allow insertion,
deletion and substitution in the comparison; generation still preserves sequence length.
A supplied target never changes either comparator. Neither distance is a rejection gate.

`predicted_openknot_score` uses **oksp-target-ecs-cpq-v1**: half Eterna Classic Score plus
half crossed-pair quality, from raw RNet channel 0 (2A3), full-construct mask, no clipping,
no normalization, no singlet filtering. The pinned OpenKnotScorePipeline revision is
`23e24fe21a3ebe50046b0cce29bcb1c4a1a571b8`. This is a supplied-target prediction score,
not an experimental ensemble score or MCC. Non-pseudoknotted targets have a maximum of **50**.
Components and raw reactivity channels are retained in JSONL.

RNet-SS is a separate checkpoint decoded with Arnie Hungarian assignment, theta=0.5,
minimum helix length 1 and diagonal distance <4 masked. Pair comparisons use 1-based
coordinates and canonical pair sets. Both-empty F1=1; one-empty F1=0; undefined
precision/recall and inapplicable crossed-pair recall are null.
The MFE ensemble frequency describes the candidate's own predicted MFE fold, not the target.

## Traceability and limits

FASTA and CSV candidate IDs join to `candidates.jsonl` and `generation.jsonl`.
JSON contains metric status, comparator strings, checkpoint identities and predictor provenance.
Blank CSV cells mean missing/null; zero is a measured zero. `run.json` contains immutable
configuration, input snapshots' hashes, source/weight identities, budgets and runtime information.
Elapsed active execution: {manifest.get("elapsed_seconds", 0):.3f} s.
Peak model-worker RSS: {manifest.get("peak_rss_bytes", 0)} bytes.

These are computational predictions, not confirmation of biological function, a matching
3D fold, or splicing activity. ViennaRNA is pseudoknot-free. RNet is used for post-generation
screening here; this workflow does not reproduce the paper's full candidate search.
{coupling} General regional/composition controls remain deferred; explicit reconstruction preserves only the mapped fixed context.
"""
    atomic_write(out / "report.md", report)
    return {"out": str(out.resolve()), "selected": len(selected_rows), "formats": sorted(formats)}
