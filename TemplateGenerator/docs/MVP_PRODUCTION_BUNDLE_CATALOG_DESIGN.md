# Template Bundle Catalog — MVP Production Graduation Design

## 1. Purpose

This document proposes the smallest production-ready boundary between the
offline PawMarvel Template Generator and the application that creates customer
previews and print designs. It is intended for review and alignment between the
template-authoring and FE/application engineers.

Detailed commands live in `MVP_OPERATIONS_GUIDE.md`. This document is the
architecture and contract source of truth for production artifacts, ownership,
handoff, and the implemented MVP boundary for approximately ten design-product
entries.

The MVP uses a push/import workflow: the generator publishes immutable release
artifacts, while the application imports them as drafts and owns activation.

FE development is still in progress, so this design does not depend on a
specific FE framework or repository. The agreed catalog, bundle, asset, model,
and rendering semantics are the boundary.

### 1.1 FE implementation quick reference

The FE/application integrates with exactly two immutable artifact types:

1. `release-catalog-v2` identifies the bundle revisions in one import batch and
   binds each entry to the SHA-256 of its `bundle.json`.
2. `bundle-v1` is the complete runtime contract for one
   `(design_id, product_profile_id, bundle_revision)`.

The application must not read the offline authoring tree, infer a bundle from
an experiment, or import a print candidate directly. Its only handoff input is
an immutable S3 release-catalog key; that catalog resolves immutable bundle
objects below the same configured S3 publication prefix.

#### Bundle identity

```text
template_id = <design_id>--<product_profile_id>
bundle key  = (template_id, bundle_revision)
example     = (life-is-good--blanket-king-9375x12375, 1)
```

The generator owns these values. FE stores them verbatim, imports the same key
idempotently, and rejects different bytes presented under an existing key.
Application draft/active/disabled state is separate from generator identity.

There are two distinct selection decisions. The offline application owner
chooses component experiment winners before a bundle exists. After import, the
FE/application owner independently chooses which immutable bundle revision is
draft, active, rolled back, or mapped to a commerce product. That production
decision uses only the release catalog and bundle and never reaches into the
offline workspace.

#### Release catalog shape

```json
{
  "schema_version": 2,
  "release_id": "2026-09-07.001",
  "created_at": "2026-09-07T20:00:00Z",
  "templates": [
    {
      "template_id": "life-is-good--blanket-king-9375x12375",
      "design_id": "life-is-good",
      "product_profile_id": "blanket-king-9375x12375",
      "bundle_revision": 1,
      "manifest_path": "bundles/life-is-good--blanket-king-9375x12375/v000001/bundle.json",
      "manifest_sha256": "<sha256>"
    }
  ]
}
```

When an HTTP base URL is supplied during release creation, the entry also has
`manifest_url`. Otherwise the application backend resolves `manifest_path`
from the configured S3 bucket and publication prefix. The local `exchange/`
tree merely mirrors those relative keys before upload; it is never an FE input.

#### Versioned S3 bundle prefix

```text
s3://<bucket>/<environment-prefix>/
  bundles/<template-id>/vNNNNNN/
    bundle.json                         # authoritative manifest
    product-profile.json                # preview and print dimensions/policy
    art.png                              # reusable preview art
    layout.json                          # composition-only preview layout-v2
    print/art.png                        # reusable profile-sized print art
    layout-print.json                    # mechanically scaled print layout-v2
    reference-design.png                 # primary runtime reference
    reference-designs/                   # optional ordered references 0002+
      reference-design-0002.png
    art-template-{gpt|gemini}.md          # offline provenance artifact
    pet-transform-gpt.md                  # MVP production pet-transform prompt
    fonts/<selected-font>.ttf
    fonts/OFL.txt
    qa/input-pet.png                     # operator-reviewed non-customer replay fixture
    qa/transformed-pet.png               # representative QA only
    qa/golden-preview.png                 # human conformance aid only
    qa/golden-preview-debug.png
  releases/<release-id>/catalog.json    # uploaded after all referenced bundles
```

`bundle.json.assets` is an array inventorying every bundle file other than
`bundle.json`. Each entry contains bundle-relative `path`, `sha256`, byte size,
and media type; PNG entries also contain width and height. FE uses manifest
fields and canonical paths, never filename scanning or authoring conventions.

#### Runtime responsibilities

| Phase | FE/application reads from bundle | FE/application supplies or creates |
| --- | --- | --- |
| Import | `bundle.json`, asset inventory, profile, layouts, prompt, references, font/license | Verified immutable application copy/cache and draft record |
| Pet transform | `runtime.provider`, `runtime.model`, `runtime.transport`, closed `runtime.request_parameters`, `runtime.prompt`, ordered `runtime.reference_assets`, output/normalization policy | Customer pet as image 1; declared references afterward; transformed-pet PNG |
| Preview | `art.png`, `layout.json`, selected OFL font, renderer and `personalization.pet_name` semantics | Validated customer name and transformed pet |
| Approved print | `print/art.png`, `layout-print.json`, profile, font, renderer semantics | Upscaled approved transformed pet and final print composition |

Preview and print must use the same bundle revision and customer values. FE
must not regenerate or upscale template art. Model calls, private asset access,
customer-image handling, and print rendering run in a trusted application
backend/job, not browser code.

Layout V2 is a closed composition contract. `name.font_size_px` is the nominal
preview size, `name.min_font_size_px` is the smallest allowed fallback,
`name.fit` is `shrink_only`, and `name.padding_px` is a uniform inset. Short
names stay at nominal size; longer names shrink only as needed. Print layout
boxes, font sizes, and padding are the round-half-up uniform scale of preview
values. `bundle.json.renderer.version` is `2` and identifies these semantics.
For a newly authored layout, the editor derives the default minimum from the
selected font and padded name box so 12 common printable name characters fit;
it no longer assumes half the box height. This is an editable authoring default,
not a promise that every possible 12-code-point Unicode sequence has equal
glyph width.

#### Import validation order

1. Reject unsupported catalog schema versions and duplicate identities.
2. Fetch `bundle.json` and verify the catalog's `manifest_sha256`.
3. Validate manifest schema and agreement among path, template, design, profile,
   and revision identities.
4. Verify every asset path is bundle-relative and every listed digest, byte
   count, media type, and image dimension.
5. Validate preview/print canvas dimensions against `product-profile.json` and
   verify that `layout-print.json` is the permitted scale of `layout.json`.
6. Reject unsupported provider/model/transport/request fields, prompt-category mismatches,
   missing ordered references, missing OFL font/license, or invalid renderer
   semantics.
7. Create or update only an application draft. Activation remains an explicit
   application-owner action.

#### Offline artifacts shown for context only

The authoring operator works with the following private hierarchy. It explains
bundle provenance and support requests, but it is not an FE schema or runtime
dependency:

```text
authoring/<design-id>/<product-profile-id>/
  product.json
  experiments/{art,pet,layout}/...
  reviews/{art,pet,layout,assembly}/<review-id>/{evaluation.json,artifacts/,decision.json}
  print-candidates/...
  graduations/<graduation-id>/{selection.json,publications/}
  scratch/...
```

FE feedback should identify `template_id`, `bundle_revision`, and a safe
reproduction input. The operator maps that report back to private experiments
and delivers a new complete bundle revision; FE never edits generator artifacts
in place.

A layout experiment may snapshot an authoring-only `font-reference-v1`
artifact: the exact reference-image text rectangle, its visible characters,
and the reference SHA-256. It exists solely to make offline OFL ranking
reproducible. It is not a bundle asset, and FE must neither consume nor recreate
it; production receives only the selected font, license, and layout.

It may also snapshot `layout-reference-v1`, containing operator-confirmed pet
and personalized-name regions bound to the reference SHA-256. This evidence
replaces generic initial placement percentages during authoring, but normalized
mapping remains advisory when screenshot and product aspect ratios differ. It
is likewise excluded from the bundle. FE consumes only the reviewed
`layout.json` and mechanically derived `layout-print.json`.

#### FE-independent support boundary

After import, FE/application support needs no offline path or operator machine.
It can verify the catalog-to-manifest digest, validate every asset, inspect the
closed runtime request and image order, replay transformation with
`qa/input-pet.png`, visually compare `qa/transformed-pet.png`, and render the QA
name against the golden previews. A support report contains `template_id`,
`bundle_revision`, manifest SHA-256, failing phase, sanitized error, and—only
when safe—a reproduction input. The offline owner responds with a complete new
revision; FE owns draft selection, activation, rollback, commerce-product
mapping, runtime credentials/calls, customer data, and vendor-job execution.

