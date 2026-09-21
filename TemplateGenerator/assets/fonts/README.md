# Curated local OFL authoring catalog

This directory contains the MVP's curated, locally available print-oriented
font faces. The set covers condensed display, general sans, rounded/playful,
handwritten, retro/vintage, premium serif, and western/outdoor pet-name
treatments. Each family directory contains one reviewed upright `.ttf` face and
its SIL Open Font License as `OFL.txt`.

[`catalog.json`](catalog.json) records each selected face's role, artifact
paths, byte sizes, tags, and SHA-256 hashes. Its top-level source revision pins
the Google Fonts snapshot used for the catalog. A later GUI-promoted family
preserves its own Google Fonts metadata and hashed source record in that family
directory.

When neither `--font` nor `--font-catalog` is supplied,
`pawmarvel-layout-config` and `pawmarvel-pipeline` use this catalog by default.
The editor compares normalized lettering silhouettes against the reference,
using an operator-confirmed text region and the exact characters visible there.
It provides the 15 best candidates with separate similarity and conservative
confidence scores. Only a high-confidence winner may be selected
automatically; otherwise the operator must choose explicitly.

The selected family is copied into the template and remains the only font
published in the current runtime bundle. The larger catalog is an authoring
asset, not a frontend payload. A font explored through the layout GUI may be
promoted here only after selection and review; preserve its `OFL.txt`,
`METADATA.pb`, and `source.json`, add its inventory entry to `catalog.json`, and
run the catalog test before commit. See the promotion procedure in the MVP
operations guide. Do not place system, proprietary, or license-ambiguous fonts
in this directory. A variable TTF is eligible only when its encoded default
instance is the intended face and renders consistently in both the Pillow
reference renderer and the target browser.

The GUI's remote lookup is restricted to the official Google Fonts OFL tree.
`remote-font-aliases.json` maps a small reviewed set of common style and family
terms to exact OFL family IDs. Runtime search verifies those directories
directly and never downloads GitHub's global font tree. Open-world discovery
outside that source remains deferred.

## Priority family coverage

The local catalog contains 109 faces. The 84 requested OFL priority families
are recorded in `catalog.json` under seven named groups, together with their
design-search tags. The remaining existing curated faces are retained because
they broaden display-style matching. The catalog is an offline search asset;
production bundles still include only the selected font and license.

Eight requested names are deliberately not catalog faces:

- Schoolbell, Coming Soon, Homemade Apple, Rock Salt, Just Another Hand,
  Smokum, and Special Elite are in Google Fonts' Apache collection. Adding
  them requires an explicit non-OFL license-policy and bundle-contract change.
- Alumni Sans Condensed is not a separate Google Fonts family. Use Alumni Sans;
  the separate Alumni Sans Pinstripe family is also available.

M PLUS Rounded 1c is included. Its TTF and metadata come from Google Fonts;
because the Google Fonts family directory currently omits the license file, the
catalog stores the upstream OFL text and records that exact URL in
`source.json`.
