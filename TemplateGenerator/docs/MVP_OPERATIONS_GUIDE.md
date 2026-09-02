# PawMarvel Preview-to-Print POC Operations Guide

This guide first runs the LifeIsGood design with the SausageDogPuppy input from
finished-design reference through template authoring, preview, print-candidate,
and bundle publication. It then consumes that bundle with WhiteFuffyDog to
create a second personalized preview and print-size design without regenerating
or editing the reusable template.

The MVP deliberately does not derive prompts automatically. Each design owns
two reviewed prompt source files beside its finished reference:

```text
examples/<design>/art-template.md
examples/<design>/pet-transform.md
```

Every new pet transformation sends ordered images to GPT Image 2:

1. the user pet, used only for identity;
2. the primary finished design reference, used for pose, expression, crop, and style;
3. any supporting finished-design references, used only as additional treatment evidence.

Repeat `--sample-design` in pipeline/generator commands or
`--reference-design` in POC/bundle commands to add supporting references. Flag
occurrence order is preserved. The first reference remains the layout editor's
visual comparison source.

## 1. Repository example inputs

```text
LifeIsGood design contract:
examples/life-is-good/reference-design.png
examples/life-is-good/art-template.md
examples/life-is-good/pet-transform.md

Charlie design contract:
examples/charlie-well-trained/reference-design.png
examples/charlie-well-trained/art-template.md
examples/charlie-well-trained/pet-transform.md

Reusable sample user pets:
examples/pet-inputs/sausage-dog-puppy.png
examples/pet-inputs/white-fluffy-dog.png
```

Each design directory contains its reference and its two corresponding prompts.
Pet inputs remain reusable across designs, but prompts must not be mixed between
design contracts. Never add an API key under `examples/`.

## 2. Install and configure the API key

```bash
cd "/Users/qbit/Documents/PawMarvel/Code/playground/TemplateGenerator"
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Confirm the installed commands:

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

Export the key without echoing or storing it in the repository:

```bash
printf "OpenAI API key: "
read -r -s OPENAI_API_KEY
printf "\n"
export OPENAI_API_KEY
```

## 3. Set the LifeIsGood example paths

```bash
PAWMARVEL_PROJECT="/Users/qbit/Documents/PawMarvel/Code/playground/TemplateGenerator"
PAWMARVEL_TEMPLATE="$PAWMARVEL_PROJECT/work/manual-life-is-good"
PAWMARVEL_SAMPLE="$PAWMARVEL_PROJECT/examples/life-is-good/reference-design.png"
PAWMARVEL_PET="$PAWMARVEL_PROJECT/examples/pet-inputs/sausage-dog-puppy.png"
PAWMARVEL_ART_PROMPT="$PAWMARVEL_PROJECT/examples/life-is-good/art-template.md"
PAWMARVEL_PET_PROMPT="$PAWMARVEL_PROJECT/examples/life-is-good/pet-transform.md"
PAWMARVEL_FONT_CATALOG="$PAWMARVEL_PROJECT/assets/fonts"
PAWMARVEL_PROFILE="$PAWMARVEL_PROJECT/profiles/blanket-king-9375x12375.json"

mkdir -p "$PAWMARVEL_TEMPLATE/qa"
```

The checked-in king-blanket profile defines:

- preview `art.png`: `800x1056`;
- transformed-pet generation canvas: `816x816`;
- print canvas: `9375x12375`;
- uniform preview-to-print scale: `11.71875`.

Inspect it before paid generation:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-product-profile" show \
  --profile "$PAWMARVEL_PROFILE"
```

The web screenshot remains visual evidence only. Product dimensions begin with
generated `art.png`, not with the screenshot dimensions.

## 4. Optional one-command E2E bundle pipeline

The pipeline uses the design's two checked-in prompts directly. It does not
call a text model, generate a prompt, or write derived prompt artifacts. With
the bundle flags below, it continues after preview generation through print
upscaling, print rendering, and clean bundle publication.

This section is the one-command alternative to the manual E2E in sections
5–12. Both examples publish the same catalog entry,
`life-is-good--blanket-king-9375x12375`; choose either path. The identity is
derived from the design ID plus the selected product profile ID.

