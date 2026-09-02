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
preselects the highest-scoring font, and provides the five best candidates with
visual-confidence scores for manual override.

The selected family is copied into the template and remains the only font
published in the current runtime bundle. The larger catalog is an authoring
asset, not a frontend payload. Do not place system, proprietary, variable-only,
or license-ambiguous fonts in this directory.

`expanded-catalog.json` and its cache loader remain for compatibility with
earlier experiments. They are not part of the current local-catalog MVP.
Open-world OFL discovery is deferred to the future scaling roadmap.
