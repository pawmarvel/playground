# PawMarvel Template Generator MVP

A lightweight Python toolset for testing a profile-driven personalized pet
design workflow from reusable preview artifacts through a high-resolution print
candidate.

The MVP provides:

- `pawmarvel-generate`: prompt-driven OpenAI or Gemini generation/editing with
  a sample image, a pet image, or both; manual profile mode derives the selected
  preview-art or transformed-pet size from `product-profile.json`.
- `pawmarvel-layout-config`: localhost visual editor for `layout.json`, using
  explicit screenshot pet/name regions for reference-guided initial geometry,
  plus exact visible text for advisory, confidence-gated ranking across the
  local OFL font catalog.
- `pawmarvel-render`: deterministic Pillow composition of art, pet, and a
  font-rendered name in the MVP.
- `pawmarvel-poc-run`: one-command pet transformation and final preview test.
- `pawmarvel-pipeline`: scratch/debug orchestration for template authoring,
  tracked preview, and print preparation.
- `pawmarvel-upscale-template`: prepare reusable print art/layout once per
  template.
- `pawmarvel-upscale-pet`: prepare only the customer transformed-pet layer and
  bind it to approved print geometry.
- `pawmarvel-upscale`: convenience coordinator for both scratch operations.
- `pawmarvel-product-profile`: derive reusable, exact-aspect preview art and pet
  dimensions from a product print canvas.
- `pawmarvel-author`: create immutable art, pet-runtime, and layout experiments;
  benchmark, compare candidates with immutable art/pet/layout contact sheets,
  prepare a hash-bound print finalist, graduate and trace a reviewed selection,
  record publication, safely clean up losers, and initialize a private
  per-design operation configuration.
- `pawmarvel-bundle`: build a complete immutable revision from a reviewed
  `selection.json` only.
- `pawmarvel-catalog`: validate bundles and build a canonical exchange-root
  release catalog whose entries bind to exact bundle-manifest hashes, then
  explicitly publish a reviewed release to S3.

## Documentation

- [MVP operations guide](docs/MVP_OPERATIONS_GUIDE.md)
- [MVP architecture and bundle contract](docs/MVP_PRODUCTION_BUNDLE_CATALOG_DESIGN.md)
- [Future iterations](docs/FUTURE_PERSONALIZATION_ITERATIONS.md)

Design folders such as [`examples/life-is-good`](examples/life-is-good) and
[`examples/charlie-well-trained`](examples/charlie-well-trained) contain their
finished reference plus independently editable `-gpt.md` and `-gemini.md` art
and pet-transformation prompts.
Reusable customer-pet fixtures live in
[`examples/pet-inputs`](examples/pet-inputs).
These reference and pet images are development inputs for exercising the
offline workflow; the repository does not assert production-use rights for
them. Generated bundles remain outside Git and are shared with FE through the
reviewed S3 release catalog. During the MVP trial, production images receive a
lightweight operator source/rights review rather than a formal license gate, and
QA replay pets must not contain customer data.

The examples are copy sources, not live authoring inputs. For each design,
operators copy the selected reference, provider prompts, and any valid optional
layout/font references into ignored `work/design-inputs/<design-id>/`.
`pawmarvel-author init-config` points generated configuration at that private
folder and leaves optional reference variables empty when their files are not
present. Experiments then snapshot the exact source files they consume.

## Install

