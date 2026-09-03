# PawMarvel Template Toolset — MVP Design

## 1. Goal

Prove that a finished personalized-product screenshot can be converted into a
reusable low-resolution art template and layout, tested with different customer
pets, and mechanically prepared as a high-resolution print candidate.

The MVP optimizes for fast local iteration, understandable files, and easy
deployment. It does not optimize for scale, automated approval, or vendor
release.

## 2. Core decision: design-specific image prompts

Automated pet-prompt derivation was removed because its generated prompts did
not produce reliable enough results. Each design instead owns provider-specific
reviewed prompt sources beside its finished reference:

```text
examples/<design>/art-template-gpt.md
examples/<design>/art-template-gemini.md
examples/<design>/pet-transform-gpt.md
examples/<design>/pet-transform-gemini.md
```

They are treated like source code: reviewed, versioned, and regression-tested
with their corresponding finished design. The pipeline reads them directly. It
does not make a multimodal text-model call, derive prompts, or copy prompt
variants into mutable working directories. A bundle publication copies the
selected art and pet variants with their qualified filenames into the immutable
template contract.

Every pet-transformation image request has exactly this ordered input contract:

1. `USER PET` / `IMAGE A`: the first image and sole identity source.
2. `REFERENCE DESIGN` / `IMAGE B`: the authoritative finished design
   providing pet pose, expression, crop, composition, and rendering style.
3. The design's `pet-transform-{gpt|gemini}.md` prompt matching the selected
   provider.

The reference pet must never replace the user's pet identity. Additional style
references and selected transformed-pet exemplars are not part of this MVP
runtime contract.

## 3. Success criteria

The POC succeeds when an operator can:

1. select a product profile and one or more ordered finished design references;
2. generate reusable preview-size `art.png` using the design's art prompt;
3. generate a transparent transformed pet using one user pet, the same ordered
   references, and the design's pet prompt;
4. author `layout.json` for pet and font-rendered name placement;
5. render a repeatable low-resolution preview;
6. independently upscale the art and transformed pet and mechanically derive
   `layout-print.json`;
7. render an exact-size print candidate;
8. publish a clean two-resolution template bundle containing the ordered
   finished-design references and both corresponding prompts; and
9. register it in a catalog whose identity includes both the design and product
   profile.

## 4. Scope

### Included

- OpenAI or Gemini image generation/editing, with GPT Image 2 retained as the
  offline authoring default.
- One primary finished design reference and optional ordered supporting
  references.
- One reviewed art prompt and one reviewed pet prompt per design.
- Named product profiles with exact preview and print dimensions.
- Local browser layout authoring.
- OFL font packaging and deterministic text rendering.
- Deterministic or Bria-assisted layer upscale.
- Checksum-bound print preparation and final rendering.
- Clean preview/print bundle publication.
- A one-command pipeline and smaller manual commands.

### Deferred

- Per-template prompt generation or prompt analysis sidecars.
- Multiple transformed-pet runtime exemplars.
- Automated visual approval.
- Vendor-specific release automation.
- Independent high-resolution layout editing.
- Cross-resolution visual scoring.
- Production queues, databases, services, or customer accounts.
- Automatic pet-relative shadow reconstruction.
- AI-generated pet-name images; the MVP contains no implementation for this path.

## 5. Repository asset organization

Inputs are separated by role while each design contract remains self-contained:

```text
examples/
  README.md
  pet-inputs/
    sausage-dog-puppy.png
    white-fluffy-dog.png
  life-is-good/
    reference-design.png
    art-template-gpt.md
    art-template-gemini.md
    pet-transform-gpt.md
    pet-transform-gemini.md
  charlie-well-trained/
    reference-design.png
    art-template-gpt.md
    art-template-gemini.md
    pet-transform-gpt.md
    pet-transform-gemini.md
```

Each design folder contains its primary finished reference and four
provider-qualified prompt sources. Optional supporting finished-design references
may be stored in a `reference-designs/` subdirectory. Customer-pet fixtures are
reusable across designs; prompt files are not interchangeable between designs.

