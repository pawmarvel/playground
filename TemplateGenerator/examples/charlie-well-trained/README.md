# Charlie Well-Trained comparison example

This second style fixture is used by the one-command pipeline example in the
operations guide:

- `reference-design.png`: personalized Charlie design with a sleeping,
  dimensional cartoon dog and hand-lettered black/red headline.
- `pet-input.png`: WhiteFuffyDog representative user image.
- `art-template.md`: Charlie-specific background-only prompt that retains the
  fixed headline while reserving transparent pet and name regions.

The fixture intentionally has no approved pet-transform baseline. The pipeline
derives a new template-specific prompt from the reference and generated art,
which exercises the automated authoring path for a style unlike LifeIsGood.

No API key belongs in this directory. Supply `OPENAI_API_KEY` through the
environment when running the example.