## 2. MVP outcome and non-goals

At the end of the MVP:

- an operator can iteratively author a design for one or more product profiles;
- an operator can keep multiple art, pet-transform, and layout/font experiments
  side by side without overwriting earlier attempts;
- an application owner can record the winning component combination before it
  is packaged;
- each approved design-product pair is emitted as an immutable, versioned
  bundle;
- a small immutable S3 release catalog can hand several bundles to the
  application in one import;
- the application can validate a bundle without knowing generator internals;
- application-owned draft, activation, and rollback do not require the
  generator to become a production control plane; and
- production bundles and authoring work remain outside Git.

The MVP does not include a database, template-authoring service, crowdsourced
authoring, workflow engine, automated visual scoring, multi-region publishing,
asset deduplication, or a general-purpose product information system. Ten
entries are small enough for reviewed JSON files and a semi-manual release.
Experiment manifests, content hashes, a small human scorecard, and safe local
cleanup are included; they are file-based authoring controls, not a workflow
service.

## 3. Delivery and activation architecture

### 3.1 Recommended MVP: push/import

```text
TemplateGenerator Git repository
  code + schemas + sample fixtures
                 |
                 v
private authoring workspace
  references + prompts + immutable experiment attempts + QA
                 |
         validate and package
                 v
immutable release in Amazon S3
  release catalog + versioned bundle directories
                 |
            FE imports
                 v
application draft -> application review -> application activation/rollback
```

The generator is the system of record for the bytes and identity of an offline
bundle revision. The application is the system of record for whether an
imported revision is draft, active, disabled, or rolled back. This avoids
building a second production activation system in the generator.

The release catalog is a transfer index, not the storefront's live catalog. FE
imports one immutable S3 catalog key through its backend. Every
`manifest_path` resolves from the configured bucket and environment prefix; an
optional `manifest_url` may expose the same object through an approved HTTPS
origin. Once imported, the application may copy assets into its own storage or
retain an immutable reference to the S3 objects.

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
- a representative transformed pet plus golden preview/debug images for
  geometry-focused conformance review;
- performing the MVP's lightweight source/rights check for reference and QA
  images before publication, with no formal license artifact in the bundle;
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
publishes an operator-reviewed, non-customer conformance fixture through S3. The
application proves it can reject unsupported schemas, verify identity and
required assets, and reproduce that fixture's geometry. Generator unit tests
build small temporary bundles instead of checking production-scale bundle
binaries into Git.

For the MVP, automated conformance is deliberately limited to canvas size,
boxes, alpha trimming, containment, anchor points, layer order, name
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
| `experiment_id` | Unique private authoring experiment | Create before generating attempts; never reuse for different inputs |
| `attempt_id` | One immutable stochastic or deterministic execution inside an experiment | Create for every execution; never overwrite |
| `selection_id` | Reviewed choice of art, pet-runtime, and layout/font winners | Create or supersede before allocating a bundle revision |

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

Private authoring only:
Design + ProductProfile
          |
          n
     Experiment 1 --- n Attempt
          |
          n
       Selection pins one winning art attempt,
       pet-runtime experiment, and layout/font attempt
          |
          1
   TemplateBundleRevision
```

`bundle_revision` remains the only production version. Experiment, attempt,
and selection IDs provide authoring lineage and must not become independently
deployable FE versions. Unchanged selected bytes may be reused by hash in a new
bundle revision, but every production revision remains a complete atomic
contract.

## 6. Storage and path contract

### 6.1 Git repository

Keep only these classes of files in `TemplateGenerator` Git:

```text
src/                         generator implementation
schemas/                     production and private-authoring JSON Schemas
profiles/                    reviewed small product-profile source files
assets/fonts/                curated OFL authoring catalog
examples/                    development-only source and pet inputs
docs/                        design and operations documentation
tests/                       unit and contract tests
```

Do not add production reference screenshots, production prompts, mutable work
directories, customer pets, generated bundles, or production release payloads
to Git. Keep one or two compact development-only source-design examples plus a
minimal reusable pet input set for the guide and local tests. These images are
not approved production fixtures and the repository does not assert production
rights for them. Reviewed conformance bundles and production catalog releases
are immutable S3 objects outside Git.

### 6.2 Private authoring storage

For the MVP, use a private local or shared POSIX filesystem. Repository-local
`work/authoring/` is the fastest setup and is ignored by Git; it deliberately
keeps art, transformed-pet, and layout attempts close to the operator for
comparison and reruns without network latency. Point the same root at a private
backed-up filesystem when losing valuable experiment history would be costly.
The authoring lifecycle relies on exclusive directory creation and atomic
rename; S3 is not an authoring backend. No attempt command uploads artifacts.

Each operator run starts from a mode-`0600`, sourceable file below
`work/configs/`. It centralizes provider keys, design/product input paths,
provider/model/quality defaults, local roots, release identity, and S3/AWS
settings. The file is mutable private operator state: it is ignored by Git,
never copied into an experiment or bundle, and never consumed by FE. Experiments
continue snapshotting their resolved non-secret inputs, so changing or switching
configuration files cannot rewrite provenance. Existing CLIs keep their explicit
arguments; sourcing the file only removes repetitive shell setup.

The conventional repository-local path is
`work/configs/<design-id>--<product-profile-id>--<iteration>.env`.
Mutable design-source files live separately at
`work/design-inputs/<design-id>/`: the primary reference, provider-qualified
prompts, optional supporting references, and optional font/layout reference
evidence. The operator copies approved inputs there before generating the
configuration. Generated configs point at this ignored private folder and
export optional reference paths as empty when the corresponding files do not
exist. Each experiment snapshots every resolved non-secret input it consumes.

```text
design-inputs/
  <design-id>/
    reference-design.png
    art-template-<provider>.md
    pet-transform-<provider>.md
    reference-designs/       # optional supporting references
    font-reference.json      # optional authoring evidence
    layout-reference.json    # optional authoring evidence
configs/
  <design-id>--<product-profile-id>--<iteration>.env
authoring/
  evaluation-protocols/<protocol-id>.json
  fixture-sets/<fixture-set-id>/
    fixture-set.json
    images/
  <design-id>/
    <product-profile-id>/
      experiments/
        art/<experiment-id>/
          experiment.json
          inputs/             exact prompt/reference/profile snapshots
          attempts/<attempt-id>/
            run.json
            outputs/
            qa/
        pet/<experiment-id>/
          experiment.json
          inputs/
          attempts/<attempt-id>/
            run.json
            outputs/
            qa/
        layout/<experiment-id>/
          experiment.json
          inputs/             pinned art/pet, OFL catalog, optional layout/font references
          attempts/<attempt-id>/
            run.json
            outputs/
            qa/
      reviews/{art,pet,layout,assembly}/<review-id>/
        evaluation.json
        artifacts/
        decision.json
      print-candidates/<candidate-id>/
        print-candidate.json
        outputs/
      graduations/<graduation-id>/
        selection.json
        publications/<release-id>--<bundle-revision>.json
      scratch/                replaceable, never publishable
```

This path boundary is an invariant, not a naming preference:

- the design directory is a namespace only; it owns no mutable or selectable
  artifact directly;
- all reference snapshots, generated art, prompt snapshots, product profiles,
  pet experiments, layout/font decisions, evaluations, selections, print
  candidates, and publication receipts live below
  `authoring/<design-id>/<product-profile-id>/`; and
- generated `authoring/<design-id>/art.png`, `layout.json`, or shared mutable
  prompts do not exist. Those artifacts depend directly or indirectly on the
  product geometry and must not cross the product boundary.

One reference design used for two products therefore creates two independent
authoring workspaces and two independent production identities:

```text
authoring/life-is-good/
  blanket-king-9375x12375/
    experiments/
    reviews/
    graduations/
    scratch/
  blanket-throw-5625x6875/
    experiments/
    reviews/
    graduations/
    scratch/

exchange/bundles/
  life-is-good--blanket-king-9375x12375/v000001/
  life-is-good--blanket-throw-5625x6875/v000001/
