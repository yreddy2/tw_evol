# Struct2SeQ adapter (TW-017 / TW-018)

Struct2SeQ is the second selectable generator. It takes the secondary target already
produced by ViennaRNA or supplied explicitly. NA-MPNN is available through its separate mapped-backbone adapter (see `NA_MPNN.md`). Both 2D
generators run locally on CPU; neither requires a CUDA GPU for this workflow.

## Pinned implementation and access

- [Official source](https://github.com/Shujun-He/Struct2SeQ), revision
  `381b7f6aa33e0ba64b436294de4cccbc721ffdcd`, MIT license in its `LICENSE` file.
- [Author's public weights](https://www.kaggle.com/datasets/shujun717/struct2seq-weights),
  dataset version 1; metadata labels the weights dataset CC0-1.0. No login was needed.
- Variant: `Struct2SeQ_SHAPE.pt`, 119,736,901 bytes, SHA-256
  `63f6bfd12d4d4f7ac5d81a3c31c3afb9dff533dbcd404064296836951b5294ed`.
- The versioned download archive contains two variants and is about 222 MB. Setup
  verifies the archive and extracts only the selected checkpoint. Exact identities,
  source, licenses and scope are in `src/rna_design/struct2seq-lock.json`.
- A separate Python 3.11 / PyTorch 2.9.1 environment lives at `.runtime/struct2seq-venv`.
  `requirements-struct2seq.txt` pins the tested dependencies. The original PyTorch 2.1.2
  / PyG environment remains responsible for gRNAde and the shared RNet checkpoints.
  Reserve approximately 2 GB additional disk space for installation and cached downloads.

The upstream launch script uses `.cuda()`, but its transformer and batched sampler use
the tensor device. The adapter calls those functions directly on CPU, without modifying
the upstream source or weights. It loads the exact published architecture, applies the
official `delete_modules` checkpoint preparation, and uses strict, weights-only loading.
Only one model worker runs per campaign at a time: the generator exits before shared
evaluation starts. Thus reported peak RSS is the maximum individual worker, not a sum
of concurrently resident generator and evaluator models.

## Exact generation contract

The supported product envelope is **8–500 nt**, verified separately from gRNAde on
8-, 36-, 76-, 282- and 500-nt inputs. These are engineering feasibility checks, not
evidence of useful biological designs across that entire range. CPU is verified on
Apple Silicon macOS. Linux and CUDA installation recipes are unverified here; requesting
an unavailable CUDA device fails. MPS is not offered.

All reference positions are mutable. The reference establishes sequence length and
the default target; its bases do not bias the generator. The adapter supplies the
full target to the official tokenizer, a symmetric float32 contact matrix including
the diagonal, and zero-based partner mappings converted from the pipeline's validated
one-based pairs. No sequence is cropped, padded, patched, or rescued. Extended targets
support `.()[]{}<>` and alphabetic bracket levels; the exact notation is preserved.

Generation calls `Functions.generate_sequence_batched_sample` with no weight or bias
override. `sampling_probability` is the probability of sampling from the native
softmax rather than taking argmax at each step. Its default is 1.0; 0 means greedy.
It is **not a neural temperature**. gRNAde's `sampling_temperature`, reference-bias
controls, alternative checkpoint variants, external SHAPE profiles, and search/rescue
controls are rejected for this adapter instead of silently ignored.

The official sampler masks right-hand bases according to previously generated pairing
partners (AU, UA, CG, GC, GU), applies repeat heuristics, and discourages additional A
after the count exceeds 40% of target length. These native heuristics have precedence
exceptions and are **not exact composition guarantees**. The shared optional C% gate
still evaluates the actual returned sequence. Native constraints may limit sequence
diversity, particularly with greedy sampling. Duplicate proposals retain their generation
events and consume budget; they do not fill the unique shortlist repeatedly.

The SHAPE name identifies the checkpoint variant. No SHAPE profile is consumed during
this inference path. No extra scientific interpretation is inferred from that name.
The upstream sampler does not return a calibrated score or perplexity, so native score
fields remain empty rather than manufacturing one.

## Budgets, provenance and evaluation

One requested proposal equals one autoregressive sampled sequence in this adapter.
Each batch records sampled sequence count, positions per sequence, zero search rounds,
zero rescue proposals and zero generation-time scorer calls. Generation events reference
this effort with scope `batch`; do not sum the repeated batch counts across events.
Attempts, including interrupted replay, follow the existing hard attempt cap. Target
identity, exact checkpoint, source revision, package versions, seed, batch size and
sampling controls are stored in the sealed run and generation records. Determinism was
verified for the same seed/device/batch; changing batching or runtime can change outputs.

The original RNet, RNet-SS, ViennaRNA, filtering and ranking implementations are reused
without scientific changes. Distances still compare against the original input and
its reference MFE, even when the design target is overridden. `generator` is included
in candidate CSV/JSONL exports; the reference control is left unattributed. Selection,
export, budget accounting, immutable recovery and failure behavior are shared.

The author's launch script combines multiple sampling policies with RNet/RNet-SS
screening and optional rescue. This adapter implements its native stochastic proposal
path, not that entire search procedure. Struct2SeQ is described as trained/guided using
RNet; shared RNet scores therefore do not establish an unbiased model leaderboard or
independent validation. Reports disclose this coupling.

## Running and checking

```bash
.venv/bin/python scripts/setup_struct2seq.py
.venv/bin/rna-design doctor --model struct2seq
.venv/bin/rna-design validate --config examples/struct2seq.yaml
.venv/bin/rna-design run --config examples/struct2seq.yaml --out runs/struct2seq-example
.venv/bin/rna-design resume runs/struct2seq-example
.venv/bin/rna-design models list
```

The base `scripts/setup_runtime.py` installation is also required for shared evaluation.
`doctor` checks prerequisites, not inference quality. A real smoke check is
`doctor --config examples/struct2seq.yaml --smoke --out runs/struct2seq-doctor`.
Setup is explicit; validation and inference never download models or upload input RNA.
Old gRNAde manifests remain resumable because the original scientific worker and
runtime identities are unchanged.

To reproduce verification using fresh run directories:

```bash
RNA_DESIGN_INTEGRATION=1 .venv/bin/pytest -m integration
.venv/bin/python scripts/verify_runtime.py --model struct2seq --out runs/struct2seq-verification-new
.venv/bin/python scripts/verify_recovery.py --model struct2seq --out runs/struct2seq-recovery-new
```

Measured outcomes are in `evidence/struct2seq-length-feasibility.json` and
`evidence/struct2seq-recovery-real.json`. The source review and adapter release work
cover TW-017/TW-018. TW-019 (multi-family campaigns, deduplication/comparison reports,
and external FASTA scoring) is partially implemented: `--model both` now runs both
families against one target, scores their deduplicated pool once, and exports one
combined `ranked.csv` with model provenance and per-model accounting. Ranking uses
the existing common scores, not model-native values. External FASTA scoring and richer
distribution comparison reports, general fixed-region controls remain deferred. NA-MPNN and explicit fixed-context reconstruction are implemented separately.
