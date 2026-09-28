# RNA Design CLI

A local command-line application for RNA design with **gRNAde 2D, Struct2SeQ and NA-MPNN**.
All three models run locally on CPU, including on Apple Silicon; CUDA is not required.
NA-MPNN requires a compatible RNA backbone and an explicit sequence mapping.
ViennaRNA predicts a target from your RNA, the selected model generates alternatives, and separate
RibonanzaNet and RibonanzaNet-SS checkpoints evaluate them. Twort is an example input.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
# Explicit download/setup; requires git, network access, and approximately 2 GB free.
.venv/bin/python scripts/setup_runtime.py
.venv/bin/rna-design doctor
.venv/bin/rna-design run --sequence twort_seq.txt --smoke --out runs/twort-smoke
```

The model environment is separate from the CLI. Model setup downloads approximately
100 MB of verified weights plus research dependencies. Input validation never downloads
models, provisions cloud resources, or uploads your RNA.

### Add or use Struct2SeQ

The second model is installed on this workspace. On a fresh installation, run the base
setup above, then install its separate runtime:

```bash
.venv/bin/python scripts/setup_struct2seq.py
.venv/bin/rna-design doctor --model struct2seq
.venv/bin/rna-design run --sequence twort_seq.txt --model struct2seq --device cpu \
  --smoke --out runs/twort-struct2seq
```

Omit `--smoke` for the default 1,000-proposal campaign. Add `--proposals 100` for a smaller
campaign. `--model grnade` selects the original generator and remains the default.
Use `--model both` to generate with both models and rank their pooled candidates together.
External FASTA scoring remains deferred.

### Run both models and get one ranked CSV

```bash
.venv/bin/rna-design run --sequence twort_seq.txt --model both --device cpu \
  --proposals 100 --top-k 100 --out runs/twort-both
```

The combined ranking is **`runs/twort-both/exports/ranked.csv`**. This command requests
100 proposals from **each** model (200 total). `--smoke` requests 10 per model instead;
without either flag the default is 1,000 per model. Models run sequentially on CPU.
Proposal and attempt budgets apply per model; the wall-time limit and top-k apply to
the entire run. `--model grnade` and `--model struct2seq` remain available.

Identical sequences are scored once and appear in one row. The `generator` column contains
`grnade`, `struct2seq`, or `grnade;struct2seq`; `generation_count` retains duplicate counts.
All eligible unique candidates are ranked by predicted OpenKnot score, then pair F1,
then stable candidate ID. `selected.csv` contains the global top-k from that ranking.
The reference is kept in its separate control file. Both models share one target,
evaluation configuration, filter set and evaluator revision.

If one generator fails during execution, available candidates are scored and the run
and CSV rows are marked **partial**, with exit code 4. `resume` retries unfinished
generation within its original per-model attempt cap and reuses completed evaluations.
The optional YAML `model.failure_policy: stop` stops immediately instead. Both runtimes
must pass preflight. See [the combined configuration](examples/both.yaml).

Struct2SeQ uses the author's **SHAPE checkpoint**, conditioned on the secondary target;
it needs no measured SHAPE profile, reference bias, PDB, or GPU. This adapter calls the
official stochastic sampler with its native pair/repeat/adenine heuristics. It records
every sampled proposal and does not run the upstream RNet-guided search/rescue stages.
Struct2SeQ was trained/guided using RNet, so its RNet scores are not independent validation.
See [the adapter contract](docs/STRUCT2SEQ.md) and [example configuration](examples/struct2seq.yaml).

```bash
.venv/bin/rna-design init --sequence examples/hairpin.fasta --output experiment.yaml
.venv/bin/rna-design validate --config experiment.yaml
.venv/bin/rna-design run --config experiment.yaml --dry-run
.venv/bin/rna-design run --config experiment.yaml --out runs/design-001
.venv/bin/rna-design status runs/design-001 --json
.venv/bin/rna-design resume runs/design-001
.venv/bin/rna-design select runs/design-001 --top-k 20 --out selections/review-001
.venv/bin/rna-design export runs/design-001 --formats fasta,csv,json --out exports/design-001
```

Raw text or a single-record FASTA is accepted. Case and whitespace are normalized;
ambiguous bases and DNA T are rejected. Optional `--target target.dbn` overrides only
the shared comparison target. Every nucleotide is mutable in the default full-redesign mode;
explicit reconstruction preserves the unmapped reference positions. Shared composition and structural
quality gates are off by default. Defaults are 1,000 proposals, batches of 10, seed 42,
physical temperature 37°C, CPU, and up to 100 unique shortlisted RNAs. gRNAde uses sampling
temperature 0.8; Struct2SeQ uses native sampling probability 1.0, a different control.
YAML file paths resolve relative to the configuration file. `init` writes absolute paths.
`RNA_DESIGN_RUNTIME` or `--runtime-root` can locate a shared model installation.

### Run all three models

Install the base and Struct2SeQ environments, then:

```bash
.venv/bin/pip install -e '.[fold,structure]'
.venv/bin/python scripts/setup_na_mpnn.py
.venv/bin/rna-design doctor --model all
.venv/bin/rna-design run --config examples/twort-all-reconstructed.yaml \
  --out runs/twort-all-new