```

An operator may reuse a source reference or copy a prompt as the starting point
for the second product, but `create-experiment` snapshots both again with that
product profile. Reusing an experiment, attempt, layout, selection, or generated
artifact by path across product roots is invalid. This explicit duplication is
cheaper and safer for the MVP than a cross-product dependency graph.

An experiment isolates one authoring kind: art/prompt generation, pet runtime
prompt/model/reference configuration, or layout/font choice. Art experiments
primarily compare prompt and quality settings; pet experiments compare prompt,
provider/model, and latency tradeoffs; layout attempts compare font, name-box
size, and placement against fixed art/pet inputs. A deliberate series varies
one field at a time when causal attribution matters. Art and pet attempts are
immutable so repeated stochastic runs of the exact configuration measure
reliability and latency without overwriting evidence. Human-readable IDs may contain a
UTC timestamp and short label, but identity comes from `experiment.json` plus
input and output hashes. A rerun creates another attempt or another experiment;
it never replaces comparison evidence. Only `scratch/` permits replacement.

Art prompts normally have design-product scope because the profile defines the
art canvas. Pet prompts may later be shared across compatible product profiles,
but the MVP snapshots them inside each experiment to avoid implicit reuse.
References may remain design-scoped; every experiment still records their
exact hashes, roles, and order.

Failed or discarded experiments may be removed by the cleanup policy in
section 6.5. Draft experiments are never removed automatically. Published
bundle revisions and the source evidence needed to explain them may not be
removed by authoring cleanup. After an experiment is referenced by a selection,
its status is immutable because the selection pins the experiment metadata
hash; later work uses a successor experiment.

### 6.3 Authoring artifact lifecycle

Offline development promotes references to immutable candidates; it never
promotes a mutable working directory:

| Stage | Durable write | Iteration rule | Downstream action |
| --- | --- | --- | --- |
| Art | `experiments/art/<experiment-id>/inputs/` and immutable `attempts/<attempt-id>/` | New prompt/model/quality = new experiment; identical stochastic rerun = new attempt | Compare experiments and short-list an art attempt |
| Pet runtime | `experiments/pet/<experiment-id>/inputs/` and immutable fixture attempts | New prompt/model/reference/request config = new experiment; identical fixture rerun = new attempt | Compare controlled fixture results, failure rate, and latency |
| Layout/font | `experiments/layout/<experiment-id>/` pinned to exact art and representative-pet attempts, OFL catalog, and optional screenshot font reference | New font/name box/nominal-size/placement against the same inputs = new attempt; changed pinned input/catalog/reference region or text = new experiment | Compare short/typical/long names, then run assembly compatibility |
| Print finalist | `print-candidates/<candidate-id>/` derived from one exact candidate combination | New ID when a pinned input changes; unchanged template print assets may be hash-verified and copied from another candidate | Inspect the profile-sized result |
| Graduation | `graduations/<graduation-id>/selection.json` | Supersede; never edit a published graduation | Build the next revision |
| Local graduation staging | `work/exchange/bundles/<template-id>/vNNNNNN/` plus `work/exchange/releases/<release-id>/catalog.json` | Immutable locally; correction = next revision | Validate and review the S3 upload plan |
| S3 publication | `<prefix>/bundles/...` plus `<prefix>/releases/...` and a local receipt | Conditional create only; correction = next revision | Give FE the S3 release-catalog key |
| FE feedback | A review packet linked to the imported revision | Fork only affected experiments | Re-run compatibility, graduate, and publish |

Art and pet candidates may be developed in parallel, but a layout is valid
only for the exact art hash it was authored against. A new pet runtime may
reuse existing geometry only after assembly compatibility passes. Print QA is
late by design: upscale finalists, not every stochastic attempt. FE feedback
returns to the same product-scoped workspace and creates a new experiment,
selection, and bundle revision; a released revision is never patched.

### 6.4 Immutable production storage in S3

Amazon S3 is the canonical shared store for graduated bundle and release
objects. `work/exchange/` is only a local, ignored staging/cache tree with the
same relative paths. The MVP uses AWS CLI v2 rather than adding a cloud SDK to
the generator package.

```text
s3://<bucket>/<environment-prefix>/
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
        pet-transform-gpt.md
        art-template-{gpt|gemini}.md
        fonts/<font>.ttf
        fonts/OFL.txt
        qa/input-pet.png
        qa/transformed-pet.png
        qa/golden-preview.png
        qa/golden-preview-debug.png
  releases/
    <release-id>/catalog.json
```

Bundles remain self-contained even when that duplicates fonts or art. At this
scale duplication is easier to audit and roll back than shared-asset graphs.

Nothing below `authoring/`, and no customer source pet, is uploaded. The
operator first reviews and validates the local exchange release, inspects the
dry-run upload plan, and then explicitly runs
`pawmarvel-catalog publish-s3 --execute`. Publication uses conditional object
creation, stores a SHA-256 checksum, verifies checksum and byte size, uploads
each bundle manifest after its assets, and uploads the release catalog last.
This makes a partial run undiscoverable and retryable without allowing an
existing key to change.

For the MVP, the application backend fetches all release and bundle artifacts.
It may expose only browser-safe art/font URLs to its UI after import. This avoids
an ambiguous mix of public and private paths; per-asset visibility and
direct-CDN browser consumption are deferred.

### 6.5 Authoring cleanup and production retirement

Cleanup and retirement are separate operations:

- `scratch/` is disposable and is never a valid selection source;
- failed or discarded experiment attempts may be deleted after the configured
  authoring retention period; draft experiments require an explicit operator
  decision and are never age-deleted automatically;
- a selected experiment is protected until its prompt, source hashes,
  generation configuration, outputs, and decision record are captured by an
  immutable bundle revision;
- an experiment or attempt referenced by any `selection.json` or published
  bundle provenance is protected from automatic cleanup;
- after all S3 objects have passed checksum and byte-size verification, the
  publisher automatically and idempotently writes
  `publications/<release-id>--<bundle-revision>.json`, recording the template/revision,
  selection and selected experiment/attempt IDs, manifest hash, release IDs,
  transfer location, and publication time; cleanup uses these local receipts
  rather than requiring a remote object-store scan, and `--bundle-revision next`
  treats them as the durable revision high-water mark when local exchange
  bundles have been reclaimed;
- keying receipts by release and bundle revision permits a later immutable
  release to re-list an unchanged bundle while retaining a distinct audit event;
- cleanup defaults to dry-run, prints exact paths and reasons, and requires an
  explicit second command to delete; and
- published bundle revisions and release catalogs are never deleted by the
  authoring cleanup command.

To keep the MVP cleanup model small, a review decision or print candidate that
is not referenced by a completed graduation is still `unselected` and may be
removed after the retention threshold. Operators must inspect the dry-run list
before `--apply`. Reference-aware protection for decided-but-not-graduated work
is deferred until authoring volume justifies the extra lifecycle state.

Retiring a production product or template is application state. The
application disables or retires the imported revision while historical orders
continue to pin it. Object-storage lifecycle rules may move old immutable
revisions to archival storage, but deletion requires a separate retention and
legal decision outside this MVP.

## 7. Proposed release catalog contract

The catalog is a small batch-delivery document. It does not say which templates
are active in production and does not replace application draft/publish state.

```json
{
  "schema_version": 2,
  "release_id": "2026-09-02.001",
  "created_at": "2026-09-02T22:00:00Z",
  "templates": [
    {
      "template_id": "life-is-good--blanket-king-9375x12375",
      "design_id": "life-is-good",
      "product_profile_id": "blanket-king-9375x12375",
      "bundle_revision": 2,
      "manifest_path": "bundles/life-is-good--blanket-king-9375x12375/v000002/bundle.json",
      "manifest_url": "https://assets.example.invalid/bundles/life-is-good--blanket-king-9375x12375/v000002/bundle.json",
      "manifest_sha256": "<bundle-json-sha256>"
    }
  ]
}
```

Rules:

- `manifest_url` is emitted per entry when `--asset-base-url` is provided and
  otherwise omitted for application-backend S3 resolution;
- `manifest_path` starts with `bundles/`, uses `/`, cannot contain `..`, and
  resolves from the configured S3 publication prefix;
- each `(template_id, bundle_revision)` appears at most once;
- identity fields must agree with the referenced manifest;
- the same `design_id` may appear with several product profiles;
- import is idempotent: the same identity and `manifest_sha256` is a no-op;
- the importer rejects the same identity with a different `manifest_sha256`;
- the importer hashes `bundle.json` before trusting its per-asset hashes; and
- catalog and entry objects are closed: unknown fields and unsupported
  `schema_version` values are rejected. A new optional field therefore requires
  a new catalog schema version.

This closes the integrity chain as trusted catalog entry -> manifest digest ->
asset digests. It detects transfer corruption or substitution relative to the
catalog. It does not authenticate a maliciously replaced catalog; HTTPS,
object-store IAM, and restricted release-write credentials establish catalog
authenticity for the MVP. Signed catalogs are deferred.

The release catalog contract accepts schema version 2 only.

If the architecture later changes to runtime pull, define a separate channel
contract then. Any channel must use a full URL or a publication-prefix-relative
path without `..`.

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
    "transport": "images.edits",
    "prompt": "pet-transform-gpt.md",
    "reference_assets": ["reference-design.png"],
    "input_image_order": ["user_pet", "reference_1"],
    "request_parameters": {
      "quality": "low",
      "size": "816x816",
      "background": "transparent",
      "output_format": "png",
      "n": 1
    },
    "output": {
      "format": "png",
      "background": "transparent",
      "width": 816,
      "height": 816
    },
    "normalization": {
      "policy": "transparent-rgba-contain",
      "version": 1,
      "alpha_failure": "reject"
    }
  },
  "renderer": {
    "layout_schema_version": 2,
    "pet_fit": "contain-visible-alpha",
    "pet_anchor": "bottom-center",
    "name_fit": "nominal-size-shrink-only-visible-ink-contain",
    "version": 2
  },
  "personalization": {
    "pet_name": {
      "normalization": "NFC",
      "whitespace": "trim-and-collapse",
      "length_unit": "unicode-code-points",
      "min_length": 1,
      "max_length": 12,
      "allowed_characters": "unicode-letters-marks-numbers-space-apostrophes-ascii-hyphen"
    }
  },
  "provenance": {
    "selection_id": "life-is-good-blanket-king-v02",
    "selection_sha256": "<sha256>",
    "art_attempt_id": "attempt-0003",
    "pet_experiment_id": "pet-gpt-v07",
    "layout_attempt_id": "attempt-0001",
    "qa_fixture": {
      "input_pet": "qa/input-pet.png",
      "pet_name": "SAUSAGE"
    },
    "component_evaluations": {
      "art": {"evaluation_id": "art-eval-20260902", "evaluation_sha256": "<sha256>", "status": "passed"},
      "pet": {"evaluation_id": "pet-eval-20260902", "evaluation_sha256": "<sha256>", "status": "passed"},
      "layout": {"evaluation_id": "layout-eval-20260902", "evaluation_sha256": "<sha256>", "status": "passed"}
    },
    "compatibility_evaluation": {"status": "passed"},
    "print_candidate": {"status": "passed"},
    "selected": {"art": {}, "pet_runtime": {}, "layout_font": {}},
    "print_derivation": {
      "mode": "selected-print-candidate",
      "print_candidate_id": "print-finalist-0002",
      "print_candidate_sha256": "<sha256>",
      "backends": {"template": "deterministic", "pet": "deterministic"},
      "template_source": {"mode": "generated"},
      "art_sha256": "<sha256>",
      "layout_sha256": "<sha256>"
    },
    "generator": {"version": "0.1.0"}
  },
  "prompts": {
    "art_template": "art-template-gpt.md",
    "pet_transform": "pet-transform-gpt.md"
  },
  "assets": [
    {
      "path": "art.png",
      "sha256": "<sha256>",
      "bytes": 123456,
      "media_type": "image/png",
      "width": 800,
      "height": 1056
    }
  ]
}
```

