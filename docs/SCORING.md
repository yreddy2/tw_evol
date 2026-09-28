# Scientific contract implemented in v0.1

The generator uses unmodified upstream gRNAde source at
`2453b18778a1981ac25f314790e5395f1e9ab218` and the 3D-dropout checkpoint in
`test_2d` mode. The graph is built exactly through the upstream featurizer with
missing-coordinate sentinels, the full reference sequence for its length/alphabet mask,
and the saved dot-bracket target. The upstream `sample` method consumes structure features
and previously sampled nucleotides, not the reference nucleotide identities. No logit
bias is supplied. No PDB is loaded. The wrapper bypasses the upstream design script's
unconditional PDB loading and built-in screening; it does not change neural weights,
graph features, or sampling code.

## Predicted OpenKnot score

`oksp-target-ecs-cpq-v1` follows the MIT-licensed
[OpenKnotScorePipeline scoring implementation](https://github.com/eternagame/OpenKnotScorePipeline/blob/23e24fe21a3ebe50046b0cce29bcb1c4a1a571b8/src/openknotscore/pipeline/scoring.py)
for **one supplied target**, full-construct region, `filter_singlets=False`.
It is not the paper's experimental predictor-ensemble procedure, nor a Matthews
correlation coefficient. Upstream-equivalence fixtures are recorded in the evidence.

- Use raw RNet channel 0, 2A3, as in the official gRNAde evaluator; save both channels.
- No clipping, rescaling, or percentile normalization is applied to model outputs.
- ECS is the percentage of target-unpaired positions with reactivity **>0.125** and
  target-paired positions with reactivity **<0.5**.
- CPQ is the percentage of nucleotides participating in crossing target pairs with
  reactivity **<0.25**. With no crossing pairs, upstream CPQ is zero.
- Predicted OKS = `(ECS + CPQ) / 2`, range 0–100; attainable range 0–50 without crossings.
- Reject nonfinite or wrong-length predictions; no position is silently omitted.
- Keep singlets, including when identifying crossings. The selected upstream primary
  implementation is used as the equivalence reference rather than gRNAde's modified
  serialized score helper, which has different singlet handling and crossing logic.

The score is target-specific reactivity agreement, not structural proof. RNet is a
post-generation screen here. No claim of exact study reproduction or unbiased
experimental validation is made.

## Structural metrics

RNet-SS uses separate weights. The official bundled implementation calls Arnie 0.2.7
Hungarian decoding with theta=0.5, minimum helix length 1 and |i-j|<4 masked. Save the
decoded structure, 1-based pairs, decoder parameters and checkpoint identity. The full
probability matrix is reproducible from the saved sequence and pinned runtime; it is
not serialized for every candidate.

Pair F1 = `2*TP/(len(predicted)+len(target))`. Both-empty sets have F1=1 and distance=0;
one-empty set has F1=0. Precision or recall with a zero denominator is null.
Pair distance is symmetric-difference cardinality. Crossed-pair recall is null if the
target has no crossing pairs. Bracket glyph choice does not change pair metrics.

ViennaRNA 2.7.2 independently folds every valid unique candidate, including those
excluded by the composition gate. Energy is kcal/mol. MFE ensemble frequency is computed
after partition-function rescaling with `fc.pr_structure(mfe_structure)`. It describes
the candidate MFE fold, not the design target. ViennaRNA does not predict pseudoknots.

## Distances, composition, and ranking

Both Levenshtein fields use unit insertion, deletion and substitution costs, no
transpositions, and raw integer distances. Sequence distance and Hamming distance can
differ for equal-length strings. The structure distance compares **two ViennaRNA MFE
strings**, never the supplied target override. Preserve the comparator strings and
folding settings. Sequence distance survives any folding/scoring failure. A failed fold
gives null structural distance with its error.

Percentages use full construct length and unrounded values for gates. Default ranking:
OpenKnot descending, RNet-SS pair F1 descending, full content-derived candidate ID ascending.
Both primary evaluations must succeed. No score, composition or distance gate is active
unless explicitly configured. Reference copies remain recorded but cannot be shortlisted.

The primary scoring source's MIT notice is included in `THIRD_PARTY_NOTICES.md`.