```

The supplied configuration requests **10 sequences per model**, 30 total, and produces
one `exports/ranked.csv`. It designs the 229-nt 1Y0Q chain A construct, then restores 53
original Twort positions before scoring the full 282-nt candidates against the original
Twort. The CSV contains both `sequence` (282 nt) and `designed_sequence` (229 nt).
This explicit fixed-context experiment does not invent missing 3D coordinates.
See [the separate issue and reconstruction document](docs/TWORT_STRUCTURE_AND_RECONSTRUCTION.md).
The completed local result is [here](runs/twort-all-reconstructed/exports/ranked.csv).

For a different input with a matching full-length backbone:

```bash
.venv/bin/rna-design inspect-backbone structure.cif --chain A --sequence input.fasta
.venv/bin/rna-design run --sequence input.fasta --model all \
  --backbone structure.cif --chain A --mapping exact_sequence \
  --proposals 10 --top-k 30 --out runs/all-three
```

`--model na_mpnn` selects the third model alone. `--model both` keeps its original
meaning (gRNAde + Struct2SeQ). `--model all` requires all three runtimes at preflight.
NA-MPNN uses the backbone; the other generators use the secondary target. Common scoring,
per-model quotas, pooled ranking and failure/resume behavior also apply to all-three runs.
Use `--missing-atoms mask_phosphate` only for explicit masking of absent P/OP1/OP2;
other required atoms must be present. Exact mapping rejects length/base mismatches.
Detailed contract: [NA-MPNN](docs/NA_MPNN.md).

## Results

Each run contains original input snapshots, a sealed manifest, immutable batch and
evaluation records, and an append-only event log. `exports/` contains candidate, ranked,
selected and reference CSVs, FASTA, detailed JSONL, generation provenance, and a report.

Both requested distances are exported: `sequence_levenshtein_to_input` compares nucleotide
strings; `mfe_structure_levenshtein_to_input` compares candidate and original-input
ViennaRNA dot-bracket strings. Both remain descriptive and refer to the original input,
even when a different design target is supplied. Missing measurements remain null.

The pinned OpenKnot definition combines Eterna Classic Score and crossed-pair quality.
**Targets without pseudoknots have a maximum of 50**, not 100. Ranking is OpenKnot score,
then RNet-SS pair F1, then stable candidate ID. No experimental score cutoff is assumed.
These predictions do not establish RNA function, catalytic activity, or a matching 3D fold.

See [runtime and recovery](docs/IMPLEMENTATION.md), [scientific definitions](docs/SCORING.md),
and [implementation evidence](evidence/README.md). The supplied web prototype is untouched
and is not a dependency of this application.

Exit codes: 0 success (including a completed empty shortlist), 2 invalid input/config,
3 missing/unsupported capability, 4 partial execution, 130 interruption. Progress goes
to stderr. Add `--json` for machine-readable output.

## Development

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests scripts
```

Real model checks are opt-in: `RNA_DESIGN_INTEGRATION=1 .venv/bin/pytest -m integration`.
No mock model is exposed by the production CLI.
