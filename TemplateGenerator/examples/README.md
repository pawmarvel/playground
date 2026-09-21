# Repository examples

The repository intentionally carries three small source-design examples
and a fourteen-pet reusable QA inventory. They demonstrate the operations guide
and exercise development tests; they are not production catalog entries. For
the MVP trial, an operator performs a lightweight source/rights review and uses
only non-customer QA pets; formal license evidence is not a publication gate.
Online additions retain their source and license metadata, while operator-
provided images are explicitly marked as unverified development fixtures.
The assets are separated by role so shared inputs are not mistaken for
design-specific artifacts:

- `pet-inputs/` contains the shared dog-and-cat inventory. Eight online additions are
  attributed in `ONLINE_FIXTURE_ATTRIBUTIONS.md`; the fixture manifests pin all
  image hashes and carry machine-readable trait and rights metadata.
- `authoring/fixture-sets/mvp-pets-smoke-v1/` selects three diverse dogs for
  fast prompt iteration. `mvp-pets-v1/` provides a fourteen-pet inventory from
  which an operator selects at least six for release QA. Both run exactly one
  attempt per selected pet.
- `life-is-good/` and `charlie-well-trained/` each contain that design's
  finished reference plus separate GPT and Gemini variants of the art-template
  and pet-transformation prompts.
- `cooper/` is the primary operations-guide example. It contains one primary
  finished design, two ordered supporting references, and the GPT prompts used
  by the MVP path. Optional-provider prompt variants are not required for this
  example.
- `life-is-good/font-reference.json` is a hash-bound, authoring-only example of
  a confirmed screenshot text region and its exact visible wording. It makes
  OFL ranking reproducible but is never a production bundle input.
- `life-is-good/layout-reference.json` binds operator-confirmed pet and name
  regions to the same screenshot. It replaces generic initial percentages with
  reference-relative geometry during layout authoring and is not bundled.
Prompt filenames follow `art-template-<provider>.md` and
`pet-transform-<provider>.md`. They are design- and provider-specific source
artifacts and must remain beside their corresponding reference. Tune one
variant without copying the result over the other. No API key belongs under
`examples/`.

Generated art, transformed pets, layouts, evaluations, bundles, and release
catalogs never belong under `examples/`. The guide writes them under ignored
`work/authoring/` and `work/exchange/` directories. Reviewed production releases
and FE conformance fixtures are shared through the S3 catalog rather than
checked into Git.
