# Design development and scaling guide

Audience: application owners and template authors choosing an operating flow.
Status: current workflow routing and quality policy. Use the
[MVP operations guide](MVP_OPERATIONS_GUIDE.md) for exact commands and the
[design-review operator guide](DESIGN_REVIEW_OPERATOR_GUIDE.md) for gallery/UI
actions. For other roles, use the [documentation index](README.md).

This guide covers four related but different jobs:

1. develop a brand-new design;
2. improve an existing design;
3. adapt an approved design to another product profile;
4. expand a category or franchise from an approved design language.

Each resulting `<design-id>--<product-profile-id>` is independently reviewed,
bundled, versioned, and activated. Reuse is an authoring convenience, never a
runtime dependency between production bundles.

## Pick the workflow

| Situation | Identity | Recommended starting point | Required new evidence |
| --- | --- | --- | --- |
| New visual concept | New design ID and product profile | Scratch-test art and pet prompts | Full art, pet, layout, assembly, print, and release flow |
| Improve an existing product | Same design/profile, successor experiments and bundle revision | Reopen only the affected finished stage | Affected stage plus every invalidated downstream decision |
| Same design, different product geometry | Same design ID, new profile ID | Copy approved inputs as drafts; adapt art/layout for target ratio | Target art, pet runtime, layout, assembly, print, and release evidence |
| New category/franchise concept | New design ID and profile | Shortlist concepts, record explicit base-to-variant delta, scratch-test | Full target evidence; the parent approval is context only |

Do not infer approval from a local image, an S3 object, a source prompt, or a
visually similar sibling. Only the target's immutable review and graduation
records support its bundle.

## Develop a brand-new design

Use this sequence when there is no approved target bundle:

1. Put the reference image and provider-specific art/pet prompts under
   `work/design-inputs/<design-id>/`.
2. Initialize and source the shared config and the design/profile config.
3. Scratch-test art until the fixed-versus-personalized content split is
   correct. Scratch-test the pet prompt against one or two representative pets
   at the intended production model and quality.
4. Create immutable art and pet experiments. Run smoke evidence first, then the
   reviewed release fixture selection.
5. Record art and pet decisions from their comparison artifacts.
6. Generate ranked deterministic layout proposals, inspect the fixture/name
   compositions, and accept or manually refine one layout.
7. Inspect composed release QA and the print finalist.
8. Graduate, build the immutable bundle/release, review the upload plan, publish
   to S3, and let FE independently activate the selected revision.

The gallery/operator workflow covers the common graduated-design path. Use the
CLI guide for initial scratch work, unusual recovery, or a missing GUI action.
Never promote scratch pixels by copying them into an immutable attempt.

## Improve an existing design

Keep the design/profile identity and create successor experiments. Do not edit
an immutable attempt, review decision, graduation, bundle, or published object.

Change the smallest independent stage that addresses the feedback:

| Change | Preserve | Re-run before a new bundle revision |
| --- | --- | --- |
| Art prompt/model or fixed copy | Existing pet candidates may remain diagnostic inputs | Art decision, layout, composed QA, print, graduation |
| Pet prompt/model/reference order | Selected art | Pet smoke/release evidence, layout representative binding, composed QA, print, graduation |
| Layout/font/placement only | Selected art and pet bytes | Layout decision, composed QA, print, graduation |
| Print/upscale implementation only | Approved preview artifacts if their hashes and geometry remain valid | Print candidate and graduation; follow validator output |
| FE mapping/activation only | Entire immutable bundle | No authoring regeneration; FE changes its selected revision/mapping |

Use **Redo/Improve** in the operator workflow when available. The tool retires
the active downstream decision and preserves prior immutable evidence for
traceability. When using the CLI, follow the successor and invalidation rules
in operations-guide section 10. Do not make old evidence appear to evaluate new
bytes.

## Scale an approved design to another product profile

Different aspect ratios are the default case. The target gets a new
`product-profile-id`, independent authoring root, target layout, print canvas,
graduation, and bundle. The source bundle remains unchanged.

### Different-ratio target

1. Verify the exact production-approved source revision and record the target
   profile.
2. Copy the approved source prompt/reference inputs into target-owned working
   inputs. Edit them only as needed to state the target aspect ratio, safe
   zones, and intended composition.
3. Generate target preview art rather than stretching or cropping the source
   art. Preserve the visual language, not source pixels at the wrong geometry.
4. Re-run the pet release fixtures. The target reference and effective request
   can change even when prompt wording is similar.
5. Generate target layout proposals from target art, representative pet
   attempts, and the target reference. Review prominence and collisions across
   release fixtures.
6. Run composed QA, print preparation, graduation, bundling, and publication
   for the target product.

### Exact-ratio target

