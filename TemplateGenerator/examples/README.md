# Repository examples

The example assets are separated by role so shared inputs are not mistaken for
design-specific artifacts:

- `pet-inputs/` contains reusable sample customer-pet images that may be tested
  against any finished design reference.
- `life-is-good/` and `charlie-well-trained/` each contain that design's
  finished reference plus separate GPT and Gemini variants of the art-template
  and pet-transformation prompts.
- `work/` contains generated reference outputs for inspection and is not an
  input-source directory.

Prompt filenames follow `art-template-{gpt|gemini}.md` and
`pet-transform-{gpt|gemini}.md`. They are design- and provider-specific source
artifacts and must remain beside their corresponding reference. Tune one
variant without copying the result over the other. No API key belongs under
`examples/`.
