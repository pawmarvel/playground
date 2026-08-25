# Offline Personalized Product Toolset — Low-Resolution MVP Design

Status: Implemented MVP  
Target: Proof of concept  
Primary implementation language: Python 3.10+  
Last updated: 2026-08-24

## 1. Goal

Build a small offline toolset that converts one personalized-product design
example into reusable low-resolution template artifacts:

```text
art.png
layout.json
prompt files
font file
optional generated-name PNG
```

The artifacts are validated by running one simple personalization flow:

1. Transform a user pet image into the required art style.
2. Place the transformed pet and either a font-rendered or generated-image pet
   name on `art.png` using `layout.json`.
3. Render a low-resolution final preview.
4. Let an operator visually accept the result or adjust the artifacts and run it
   again.

This production-like run is an MVP test harness. It is not an online production
service and does not need production approval, print, vendor, or scaling
infrastructure.

## 2. MVP success definition

The proof of concept succeeds when an operator can:

- Start from a cropped design reference.
- Generate a reusable background-only `art.png`.
- Create `layout.json` without manually calculating coordinates.
- Transform a representative pet image.
- Render a personalized preview using `art.png`, `layout.json`, the transformed
  pet, a pet name, and either a bundled font or an optional generated-name PNG.
- Adjust the art, prompt, or layout and rerun the workflow quickly.
- Confirm visually that the result follows the reference design closely enough
  to justify a future production iteration.

The MVP optimizes for learning speed and ease of local deployment, not scale or
print readiness.

## 3. Design principles

1. Keep Tool 1 prompt-driven so template experiments are not constrained by
   predefined task types.
2. Use AI only for background-art generation, pet transformation, and the
   optional generation of design-specific name lettering.
3. Use deterministic code for pet placement, name placement, font fallback,
   and final composition.
4. Use one low-resolution canvas and one layout file.
5. Let a person tune the layout visually instead of building automatic layout
   detection.
6. Keep draft artifacts mutable so iteration is fast.
7. Use the same renderer for layout editing, local QA, and the production-like
   MVP test.
8. Prefer Python, Pillow, JSON, and a small local HTML/Canvas editor over heavier
   services or frameworks.
9. Provide one thin installed test runner that automates the live POC sequence
   without duplicating generation or rendering logic.

## 4. Scope

### In scope

- One low-resolution `art.png` canvas.
- One pet image slot.
- One single-line pet-name slot.
- Transparent PNG transformed-pet input.
- Pixel-based pet and text bounding boxes.
- Pet fit, anchor, and optional rotation.
- Font file, font size, color, alignment, and shrink-to-fit.
- Optional transparent generated-name PNG, alpha trimming, contain fit, and the
  same name-box alignment used by font rendering.
- A local visual layout editor.
- A deterministic low-resolution preview renderer.
- A debug overlay showing the resolved pet and text boxes.
- An installed live POC runner using a representative user pet image.
- A one-step orchestration command and a small per-run tracking manifest.
- Mutable working directories and easy reruns.

### Not in scope

- High-resolution art or pet assets.
- Preview-to-print coordinate scaling.
- Multiple resolution profiles.
- Cross-resolution QA.
- Print dimensions, DPI, bleed, color-management, or vendor requirements.
- Customer-editable layout overrides.
- Customer, operations, or vendor approval state machines.
- Release-grade immutable revisions, signing, or deployment manifests.
- Background workers, APIs, object storage, databases, or review portals.
- Authentication, authorization, deployment automation, or scalability work.
- Automatic coordinate extraction, OCR, perspective correction, or font
  identification.
- Multiple pets, multiple text fields, curved text, or foreground occlusion.

Deferred work is recorded in
[`FUTURE_PERSONALIZATION_ITERATIONS.md`](FUTURE_PERSONALIZATION_ITERATIONS.md).

## 5. System overview

```text
PREPRODUCTION TEMPLATE AUTHORING

 Cropped design reference + art prompt
                    |
                    v
          [1. TemplateGenerator]
                    |
                    v
                 art.png

 design reference + optional approved art.png/baseline prompt
                    |
                    v
        [1A. Pet Prompt Generator + Critic]
                    |
                    +-- pet-transform.md
                    +-- pet-transform.analysis.json

 Representative user pet + pet-transform.md
                    |
                    v
          [1. TemplateGenerator]
                    |
                    v
          transformed-pet.png

 art.png + design reference + transformed pet + sample name + font
                    |
                    v
          [2. Layout Configurator]
                    |
                    v
                layout.json

 design reference + art.png + layout.json
                    |
                    v
       [2A. Name Prompt Configurator]
                    |
                    +-- name-generation.json
                    +-- name-style-reference.png
                    +-- per-name prompt + API settings
                    |
                    v
          [1. TemplateGenerator]
                    |
                    v
          generated-name.png (optional)

 Reopen [2. Layout Configurator] with generated-name.png when image-mode
 placement needs visual adjustment; the same layout.json remains authoritative.


MVP PERSONALIZATION TEST

 User pet + pet name + template directory + optional generated-name.png
                    |
                    v
          [POC Automation Runner]
                    |
                    +-- calls [1. TemplateGenerator]
                    |              |
                    |              v
                    |     transformed-pet.png
                    |
                    +-- calls [3. Renderer]
                                   |
                                   v
                           final-preview.png
                                   |
                                   v
                         Visual accept or iterate


ONE-STEP TEST ORCHESTRATION

 Sample + prompts + pet + name method + font
                    |
                    v
        [Pipeline Orchestrator]
                    |
                    +-- invokes Tools 1, 1A, 2, 2A, and 3
                    +-- waits for browser layout confirmation
                    +-- template artifact directory
                    +-- per-run preview/debug/layout snapshot/run.json
```