A deterministic copy/resize of approved art may be used when the target ratio
and composition are genuinely equivalent. Record truthful local-derivation
provenance and still create target-owned files. Recompute layout geometry and
inspect preview and print results; equivalent ratios do not prove equivalent
safe zones, physical scale, or vendor output.

For MVP simplicity, the current manual tools remain authoritative. The
[authoring automation design](AUTHORING_AUTOMATION_DESIGN.md#9-scenario-b-another-product-profile)
defines the planned config-driven optimization; do not use its proposed
commands until CLI help and the operations guide expose them.

## Scale a design category or franchise

A category expansion reuses a reviewed visual grammar, not a mutable parent
asset. Use a small concept pool and select before paying for complete evidence.

### Prepare and shortlist

For each candidate, create a readable `<concept>-<bottom-line>` design ID and a
folder containing:

```text
reference-design.png
art-template-gpt.md
pet-transform-gpt.md
```

Keep concept drafts in `work/design-inputs/Test Design Pool/`. Use the reviewer
gallery to collect `Graduate`, `Improve`, or `Abandon` recommendations. The
operator moves selected concepts to `Graduation Pool`; this is permission to
begin bundle development, not evidence that a bundle is approved.

Record an explicit variant delta:

- **preserve:** category typography, illustration treatment, hierarchy, and
  layer-ownership rules;
- **change:** exact copy, motif, prop, pose, occasion, and target reference;
- **forbid:** parent pet identity, stale copy/props, opaque background, or fixed
  artwork leaking into the personalized layer.

Use an approved prompt as a drafting baseline when it reduces ambiguity, but
review the complete resulting prompt. A changed finished-design reference
changes the runtime request and requires target pet QA.

### Develop each selected variant

Process each chosen concept as an independent new design:

1. copy its three inputs into `work/design-inputs/<design-id>/`;
2. initialize a design/profile config;
3. scratch-test the fixed-art split and personalized pet behavior;
4. run durable art and pet evidence only after scratch acceptance;
5. review layout, composition, print, graduation, bundle, and publication;
6. retire losing experiments without deleting source pools, selected evidence,
   exchange releases, or S3 artifacts.

Batch actions may queue designs at the same workflow stage, but every design
keeps independent experiments, decisions, warnings, and publication results.
Never hide failed fixtures by retrying until only successful images remain.

### Patrol Franchise example

Patrol designs use an arched black display headline, a small corner motif, a
dominant hand-inked personalized pet/prop/name cutout, a humorous bottom line,
and two footer paw prints. The current variants generally use
`embedded-in-pet`: the art layer owns headline/motif/bottom line/footer paws;
the transformed-pet layer owns the customer pet, center props, local marks, and
artistic name.

For Patrol candidates:

- use the target candidate as the primary reference; add a supporting reference
  only for a specific unresolved style issue;
- generate the art layer with no pet, center prop, or name;
- generate the pet layer with no headline, corner motif, bottom line, or footer
  paws and with true transparency outside the cutout;
- test short and maximum-length embedded names as separately budgeted image
  calls;
- treat a changed pose or prop footprint as a new layout, not a parent-layout
  copy;
- start with a small collection batch rather than benchmarking every slogan.

These rules are a reusable case study, not a second Patrol-specific contract.
The FE receives the same ordinary self-contained bundle as every other design.

## Artifact ownership and cleanup

```text
work/design-inputs/<design-id>/
work/configs/<design-id>--<profile-id>--vNN.env
work/authoring/<design-id>/<profile-id>/
  experiments/{art,pet,layout}/
  reviews/{art,pet,layout,assembly}/
  print-candidates/
  graduations/
work/exchange/bundles/<design-id>--<profile-id>/<revision>/
work/exchange/releases/<release-id>/catalog.json
```

Local pools, experiment state, review votes, workflow reports, and base-design
relationships are private operator inputs. FE consumes only the validated
bundle and release catalog described by the
[production contract](MVP_PRODUCTION_BUNDLE_CATALOG_DESIGN.md). FE owns product
mapping, activation, rollback, customer interaction, and order behavior.

Use dry-run cleanup and remove only failed/discarded local experiments that are
not selected or otherwise protected. Published revisions are immutable;
retirement is a catalog/application decision, not deletion of production
objects.

## Planned workflow optimization

The [authoring automation design](AUTHORING_AUTOMATION_DESIGN.md) proposes one
config-driven coordinator for post-scratch new designs, profile adaptation,
and category variants. It keeps the same human checkpoints and public bundle
contract. Until each milestone is implemented, use the current operator GUI
and canonical CLI commands above. Automation must reduce path/configuration
mistakes; it must not automatically approve aesthetics or weaken fixture,
layout, print, or publication review.