```bash
PAWMARVEL_PIPELINE_TEMPLATE="$PAWMARVEL_PROJECT/work/life-is-good"
PAWMARVEL_PIPELINE_RUN="$PAWMARVEL_PIPELINE_TEMPLATE/runs/sausage-dog-puppy"
PAWMARVEL_PIPELINE_PRINT="$PAWMARVEL_PIPELINE_RUN/print"
PAWMARVEL_PIPELINE_BUNDLES="$PAWMARVEL_PROJECT/bundles"

pawmarvel_pipeline_debug() {
  "$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-pipeline" \
    --sample-design "$PAWMARVEL_SAMPLE" \
    --art-prompt "$PAWMARVEL_ART_PROMPT" \
    --pet-prompt "$PAWMARVEL_PET_PROMPT" \
    --pet-image "$PAWMARVEL_PET" \
    --pet-name "SAUSAGE" \
    --product-profile "$PAWMARVEL_PROFILE" \
    --font-catalog "$PAWMARVEL_FONT_CATALOG" \
    --template-dir "$PAWMARVEL_PIPELINE_TEMPLATE" \
    --run-dir "$PAWMARVEL_PIPELINE_RUN" \
    --print-dir "$PAWMARVEL_PIPELINE_PRINT" \
    --bundle-output-dir "$PAWMARVEL_PIPELINE_BUNDLES" \
    --design-id life-is-good \
    --upscale-backend deterministic \
    --quality high \
    "$@"
}

pawmarvel_pipeline_debug
```

When the editor opens, adjust the pet and name boxes and select **Save &
continue**. Closing without first saving is an error. Before spending API
credits, run `pawmarvel_pipeline_debug --dry-run`. Use `--force` only when
intentionally replacing the complete pipeline; use the selective debug commands
below for normal iteration.

The pipeline makes two image API calls: one for `art.png` and one for the
reference-guided transformed pet. The remaining layout, preview, upscale,
print-render, and bundle stages are offline with the deterministic backend. Its
generated artifacts are:

```text
work/life-is-good/
  source-reference-design.png
  source-reference-designs/          # only when supporting references are supplied
    reference-design-0002.png
  product-profile.json
  art.png
  layout.json
  fonts/<selected-font>.ttf
  fonts/OFL.txt
  qa/calibration-preview.png
  runs/sausage-dog-puppy/
    input-pet.png
    transformed-pet.png
    preview.png
    preview-debug.png
    layout.snapshot.json
    run.json
    print/
      art-print.png
      transformed-pet-print.png
      layout-print.json
      product-profile.json
      template-print-manifest.json
      pet-print-manifest.json
      final-print.png
      final-print-debug.png
      fonts/<selected-font>.ttf
      fonts/OFL.txt
bundles/catalog.json
bundles/life-is-good--blanket-king-9375x12375/
  art.png
  layout.json
  print/art.png
  layout-print.json
  reference-design.png
  reference-designs/                 # optional supporting references
    reference-design-0002.png
  art-template.md
  pet-transform.md
  qa/transformed-pet.png
  fonts/<selected-font>.ttf
  fonts/OFL.txt
```

`run.json` records the source paths and SHA-256 hashes of both design prompts.
The pipeline also publishes exact copies as `art-template.md` and
`pet-transform.md` in the bundle contract. It records the print artifacts and
successful publication; customer source data remains excluded from the bundle.

### Selectively rerun an authoring step

After one successful pipeline run, the shell function from section 4 becomes a
stage-level debug driver. Run one of these commands:

```bash
pawmarvel_pipeline_debug --rerun-step art
pawmarvel_pipeline_debug --rerun-step pet
pawmarvel_pipeline_debug --rerun-step layout
```

For example, after editing `art-template.md`, add `--rerun-step art` to replace
only `art.png`. After changing the customer pet or `pet-transform.md`, add
`--rerun-step pet`. Add `--rerun-step layout` to reopen the layout editor with
the existing art and transformed pet. Options are repeatable when two stages
must change together:

```bash
pawmarvel_pipeline_debug --rerun-step art --rerun-step layout
```

