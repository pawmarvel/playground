# Static Print Design Workflow and FE Contract

Status: MVP architecture and implemented contract source of truth.

Audience: reviewers, static-design operators, FE/application engineers, and tooling maintainers.

## 1. Outcome and system boundary

This product line publishes static, non-personalized print designs. The offline
tool owns design intake, review, subject variants, exact product rendering,
versioning, validation, and S3 publication. FE consumes public catalog and
bundle artifacts only. It never reads the operator workspace or waits for an
offline process during a customer request.

```text
local candidates
      |
      v
reviewer feedback -> operator decision -> subject variants -> product evidence
                                                               |
                                                               v
FE discovery catalog <- publish <- immutable release <- immutable bundles
```

The public unit is one `(design_id, product_profile_id, version_id)` bundle.
The same design may cover many products, and every design/product pair advances
independently.

### 1.1 FE discovery contract

The canonical subject and bundle identity uses `--` as its field delimiter:

```text
<seed-design-id>--subject-<subject-id>--<product-profile-id>
```

The stable FE discovery object is:

```text
s3://<bucket>/<prefix>/catalog.json
```

It provides deterministic latest-version discovery and product retirement. FE
must not list or merge release objects to infer current state. Exact bundle
paths are immutable and may be pinned. The catalog path and
`pawmarvel-static-catalog-v1` schema are the integration contract that must be
aligned with FE before launch. Only bundles referenced by this catalog are
valid FE inputs.

## 2. Personas, ownership, and dependencies

| Persona | Owns | Reads | Must not depend on |
| --- | --- | --- | --- |
| Reviewer | votes and actionable feedback for active seed pixels | reviewer gallery, source image, transparency backgrounds, aggregate vote context | S3, product profiles, authoring folders, publication credentials |
| Operator | offline decisions and artifacts from intake through release; subject/product iteration; retirement | reviewer feedback, local provenance, visual evidence, product profiles, S3 discovery status | FE deployment or commerce configuration |
| FE/application engineer | catalog import, latest or pinned selection, commerce mapping, activation/rollback, preview display, exact vendor target selection | stable catalog, immutable manifest, preview and print PNGs | local operator paths, SQLite, prompts, rejected designs, operator availability |

The only runtime dependency from FE to the operator domain is publication of a
valid S3 catalog update. Once published, FE discovery and selection are
independent. FE owns whether an active catalog entry is actually offered in the
store. Operator `retired` status removes it from default discovery but does not
delete immutable bytes or forcibly disable an already pinned FE mapping.

## 3. Identity and automatic versions

```text
design_id  = <seed-design-id>--subject-<subject-id>
bundle_id  = <design-id>--<product-profile-id>
bundle key = (bundle_id, version_id)
version_id = vNNNNNN
```

At first intake, the operator tool uses a low-detail vision request to propose a
theme-descriptive `seed-design-id`, then resolves collisions locally with a
numeric suffix. `intake.json` pins the resolved source path to that selected ID,
so a refresh of the same candidate never changes its identity or incurs another
naming request. The ID becomes immutable once review begins; the vision model is
not an FE dependency and its output is never used as a display label.

Splitting a bundle ID on `--` produces exactly three fields: seed design ID,
`subject-<subject-id>`, and product profile ID. FE should still prefer the
explicit `design_id` and `product_profile_id` manifest/catalog fields and use
the delimiter rule for validation and routing, rather than deriving business
labels from filenames.

`subject_id` is not generated from a display label. It is the explicit,
immutable kebab-case key in `catalog/static-design-subjects.json`; duplicate or
invalid subject IDs and priorities fail catalog loading. Changing a subject ID
creates a different product-variant identity; it is not a display-label edit.
FE uses that exact key for product variant selection.

FE treats every ID as opaque. The server—not the browser or operator—assigns
the next version independently for each `(design_id, product_profile_id)`. It
advances past the highest local candidate and published S3 version under the
same key. Batch generation never creates one shared version across products.

Published bundle and release keys are immutable. Any changed source pixels,
crop, product specification, preview, or print target require a new version of
only the affected bundle. A successful publication of the tool-assigned next
version becomes that product's catalog `latest_version_id` automatically.

