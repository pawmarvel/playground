# Personalized Product Toolset — Future Iterations

Status: Deferred roadmap  
Depends on: Successful low-resolution MVP validation  
Last updated: 2026-08-24

## 1. Purpose

This document records capabilities intentionally excluded from the
low-resolution proof of concept described in
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

### Iteration 3 — High-resolution and print-ready assets

Goal: derive print assets from an approved low-resolution template without
changing its composition.

Potential package:

```text
art-preview.png
layout-preview.json
art-print.png
layout-print.json
font files
profile metadata
```

Required design work:

- Define preview and print canvas dimensions.
- Require matching aspect ratios or define an explicit nonuniform mapping.
- Prefer a uniform integer scale when possible.
- Derive print geometry mechanically from preview geometry.
- Scale box edges to avoid accumulated rounding error.
- Scale font sizes, strokes, tracking, padding, masks, and other pixel values.
- Keep dimensionless values such as rotation, opacity, and relative scale
  unchanged.
- Store the materialized print layout if runtime derivation is undesirable.
- Prevent independent manual tuning of preview and print layouts unless the
  product explicitly supports separate compositions.

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

Start with a replaceable upscaler interface. Evaluate simple Lanczos before
adding a heavyweight super-resolution model.

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
| Approved files are changed accidentally | Add immutable revisions and content hashes |
| Operational review does not scale | Add an approval portal and role-based workflow |
| Vendor specifications differ by product | Add explicit product/vendor print profiles |
| Reference designs create copyright or trademark exposure | Require design-rights review before publication |

## 8. Decision rule

Implement the smallest next iteration that addresses a limitation observed in
real MVP runs. High-resolution, print, lifecycle, and infrastructure work should
remain deferred until the low-resolution artifacts and personalization result
have demonstrated sufficient product value.
