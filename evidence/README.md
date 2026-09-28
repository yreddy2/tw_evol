# Local implementation evidence

Verified 2026-09-22 on Apple Silicon macOS with 18 GiB RAM, CPU only. No GPU host was
provisioned. All listed generation and scoring results use real pinned pretrained models.
Synthetic predictors appear only in failure-path tests, never in production commands.

Original gRNAde verification:

| Run | Input length | Returned / scored | Selected | Active seconds | Peak worker RSS |
|---|---:|---:|---:|---:|---:|
| Release campaign | 36 | 1000 / 1000 | 100 | 302.21 | 0.41 GiB |
| Twort smoke | 282 | 10 / 10 | 10 | 93.50 | 1.06 GiB |
| Lower boundary | 8 | 10 / 10 | 10 | 3.94 | 0.51 GiB |
| tRNA fixture | 76 | 10 / 10 | 10 | 9.12 | 0.60 GiB |
| Upper boundary | 500 | 10 / 10 | 10 | 302.81 | 2.08 GiB |
| Pseudoknot target override | 36 | 10 / 10 | 10 | 8.39 | 0.50 GiB |
| C%=0 filter | 36 | 10 / 0 | 0 | 6.64 | 0.51 GiB |

Times are observed active campaign time, excluding preflight/install overhead. Some
checks ran concurrently, so this is a feasibility baseline, not a controlled throughput
benchmark. Memory is peak model-worker RSS, not total system RAM or GPU VRAM. Zero scored
in the C%=0 case is intentional: every candidate was excluded before neural screening,
while the reference was fully evaluated and candidate distances were still produced.

## Reproducible evidence

- `release-results.json`: run counts, score/distance distributions, reference controls,
  timing, memory, and an audit of both Levenshtein fields for **every** stored candidate.
- `scoring-equivalence.json`: 72 expected/actual score fixtures against official
  OpenKnotScorePipeline revision `23e24fe21a3ebe50046b0cce29bcb1c4a1a571b8`, tolerance 1e-12.
- `recovery-real.json`: actual CLI SIGINT exit 130 followed by successful resume; hashes
  show that completed generation batches, target and predictor stages did not change.
- `real-integration-test.txt`: real repeatable seeded gRNAde sampling and both evaluators.
- `doctor.json`: source, checkpoint, package/device readiness and local verification.
- `runtime-macos-py311.txt`: resolved research environment; setup is in
  `scripts/setup_runtime.py`, direct pins in `requirements-runtime.txt`.
- `setup-verification.txt`: successful execution of the setup script on the local install.

Full self-contained run artifacts are present under `runs/` (git-ignored), including
`runs/twort-smoke/exports/report.md`, `runs/release-1000/exports/report.md`, the ranked
tables, selected FASTAs and detailed per-candidate predictions. Reselecting Twort to
three candidates and exporting the entire release campaign were also exercised.

The test suite covers inputs, crossing-pair semantics, numerical thresholds, both edit
distances, target overrides, fold/predictor failures, deduplication, reference exclusion,
all-filtered results, attempt/time limits, interrupted replay, locks, immutable artifacts,
source drift and lightweight commands. The separate real test is opt-in.

To repeat the scientific checks, use `scripts/verify_scoring.py` with the model Python,
`scripts/verify_runtime.py` with the CLI Python, `scripts/verify_recovery.py` with the CLI
Python, and `scripts/summarize_evidence.py` to audit saved results. Runtime/recovery
scripts need fresh output directories where specified; inference creates real artifacts.

## Struct2SeQ extension

The second generator was verified separately using the author's real SHAPE checkpoint,
an isolated PyTorch 2.9.1 environment and the same shared evaluators. All runs below
returned 10 unique candidates, scored all 10, and selected all 10 with quality gates off.

| Input | Length | Active seconds | Peak worker RSS |
|---|---:|---:|---:|
| Lower boundary | 8 | 3.50 | 0.46 GiB |
| Hairpin fixture | 36 | 6.36 | 0.50 GiB |
| tRNA fixture | 76 | 10.18 | 0.48 GiB |
| Twort | 282 | 112.18 | 0.94 GiB |
| Upper boundary | 500 | 361.96 | 1.97 GiB |

Concurrent checks affected timing; these are feasibility observations, not a speed
comparison between generators. No CUDA or MPS device was used.

- `struct2seq-results.json`: audited every proposal's source/checkpoint/target identity
  and every candidate's two edit distances; verified matching Twort target, scorer code,
  checkpoint identities, evaluation settings, package versions, device and proposal quota
  between the two generators. This is not an unbiased leaderboard.
- `struct2seq-length-feasibility.json`: measured boundary and Twort runs.
- `struct2seq-recovery-real.json`: real SIGINT/resume preserved committed artifact hashes.
- `struct2seq-grnade-compatibility.json`: an existing gRNAde Twort run still resumes.
- `struct2seq-unit-tests.txt`: 88 passed; 2 real-model tests skipped unless enabled,
  and 1 inapplicable gRNAde-only target-pair protocol case skipped.
- `struct2seq-real-integration-tests.txt`: both real-model tests passed, including
  repeatable Struct2SeQ sampling, alphabetic crossing pairs, no reference bias, and
  both common evaluators in their original environment.