## 4. Operator-supported workflow

### 4.1 State overview

```text
ACTIVE SEED REVIEW
  pending feedback
      |-- abandon ------------------------------> ABANDONED SEED
      |                                              |
      |<---------------- restore --------------------+
      |
      +-- graduate
             v
GRADUATED WORKFLOW
  subject missing -> generated -> approved -> product missing
       ^                |             |              |
       |                +-- rework ---+              v
       +-------- regenerate                    product candidate
                                                    |
                                  reject/iterate <--+--> publish
                                                            |
                                                            v
RELEASED INVENTORY
  active product -> new version / add product / retire
                         ^                         |
                         +------- reactivate ------+
```

Seed state, subject state, and product state are deliberately separate. A seed
can have approved and rework subjects at the same time. One subject can have a
released mug, a ready-to-publish shirt, and a missing blanket at the same time.

### 4.2 Decision checkpoints and evidence

| Checkpoint | Evidence shown before decision | Individual action | Batch action | Persisted identity |
| --- | --- | --- | --- | --- |
| Active seed | exact source on raw/blue/white/custom backgrounds; alpha report; reviewer votes/comments; intake provenance | graduate or abandon | graduate/abandon filtered selection | seed source SHA-256 |
| Subject preview | seed beside generated subject; generation provenance; alpha policy; current decision/note | approve, rework, abandon, regenerate | generate or decide selected seed/subject pairs | subject source SHA-256 |
| Product preview and print | exact profile preview; every target PNG; dimensions; transparency; source/version/profile metadata | generate next version, select/publish | generate or publish selected tuples | `(design_id, profile_id, version_id)` plus hashes |
| Released inventory | latest/pinned versions, release ID, active/retired status, preview and target evidence | add product, improve, retire/reactivate | selected generation/publication/lifecycle change | immutable S3 key plus catalog state |

Completed controls, product-coverage evidence, and secondary evidence are
collapsed by default; the winning visual remains visible or expandable. The
next incomplete action remains prominent. Each batch control exposes its
selected IDs and Cartesian work-unit count before execution. Long-running
operator batches record durable per-work-unit progress and a rolling event log
in the workspace SQLite database. The common report exposes completed/published,
pending, error, and warning counts plus the latest timestamped artifact or S3
output. Live polling is bounded to the 100 newest operations so a long-lived
workspace does not make the GUI progressively slower; exact-ID recovery still
loads any older durable operation directly. Errors identify the affected item
and include corrective guidance for common credential, path, conflict, and
network failures. This applies to seed
transitions, subject decisions/generation, product generation/lifecycle, bundle
validation, and S3 publication. Every generated work unit writes to a sibling
staging directory and is atomically promoted only after its images and metadata
are complete. Startup reconciliation removes staging debris, restores
interrupted subject replacements, quarantines invalid product directories, and
exposes Resume/Cleanup actions where supported.

A workspace is single-writer: a process-held lock rejects a second server on
the same work root. Resume provides at-least-once handling for an external paid
image request that was accepted but whose response was lost during a process or
machine failure; all locally committed work units are exactly-once by immutable
version identity and atomic promotion.

### 4.3 Reviewer flow

Reviewers see only active seeds. They can filter the collection, inspect one
image on multiple backgrounds, choose `approve`, `changes`, or `reject`, and
write feedback. Edits across several cards are queued and saved in one batch.
Feedback is upserted per `(design_id, source_sha256, reviewer)`; saving against
pixels changed since page load is rejected with a refresh instruction.

Reviewer votes are advisory. Only the operator can move seed folders or record
subject/product/publication decisions.

### 4.4 Operator GUI organization

The central operator page has four inventory tabs:

1. **Active review** — votes, comments, source evidence, batch
   graduate/abandon.
2. **Graduated workflow** — a checkpoint table with reference, subject,
   product evidence, and the next action.
3. **Released** — the complete product-coverage matrix for every published
   subject, including missing, ready, active, retired, and update-ready states.
4. **Abandoned** — rejected seeds with batch restore.

