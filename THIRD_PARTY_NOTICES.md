# Third-party scientific implementations

The score implementation follows OpenKnotScorePipeline (Das Lab / Eterna),
https://github.com/eternagame/OpenKnotScorePipeline, revision
23e24fe21a3ebe50046b0cce29bcb1c4a1a571b8. Its license is reproduced below.

The gRNAde, Struct2SeQ, NA-MPNN, RibonanzaNet and RibonanzaNet-SS code and weights are downloaded separately.
Their respective licenses remain with the downloaded source and model repositories;
see the runtime manifest for exact sources. No checkpoint is bundled with this package.

Struct2SeQ source: https://github.com/Shujun-He/Struct2SeQ, revision
381b7f6aa33e0ba64b436294de4cccbc721ffdcd, MIT, copyright (c) 2026 Shujun-He.
Its weights are from https://www.kaggle.com/datasets/shujun717/struct2seq-weights,
version 1, whose publisher metadata specifies CC0-1.0. The selected SHAPE checkpoint
and archive digests are in `src/rna_design/struct2seq-lock.json`. The source is checked
out unmodified, with its license retained; no upstream source or weights are vendored.

NA-MPNN source and included design checkpoint:
https://github.com/baker-laboratory/NA-MPNN, revision
9fabc2482092b725e067969fba21297a806b6fda. The top-level LICENSE is MIT,
copyright (c) 2025 Andrew Kubaney. Individual upstream files also contain BSD-3-Clause
University of Washington notices, including terms referring to model parameters.
Those notices are retained in the unmodified downloaded source; consult them for use
and redistribution. The pinned design checkpoint is `models/design_model/s_19137.pt`;
its SHA-256 is recorded in `src/rna_design/na-mpnn-lock.json`. No NA-MPNN source or
weights are bundled in the CLI package.

MIT License

Copyright (c) 2024 Eternagame

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
