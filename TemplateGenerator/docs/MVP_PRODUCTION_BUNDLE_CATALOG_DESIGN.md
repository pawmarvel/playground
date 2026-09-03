# Template Bundle Catalog — MVP Production Graduation Design

## 1. Purpose

This document proposes the smallest production-ready boundary between the
offline PawMarvel Template Generator and the application that creates customer
previews and print designs. It is intended for review and alignment between the
template-authoring and FE/application engineers.

The detailed command behavior remains in
`OFFLINE_PERSONALIZATION_TOOLSET_DESIGN.md` and `MVP_OPERATIONS_GUIDE.md`. This
document defines the production artifact, ownership boundary, handoff workflow,
and narrow code changes needed to trial approximately ten design-product
entries in 3–5 days.

`TemplateBundleContract.pdf` shared by FE engineer is useful evidence from an earlier working
integration, not the final contract. It describes a push/import workflow in
which the application imports a directory as a draft and owns publication. The
first version of this proposal instead assumed that the application would pull
a generator-owned runtime catalog. Those are materially different
architectures. This revision makes the choice explicit and recommends the
push/import boundary for the MVP.

FE development is still in progress, so this design does not depend on a
specific FE framework or repository. The agreed catalog, bundle, asset, model,
and rendering semantics are the boundary.

## 2. MVP outcome and non-goals

At the end of the MVP:

- an operator can iteratively author a design for one or more product profiles;
- each approved design-product pair is emitted as an immutable, versioned
  bundle;
- a small release catalog can hand several bundles to the application in one
  import;
- the application can validate a bundle without knowing generator internals;
- application-owned draft, activation, and rollback do not require the
  generator to become a production control plane; and
- production bundles and authoring work remain outside Git.

The MVP does not include a database, template-authoring service, crowdsourced
authoring, workflow engine, automated visual scoring, multi-region publishing,
asset deduplication, or a general-purpose product information system. Ten
entries are small enough for reviewed JSON files and a semi-manual release.

## 3. Delivery and activation architecture

### 3.1 Recommended MVP: push/import

```text
TemplateGenerator Git repository
  code + schemas + sample fixtures
                 |
                 v
private authoring workspace
  references + prompts + mutable candidates + QA
                 |
         validate and package
                 v
immutable transfer release in object storage
  release catalog + versioned bundle directories
                 |
            FE imports
                 v
application draft -> application review -> application activation/rollback
```

The generator is the system of record for the bytes and identity of an offline
bundle revision. The application is the system of record for whether an
imported revision is draft, active, disabled, or rolled back. This matches the
earlier PDF's responsibility boundary and avoids building a second production
activation system in the generator.

The release catalog is a transfer index, not the storefront's live catalog. FE
may import an HTTPS/object-store release or an uploaded archive. An HTTP release
declares its exchange-root URL; an archive has `catalog.json` and `bundles/` at
its root. Once imported, the application may copy assets into its own storage
or retain an immutable reference to the transfer location.

### 3.2 Alternative: runtime pull catalog

If the kickoff explicitly chooses direct runtime consumption, the immutable
bundle format remains valid. A later extension may add immutable runtime
catalog releases plus a mutable channel pointer. Do not implement both
activation models during the MVP. The pull alternative requires FE cache,
availability, rollback, and disabled-template behavior that the import model
keeps inside the application.

## 4. Ownership boundary

### 4.1 Offline Template Generator owns

- authoring and validating preview `art.png` and profile-specific
  `print/art.png`;
- authoring preview placement and mechanically deriving print placement;
- snapshotting the product profile in every bundle revision;
- the selected redistributable OFL font and its license;
- the pet-transformation prompt and ordered runtime reference assets;
- schema validation and bundle asset dimensions, media types, sizes, and
  SHA-256 hashes;
- a representative transformed pet and geometry-focused conformance fixture;
- assigning the immutable `bundle_revision`; and
- producing an immutable bundle directory and release catalog for import.

The generator does not own customer uploads, customer approval, order state,
application activation, rollback, runtime API credentials, or vendor delivery.

### 4.2 FE/application owns

- importing and validating a release catalog and its bundles;
- mapping a commerce product/variant to `template_id`;
- draft review, activation, disabling, and rollback;
- customer-pet upload validation, storage, retention, and privacy;
- making image-model calls from a trusted server environment, never exposing an
  API key in browser code;
