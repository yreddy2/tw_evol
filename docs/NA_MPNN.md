# NA-MPNN adapter

The third generator uses the official RNA-capable **NA-MPNN design checkpoint**, locally on CPU or explicitly requested CUDA. CPU operation is verified on this Apple Silicon Mac. The upstream Python class is named `ProteinMPNN`, but this adapter instantiates `model_type="na_mpnn"` with the 33-token nucleic-acid vocabulary, RNA backbone features and the official NA-MPNN weights. It does not use a protein-only checkpoint or claim to reproduce the paper's exact MPNN-fixbb search variant.

## Pinned runtime

- Official source: https://github.com/baker-laboratory/NA-MPNN
- Revision: `9fabc2482092b725e067969fba21297a806b6fda`
- Checkpoint: `models/design_model/s_19137.pt`, 27,667,482 bytes
- SHA-256: `5fb12951a0ad3ee44b91f33b7dd19114e755b49eb577d9eb1be71b24b7e49e4c`
- Runtime: Python 3.12, torch 2.5.1, NumPy 2.3.3, ProDy 2.6.1; complete pins in `requirements-na-mpnn.txt`.
- Setup: `scripts/setup_na_mpnn.py`; source remains unmodified in `.runtime/sources/na_mpnn` and the runtime is `.runtime/na-mpnn-venv`.
- Source, checkpoint, worker and package identities are included in each immutable run manifest. Existing gRNAde, Struct2SeQ and evaluator environments remain separate.

Install the CLI `structure` extra for Biopython 1.86 PDB/mmCIF parsing. Lightweight sequence-only commands retain lazy scientific imports. Inference does not require OpenBabel; the upstream inference modules exercised here use torch, NumPy and ProDy. The base runtime provides the shared RNet/RNet-SS evaluators. `doctor --model all` checks all three generator runtimes and the shared evaluators.

## Backbone contract

`input.backbone` requires a PDB/mmCIF path, explicit chain ID and `mapping: exact_sequence`. Exactly one coordinate model and one selected canonical RNA chain are supported. The sequence of observed residues must exactly match the design sequence in length and bases. Author residue numbers, insertion codes and chain labels remain in the mapping report; numeric gaps are reported. The native featurizer handles residue indexing internally, while original IDs remain in the saved mapping.

The required observed RNA atoms are P, OP1, OP2, O5′, C5′, C4′, O4′, C3′, O3′, C2′, O2′ and C1′. The default missing-atom policy is `error`. An explicit `mask_phosphate` policy allows only absent P/OP1/OP2 using upstream atom masks. The sugar atoms used by the model's own pseudo-base feature must be observed. No missing coordinates are fitted or invented. Modified/noncanonical polymer residues, ambiguous alternate positions/identities, invalid occupancies and nonfinite coordinates fail validation. Other chains and nonpolymer residues are excluded and reported.

The CLI's tensor assembly for a single RNA chain is checked field-for-field against the official `parse_PDB` plus `featurize` path on 1Y0Q, including the absent phosphate atoms. All 229 residue and design masks are active; incomplete residues are not silently frozen.

`inspect-backbone` reports observed sequence, mapping, missing atoms, numbering gaps, excluded chains and incompatibilities. An incompatible report exits 3. A full-length 282-nt Twort reference with the 229-nt 1Y0Q chain is rejected unless the separate explicit reconstruction workflow is configured.

## Sampling and provenance

The adapter calls the upstream native autoregressive sampler directly, with 128-dimensional node/edge/hidden features, three encoder and three decoder layers, 32 neighbors, eval mode and no coordinate perturbation. Sampling temperature defaults to 0.1. The seed is set for Python, NumPy and torch; per-model batches derive deterministic seeds from the run seed.

Native shared tokens 21/22/23/24 represent RNA A/C/G/U. The sampler's token-omission bias allows those four only. Protein, unknown, padding and duplicate legacy RNA tokens are excluded; decoding unexpected tokens fails. Native per-sequence negative log-likelihood is retained as provenance, not substituted for the shared ranking score. There is no reference-sequence logit bias or internal RNet search/rescue.

NA-MPNN consumes backbone coordinates. It explicitly returns no secondary-target receipt; the pipeline checks the consumed backbone identity instead. gRNAde and Struct2SeQ continue to report their consumed secondary target. The separate common target is used for evaluation. This distinction is preserved in generation events and reports.

## Explicit short-design reconstruction

Optional `input.reconstruction` supplies a `design_sequence` file and an explicit JSON `mapping`. The ordinary `input.sequence` remains the full comparison reference. The map specifies the exact reference and design strings, one strictly increasing 1-based reference position per design base, a strategy description and acknowledged base mismatches. Every unmapped reference position is copied unchanged.

Models generate the short sequence; the adapter assembles it into the full reference before deduplication, scoring, filtering and ranking. All models use the same mapped/fixed positions. The 2D generation target is predicted from the short input. The shared evaluation target is predicted from the full reference, or supplied as a full-length target override. Reports, CSV and JSON distinguish the two targets and both sequence lengths. This is an explicitly supported fixed-context mode, not general regional constraints or 3D reconstruction.

Backbone, reconstruction and short generation target are sealed with the manifest and independently saved. Original structure, short sequence and mapping bytes are snapshotted. Resume reads sealed inputs and rejects edited snapshots or mapping artifacts. See [the Twort issue document](TWORT_STRUCTURE_AND_RECONSTRUCTION.md) for the actual alignment choice and limitations.

## Combined mode and evidence

`--model both` still means gRNAde + Struct2SeQ. `--model all` adds NA-MPNN. Proposal/attempt quotas are per model, while wall-time and shortlist size apply to the run. One model worker runs at a time; scoring uses the original common worker. The default continuation policy scores available candidates and marks the run partial if a generator fails. Resume retries unfinished batches within their attempt caps, preserving seeds and completed results.

The real Twort experiment produced **10 unique sequences per model**, then evaluated all **30 reconstructed 282-nt candidates plus the reference** and ranked them in one CSV. Native feature equivalence, same-seed repeatability, different-seed variation and canonical outputs are saved in `evidence/na-mpnn-native-verification.json`. `evidence/all-twort-reconstructed.json` audits every reconstructed sequence, both edit distances, fixed positions, provenance and global CSV ordering.

The configured input envelope is 8–500 nt, a product cap rather than a claim that every length is verified. NA-MPNN's locally verified backbone length is **229 nt**; shared evaluators scored the assembled 282-nt outputs. Other NA-MPNN lengths and CUDA are unverified here. Engineering tests cover failure, interruption/replay, three-model overlap, exact mapping, PDB/mmCIF parity, atom validation and immutable recovery. Existing two-model runs remain resumable.