## 6. Tool 1 — TemplateGenerator

Proposed command: `pawmarvel-generate` (existing)

### Responsibility

Use `gpt-image-2` for three prompt-defined image operations:

- Generate background-only `art.png` from a design reference and prompt.
- Transform a pet image into the desired style as an isolated transparent
  asset.
- Optionally generate only the exact pet-name lettering as a transparent PNG
  using the design reference for typography and texture.

The command remains prompt-driven. Do not add a required `--task` enum. The
prompt file decides whether the requested operation is background extraction,
art recreation, pet transformation, or another POC experiment.

### Input contract

`--sample-design` and `--pet-image` are independently optional, but at least one
must be provided:

| Supplied input | Typical use |
| --- | --- |
| Sample design only | Generate or recreate background-only `art.png` |
| Pet image only | Transform the pet into the template style |
| Both images | Reference-guided transformation or another prompt-defined experiment |

When both images are present, API input order is stable:

1. Sample design
2. User pet

The injected prompt wrapper labels these roles neutrally. It must not force a
specific edit such as replacing only the pet.

### Example: generate background art

```bash
pawmarvel-generate \
  --sample-design reference/design-example.png \
  --prompt-file prompts/art-template.md \
  --api-key-file OPENAI_API_KEY.rtf \
  --output-dir work/life-is-good \
  --output-name art.png \
  --size 1024x1120 \
  --background transparent \
  --output-format png
```

### Example: transform a representative pet

```bash
pawmarvel-generate \
  --pet-image test-inputs/hound.png \
  --prompt-file work/life-is-good/pet-transform.md \
  --api-key-file OPENAI_API_KEY.rtf \
  --output-dir work/life-is-good/qa \
  --output-name transformed-pet.png \
  --size 1024x1024 \
  --background transparent \
  --output-format png
```

### Example: generate design-specific name lettering

The prompt, cropped style reference, and size in this example are outputs from
Tool 2A rather than hand-authored guesses:

```bash
pawmarvel-generate \
  --sample-design work/life-is-good/name-style-reference.png \
  --prompt-file work/life-is-good/qa/name-BUDDY.md \
  --api-key-file OPENAI_API_KEY.rtf \
  --output-dir work/life-is-good/qa \
  --output-name generated-name.png \
  --size 1536x512 \
  --quality high \
  --background transparent \
  --output-format png
```

For the MVP, `1536x512` is the standard name-generation canvas. The prompt
controls natural letter height and tracking; it must not stretch short names to
fill the width. Very long names may use `2048x688` after visual review. The
renderer does not depend on the AI canvas dimensions because it trims visible
alpha bounds and fits the result into `name.box`.

### Implemented refinements

- Make `--pet-image` optional and validate that at least one image is supplied.
- Replace the behavioral prompt wrapper with neutral input-role labels.
- Remove the machine-specific default output directory. Default to `./output`
  or require `--output-dir`.
- Keep `--api-key-file`; optionally also accept `OPENAI_API_KEY` for portable
  local and CI use.
- Keep existing dry-run, progress, input-summary, output-format, and overwrite
  behavior.

### Output requirements

For `art.png`:

- Exact configured canvas dimensions.
- No example pet or example name.
- No shirt, room scene, border, or product mockup unless it is intentionally
  part of the reusable artwork.

For a transformed pet:

- PNG with an alpha channel.
- Complete, unclipped visible subject.
- No name text, product mockup, or unwanted background.
- Recognizable pet identity and markings unless the prompt requests otherwise.

For a generated name:

- PNG with an alpha channel and some transparent background.
- Exactly one correctly spelled, single-line pet name.
- No pet, fixed artwork, product mockup, or rectangular background.
- Complete, unclipped lettering, including distress, outlines, and shadows.
- Natural typography proportions; short names must not be stretched to fill
  the canvas.

Prompt files are stored beside the working artifacts so an operator can edit
and rerun them quickly.

## 7. Tool 1A — Pet Prompt Generator

Proposed command: `pawmarvel-pet-prompt` (implemented)

### Responsibility

Derive a reusable, self-contained pet-transformation prompt from the complete
sample design. The resulting prompt assumes exactly one later input: the
customer's pet identity photo. It must not require the sample, art, baseline,
or layout during the GPT Image 2 transformation call.

This is a one-time, API-backed template-authoring tool, not an offline image
processor. By default it uses a vision-capable text model through the Responses
API to author the final prompt directly, then makes a second visual critic call
to remove contradictions, verbosity, palette leakage, wrong crop, and weak
transparency requirements. This intentionally mirrors the successful
human-plus-ChatGPT prompt-authoring workflow more closely than mechanically
expanding an exhaustive JSON analysis.

The generated Markdown and provenance JSON become stored offline template
artifacts. Runtime pet transformation remains the existing pet-only Tool 1
call.

### Inputs and why layout is not required

