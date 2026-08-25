# LifeIsGood reproducible example

This directory contains the non-secret inputs needed by the operations guide:

- `reference-design.png`: cropped personalized design example.
- `pet-input.png`: representative user pet photo.
- `art-template.md`: supplied prompt used for the `art.png` experiment.
- `pet-transform-baseline.md`: approved manual pet-only prompt used as an
  optional authoring baseline.

It also retains two earlier prompt fixtures for focused/manual experiments:

- `pet-transform.md`: concise standalone pet transformation prompt.
- `name-image-sausage.md`: standalone `SAUSAGE` name-image prompt.

The one-step pipeline generates fresh reusable and per-run prompt artifacts; it
does not treat the two earlier fixtures as accepted outputs.

No API key belongs in this directory. Supply `OPENAI_API_KEY` through the
environment when running paid API examples.