The real `assets` array contains every file except `bundle.json`. Each entry
has `path`, `media_type`, `bytes`, and `sha256`; raster images also include
dimensions. Paths are unique, bundle-relative, and cannot escape the directory.
`product-profile.json` is included so later source-profile edits cannot change
an existing bundle's meaning.

Integrity verification occurs on application import or first immutable bundle
cache-fill, not once per customer personalization. Subsequent runtime reads use
the application's verified immutable copy/cache.

The manifest carries the customer-name policy separately from layout geometry.
Before measuring length, the consumer applies NFC normalization, trims leading
and trailing whitespace, and collapses each internal whitespace run to one
ASCII space. It then permits Unicode general categories Letter, Mark, and
Number, plus ASCII space, straight apostrophe, right single quotation mark, and
ASCII hyphen. Length is the number of Unicode code points after normalization;
the inclusive minimum is one and the per-bundle maximum is authored with
`pawmarvel-bundle --pet-name-max-length`. The application validates once before
preview generation and reuses the exact normalized value for preview and
print. UAX #29 grapheme counting can replace code-point counting only in a
future bundle schema version.

### 8.3 Runtime provider and model scope

The offline generator supports both OpenAI and Gemini experiments. Bundle-v1
permits only the OpenAI `gpt-image-2` customer pet runtime because offline trials
found its pet-only transparency behavior more reliable and the MVP has no
reviewed Gemini transport contract. `graduate` and bundle construction reject a
selected Gemini pet experiment. Gemini remains available for private latency and
quality comparison; enabling it in production requires a new schema/runtime
contract and matching FE adapter tests. Art-template generation remains offline,
so either provider may be evaluated and its selected prompt retained as
provenance.

`bundle.json.runtime` identifies OpenAI and `gpt-image-2` explicitly; the
application importer still checks that pair against its deployment allowlist.
Preview and print must pin the same bundle revision and transformed-pet lineage.
OpenAI provides the native transparent-background control required by the MVP.
Bundle-v1 specifies `alpha_failure: reject` and does not imply a background-
removal algorithm.

The OpenAI implementation uses `/v1/images/edits`. That endpoint and the
Python SDK `Images.edit()` method do not accept `service_tier`; request-level
Fast mode is documented for Responses and Chat Completions. The MVP therefore
does not expose `--service-tier` or claim Fast processing for OpenAI image
edits. Evaluating a Responses-based image transport is a separate future
experiment and must not be implemented by passing an undocumented body field.

Prompt filenames are provider-qualified:
`art-template-{gpt|gemini}.md` and `pet-transform-gpt.md`. Each released bundle
contains one selected file for each role. `runtime.request_parameters` is the
closed OpenAI field set the trusted application adapter passes
without inventing defaults. For OpenAI this includes quality, requested size,
background, output format, count, and input fidelity when required by the
selected model. The manifest identifies the runtime pet prompt under
`runtime.prompt` and the offline art prompt under `prompts.art_template`;
`prompts.pet_transform` repeats the runtime prompt path for convenient role
lookup. Both prompt files also appear in `assets` with their content hashes.
The runtime pet prompt must use the GPT category. Authoring may retain Gemini
variants privately for side-by-side evaluation. Private experiment filenames may add a
lowercase variant suffix, such as `art-template-gpt-v02.md`; bundle graduation
copies the winner under the canonical provider-qualified filename. Variant
identifiers therefore never leak into the FE contract.

When evaluating an alternate provider, benchmark its GA configuration against the GPT default
on representative designs, recording
median/minimum/maximum, retry rate, alpha failures, identity/style acceptance,
and cost before freezing the production allowlist. Report p95 only for a run
with at least 20 successful calls.

### 8.4 Runtime image order and finished-design references

Customer pet first is an invariant, not configurable manifest data. The
application sends:

1. the customer pet; then
2. every path in `runtime.reference_assets`, in array order.

Prompts refer to the customer pet and reference assets by role, never “image 1”
or “image 2.” Bundle-v1 JSON Schema defines `reference_assets` with
`minItems: 1` and `maxItems: 4`; that limit covers finished-design references
only and is not instance-configurable. The primary reference is
`reference-design.png`. Optional supporting references are stored as
`reference-designs/reference-design-0002.png` and later numbers, matching the
manifest array order. Bundle construction applies EXIF orientation and
canonicalizes each finished reference to PNG so FE and the authoring run use
the same visual orientation.

The current MVP does not send a generated transformed-pet exemplar as a runtime
reference. Reusable pet-only prompting proved unreliable, so each transform
uses the customer pet followed by one or more selected finished-design
references. Adding an exemplar role later would change prompt semantics and
must be represented by a future schema/contract decision rather than inferred
from `qa/` files.

`runtime.input_image_order` makes the invariant explicit for inspection, while
`runtime.reference_assets` is the authoritative ordered path list. Changing a
reference image or its order creates a new bundle revision. No file below
`qa/` is a runtime input.

### 8.5 Preview and print art strategy