Generated `work/` directories remain mutable and ignored. They may contain
staged source copies and run provenance, but never become the frontend contract.

## 6. Product profile contract

A versioned product profile owns:

- `profile_id`;
- exact print width and height;
- preview art dimensions with the same exact aspect ratio;
- provider-compatible transformed-pet generation dimensions;
- optional DPI, physical dimensions, bleed, safe margin, background, color
  space, format, and file-size constraints;
- whether vendor requirements were confirmed; and
- the uniform preview-to-print scale.

The screenshot is never resized or cropped into a profile-aligned layout
reference. It remains visual evidence only. Product geometry begins with
generated `art.png`.

## 7. Tool 1 — `pawmarvel-generate`

This remains a flexible prompt-driven image wrapper. It supports OpenAI
(`gpt-image-2` by default) and Gemini (`gemini-3.1-flash-image` by default), plus
sample-only, pet-only, or combined inputs. GPT Image 2 remains recommended for
offline art authoring; Gemini can be selected for latency-sensitive pet
transformation tests. The two MVP operations are:

### Art generation

```bash
pawmarvel-generate \
  --sample-design examples/life-is-good/reference-design.png \
  --prompt-file examples/life-is-good/art-template-gpt.md \
  --product-profile work/life-is-good/product-profile.json \
  --profile-layer art \
  --output-dir work/life-is-good \
  --output-name art.png \
  --background transparent \
  --output-format png
```

To exercise the lower-latency Gemini route for the pet operation, add
`--provider gemini`. A concrete `--model gemini-*` also selects Gemini when
`--provider auto` is retained. The wrapper maps an exact requested canvas to
the nearest Gemini native aspect/resolution tier, then contains the result on
the exact canvas without cropping. Gemini has no native transparent-background
request parameter: transparency is reinforced in the prompt and must remain a
QA gate.

### Pet transformation

```bash
pawmarvel-generate \
  --pet-image examples/pet-inputs/sausage-dog-puppy.png \
  --sample-design examples/life-is-good/reference-design.png \
  --prompt-file examples/life-is-good/pet-transform-gpt.md \
  --product-profile work/life-is-good/product-profile.json \
  --profile-layer transformed-pet \
  --output-dir work/life-is-good/qa \
  --output-name transformed-pet.png \
  --background transparent \
  --output-format png
```

For the combined request, Tool 1 sends the user pet first, the primary finished
reference second, and supporting references afterward regardless of CLI flag
order. Its injected wrapper identifies the first as identity-only, the second
as primary treatment evidence, and all remaining images as supporting treatment
evidence. It prints resolved inputs
and API parameters without logging prompt contents or credentials, emits a
progress heartbeat, validates output dimensions/format/alpha, and refuses
overwrite without `--force`.

`--sample-design` is repeatable across the general generator and pipeline.
`--reference-design` is repeatable in the POC runner and bundle publisher. In
each case, CLI occurrence order is contract order.

## 8. Tool 2 — `pawmarvel-layout-config`

The local editor receives:

- generated preview `art.png`;
- the unchanged finished reference for visual comparison;
- one representative transformed pet;
- a sample pet name;
- the curated 40-face local OFL catalog or an explicit OFL font override;
  and
- output `layout.json`.

It lets an operator move and resize the pet/name boxes while previewing the
shared deterministic renderer. Saving writes `layout.json`, bundles the font and
`OFL.txt`, and creates a calibration preview. The command returns after the
saved browser window closes, allowing the pipeline to continue.

The local catalog contains 40 static TTF faces with sibling OFL licenses and a
hash-pinned manifest. It is used automatically when no explicit font or catalog
is supplied. The editor validates every candidate before opening, maps the
authored name box onto the reference, and compares normalized lettering
silhouettes. It preselects the highest-ranked font and exposes the five best
matches and their visual-confidence scores in a drop-down. The operator can
override the recommendation before saving. Matching is advisory and never
selects an arbitrary system font. Saving records the selected font in
`layout.json` and all five ranked options in
`qa/font-recommendation.json`. Print preparation and bundle publication
propagate only the selected font and its license.

