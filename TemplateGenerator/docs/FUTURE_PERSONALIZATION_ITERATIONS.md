# Personalized Product Toolset — Future Iterations

Status: Deferred roadmap  
Depends on: Successful profile-driven preview-to-print POC validation
Last updated: 2026-08-31

## 1. Purpose

This document records capabilities intentionally excluded from the
profile-driven proof of concept described in
[`OFFLINE_PERSONALIZATION_TOOLSET_DESIGN.md`](OFFLINE_PERSONALIZATION_TOOLSET_DESIGN.md).

These items should not be implemented merely because they appear here. Each
iteration starts only after the preceding workflow has demonstrated a real need
and the team has selected measurable acceptance criteria.

## 2. Graduation criteria from the current MVP

Consider the MVP validated when several representative runs show that:

- Background-only `art.png` can be generated at acceptable quality.
- The layout editor can reproduce useful pet and name placement.
- Different pet shapes remain correctly aligned after alpha trimming and fit.
- The same stored `art.png` and `layout.json` work with more than one user pet.
- Prompt or layout changes can be tested quickly.
- The result is promising enough to justify print-quality and online-production
  investment.
- Product-profile geometry and print-manifest integrity work across more than
  one print canvas.
- Upscaled candidates retain acceptable detail at the actual scale factors
  required by representative products.

Before starting the next iteration, record the actual MVP limitations. Do not
design future schema fields from hypothetical requirements alone.

## 3. Suggested iteration sequence

### Iteration 2 — Template quality and authoring efficiency

Goal: improve the offline template workflow while it remains low resolution.

Potential capabilities:

- Optional layout presets for repeated design structures.
- More pet and name slots only when required by tested products.
- Additional deterministic properties such as text stroke, tracking, shadows,
  opacity, rotation, and configurable layer order.
- Curved or path-based text.
- Evaluate AI-generated pet-name PNGs as an alternative to deterministic font
  rendering, including spelling reliability, isolated lettering extraction,
  transparent-background quality, long-name handling, and approval criteria.
  No implementation remains in the MVP repository; reintroduce it only after
  the font-rendered workflow is validated and there is evidence it is needed.
- Foreground masks and controlled occlusion.
- Multiple representative-pet fixtures and name-length test cases.
- A pet-prompt evaluation set spanning breeds, coat lengths, colors, markings,
  head shapes, source poses, and photo quality.
- Automated identity/style/pose scoring, prompt-derivation retries, and
  side-by-side analyzer comparison once human MVP results justify them.
- Optional automatic suggestions for pet and text regions.
- OCR-assisted text-region suggestions.
- Reference cropping and perspective-rectification assistance.
- Prompt history and side-by-side result comparison.
- A lightweight accepted-template snapshot command.

Keep automatic detection advisory. Operators should remain able to override all
suggested geometry.

#### Deferred issue — Charlie Well Trained pet shadow

The `charlie-well-trained` reference design includes a pet shadow that the
current automated art-template workflow does not reproduce reliably. Supporting
this effect requires more than generating a shadow in the background artwork:

- The shadow position, dimensions, shape, softness, opacity, and color must
  match the reference design.
- The shadow must be positioned relative to the final transformed pet rather
  than treated as a fixed part of `art.png`.
- Changes to the transformed pet's crop, scale, or placement must move or adjust
  the shadow consistently so the two elements remain visually connected.
- The compositor and layout schema may need an explicit shadow layer or another
  pet-relative effect representation, with deterministic layer ordering.

This coupling adds complexity to template extraction, layout authoring, and
preview composition. Shadow extraction and pet-relative shadow placement are
therefore excluded from the current MVP. For MVP validation, the Charlie
example may omit or approximate the shadow; it must not be treated as evidence
that automated shadow handling is supported. Revisit this issue in Iteration 2
after the basic art-template, transformed-pet, and layout workflow is validated.

### Iteration 3 — Vendor-qualified print assets

Goal: graduate exact-size print candidates into vendor-qualified deliverables
without changing the approved composition.

Implemented POC baseline (2026-08-31): named profiles define preview and print
canvases plus print-contract metadata; `pawmarvel-upscale-template` prepares
reusable art/layout once and `pawmarvel-upscale-pet` prepares each customer
cutout against that immutable geometry, with exact-canvas scaling,
source-alpha preservation, and separate checksum manifests;
the renderer verifies the bundle/profile and creates an exact-size final-review
manifest. Formal preview approval is deliberately deferred. This still produces
print *candidates*, not vendor-qualified deliverables.

Implemented two-resolution consumer package:

```text
art.png                  # low-resolution web asset
layout.json              # low-resolution web geometry
print/art.png            # high-resolution print asset
layout-print.json        # high-resolution print geometry
fonts/
```

The publisher validates that both art files share an exact aspect ratio and
that print placement/text geometry is mechanically scaled from the preview
layout. Profile metadata and vendor qualification remain outside the portable
consumer bundle for this MVP.

Implemented geometry rules:

- Define preview and print canvas dimensions in one named profile.
- Require exact matching aspect ratios and uniform scaling.
- Derive print geometry mechanically from preview geometry.
- Scale rectangle edges to avoid accumulated rounding error.
- Scale font sizes while keeping rotation, alignment, color, and other
  dimensionless values unchanged.
- Store `layout-print.json` and prevent independent print-layout tuning.

Future graduation work:

- Qualify one or more upscaling backends per style and scale-factor range.
- Define measurable detail, edge, and alpha acceptance thresholds.
- Confirm how strokes, tracking, masks, and future effect layers scale.
- Confirm each product/vendor profile and lock its revision.

Example uniform derivation:

```text
scale_x = print_width / preview_width
scale_y = print_height / preview_height

require scale_x == scale_y

print_left  = round(preview_left * scale_x)
print_right = round((preview_left + preview_width) * scale_x)
print_width = print_right - print_left
```

#### Print specification contract

Before calling an asset print-ready, define:

- Physical print width and height.
- Required pixel dimensions and DPI.
- Printable area, bleed, and safe-area bounds.
- Required color space and ICC profile behavior.
- Transparency and background requirements.
- Accepted output formats and compression settings.
- Vendor-specific file limits.

#### Upscaling

The POC has replaceable deterministic Lanczos and Bria backends. Evaluate them
against real printed samples before selecting a production default or adding a
heavyweight local super-resolution model.

An upscaler must preserve:

- Exact output dimensions and aspect ratio.
- Canvas origin.
- Alpha silhouette.
- Subject position and crop.
- Input-to-output geometry.

It must not independently regenerate or reinterpret approved artwork.

For alpha images, consider enhancing RGB separately while scaling the approved
alpha mask deterministically, then recombining them.

### Iteration 4 — Cross-resolution validation

Goal: prove that the print composition preserves the approved preview layout.

Potential capabilities:

- Emit `render-metadata.json` with resolved boxes, anchors, visible alpha bounds,
  text bounds, and baselines.
- Compare preview and scaled print metadata numerically.
- Downscale print renders for human side-by-side inspection.
- Add debug overlays for both profiles.
- Define numeric tolerances for scaled positions and rounding.
- Detect crop, padding, translation, or alpha loss introduced by upscaling.
- Add golden tests using bundled fonts and pinned image-library versions.

Do not attempt to recover boxes or baselines from a flattened raster when the
renderer can report them directly.

### Iteration 5 — Online customer preview and order overrides

Goal: expose the validated renderer through a low-latency customer workflow.

Potential flow:

1. Accept the customer pet and name.
2. Generate a transformed preview pet.
3. Render with the approved preview template.
4. Allow a bounded set of layout adjustments.
5. Save adjustments as an order-scoped override instead of modifying the
   product template.
6. Record customer approval.

Potential override fields:

```json
{
  "pet": {
    "translate_x_px": 12,
    "translate_y_px": -8,
    "scale": 1.05,
    "rotation_delta_degrees": 0
  },
  "name": {
    "translate_x_px": 0,
    "translate_y_px": 4
  }
}
```

The server should whitelist and range-check overrides. If print profiles exist,
derive the print override with the same shared geometry function used for the
product layout.

### Iteration 6 — Approved high-resolution order rendering

Goal: create final high-resolution personalized artwork after customer
approval.

Recommended flow:

1. Bind customer approval to the selected product revision, transformed preview
   pet, pet name, layout override, and preview result.
2. Upscale the exact approved transformed pet rather than independently
   generating a different high-resolution pet.
3. Preserve the approved pet geometry and alpha silhouette.
4. Derive the print override from the approved preview override.
5. Render high-resolution text from the bundled font.
6. Compose high-resolution art, pet, and text from their separate components.
7. Do not upscale the flattened customer preview as the final print asset.
8. Run cross-resolution geometry validation.

An independently generated high-resolution pet may change pose, markings,
silhouette, crop, or style and therefore must not silently replace the pet the
customer approved.

### Iteration 7 — Product and order lifecycle controls

Goal: make approved artifacts traceable and resistant to accidental mutation.

This iteration owns the deferred preview-approval feature. Reintroduce an
approval command or authenticated action only with an explicit revision model;
it may bind a pipeline `run.json` when available, but must also define how
manually assembled artifacts are captured into an immutable revision before
approval.