The selected stage is replaced, then the pipeline always rerenders
`preview.png`, `preview-debug.png`, `layout.snapshot.json`, and `run.json`.
When the section 4 print and bundle flags remain in the command, it also
rebuilds the print assets, final print candidate, and published bundle. Omit
those publication flags when only a low-resolution preview refresh is needed.

Selective reruns require the current `run.json` and unchanged reusable
prerequisites. The command rejects an unselected changed prompt/input, a
different reference design, product profile, layer dimensions, or runtime model
before making an API call. Include the corresponding rerun step for a changed
art prompt, pet prompt, or pet input. A layout-only rerun makes no
image API call and does not require an OpenAI API key. Do not combine
`--rerun-step` with `--force`: the selected rerun is already scoped permission
to replace its stage and downstream outputs. Use a full run in a new working
directory when changing the reference or product geometry.

## 5. Manual E2E: validate inputs and stage the profile

Sections 5–12 remain an independently runnable, step-by-step implementation in
`$PAWMARVEL_TEMPLATE` (`work/manual-life-is-good`). If section 4 was run in the
same shell, each authoring step below also shows the corresponding pipeline
debug command. Those debug commands operate on
`$PAWMARVEL_PIPELINE_TEMPLATE` (`work/life-is-good`) and require its existing
`run.json`; they do not overwrite the separate manual workspace. This makes it
possible to compare a direct tool invocation with the orchestrated result.

```bash
test -f "$PAWMARVEL_SAMPLE"
test -f "$PAWMARVEL_PET"
test -f "$PAWMARVEL_ART_PROMPT"
test -f "$PAWMARVEL_PET_PROMPT"
test -d "$PAWMARVEL_FONT_CATALOG"
test -f "$PAWMARVEL_PROFILE"
test -n "${OPENAI_API_KEY:-}"

cp "$PAWMARVEL_PROFILE" "$PAWMARVEL_TEMPLATE/product-profile.json"
```

No output means validation succeeded. Do not copy prompts into the mutable
working template. The bundle publisher copies the reviewed design sources
directly into the immutable bundle contract.

## 6. Generate reusable `art.png`

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-generate" \
  --sample-design "$PAWMARVEL_SAMPLE" \
  --prompt-file "$PAWMARVEL_ART_PROMPT" \
  --output-dir "$PAWMARVEL_TEMPLATE" \
  --output-name art.png \
  --product-profile "$PAWMARVEL_TEMPLATE/product-profile.json" \
  --profile-layer art \
  --quality high \
  --background transparent \
  --output-format png
```

The profile resolves the request to `800x1056`. Accept `art.png` only when it
contains fixed reusable artwork and excludes the example pet, personalized pet
name, mockup, garment, and placeholder pet.

Pipeline-managed debug equivalent, after editing the art prompt:

```bash
pawmarvel_pipeline_debug --rerun-step art
```

This makes one art-generation API call, reuses the existing transformed pet and
layout, and rebuilds preview, print staging, and the published test bundle. If
the regenerated art changes usable placement geometry, rerun art and layout
together instead:

```bash
pawmarvel_pipeline_debug --rerun-step art --rerun-step layout
```

## 7. Generate the transformed pet from ordered images

This is the required MVP pet-transformation contract. To match the shared
prompt's definitions, `pawmarvel-generate` sends the user pet as `IMAGE A` and
the finished design as `IMAGE B` regardless of CLI flag order.

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-generate" \
  --pet-image "$PAWMARVEL_PET" \
  --sample-design "$PAWMARVEL_SAMPLE" \
  --prompt-file "$PAWMARVEL_PET_PROMPT" \
  --output-dir "$PAWMARVEL_TEMPLATE/qa" \
  --output-name transformed-pet.png \
  --product-profile "$PAWMARVEL_TEMPLATE/product-profile.json" \
  --profile-layer transformed-pet \
  --quality high \
  --background transparent \
  --output-format png
```

Inspect the result before layout authoring. It must preserve the input pet's
recognizable identity while matching the reference pet's pose, expression,
crop, and rendering style. It must contain one isolated pet on genuine
transparency, without fixed design text or decoration.

Pipeline-managed debug equivalent, after changing the pet prompt or input pet:

```bash
pawmarvel_pipeline_debug --rerun-step pet
```

