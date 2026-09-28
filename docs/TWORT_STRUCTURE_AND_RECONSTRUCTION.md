# Twort: why the sequences differ and how this run handles it

The original input is **282 nucleotides**. The supplied crystal structure contains a **229-nucleotide ribozyme in chain A**, plus a separate **4-nucleotide product in chain B**. These are different constructs. The current comparison designs the 229-nucleotide chain and restores 53 positions from the original sequence before scoring each full 282-nucleotide candidate.

This document records the sequence issue, the explicit reconstruction convention, and what the results do—and do not—show. The original `twort_seq.txt` and supplied PDB are unchanged.

## The three sequences

| Input | Length | Role |
|---|---:|---|
| `twort_seq.txt` | 282 nt | Original reference for all final sequence and structure comparisons |
| 1Y0Q chain A | 229 nt | Engineered crystal ribozyme; input to all three generators in this run |
| 1Y0Q chain B | 4 nt (`GCUU`) | Separate bound product; excluded from generation and reconstruction |

Both the deposited sequence and modeled coordinates for chain A contain 229 residues. Therefore the 53-nucleotide difference is **not simply 53 unresolved residues in this PDB**. The [RCSB entry for 1Y0Q](https://www.rcsb.org/structure/1Y0Q) describes the engineered Twort group I ribozyme–product complex. The [associated paper](https://www.nature.com/articles/nsmb868) describes a ribozyme derived from the Twort intron bound to a four-nucleotide product. The local PDB also labels the construct as engineered.

The files alone do not establish the experimental provenance of every difference between your 282-nt input and the crystallized construct. We therefore describe the exact sequence correspondence rather than asserting why each nucleotide was removed or substituted.

Exact sequences are saved together in [sequences.fasta](../evidence/twort-1y0q-comparison/sequences.fasta). The alignment is saved in [alignment.txt](../evidence/twort-1y0q-comparison/alignment.txt).

## Why NA-MPNN cannot directly redesign all 282 positions from this PDB

The [official NA-MPNN model](https://github.com/baker-laboratory/NA-MPNN) designs sequences conditioned on backbone coordinates. The supplied structure does not provide a matching 282-position backbone. gRNAde 2D and Struct2SeQ can already use the original sequence through its predicted secondary structure, but a full 282-position NA-MPNN redesign needs a corresponding 3D structure.

For this run, all three models use the shorter construct. gRNAde and Struct2SeQ receive its ViennaRNA-predicted secondary target. NA-MPNN receives the observed chain A backbone; it does not receive that secondary target. All models run locally on CPU.

## Explicit sequence reconstruction

The requested workflow restores the original sequence's omitted sections **at their original positions**, after generation. “Restore” refers to the primary sequence; it does not mean append all missing bases at the 3′ end or build missing coordinates.

Coordinates below are **1-based and inclusive**.

| Original reference positions | Short design positions | Treatment |
|---|---|---|
| 1–23 | None | Copy original `AACUACUGAAAGCAUAAAUAAUU` unchanged |
| 24–77 | 1–54 | Insert model-generated bases |
| 78–92 | None | Copy original `AACAAACUAAGAUGU` unchanged |
| 93–267 | 55–229 | Insert model-generated bases |
| 268–282 | None | Copy original `AACUUUUAGUUAUUU` unchanged |

Every final candidate is therefore **229 designed bases + 53 fixed bases = 282 bases**. All 53 fixed positions are checked against the original reference.

The selected correspondence also contains two base differences:

| Short position | Crystal residue | Original position | Original base | Crystal base | Treatment |
|---|---|---|---|---|---|
| 2 | A:10 | 25 | U | A | Mutable; retain the generated base |
| 216 | A:239 | 254 | U | C | Mutable; retain the generated base |

We do not overwrite these two designed positions with the original bases after generation. The original 282-nt sequence remains the comparison reference and is scored separately as a control.

### Alignment ambiguity

Global alignment with match +2, mismatch −3, gap opening −6 and extension −0.5 gives **four equal-scoring alignments**. Two choices concern placement of the leading gap, and two concern a one-base shift of the internal gap across repeated sequence. The numerical alignment score cannot establish the biologically correct correspondence.

The chosen convention uses a contiguous 23-base leading section and places the internal gap after short position 54. That position coincides with the PDB author-numbering break from A:62 to A:78. This supports an inspectable convention; it is not experimental validation of a full-length structural mapping. The machine-readable [reconstruction mapping](../examples/twort-reconstruction.json) saves all alternative alignment coordinates, the selected reference positions, both acknowledged base differences and the limitations. The CLI consumes this explicit mapping; it never infers a fresh alignment during a run or resume.

## Missing atoms within the 229-residue crystal chain

The observed residues A:9 and A:78 lack phosphate atoms P, OP1 and OP2. All 229 residues have the remaining required RNA sugar/backbone atoms. The run explicitly selects `missing_atoms: mask_phosphate`, using NA-MPNN's native atom-validity masks. No coordinates are invented. Other missing required atoms, ambiguous alternate locations, noncanonical RNA and zero-occupancy required atoms block validation.

This atom issue is separate from the difference in construct lengths. A residue can exist in the coordinate model while some of its atoms are absent. Without the explicit policy, validation rejects this backbone.

## Comparisons and output

[The run configuration](../examples/twort-all-reconstructed.yaml) requests **10 proposals per model**, 30 total, with a shared top-k of 30. The models generate the short sequences first. The pipeline then reconstructs each full candidate, deduplicates the full sequences, evaluates each unique candidate once, and ranks the combined pool.

The main [combined ranked CSV](../runs/twort-all-reconstructed/exports/ranked.csv) includes:

- `sequence`: reconstructed 282-nt candidate, used for all reported scoring and comparisons.
- `designed_sequence`: actual 229-nt model output.
- `generator`: originating model; multiple names are retained if models produce the same sequence.
- `designed_length`, `fixed_context_length`, `design_policy`, `reconstruction_id`: explicit reconstruction provenance.
- Sequence edit distance and identity to the **original 282-nt Twort**, plus differences between candidate/reference predicted secondary structures and the shared prediction scores.

The full-length evaluation target is predicted from the original 282-nt sequence. The short generation target is saved separately. The original reference control has a separate CSV and is not ranked as a novel design. Duplicate, failed or filtered proposals may reduce the number of eligible unique rows; proposal counts and per-model accounting remain visible in the run report.

## Interpretation

These candidates are **short-construct designs assembled with fixed original context**. They are not full-length backbone designs. The restored regions can interact with the redesigned regions and alter the fold; scoring the entire reconstructed sequence helps inspect predicted effects but cannot verify a matching 3D fold or catalytic/splicing activity. In particular, NA-MPNN never saw coordinates for the restored sections or the separate product chain.

Keeping 53 positions identical also increases sequence identity relative to a full 282-position redesign. Results should be compared under this same fixed-context policy. The two earlier models' previous full-redesign runs used different design constraints and conditioning inputs.

A future matching 282-nt experimental or predicted backbone would permit a separate full-length NA-MPNN experiment. General sequence-to-3D prediction is outside the current CLI's scope. Struct2SeQ was trained/guided using RNet, so the shared RNet scores are screening predictions rather than independent experimental validation or an unbiased model leaderboard.