The MVP bundle ships both preview `art.png` and generator-prepared,
profile-specific `print/art.png`, plus both layouts. FE must not upscale preview
art or apply another art-upscale policy. It reuses `print/art.png` for every
order under that bundle and upscales only the approved customer transformed
pet. This matches the current target workflow and avoids repeated cost and
quality drift.

FE must explicitly accept this rule before implementation. A future
single-master contract would require a different manifest strategy or schema
version.

### 8.6 Layout-v2 and renderer semantics

Production bundles use composition layout schema version 2. Preserve pixel
coordinates, the nominal/minimum font sizes, uniform padding, RGBA name color,
horizontal alignment, fit policy, and relative font path. Keep
`model`, provider, prompt, API transport, reference order, and output
normalization out of the layout. The renderer begins at `font_size_px`, shrinks
only as needed to contain visible text ink inside the padded name box, and
rejects names that do not fit at `min_font_size_px`. It never grows a short name
beyond the nominal size. `bundle.json.runtime` is the sole authority for
pet-transformation routing. This separation lets a prompt/model revision reuse
byte-identical layout and font artifacts.

Only layout schema version 2 is accepted. All authoring attempts, fixtures,
preview layouts, and print layouts use this shape directly; there is no
compatibility branch or migration command. `layout-print.json` mechanically scales rectangle edges,
font sizes, and padding with round-half-up while preserving dimensionless
values. `bundle.json.renderer` pins renderer semantics version 2.

Renderer semantics are pinned by `bundle.json.renderer.version`. Because the
bundle contract has not been released, bundle schema version 1 directly requires
renderer version 2. After publication, a breaking renderer interpretation must
bump the bundle manifest's `schema_version`; the renderer version remains a
quick runtime compatibility check.

The publisher validates that preview and print layouts contain no runtime model
fields and that their geometry relationship is valid. A future multi-slot
layout introduces layout schema version 3 without changing catalog lookup.

### 8.7 QA fixture

`qa/input-pet.png` is an operator-reviewed, non-customer input that byte-matches
the selected representative attempt before canonical PNG conversion. The MVP
records a lightweight source/rights check operationally but does not require or
validate formal license evidence in the bundle.
`qa/transformed-pet.png` is that attempt's representative authoring output used to compose
`qa/golden-preview.png` and `qa/golden-preview-debug.png`. The publisher uses
the exact pet name from the selected print candidate and records it under
`bundle.json.provenance.qa_fixture`. These files are
review aids, not runtime references or cross-renderer pixel oracles. Automated
consumer diagnostics can replay the declared model call and cover role order,
request settings, manifest integrity, alpha, and geometry/renderer semantics;
the golden preview is compared by a human. If exact pixel equality later
becomes a product requirement, preview and print should use one shared server
renderer and the contract must define a machine-readable fixture separately.

### 8.8 Importer validation and failure behavior

The importer validates catalog identity, manifest identity, schemas, required
assets, dimensions, and hashes before creating a draft. It fails closed and
does not substitute another bundle revision, font, prompt, reference order, or
model. Reimporting the same `manifest_sha256` is idempotent; receiving a
different manifest digest for an existing `(template_id, bundle_revision)` is
an integrity error.

### 8.9 Private experiment contract

`experiment.json` is private authoring metadata and is not an FE input. Its
schema captures enough information to reproduce and compare a question without
turning the generator into a workflow service. A minimal record is:

```json
{
  "schema_version": 1,
  "experiment_id": "pet-gemini-v07",
  "kind": "pet",
  "design_id": "life-is-good",
  "product_profile_id": "blanket-king-9375x12375",
  "base_bundle_revision": 2,
  "parent_experiment_id": "pet-gemini-v06",
  "status": "evaluated",
  "inputs": {
    "prompt": {"path": "inputs/pet-transform-gemini.md", "sha256": "<sha256>"},
    "product_profile": {"path": "inputs/product-profile.json", "sha256": "<sha256>"},
    "references": [
      {"role": "finished_design", "path": "inputs/reference-design.png", "sha256": "<sha256>"}
    ]
  },
  "generation": {
    "provider": "gemini",
    "model": "gemini-3.1-flash-image",
    "transport": "interactions",
    "parameters": {"quality": "high", "output_format": "png", "background": "transparent"}
  },
  "generator": {"version": "<version>"},
  "created_by": "<operator>",
  "created_at": "2026-09-02T20:00:00Z"
}
```

The exact parameter object is provider-specific but must exclude credentials.
Local absolute source paths may appear in private authoring metadata; published
provenance contains bundle-relative paths or hashes only. Every attempt has a
`run.json` containing start/end time, the resolved experiment generation
configuration excluding prompt text and secrets, input hashes, output hashes,
elapsed time, outcome, and error category. Image calls remain stochastic.

Attempt creation is crash-safe. The tool exclusively reserves the attempt ID,
writes under an adjacent `.partial` directory with `status: running`, and only
atomically renames it to the final attempt path after outputs and final
`run.json` hashes are durable. A failed call retains an immutable failed record
with its sanitized error category or becomes cleanup-eligible partial state;
comparison and selection accept only `status: succeeded`. Reusing an existing
attempt ID is always an error.

Experiment status follows `draft -> evaluated -> selected` or
`draft/evaluated -> discarded`. Selection is established by `selection.json`,
not by editing a status string alone. Once an experiment is selected for a
published bundle, its record and attempts are immutable and permanently
protected from authoring cleanup; a later winner supersedes the selection but
does not rewrite that history.

### 8.10 Review and offline graduation contracts

Each `reviews/<kind>/<review-id>/` directory is one immutable review packet.
Its `evaluation.json` compares completed attempts under a versioned evaluation
protocol. The MVP does not automate visual judgment. It records the protocol
ID and SHA-256, explicit design/product/review identity, optional fixture-set SHA-256, bundle baseline, candidate attempt
statuses, hard image/layout gates, elapsed-time summary, and a pending human
review marker. An art, pet, or layout evaluation with at least two renderable
attempts also creates an immutable labeled contact sheet under `artifacts/` and records its path,
hash, byte count, and source candidate hashes in `review_artifacts`. The
application owner immediately records the winning candidate with
`record-decision --review <review-directory>`. The sibling `decision.json` contains
the chosen experiment/attempt or assembly, reviewer, timestamp, notes,
review ID, and evaluation SHA-256. A later change creates a new review ID; no
file in an earlier packet is overwritten. This keeps superseded
choices traceable and prevents final selection from drifting from the evidence
reviewed at each stage. Rich scoring, cost estimates, and provider/network
timing breakdowns are future extensions; the MVP must not imply they are
measured.

Review packets live under
`reviews/<kind>/<review-id>/`. Art and layout decisions identify both
an experiment and attempt; pet decisions identify the reusable runtime
experiment; assembly decisions identify the exact art-attempt, pet-experiment,
and layout-attempt product-relative paths. For example:

```json
{
  "schema_version": 1,
  "review_id": "art-prompt-v03-v04",
  "kind": "art",
  "design_id": "life-is-good",
  "product_profile_id": "blanket-king-9375x12375",
  "approved": true,
  "evaluation": {
    "path": "evaluation.json",
    "evaluation_sha256": "<sha256>"
  },
  "selected": {"experiment_id": "art-gpt-v04", "attempt_id": "attempt-0003"},
  "decision": {
    "selected_by": "application-owner",
    "selected_at": "<UTC timestamp>",
    "notes": "Best fixed-art fidelity across repeated attempts"
  }
}
```

A pet prompt/model comparison uses the same private, non-customer pet fixtures,
ordered references, product profile, and normalization policy. The tool checks
successful calls, PNG dimensions, usable alpha, and elapsed time; the reviewer
checks identity, style, pose/crop, unwanted background/text, and acceptable
latency. Its contact sheet groups attempts by input-pet hash and labels the
provider/model/quality configuration. With fewer than 20 successful calls, the
tool reports minimum, maximum, and median but not p95. A later assembly
evaluation with fixed art/layout remains required because isolated cutout
quality does not prove the final composition.

After a layout is available, a pet comparison may additionally pin an exact
art and layout attempt. The comparison renderer then creates full-size composed
previews plus a `pet-composition-comparison.png` sheet without making image API
calls. The evaluation records every composed preview path and hash. This is
supplemental visual evidence: the fixture-wide pet evaluation
remains the reliability/latency evidence and the selected combination still
requires assembly evaluation.