- The complete sample establishes the example pet's pose, expression, crop,
  silhouette, and rendering style.
- Approved background-only `art.png` is optional in direct mode. When supplied,
  it helps distinguish the replaceable pet from fixed text, rainbow, paws,
  borders, and decoration. It is contextual evidence, not pixel subtraction;
  independently regenerated art must not be treated as an exact inverse.
- An optional approved baseline prompt gives the model a known-good concise
  structure. It is an authoring example, not a runtime input, and current
  template-specific style must still come from the sample.
- `layout.json` is not needed to describe the isolated pet. Placement size and
  coordinates remain Tool 2/Tool 3 responsibilities after alpha trimming. This
  keeps prompt derivation possible before layout authoring.

When `--art` is supplied, both images must be flat design-area crops with aspect
ratios within 2%. The art must be approved as background-only; otherwise it can
confuse fixed-versus-replaceable classification. Direct mode can run with only
`--sample-design` when the pet is visually unambiguous.

### Interface

```bash
pawmarvel-pet-prompt \
  --sample-design reference/design-example.png \
  --art work/life-is-good/art.png \
  --api-key-file OPENAI_API_KEY.rtf \
  --output work/life-is-good/pet-transform.md \
  --analysis-output work/life-is-good/pet-transform.analysis.json \
  --model gpt-5.6 \
  --strategy direct \
  --reasoning-effort high \
  --image-detail original
```

The command accepts `OPENAI_API_KEY` when `--api-key-file` is absent. It prints
resolved inputs and API settings without printing the key, image data, authoring
instructions, or generated prompt. It reports progress during a long response,
supports `--dry-run`, and refuses to overwrite either output without `--force`.

Direct mode is the default, and its critic pass is enabled by default. Use
`--no-critic-pass` for a cheaper one-call draft. Use `--baseline-prompt FILE`
to supply an approved prompt example. The legacy deterministic workflow remains
available as `--strategy structured`; it requires `--art`, disables the critic
by default, and does not accept a baseline prompt.

### Direct prompt authoring and validation

1. Decode the sample and optional art as PNG, JPEG, or WebP. Validate matching
   aspect ratios when art is present, and validate an optional baseline as
   nonempty UTF-8 text.
2. Send labeled image inputs at `original` detail to the Responses API with
   `store=false` and explicit reasoning effort.
3. Ask the multimodal model to return only a concise 180–450 word final runtime
   prompt, not analysis or JSON. It must resolve conflicts and assume exactly
   one later pet-photo input.
4. Locally reject empty, unusually short or long prompts, missing pet identity
   or transparency requirements, and references to unavailable authoring
   images.
5. Unless disabled, send the sample, optional art/baseline, and draft through a
   second critic call. The critic returns only the complete revised prompt.
6. Atomically write `pet-transform.md` and the
   `pet-transform.analysis.json` provenance sidecar.

The provenance sidecar has schema version 2. It records strategy, model,
reasoning effort, image detail, critic usage, all response IDs, source paths,
source hashes and dimensions, baseline hash, prompt hashes and word counts, and
whether the critic changed the draft. It never includes an API key, base64 image
data, or hidden authoring instructions. Structured fallback mode additionally
stores its validated analysis fields.

The generated runtime prompt explicitly separates responsibilities:

- Preserve the uploaded customer's breed/species, facial geometry, muzzle,
  eyes, ears, fur, markings, and distinctive asymmetry.
- Reconstruct the example's target posture, expression, viewpoint, crop,
  composition, and silhouette.
- Apply the extracted medium, palette, linework, shading, texture, and detail
  treatment.
- Output one isolated pet with a real transparent alpha background and no
  fixed design elements, text, mockup, or background plane.

### Structured fallback

`--strategy structured` retains the original diagnostic path: the model returns
strict JSON fields for style, pose, identity priorities, and exclusions; local
code expands them into a fixed prompt. This mode is useful for comparing or
debugging extracted attributes, but it is not the default because exhaustive
field expansion can introduce repetition and competing instructions.

### Reliability boundary and acceptance

The prompt can make requirements explicit but cannot guarantee exact identity,
pose, expression, or style from a probabilistic image model. The direct path
reduces avoidable text-generation loss but does not remove the fundamental
image-to-text-to-image bottleneck. A source photo that hides ears, markings,
muzzle shape, or other required anatomy cannot support strict preservation of
those traits. The MVP therefore requires a clear representative pet photo plus
visual review of the generated prompt and test image. Multi-image runtime style
references, automated similarity scoring, and retry policy remain future work.

## 8. Tool 2 — Layout Configurator

Proposed command: `pawmarvel-layout-config`

### Responsibility

Provide a small local visual editor for positioning one representative
transformed pet and one sample pet name over `art.png`. The name preview uses
the configured font by default or an optional generated-name PNG. Save the
selected values as `layout.json`.

The editor is assisted, not automatic. Its purpose is to replace manual pixel
calculation with fast drag-and-resize iteration.

### Interface

```bash
pawmarvel-layout-config \
  --art work/life-is-good/art.png \
  --reference reference/design-example-cropped.png \
  --pet work/life-is-good/qa/transformed-pet.png \
  --pet-name "BUDDY" \
  --name-image work/life-is-good/qa/generated-name.png \
  --font assets/fonts/ExampleFont.ttf \
  --output work/life-is-good/layout.json
```