Legacy expanded mode uses a checked-in, versioned OFL index whose artifact
URLs are pinned to an immutable upstream revision. A bounded candidate set is
downloaded into a checksum-addressed local cache before paid pipeline work.
TTF hashes, license hashes, OFL 1.1 text, and Pillow renderability are validated.
It remains for compatibility with earlier experiments and is outside the local
catalog MVP. Open-world discovery is deferred to the future roadmap. Cache
contents never become bundle inputs directly: saving copies only the confirmed
TTF and its license into the template.

The reference screenshot never supplies coordinates. Placement is authored
against `art.png`.

## 9. `layout.json` contract

The version-1 layout stores integer canvas coordinates:

```json
{
  "version": 1,
  "model": "gpt-image-2",
  "art": "art.png",
  "pet": {
    "box": {"x": 120, "y": 260, "width": 560, "height": 520},
    "fit": "contain",
    "anchor": "bottom-center",
    "rotation_degrees": 0
  },
  "name": {
    "box": {"x": 150, "y": 820, "width": 500, "height": 100},
    "font": "fonts/Anton-Regular.ttf",
    "font_size_px": 72,
    "min_font_size_px": 32,
    "color": "#111111",
    "horizontal_align": "center",
    "vertical_align": "middle"
  }
}
```

Coordinates are preview-canvas pixels. The renderer alpha-trims the pet,
contains it in the pet box, and anchors the visible result. Text shrinks to fit
using the bundled font. Print geometry is mechanically derived; it is not
independently edited in the MVP.

## 10. Tool 3 — shared renderer

`pawmarvel-render` and the layout editor use the same Pillow composition code:

1. load `art.png` and validate the layout canvas;
2. alpha-trim and place the transformed pet;
3. render the exact pet name with the bundled OFL font;
4. write final and optional debug images; and
5. in profile-aware print mode, validate the print manifest and exact output
   contract and write a final-review manifest.

Rendering an existing transformed pet is deterministic and makes no AI call.

## 11. POC runner

`pawmarvel-poc-run` is a thin generation-plus-render harness. For a new pet,
it requires all three runtime transformation inputs:

```bash
pawmarvel-poc-run \
  --template-dir work/life-is-good \
  --pet-image examples/pet-inputs/sausage-dog-puppy.png \
  --reference-design examples/life-is-good/reference-design.png \
  --prompt-file examples/life-is-good/pet-transform-gpt.md \
  --pet-name SAUSAGE \
  --output-dir work/life-is-good/qa
```

It validates every input and all output conflicts before the paid call. It
supports `--transformed-pet` as a mutually exclusive no-generation path for
rerendering preview or print assets; reference and prompt are not required when
no transformation occurs.

## 12. One-step pipeline

`pawmarvel-pipeline` coordinates one template experiment. Its required
generation inputs are:

- `--sample-design`: one primary finished design reference, repeatable for
  ordered supporting references;
- `--art-prompt`: design-specific art prompt;
- `--pet-prompt`: design-specific pet prompt;
- `--pet-image`: representative user pet;
- product profile or legacy explicit art resolution;
- font/license, pet name, and output directories.

It performs:

1. complete preflight validation;
2. staging of the finished references, profile, and test pet;
3. art generation from the ordered references plus the design's art prompt;
4. pet transformation from user pet plus the same ordered references plus
   design-specific pet prompt;
5. local layout authoring or reuse of an existing layout;
6. deterministic preview/debug rendering;
7. when bundle publication is requested, profile-driven layer upscaling and
   mechanical `layout-print.json` derivation;
8. profile-sized print-candidate rendering;
9. clean two-resolution bundle publication; and
10. `run.json` and `layout.snapshot.json` provenance.

