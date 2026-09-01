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
not produce reliable enough results. Each design instead owns two reviewed,
checked-in prompt sources beside its finished reference:

```text
examples/<design>/art-template.md
examples/<design>/pet-transform.md
```

They are treated like source code: reviewed, versioned, and regression-tested
with their corresponding finished design. The pipeline reads them directly. It
does not make a multimodal text-model call, derive prompts, or copy prompt
variants into mutable working directories. Bundle publication copies the exact
two reviewed sources into the immutable template contract.

Every pet-transformation image request has exactly this input contract:

1. `REFERENCE DESIGN` / `IMAGE A`: one finished design image providing pet
   pose, expression, crop, composition, and rendering style.
2. `USER PET` / `IMAGE B`: the second image and sole identity source.
3. The design's `pet-transform.md` text prompt.

The reference pet must never replace the user's pet identity. Additional style
references and selected transformed-pet exemplars are not part of this MVP
runtime contract.

## 3. Success criteria

The POC succeeds when an operator can:

1. select a product profile and finished design reference;
2. generate reusable preview-size `art.png` using the design's art prompt;
3. generate a transparent transformed pet using one user pet, the same finished
   reference, and the design's pet prompt;
4. author `layout.json` for pet and font-rendered name placement;
5. render a repeatable low-resolution preview;
6. independently upscale the art and transformed pet and mechanically derive
   `layout-print.json`;
7. render an exact-size print candidate; and
8. publish a clean two-resolution template bundle containing one finished
   design reference and both corresponding prompts.

## 4. Scope

### Included

- GPT Image 2 art and pet generation.
- One finished design reference per pet transformation.
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
- Multiple pet-style references or transformed-pet runtime exemplars.
- Automated visual approval.
- Vendor-specific release automation.
- Independent high-resolution layout editing.
- Cross-resolution visual scoring.
- Production queues, databases, services, or customer accounts.
- Automatic pet-relative shadow reconstruction.
- AI-generated pet-name images in the main flow.

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
    art-template.md
    pet-transform.md
  charlie-well-trained/
    reference-design.png
    art-template.md
    pet-transform.md
```

Each design folder contains its finished reference and exactly two corresponding
prompt sources. Customer-pet fixtures are reusable across designs; prompt files
are not interchangeable between designs.

Generated `work/` directories remain mutable and ignored. They may contain
staged source copies and run provenance, but never become the frontend contract.

## 6. Product profile contract

A versioned product profile owns:

- `profile_id`;
- exact print width and height;
- preview art dimensions with the same exact aspect ratio;
- GPT Image 2-compatible transformed-pet generation dimensions;
- optional DPI, physical dimensions, bleed, safe margin, background, color
  space, format, and file-size constraints;
- whether vendor requirements were confirmed; and
- the uniform preview-to-print scale.

The screenshot is never resized or cropped into a profile-aligned layout
reference. It remains visual evidence only. Product geometry begins with
generated `art.png`.

## 7. Tool 1 — `pawmarvel-generate`

This remains a flexible prompt-driven GPT Image 2 wrapper. It supports sample
only, pet only, or combined inputs. The two MVP operations are:

### Art generation

```bash
pawmarvel-generate \
  --sample-design examples/life-is-good/reference-design.png \
  --prompt-file examples/life-is-good/art-template.md \
  --product-profile work/life-is-good/product-profile.json \
  --profile-layer art \
  --output-dir work/life-is-good \
  --output-name art.png \
  --background transparent \
  --output-format png
```

### Pet transformation

```bash
pawmarvel-generate \
  --pet-image examples/pet-inputs/sausage-dog-puppy.png \
  --sample-design examples/life-is-good/reference-design.png \
  --prompt-file examples/life-is-good/pet-transform.md \
  --product-profile work/life-is-good/product-profile.json \
  --profile-layer transformed-pet \
  --output-dir work/life-is-good/qa \
  --output-name transformed-pet.png \
  --background transparent \
  --output-format png
