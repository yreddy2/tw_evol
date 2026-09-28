# Source review and decision log

Reviewed: 2026-09-22. This review distinguishes what the sources show, what the user decided, and what still needs experimental or implementation verification. No models were installed or run while preparing the PRD.

## 1. Source material

### Project presentation

`Twort Evolution.pdf` (original local context, not committed), especially slides 5, 19 and 20; all slide text reviewed and the main workflow diagrams inspected visually.

- **Slide 19, Sequence Generation:** reference sequence and MFE secondary structure feed three named families: Struct2SeQ, MPNN and gRNAde. Dotted connections depict crystal-structure input. Composition filtering precedes the final candidates.
- **Slide 20, AI parameters:** proposes different sequence/structure emphasis, thousands of generated sequences, and later preservation of the 5' portion while redesigning a region containing P7.1-P9.1.
- **Slide 5, Optimizing Oligo search:** adds structural comparison, C%, and MFE frequency. It motivates reporting composition, sequence change, structural agreement and ensemble behavior as distinct quantities.
- **Slides 3, 8 and 15-17:** emphasize catalytic and long-range interactions. They justify preserving coordinate mappings and cross-region pairs when region controls are eventually added, rather than designing isolated halves without context.

The diagram expresses a desired product workflow. Its arrows are not proof that each model supports identical native inputs or a common numeric weighting control.

### Research paper

`de_novo_design_paper.pdf` (original local context, not committed): *De novo design of RNA pseudoknots with deep learning*, Townley and colleagues, supplied preprint dated May 22, 2026. Main text and figure captions reviewed; relevant figures rendered for visual inspection.

- **Pages 3-4:** RNet-guided filtering improved later design rounds; the work includes multiple model variants and longer challenge targets up to 240 nt.
- **Page 4:** identifies a gRNAde variant without 3D input and describes Struct2SeQ as trained/guided by RNet.
- **Pages 4-6 and Figures 3-4:** distinguish secondary-structure screening from more direct experimental evidence about base pairs and 3D folds.
- **Page 6:** supports combining model families because they sample complementary sequence regions. It does not establish a universal best generator.
- The supplied file references supplementary Methods and Table S2 but does not include their full contents. Exact reproduction of the paper's model variants and scoring pipeline therefore requires additional version-specific verification. The PRD does not claim to reproduce the study.

The paper's experimental OpenKnot success threshold is not a demonstrated threshold for Twort function, and a predicted OpenKnot score is not an experimental measurement.

### Prototype and supplied data

The prototype's documentation and user-facing input/output surface were reviewed to understand the general task. Its generation, scoring and adapter implementations were not adopted as design logic. Its documentation also describes different historical configurations, making it unsuitable as the authority for release scope.

On the user's follow-up question, the code path was inspected to confirm the existing behavior, without adopting its implementation: run creation (`RNA-Inverse-Folding-Workbench/artifacts/api-server/src/routes/rna.ts:67`, original local prototype) invokes ViennaRNA and defaults the design target to its predicted structure; folding (`RNA-Inverse-Folding-Workbench/artifacts/api-server/python/fold.py:17`, original local prototype) calls `fc.mfe()`; generation (`RNA-Inverse-Folding-Workbench/artifacts/api-server/python/grnade_runtime.py:332`, original local prototype) passes the target into the 2D model input; evaluation (`RNA-Inverse-Folding-Workbench/artifacts/api-server/python/grnade_no3d_runtime.py:170`, original local prototype) scores returned candidates with RNet. Candidates also undergo separate ViennaRNA refolding. This is source-code confirmation, not a successful runtime test.

Direct inspection of [twort_seq.txt](../twort_seq.txt) gives:

| Property | Observed value |
|---|---|
| Normalized length | 282 nt |
| Alphabet | A/C/G/U only |
| Counts | A=106, C=43, G=56, U=77 |
| C fraction | 43/282 = 15.2482...% |
| U fraction | 77/282 = 27.3049...% |

The included [1Y0Q structure asset](../examples/backbones/1Y0Q.pdb) identifies an engineered group I ribozyme-product complex. SEQRES and coordinate records contain 229 canonical RNA residues in chain A and four in chain B. Chain A's residue numbering is noncontiguous. This inspection establishes a mapping problem; it does not establish a biologically correct alignment or a complete backbone for the 282-nt reference.

No separate machine-readable target secondary annotation was found. This does not leave the default workflow incomplete: the product predicts a target from any supported input sequence. The 3D mapping issue is deferred with 3D-dependent models and is an example-specific regression case, not a prerequisite for the general gRNAde 2D release.

## 2. Primary upstream sources checked

These links informed the integration plan. Repository documentation is evidence of intended capabilities, not proof that the current local machine can execute them.