The command starts a server bound only to `127.0.0.1` and opens a single local
HTML/Canvas page. Use Python standard-library HTTP primitives and plain
JavaScript. No frontend framework is needed.

### Browser-to-renderer protocol

The browser handles interactive box dragging, but Pillow is the authoritative
renderer. Browser Canvas text and image metrics must not be used to create the
saved calibration result.

The local server exposes only four MVP route patterns:

- `GET /` returns the packaged editor HTML.
- `GET /assets/<name>` returns only the allowlisted packaged `layout.js` and
  `layout.css` resources.
- `POST /preview` accepts the current draft layout values as JSON, validates
  them in Python, renders through the shared Pillow renderer, and returns a PNG.
- `POST /save` accepts the same draft values, validates them, copies the selected
  font into the template `fonts/` directory, atomically writes `layout.json`,
  renders `calibration-preview.png` through Pillow, and returns the saved paths.

The art, reference, representative pet, sample name, optional name image, font
source, and output paths are fixed from the CLI arguments for the lifetime of
the editor session. The browser does not submit arbitrary filesystem paths.

For responsive dragging, the browser may draw approximate box overlays locally
and debounce `/preview` calls. The latest PNG returned by `/preview` is the
authoritative preview. Validation failures return a short JSON error and do not
modify files.

### Editor behavior

- Display `art.png` as the canonical canvas.
- Display the cropped reference beside it or as an adjustable overlay.
- Render the actual representative transformed pet, not a generic placeholder.
- Drag and resize the pet box.
- Drag and resize the name box.
- Adjust pet rotation when the reference requires it.
- Select font size, text color, and alignment.
- Render the sample pet name live using the configured font when
  `--name-image` is absent.
- When `--name-image` is present, render that alpha-trimmed PNG in `name.box`,
  retain alignment controls, and mark font size and color controls inactive.
- Show numeric coordinates beside visual controls.
- Copy the selected font into the template's `fonts/` directory and store a
  relative font path in `layout.json`.
- Save `layout.json` and `calibration-preview.png`.
- Permit draft overwrites after explicit `--force` or an in-editor confirmation.

### Reference precondition

For the MVP, the reference must be a front-facing crop of the flat design area
with the same aspect ratio as `art.png`. Perspective correction is a manual
preprocessing step outside this tool.

### Why the real transformed pet is required

Placement depends on the pet asset's visible alpha bounds and aspect ratio. The
editor must use a representative transformed pet so alpha trimming, fitting,
and bottom alignment match the rendered test result.

The same principle applies to generated lettering: use the actual generated
name PNG when tuning image-mode placement because its visible alpha bounds and
aspect ratio can differ from the fallback font preview.

## 9. `layout.json` contract

Coordinates use the low-resolution `art.png` canvas. Its decoded dimensions
define the canvas; they are not duplicated in the layout. The origin is the
top-left; `x` increases right and `y` increases down. All MVP coordinates are
integer pixels.

```json
{
  "schema_version": 1,
  "art": "art.png",
  "pet": {
    "box": { "x": 212, "y": 270, "width": 600, "height": 720 },
    "rotation_degrees": 0
  },
  "name": {
    "box": { "x": 150, "y": 1040, "width": 724, "height": 130 },
    "font": "fonts/ExampleFont.ttf",
    "font_size_px": 92,
    "min_font_size_px": 42,
    "color": "#F7E7C6FF",
    "horizontal_align": "center",
    "vertical_align": "middle"
  }
}
```

### Validation rules

- `art` must be a relative path inside the template directory and must decode as
  an image with positive dimensions. Those dimensions define the canvas.
- Pet and name boxes must have positive sizes and intersect the art canvas.
- `rotation_degrees` must be a finite number.
- The font path must be relative to the template directory and must exist.
- Colors use eight-digit RGBA hex.
- Horizontal and vertical alignment must use supported values.
- `min_font_size_px` must be positive and no greater than `font_size_px`.
- Unsupported or invalid values produce clear input errors.

The following policies are fixed in code for the MVP and are deliberately not
stored in every layout:

- Alpha trimming is enabled with threshold `8`.
- Pet fit is `contain`.
- Pet anchor is `bottom_center`.
- Font-rendered name overflow uses shrink-to-fit.
- A supplied name PNG must contain an alpha channel and some transparent
  background. Its visible alpha bounds are trimmed, contain-fitted without
  distortion, and positioned using the existing name alignments.
- Layer order is art, pet, then name.
- Output format is PNG.

### Name rendering modes and schema compatibility

`layout.json` remains schema version 1. `name.box`, `horizontal_align`, and
`vertical_align` are the canonical placement contract for both modes:

- Font mode is selected when no `--name-image` input is supplied. All existing
  font, size, color, and shrink-to-fit behavior remains unchanged.
- Image mode is selected when `--name-image` is supplied. Font, size, and color
  fields remain valid fallback configuration but are not used for that render.

The mode belongs to the preview invocation rather than the reusable geometry,
so a single layout can test both approaches without migration or duplicate
layout files.

### Rendering semantics

1. Resolve the configured art path, load it as RGBA, and infer the canvas size.
2. Crop the transformed pet to alpha bounds using the fixed threshold.
3. Resize the visible pet with Lanczos so it fits inside the configured box.
4. Anchor it at the bottom center of the box.
5. Apply optional rotation on a transparent canvas.
6. Composite the pet over the art.
7. If no name image is supplied, render the name using Pillow/FreeType glyph
   bounds and shrink one pixel at a time until it fits or reaches the configured
   minimum.