The sticky batch tray applies the same operation to a filtered selection, but
never changes artifact identity. Row controls provide the same operation for
one seed, subject, or product. S3 failure is represented as
`release-status-unavailable`; the GUI never guesses from local receipts.

## 5. Supported iteration scenarios

### 5.1 Improve an existing subject

Regenerate only the subject variant with an explicit instruction. Its new
digest invalidates the previous subject approval and excludes product outputs
derived from the old digest from current release selection. After approval,
generate new versions only for affected products. Published versions remain
available for rollback.

### 5.2 Improve one design/product bundle

Choose **Generate new version** for that product. Automatic version allocation
does not advance sibling products. Review exact preview and print targets, then
publish that candidate. The catalog moves `latest_version_id`; FE may adopt
latest or keep its existing pin.

### 5.3 Add products to a released design

The Released product matrix always lists every configured profile. Select
missing profiles individually or in a batch, generate local artifacts, review
each target, and publish accepted candidates. Existing bundles and history are
not rewritten.

### 5.4 Vendor or product-spec update

Edit the tracked product profile, restart the server, and generate a new
version for each affected subject/product. Do not replace an existing bundle.
Use a changed target ID when the vendor contract represents a materially
different output target.

Private generation records include a canonical digest of the semantic product
profile, so a spec edit under the same profile ID cannot leave an older local
candidate looking current. This authoring safeguard does not change the public
bundle schema.

### 5.5 Retire or restore inventory

Retirement is non-destructive. The operator selects one or several published
products, records an optional reason, and conditionally updates the stable S3
catalog to `retired`. Versions remain pinned and auditable. Reactivation sets
the same entry to `active`; publishing a deliberately selected new version also
reactivates that bundle. Physical S3 deletion is outside the MVP GUI.

## 6. Private operator artifact model

```text
<external-review-collection>/<candidate>.png

work/
  static-design-inputs/<seed-design-id>/
    source-design.png
    intake.json              # pins source path -> unique semantic seed ID
  static-graduated-designs/<seed-design-id>/
    source-design.png
    <subject-id>/
      source-design.png
      subject-generation.json
  static-abandoned-designs/<seed-design-id>/
    source-design.png
    intake.json
  static-authoring/<seed-design-id>/<subject-id>/<product-profile-id>/<version-id>/
    generation.json
    <design-id>-preview.png
    profile-previews/<product-profile-id>.png
    <design-id>--<product-profile-id>[--<target-id>].png
  static-reviews.sqlite3
  static-exchange/
    catalog.json                         # non-authoritative mirror of S3 catalog
    releases/<release-id>/catalog.json
    publications/<release-id>.json
    retirements/<retirement-id>.json     # obsolete-artifact plan and receipt
  static-retired-artifacts/<retirement-id>/
    ...                                  # quarantined legacy local artifacts
```

The workspace is private and mutable. SQLite contains reviewer feedback and
operator subject decisions keyed to image digests. The local `catalog.json`
mirror and publication receipts are audit/debug evidence, not FE input or
publication truth; S3 remains authoritative.

The private source hierarchy is `<seed-design-id>/<subject-id>/`. Its public
design ID is `<seed-design-id>--subject-<subject-id>`. Subject IDs use forms such
as `dog-dachshund`, `cat`, or `cat-maine-coon`.

## 7. Public S3 artifact contract

```text
s3://<bucket>/<prefix>/
  catalog.json                                      # stable FE discovery index
  catalog-history/<timestamp>-<sha-prefix>.json    # immutable operator recovery
  bundles/<bundle-id>/<version-id>/
    bundle.json                                     # immutable manifest
    preview.png
    print/<target-id>.png
  releases/<release-id>/catalog.json                # immutable publication event
```

### 7.1 Stable discovery catalog

`catalog.json` validates against `static-catalog-v1.schema.json`. It contains
one record per `bundle_id`:

```json
{
  "schema": "pawmarvel-static-catalog-v1",
  "updated_at": "2026-10-15T20:00:00Z",
  "products": [
    {
      "bundle_id": "holiday-patrol--subject-dog-dachshund--coffee-mug-11oz",
      "design_id": "holiday-patrol--subject-dog-dachshund",
      "product_profile_id": "coffee-mug-11oz",
      "status": "active",
      "latest_version_id": "v000003",
      "versions": [
        {
          "version_id": "v000003",
          "release_id": "2026-10-15.002",
          "published_at": "2026-10-15T19:55:00Z",
          "manifest_path": "bundles/holiday-patrol--subject-dog-dachshund--coffee-mug-11oz/v000003/bundle.json",
          "manifest_sha256": "<64 lowercase hex characters>"
        }
      ]
    }
  ]
}
```

Versions are unique and ascending; `latest_version_id` equals the last version.
The publisher derives version inventory from immutable S3 release catalogs,
preserves explicit lifecycle state from the current catalog, and replaces the
stable object with an ETag precondition. After a successful replacement it
writes that committed state to `catalog-history/` under an immutable,
content-derived key. A losing concurrent writer therefore cannot leave a false
recovery state, and concurrent modification fails instead of losing another
operator's publication.

The operator may rebuild the catalog without creating a release. Rebuild uses
the stable catalog when present, otherwise the newest immutable catalog-history
snapshot, and finally immutable release catalogs when neither index exists.
This preserves retirement/reactivation decisions during recovery whenever a
committed catalog snapshot exists.
`catalog-history/` is operator recovery data; FE must read `catalog.json`, not
list or merge history snapshots.

### 7.2 Deterministic FE selection

To use latest:

1. Fetch `<prefix>/catalog.json` and validate its schema.
2. Find the exact `(design_id, product_profile_id)` record.
3. Require `status == active` for default discovery.
4. Select the version equal to `latest_version_id`.
5. Fetch `manifest_path`; verify `manifest_sha256`.
6. Validate `bundle.json`, display its preview, and use an exact target.

To pin:

1. Store `(bundle_id, version_id, manifest_path, manifest_sha256)` from one
   validated catalog record.
2. Fetch that immutable path directly on subsequent requests.
3. Change the pin only through an FE-owned product decision.

FE must not infer latest from object listings, directory names, timestamps, or
release IDs. A retired version remains fetchable for rollback; whether an
existing commerce mapping is disabled is FE business policy.

### 7.3 Immutable release catalog

`releases/<release-id>/catalog.json` validates against
`static-release-catalog-v1.schema.json`. It records one publication action and
is the audit history used to rebuild the stable catalog. It is no longer the
primary FE discovery API.

### 7.4 Immutable bundle manifest

`bundle.json` validates against `static-bundle-v1.schema.json` and declares:

- bundle/design/profile/version identity;
- canonical preview path and dimensions;
- exact print targets, dimensions, DPI, color space, background, and optional
  physical dimensions;
- a complete asset inventory with SHA-256, byte size, media type, and PNG
  dimensions; and
- generator/source provenance without private source pixels.

FE displays `preview.png` directly. It maps a commerce variant to an exact
`target_id` and sends that PNG to the vendor unchanged. FE must not resize a
target, derive print pixels from preview, choose a nearest target, scan
filenames for undeclared assets, or invoke an image model.

## 8. Publication transaction and failure behavior

1. Validate source decision, candidate, manifest, inventory, dimensions, and
   hashes locally.
2. Upload immutable preview/print assets.
3. Upload immutable bundle manifests.
4. Upload the immutable release catalog.
5. Rebuild and conditionally replace the stable discovery catalog.
6. Upload an immutable catalog-history snapshot of the committed state.
7. Write the local receipt and invalidate the GUI S3 cache.

The operation succeeds only after step 6. If catalog replacement loses an ETag
race, immutable objects may already exist but remain safe; refresh and retry the
same release so matching bytes are reused and the catalog is rebuilt. Changed
bytes under an existing immutable key are always rejected.

## 9. Inventory lifecycle and cleanup

