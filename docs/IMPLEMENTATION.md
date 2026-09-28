# Operating the base CLI

The new application lives in `src/rna_design`; the supplied prototype is untouched.
The implemented scope is M0–M3 plus TW-017/TW-018: selectable gRNAde 2D and Struct2SeQ
generators, separate RNet/RNet-SS evaluator checkpoints, ViennaRNA diagnostics, durable
runs, selection and exports. No server, database service, UI or GPU provisioning is
required. See [Struct2SeQ](STRUCT2SEQ.md) for its isolated runtime and native sampling
contract, and `evidence/README.md` for measured checks and remaining review items.
The combined-run portion of TW-019 is also implemented: `--model both` runs both
generators with one sealed target and evaluation revision, then ranks the deduplicated
pool in `exports/ranked.csv`. Generation provenance is retained across model overlap.

## Local setup

Tested host: Apple Silicon macOS, 18 GiB RAM, Python 3.13 CLI and Python 3.11 model worker.
Use the root README commands. `scripts/setup_runtime.py` creates `.runtime/venv`, checks
out pinned official sources, installs compatible PyTorch 2.1.2/PyG CPU wheels and downloads
three SHA-256-verified checkpoints. Actual model runtime installation is approximately
1.2 GB on this Mac; reserve at least 2 GB for setup and more for campaigns.

The weights total 99,552,618 bytes. No Hugging Face login was required for the verified
public artifacts. gRNAde and the bundled RibonanzaNet implementations are MIT-licensed;
upstream notices remain in the checked-out source. Checkpoint locations and digests are
in `src/rna_design/runtime-lock.json`. The complete installed package list is saved in
`.runtime/installed-requirements.txt`; the tested Mac snapshot is under `evidence/`.

`doctor` validates hashes, source revision, imports, device and runtime versions. This
checks prerequisites; a real inference check requires `doctor --smoke --config ... --out ...`
or an ordinary smoke run. It does not download anything. `models list` documents
capabilities and the bounded 8–500 nt input envelope. Boundary probes are necessary
evidence, not a guarantee of useful predictions on every RNA in that range.

`MPS` is not supported by this adapter. An explicit CUDA request without CUDA fails;
the application does not silently switch devices.

## Configuration

```yaml
schema_version: 1
input:
  sequence: ../twort_seq.txt
  target: null
  target_source: reference_mfe
model:
  name: grnade
  mode: 2d
  policy: full_redesign
  sampling_temperature: 0.8
budget:
  proposals: 1000
  attempt_cap: 2000
  wall_time_seconds: 86400
  batch_size: 10
  seed: 42
runtime:
  root: ../.runtime
  device: cpu
  threads: 2
  request_timeout_seconds: 1800
evaluation:
  physical_temperature_c: 37
  score_definition: oksp-target-ecs-cpq-v1
selection:
  top_k: 100
  max_c_percent: null
  min_openknot_score: null
  min_pair_f1: null
```

Paths are relative to the YAML file. `init` writes absolute paths for convenience.
Unknown/future controls fail, including 3D modes, region masks and unsupported models.
For Struct2SeQ, use `model: {name: struct2seq, sampling_probability: 1.0}` in place of
the gRNAde model block, or pass `--model struct2seq`. Switching models via the CLI
selects that model's default controls. See `examples/struct2seq.yaml`.
For both generators, use `model: {name: both}` or `--model both`. Model-specific controls
live under `model.grnade` and `model.struct2seq`; see `examples/both.yaml`. Quotas and
attempt caps apply separately to each model, while top-k and wall-time remain global.
Both runtimes must pass preflight. The default `model.failure_policy: continue` records
generation failures, runs the remaining model and scores available candidates; any
shortfall leaves status `partial` and exit 4. Set `stop` to stop immediately instead.
A supplied `target` requires `target_source: provided`, or omit `target_source` and it is
resolved automatically. CLI `--target` does this automatically in sequence-file mode.
Target files contain only dot-bracket text and whitespace, not FASTA headers or energies.
gRNAde input accepts `.()[]{}<>`; Struct2SeQ and the internal pair parser also handle
alphabetic bracket levels. Every nucleotide has at most one pairing partner.

Composition gates compare unrounded full-length percentages. Candidates filtered by C%
still receive sequence and ViennaRNA diagnostics, but skip expensive neural screening.
Reference controls always receive full evaluation. Re-selection can tighten or relax
stored-score gates; candidates never originally screened remain unavailable if a
composition gate is subsequently relaxed. Start a new run to obtain their missing scores.

## Durability and reproducibility

