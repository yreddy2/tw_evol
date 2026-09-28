# Example RNA backbone

`1Y0Q.pdb` is a byte-for-byte copy of the supplied `1Y0Q_1789659948106.pdb`
context asset. [RCSB entry 1Y0Q](https://www.rcsb.org/structure/1Y0Q) describes
the engineered Twort ribozyme–product complex.

SHA-256: `7dde4f0a10009bd672f173fedf1bc8c07fd14efa4576945a612c2de0e6c1da73`

Chain A contains 229 RNA residues; chain B is a separate four-nucleotide product.
The example configuration selects chain A and explicitly masks missing phosphate atoms.
See [the reconstruction document](../../docs/TWORT_STRUCTURE_AND_RECONSTRUCTION.md)
for the 282-nt reference mismatch, mapping choice and limitations.

This small fixture is included so tests and example configurations do not depend on
the separate historical prototype repository.