| Inventory | MVP lifecycle | Cleanup rule |
| --- | --- | --- |
| Active seeds | graduate or abandon | external collection remains unchanged |
| Abandoned seeds | restore or retain | destructive purge is manual and outside GUI |
| Subject variants | approve, rework, abandon, regenerate | retain provenance needed by released versions |
| Local candidates | ready, superseded, published | remove only after S3 verification; published-state correlation uses the immutable bundle manifest, not the private generation record |
| Published product | active or retired | current-contract bundles/releases are retained in MVP |
| Pre-contract artifact | cannot be loaded by current identity/schema contract | audit, quarantine local files, and delete exact S3 keys with `retire-obsolete-artifacts` |

Normal product retirement is not deletion. Current-contract bundles are never
selected by obsolete-artifact cleanup merely because they are old, retired, or
unreferenced. A legal takedown that prevents pinned access requires separate
FE/CDN policy.

The expert obsolete-artifact workflow is deliberately narrower. Its dry run
validates the stable catalog and inventories only local records or S3 objects
that cannot satisfy the current path, identity, or schema contract. The
operator saves and reviews an immutable plan under a unique retirement ID and
reason before execution; execution never recomputes a larger target set. It
requires the workspace lock, refuses to retire any bundle/release referenced
by the stable FE catalog, quarantines local artifacts after verifying their
content hashes, preflights every remote object's audited ETag, conditionally
deletes only those exact keys, and updates a durable receipt after every item.
A failed or interrupted run resumes the same plan with the same ID and reason.
The stable catalog is validated but never automatically deleted. This workflow
handles pre-launch contract migrations; it is not retention policy for valid
releases.

## 10. Implementation boundaries and evolution

The MVP keeps one installable Python package and a build-free local web UI:

| Module | Responsibility |
| --- | --- |
| `core.py` | IDs, profiles, canonical profile digests, hashes, safe JSON/image primitives |
| `lifecycle.py` | intake pools, seed movement, subject catalog and paid subject generation |
| `reviews.py` | digest-bound reviewer/operator decisions and atomic batches |
| `authoring.py` | deterministic product preview/print rendering and private provenance |
| `bundles.py` | immutable bundle/release construction and validation |
| `catalog.py` | pure public discovery-catalog derivation, validation, and lifecycle transitions |
| `publisher.py` | AWS reads, conditional immutable uploads, catalog replacement, verification |
| `retirement.py` | contract-invalid inventory audit, local quarantine, conditional exact-key S3 retirement, resumable receipts |
| `server.py` | authenticated local workflow orchestration and guarded endpoints |
| `static/` | reviewer/operator presentation and client-side selection state |

Product rendering, subject generation, review persistence, catalog derivation,
and S3 transport remain independently testable. Schema changes are made in the
schema/pure-validation layer before server or GUI changes. The UI deliberately
has no framework/build dependency for MVP deployment. If additional personas or
workflow families are added, split the current static application and HTTP
handler by persona before adding another cross-cutting state system; do not put
authoring rules into browser-only state.

## 11. Security and operational constraints

- Reviewer and operator access codes differ for non-loopback serving.
- S3/model credentials live only in ignored `work/static-print.env`.
- FE receives no model key, prompt, local path, reviewer identity, or source
  collection.
- Public artifacts reject unknown fields and invalid identity/hash relations.
- Operator status is read from S3; local receipts cannot claim release.
- Print/bleed/color requirements belong in the profile and prepublication QA.

## 12. Alignment decisions still required

These are not P1/P2 blockers for the implemented MVP:

1. **P3 — FE retirement policy:** keep, warn, or disable an already pinned
   commerce mapping when status becomes `retired`.
2. **P3 — retention:** durations for abandoned seeds, superseded local
   candidates, and retired immutable versions.
3. **P3 — hard takedown:** IAM/CDN/application behavior for legal removal.
4. **P3 — cache policy:** FE polling/webhook cadence and catalog CDN TTL.
5. **P3 — identity:** replace shared access codes with named identities and
   auditable authorization when the team grows.
6. **P3 — UI/service split threshold:** split the build-free static UI and HTTP
   handler into persona modules when a fourth workflow persona or another
   independent authoring pipeline is introduced. The current single-page MVP
   avoids a frontend build/deployment system.