```

For the combined request, Tool 1 sends the finished reference first and user pet
second regardless of CLI flag order, matching the design prompt's Image A/Image
B contract. Its injected wrapper identifies the first as treatment-only and the
second as identity-only. It prints resolved inputs
and API parameters without logging prompt contents or credentials, emits a
progress heartbeat, validates output dimensions/format/alpha, and refuses
overwrite without `--force`.

The general command may retain repeatable `--sample-design` for loose
experiments, but the pipeline and POC runner enforce exactly one finished
reference for customer-pet transformation.

## 8. Tool 2 — `pawmarvel-layout-config`

The local editor receives:

- generated preview `art.png`;
- the unchanged finished reference for visual comparison;
- one representative transformed pet;
- a sample pet name;
- an initial OFL font/license plus zero or more approved local font catalogs;
  and
- output `layout.json`.

It lets an operator move and resize the pet/name boxes while previewing the
shared deterministic renderer. Saving writes `layout.json`, bundles the font and
`OFL.txt`, and creates a calibration preview. The command returns after the
saved browser window closes, allowing the pipeline to continue.

Font catalogs are recursive directories of TTF families with sibling OFL
licenses. The editor validates every candidate before opening, maps the authored
name box onto the reference, compares normalized lettering silhouettes, and
preselects the highest-ranked local font. It exposes side-by-side specimens,
confidence, and alternatives and uses the shared Pillow renderer for the
authoritative full preview. The operator confirms the preselection; matching is
advisory and never selects an arbitrary system or downloaded font. `--font`
remains an explicit override. Saving records the selected font in `layout.json`
and recommendation diagnostics in `qa/font-recommendation.json`. Print preparation and
bundle publication propagate only that font and its license, so production
remains deterministic even though authoring can compare multiple candidates.

Optional expanded mode uses a checked-in, versioned OFL index whose artifact
URLs are pinned to an immutable upstream revision. A bounded candidate set is
downloaded into a checksum-addressed local cache before paid pipeline work.
TTF hashes, license hashes, OFL 1.1 text, and Pillow renderability are validated.
Cached candidates enter the same reference-image ranking path as local fonts;
the local catalog remains the offline fallback. Cache contents are never bundle
inputs directly: saving copies only the confirmed TTF and its license into the
template, after which print and production consumers remain network-independent.

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
  --prompt-file examples/life-is-good/pet-transform.md \
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

- `--sample-design`: one finished design reference;
- `--art-prompt`: design-specific art prompt;
- `--pet-prompt`: design-specific pet prompt;
- `--pet-image`: representative user pet;
- product profile or legacy explicit art resolution;
- font/license, pet name, and output directories.

It performs:

1. complete preflight validation;
2. staging of the finished reference, profile, and test pet;
3. art generation from finished reference plus the design's art prompt;
4. pet transformation from user pet plus the same finished reference plus
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
the mutable work tree. Supplying `--template-id` and `--bundle-output-dir`
together publishes exact prompt copies in the bundle and enables
the full print/publication path and requires `--product-profile`; legacy
explicit art resolution remains preview-only. Font mode has nine visible
stages for the complete path; experimental AI-name mode has ten.

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

`pawmarvel-upscale` accepts the preview template, transformed pet, layout, and
product profile. It:

1. independently upscales the complete art and pet canvases;
2. preserves alpha and visible bounds;
3. uniformly scales rectangle edges and font sizes into `layout-print.json`;
4. copies font/license/profile inputs; and
5. writes a checksum manifest.

The deterministic backend uses geometry-preserving Lanczos. The optional Bria
backend may add a supported detail pass before normalization to exact profile
dimensions. Neither backend removes the need for 100% visual inspection.

## 14. Clean bundle contract

`pawmarvel-bundle` publishes only runtime/template assets:

```text
bundles/<template-id>/
  art.png
  layout.json
  print/art.png
  layout-print.json
  reference-design.png
  art-template.md
  pet-transform.md
  qa/transformed-pet.png
  fonts/<font>.ttf
  fonts/OFL.txt
```

The bundle contains exactly one finished reference and the corresponding
`art-template.md` and `pet-transform.md`. It contains no prompt analysis,
customer source pet, product profile, run manifest, local paths, or API
credentials. The application reads the pet prompt from the selected bundle when
transforming a customer pet; the art prompt preserves reproducible authoring
provenance.

The publisher validates:

- true-alpha preview and print art;
- exact preview/print aspect ratio and uniform geometry scaling;
- one readable finished reference;
- two nonempty UTF-8 prompt artifacts with exact contract filenames;
- the representative transparent pet;
- OFL font/license pairing; and
- a strict allowlist of bundle files.

## 15. Working-directory layout

```text
work/life-is-good/
  source-reference-design.png
  product-profile.json
  art.png
  layout.json
  fonts/
    Anton-Regular.ttf
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
    print-manifest.json
    final-print.png
    final-print-debug.png
    final-print.manifest.json
```

No generated or copied prompt belongs in this mutable work tree; reviewed
prompt copies belong only in the source example and published bundle.

## 16. Technical structure

```text
src/pawmarvel_generator/
  cli.py                 # GPT Image 2 wrapper
  pipeline_cli.py        # art + pet + layout + preview + print + bundle coordinator
  poc_runner.py          # one reference-guided personalization run
  layout_cli.py          # local layout command
  layout_server.py       # localhost editor
  renderer.py            # shared deterministic composition
  render_cli.py          # render command
  product_profile.py     # profile schema and derivation
  profile_cli.py         # profile command
  print_upscale.py       # layer upscale and geometry scaling
  upscale_cli.py         # print-preparation command
  bundle.py              # clean bundle validation/publication
  bundle_cli.py          # bundle command
  name_prompt_cli.py     # deferred AI-name experiment
  config.py              # layout parsing/validation
```

Dependencies stay small: `openai`, Pillow, and the Python standard library.
There is no text-model prompt-authoring command or module.

## 17. Testing strategy

Unit and integration tests verify:

- Tool 1 reference-first/pet-second request ordering;
- the pipeline makes only two image-generation calls and no Responses call;
- the profile pipeline reaches print rendering and clean bundle publication;
- design prompt paths and hashes appear in provenance and exact copies appear
  in the published bundle;
- POC generation fails before payment without exactly one finished reference or
  design-specific prompt;
- design folders contain their reference and two corresponding prompt sources;
- layout geometry, alpha trimming, font fitting, profile size derivation,
  preview/print scaling, manifests, and OFL licensing;
- bundle publication includes one reference plus both required prompts; and
- local editor save/close behavior.

All API clients are mocked in the default suite.

## 18. MVP acceptance criteria

- Each design's prompts work acceptably with its checked-in reference.
- Art output contains only fixed template graphics.
- Pet output preserves user identity and follows reference pose/style/crop.
- Exactly one user pet and one finished reference are used for each pet call.
- Layout and deterministic preview match the intended composition.
- Print layout is a uniform scale of preview layout.
- Final output matches the product profile's exact print dimensions.
- Bundle output passes its strict allowlist, contains both prompt contract files,
  and contains no customer source data.

## 19. Future extension: AI pet-name images

The implemented name-image experiment may continue to reuse the `name.box` in
`layout.json`, but it remains outside the main MVP. If adopted later, its prompt
and constraints should follow the same reviewed, design-owned source principle
rather than creating untracked prompt drift.