This makes one pet-transformation API call, preserves `art.png` and
`layout.json`, and rebuilds the downstream preview, print staging, and bundle.
When the new pet changes the intended placement rather than only its pixels,
combine `pet` and `layout`:

```bash
pawmarvel_pipeline_debug --rerun-step pet --rerun-step layout
```

## 8. Author `layout.json`

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-layout-config" \
  --art "$PAWMARVEL_TEMPLATE/art.png" \
  --reference "$PAWMARVEL_SAMPLE" \
  --pet "$PAWMARVEL_TEMPLATE/qa/transformed-pet.png" \
  --pet-name "SAUSAGE" \
  --font-catalog "$PAWMARVEL_FONT_CATALOG" \
  --runtime-model gpt-image-2 \
  --output "$PAWMARVEL_TEMPLATE/layout.json"
```

Adjust pet and name boxes, save, and close the browser. The tool stores the OFL
font and license with the template. The reference screenshot guides appearance
only; all coordinates are authored on `art.png`.

The local catalog contains 40 static TTF faces whose sibling `OFL.txt` files
pass validation. The tool maps the current name box onto the reference,
normalizes the lettering silhouettes, and selects the highest-scoring match by
default. The **Top font recommendations** drop-down presents the five best
matches with visual-confidence percentages. Selecting another entry refreshes
the authoritative Pillow preview before saving.

The confirmed font is written into `layout.json`; all five ranked options,
scores, and confidence values are recorded in
`qa/font-recommendation.json`. Only the selected font and OFL license are
published. Pass `--font` and optionally `--font-license` only to force a known
font and bypass automatic default selection. `--font-catalog` may also be
omitted because the checked-in local catalog is now the tool default.

The older expanded-cache flags remain available for compatibility but are not
part of this MVP flow. Broader OFL discovery is documented in the future
iteration roadmap.

Pipeline-managed debug equivalent:

```bash
pawmarvel_pipeline_debug --rerun-step layout
```

This opens the editor using the current pipeline-managed art and transformed
pet. It makes no image API call and does not require `OPENAI_API_KEY`, but it
still rebuilds the preview, print staging, and bundle after the editor is saved
and closed.

### 8.2 Correct the existing Charlie font selection

The existing Charlie workspace can be corrected without regenerating art or
the transformed pet. Run:

```bash
PAWMARVEL_CHARLIE_TEMPLATE="$PAWMARVEL_PROJECT/work/charlie-well-trained"
PAWMARVEL_CHARLIE_RUN="$PAWMARVEL_CHARLIE_TEMPLATE/runs/sausage-dog-puppy"

"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-pipeline" \
  --sample-design "$PAWMARVEL_PROJECT/examples/charlie-well-trained/reference-design.png" \
  --art-prompt "$PAWMARVEL_PROJECT/examples/charlie-well-trained/art-template.md" \
  --pet-prompt "$PAWMARVEL_PROJECT/examples/charlie-well-trained/pet-transform.md" \
  --pet-image "$PAWMARVEL_PROJECT/examples/pet-inputs/sausage-dog-puppy.png" \
  --pet-name "SAUSAGE" \
  --product-profile "$PAWMARVEL_PROFILE" \
  --font-catalog "$PAWMARVEL_FONT_CATALOG" \
  --template-dir "$PAWMARVEL_CHARLIE_TEMPLATE" \
  --run-dir "$PAWMARVEL_CHARLIE_RUN" \
  --print-dir "$PAWMARVEL_CHARLIE_RUN/print" \
  --bundle-output-dir "$PAWMARVEL_PROJECT/bundles" \
  --design-id charlie-well-trained \
  --upscale-backend deterministic \
  --quality high \
  --rerun-step layout
```

The editor analyzes the reference name region and preselects Amatic SC Bold as
the current best catalog match, even though the older saved layout uses Anton.
Confirm the recommendation, change the text color to near-black, reduce the name
box to match the small reference label, and then save. No API call is made. The
pipeline regenerates
`layout.snapshot.json`, print layout, print candidate, and
`bundles/charlie-well-trained--blanket-king-9375x12375` with the confirmed
font.

## 9. Render the low-resolution preview

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-render" \
  --template-dir "$PAWMARVEL_TEMPLATE" \
  --pet "$PAWMARVEL_TEMPLATE/qa/transformed-pet.png" \
  --pet-name "SAUSAGE" \
  --output "$PAWMARVEL_TEMPLATE/qa/final-preview.png" \
  --debug-output "$PAWMARVEL_TEMPLATE/qa/final-preview-debug.png"
```

