# Repository examples

The repository intentionally carries exactly two small source-design examples
and two reusable pet inputs. They demonstrate the operations guide and exercise
development tests; they are not production catalog entries or approved
production QA fixtures. The repository does not assert production-use or
redistribution rights for these images. For the MVP trial, an operator performs
a lightweight source/rights review before using an image in a catalog and uses
only non-customer QA pets; formal license evidence is not a publication gate.
The assets are separated by role so shared inputs are not mistaken for
design-specific artifacts:

- `pet-inputs/` contains only `sausage-dog-puppy.png` and
  `white-fluffy-dog.png`; either may be tested against either design.
- `life-is-good/` and `charlie-well-trained/` each contain that design's
  finished reference plus separate GPT and Gemini variants of the art-template
  and pet-transformation prompts.
- `life-is-good/font-reference.json` is a hash-bound, authoring-only example of
  a confirmed screenshot text region and its exact visible wording. It makes
  OFL ranking reproducible but is never a production bundle input.
- `life-is-good/layout-reference.json` binds operator-confirmed pet and name
  regions to the same screenshot. It replaces generic initial percentages with
  reference-relative geometry during layout authoring and is not bundled.
Prompt filenames follow `art-template-{gpt|gemini}.md` and
`pet-transform-{gpt|gemini}.md`. They are design- and provider-specific source
artifacts and must remain beside their corresponding reference. Tune one
variant without copying the result over the other. No API key belongs under
`examples/`.

Generated art, transformed pets, layouts, evaluations, bundles, and release
catalogs never belong under `examples/`. The guide writes them under ignored
`work/authoring/` and `work/exchange/` directories. Reviewed production releases
and FE conformance fixtures are shared through the S3 catalog rather than
checked into Git.