- sending model inputs in the bundle-defined order;
- rendering the customer name and transformed pet with declared semantics;
- storing approval and binding it to an exact bundle revision;
- upscaling only the approved customer transformed-pet layer;
- rendering the print design from the same bundle revision used for approval;
- vendor-specific validation and delivery; and
- runtime observability, retries, failure handling, and cost controls.

“FE consumer” means the complete application-owned path. Model calls, private
bundle fetching, and print rendering should run in its trusted backend or job
layer rather than in browser code.

### 4.3 Shared conformance responsibility

Both teams own a small contract test. The generator validates the directory and
publishes a fixture. The application proves it can reject unsupported schemas,
verify identity and required assets, and reproduce the fixture's geometry.

For the MVP, automated conformance is deliberately limited to canvas size,
boxes, alpha trimming, containment, anchor points, rotation, layer order, name
containment, and transparency. One golden preview is reviewed visually. Do not
attempt a cross-renderer pixel-difference threshold in the 3–5 day window;
Pillow and browser text metrics can consume the schedule without improving the
artifact contract.

## 5. Identity and version fields

| Field | Meaning | Change rule |
| --- | --- | --- |
| `schema_version` | Machine-readable shape of one JSON document | Increment that document only for a breaking change |
| `template_id` | Stable design-product identity | `<design_id>--<product_profile_id>` |
| `bundle_revision` | Generator-assigned immutable revision of one template | Increment for every published asset, prompt, font, layout, profile, or runtime-config change |
| `release_id` | Immutable batch handed to the importer | Create for each delivery batch |

Use positive integer bundle revisions. Semantic versioning adds process without
useful precision here; compatibility is expressed by `schema_version`.

```text
life-is-good--blanket-king-9375x12375 / revision 1
life-is-good--blanket-king-9375x12375 / revision 2
life-is-good--blanket-twin-full-7875x9375 / revision 1
```

The generator revision is authoritative. The importer records it verbatim and
must not assign a second field also called “template version.” It may have an
internal `import_id` or `deployment_id`, but the mapping to `template_id` plus
`bundle_revision` is mandatory. Orders persist that pair and, if present, the
application deployment ID. This prevents two drifting version counters.

Use UTC `YYYY-MM-DD.NNN` release IDs with a fixed-width, zero-padded daily
sequence, for example `2026-09-02.001`. They are lexicographically increasing
and informational only; consumers never use ordering to resolve a bundle.

Never overwrite a delivered revision. A visual correction that preserves the
same concept creates a new bundle revision. A materially different creative
concept receives a new `design_id`. A print-dimension or product-meaning change
receives a new `product_profile_id`; prior profiles and bundles remain
immutable.

The logical entity model is intentionally small:

```text
Design 1 --- n TemplateBundleRevision n --- 1 ProductProfile
                         |
                         n
             ReleaseCatalog lists one or more
             immutable bundle revisions
```

## 6. Storage and path contract

### 6.1 Git repository

Keep only these classes of files in `TemplateGenerator` Git:

```text
src/                         generator implementation
schemas/                     JSON Schemas for release, bundle, layout, profile
profiles/                    reviewed small product-profile source files
assets/fonts/                curated OFL authoring catalog
examples/                    synthetic/licensed contract fixtures only
docs/                        design and operations documentation
tests/                       unit and contract tests
```

Do not add production reference screenshots, production prompts, generated art,
print assets, mutable work directories, customer pets, or production release
payloads to Git. The current two example bundles remain non-production fixtures.

### 6.2 Private authoring storage

Use a private object-store prefix or a backed-up local/shared workspace:

```text
authoring/
  <design-id>/
    sources/
      reference-design.png
      reference-designs/reference-design-0002.png
      art-template-gpt.md
      art-template-gemini.md
      pet-transform-gpt.md
      pet-transform-gemini.md
    <product-profile-id>/
      candidates/<candidate-id>/
        template/             mutable pipeline output
        runs/                 representative test runs
        review-notes.md
```

Candidate IDs may be timestamps or short operator labels. They are provenance,
not production revisions. Failed experiments may be removed under a retention
policy; delivered revisions may not.

### 6.3 Immutable transfer storage