8. If a name image is supplied, require transparency, crop it to visible alpha
   bounds, contain-fit it in `name.box`, and apply the configured alignment.
9. Fail clearly rather than clipping text or accepting an opaque or empty name
   asset.
10. Save the final composition as PNG using the fixed layer order.

The renderer and editor must call the same placement and text-layout functions.

## 10. Tool 2A — Name Prompt Configurator

Proposed command: `pawmarvel-name-prompt` (implemented)

### Responsibility and sequencing decision

Generate a reusable, design-specific name prompt configuration and then create
a validated concrete prompt for each pet name. This tool is deterministic and
offline; it does not call GPT Image or require an API key.

For the MVP, an approved `layout.json` is required by `configure`. A run without
layout is intentionally not supported: the sample design can suggest lettering
style, but it cannot establish the production name box, font baseline, minimum
legible scale, or final fit acceptance. The fast font preview in Tool 2 therefore
comes first. Its saved `layout.json` becomes the measurable contract for this
tool. A future image-analysis draft mode could propose layout values, but it
would still require Tool 2 approval and is outside this MVP.

### Configure interface

```bash
pawmarvel-name-prompt configure \
  --sample-design reference/design-example.png \
  --art work/life-is-good/art.png \
  --layout work/life-is-good/layout.json \
  --output-dir work/life-is-good
```

The command requires `--output-dir` to be the layout directory and requires
`--art` to match the relative art path already stored by `layout.json`. The
sample design and art aspect ratios may differ by no more than 2% so coordinate
mapping remains predictable.

It maps `name.box` from the art canvas into the sample design, saves the exact
lettering crop as `name-style-reference.png`, and saves
`qa/name-slot-debug.png` with the exact and crop rectangles. Optional
`--crop-padding-ratio` may expand the crop when the layout box is unusually
tight; the default is zero to avoid including nearby text or artwork. It also
creates:

- `name-prompt-template.md`, containing one `{{PET_NAME}}` placeholder and the
  single-line, exact-spelling, transparent-output constraints.
- `name-generation.json`, containing normalization, fit rules, GPT Image
  settings, artifact paths, and a snapshot of the layout fields on which the
  rules depend.

All outputs are preflighted and written atomically. Existing outputs require
`--force`.

### Per-name interface

```bash
pawmarvel-name-prompt create \
  --config work/life-is-good/name-generation.json \
  --pet-name "BUDDY" \
  --output work/life-is-good/qa/name-BUDDY.md
```

The command creates the concrete Markdown prompt and, by default,
`name-BUDDY.request.json`. The sidecar records the normalized name, cropped
style reference, measured fit data, and exact API parameters to pass to
`pawmarvel-generate`. It contains no API key.

### `name-generation.json` contract

The separate configuration avoids adding AI-generation policy to the renderer's
stable geometry schema. Its MVP fields are:

```json
{
  "schema_version": 1,
  "source_sample_design": "/absolute/audit/path/design-example.png",
  "layout": "layout.json",
  "art": "art.png",
  "style_reference": "name-style-reference.png",
  "prompt_template": "name-prompt-template.md",
  "layout_snapshot": {
    "canvas": { "width": 1024, "height": 1120 },
    "name_box": { "x": 150, "y": 900, "width": 724, "height": 130 },
    "font": "fonts/ExampleFont.ttf",
    "font_size_px": 92,
    "min_font_size_px": 42,
    "horizontal_align": "center",
    "vertical_align": "middle"
  },
  "normalization": {
    "case": "upper",
    "trim_whitespace": true,
    "single_line": true,
    "allowed_pattern": "^[A-Z]+(?:[ '\\-][A-Z]+)*$"
  },
  "constraints": {
    "min_characters": 2,
    "max_characters_advisory": 15,
    "min_natural_width_ratio": 0.2,
    "min_font_scale_ratio": 0.6
  },
  "generation": {
    "model": "gpt-image-2",
    "standard_size": "1536x512",
    "long_name_size": "2048x688",
    "long_name_scale_threshold": 0.8,
    "quality": "high",
    "background": "transparent",
    "output_format": "png"
  }
}
```

### Name validation semantics

1. Trim outer whitespace and normalize to uppercase.
2. Require one line of ASCII letters with only single spaces, apostrophes, or
   hyphens between letter groups.
3. Count letters and reject fewer than `min_characters`.
4. Measure the name with the exact bundled font at `font_size_px` using Pillow.
5. Shrink one pixel at a time, using the renderer's geometry model, until both
   dimensions fit `name.box` or `min_font_size_px` is exhausted.
6. Reject a visually too-short name when its preferred-size width divided by
   name-box width is below `min_natural_width_ratio`.
7. Reject a too-long name when its selected font size divided by preferred font
   size is below `min_font_scale_ratio`.
8. Treat `max_characters_advisory` as a warning and long-canvas selector, not a
   hard rule. Actual glyph width is authoritative because names of equal length
   can have very different widths.
9. Select the long canvas when the accepted font scale is below
   `long_name_scale_threshold` or the advisory letter count is exceeded.
