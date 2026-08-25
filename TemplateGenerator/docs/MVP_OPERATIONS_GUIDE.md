# PawMarvel Low-Resolution MVP Operations Guide

This guide retains the supplied LifeIsGood/SausageDogPuppy step-by-step example.
Its recommended one-command test uses the separate Charlie/WhiteFuffyDog fixture
to demonstrate the same pipeline against a substantially different style.

The workflow creates:

```text
art.png
layout.json
art-template.md
pet-transform.md
pet-transform.analysis.json
name-generation.json
name-prompt-template.md
name-style-reference.png
fonts/<selected-font>.ttf
qa/transformed-pet.png
qa/name-slot-debug.png
qa/name-SAUSAGE.md
qa/name-SAUSAGE.request.json
qa/generated-name.png
qa/calibration-preview.png
qa/final-preview.png
qa/final-preview-debug.png
```

It is a low-resolution proof of concept. It does not create print-ready assets.

## 1. Repository example inputs

All non-secret inputs used by this guide are checked into the repository. No
source asset from another PawMarvel directory is required.

```text
Sample design:
examples/life-is-good/reference-design.png

User pet:
examples/life-is-good/pet-input.png

Prompt for art.png template generation:
examples/life-is-good/art-template.md

Approved baseline for pet-transform prompt authoring:
examples/life-is-good/pet-transform-baseline.md

Separate one-command comparison fixture:
examples/charlie-well-trained/reference-design.png
examples/charlie-well-trained/pet-input.png
examples/charlie-well-trained/art-template.md
```

The API key is deliberately not a repository example asset. Export it as
`OPENAI_API_KEY` before running a paid example. Never add a key or `.env` file
to the repository.

## 2. Install

From the repository:

```bash
cd "/Users/qbit/Documents/PawMarvel/Code/playground/TemplateGenerator"
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Confirm all installed commands:

```bash
.venv/bin/pawmarvel-generate --help
.venv/bin/pawmarvel-layout-config --help
.venv/bin/pawmarvel-name-prompt --help
.venv/bin/pawmarvel-pet-prompt --help
.venv/bin/pawmarvel-render --help
.venv/bin/pawmarvel-poc-run --help
.venv/bin/pawmarvel-pipeline --help
```

Tool 1 follows the current OpenAI Image API edit pattern: it sends one or more
reference images and receives base64 image output. GPT Image 2 transparent
output must use PNG or WebP. See the
[official OpenAI image-generation guide](https://developers.openai.com/api/docs/guides/image-generation).
The pet-prompt tool uses the Responses API to author the final prompt directly
from the sample and optional art, then runs a visual critic pass by default.
The later GPT Image 2 transformation call still receives only the user pet.

## 3. Prepare the working directory

The commands below use shell variables only to shorten the quoted paths:

```bash
PAWMARVEL_PROJECT="/Users/qbit/Documents/PawMarvel/Code/playground/TemplateGenerator"
PAWMARVEL_TEMPLATE="$PAWMARVEL_PROJECT/work/life-is-good"
PAWMARVEL_EXAMPLE="$PAWMARVEL_PROJECT/examples/life-is-good"
PAWMARVEL_SAMPLE="$PAWMARVEL_EXAMPLE/reference-design.png"
PAWMARVEL_PET="$PAWMARVEL_EXAMPLE/pet-input.png"
PAWMARVEL_ART_PROMPT="$PAWMARVEL_EXAMPLE/art-template.md"
PAWMARVEL_PET_PROMPT_BASELINE="$PAWMARVEL_EXAMPLE/pet-transform-baseline.md"
PAWMARVEL_FONT="/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf"