There is no prompt-authoring stage and no text-model API call. `run.json`
records design prompt source paths and hashes without copying prompt files into
the mutable work tree. Supplying `--design-id` and `--bundle-output-dir`
together publishes exact prompt copies in the bundle, creates the catalog ID
`<design-id>--<product-profile-id>`, and enables
the full print/publication path and requires `--product-profile`; legacy
explicit art resolution remains preview-only. The complete profile-backed path
has nine visible stages.

The pipeline also supports repeatable `--rerun-step art`, `--rerun-step pet`,
and `--rerun-step layout` selections after a successful full run. Only selected
authoring stages execute; preview/provenance and any requested print/bundle
outputs are deterministic downstream rebuilds. Art and pet each make one image
API call, while layout-only is offline and reopens the local editor. Selecting
art or pet does not implicitly change layout; callers combine the relevant
flags when geometry must be reconfirmed.

For POC iteration, this makes the pipeline a stage-level debug driver for the
same operations exposed by the manual commands. It does not execute or mutate a
separate hand-built manual workspace; it invokes the shared implementation in
its own tracked pipeline workspace and uses `run.json` as the rerun guard. The
operations guide places each selective pipeline command beside its standalone
manual equivalent so results can be compared without path collisions.

Selective rerun is a scoped replacement operation and is mutually exclusive
with broad `--force`. It requires the existing `run.json`, staged reference,
art/pet artifacts needed by unselected stages, and a valid reusable layout.
Preflight compares source hashes and stable geometry/runtime settings before a
paid call. A changed art prompt is allowed only when art is selected; a changed
pet prompt or customer input is allowed only when pet is selected. Reference or
product-profile changes require a new full working run. The new `run.json`
records `run_mode: selective-rerun` and the exact `rerun_steps` list.

## 13. Print preparation

Print preparation is split by asset lifetime:

1. `pawmarvel-upscale-template` runs once per template. It upscales `art.png`,
   uniformly derives `layout-print.json`, copies font/license/profile inputs,
   and writes `template-print-manifest.json`.
2. `pawmarvel-upscale-pet` runs once per customer-approved transformed pet. It
   derives scale from the approved preview/print layout pair, upscales only the
   cutout, and writes `pet-print-manifest.json` with template geometry hashes.
3. `pawmarvel-upscale` remains a backward-compatible combined coordinator for
   older scripts; new bundle-consumer code must use the pet-only command.

The deterministic backend uses geometry-preserving Lanczos. The optional Bria
backend may add a supported detail pass before normalization to exact profile
dimensions. Neither backend removes the need for 100% visual inspection.

## 14. Clean bundle contract

`pawmarvel-bundle` publishes runtime/template assets and updates their catalog:

```text
bundles/catalog.json
bundles/<design-id>--<product-profile-id>/
  art.png
  layout.json
  print/art.png
  layout-print.json
  reference-design.png
  reference-designs/                 # optional; supporting references only
    reference-design-0002.png
    reference-design-0003.png
  art-template-{gpt|gemini}.md       # exactly one selected variant
  pet-transform-{gpt|gemini}.md      # exactly one; matches runtime route
  qa/transformed-pet.png
  fonts/<font>.ttf
  fonts/OFL.txt
```

`catalog.json` is schema-versioned and contains one entry per design/product
pair. Each entry includes the composite `template_id`, separate `design_id` and
`product_profile_id`, design metadata, the complete product-profile metadata,
preview/print dimensions, runtime model, selected prompt paths, and relative bundle path. This avoids
collisions when the same visual design is published for multiple products or
variants. Catalog publication replaces only the matching pair and preserves
other variants. Its ordered `reference_designs` array gives static consumers
the primary and supporting bundle paths without requiring directory listing.

