# Curated local OFL authoring catalog

This directory contains the MVP's 40 locally available, print-oriented font
faces. The set covers bold/condensed, rounded/playful, handwritten, script,
slab/western, and retro/decorative pet-name treatments. Each family directory
contains one static `.ttf` face and its unmodified SIL Open Font License as
`OFL.txt`.

[`catalog.json`](catalog.json) records the selection role, source revision,
artifact paths, byte sizes, and SHA-256 hashes. All artifacts come from the
official Google Fonts repository at the immutable revision recorded there.

When neither `--font` nor `--font-catalog` is supplied,
`pawmarvel-layout-config` and `pawmarvel-pipeline` use this catalog by default.
The editor compares normalized lettering silhouettes against the reference,
using an operator-confirmed text region and the exact characters visible there.
It provides the 15 best candidates with separate similarity and conservative
confidence scores. Only a high-confidence winner may be selected
automatically; otherwise the operator must choose explicitly.

The selected family is copied into the template and remains the only font
published in the current runtime bundle. The larger catalog is an authoring
asset, not a frontend payload. Do not place system, proprietary, variable-only,
or license-ambiguous fonts in this directory.

Open-world OFL discovery is deferred to the future scaling roadmap.