Use an S3-compatible object store or the equivalent already operated by the
team. The MVP does not require a new storage vendor or cloud SDK.

```text
exchange/
  bundles/
    <template-id>/
      v000001/
        bundle.json
        product-profile.json
        art.png
        layout.json
        print/art.png
        layout-print.json
        reference-design.png
        reference-designs/reference-design-0002.png
        runtime-references/01-exemplar.png
        runtime-references/02-finished-design.png
        pet-transform-{gpt|gemini}.md
        art-template-{gpt|gemini}.md
        fonts/<font>.ttf
        fonts/OFL.txt
        qa/transformed-pet.png
        qa/golden-preview.png
        qa/fixture.json
  releases/
    <release-id>/catalog.json
```

Bundles remain self-contained even when that duplicates fonts or art. At this
scale duplication is easier to audit and roll back than shared-asset graphs.

For the MVP, the application backend fetches all release and bundle artifacts.
It may expose only browser-safe art/font URLs to its UI after import. This avoids
an ambiguous mix of public and private paths; per-asset visibility and
direct-CDN browser consumption are deferred.

## 7. Proposed release catalog contract

The catalog is a small batch-delivery document. It does not say which templates
are active in production and does not replace application draft/publish state.

```json
{
  "schema_version": 2,
  "release_id": "2026-09-02.001",
  "generated_at": "2026-09-02T22:00:00Z",
  "asset_base_url": "https://assets.example.invalid/exchange/",
  "bundles": [
    {
      "template_id": "life-is-good--blanket-king-9375x12375",
      "design_id": "life-is-good",
      "product_profile_id": "blanket-king-9375x12375",
      "bundle_revision": 2,
      "bundle_manifest": "bundles/life-is-good--blanket-king-9375x12375/v000002/bundle.json",
      "manifest_sha256": "<bundle-json-sha256>",
      "display": {
        "name": "Life Is Good — King Blanket"
      }
    }
  ]
}
```

Rules:

- `asset_base_url` is required for HTTP/object-store imports and omitted for an
  archive; archive paths resolve from the archive root containing both
  `catalog.json` and `bundles/`;
- paths use `/`, cannot contain `..`, and resolve from `asset_base_url` or the
  archive root;
- each `(template_id, bundle_revision)` appears at most once;
- identity fields must agree with the referenced manifest;
- the same `design_id` may appear with several product profiles;
- import is idempotent: the same identity and `manifest_sha256` is a no-op;
- the importer rejects the same identity with a different `manifest_sha256`;
- the importer hashes `bundle.json` before trusting its per-asset hashes; and
- unknown optional fields are ignored, while an unsupported `schema_version`
  is rejected.

This closes the integrity chain as trusted catalog entry -> manifest digest ->
asset digests. It detects transfer corruption or substitution relative to the
catalog. It does not authenticate a maliciously replaced catalog; HTTPS,
object-store IAM, and restricted release-write credentials establish catalog
authenticity for the MVP. Signed catalogs are deferred.

This production release catalog starts at schema version 2 only to avoid
confusion with the current generator's incompatible local POC `catalog.json`,
which already calls itself version 1. No FE migration is implied because the
POC catalog has not been adopted as this contract.

If the architecture later changes to runtime pull, define a separate channel
contract then. Any channel must use a full URL or an exchange-root-relative
path without `..`; the invalid parent-relative example from the earlier draft
has been removed.

## 8. Proposed bundle manifest contract

### 8.1 Adoption decision

`bundle.json` is a proposed new contract, not something established by the
reference PDF. It is mandatory for this MVP proposal because FE development is
in progress, its implementation cost is small, and the dual preview/print-art
strategy cannot safely coexist with the PDF's filename-only, auto-upscale
behavior. It also removes filename inference from model routing, reference
order, dimensions, and integrity checks.

If FE declines the manifest, this proposal is not internally compatible as
written. The teams must reopen the print-art ownership decision and define one
complete conventional-filename contract; merely freezing current filenames is
not a viable fallback.

### 8.2 Manifest example