Each candidate has independent attempt-count, success-rate, hard-gate-rate,
latency, and fixture-coverage measurements. An optional attempt-ID prefix
excludes ad hoc smoke runs from a controlled benchmark. A pet candidate cannot
pass a fixture-backed evaluation unless all declared fixture hashes are covered;
coverage requires at least one successful hard-gate-passing result per fixture.
Operators additionally keep attempt counts equal when comparing latency.

Art comparison has the same machine gates for PNG geometry and alpha. The
repeatable experiment input supports both within-experiment stability review
and cross-experiment prompt/model review. The contact sheet groups labeled
attempts side by side on a transparency checkerboard; original images remain
the source for full-resolution judgment. The reviewer checks fixed-art
completeness, unwanted personalized content, style, and consistency. Layout
comparison checks layout-v2 and preview existence and creates a sheet labeling
font and name-box dimensions; the reviewer checks placement, representative
name containment, readability, and the ranked font choice. Each layout
experiment exposes and records the top 15 scored fonts from its immutable OFL
catalog snapshot. Ranking uses a hash-bound, operator-confirmed screenshot text
region and that region's exact visible wording. Similarity is not presented as
selection certainty, and medium/low confidence requires explicit confirmation.
The same private evidence may calibrate one fixed nominal font size from the
reference ink-fill ratio. This font-reference evidence remains private and is
not copied to the bundle; FE receives the resulting ordinary layout-v2 size and
retains shrink-only runtime behavior.
A changed official representative pet requires another layout experiment
because its bytes are pinned experiment input. During one editor session, the
operator may upload and switch among temporary transformed-pet QA fixtures
without changing that lineage. Those uploads remain local and are never bundle
assets; the calibration fixture records their hashes and the active fixture.
Different short, typical, and long QA names may be tested in the same session;
attempt records preserve the tested pet hashes, exact final test text, applied
font size, and fit status. Every selected art attempt, pet
experiment, and layout attempt must be covered by its own passing evaluation in
addition to a passing assembly evaluation and print finalist. `graduate`
accepts the four immutable review directories and verifies that each sibling
decision names the exact selected component or assembly.

The normal `prepare-print` path also accepts the art, pet-runtime, and layout
decision records. It resolves the chosen art/layout attempts and obtains the
representative pet attempt from the selected layout experiment, which already
pins the exact pet bytes used for composition QA. It verifies those paths and
run hashes before creating the finalist. Explicit art/pet/layout attempt flags
remain an advanced diagnostic mode, but all three must be supplied together
and cannot be mixed with decision inputs. This avoids accidental cross-winner
mixing without removing deliberate experimentation.

Assembly and composed-pet evaluations obtain their QA name from the immutable
layout attempt record. They never substitute a generic placeholder. Short names
remain at the authored nominal size; longer names can shrink, so preserving the
exact fixture is required for reproducible pixels and fit-status evidence.

The application owner records the winning assembly in
`graduations/<graduation-id>/selection.json` before the generator allocates a
production revision. `graduate` derives the exact art
attempt, pet runtime, and layout attempt from the succeeded print candidate and
validates them against the four review packets. Reviewer identity and notes are
recorded by this command, eliminating a reusable free-form approval file.
Operators do not repeat component source paths on the command line:

```json
{
  "schema_version": 1,
  "selection_id": "life-is-good-blanket-king-20260902",
  "graduation_id": "life-is-good-blanket-king-20260902",
  "design_id": "life-is-good",
  "product_profile_id": "blanket-king-9375x12375",
  "selected": {
    "art": {"experiment_id": "art-gpt-v04", "attempt_id": "attempt-0003", "artifact_sha256": "<sha256>"},
    "pet_runtime": {"experiment_id": "pet-gpt-v07", "experiment_sha256": "<sha256>"},
    "layout_font": {"experiment_id": "layout-v03", "attempt_id": "attempt-0001", "layout_sha256": "<sha256>", "font_sha256": "<sha256>"}
  },
  "sources": {
    "art_attempt": "experiments/art/art-gpt-v04/attempts/attempt-0003",
    "pet_experiment": "experiments/pet/pet-gpt-v07",
    "layout_attempt": "experiments/layout/layout-v03/attempts/attempt-0001",
    "print_candidate": "print-candidates/print-finalist-20260902-01"
  },
  "component_qa": {
    "art": {"review_id": "art-20260902", "evaluation_path": "reviews/art/art-20260902/evaluation.json", "evaluation_sha256": "<sha256>", "status": "passed"},
    "pet": {"review_id": "pet-20260902", "evaluation_path": "reviews/pet/pet-20260902/evaluation.json", "evaluation_sha256": "<sha256>", "status": "passed"},
    "layout": {"review_id": "layout-20260902", "evaluation_path": "reviews/layout/layout-20260902/evaluation.json", "evaluation_sha256": "<sha256>", "status": "passed"}
  },
  "compatibility_qa": {"review_id": "assembly-20260902", "evaluation_path": "reviews/assembly/assembly-20260902/evaluation.json", "evaluation_sha256": "<sha256>", "status": "passed"},
  "review_decisions": {
    "art": {"review_id": "art-20260902", "decision_path": "reviews/art/art-20260902/decision.json", "decision_sha256": "<sha256>", "selected_by": "<application-owner>", "selected_at": "<timestamp>", "notes": "<reason>"},
    "pet": {"review_id": "pet-20260902", "decision_path": "reviews/pet/pet-20260902/decision.json", "decision_sha256": "<sha256>", "selected_by": "<application-owner>", "selected_at": "<timestamp>", "notes": "<reason>"},
    "layout": {"review_id": "layout-20260902", "decision_path": "reviews/layout/layout-20260902/decision.json", "decision_sha256": "<sha256>", "selected_by": "<application-owner>", "selected_at": "<timestamp>", "notes": "<reason>"},
    "assembly": {"review_id": "assembly-20260902", "decision_path": "reviews/assembly/assembly-20260902/decision.json", "decision_sha256": "<sha256>", "selected_by": "<application-owner>", "selected_at": "<timestamp>", "notes": "<reason>"}
  },
  "print_qa": {"print_candidate_id": "print-finalist-20260902-01", "print_candidate_sha256": "<sha256>", "status": "passed"},
  "decision": {
    "selected_by": "<application-owner>",
    "selected_at": "2026-09-02T21:00:00Z",
    "notes": "Best accepted identity/style score with acceptable measured latency"
  }
}
```

The pet runtime selection pins a prompt, provider, model, API transport,
reference order, output policy, and normalization
policy; it does not pin one QA pet output as the production runtime result. The
bundle builder validates that all selected components share the same design and
product profile, that layout input art hashes match the selected art, and that
the immutable print finalist covers the exact selected assembly.

A selection may combine winners from separate experiments only after assembly
compatibility QA renders representative successful outputs from the selected
pet runtime into the selected layout and confirms alpha, visible-pet bounds,
containment, anchor behavior, and name placement. Reusing a layout authored
with a different pet experiment is allowed only when this compatibility QA
passes and its evaluation hash is recorded. A selection is superseded by a new
selection record, never edited after publication.

`pawmarvel-author trace --graduation <directory>` resolves the selection's
product-relative paths, verifies the four decision/evaluation hashes, checks
the selected source paths, and prints one lineage report. Absolute paths are
diagnostic output only; stored lineage remains portable within its product
root. FE does not consume reviews or graduations and continues to depend only
on the immutable bundle contract.

### 8.11 Dependency and invalidation rules

Selective iteration follows this minimum dependency contract:

| Change | May reuse | Must regenerate or revalidate |
| --- | --- | --- |
| Pet prompt, runtime model, API transport, or provider | Preview/print art, composition-only layout-v2, font | Representative pet attempts, latency/quality evaluation, alpha policy, assembly compatibility QA, golden previews |
| Runtime reference bytes, roles, or order | Preview/print art, layout, font | Representative pet attempts and golden previews |
| Art prompt or preview art bytes | Pet runtime experiment | Preview art, print art, layout compatibility, preview and print QA |
| Layout or font selection | Art and representative pet bytes | Preview, `layout-print.json`, name containment, print render |
| Art upscale backend or parameters | Preview art and layout | Print art, print-layout validation, print render |
| Product profile or profile geometry | Design references only | All profile-dependent art, layout, print assets, and QA under a new `product_profile_id` when meaning or dimensions change |

