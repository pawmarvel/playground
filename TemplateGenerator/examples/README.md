# Repository examples

The example assets are separated by role so shared inputs are not mistaken for
design-specific artifacts:

- `pet-inputs/` contains reusable sample customer-pet images that may be tested
  against any finished design reference.
- `life-is-good/` and `charlie-well-trained/` each contain that design's
  finished reference, art-template prompt, and pet-transformation prompt.
- `work/` contains generated reference outputs for inspection and is not an
  input-source directory.

Prompts are design-specific source artifacts and must remain beside their
corresponding reference. No API key belongs under `examples/`.