```bash
cd TemplateGenerator
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Install the test extra when running the repository contract suite:

```bash
.venv/bin/python -m pip install -e '.[test]'
```

## Commands

```bash
.venv/bin/pawmarvel-generate --help
.venv/bin/pawmarvel-layout-config --help
.venv/bin/pawmarvel-render --help
.venv/bin/pawmarvel-poc-run --help
.venv/bin/pawmarvel-pipeline --help
.venv/bin/pawmarvel-upscale --help
.venv/bin/pawmarvel-upscale-template --help
.venv/bin/pawmarvel-upscale-pet --help
.venv/bin/pawmarvel-product-profile --help
.venv/bin/pawmarvel-bundle --help
.venv/bin/pawmarvel-author --help
.venv/bin/pawmarvel-catalog --help
```

Tool 1 keeps `gpt-image-2` as its default. Pass `--provider gemini` to use the
GA `gemini-3.1-flash-image` default, or pass a concrete `gemini-*` model and let
provider auto-detection select Gemini. Credentials come from `--api-key-file`,
`OPENAI_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY` as appropriate. Gemini
key files must be plain text. The layout and render tools work offline. The POC
runner exposes the same provider/model options and calls Tool 1 once followed
by the renderer once.
It can instead reuse `--transformed-pet` with an explicit `--layout`, which is
the no-generation path used to inspect a prepared print bundle.
For the full authoring flow, `pawmarvel-author init-config` creates a mode-0600,
sourceable template below ignored `work/configs/` for provider keys, input
paths, model defaults, local roots, and S3/AWS settings.

Use `pawmarvel-pipeline` when starting from a new finished design. It generates
art with the design's art prompt, transforms a representative pet using the
design's pet prompt plus one or more ordered finished references, waits for the
local layout editor to be saved and closed, then renders a preview/debug pair and
can continue through print upscaling, print rendering, and `run.json`
provenance. It makes image API calls only; it does not derive prompts through a
text model. Pet names are always rendered with the bundled font. It has no
publication option. Production publication uses `pawmarvel-author graduate`,
selection-only `pawmarvel-bundle`, then
`pawmarvel-catalog build-release`. `pawmarvel-catalog publish-s3` is a separate,
dry-run-by-default graduation step; no generation or experiment command uploads
anything. Executed publication requires `--authoring-root` and writes an
idempotent local publication receipt only after every S3 object is verified;
that receipt preserves revision allocation safety after local exchange cleanup.
A product profile derives preview art,
transformed-pet, and print dimensions rather than trusting screenshot pixels.

Repeat `--sample-design` to add supporting references. The first reference is
the primary design and the only one used by the layout editor; later references
are ordered supporting visual evidence for art and pet generation. Pet-runtime
experiments enforce the bundle contract's maximum of four total ordered
finished-design references before making any paid generation call.

After a successful run, repeat the same command with `--rerun-step art`,
`--rerun-step pet`, or `--rerun-step layout` to replace only that authoring
stage. The option is repeatable, and the pipeline then refreshes preview,
provenance, and requested print outputs. Layout-only reruns are
offline. Selective reruns require the existing `run.json` and cannot be combined
with broad `--force`.

All generated work and private authoring paths are scoped by both design and
product profile, for example
`work/life-is-good/blanket-king-9375x12375/` and
`work/authoring/life-is-good/blanket-king-9375x12375/`. The ignored local tree
keeps art, transformed-pet, and layout attempts available for low-latency
comparison and reruns. `work/exchange/` is only a local staging/cache for a
reviewed immutable release; Amazon S3 is the shared production bundle store.
A reference may seed another
product profile, but generated art, prompt snapshots, layouts, attempts, and
selections must not be shared through a design-only mutable directory.

After preview inspection, run `pawmarvel-upscale-template` once for the reusable
art/layout and `pawmarvel-upscale-pet` for each transformed pet. No
`layout.snapshot.json`, pipeline `run.json`, or approval artifact is required.
In immutable authoring, `pawmarvel-author prepare-print --reuse-template-from`
can hash-verify and reuse a prior finalist's print art/layout/font while
upscaling only a newly selected representative pet.
The final `pawmarvel-render` invocation validates both manifests against the
profile and writes a final-review manifest. A profile whose vendor requirements
are not confirmed produces a print candidate, not an automatic vendor-ready
certification.

Design-specific prompts are treated like source code. For pet transformation,
`pawmarvel-generate` sends the customer pet first for identity, then the finished
design for pose, expression, crop, and style with either provider. Gemini uses
native aspect-ratio/resolution tiers; its response is fitted without cropping
onto the exact requested CLI canvas. Gemini transparency is prompt-driven and
cannot be guaranteed because its image model does not support transparent
background output. A Gemini result that uses its permitted flat-white fallback
requires a separate background-removal/matting step before it satisfies the
bundle alpha contract. Working directories and
published bundles do not carry customer source data. Each bundle carries its
selected `art-template-{gpt|gemini}.md` and the MVP production
`pet-transform-gpt.md` contract file without removing the art provider
category; `bundle.json` identifies both paths, exact runtime request fields, and
an operator-reviewed, non-customer QA replay input/output pair.

The layout editor compares the checked-in 40-face OFL catalog with lettering in
the reference. The catalog is the default when neither `--font` nor
`--font-catalog` is supplied. The matcher uses an operator-confirmed screenshot
region and its exact visible text, normalizes glyph silhouettes, and displays
the 15 best matches. The initial preview uses rank one and its calibrated
nominal/minimum font sizes. A high-confidence winner can be accepted
automatically; medium/low confidence requires explicit selection before Save
even though rank one is previewed. The source region and
ranking remain private authoring evidence. The operator's final selection is
saved in `layout.json`, and only that TTF and its `OFL.txt` are published.
The editor can also calibrate one fixed nominal size from the reference text's
visible fill and preserves that typography scale when its name box is resized.
An optional hash-bound `layout-reference-v1` artifact records the reference pet
and name regions. The editor maps their normalized edges onto a new product
canvas to seed both boxes, then requires the operator to review the real
generated art and transformed pet. This is an authoring heuristic, not an FE or
print contract; the reviewed `layout.json` remains authoritative.
Supplying `--font` remains an explicit override; macOS system fonts are not
valid bundle inputs. Layout schema v2 is composition-only. It rejects schema v1
and any provider/model field; runtime routing belongs exclusively in the
selected experiment and `bundle.json.runtime`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests mock image-generation requests and do not consume API credits.