10. Reject a stale configuration if any snapshotted layout geometry, font,
    size, or alignment changed; the operator must rerun `configure`.

The measurements predict layout suitability; they cannot guarantee that a
generative model spells or styles text correctly. Those remain explicit visual
checks before using the PNG.

## 11. Tool 3 — Low-Resolution Renderer

Proposed command: `pawmarvel-render`

### Responsibility

Combine `art.png`, `layout.json`, a transformed pet, and either a font-rendered
pet name or generated-name PNG into one deterministic low-resolution preview.
This is both the template QA renderer and the production-like POC test.

### Interface

```bash
pawmarvel-render \
  --template-dir work/life-is-good \
  --pet work/life-is-good/qa/transformed-pet.png \
  --pet-name "BUDDY" \
  --name-image work/life-is-good/qa/generated-name.png \
  --output work/life-is-good/qa/final-preview.png \
  --debug-output work/life-is-good/qa/final-preview-debug.png \
  --force
```

### Render sequence

1. Load and validate `layout.json` and `art.png`.
2. Load the transformed pet as RGBA.
3. Trim, resize, anchor, and rotate the pet using the layout contract.
4. Render the supplied name PNG, or use the bundled-font behavior when the
   optional input is absent.
5. Save `final-preview.png` atomically.
6. Optionally save a debug image showing pet and name boxes.
7. Print resolved inputs, dimensions, and output paths.

No approval state is stored. An operator visually accepts the preview or edits
the prompt, art, or layout and reruns the commands.

### Shared renderer function

```python
render_preview(
    template_dir: Path,
    pet_image: Path | BinaryIO,
    pet_name: str,
    *,
    name_image: Path | BinaryIO | None = None,
) -> bytes
```

The Layout Configurator and CLI use this same function or its lower-level pet
and text placement helpers.

## 12. POC Automation Runner

Proposed command: `pawmarvel-poc-run`

This is a thin installed test harness, not a fourth product-processing tool. It
calls the existing Tool 1 and Tool 3 Python functions in sequence so automation
does not duplicate prompt construction, API handling, or composition logic.

```bash
pawmarvel-poc-run \
  --template-dir work/life-is-good \
  --pet-image test-inputs/hound.png \
  --pet-name "BUDDY" \
  --name-image work/life-is-good/qa/generated-name.png \
  --api-key-file OPENAI_API_KEY.rtf \
  --output-dir work/life-is-good/qa \
  --force
```

The runner:

1. Validates `art.png`, `layout.json`, `pet-transform.md`, the user pet, optional
   generated-name PNG, and API-key input before making a paid request.
2. Calls Tool 1 to write `transformed-pet.png` in the output directory.
3. Calls Tool 3 with the optional name image to write `final-preview.png` and
   `final-preview-debug.png` in the same directory.
4. Prints the resolved input and output paths and returns a nonzero exit code if
   either step fails.
5. Never decides whether the design looks good. Visual acceptance remains a
   human POC decision.

`--force` applies to all runner outputs. Without it, the runner fails before the
API request if any target output already exists. This prevents paying for an
image that cannot be saved.

## 13. One-Step Pipeline Orchestrator

Command: `pawmarvel-pipeline`

This is a thin workflow coordinator for new-template experiments. It reuses the
existing Python entry functions; it does not duplicate API request, prompt,
layout, or rendering logic. Its inputs are the sample design, art prompt,
optional approved pet-prompt baseline, representative user pet, pet name, name
method (`font` or `ai`), font, template directory, and optional run directory.

The coordinator performs these stages in order:

1. Resolve and validate every input and planned output before a paid call.
2. Copy the sample, art prompt, optional baseline, and pet into stable artifact
   locations for traceability.
3. Generate low-resolution `art.png`.
4. Author `pet-transform.md` plus its provenance sidecar using the direct
   multimodal prompt generator and optional critic.
5. Generate the representative transparent pet cutout.
6. Start the local layout editor and block until a saved browser session closes.
7. In `ai` name mode, configure the layout-aware name rules, validate the name,
   create its concrete prompt, and generate `generated-name.png`. In `font`
   mode, retain the deterministic bundled-font rendering path.
8. Render `preview.png` and `preview-debug.png`.
9. Copy the exact accepted layout to `layout.snapshot.json` and write `run.json`
   with source hashes, non-secret settings, name validation, and artifact paths.

The editor exposes a **Save & continue** action. It also sends a local close
notification and heartbeat; closing a saved browser tab/window shuts down the
localhost server and resumes the command. Closing without a successful save is
an error. This makes browser confirmation a bounded pipeline stage instead of a
server that must be interrupted manually.

The coordinator deliberately has no queue, database, remote service, or resume
engine. `--force` is the explicit reset mechanism for mutable POC artifacts.
`--dry-run` validates inputs and prints the resolved plan without writes or API
calls. `--layout-mode existing` is available for automated reruns that already
have an accepted layout.

## 14. Working-directory layout

```text
work/
  life-is-good/
    art.png
    layout.json
    art-template.md
    pet-transform.md
    pet-transform.analysis.json
    name-generation.json
    name-prompt-template.md
    name-style-reference.png
    fonts/
      ExampleFont.ttf
    qa/
      transformed-pet.png
      name-slot-debug.png
      name-BUDDY.md
      name-BUDDY.request.json
      generated-name.png
      calibration-preview.png
      final-preview.png
      final-preview-debug.png
    runs/
      sausage/
        input-pet.png
        transformed-pet.png
        generated-name.png
        preview.png
        preview-debug.png
        layout.snapshot.json
        run.json
```