This step is deterministic and makes no API call.

There is no separate pipeline rerun selector for rendering. Every
`pawmarvel_pipeline_debug --rerun-step ...` command automatically performs this
preview render after its selected authoring stages.

## 10. Prepare reusable print art, then the representative print pet

First upscale the reusable template assets. This is the offline,
once-per-template operation:

```bash
PAWMARVEL_PRINT="$PAWMARVEL_TEMPLATE/print/sausage-dog-puppy"

"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-upscale-template" \
  --template-dir "$PAWMARVEL_TEMPLATE" \
  --product-profile "$PAWMARVEL_TEMPLATE/product-profile.json" \
  --output-dir "$PAWMARVEL_PRINT" \
  --backend deterministic
```

It writes `art-print.png`, mechanically derives `layout-print.json`, copies the
font, OFL license, and profile, and records hashes in
`template-print-manifest.json`. It never accepts or generates a customer pet.

Next upscale only the representative transformed pet used by this offline E2E
test:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-upscale-pet" \
  --template-dir "$PAWMARVEL_TEMPLATE" \
  --print-layout "$PAWMARVEL_PRINT/layout-print.json" \
  --transformed-pet "$PAWMARVEL_TEMPLATE/qa/transformed-pet.png" \
  --output-dir "$PAWMARVEL_PRINT" \
  --backend deterministic
```

This writes only `transformed-pet-print.png` and `pet-print-manifest.json`. The
pet manifest binds the customer layer to hashes of the approved preview layout,
print layout, and print art. No pipeline `run.json` or `layout.snapshot.json` is
required for either command. `pawmarvel-upscale` remains as a backward-compatible
one-command coordinator for older scripts.

The deterministic backend preserves geometry with Lanczos but cannot invent
missing detail. `--backend bria` can provide a visual enhancement pass when
`BRIA_API_TOKEN` is configured, but a roughly `11.7x` result still requires
human inspection at 100% zoom.

The section 4 debug function retains all print and publication flags, so every
selective rerun automatically repeats both print-preparation stages. To debug
only through low-resolution preview, invoke the pipeline without `--print-dir`,
`--bundle-output-dir`, and `--design-id`.

## 11. Render the print candidate

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-render" \
  --template-dir "$PAWMARVEL_PRINT" \
  --layout "$PAWMARVEL_PRINT/layout-print.json" \
  --pet "$PAWMARVEL_PRINT/transformed-pet-print.png" \
  --pet-name "SAUSAGE" \
  --product-profile "$PAWMARVEL_PRINT/product-profile.json" \
  --output "$PAWMARVEL_PRINT/final-print.png" \
  --debug-output "$PAWMARVEL_PRINT/final-print-debug.png"
```

The renderer verifies both split print manifests and exact profile dimensions,
then writes `final-print.manifest.json`. A profile with
`vendor_requirements_confirmed: false` produces a print candidate, not an
automatic vendor-ready certification.

## 12. Publish the two-resolution template bundle

```bash
PAWMARVEL_BUNDLES="$PAWMARVEL_PROJECT/bundles"

"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-bundle" \
  --template-dir "$PAWMARVEL_TEMPLATE" \
  --design-id life-is-good \
  --product-profile "$PAWMARVEL_TEMPLATE/product-profile.json" \
  --output-dir "$PAWMARVEL_BUNDLES" \
  --print-art "$PAWMARVEL_PRINT/art-print.png" \
  --print-layout "$PAWMARVEL_PRINT/layout-print.json" \
  --exemplar "$PAWMARVEL_TEMPLATE/qa/transformed-pet.png" \
  --reference-design "$PAWMARVEL_SAMPLE" \
  --art-prompt "$PAWMARVEL_ART_PROMPT" \
  --pet-prompt "$PAWMARVEL_PET_PROMPT" \
  --runtime-model gpt-image-2
```

