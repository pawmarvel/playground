# PawMarvel Static Print Design

Independent tooling for non-personalized print designs. It deliberately does
not depend on `TemplateGenerator` or its personalized bundle contract.

The operator can:

- load a flat local PNG collection into active review without reorganizing the
  source collection by hand;
- iterate and approve one product-independent seed design;
- move rejected seeds to an abandoned pool or approved seeds to a graduated
  pool;
- generate selected configured dog-breed and cat previews from an approved
  seed;
- share seed and subject previews for reviewer feedback;
- approve subject previews and render versioned product print outputs,
  individually or in a batch;
- review generated pixels in the local GUI; and
- publish immutable static bundles and release history plus a stable discovery
  catalog for deterministic FE latest-or-pinned selection.

Start with [the operator guide](docs/OPERATOR_GUIDE.md). The public FE handoff
is defined in [the contract design](docs/STATIC_PRINT_DESIGN_CONTRACT.md).

## Install

```bash
cd StaticPrintDesign
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

## Start the GUI

```bash
.venv/bin/pawmarvel-static-print init-workspace
cp static-print.env.example work/static-print.env
# Edit the private settings once. The server reloads this file on every start.
.venv/bin/pawmarvel-static-print serve --open
```

Generated work and private feedback remain under `work/` and are ignored by
Git. Production bundles live in S3; only schemas, profiles, code, tests, and
documentation belong in this repository.

The browser can preview transparent PNGs over a checkerboard, blue, white, or
a custom color and reports whether non-opaque pixels are present.

The `test` extra installs the JSON Schema validator used to verify that emitted
artifacts match the checked-in FE schemas. Run the focused contract tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