Potential product states:

```text
draft -> preview_approved -> print_approved -> published
```

Potential order states:

```text
draft -> customer_approved -> print_rendered -> operations_approved
      -> sent_to_vendor
```

Potential controls:

- Immutable product revision directories.
- Package and order manifests.
- SHA-256 hashes for approved assets and configs.
- Actor and timestamp records for state transitions.
- Approval commands or authenticated API actions.
- Rejection and controlled revision paths.
- Verification that vendor release references the operations-approved print
  hash.
- Rollback by switching product revision instead of editing approved files.

These controls should be introduced together with a real production owner and
operational workflow, not during the offline POC.

### Iteration 8 — Scalable service infrastructure

Goal: support concurrent customer requests and operational reliability.

Potential capabilities:

- Online API around the shared renderer.
- Asynchronous job queue for generation and print preparation.
- Object storage for product and order assets.
- Database for product revisions, jobs, and approvals.
- Retry and idempotency policy.
- Authentication and authorization.
- API-key secret management.
- Observability, structured logs, metrics, and alerts.
- Resource limits and abuse protection.
- Cost tracking for image-generation requests.
- Automated deployment and rollback.
- Operations review portal.
- Vendor integration.

Retain the deterministic renderer as a reusable library. Avoid duplicating
placement logic across offline and online code paths.

## 4. Future schema considerations

Potential additions should be driven by demonstrated product needs:

- Multiple named image slots.
- Multiple text fields.
- Optional slots and conditional layers.
- `contain` and `cover` fit modes.
- Named anchors beyond `bottom_center`.
- Rotation origin.
- Clipping and foreground masks.
- Text tracking, stroke, shadow, case transformation, and multiline behavior.
- Curved text paths.
- Safe areas and print regions.
- Multiple output profiles.
- Product-defined customer override bounds.
- Schema migration and backward compatibility.

Prefer a small explicit schema over a generic graphics document model until
the product set proves that broader abstraction is necessary.

## 5. Future quality and testing work

- Test short, long, narrow, and wide names.
- Test pets with wide ears, tall bodies, long tails, and unusual alpha padding.
- Validate multiple image formats and color modes.
- Pin fonts and rendering-library versions when exact reproducibility matters.
- Prefer pixel-difference thresholds over raw PNG byte equality across
  environments.
- Add render metadata for structural assertions.
- Test layout-profile derivation and rounding.
- Test order-override derivation.
- Test approval-state transitions and unauthorized transitions.
- Test upscaler geometry and alpha preservation.
- Test failed and retried asynchronous jobs.
- Keep OpenAI API calls out of default unit tests; use explicit live integration
  tests.

## 6. Security, legal, and operational concerns

Address these before external production use:

- Confirm rights to reference designs, fonts, trademarks, and generated assets.
- Keep API keys and customer source images out of reusable product packages.
- Define retention and deletion rules for customer photos.
- Validate uploaded image type, size, and decode behavior.
- Isolate untrusted uploads from executable paths.
- Define who may approve products, print files, and vendor release.
- Record model, prompt, input roles, and generation settings where auditability
  is required.
- Confirm vendor privacy and data-handling requirements.

## 7. Risks to reassess after MVP

| Risk | Future response |
| --- | --- |
| AI-generated background does not reproduce reusable artwork reliably | Improve prompts, add controlled editing, or introduce a human art-cleanup step |
| Manual layout becomes the authoring bottleneck | Add automatic region suggestions while preserving manual override |
| Low-resolution transformed pets do not upscale adequately | Evaluate dedicated super-resolution or a controlled high-resolution generation workflow with explicit reapproval |
| Preview and print geometry diverge | Use shared derivation, render metadata, and cross-resolution tests |
| Font rendering differs by environment | Bundle fonts and pin rendering dependencies |
| Customer overrides break composition | Use product-defined bounds and server-side validation |
| Reference shadows do not stay aligned with transformed pets | Add an explicit pet-relative shadow layer and validate its geometry and compositing order |
| Approved files are changed accidentally | POC hashes detect mutation; add immutable revision storage and signing for production |
| Operational review does not scale | Add an approval portal and role-based workflow |
| Vendor specifications differ by product | Confirm and revision-lock the existing explicit profiles per vendor/product |
| Reference designs create copyright or trademark exposure | Require design-rights review before publication |

## 8. Decision rule

Implement the smallest next iteration that addresses a limitation observed in
real POC runs. Vendor qualification, cross-resolution quality scoring,
lifecycle, and infrastructure work should remain deferred until the reusable
artifacts and personalized print candidates demonstrate sufficient product
value.