A model or pet-prompt upgrade is therefore allowed to start from a published
bundle revision, reuse its art/layout/font bytes by verified hash, and create
only new pet experiments and downstream QA. If selected, packaging still emits
a complete new atomic bundle revision. A new print candidate uses
`--reuse-template-from` to copy the prior candidate's print art, print layout,
profile, and font only after their recorded hashes and selected art/layout
attempts match; it upscales only the new representative pet. An art-only
improvement may reuse the selected pet runtime contract, but must regenerate
print art and revalidate the layout because changed pixels may alter usable
placement geometry.

For art iteration, first run repeated attempts within one experiment to measure
stability and latency, then compare separate prompt/quality experiments in one
evaluation and contact sheet. For pet-runtime iteration, benchmark every
prompt/model candidate against the identical fixture set and attempt count,
then evaluate both cutouts and the fixed-layout composition. For layout, keep
art/pet inputs fixed while comparing font, name-box sizing, and placement
attempts. Run print upscaling and 100%-zoom
inspection only for the short-listed finalist or finalists before selection.
This avoids paying print-preparation cost for every failed prompt while still
catching upscale-specific artifacts before release.

### 8.12 Prompt authoring scope

For the MVP, “generate the prompt” means the offline template operator authors
or edits a design-specific prompt file and the tool snapshots, hashes, runs,
evaluates, compares, selects, and packages it. The tool does not automatically
synthesize prompts with another model. Automated prompt synthesis would need
its own model provenance, source contract, evaluation loop, and cost controls
and is deferred.

An experiment is a versioned candidate configuration. It may change several
fields when the goal is to select the best complete runtime configuration, such
as comparing Gemini `interactions` against OpenAI `images.edits`. When causal attribution is the
goal, the operator creates a series that changes one variable at a time. The
operator records the intended comparison in the experiment ID and review notes;
the MVP schema does not add a second change-tracking vocabulary.

## 9. Catalog bundle authoring and handoff workflow

The operations guide is the procedural source. Authoring selection happens
before FE import; production activation happens after import:

1. **Register sources.** Choose a stable design ID and store the primary and
   optional supporting references privately.
2. **Select a product profile.** Confirm preview geometry, print dimensions,
   format, color space, bleed, safe area, and vendor status.
3. **Create baseline experiments.** Create separate art, pet, and layout
   experiment records. Snapshot the exact prompt, references, profile, provider,
   model, API transport, and generator revision before executing an attempt.
4. **Generate immutable attempts.** Generate preview art and representative pet
   attempts under new attempt IDs. Never replace an earlier comparison output.
5. **Author layout/font attempts.** Select the OFL font, save `layout.json`, and
   render preview/debug output against a pinned art and pet attempt.
6. **Evaluate.** Compare like-for-like attempts with the bounded scorecard in
   section 8.10. Failed experiments may be marked discarded; they do not consume
   bundle revisions.
7. **Iterate selectively.** Fork only the art, pet runtime, or layout/font
   experiment being changed. Follow section 8.11 invalidation rules and create
   new downstream attempts where required.
8. **Prepare the print finalist.** Pass the recorded art, pet-runtime, and
   layout decisions; the tool resolves their attempts and the layout-pinned
   representative pet, upscales them, and emits a hash-bound finalist. Use the
   explicit-attempt mode only for diagnostics. A failure loops back to the
   affected experiment.
9. **Select offline winners.** The application owner approves the succeeded
   print finalist. The tool infers its exact component sources and validates
   them against all stage decisions before writing `selection.json`; source
   paths are not entered twice. Selection does not activate production.
10. **Build the selected bundle.** Allocate the next generator revision only
    now, snapshot the profile and selected prompt/runtime contract, generate
    `bundle.json`, and validate all hashes and provenance.
11. **Run contract QA.** Validate schemas and geometry; visually compare the
    golden preview. A prompt/model-only revision reruns the bounded pet test but
    reuses verified unchanged art/layout assets.
12. **Create the local release catalog.** List the exact immutable bundles
    intended for one handoff with each `manifest_sha256`, and validate the
    catalog plus every referenced local asset.
13. **Review and upload immutably.** Inspect the dry-run publication plan, then
    explicitly execute it. Refuse a different object at an existing key using
    conditional creation (`If-None-Match: *`). Upload assets to their final
    unreferenced prefixes, verify checksum and byte size, upload each
    `bundle.json` after its assets, and upload `catalog.json` last.
14. **Write a publication receipt as part of successful publication.**
    After remote verification, the publisher records the selection, selected
    component IDs, manifest hash, release ID, and transfer location under the
    private authoring product. Retrying the same publication accepts an exact
    existing receipt, closing the revision-allocation gap after local exchange
    cleanup. The standalone receipt command is recovery/backfill only.
15. **Import as draft.** FE imports idempotently and records the generator
    identities without minting a competing template version.
16. **Review and activate.** FE/application review, activation, disablement, and
    rollback remain application operations.
17. **Clean authoring storage.** After publication, run protected-reference
    checks and remove only expired scratch, failed, or discarded experiments.
    Never age-delete drafts or use authoring cleanup to retire an application
    template or delete a release.

For another product variation, reuse design sources but create a distinct
bundle under the other product profile. Do not reuse coordinates across
profiles except through the validated mechanical scaling flow.

## 10. MVP implementation boundaries

The code is split by lifecycle and asset lifetime:

- `pawmarvel-generate` performs one image-model operation;
- layout and rendering modules own geometry, font selection, and deterministic
  composition;
- `pawmarvel-upscale-template` owns reusable print art/layout preparation;
- `pawmarvel-upscale-pet` owns one transformed-pet print preparation;
- `pawmarvel-pipeline` is replaceable scratch/debug orchestration and has no
  publication option;
- `pawmarvel-author` owns immutable product-scoped experiments, attempts,
  evaluations, print candidates, selections, publication receipts, and safe
  cleanup;
- `pawmarvel-bundle` accepts only a reviewed selection and creates one complete
  immutable revision; and
- release-catalog validation/construction and S3 transport are separate modules;
  the thin `pawmarvel-catalog` CLI exposes them and publishes only through an
  explicit, dry-run-by-default subcommand.

This boundary permits each step to evolve without making the scratch pipeline
a production control plane. Production code has no replace-in-place bundle
mode. Before FE integration, both engineers must still
freeze push/import, mandatory `bundle.json` consumption, runtime reference
roles/order, and renderer/name semantics.

### Implemented MVP contract

1. **Use push/import and mandatory manifest consumption.** Freeze the importer
   boundary before FE integration.
2. **Provide private authoring schemas.** Check in `experiment-v1`, `attempt-v1`,
   `evaluation-protocol-v1`, `evaluation-v1`, `print-candidate-v1`,
   `selection-v1`, and `publication-receipt-v1` JSON Schemas. Validate IDs,
   hashes, component kinds,
   states, parent lineage, hard gates, and design/profile compatibility.
3. **Provide a lightweight authoring lifecycle CLI.** Use one focused
   `pawmarvel-author` command with `create-experiment`, `run-attempt`,
   `benchmark`, `compare`, `prepare-print`, `graduate`, `trace`, `set-status`,
   `record-publication`, and `cleanup` subcommands. `benchmark` creates the declared fixture attempts;
   `compare` reads completed attempts and never makes a paid call. The command
   orchestrates the existing focused CLIs; it does not duplicate image
   generation or rendering. A small independent `comparison_artifacts.py`
   module creates deterministic art, pet, and layout contact sheets from
   immutable attempt outputs without provider access or winner-selection logic.
4. **Keep experiment attempts non-destructive.** Authoring exclusively
   reserves IDs, writes `.partial` state, and
   atomically completes a new attempt directory with resolved input/output
   hashes instead of replacing an earlier result. Preserve replacement only
   under explicit `scratch/`. Selective rerun validates the dependency rules in
   section 8.11 and explains every invalidated downstream artifact.
5. **Emit a bundle manifest and profile snapshot.** `pawmarvel-bundle` writes
   `bundle.json` and `product-profile.json`, including hashes, runtime
   role-specific references, output size, renderer semantics, generator
   revision, selected component/evaluation lineage, both prompt paths/hashes,
   and print-art/layout derivation provenance.
6. **Require reviewed selection for publication.** Bundle creation consumes a
   validated `selection.json`, resolves the selected immutable attempts, checks
   art/layout/profile hashes and pet-runtime/layout assembly compatibility, and
   allocates a revision only after those checks pass. It may copy unchanged
   bytes from a base revision after verifying hashes; the output remains a
   complete self-contained bundle.