```json
{
  "schema_version": 1,
  "template_id": "life-is-good--blanket-king-9375x12375",
  "design_id": "life-is-good",
  "product_profile_id": "blanket-king-9375x12375",
  "bundle_revision": 2,
  "created_at": "2026-09-02T21:30:00Z",
  "runtime": {
    "provider": "openai",
    "model": "gpt-image-2",
    "prompt": "pet-transform-gpt.md",
    "reference_assets": [
      "runtime-references/01-exemplar.png",
      "runtime-references/02-finished-design.png"
    ],
    "output": {
      "format": "png",
      "background": "transparent",
      "width": 816,
      "height": 816
    }
  },
  "renderer": {
    "layer_order": ["art", "pet", "name"],
    "pet_alpha_trim": true,
    "pet_fit": "contain",
    "pet_anchor": "bottom-center",
    "rotation_origin": "visible-pet-center",
    "name_fit": "shrink-to-fit-ink-bounds"
  },
  "preview": {
    "art": "art.png",
    "layout": "layout.json",
    "canvas": {"width": 800, "height": 1056}
  },
  "print": {
    "art": "print/art.png",
    "layout": "layout-print.json",
    "canvas": {"width": 9375, "height": 12375}
  },
  "product_profile": "product-profile.json",
  "personalization": {
    "pet_count": 1,
    "name": {
      "required": true,
      "max_graphemes": 16,
      "normalization": "NFC",
      "counting": "uax29-extended-grapheme-clusters",
      "allowed_characters": "unicode-letters-marks-numbers-space-apostrophe-hyphen",
      "trim": true,
      "collapse_internal_whitespace": true,
      "case_transform": "preserve"
    }
  },
  "font": {
    "file": "fonts/Anton-Regular.ttf",
    "license": "fonts/OFL.txt"
  },
  "qa": {
    "transformed_pet": "qa/transformed-pet.png",
    "golden_preview": "qa/golden-preview.png",
    "fixture": "qa/fixture.json"
  },
  "assets": {
    "art.png": {
      "media_type": "image/png",
      "bytes": 123456,
      "sha256": "<sha256>",
      "width": 800,
      "height": 1056,
      "alpha_required": true
    }
  }
}
```

The real `assets` object contains every file except `bundle.json`. Each entry
has `media_type`, `bytes`, and `sha256`; raster images also include dimensions
and alpha requirements. Paths are unique, bundle-relative, and cannot escape
the directory. `product-profile.json` is included so later source-profile edits
cannot change an existing bundle's meaning.

Integrity verification occurs on application import or first immutable bundle
cache-fill, not once per customer personalization. Subsequent runtime reads use
the application's verified immutable copy/cache.

In bundle-v1, `allowed_characters` is a closed enum with exactly the value shown
above. It means Unicode general categories Letter, Mark, and Number plus ASCII
space, apostrophe, and hyphen. Digits are intentionally allowed for names such
as `R2`; control characters, emoji, and other punctuation are rejected. FE does
not implement a general character-class expression language.

### 8.3 Runtime provider and model scope

The offline generator now supports both OpenAI and Gemini. The recommended MVP
split is `gpt-image-2` for infrequent offline art-template authoring and
`gemini-3.1-flash-image` for latency-sensitive customer pet transformation,
subject to the same visual acceptance set. This CLI capability does not make
the old omitted-`layout.model` convention an acceptable production contract.

Before FE integration, `bundle.json.runtime` must identify `provider` and
`model` explicitly from an allowlist. Preview and print must pin the same bundle
revision and transformed-pet lineage. OpenAI provides a native transparent
background control. Gemini transparency is prompt-driven, uses native
aspect-ratio/resolution tiers, and requires output normalization and alpha QA;
those semantics must be named in runtime policy rather than hidden behind a
missing field.

Prompt filenames are provider-qualified:
`art-template-{gpt|gemini}.md` and
`pet-transform-{gpt|gemini}.md`. Each released bundle contains one selected
file for each role, and its manifest identifies both paths. The runtime pet
prompt category must match `runtime.provider`; importer validation rejects a
GPT/Gemini mismatch. Authoring may retain all variants privately for
side-by-side evaluation.

The earlier PDF latency figures remain historical measurements. Benchmark the
GA Gemini and OpenAI configurations on representative designs, recording p50,
p95, retry rate, alpha failures, identity/style acceptance, and cost before
freezing the production allowlist.

### 8.4 Runtime image order and reference decision

Customer pet first is an invariant, not configurable manifest data. The
application sends:

1. the customer pet; then
2. every path in `runtime.reference_assets`, in array order.

Prompts refer to the customer pet and reference assets by role, never “image 1”
or “image 2.” Bundle-v1 JSON Schema defines `reference_assets` with
`minItems: 1` and `maxItems: 4`. Four is the total after the customer pet,
including an exemplar if one is listed—not four additional references after an
exemplar. The limit is not instance-configurable.

The PDF reports a production-validated exemplar-first configuration, while the
current generator workflow was deliberately changed to use finished-design
references after reusable pet-only prompting proved unreliable. Neither result
should be discarded. Run a bounded A/B before freezing each initial design:

- exemplar first plus finished design as supporting reference; and
- finished design first without the exemplar.

Use two representative designs and three materially different pet inputs. The
template-authoring owner is the decider, with FE confirming that the exact
runtime request is reproducible. Transparency and recognizable identity are
pass/fail gates; among passing outputs, choose the ordering with more consistent
style and pose/crop across the three pets. A tie or ambiguous result keeps the
provisional exemplar-first default because it has the stronger reported
end-to-end evidence. The winning ordered list is stored per bundle revision, so
the consumer contract does not change if designs choose different lists.

If the exemplar is selected, publish it under a runtime-role path such as
`runtime-references/01-exemplar.png`. `qa/transformed-pet.png` may contain the
same bytes initially, but it remains a separate asset. Regenerating a QA fixture
then cannot silently change runtime styling; changing the runtime exemplar
always creates and reviews a new bundle revision.

### 8.5 Preview and print art strategy

The MVP bundle ships both preview `art.png` and generator-prepared,
profile-specific `print/art.png`, plus both layouts. FE must not upscale preview
art or apply another art-upscale policy. It reuses `print/art.png` for every
order under that bundle and upscales only the approved customer transformed
pet. This matches the current target workflow and avoids repeated cost and
quality drift.

This intentionally differs from the earlier PDF's single-master/FE-upscale
flow. FE must explicitly accept this rule before implementation. A future
single-master contract would require a different manifest strategy or schema
version.

### 8.6 Existing layout and renderer semantics

Keep layout schema version 1. Current pixel coordinates, RGBA name color,
rotation, font sizes, and relative font path remain. `layout-print.json` is the
mechanically scaled form. Capture implicit composition behavior in
`bundle.json.renderer` rather than refactoring layout now.

Renderer semantics are part of bundle schema version 1. There is no independent
`renderer.contract_version`; a future breaking renderer interpretation bumps
the bundle manifest's `schema_version`.

The manifest is authoritative for model routing and references. The publisher
validates that the two layouts agree with it. A future multi-slot layout may
introduce layout schema version 2 without changing catalog lookup.

### 8.7 QA fixture

`qa/fixture.json` identifies the representative transformed pet, sample name,
expected preview, and expected geometry. Automated consumer assertions are
geometry/semantics only. `qa/golden-preview.png` is a human comparison aid, not
a cross-renderer pixel oracle. If exact pixel equality later becomes a product
requirement, preview and print should use one shared server renderer.

QA files are not runtime inputs unless explicitly listed in
`runtime.reference_assets`.

### 8.8 Importer validation and failure behavior

The importer validates catalog identity, manifest identity, schemas, required
assets, dimensions, and hashes before creating a draft. It fails closed and
does not substitute another bundle revision, font, prompt, reference order, or
model. Reimporting the same `manifest_sha256` is idempotent; receiving a
different manifest digest for an existing `(template_id, bundle_revision)` is
an integrity error.

## 9. Catalog bundle authoring and handoff workflow

The current operations guide remains the procedural source. Production handoff
adds immutable packaging and import:

1. **Register sources.** Choose a stable design ID and store the primary and
   optional supporting references plus design-specific prompts privately.
2. **Select a product profile.** Confirm preview geometry, print dimensions,
   format, color space, bleed, safe area, and vendor status.
3. **Create a candidate workspace.** Work under
   `<design>/<profile>/candidates/<candidate-id>`, never in a delivered prefix.
4. **Generate preview art.** Run the profile-driven art generation step.
5. **Generate a representative transformed pet.** Test the proposed ordered
   reference strategy. If selected as a runtime exemplar, copy the approved
   bytes into `runtime-references/`; keep the QA fixture independently named.
