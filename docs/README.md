# RNA Sequence Design CLI project plan

The first release is a local-first CLI that accepts a user's RNA sequence, predicts its secondary structure with ViennaRNA, generates different sequences targeting that structure with gRNAde in 2D mode, screens candidates with RNet and RNet-SS, and exports a ranked shortlist. Every nucleotide may change. Twort is one example input; the product supports arbitrary canonical RNA sequences within documented model limits. Model additions, tertiary-structure conditioning and partial-sequence preservation come later.

Every candidate report includes two separate Levenshtein distances: generated RNA versus the original input RNA, and its ViennaRNA-predicted dot-bracket structure versus the original input's predicted dot-bracket structure. These are first-release measurements, with no distance-based rejection enabled by default.

| Document | Contents |
|---|---|
| [Product requirements](PRD.md) | Scope, workflow, scientific definitions, CLI behavior, data contracts and release acceptance |
| [Milestones and tickets](MILESTONES_AND_TICKETS.md) | Seven milestones and 27 tickets with priorities, dependencies, owners and acceptance criteria |
| [Source review and decisions](SOURCE_REVIEW.md) | What came from the paper/slides, confirmed user choices, model sources and unresolved questions |

## Delivery sequence

1. **M0:** Verify the ViennaRNA -> gRNAde 2D workflow, evaluator compatibility across representative input lengths, and local runtime feasibility.
2. **M1-M3:** Build and release the gRNAde CLI with real scoring, resumable runs and exports.
3. **M4:** Add Struct2SeQ.
4. **M5:** Validate backbone mapping and add RNA MPNN.
5. **M6:** Add fixed/mutable regions, preservation profiles and diversity controls.

Runpod is the optional execution fallback when local inference cannot meet the needs of the selected stack. The initial product does not require a web app, backend service, model training, or automated cloud provisioning.

Sequence input alone is sufficient. The "reference" is simply the input sequence for the current run; its predicted structure becomes the default target. An optional supplied structure overrides that prediction when the user already has an annotation. No structure file or biological annotation is required for normal use. Twort and its mismatched crystal structure are regression examples, not required runtime assets.

The base CLI is now implemented at the repository root. Start with [the application README](../README.md), [operating instructions](IMPLEMENTATION.md), and [verification evidence](../evidence/README.md). The PRD remains the product contract; the prototype supplied context only and is not a runtime dependency.

The v0.4.0 third-model implementation is documented in [NA_MPNN.md](NA_MPNN.md). The Twort construct mismatch and user-requested reconstruction are explained separately in [TWORT_STRUCTURE_AND_RECONSTRUCTION.md](TWORT_STRUCTURE_AND_RECONSTRUCTION.md).
