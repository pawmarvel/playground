# PawMarvel Template Generator MVP

A lightweight Python toolset for testing low-resolution personalized pet design
templates.

The MVP provides:

- `pawmarvel-generate`: prompt-driven GPT Image 2 generation/editing with a
  sample image, a pet image, or both.
- `pawmarvel-layout-config`: localhost visual editor for `layout.json`, with
  font-name preview or optional generated-name PNG preview.
- `pawmarvel-name-prompt`: offline, layout-aware name prompt configuration and
  per-name fit validation.
- `pawmarvel-pet-prompt`: one-time direct multimodal prompt authoring with an
  optional visual critic and a legacy structured-analysis fallback.
- `pawmarvel-render`: deterministic Pillow composition of art, pet, and either
  a font-rendered or generated-image name.
- `pawmarvel-poc-run`: one-command pet transformation and final preview test.
- `pawmarvel-pipeline`: one-command template authoring plus a tracked preview
  run, with font or AI-generated name lettering.

## Documentation

- [MVP operations guide](docs/MVP_OPERATIONS_GUIDE.md)
- [MVP design](docs/OFFLINE_PERSONALIZATION_TOOLSET_DESIGN.md)
- [Future iterations](docs/FUTURE_PERSONALIZATION_ITERATIONS.md)

The complete non-secret LifeIsGood test fixture is in
[`examples/life-is-good`](examples/life-is-good): reference design,
representative pet, art prompt, and approved pet-transform baseline. The
separate [`examples/charlie-well-trained`](examples/charlie-well-trained)
fixture provides a different reference style and user pet for the one-command
pipeline comparison. The operations guide refers only to repository assets.

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
.venv/bin/pawmarvel-name-prompt --help
.venv/bin/pawmarvel-pet-prompt --help
.venv/bin/pawmarvel-render --help
.venv/bin/pawmarvel-poc-run --help
.venv/bin/pawmarvel-pipeline --help
```

Tool 1 and the pet-prompt generator read the API key from `--api-key-file` or
`OPENAI_API_KEY`. The layout, name-prompt, and render tools work offline. The
POC runner calls Tool 1 once and the renderer once.

Use `pawmarvel-pipeline` when starting from a new sample design. It generates
the art and reusable pet prompt, transforms a representative pet, waits for the
local layout editor to be saved and closed, then renders a preview/debug pair
and writes an exact layout snapshot plus `run.json` provenance. Select
`--name-method font` or `--name-method ai`.

`pawmarvel-pet-prompt` uses GPT-5.6 through the Responses API while authoring a
template. Direct mode is the default: it writes the final prompt from the sample
and optional background-only `art.png`, then runs a second visual critic call by
default. It writes `pet-transform.md` and a schema-v2 provenance JSON file. Add
`--no-critic-pass` for one-call authoring or `--strategy structured` to retain
the original JSON-analysis compiler. The generated prompt later runs through
`pawmarvel-generate` with only the user pet image.

Pass `--name-image generated-name.png` to the layout editor, renderer, or POC
runner to use AI-generated lettering. Omit it to retain the original font-name
behavior. Both modes reuse the same `layout.json` name box.

Run `pawmarvel-name-prompt configure` after the initial font-mode layout is
saved. It derives a cropped lettering reference and immutable layout snapshot.
Then run `pawmarvel-name-prompt create` for each name; the command rejects names
that cannot meet the template's measured width and legibility constraints.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests mock image-generation requests and do not consume API credits.