Run directories are exclusive output destinations. Existing paths are never overwritten.
`manifest.json` seals configuration, original-input hashes, target, reference fold, software
and model identities. Input snapshots, target and resolved configuration are checked
on reopening. Target prediction never changes on resume.

`attempts/` reserves deterministic batch sizes and seeds. Seeds derive from a master seed
and batch number using SHA-256. `invocations/` records every actual generation call,
including replay of interrupted work; each call consumes the proposal-attempt budget.
Defaults allow 2,000 attempts for a 1,000-returned-proposal quota (20 for smoke runs).
An incomplete batch replays with its original size/seed only if budget remains.
For a combined run, seeds use each model's own batch number, matching single-model
sampling for the same settings. Global batch IDs remain unique for durable storage.
Each model's interrupted calls consume its own attempt cap. Already-scored cross-model
duplicates reuse the same candidate/evaluation record, including on resume.
Returned invalid proposals count toward the returned-proposal quota and retain reasons.
gRNAde exposes no separate internal search count, so that field is null. Struct2SeQ
records its exact native proposal count per batch and explicitly records zero search,
rescue and generation-scorer calls; see the adapter contract.

`batches/` commits generation events atomically. `candidates/` contains content-derived
identities, deduplicated within the construct. Different generation events can reference
the same candidate. A crash after batch commit but before candidate materialization is
repaired by rebuilding candidate records from those committed batches.

`evaluations/<revision>/stages/` commits ViennaRNA, RNet and RNet-SS separately; completed
compatible predictor calls are reused after interruption. Failed metric records retain
errors and are not silently replaced on resume. A model-process crash or timeout leaves
its in-flight stage uncommitted, so resume can retry it. A recorded per-candidate predictor
failure remains failed in that immutable evaluation; a new run is needed to retry those
failed records. Automatic reevaluation revisions are outside this first release.

`selections/` is immutable selection history. Re-selection writes a new selection and
export location without replacing the original run's selection. Generated export tables
are reproducible views of committed JSON. The append-only event log is advisory; committed
records are the recovery authority. Partial `.pending-*` files are ignored. One run writer
is allowed at a time through an OS file lock, released even after process death.

CPU sampling was repeatable for the tested seed/batch fixture. Different batch sizes,
devices, package versions, or kernels may change results. Runtime identity changes block
resume. The active wall-time budget is cumulative across clean stops/resumes; time after
the last checkpoint of an uncatchable machine/process crash cannot be fully recovered.
The model worker is terminated after a request timeout or ordinary interruption.

## Troubleshooting

- Missing/corrupt checkpoint: `doctor` identifies the path and expected digest. Setup
  refuses to overwrite a corrupt file; remove that file explicitly and rerun setup.
- Input outside 8–500 nt: rejected before inference, never cropped or padded.
- Invalid DNA/ambiguity symbols, multiple FASTA records, malformed/mismatched target:
  fix the input; no silent conversion occurs.
- CUDA OOM/process failure: completed batches and stages survive. Use smaller generation
  batches in a **new** configuration/run, or resume the original run on its same compatible
  runtime after resolving transient resource pressure. Scoring processes one sequence
  at a time. No automatic batch-size change is hidden from provenance.
- Empty shortlist: inspect exclusion reasons and metric statuses. Completed empty runs
  exit 0; exhausted proposal budgets or primary failures exit 4. Gates never relax.
- Auxiliary ViennaRNA failure: sequence distance and successful neural metrics remain;
  structural distance and energy/frequency are null with the fold error.
- Pseudoknot target: retained exactly for gRNAde/RNet-SS evaluation. ViennaRNA distances
  remain pseudoknot-free diagnostics; see `docs/SCORING.md`.

## Conditional Linux/Runpod execution — unverified

Local CPU inference was sufficient, so no GPU resources were created or charged.
The following is an **unverified portability recipe**, not a supported-host claim:

1. Choose a Linux x86_64 GPU host with a CUDA-12.1-compatible driver and persistent
   storage for this repository, `.runtime` and `runs`. Match PyTorch/PyG wheels exactly.
2. Install the lightweight CLI and run `python scripts/setup_runtime.py --device cuda`.
3. Run `rna-design doctor --device cuda`, then a 10-proposal smoke run using `--device cuda`.
4. If verified on that host, launch the same bounded CLI campaign there. Record GPU model,
   VRAM usage and runtime; local RSS does not establish GPU memory requirements.
5. Copy the entire run directory back (e.g. `rsync -a host:/workspace/project/runs/run-id ./runs/`).
   `status`, `select` and `export` read copied artifacts without any model installation.
6. After copying and verifying artifacts, stop the manually provisioned compute.

No automatic provisioning, remote scheduler, or upload exists in the application.
