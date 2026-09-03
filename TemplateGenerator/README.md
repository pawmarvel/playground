# PawMarvel Template Generator MVP

A lightweight Python toolset for testing a profile-driven personalized pet
design workflow from reusable preview artifacts through a high-resolution print
candidate.

The MVP provides:

- `pawmarvel-generate`: prompt-driven GPT Image 2 generation/editing with a
  sample image, a pet image, or both; manual profile mode derives the selected
  preview-art or transformed-pet size from `product-profile.json`.
- `pawmarvel-layout-config`: localhost visual editor for `layout.json`, using
  deterministic font-name preview in the MVP.
- `pawmarvel-render`: deterministic Pillow composition of art, pet, and a
  font-rendered name in the MVP.
- `pawmarvel-poc-run`: one-command pet transformation and final preview test.
- `pawmarvel-pipeline`: one-command template authoring, tracked preview, print
  preparation, and optional clean bundle publication using font-rendered name
  lettering by default.
- `pawmarvel-upscale-template`: prepare reusable print art/layout once per
  template.
- `pawmarvel-upscale-pet`: prepare only the customer transformed-pet layer and
  bind it to approved print geometry.
- `pawmarvel-upscale`: backward-compatible coordinator for both operations.
- `pawmarvel-product-profile`: derive reusable, exact-aspect preview art and pet
  dimensions from a product print canvas; optional screenshot normalization is
  diagnostic only and is not used by the pipeline.
- `pawmarvel-bundle`: publish a clean, OFL-licensed template bundle containing
  low-resolution web art/layout, high-resolution print art/layout, ordered
  finished-design references, both design-specific prompts, and a representative
  transformed-pet QA asset.

## Documentation

- [MVP operations guide](docs/MVP_OPERATIONS_GUIDE.md)
- [MVP design](docs/OFFLINE_PERSONALIZATION_TOOLSET_DESIGN.md)
- [MVP production bundle catalog design](docs/MVP_PRODUCTION_BUNDLE_CATALOG_DESIGN.md)
- [Future iterations](docs/FUTURE_PERSONALIZATION_ITERATIONS.md)

Design folders such as [`examples/life-is-good`](examples/life-is-good) and
[`examples/charlie-well-trained`](examples/charlie-well-trained) contain their
finished reference plus design-specific art and pet-transformation prompts.
Reusable customer-pet fixtures live in
[`examples/pet-inputs`](examples/pet-inputs).

## Install

```bash
cd TemplateGenerator
python3 -m venv .venv
.venv/bin/python -m pip install -e .
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
```

Tool 1 reads the API key from `--api-key-file` or `OPENAI_API_KEY`. The layout
and render tools work offline. The POC runner calls Tool 1 once and the renderer
once.
It can instead reuse `--transformed-pet` with an explicit `--layout`, which is
the no-generation path used to inspect a prepared print bundle.

Use `pawmarvel-pipeline` when starting from a new finished design. It generates
art with the design's art prompt, transforms a representative pet using the
design's pet prompt plus one or more ordered finished references, waits for the
local layout editor to be saved and closed, then renders a preview/debug pair and
can continue through print upscaling, print rendering, bundle publication, and
`run.json` provenance. It makes image API calls only; it does not derive prompts
through a text model. Pet names are always rendered with the bundled font. Supply
`--design-id` with `--bundle-output-dir` to enable the complete publication
path; this mode requires a product profile. Publication derives the unique
template identity as `<design-id>--<product-profile-id>` and updates the
top-level `catalog.json`, so one design can safely target several product
variants. The deprecated `--template-id` spelling remains an alias for
`--design-id`. The command otherwise requires
either `--product-profile` (recommended) or the legacy
`--art-resolution WIDTHxHEIGHT`. A profile derives the preview art, transformed
pet, and print dimensions rather than trusting screenshot pixels.

Repeat `--sample-design` to add supporting references. The first reference is
the primary design and the only one used by the layout editor; later references
are ordered supporting visual evidence for art and pet generation.

After a successful run, repeat the same command with `--rerun-step art`,
`--rerun-step pet`, or `--rerun-step layout` to replace only that authoring
stage. The option is repeatable, and the pipeline then refreshes preview,
provenance, and any requested print/bundle outputs. Layout-only reruns are
offline. Selective reruns require the existing `run.json` and cannot be combined
with broad `--force`.

After preview inspection, run `pawmarvel-upscale-template` once for the reusable
art/layout and `pawmarvel-upscale-pet` for each transformed pet. No
`layout.snapshot.json`, pipeline `run.json`, or approval artifact is required.
The final `pawmarvel-render` invocation validates both manifests against the
profile and writes a final-review manifest. A profile whose vendor requirements
are not confirmed produces a print candidate, not an automatic vendor-ready
certification.

Design-specific prompts are treated like source code. For pet transformation,
`pawmarvel-generate` sends the customer pet first for identity, then the finished
design for pose, expression, crop, and style. Working directories and
published bundles do not carry customer source data; each bundle does carry its
exact `art-template.md` and `pet-transform.md` contract files.

The layout editor compares the checked-in 40-face OFL catalog with lettering in
the reference. The catalog is the default when neither `--font` nor
`--font-catalog` is supplied. The matcher normalizes glyph silhouettes,
preselects the highest-scoring local font, and displays a drop-down containing
the five best matches and their visual-confidence scores. The operator's final
selection is saved in `layout.json` and only that TTF and its `OFL.txt` are
published. Supplying `--font` remains an explicit override; macOS system fonts
are not valid bundle inputs. `layout.json` includes `"model": "gpt-image-2"`
for prompts whose pose and expression directives must run verbatim; omitting
the field selects the consumer's Gemini route.

Legacy expanded font mode can add candidates from the pinned
`assets/fonts/expanded-catalog.json`. It downloads them into a checksum-addressed
cache, validates both the TTF and OFL 1.1 license, and ranks them with the local
catalog. It remains for compatibility with earlier experiments and is not part
of the current local-catalog MVP. Broader discovery is deferred.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests mock image-generation requests and do not consume API credits.