6. **Author layout.** Select the OFL font, save `layout.json`, and render preview
   and debug output.
7. **Iterate selectively.** Use `--rerun-step art`, `pet`, and/or `layout`.
8. **Prepare print assets.** Upscale reusable template art once, upscale the QA
   pet, and inspect the print render at target resolution.
9. **Build a bundle candidate.** Allocate the next generator revision,
   snapshot the profile, generate `bundle.json`, and validate all hashes.
10. **Run contract QA.** Validate schemas and geometry; visually compare the
    golden preview; run one live pet transform for new/materially changed
    designs.
11. **Upload immutably.** Refuse an existing revision, preferably with a
    conditional object creation (`If-None-Match: *`) where supported. Upload
    assets to the unreferenced final prefix, verify them, and upload
    `bundle.json` last; compute its final SHA-256.
12. **Create a release catalog.** List the exact immutable bundles intended for
    one handoff with each `manifest_sha256`, then upload `catalog.json` after
    bundle validation.
13. **Import as draft.** FE imports idempotently and records the generator
    identities without minting a competing template version.
14. **Review and activate.** FE/application review, activation, disablement, and
    rollback remain application operations.

For another product variation, reuse design sources but create a distinct
bundle under the other product profile. Do not reuse coordinates across
profiles except through the validated mechanical scaling flow.

## 10. Minimal code changes

### P0 — required before FE integration

1. **Agree on push/import and mandatory manifest consumption.** Freeze the
   boundary before implementing importer-specific behavior.
2. **Add bundle manifest and profile snapshot.** Extend `pawmarvel-bundle` to
   write `bundle.json` and `product-profile.json`, including hashes, runtime
   role-specific references, output size, renderer semantics, and generator
   revision.
3. **Make delivered revisions immutable.** Add required
   `--bundle-revision`; publish to `<template-id>/vNNNNNN`; do not allow
   `--force` for release output. Retain overwrite only in explicit candidate
   mode.
4. **Add a release builder/validator.** A small `pawmarvel-catalog` command needs
   `validate` and `build-release`; catalog entries include the bundle-manifest
   digest. It does not need production activation, rollback, or a database.
5. **Check in shared JSON Schemas.** Add release-catalog-v2, bundle-v1,
   layout-v1, and product-profile-v1 schemas. FE uses the same fixtures to
   create matching types/tests.
6. **Publish a geometry fixture.** Include representative transformed pet,
   sample name, expected preview, and semantic expectations. Use human visual
   review rather than cross-renderer pixel thresholds.
7. **Add runtime policy.** Capture provider/model, exact reference order,
   provider-specific output normalization and alpha requirements, name
   validation, renderer behavior, and preview/print art ownership in the
   manifest. Enforce one-to-four runtime references in bundle-v1 schema.

### P1 — useful during the trial

8. **Add remote verification.** Compare object size and SHA-256 after upload,
   and verify content types. Use conditional creation if the store supports it.
9. **Add release summary.** Print design, profile, revision, preview size, print
   size, reference order, and validation state for the approximately ten
   entries.
10. **Record provenance.** Add generator version/commit, operator, source hashes,
    and notes without local absolute paths, secrets, or customer data.

### Keep unchanged

- Keep the Python package and focused CLIs.
- Keep the browser layout editor and Pillow reference renderer.
- Keep design-specific prompts and ordered references.
- Keep preview and print layouts as separate files.
- Keep the local 40-font OFL catalog and publish only the selected font.
- Keep deterministic upscale for flow testing and optional Bria experiments.
- Do not add a web authoring app, relational catalog database, or shared asset
  service.

### Simplification

Mark `examples/bundles/` as contract fixtures. Stop treating the current
replace-in-place `bundles/catalog.json` as a production deployment artifact.
Keep it temporarily as local/candidate behavior while migrating to immutable
revision directories and release catalogs.

## 11. Alignment checkpoints before coding

Resolve these in order during kickoff:

| Priority | Decision | Recommended MVP answer | Status |
| --- | --- | --- | --- |
| 1 | Who owns activation and rollback? | Application draft/publish flow; generator delivers immutable bundles and release catalog | Accept at kickoff |
| 2 | Which revision is authoritative? | Generator `bundle_revision`; importer records it verbatim and uses a differently named internal ID if needed | Accept at kickoff |
| 3 | Does FE adopt `bundle.json`? | Yes, mandatory for the dual-art contract; declining it reopens print-art architecture | Accept at kickoff |
| 4 | Which runtime references are sent? | Bounded A/B; provisional exemplar-first, four total; template owner decides and a tie keeps exemplar-first | Deferred to measured test |
| 5 | Which model paths are in MVP? | GPT Image 2 for offline art authoring and Gemini 3.1 Flash Image for online pet transformation; retain a measured OpenAI pet fallback | Product and FE acceptance required |
| 6 | Who creates print art? | Generator ships profile-specific high-resolution art; FE upscales only customer pet | Proposed decision |
| 7 | How are runtime and QA references separated? | Manifest paths are authoritative; runtime exemplar gets a `runtime-references/` path independent of QA | Proposed decision |
| 8 | How is renderer conformance tested? | Machine-check geometry/semantics plus one human golden review | Proposed decision |
| 9 | Where do model/print operations run? | Trusted application backend/job, not browser | Proposed decision |
| 10 | How are artifacts accessed? | Application server fetches all bundle assets during MVP | Proposed decision |
| 11 | How are products mapped? | Commerce variant stores `template_id`; orders pin generator revision and application deployment ID | Proposed decision |
| 12 | What are name rules? | NFC, UAX #29 extended graphemes, Unicode letters/marks/numbers plus space/apostrophe/hyphen, per-bundle maximum | Proposed decision |
| 13 | What makes a profile vendor-ready? | Explicitly confirm format, alpha/background, color profile, DPI, bleed, safe area, dimensions, and byte limit | Vendor/operations input required |

No bundle is production-ready until decisions 1–8 are recorded in the contract
README and represented by one shared fixture.

## 12. Three-to-five-day delivery plan

### Day 1 — freeze the handoff

- Resolve alignment items 1–3 and 5–8. The manifest schema supports an ordered
  list, so item 4 does not block contract freeze.
- Finalize one hand-built manifest, schemas, directory names, and storage URL.
- Have FE parse and validate the hand-built bundle as a draft.

### Day 2 — versioned bundle build

- Add bundle revision, manifest, profile snapshot, hashes, name policy, and
  geometry fixture to the publisher.
- Add immutable local output and validation.
- Upgrade the two examples into non-production contract fixtures.
- Run the reference-order A/B in parallel; the template owner applies the
  pass/fail and tie-break rules from section 8.4.

### Day 3 — release handoff

- Add release-catalog build/validation.
- Complete idempotent FE draft import for one bundle.
- Verify generator revision preservation and failure on conflicting bytes.

### Day 4 — storage and representative flow

- Wire upload to the team's existing storage CLI or document a reviewed manual
  upload.
- Deliver a multi-bundle development release.
- Exercise one customer pet from import through preview and print candidate.

### Day 5 — trial set and handoff

- Package the initial trial entries, up to approximately ten.
- Test one non-fixture pet for each distinct design style.
- Record operating steps, owners, known quality limits, activation, and
  rollback procedures.

If FE draft import is not ready, Day 3 still delivers validated immutable bundle
directories and a release catalog; FE activation can follow without changing
the artifact contract. Do not add a generator-owned channel system as a
temporary workaround.

## 13. MVP acceptance criteria

- Release catalog and every bundle validate against shared schemas.
- Every catalog entry hashes `bundle.json`, and every manifest hashes all other
  bundle assets.
- Every delivered bundle path is immutable.
- The same design works with multiple product profiles without collision.
- Reimport is idempotent and conflicting bytes under one revision are rejected.
- The importer preserves generator identity and creates no ambiguous second
  template version.
- The consumer sends the customer pet first and no more than four ordered
  reference assets afterward.
- Runtime exemplar and QA transformed-pet paths are separate roles even when
  their initial bytes are identical.
- Production bundles explicitly declare an allowlisted runtime provider/model
  and its output-normalization policy.
- FE uses bundled print art and does not independently upscale preview art.
- Preview and print use the same exact bundle revision and customer values.
- Automated conformance checks geometry; a human compares the golden preview.
- Production Git contains no customer images, authoring work, or production
  bundle payloads.