The cropped web-research reference and original user pet can remain in separate
local input directories. They do not need to be copied into the reusable
template artifacts.

The working directory is intentionally mutable. If a template is accepted, it
may be copied to an `accepted/` directory for comparison with later experiments,
but immutable release packaging is deferred.

## 15. End-to-end MVP workflow

### Offline template authoring

1. Crop the design example to a front-facing flat design area.
2. Write or refine `art-template.md`.
3. Run Tool 1 to generate background-only `art.png`.
4. Run Tool 1A with the sample and approved art to derive
   `pet-transform.md` and `pet-transform.analysis.json`; inspect both artifacts.
5. Run Tool 1 with only one representative pet and the derived prompt to create
   `qa/transformed-pet.png`.
6. Run Tool 2 with the art, reference, representative pet, name, and font to
   establish the initial name box using the fast font preview.
7. Run Tool 2A `configure` with the approved layout, art, and design reference;
   inspect `qa/name-slot-debug.png` and the cropped style reference.
8. Run Tool 2A `create` for the sample name. If accepted, pass its prompt,
   cropped style reference, and recorded API settings to Tool 1 to create
   `qa/generated-name.png`.
9. Reopen Tool 2 with `--name-image` to verify or adjust the same name box. If
   geometry changes, rerun Tool 2A `configure` and `create` before accepting the
   name asset.

### Production-like POC validation

10. Run `pawmarvel-poc-run` with a user pet, pet name, template directory,
   optional `--name-image`, and API-key input.
11. The runner transforms the pet and renders the final and debug previews.
12. Inspect `final-preview.png`, exact name spelling, and the debug overlay.
13. If the result is not acceptable, adjust the prompt, regenerate art, pet, or
    name, or edit the layout, then rerun the same command.
14. Mark the experiment successful through a simple human decision when the
    preview demonstrates the workflow.

There is no online service, formal customer approval, operations approval, or
vendor handoff in this MVP.

## 16. Technical structure

```text
TemplateGenerator/
  src/pawmarvel_generator/
    cli.py                  # existing Tool 1
    layout_cli.py           # Tool 2 command
    layout_server.py        # localhost editor
    name_prompt_cli.py      # Tool 2A configure/create command
    pet_prompt_cli.py       # Tool 1A multimodal prompt derivation
    renderer.py             # shared deterministic placement/rendering
    render_cli.py           # Tool 3 command
    poc_runner.py           # installed live POC automation harness
    pipeline_cli.py         # complete template + tracked-preview coordinator
    config.py               # layout parsing and validation
    static/layout.html      # plain local editor UI
    static/layout.js
    static/layout.css
  tests/
    test_cli.py
    test_config.py
    test_renderer.py
    test_name_prompt_cli.py
    test_pet_prompt_cli.py
    test_poc_runner.py
    test_pipeline_cli.py
    test_layout_server.py
    fixtures/
```

Dependencies:

- Existing `openai` dependency for Tool 1 image generation and Tool 1A Responses
  API prompt authoring, criticism, and structured fallback.
- `Pillow` for image loading, alpha handling, composition, and text metrics.
- Python standard library for the CLI, JSON, file handling, and local server.

The package metadata must expose every command and include the editor assets:

```toml
[project]
dependencies = ["openai>=2.48.0,<3", "Pillow"]

[project.scripts]
pawmarvel-generate = "pawmarvel_generator.cli:main"
pawmarvel-layout-config = "pawmarvel_generator.layout_cli:main"
pawmarvel-name-prompt = "pawmarvel_generator.name_prompt_cli:main"
pawmarvel-pet-prompt = "pawmarvel_generator.pet_prompt_cli:main"
pawmarvel-render = "pawmarvel_generator.render_cli:main"
pawmarvel-poc-run = "pawmarvel_generator.poc_runner:main"
pawmarvel-pipeline = "pawmarvel_generator.pipeline_cli:main"

[tool.setuptools.package-data]
pawmarvel_generator = ["static/*.html", "static/*.js", "static/*.css"]
```

Load packaged editor files with `importlib.resources`, not paths relative to the
current working directory.

Portable local installation:

```bash
cd TemplateGenerator
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/pawmarvel-poc-run --help
```

The checked-in shell launcher may remain as a convenience, but the installed
console commands must not depend on a workspace-level `../.venv`.

Do not add OpenCV, NumPy, a frontend framework, a database, an embedded
super-resolution model, or a production web service for this iteration.

## 17. Testing strategy

### Unit tests

- Layout accepts a complete valid configuration and rejects missing or invalid
  required values.
- Art decoding supplies the layout canvas dimensions.
- Alpha trimming finds visible pet bounds and handles an empty alpha channel.
- `contain` produces the expected pet dimensions.
- Bottom-center anchoring produces expected coordinates.
- Text centering and shrink-to-fit use the bundled font consistently.
- Generated-name images require transparency, trim to visible alpha, preserve
  aspect ratio, and obey the same name-box alignments.
- Name prompt configuration maps the approved name box into the reference,
  snapshots layout dependencies, and refuses accidental overwrite.