mkdir -p "$PAWMARVEL_TEMPLATE/qa"
```

The font above exists on the development Mac and is suitable for an initial
condensed-name approximation. It is not claimed to be the exact reference font.

If `OPENAI_API_KEY` is not already set, enter it without echoing it or storing
it in the repository:

```bash
printf "OpenAI API key: "
read -r -s OPENAI_API_KEY
printf "\n"
export OPENAI_API_KEY
```

## Recommended one-step template and preview test

`pawmarvel-pipeline` runs the complete workflow below with one command. This
example deliberately uses the Charlie sleeping-cartoon reference and
WhiteFuffyDog input instead of the LifeIsGood assets used by the manual steps.
It creates reusable template assets, transforms the supplied pet, pauses for
layout confirmation in a local browser, and then creates the preview, debug
overlay, layout snapshot, and tracking metadata.

Use AI-generated name lettering:

```bash
PAWMARVEL_PIPELINE_EXAMPLE="$PAWMARVEL_PROJECT/examples/life-is-good"
PAWMARVEL_PIPELINE_TEMPLATE="$PAWMARVEL_PROJECT/work/life-is-good"
PAWMARVEL_PIPELINE_RUN="$PAWMARVEL_PIPELINE_TEMPLATE/runs/white-fluffy-dog"

"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-pipeline" \
  --sample-design "$PAWMARVEL_PIPELINE_EXAMPLE/reference-design.png" \
  --art-prompt "$PAWMARVEL_PIPELINE_EXAMPLE/art-template.md" \
  --pet-image "$PAWMARVEL_PIPELINE_EXAMPLE/pet-input.png" \
  --pet-name "FLUFFY" \
  --name-method ai \
  --font "$PAWMARVEL_FONT" \
  --template-dir "$PAWMARVEL_PIPELINE_TEMPLATE" \
  --run-dir "$PAWMARVEL_PIPELINE_RUN" \
  --quality high
```

When the editor opens, adjust the pet and name boxes and select
**Save & continue**. The command resumes as soon as that action closes the page.
You can also select **Save layout** and then close the browser tab or window;
the heartbeat detects the closure and returns control to the pipeline. Closing
without saving stops the pipeline with an error, so it cannot silently render
with an unconfirmed layout.

For the faster font-name variant, change only:

```bash
--name-method font
```

The command chooses an art size from the sample's aspect ratio. Override it with
`--art-size WIDTHxHEIGHT` only when a specific supported image size is required.
Before spending API credits, inspect the resolved paths and settings with
`--dry-run`. Use `--force` for an intentional rerun; without it, any existing
planned output stops the command before a paid call.

The one-step output is split into reusable template artifacts and one traceable
test run:

```text
work/charlie-well-trained/
  reference-design.png
  art-template.md
  art.png
  pet-transform.md
  pet-transform.analysis.json
  layout.json
  name-generation.json                 # AI name mode only
  name-prompt-template.md              # AI name mode only
  name-style-reference.png             # AI name mode only
  fonts/<selected-font>.ttf
  qa/calibration-preview.png
  qa/name-slot-debug.png                # AI name mode only
  runs/white-fluffy-dog/
    input-pet.png
    transformed-pet.png
    name-fluffy.md                       # AI name mode only
    name-fluffy.request.json             # AI name mode only
    generated-name.png                  # AI name mode only
    preview.png
    preview-debug.png
    layout.snapshot.json
    run.json
```

`run.json` records source hashes, resolved non-secret parameters, name method,
validation measurements, and artifact paths. It never records the API key.
`layout.snapshot.json` preserves the exact layout used for this preview even if
the reusable template layout is edited later.

The remaining sections intentionally return to the original
LifeIsGood/SausageDogPuppy workflow and document it step by step for inspection,
debugging, and selective reruns.

## 4. Validate and stage the inputs

Confirm that all required inputs exist before making an API request:

```bash
test -f "$PAWMARVEL_SAMPLE"
test -f "$PAWMARVEL_PET"
test -f "$PAWMARVEL_ART_PROMPT"
test -f "$PAWMARVEL_PET_PROMPT_BASELINE"
test -f "$PAWMARVEL_FONT"
test -n "${OPENAI_API_KEY:-}"
```

No output means every file exists. Copy the supplied art prompt into the
template working directory so the generated template retains the exact prompt
used:

```bash
cp "$PAWMARVEL_ART_PROMPT" \
  "$PAWMARVEL_TEMPLATE/art-template.md"