7. **Keep delivered revisions immutable.** Require
   `--bundle-revision`, including `--bundle-revision next`; publish to
   `<template-id>/vNNNNNN`; require monotonic allocation and conditional
   creation, and do not allow `--force` for release output. `next` scans both
   local staging revisions and retained publication receipts. Retain overwrite
   only in explicit scratch mode.
8. **Provide release build, validation, and S3 publication.**
   `pawmarvel-catalog` supports `validate`, `build-release`, and a separate
   dry-run-by-default `publish-s3`; catalog entries include the bundle-manifest
   digest. S3 publication accepts only a locally validated canonical release,
   performs conditional writes and checksum/size verification, and publishes
   the catalog last. Executed publication requires the authoring root and then
   writes idempotent per-bundle publication receipts. It does not own production
   activation, rollback, or a database.
9. **Check in and execute shared production JSON Schemas.** Include release-catalog-v2,
   bundle-v1, composition-only layout-v2, and product-profile-v1 schemas. Layout-v1
   is deliberately unsupported. CI checks every schema as Draft 2020-12 and
   validates generated contract documents against it. FE uses the same schemas
   and the reviewed S3 conformance fixture to create matching types/tests.
10. **Publish QA conformance aids.** Include an operator-reviewed, non-customer input pet,
    its representative transformed output, golden preview, and debug preview.
    Validate manifest/geometry semantics in code and use human visual review
    rather than stochastic image pixel thresholds.
11. **Declare runtime policy.** Capture provider/model/API transport, closed
    provider request parameters, exact reference order, provider-specific output
    normalization and alpha requirements, name validation, renderer behavior,
    and preview/print art ownership in the manifest. Enforce one-to-four runtime
    references in bundle-v1 schema.
12. **Use safe cleanup.** `pawmarvel-author cleanup` defaults to dry-run,
    deletes only failed/discarded state by default, understands retention age,
    resolves selections, rejects unsafe roots/retention values, and refuses to
    delete draft or selected paths. It never operates on exchange or S3 paths;
    production retirement remains outside this command.

### Keep unchanged

- Keep the Python package and focused generation/rendering CLIs; the authoring
  lifecycle command only coordinates and records them.
- Keep the browser layout editor and Pillow reference renderer.
- Keep design-specific prompts and ordered references.
- Keep preview and print layouts as separate files.
- Keep the local 40-font OFL catalog and publish only the selected font.
- Keep deterministic upscale for flow testing and optional Bria experiments.
- Do not add a web authoring app, relational catalog database, or shared asset
  service.

### Simplification

Generated bundles are not checked into Git. Contract tests construct ephemeral
bundles from small fixtures. A replace-in-place `bundles/catalog.json` workflow
is unsupported. Production uses immutable
attempt directories, reviewed selections, versioned bundle revisions, and
release catalogs. Do not create a database, independently deployed component
registry, or separate production version counter for every prompt; authoring
IDs plus hashes and one atomic `bundle_revision` are sufficient.

## 11. Alignment checkpoints before FE trial

Resolve these in order during kickoff:

| Priority | Decision | Recommended MVP answer | Status |
| --- | --- | --- | --- |
| 1 | Who owns activation and rollback? | Application draft/publish flow; generator delivers immutable bundles and release catalog | Accept at kickoff |
| 2 | Which revision is authoritative? | Generator `bundle_revision`; importer records it verbatim and uses a differently named internal ID if needed | Accept at kickoff |
| 3 | Does FE adopt `bundle.json`? | Yes, mandatory for the dual-art contract; declining it reopens print-art architecture | Accept at kickoff |
| 4 | Which runtime references are sent? | Customer pet first, then one-to-four bundle-declared finished-design references in manifest order | Proposed decision |
| 5 | Which model paths are in MVP? | GPT Image 2 is the default for art and pet transformation; Gemini is an optional measured candidate and cannot be selected until it passes identity/composition/alpha gates | Product and FE acceptance required |
| 6 | Who creates print art? | Generator ships profile-specific high-resolution art; FE upscales only customer pet | Proposed decision |
| 7 | How are runtime and QA references separated? | `runtime.reference_assets` names only finished-design assets; nothing under `qa/` is a runtime input | Proposed decision |
| 8 | How is renderer conformance tested? | Machine-check geometry/semantics plus one human golden review | Proposed decision |
| 9 | Where do model/print operations run? | Trusted application backend/job, not browser | Proposed decision |
| 10 | How are artifacts accessed? | Application server imports an immutable S3 release key and fetches all bundle assets during MVP | Proposed decision |
| 11 | How are products mapped? | Commerce variant stores `template_id`; orders pin generator revision and application deployment ID | Proposed decision |
| 12 | What are name rules? | Bundle-declared NFC/code-point policy, Unicode letters/marks/numbers plus space/apostrophes/hyphen, per-bundle maximum | Implemented contract; FE acceptance required |
| 13 | What makes a profile vendor-ready? | Explicitly confirm format, alpha/background, color profile, DPI, bleed, safe area, dimensions, and byte limit | Vendor/operations input required |
| 14 | Who selects offline experiment winners? | Application owner records the decision; template operator prepares evidence and packages the selection | Accept at kickoff |
| 15 | Can a selection mix component experiments? | Yes, after design/profile/art hashes and pet-runtime/layout assembly compatibility QA pass | Accept at kickoff |
| 16 | What is the initial authoring retention period? | Keep selected/published lineage; dry-run deletion of discarded attempts older than 30 days | Operations input required |
| 17 | Where does runtime routing live? | Only in `bundle.json.runtime`; production layout-v2 contains geometry/font data only | Accept at kickoff |
| 18 | What is the MVP storage boundary? | Local/shared POSIX for private experiments; ignored local exchange for staging; Amazon S3 only for reviewed immutable releases | Accept at kickoff |
| 19 | Does the MVP synthesize prompts automatically? | No; an operator authors prompts and the tool versions, evaluates, selects, and packages them | Accept at kickoff |
| 20 | What image-rights evidence blocks an MVP release? | A lightweight operator source/rights check and non-customer QA pet; formal license evidence is deferred and is not represented in bundle-v1 | Accepted MVP tradeoff |

No bundle is production-ready until decisions 1–8, 14–15, and 17–20 are
recorded in the contract README and represented by one reviewed S3
conformance fixture. Decision 16 blocks automatic deletion, not bundle
generation.

## 12. MVP acceptance criteria

- Release catalog and every bundle validate against shared schemas.
- Every catalog entry hashes `bundle.json`, and every manifest hashes all other
  bundle assets.
- Every delivered bundle path is immutable.
- Every experiment attempt is immutable; replacement is confined to explicit
  scratch paths.
- Attempt creation is crash-safe, and comparison/selection accepts only
  successfully finalized attempts.
- Every selected bundle has a validated `selection.json` and component
  provenance linking it to exact experiment inputs and attempts.
- Production layout-v2 contains no provider/model routing; `bundle.json.runtime`
  is the sole runtime authority.
- Mixed component selections include passing assembly compatibility QA and an
  evaluation hash.
- Print art and print layout record their source hashes, tool/backend versions,
  parameters, and output hashes.
- A pet-prompt/model-only experiment can reuse art/layout/font assets while
  rerunning the required pet evaluation and golden previews.
- Cleanup defaults to dry-run and refuses graduated/published lineage and all
  exchange or release artifacts. Unselected reviews and print candidates remain
  intentionally cleanup-eligible after operator review.
- Successful publication writes a local receipt used by protected cleanup.
- No experiment or generation command writes to S3; publication requires an
  explicit reviewed-release command and is dry-run by default.
- S3 publication verifies SHA-256 and byte size, never replaces a conflicting
  object, and makes the release discoverable only by uploading its catalog
  last.
- The same design works with multiple product profiles without collision.
- Reimport is idempotent and conflicting bytes under one revision are rejected.
- The importer preserves generator identity and creates no ambiguous second
  template version.
- The consumer sends the customer pet first and no more than four ordered
  reference assets afterward.
- Runtime finished-design references and QA transformed-pet/golden files are
  separate roles and paths.
- Production bundles explicitly declare an allowlisted runtime provider/model
  and its output-normalization policy.
- FE uses bundled print art and does not independently upscale preview art.
- Preview and print use the same exact bundle revision and customer values.
- Automated conformance checks geometry; a human compares the golden preview.
- The operator completes the lightweight image source/rights check; bundle-v1
  intentionally has no formal image-license evidence requirement.
- Production Git contains no customer images, authoring work, or production
  bundle payloads.
- Runtime secrets are absent from bundles, browser code, and logs.