- `struct2seq-wheel-smoke.json`: the built v0.2.0 wheel generated and scored 3 real
  proposals in a separate Python 3.12 CLI environment.
- `struct2seq-fresh-setup.txt`: source checkout, dependency installation, download and
  verified extraction succeeded using a fresh runtime directory. Real generation from
  that installation also succeeded.
- `struct2seq-doctor.json`, `struct2seq-models.json`, and
  `struct2seq-runtime-macos-py311.txt`: current asset, package and capability evidence.
- `struct2seq-checkpoint-access.json`: public author dataset version/license metadata.

Twort outputs are in `runs/struct2seq-feasibility/twort-282/exports/`, including the
selected FASTA and all scores. The adapter contract is in `docs/STRUCT2SEQ.md`.

## Combined ranking extension

`--model both` was exercised on the full 282-nt Twort input with 10 proposals per
generator. All 20 unique candidates were scored, plus one reference evaluation, and
exported in one globally sorted `runs/twort-both-smoke/exports/ranked.csv`. The shortlist
contained 10 candidates across both models. The run completed in 197.37 active seconds
with peak worker RSS about 0.96 GiB; concurrent checks make this a feasibility observation.

- `both-twort-smoke.json`: real generation counts, per-model yield, overlap, single
  target identity, and audit of CSV sort order, unique rows and model provenance.
- `both-unit-tests.txt`: 98 passed, 3 intentionally skipped. Combined-mode cases cover
  cross-model duplicates scored once, source-name preservation, global selection and
  filters, partial failures of either generator, stop policy, separate attempt caps,
  per-model controls/batch seeds, interruption/replay, export and re-selection.
- `both-recovery-real.json`: real combined CLI interruption/resume preserved all four
  completed generation batches and committed evaluator stages.
- `both-grnade-compatibility.json` and `both-struct2seq-compatibility.json`: pre-existing
  single-model runs resume after the extension.
- `both-wheel-smoke.json`: installed v0.3.0 wheel ran both generators and shared scoring.
- `both-doctor.json`: both model environments and common evaluator prerequisites pass.

The combined CSV ranks the shared prediction scores; Struct2SeQ's RNet training coupling
still applies. Multi-model generation, pooled ranking, deduplication and partial-run
handling implement part of TW-019. External FASTA scoring and richer distribution
comparison reports remain deferred.

## NA-MPNN and all-three reconstructed Twort run

The official NA-MPNN design checkpoint ran on this Mac's CPU. All three models generated
10 unique 229-nt sequences each. Reconstruction copied 53 original Twort positions back
into every candidate, then the common evaluators scored all 30 full 282-nt candidates
and the original reference. The globally ranked CSV has 30 rows, 10 from each model,
with both raw design and reconstructed sequence columns. The original run took 260.90
active seconds, with peak worker RSS 1,075,249,152 bytes (about 1.00 GiB).

- `na-mpnn-native-verification.json`: all 16 native feature fields equal the upstream
  parser/featurizer, all residue masks active, canonical tokens, repeatable same-seed
  output and changed output with a different seed. No CUDA was used.
- `all-twort-reconstructed.json`: every fixed position, reconstructed proposal, both
  edit distances, checkpoint/conditioning receipts, per-model quota and global CSV
  ordering independently audited.
- `all-unit-tests.txt`: mapping, missing atoms, mmCIF/PDB parity, sequence reconstruction,
  three-model overlap, partial failure, interruption/replay and immutable-input tests,
  alongside the existing suite. Real model tests remain separately opt-in.
- `all-doctor.json`: source, weights, device, package identities and local verification.
- `all-wheel-validation.json`, `all-wheel-resume.json`: installed v0.4.0 wheel validates
  the reconstruction configuration and resumes the completed real run.
- `all-completed-resume-audit.json`: 142 committed generation/evaluation artifacts retained
  identical hashes; resume generated and evaluated no new sequences.
- `all-existing-both-resume.json`: the earlier full-length two-model run still resumes.
- `twort-1y0q-comparison/`: exact source sequences, alignment and inspected backbone.

The chosen mapping has acknowledged alignment ambiguity and two base differences;
see [the separate issue document](../docs/TWORT_STRUCTURE_AND_RECONSTRUCTION.md).
NA-MPNN's verified backbone length is 229 nt; this is not evidence for arbitrary lengths,
CUDA, full-length 282-nt backbone design, or reconstructed 3D coordinates.

## Limits and open review

The product admits 8–500 nt. The 2D models tested multiple contents at 8, 36, 76, 282 and 500 nt; NA-MPNN has been verified at a 229-nt backbone length.
Boundary success does not prove useful predictions for every sequence. The 500-nt
boundary is a synthetic compatibility fixture, not a biological validation target.
The tRNA fixture uses canonical letters; modified-base chemistry is not modeled.

The selected score is target-specific predicted OpenKnot, not the paper's experimental
ensemble score; non-pseudoknotted targets max out at 50. There is no score cutoff of 90.
Thresholds are off by default, so a selected sequence is not a biologically validated hit.
Human review of report terminology and biological usefulness remains pending (TW-015).
No splicing or catalytic function claim is made.

Richer model comparison reports, general regional constraints, imported-sequence scoring,
automatic re-evaluation of failed immutable scores and automated remote execution are
not implemented. The Linux/CUDA/Runpod recipe is unverified because CPU execution worked.