```

`pet-transform.md` will be generated after `art.png` is approved and is required
by `pawmarvel-poc-run`. The supplied `LifeIsGood_PetOnly.md` is an optional
known-good baseline for direct pet-prompt authoring; it guides structure but
does not override visual evidence from the current sample.

Optionally inspect all prompts before incurring API cost:

```bash
open "$PAWMARVEL_ART_PROMPT"
```

## 5. Generate reusable `art.png`

Run Tool 1 with the sample design and the supplied `LifeIsGood.md` art prompt:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-generate" \
  --sample-design "$PAWMARVEL_SAMPLE" \
  --prompt-file "$PAWMARVEL_ART_PROMPT" \
  --output-dir "$PAWMARVEL_TEMPLATE" \
  --output-name art.png \
  --size 1024x1120 \
  --quality high \
  --background transparent \
  --output-format png
```

`1024x1120` closely follows the supplied reference aspect ratio while meeting
GPT Image 2 size constraints. Tool 1 validates the returned image format,
dimensions, and alpha channel before saving it.

Inspect `art.png` before proceeding. For the MVP artifact model it must contain
only fixed, reusable artwork. Reject or revise the art prompt and regenerate if
the result contains:

- The example dog
- `CHARLIE`, `BENNY`, or another pet name
- A replacement pet or placeholder
- A T-shirt or other mockup background
- Missing headline, rainbow, paws, or other fixed design elements

The current `LifeIsGood.md` text describes two reference images and a complete
personalized `BENNY` result. That conflicts with a background-only reusable
`art.png` when only the sample design is supplied. The command above uses the
requested file exactly, but do not approve its result unless it passes the
background-only checklist. If it fails, revise that art prompt so it explicitly
requests fixed artwork only, then rerun the same command with `--force`.

## 6. Derive the reusable pet-transformation prompt

After approving background-only `art.png`, run the direct pet-prompt generator
once for this template. The approved manual prompt is supplied as a known-good
authoring example; the model must still derive the current style and pose from
the sample:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-pet-prompt" \
  --sample-design "$PAWMARVEL_SAMPLE" \
  --art "$PAWMARVEL_TEMPLATE/art.png" \
  --baseline-prompt "$PAWMARVEL_PET_PROMPT_BASELINE" \
  --output "$PAWMARVEL_TEMPLATE/pet-transform.md" \
  --analysis-output "$PAWMARVEL_TEMPLATE/pet-transform.analysis.json" \
  --model gpt-5.6 \
  --strategy direct \
  --reasoning-effort high \
  --image-detail original
```

Direct mode runs two paid multimodal Responses API calls by default, but only
during template authoring. The first writes the complete runtime prompt; the
second visually critiques and revises it for conflicts, verbosity, palette
leakage, wrong crop, fixed-art contamination, and weak transparency. Add
`--no-critic-pass` when a cheaper one-call draft is sufficient.

The command writes:

```text
work/life-is-good/pet-transform.md
work/life-is-good/pet-transform.analysis.json
```

The Markdown prompt is self-contained and assumes exactly one future input: the
customer pet photo. It never relies on the sample, art, or baseline being
supplied during pet generation. The JSON file is a schema-v2 provenance
sidecar: it records strategy, source hashes, model, reasoning effort,
image-detail setting, response IDs, prompt hashes and word counts, and whether
the critic changed the draft. It contains no API key or encoded image data.

Inspect `pet-transform.md` before continuing. Confirm that the target pose and
style describe the example pet—not the rainbow, text, paws, or other fixed
artwork. Confirm that it has no instruction to look at the sample, art, or a
second runtime image. Use `--force` only after reviewing the existing artifacts.

For comparison or debugging, the original JSON-analysis compiler remains
available:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-pet-prompt" \
  --sample-design "$PAWMARVEL_SAMPLE" \
  --art "$PAWMARVEL_TEMPLATE/art.png" \
  --output "$PAWMARVEL_TEMPLATE/pet-transform-structured.md" \
  --analysis-output "$PAWMARVEL_TEMPLATE/pet-transform-structured.analysis.json" \
  --strategy structured
```