- Per-name prompt creation normalizes supported names, measures actual glyph
  width, rejects underfilled or illegible fits, selects an API canvas, and
  detects a stale layout snapshot.
- Pet prompt analysis submits exactly two labeled images, requires strict
  structured output, and renders a self-contained one-pet-input prompt.
- Pet prompt artifacts record hashes and analysis provenance, reject mismatched
  aspect ratios and invalid responses, and never persist credentials or base64
  source data.
- Output dimensions match the decoded `art.png` canvas.
- Existing files are not overwritten without `--force`.
- Tool 1 accepts sample-only, pet-only, and combined inputs and rejects a
  request with neither image.
- The POC runner validates every output path before calling Tool 1, invokes Tool
  1 and Tool 3 once each, and propagates failures without leaving a false-success
  preview.
- An invalid or missing optional name image fails POC preflight before the paid
  pet-transformation request.
- `/preview` and `/save` validate through the same Python layout parser and use
  the shared Pillow renderer.

### Deterministic end-to-end test

Commit small synthetic art, pet, layout, and font fixtures. Render a known name
and verify dimensions, resolved bounds, and a small pixel-difference tolerance.
This test does not call the OpenAI API.

### Live POC smoke test

Use one real user pet image and, optionally, one generated-name image:

1. Call `pawmarvel-poc-run` with the template, pet, name, optional name image,
   and API-key input.
2. Confirm that the runner invokes transformation and rendering successfully.
3. Confirm that it produces the transformed pet, final preview, and debug image.
4. Perform a human visual check of transformation and template quality.

The live smoke test may incur API cost and should be opt-in, not part of the
default unit-test suite.

## 18. MVP acceptance criteria

- All commands install and run locally with Python 3.10+.
- The installed `pawmarvel-poc-run` command completes the live transformation
  and preview sequence without manual command chaining.
- Tools 2, 2A, and 3 work without network access or an API key.
- Tool 1A runs once per template with API access; its saved prompt later allows
  Tool 1 to transform a pet using only the customer image.
- Tool 1 has no machine-specific output path requirement.
- An operator can create `layout.json` without manually calculating pixels.
- The layout editor uses a real representative transformed pet.
- Layout preview and save output are rendered by the shared Pillow renderer, not
  by browser-only text or image metrics.
- `art.png` contains only reusable design artwork.
- The renderer output dimensions exactly match the decoded `art.png` canvas.
- Transparent source padding does not move the visible pet.
- The pet name fits or fails with a clear error; it is not silently clipped.
- The same `layout.json` renders with either the existing font path or an
  optional generated-name PNG.
- A layout-aware offline command produces a reusable name prompt template,
  lettering crop, and per-name request metadata without changing layout schema.
- Too-short and too-long names are rejected using font metrics and layout
  geometry before a paid image-generation request.
- Generated lettering is alpha-trimmed, proportionally fitted, and rejected if
  it is opaque or empty. Misspelling, multiline output, and unrelated artwork
  remain explicit human acceptance failures.
- A user pet can be transformed and rendered into a visually reviewable final
  preview.
- The derived pet prompt separately preserves user-pet identity while applying
  the example's observable style, pose, expression, crop, and silhouette, and
  always requires a genuinely transparent isolated output.
- An operator can modify a prompt or layout and rerun the workflow without
  creating manifests or version migrations.
- The proof-of-concept result is sufficient to decide whether to invest in the
  future production workflow.

## 19. Implementation record

Implemented in this order:

1. Refined Tool 1 for optional independent image inputs, neutral prompt labels,
   and portable output paths.
2. Added the small `layout.json` model and validation.
3. Added shared pet and text placement in `renderer.py`.
4. Added `pawmarvel-render` and deterministic renderer tests.
5. Added and packaged the minimal Layout Configurator routes and static UI.
6. Added the installed `pawmarvel-poc-run` orchestration harness.
7. Verified that editable installation exposes all commands and packaged static
   files.
8. Verified the supplied LifeIsGood paths and request configuration through a
   no-cost dry-run. The opt-in paid generation and human visual acceptance steps
   are documented in the operations guide.
9. Added optional generated-name PNG preview support without changing the
   `layout.json` schema or removing the original font-rendered preview.
10. Added `pawmarvel-name-prompt` with offline style-reference extraction,
    layout snapshot validation, measured name constraints, concrete prompt
    creation, request sidecars, and unit tests.
11. Added `pawmarvel-pet-prompt` with two-image Responses API analysis, strict
    structured output, locally enforced runtime-prompt requirements, provenance
    records, dry-run/progress/overwrite behavior, and unit tests.
12. Changed pet-prompt authoring to direct multimodal prompt generation by
    default, added a default visual critic pass, optional art and approved
    baseline inputs, high reasoning with original image detail, schema-v2
    provenance, and retained the original structured compiler as a fallback.

Do not implement future production concerns until the low-resolution workflow
has been validated.

## 20. Decisions embodied by the MVP

The implementation uses these deliberately narrow assumptions:

1. One low-resolution canvas is sufficient for the current experiment.
2. One pet and one single-line name cover the first test design.
3. Reference screenshots can be manually cropped before authoring.
4. A representative transformed pet will be supplied to the layout editor.
5. Simple visual operator acceptance is sufficient for the MVP.
6. High-resolution, print, approval-state, and vendor concerns remain deferred.