The bundle contains a primary `reference-design.png`, zero or more ordered
supporting references under `reference-designs/`, and the corresponding
provider-qualified art and pet prompt files. Supporting filenames use consecutive,
zero-padded sequence numbers beginning at `0002`. The catalog's `prompts`
object identifies both exact filenames. It contains no prompt analysis,
customer source pet, product profile, run manifest, local paths, or API
credentials. The application reads the pet prompt from the selected bundle when
transforming a customer pet; the art prompt preserves reproducible authoring
provenance.

The publisher validates:

- true-alpha preview and print art;
- exact preview/print aspect ratio and uniform geometry scaling;
- one readable primary finished reference and a valid ordered supporting set;
- two nonempty UTF-8 prompt artifacts with valid provider-qualified filenames,
  with the pet category matching the runtime route;
- the representative transparent pet;
- OFL font/license pairing; and
- a strict allowlist of bundle files.

## 15. Working-directory layout

```text
work/life-is-good/
  source-reference-design.png
  source-reference-designs/          # optional; supporting references only
    reference-design-0002.png
  product-profile.json
  art.png
  layout.json
  fonts/
    <selected-font>.ttf
    OFL.txt
  qa/
    transformed-pet.png
    calibration-preview.png
    final-preview.png
    final-preview-debug.png
  runs/sausage-dog-puppy/
    input-pet.png
    transformed-pet.png
    preview.png
    preview-debug.png
    layout.snapshot.json
    run.json
  print/sausage-dog-puppy/
    art-print.png
    transformed-pet-print.png
    layout-print.json
    product-profile.json
    template-print-manifest.json
    pet-print-manifest.json
    final-print.png
    final-print-debug.png
    final-print.manifest.json
```

No generated or copied prompt belongs in this mutable work tree; reviewed
prompt copies belong only in the source example and published bundle.

## 16. Technical structure

```text
src/pawmarvel_generator/
  cli.py                 # OpenAI/Gemini image-generation wrapper
  pipeline_cli.py        # art + pet + layout + preview + print + bundle coordinator
  poc_runner.py          # one ordered-reference-guided personalization run
  layout_cli.py          # local layout command
  layout_server.py       # localhost editor
  renderer.py            # shared deterministic composition
  render_cli.py          # render command
  product_profile.py     # profile schema and derivation
  profile_cli.py         # profile command
  print_upscale.py       # split layer upscale and geometry contracts
  upscale_template_cli.py # reusable template print preparation
  upscale_pet_cli.py     # customer-specific pet print preparation
  upscale_cli.py         # legacy combined coordinator
  bundle.py              # clean bundle validation/publication
  bundle_cli.py          # bundle command
  config.py              # layout parsing/validation
```

Dependencies stay small: `openai`, Pillow, and the Python standard library.
There is no text-model prompt-authoring command or module.

## 17. Testing strategy

Unit and integration tests verify:

- Tool 1 pet-first/reference-second request ordering;
- the pipeline makes only two image-generation calls and no Responses call;
- the profile pipeline reaches print rendering and clean bundle publication;
- design prompt paths and hashes appear in provenance and exact copies appear
  in the published bundle;
- POC generation fails before payment without at least one finished reference or
  design-specific prompt;
- design folders contain their reference and four provider-qualified prompt sources;
- layout geometry, alpha trimming, font fitting, profile size derivation,
  preview/print scaling, manifests, and OFL licensing;
- bundle publication includes all ordered references plus both required prompts;
- the catalog distinguishes product variants that share a design ID; and
- local editor save/close behavior.

All API clients are mocked in the default suite.

## 18. MVP acceptance criteria

- Each design's prompts work acceptably with its checked-in reference.
- Art output contains only fixed template graphics.
- Pet output preserves user identity and follows reference pose/style/crop.
- Exactly one user pet and one or more ordered finished references are used for
  each pet call; the user pet is always the first API image.
- Layout and deterministic preview match the intended composition.
- Print layout is a uniform scale of preview layout.
- Final output matches the product profile's exact print dimensions.
- Bundle output passes its strict allowlist, contains both prompt contract files,
  and contains no customer source data.