Structured mode requires `--art`, performs one call by default, and does not
accept `--baseline-prompt`.

## 7. Generate the representative transformed pet

Use the generated prompt with only the SausageDogPuppy identity image to create
the representative pet used while authoring the layout:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-generate" \
  --pet-image "$PAWMARVEL_PET" \
  --prompt-file "$PAWMARVEL_TEMPLATE/pet-transform.md" \
  --output-dir "$PAWMARVEL_TEMPLATE/qa" \
  --output-name transformed-pet.png \
  --size 1024x1024 \
  --quality high \
  --background transparent \
  --output-format png
```

Inspect the pet asset. It must contain one isolated, recognizable dachshund
portrait with transparent background and no rainbow or text.

## 8. Author `layout.json`

Open Tool 2:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-layout-config" \
  --art "$PAWMARVEL_TEMPLATE/art.png" \
  --reference "$PAWMARVEL_SAMPLE" \
  --pet "$PAWMARVEL_TEMPLATE/qa/transformed-pet.png" \
  --pet-name "SAUSAGE" \
  --font "$PAWMARVEL_FONT" \
  --output "$PAWMARVEL_TEMPLATE/layout.json"
```

The command prints a `http://127.0.0.1:<port>/` URL and normally opens it in the
default browser.

In the editor:

1. Compare the reference with the Pillow preview.
2. Drag or resize the red pet box.
3. Drag or resize the blue name box.
4. Adjust rotation, font sizes, color, and alignment as needed.
5. Select **Save & continue** to save and return control to the terminal. You
   may instead select **Save layout** and close the browser tab or window.
6. Confirm that the status reports saved `layout.json` and calibration paths.

The browser handles the controls, but every authoritative preview and saved
calibration image is rendered by the same Pillow renderer used by Tool 3.

Saved artifacts:

```text
work/life-is-good/layout.json
work/life-is-good/fonts/DIN Condensed Bold.ttf
work/life-is-good/qa/calibration-preview.png
```

To replace an existing layout without an editor confirmation, add `--force`.

## 9. Configure and generate the design-specific pet-name image

First derive the reusable name-generation rules after `layout.json` has been
```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-name-prompt" configure \
  --sample-design "$PAWMARVEL_SAMPLE" \
  --art "$PAWMARVEL_TEMPLATE/art.png" \
  --layout "$PAWMARVEL_TEMPLATE/layout.json" \
  --output-dir "$PAWMARVEL_TEMPLATE"
```

This offline command maps `name.box` onto the sample design and creates:

```text
work/life-is-good/name-generation.json
work/life-is-good/name-prompt-template.md
work/life-is-good/name-style-reference.png
work/life-is-good/qa/name-slot-debug.png
```

Inspect `name-slot-debug.png`. The blue rectangle is the exact mapped name box;
the green rectangle is the crop saved as `name-style-reference.png` and
normally coincides with it. Use `--crop-padding-ratio` only when the box cuts off
part of the lettering; padding can accidentally include nearby fixed artwork.
If it captures the wrong lettering, correct `layout.json` in Tool 2 and rerun
`configure --force`.

Next validate `SAUSAGE` against the approved name box and create its concrete
prompt plus request metadata:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-name-prompt" create \
  --config "$PAWMARVEL_TEMPLATE/name-generation.json" \
  --pet-name "SAUSAGE" \
  --output "$PAWMARVEL_TEMPLATE/qa/name-SAUSAGE.md"