| Source | Finding used in the plan |
|---|---|
| [gRNAde repository](https://github.com/chaitjo/geometric-rna-design) and [design guide](https://github.com/chaitjo/geometric-rna-design/blob/main/docs/creating_designs.md) | Distinct 2D and 3D design modes; verify a 2D mode first and add backbone conditioning later |
| [Struct2SeQ repository](https://github.com/Shujun-He/Struct2SeQ) | Structure-conditioned inference, reference-bias controls and separate weights; exact variant and internal search accounting need a spike |
| [NA-MPNN repository](https://github.com/baker-laboratory/NA-MPNN) | RNA-capable fixed-backbone design; its output uses a nonstandard residue alphabet that requires correct decoding |
| [RibonanzaNet repository](https://github.com/Shujun-He/RibonanzaNet) | Separate chemical-mapping and secondary-structure inference resources |
| [OpenKnotScorePipeline](https://github.com/eternagame/OpenKnotScorePipeline) | Candidate upstream implementation for versioned common scoring; validate score semantics with upstream fixtures |
| [OpenKnot design data](https://github.com/eternagame/OpenKnotAIDesignData) | Potential public regression/calibration data, with attention to training and benchmark overlap |
| [OpenKnotAI source archive](https://zenodo.org/records/20649966) | Authors' archived source snapshots provide a path toward identifying study-specific versions |
| [ViennaRNA RNAfold manual](https://www.tbi.univie.ac.at/RNA/ViennaRNA/doc/html/man/RNAfold.html) | Partition-function output includes MFE frequency and ensemble diagnostics; preserve physical settings |
| [NIST Levenshtein definition](https://xlinux.nist.gov/dads/HTML/Levenshtein.html) | String edit distance uses insertions, deletions and substitutions; sequence and dot-bracket comparisons must remain separately named |
| [Runpod Pods](https://docs.runpod.io/pods/overview) and [connection options](https://docs.runpod.io/pods/connect-to-a-pod) | A GPU pod can host the same command-line workload; no hosted UI/API is required |

Read-only Runpod planning also used the local [Runpod skill](/Users/yadureddy/.agents/skills/runpod/SKILL.md), [usage guide](/Users/yadureddy/.agents/skills/runpod-usage/SKILL.md), and its development-pod example. No Runpod account inspection or provisioning was performed.

## 3. Confirmed product decisions

| ID | Decision | Basis |
|---|---|---|
| D-01 | Create the CLI before any UI | Original user request |
| D-02 | Add models one at a time, starting with gRNAde | User clarification |
| D-03 | First redesign the full sequence to seek the same structure with a different sequence | User clarification |
| D-04 | Preserve selected sequence regions in a later milestone | User clarification |
| D-05 | Prefer local execution; use Runpod GPU compute if CUDA is needed | User clarification |
| D-06 | Prototype is incomplete and contains unnecessary features; use only its general intent | Original request and follow-up note |
| D-07 | Chrome is signed into the user's Runpod account | User note; available for later implementation if needed |
| D-08 | The program accepts general RNA input; Twort is only an example | User correction after reviewing the initial plan |
| D-09 | Start with secondary-structure design; keep tertiary-structure conditioning for later | User follow-up; ViennaRNA -> secondary target -> gRNAde 2D is the initial workflow |
| D-10 | Include Levenshtein distance for both RNA sequences and predicted dot-bracket structures relative to the original input in the first release | User explicitly clarified that this means Levenshtein and dot-bracket strings, rather than a probability-matrix comparison |

## 4. Proposed implementation decisions

| ID | Proposal | Reason / decision gate |
|---|---|---|
| P-01 | Verify the selected gRNAde 2D checkpoint and runtime | Secondary-only scope is confirmed in D-09; TW-002 verifies execution compatibility |
| P-02 | Keep sequence length fixed | Makes reference and target coordinates unambiguous; indel design is a separate problem |
| P-03 | Use RNet reactivity scoring and RNet-SS pair metrics together | Avoids conflating paired/unpaired reactivity agreement with recovery of particular base pairs |
| P-04 | Default to 1,000 proposals and at most 100 unique shortlisted sequences | Adjustable starting budgets, not proven ideal sample sizes |
| P-05 | Report composition; leave C% filtering disabled unless configured | No biological C limit was supplied |
| P-06 | Implement Python core, isolated model runtimes and filesystem run artifacts | Supports a small local CLI and later adapters without application-server infrastructure |
| P-07 | Add Struct2SeQ next, then RNA MPNN | A second secondary-target workflow can precede the more complex backbone mapping work |
| P-08 | Use a GPU host only after measured local feasibility | Honors local preference without assuming CPU or Apple GPU compatibility |
| P-09 | Predict the input's target structure automatically; retain an optional explicit structure override | Sequence-only operation follows the clarified general-input requirement; no curated annotation is required |

## 5. Open decisions and risks

| Item | Why it matters | Resolution |
|---|---|---|
| Default target predictor accuracy | An MFE prediction can differ from the native structure, particularly with pseudoknots | Automatically predict for sequence-only input, preserve provenance, and allow an explicit override; no open user choice blocks the workflow |
| Supported input-length range | Model capabilities need verification beyond a single example or benchmark length | TW-002 multi-sequence/length smoke tests, including 282-nt Twort; TW-017 for the second model |
| Exact score/checkpoint variant | Shared names can hide different definitions; research model scores are not interchangeable | TW-003 fixture-based contract |
| Local runtime practicality | CPU support and usable speed are different questions | TW-002 benchmark before selecting local or GPU execution |
| Crystal-to-reference mismatch | Wrong mapping would condition a different construct | TW-020 before any backbone-dependent Twort design |
| No guarantee of functional preservation | Structural predictions do not demonstrate splicing activity | Keep computational and later experimental evidence separate |
| Reference-mask feasibility | Upstream generators may differ in native constraint support | TW-024 verifies each adapter before exposing controls |

General RNA input, ViennaRNA MFE target prediction, full-sequence redesign, gRNAde 2D-first delivery and local preference are settled. The product derives its target from the input by default. Runtime compatibility, supported lengths and performance require implementation verification; Twort-specific annotations do not block general use.

## v0.4.0 implementation follow-up

The sequence-only release discussion above records the original source review. NA-MPNN and explicit mapping are now implemented. The 282/229-nt mismatch remains real: the current user-requested comparison uses 229-nt designs reconstructed with 53 fixed original bases, as documented in [TWORT_STRUCTURE_AND_RECONSTRUCTION.md](TWORT_STRUCTURE_AND_RECONSTRUCTION.md). No compatible 282-nt backbone or biological validation of the sequence correspondence is asserted.
