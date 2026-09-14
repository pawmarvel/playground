# Personalized Product Toolset — Prioritized Future Roadmap

Status: Deferred roadmap
Depends on: Evidence from the immutable-bundle MVP trial
Last updated: 2026-09-13

## 1. Purpose and decision rule

This document records work intentionally excluded from the current MVP. It is
not a commitment to implement every item. Start an iteration only when trial or
production evidence identifies a concrete limitation, an owner, and measurable
acceptance criteria.

| Priority | Meaning |
| --- | --- |
| P0 | Required before moving from the limited MVP trial to paid orders or vendor production |
| P1 | Highest-value quality or reliability work after the MVP baseline is measured |
| P2 | Efficiency and maintainability work triggered by recurring operator or engineering cost |
| P3 | Scale infrastructure unnecessary for the initial catalog and traffic level |

Do not add speculative fields to a published contract. A bundle or layout
contract changes only when an accepted requirement cannot be represented by the
current schema. Published revisions remain immutable.

## 2. MVP baseline and graduation evidence

The current baseline already provides:

- product-scoped immutable art, pet, layout, print, review, and graduation
  artifacts;
- independent low-resolution and print-resolution art/pet preparation;
- mechanically derived preview and print geometry;
- bundle-v1, release-catalog-v2, layout-v2, and product-profile-v1 validation;
- an OpenAI `gpt-image-2` production pet-runtime contract;
- private OpenAI/Gemini authoring experiments;
- a local catalog of 40 print-oriented OFL fonts; and
- immutable S3 bundle and release publication with local receipts.

The limited MVP trial should establish a baseline across representative designs,
products, pets, and name lengths. Record at least:

- art-template acceptance and retry rate;
- pet identity, style, pose, crop, and usable-alpha failure rate;
- generation latency and cost by model/configuration;
- layout/font correction time;
- preview-to-print geometry discrepancies;
- upscale artifacts observed at real product scale factors;
- FE bundle-import or renderer-contract failures; and
- operator effort from new design through published release.

The trial is successful when multiple product profiles can reuse their bundled
art/layout with different pets, the FE can import and render bundles without
private authoring knowledge, and observed print candidates justify further
production investment.

## 3. Prioritized iteration stages

Stages express dependencies, not fixed calendar releases. Stage 2 and Stage 3
may run in parallel when different owners are available, but P0 gates take
precedence.

### Stage 0 — Measure and stabilize the MVP trial (current, P0)

Goal: obtain trustworthy evidence without broadening the contract.

- Exercise the complete authoring-to-S3-to-FE import path for the trial catalog.
- Classify failures by art generation, pet runtime, layout/font, print
  derivation, bundle contract, or FE integration.
- Retain exact bundle identity, hashes, provider/model settings, latency, and
  safe reproduction inputs for each defect.
- Verify that publication receipts preserve revision allocation after local
  exchange cleanup.
- Agree with FE on ownership of import validation, runtime retries, activation,
  rollback, and production diagnostics.

Exit when the trial catalog imports successfully, no contract or geometry defect
remains unresolved, and measured quality/latency results can prioritize later
work.

### Stage 1 — Vendor readiness and customer-data safety (P0)

Goal: qualify an accepted print artifact for fulfillment and safely handle real
customer inputs. Vendor qualification, geometry conformance, and upload/privacy
controls are required before automated fulfillment.

#### 1.1 Vendor print qualification

Define and revision-lock per vendor/product:

- physical and pixel dimensions plus DPI interpretation;
- printable area, bleed, and safe area;
- formats, compression, transparency, file-size limits, color space, and ICC
  behavior;
- backend/style/scale-factor combinations approved for upscaling; and
- measurable edge, detail, color, and alpha thresholds from printed samples.

The current deterministic and Bria backends produce print candidates, not
vendor-qualified deliverables. An upscaler must preserve canvas origin, aspect
ratio, crop, subject placement, and alpha silhouette; it must not regenerate or
reinterpret approved artwork. For alpha images, evaluate enhancing RGB while
scaling the approved alpha mask deterministically.

#### 1.2 Cross-resolution conformance

Add renderer-owned `render-metadata.json` containing resolved boxes, anchors,
visible-alpha bounds, text bounds, and baselines. Compare preview and print
metadata after applying the exact profile scale and defined rounding rules.

Also provide downscaled print comparisons, preview/print debug overlays, numeric
tolerances, crop/padding/translation/alpha checks, and golden tests with bundled
fonts and pinned rendering dependencies. Do not recover geometry from flattened
images when the renderer can emit it directly.

#### 1.3 Upload/privacy safety (P0) and formal rights evidence (P1)

The limited MVP deliberately keeps the accepted lightweight operator
source/rights review. Do not make a formal license schema a retroactive MVP
blocker. Before catalog distribution or sales channels expand, introduce a
formal publication gate because ownership and permitted use cannot be inferred
from a file or generated output.