```

The command normalizes the name to uppercase, measures it with the layout's
bundled font, and rejects it before an API call when it is too visually short,
cannot fit at `min_font_size_px`, or requires more shrinking than the configured
legibility limit. Character count is only advisory for long names; measured
width is authoritative. It also writes `qa/name-SAUSAGE.request.json`, which
records the chosen API parameters.

Generate the accepted name using the cropped style reference and the concrete
prompt:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-generate" \
  --sample-design "$PAWMARVEL_TEMPLATE/name-style-reference.png" \
  --prompt-file "$PAWMARVEL_TEMPLATE/qa/name-SAUSAGE.md" \
  --output-dir "$PAWMARVEL_TEMPLATE/qa" \
  --output-name generated-name.png \
  --size 1536x512 \
  --quality high \
  --background transparent \
  --output-format png
```

For this example, `name-SAUSAGE.request.json` selects `1536x512`. Use the
`api_parameters.size` written to that file if another name selects the
`2048x688` long-name canvas. The API canvas is intentionally larger than
`name.box`; the renderer removes transparent padding and proportionally fits
the visible lettering into the stored box.

Inspect `generated-name.png` before continuing. It must:

- Spell `SAUSAGE` exactly once on one line.
- Match the reference name's condensed vintage lettering and distress.
- Have a transparent background and unclipped visible effects.
- Contain no pet, rainbow, paw prints, tagline, or product mockup.

GPT Image text rendering is not deterministic. Regenerate if the spelling is
wrong. If the style is consistently wrong, refine
`name-prompt-template.md`; this is an operator-controlled template artifact.
After editing that file, `create --force` regenerates the concrete prompt.

## 10. Preview the generated name with the same layout

Reopen Tool 2 with `--name-image`. It loads the existing `layout.json`; no schema
conversion or second layout file is needed:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-layout-config" \
  --art "$PAWMARVEL_TEMPLATE/art.png" \
  --reference "$PAWMARVEL_SAMPLE" \
  --pet "$PAWMARVEL_TEMPLATE/qa/transformed-pet.png" \
  --pet-name "SAUSAGE" \
  --name-image "$PAWMARVEL_TEMPLATE/qa/generated-name.png" \
  --font "$PAWMARVEL_FONT" \
  --output "$PAWMARVEL_TEMPLATE/layout.json" \
  --force
```

The editor now uses the generated PNG as the authoritative name preview. Font
size and color controls are inactive, while the name box and horizontal and
vertical alignment controls still apply. Adjust the box if necessary, select
**Save & continue**. You may also save normally and close the browser.

To return to the original fast font preview, rerun the Tool 2 command from step
8 without `--name-image`.

## 11. Run the automated MVP personalization test

The POC runner validates every destination before making the paid API call,
transforms the user pet, and renders the final preview:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-poc-run" \
  --template-dir "$PAWMARVEL_TEMPLATE" \
  --pet-image "$PAWMARVEL_PET" \
  --pet-name "SAUSAGE" \
  --name-image "$PAWMARVEL_TEMPLATE/qa/generated-name.png" \
  --output-dir "$PAWMARVEL_TEMPLATE/qa" \
  --size 1024x1024 \
  --quality high \
  --force
```

Expected outputs:

```text
work/life-is-good/qa/generated-name.png (reused input from step 9)
work/life-is-good/qa/transformed-pet.png
work/life-is-good/qa/final-preview.png
work/life-is-good/qa/final-preview-debug.png
```

`--force` is necessary here because the representative transformed pet from
step 7 already exists. Without `--force`, the runner exits before calling the
API.

## 12. Render without another API call

After changing only `layout.json`, reuse the existing transformed pet and name
image with Tool 3:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-render" \
  --template-dir "$PAWMARVEL_TEMPLATE" \
  --pet "$PAWMARVEL_TEMPLATE/qa/transformed-pet.png" \
  --pet-name "SAUSAGE" \
  --name-image "$PAWMARVEL_TEMPLATE/qa/generated-name.png" \
  --output "$PAWMARVEL_TEMPLATE/qa/final-preview.png" \
  --debug-output "$PAWMARVEL_TEMPLATE/qa/final-preview-debug.png" \
  --force