The clean consumer bundle contains the primary finished design reference,
optional ordered supporting references, and its exact two design-specific
prompt sources:

```text
bundles/catalog.json
bundles/life-is-good--blanket-king-9375x12375/
  art.png
  layout.json
  print/art.png
  layout-print.json
  reference-design.png
  reference-designs/                 # optional
    reference-design-0002.png
  art-template.md
  pet-transform.md
  qa/transformed-pet.png
  fonts/<selected-font>.ttf
  fonts/OFL.txt
```

`catalog.json` records the composite template ID, design ID, complete product
profile metadata, preview/print dimensions, runtime model, and relative bundle
path. The frontend selects a template by both design and product variant; it
must not key a catalog by design alone. The `reference_designs` array lists the
primary and supporting reference paths in API input order.

The frontend or backend reads `pet-transform.md` from the same bundle as the
primary `reference-design.png` and appends any files under `reference-designs/`
in lexical order before combining them with the current user pet.
`art-template.md` preserves template-generation provenance for later controlled
regeneration. Do not publish the mutable authoring `work/` directory.

The section 4 pipeline debug function republishes this bundle after every
selective rerun. No separate `bundle` rerun selector is needed because bundle
publication is a deterministic downstream stage.

## 13. Reuse the bundle with another user pet

This is the consumer-side proof. It reuses the published LifeIsGood preview
art, print art, layouts, font, and reference design. It makes one paid image
call for the new pet and does not regenerate `art.png`, author a layout, or
publish another template bundle.

Set separate per-personalization paths for WhiteFuffyDog:

```bash
PAWMARVEL_BUNDLES="$PAWMARVEL_PROJECT/bundles"
PAWMARVEL_BUNDLE="$PAWMARVEL_BUNDLES/life-is-good--blanket-king-9375x12375"
PAWMARVEL_SECOND_PET="$PAWMARVEL_PROJECT/examples/pet-inputs/white-fluffy-dog.png"
PAWMARVEL_SECOND_NAME="FLUFFY"
PAWMARVEL_SECOND_RUN="$PAWMARVEL_PROJECT/work/bundle-runs/life-is-good-white-fluffy-dog"
PAWMARVEL_SECOND_PREVIEW="$PAWMARVEL_SECOND_RUN/preview"
PAWMARVEL_SECOND_PRINT="$PAWMARVEL_SECOND_RUN/print-staging"

test -f "$PAWMARVEL_BUNDLE/art.png"
test -f "$PAWMARVEL_BUNDLE/layout.json"
test -f "$PAWMARVEL_BUNDLE/print/art.png"
test -f "$PAWMARVEL_BUNDLE/layout-print.json"
test -f "$PAWMARVEL_BUNDLE/reference-design.png"
test -f "$PAWMARVEL_BUNDLE/art-template.md"
test -f "$PAWMARVEL_BUNDLE/pet-transform.md"
test -f "$PAWMARVEL_SECOND_PET"
test -n "${OPENAI_API_KEY:-}"

mkdir -p "$PAWMARVEL_SECOND_PREVIEW"
```

### 13.1 Transform the second pet and render its preview

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-poc-run" \
  --template-dir "$PAWMARVEL_BUNDLE" \
  --pet-image "$PAWMARVEL_SECOND_PET" \
  --reference-design "$PAWMARVEL_BUNDLE/reference-design.png" \
  --prompt-file "$PAWMARVEL_BUNDLE/pet-transform.md" \
  --pet-name "$PAWMARVEL_SECOND_NAME" \
  --size 816x816 \
  --quality high \
  --output-dir "$PAWMARVEL_SECOND_PREVIEW"
```

This writes the customer-specific files outside the immutable bundle:

```text
work/bundle-runs/life-is-good-white-fluffy-dog/
  preview/
    transformed-pet.png
    final-preview.png
    final-preview-debug.png