The gate should:

- cover every reference design, QA pet, and generated production asset;
- record source/owner, commercial and redistribution permission, applicable
  territory/term, reviewer, date, status, and artifact hash;
- distinguish source permission, provider terms, trademark review,
  privacy/publicity rights, and generated-output review;
- keep sensitive evidence in restricted storage while authoring stores a stable
  evidence identifier; and
- block publication when evidence is missing, expired, or does not cover the
  sales channel.

Before processing customer images, define retention/deletion policy, validate
image type/size/decode behavior, isolate uploads from executable paths, keep
credentials and customer sources out of bundles, and confirm vendor privacy
handling.

Exit the P0 portion when at least one product/profile/backend passes physical
sample QA, preview-to-print structural checks pass, and upload/privacy controls
are enforced. Complete the P1 formal-rights gate before broader catalog or
channel expansion.

### Stage 2 — Pet-runtime quality, reliability, and latency (P1)

Goal: improve customer-facing transformation using measured MVP failures while
preserving one explicit FE contract.

Build a versioned evaluation set spanning breeds, coat lengths, colors,
markings, head shapes, source poses, photo quality, and difficult alpha edges.
Measure:

- identity, style, pose, expression, crop, and composition hard gates;
- usable transparency and edge contamination;
- retry/fallback behavior, stability, p50/p95 latency, and cost; and
- automated scores only after their correlation with human decisions is known.

Provider/model changes remain independent pet experiments and must not force art
or layout regeneration when outputs satisfy existing geometry.

#### Provider expansion

Bundle-v1 permits only the OpenAI image-edit pet runtime. Future candidates
include a Responses-based OpenAI image transport and Gemini. Before either can
graduate:

1. Define the exact endpoint, request fields, image order, output semantics, and
   model allowlist.
2. Define deterministic alpha validation or a named/versioned matting policy.
3. Implement matching offline and FE adapters.
4. Run conformance, quality, latency, failure, and cost tests.
5. Introduce an explicit bundle schema revision and rollout/rollback policy.

Gemini remains suitable for private experiments but cannot graduate merely by
winning latency. `Images.edit()` does not accept `service_tier`; Fast-mode
evaluation requires a supported transport, not an undocumented request field.

Exit when the chosen runtime meets agreed quality/latency/cost targets, offline
replay and FE use the same contract, and retry outcomes remain diagnosable.

### Stage 3 — Template quality and authoring throughput (P1/P2)

Goal: expand design coverage and reduce operator effort without turning
layout-v2 into a generic graphics format.

Prioritize only features required by rejected or slow-to-author designs:

1. Prompt history, side-by-side results, accepted-template snapshots, and richer
   pet/name fixtures.
2. Advisory pet/text region and OCR-assisted text suggestions.
3. Layout presets for repeated structures.
4. Deterministic tracking, stroke, opacity, shadow, pet/name rotation, layer ordering,
   masks, and controlled occlusion.
5. Curved/path text, multiple slots, and optional layers only when real products
   require them.
6. Reference-cropping and perspective-rectification assistance.

Automatic detection remains advisory; operators retain final control.

#### Pet-relative shadows

The `charlie-well-trained` design exposes a missing abstraction. Its shadow
cannot be baked reliably into `art.png`: shape, softness, opacity, and position
must move with the transformed pet. Support should use an explicit pet-relative
effect/layer with deterministic ordering and preview/print derivation. Until
then, this example may omit or approximate its shadow and is not evidence of
automated shadow support.

#### Wider OFL font discovery

Use the local 40-face catalog first. Enter broader discovery only after an
operator rejects local results or calibrated confidence shows no adequate match.
The future flow should:

- distinguish missing typefaces from tracking, outlines, shadows, width
  changes, curved baselines, or distressing;
- search a pinned metadata snapshot of a broader OFL universe;
- materialize and validate a bounded candidate set;
- optionally rerank only known candidates with a multimodal model;
- verify glyph coverage, renderability, source integrity, license, and
  FontBakery-style quality; and
- pin font, license, source revision, and hashes before local-catalog promotion.

Possible sources include Google Fonts, Fontsource, and reputable OFL foundries.
Identification services may provide clues but cannot bypass validation. If no
suitable OFL font exists, require an explicit decision rather than silently
using a proprietary font.

#### Generated pet-name artwork

Reconsider AI-generated name PNGs only if deterministic font/effect support
cannot meet validated designs. Evaluation must cover spelling, transparent
isolation, reproducibility, long names, and review criteria. No AI-name
implementation remains in the MVP repository.

Exit criteria are feature-specific: show material improvement on designs that
failed the current workflow, with deterministic bundles and matching preview and
print behavior.

### Stage 4 — Customer approval and order reproducibility (P1)

Goal: bind a customer-approved preview to the exact high-resolution artifact
sent for fulfillment.

Potential flow:

1. Validate the customer pet and name.
2. Generate the transformed preview pet.
3. Render with one immutable bundle revision.
4. Optionally allow bounded, server-validated translation/scale adjustments.
5. Store changes as an order override, never a template mutation.
6. Bind approval to bundle revision, normalized name, transformed-pet hash,
   override, and preview hash.

The print flow upscales the exact approved transformed pet, derives any print
override mechanically, renders text from the bundled font, and composes separate
print-resolution layers. It must not upscale the flattened preview or silently
substitute an independently regenerated pet.

Add pet or name rotation only with a defined origin, post-rotation containment,
preview/print derivation, and FE conformance tests.

```text
draft -> customer_approved -> print_rendered -> operations_approved
      -> sent_to_vendor
```

Record actor, timestamp, hashes, retry lineage, and the exact approved vendor
artifact. Product activation and rollback remain FE state over immutable bundle
revisions.

### Stage 5 — Authoring lifecycle and deployment hardening (P2)

Goal: reduce maintenance cost only after trial use demonstrates recurring pain.

- Add reference-aware cleanup states for reviewed-but-not-graduated decisions
  and print candidates.
- Provide standalone wheel/container distribution, including packaged or
  explicitly provisioned fonts, schemas, profiles, and static assets.
- Split the large authoring module into experiment, review/decision,
  print/graduation, trace, and cleanup services while preserving one CLI.
- Add a compact release summary covering design, product profile, revision,
  preview/print sizes, runtime references, and validation state.
- Add an optional HTML comparison report over immutable evaluation data and
  contact-sheet assets; keep winner selection explicitly human-owned.
- Expand thin CLI-adapter tests beyond the MVP smoke coverage to a complete
  argument/error matrix for bundle and catalog commands.
- Pin rendering dependencies where exact reproduction matters.
- Add structured diagnostics and cost/latency summaries.
- Replace repeated font-catalog snapshots with content-addressed storage only if
  storage or copy time becomes material.

These changes must not add compatibility branches for pre-MVP formats.

### Stage 6 — Service and operational scale (P3)

Goal: support concurrency and operational reliability after traffic and catalog
volume justify a service platform.

- asynchronous generation and print jobs with retry/idempotency;
- object storage and databases for customer jobs, orders, and approvals;
- authentication, authorization, managed secrets, abuse controls, and quotas;
- logs, metrics, tracing, alerts, and per-request cost accounting;
- automated deployment and rollback;
- operations review tooling; and
- vendor API integration.

Retain the deterministic renderer as shared code. Do not duplicate geometry or
text behavior across offline and online paths.

## 4. Contract evolution rules

Possible additions include multiple image/text slots, optional layers, named
anchors, contain/cover fit, clipping, masks, tracking, stroke, shadow, rotation,
curved or multiline text, safe areas, multiple output profiles, and bounded
order overrides.

For every change:

- add the smallest representation satisfying a demonstrated requirement;
- define preview rendering, print derivation, validation, and FE conformance
  together;
- create a new schema version when published semantics would change;
- never silently reinterpret bundle-v1 name or rendering rules; and
- keep published bundles complete and immutable.

If multilingual testing requires user-perceived name lengths, replace Unicode
code-point counting with UAX #29 grapheme counting only in a new schema version.

## 5. Shared quality backlog

Each stage selects relevant tests from this common backlog:

- short, long, narrow, wide, multilingual, and punctuation-bearing names;
- pets with wide ears, tall bodies, long tails, unusual padding, difficult
  edges, and varied source quality;
- supported formats, malformed inputs, color modes, and size limits;
- pixel-difference thresholds rather than PNG byte equality across platforms;
- structural assertions using render metadata;
- profile derivation, rounding, alpha, and upscale preservation;
- override and approval-state transitions;
- failed and retried asynchronous jobs; and
- opt-in live provider tests, with mocked calls in the default unit suite.

## 6. Priority summary

| Order | Stage | Priority | Trigger |
| --- | --- | --- | --- |
| 0 | Measure and stabilize MVP | P0 | Current trial |
| 1 | Vendor, cross-resolution, and customer-data safety | P0 | Before automated vendor fulfillment |
| 1b | Formal rights evidence | P1 | Before broader catalog/channel distribution |
| 2 | Pet-runtime quality and latency | P1 | Runtime quality, latency, or cost misses target |
| 3 | Template quality and authoring throughput | P1/P2 | Designs are rejected or authoring cost repeats |
| 4 | Customer approval and order reproducibility | P1 | Before self-service approval or automated fulfillment |
| 5 | Lifecycle and deployment hardening | P2 | Maintenance, retention, or deployment pain repeats |
| 6 | Service and operational scale | P3 | Proven traffic, catalog, or operations demand |

When priorities compete, protect contract correctness, customer identity, print
fidelity, rights/privacy, and reproducibility before design breadth or
operational automation.