```

This command is deterministic, offline, and free of API cost.

## 13. MVP visual acceptance checklist

Accept the template experiment when:

- `art.png` contains only reusable fixed artwork.
- The transformed pet is recognizable and isolated.
- Transparent padding does not shift the visible pet.
- Pet placement resembles the reference composition.
- The personalized name is readable and positioned correctly.
- The generated name spells `SAUSAGE` exactly and retains the reference's
  design details without distortion.
- The final preview contains no clipping or accidental background.
- Re-rendering with the same inputs gives the same composition.

If it fails, adjust only the responsible artifact:

| Problem | Adjust |
| --- | --- |
| Fixed graphics are wrong | `LifeIsGood.md`, restage it, then regenerate `art.png` |
| Derived pet style, pose, or crop is wrong | Review `pet-transform.md` and its provenance; rerun direct generation with the critic/baseline, or refine the approved prompt |
| User-pet identity is weak | Strengthen identity priorities in `pet-transform.md`, regenerate, and compare recognizable traits |
| Generated name spelling is wrong | Regenerate from `qa/name-SAUSAGE.md` |
| Generated name style is wrong | Refine `name-prompt-template.md`, rerun `create --force`, then regenerate the PNG |
| Name prompt tool reports a stale layout | Rerun `configure --force`, then rerun `create --force` |
| Pet or name position is wrong | Reopen Tool 2 and edit `layout.json` |
| Only placement changes | Run Tool 3 again; no API call is needed |

## 14. Tests

Run the offline suite:

```bash
cd "$PAWMARVEL_PROJECT"
.venv/bin/python -m unittest discover -s tests -v
```

The default suite mocks the OpenAI request. It does not consume API credits.
The layout-server tests bind only to a temporary `127.0.0.1` port.

## 15. Troubleshooting

### Output already exists

Review the existing file, then pass `--force` only when replacement is intended.

### No API key

Pass `--api-key-file` or export `OPENAI_API_KEY`. The key value is never printed.

### Transparent output error

Use `--background transparent` with `--output-format png` or `webp`, not JPEG.

### Pet is fully transparent

Regenerate it and confirm the prompt asks for an isolated visible pet rather
than an empty cutout.

### Pet prompt includes unrelated fixed artwork

When `--art` is used, confirm it is genuinely background-only and has the same
flat-design aspect ratio as the sample. Correct `art.png` and rerun with
`--force`. If the pet is visually unambiguous, retry direct mode without
`--art` so independently regenerated art cannot distract the prompt author.

### Pet pose is correct but identity is weak

Prompting cannot guarantee an exact identity-preserving reconstruction when the
input photo hides required anatomy or is too low quality. Use a clear pet photo
showing the face, eyes, muzzle, ears, coat, and markings. Inspect the structured
identity rules in the final prompt, strengthen them if needed, and reject any
result that looks like a generic breed replacement. Use `--strategy structured`
only when the field-level diagnostic analysis is specifically useful.

### Name cannot fit

The name-prompt tool uses actual font metrics rather than character count alone.
Increase the name-box width or height, reduce `font_size_px`, or reduce
`min_font_size_px` in the layout editor. Save the layout, rerun
`pawmarvel-name-prompt configure --force`, and retry `create`.

Do not bypass the rejection by hand-editing the concrete prompt. The rejection
means the approved layout cannot preserve the configured legibility for that
name.

### Generated name image is rejected

The PNG must have an alpha channel, some transparent background, and visible
lettering. Regenerate it with transparent PNG output if the renderer reports an
opaque or empty name image.

### Generated name is misspelled

Do not attempt to correct spelling in `layout.json`; it only controls placement.
Regenerate the name PNG from the concrete prompt. For a different pet name, run
`pawmarvel-name-prompt create` again and ensure `--pet-name`, the concrete
prompt, and the visible PNG spelling agree.

### Name configuration is stale

`name-generation.json` stores the approved canvas, name box, font, sizes, and
alignments. Any change to those `layout.json` values intentionally invalidates
the name configuration. Rerun `configure --force`; this refreshes the style crop
and snapshot before another name is accepted.

### Editor does not open a browser

Copy the printed localhost URL into a browser. Use `--no-open` when launching
the editor if manual browser opening is preferred.