- Runtime secrets are absent from bundles, browser code, and logs.

## 14. Review verdict disposition

| Review finding | Disposition | Resolution |
| --- | --- | --- |
| Pull catalog versus push import | Accepted | Push/import is now the recommended MVP; pull/channel is a deferred alternative |
| Two revision counters | Accepted | Generator revision is authoritative; FE may use a separately named internal ID with an explicit mapping |
| `bundle.json` was presented as settled | Accepted, then superseded by second review | It became an explicit decision first; the dual-art analysis now makes it mandatory for this proposal |
| Finished-design-first contradicted tested exemplar | Accepted with bounded deferral | Provisional exemplar-first; run a two-design/three-pet A/B and persist the result per revision |
| Gemini semantics missing | Accepted, superseded by provider implementation | Generator supports Gemini; production bundles must declare provider/model and normalization explicitly, while legacy omitted-model behavior remains excluded |
| Print-art ownership conflict | Accepted | Generator supplies preview and profile-specific print art; FE does not upscale art |
| Reference filename mismatch | Accepted, then superseded by second review | Mandatory manifest paths resolve it; there is no filename-only fallback in this proposal |
| Channel path used `..` | Accepted | Channel removed from MVP; any future channel uses full or base-relative paths |
| Pixel tolerance could derail schedule | Accepted | Automated checks are geometry-only plus human golden comparison |
| Grapheme counting undefined | Accepted | NFC plus UAX #29 extended grapheme clusters and a defined character policy |
| Version table wording | Accepted | Retitled to identity and version fields |
| Configurable-looking input order | Accepted | Removed; customer pet first is an invariant |
| Catalog v2 rationale | Accepted | Clarified it avoids internal POC confusion and implies no FE migration |
| Object-store check/write race | Accepted proportionally | Conditional object creation is recommended; single-operator limitation remains acceptable |
| Hash cost sounded per-personalization | Accepted | Verification occurs on import/cache-fill |
| Public/private asset ambiguity | Accepted | All artifacts are server-fetched in the MVP |
| Plan depended on premature FE runtime work | Accepted | Plan now centers on validated bundle delivery and idempotent draft import |

### Second review

| Review finding | Disposition | Resolution |
| --- | --- | --- |
| Catalog did not hash `bundle.json` | Accepted as blocking | Added required `manifest_sha256`; idempotency is defined by the manifest digest |
| Filename fallback conflicts with dual art | Accepted as blocking | Manifest consumption is mandatory; declining it reopens the print-art architecture |
| Archive delivery conflicts with required base URL | Accepted | HTTP imports require `asset_base_url`; archives omit it and resolve from an archive root containing `catalog.json` and `bundles/` |
| GPT-only hides latency/cost consequence | Accepted, superseded by dual-provider direction | Recommend GPT Image 2 for offline art and Gemini 3.1 Flash Image for online pet transformation; benchmark quality, alpha failures, latency, retries, and cost before freezing the allowlist |
| Exemplar couples runtime and QA roles | Accepted | Runtime exemplar has a separate `runtime-references/` path; changing it requires a new reviewed revision |
| Reference cap is instance data | Accepted | Removed `max_reference_assets`; schema enforces `maxItems: 4` |
| Renderer has an undefined second version | Accepted | Removed `renderer.contract_version`; bundle schema version governs semantics |
| A/B overload and missing decision rule | Accepted | Runs on Days 1–2 in parallel; template owner decides, identity/transparency gate, tie keeps exemplar-first |
| Character-policy value was undefined and excluded digits silently | Accepted with adjustment | Defined a closed bundle-v1 enum and intentionally included Unicode numbers |
| `release_id` ordering undefined | Accepted | Defined informational, lexicographically increasing UTC date/sequence IDs |
| Entity diagram label incomplete | Accepted | Completed the release-catalog relationship |
| Validation heading implied runtime work | Accepted | Renamed it to importer validation and tied equality to `manifest_sha256` |

## 15. Review inputs

This proposal was checked against:

- the current Template Generator implementation and operations guide;
- `/Users/qbit/Downloads/TemplateBundleContract.pdf`, contract draft v3 dated
  2026-08-27, as non-final reference material;
- the target workflow stated for MVP graduation; and
- the engineering review verdict supplied for this revision.