```

For a multi-reference bundle, add each supporting file after the primary using
another flag, preserving lexical order:

```bash
--reference-design "$PAWMARVEL_BUNDLE/reference-design.png" \
--reference-design "$PAWMARVEL_BUNDLE/reference-designs/reference-design-0002.png"
```

The rendered pet name can differ from the first example because the bundled
font and name box are reusable. If a new name does not fit, reject or revise
the input under the product's naming policy; do not change the shared bundle's
layout for one customer.

### 13.2 Scale the second transformed pet for the bundled print layout

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-upscale-pet" \
  --template-dir "$PAWMARVEL_BUNDLE" \
  --layout "$PAWMARVEL_BUNDLE/layout.json" \
  --print-layout "$PAWMARVEL_BUNDLE/layout-print.json" \
  --transformed-pet "$PAWMARVEL_SECOND_PREVIEW/transformed-pet.png" \
  --output-dir "$PAWMARVEL_SECOND_PRINT" \
  --backend deterministic
```

The command derives scale from the bundle's approved preview/print layout pair
and creates only customer-specific `transformed-pet-print.png` plus
`pet-print-manifest.json`. It neither regenerates nor copies art. The reusable
print art remains `$PAWMARVEL_BUNDLE/print/art.png`.

### 13.3 Render the second print-size personalized design

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-render" \
  --template-dir "$PAWMARVEL_BUNDLE" \
  --layout "$PAWMARVEL_BUNDLE/layout-print.json" \
  --pet "$PAWMARVEL_SECOND_PRINT/transformed-pet-print.png" \
  --pet-name "$PAWMARVEL_SECOND_NAME" \
  --output "$PAWMARVEL_SECOND_RUN/final-print.png" \
  --debug-output "$PAWMARVEL_SECOND_RUN/final-print-debug.png"
```

The final render reads the approved high-resolution art and print geometry from
the bundle while keeping every customer-specific output under the second run
directory. Confirm that `final-print.png` is `9375x12375` and visually matches
the low-resolution `final-preview.png` before treating the bundle-consumption
test as successful.

## 14. MVP acceptance checklist

- `art.png` contains only reusable fixed artwork.
- The transformed pet remains recognizable as the user pet.
- Its pose, expression, crop, and style follow the finished reference.
- The transformed asset has genuine transparency and no copied fixed artwork.
- Pet and name placement resemble the reference composition.
- The font-rendered name is exact, readable, and unclipped.
- Preview and print compositions match at their respective resolutions.
- `layout-print.json` is a uniform mechanical scale of `layout.json`.

If generation quality is wrong, edit the appropriate prompt inside that
design's `examples/<design>/` directory, retest that design, and republish its
bundle. Do not change another design's prompt as a workaround.

## 15. Test and troubleshoot

Run the offline suite:

```bash
cd "$PAWMARVEL_PROJECT"
.venv/bin/python -m unittest discover -s tests -v
```

Tests mock OpenAI calls and consume no API credits.

| Problem | Action |
| --- | --- |
| Fixed art contains a pet or name | Refine that design's `art-template.md`, then run `pawmarvel_pipeline_debug --rerun-step art` |
| Pet identity, pose, crop, or style is weak | Refine that design's `pet-transform.md`, keep exactly one user pet, and add only relevant ordered finished-design references before running `pawmarvel_pipeline_debug --rerun-step pet` |
| Pet or name position is wrong | Run `pawmarvel_pipeline_debug --rerun-step layout` |
| Upscale reports an aspect-ratio mismatch | Regenerate preview art from the same product profile; do not use screenshot-sized art |
| A standalone manual command reports an existing output | Review it first, then pass `--force` only to that standalone command; pipeline debug reruns do not use `--force` |

## Appendix A: One-command personalization test

For an existing template and layout, `pawmarvel-poc-run` performs one
reference-guided transformation and renders the preview:

```bash
"$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-poc-run" \
  --template-dir "$PAWMARVEL_TEMPLATE" \
  --pet-image "$PAWMARVEL_PET" \
  --reference-design "$PAWMARVEL_SAMPLE" \
  --prompt-file "$PAWMARVEL_PET_PROMPT" \
  --pet-name "SAUSAGE" \
  --size 816x816 \
  --quality high \
  --output-dir "$PAWMARVEL_TEMPLATE/qa"
```

When rendering an already transformed preview or print layer, use
`--transformed-pet` instead of `--pet-image`; reference and prompt inputs are
then unnecessary because no image API call occurs.
