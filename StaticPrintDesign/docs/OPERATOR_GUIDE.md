# Static Print Design Operator Guide

## 1. Install and configure once

```bash
cd /Users/qbit/Documents/PawMarvel/Code/playground/StaticPrintDesign
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/pawmarvel-static-print init-workspace

cp static-print.env.example work/static-print.env
# Edit access codes, API credentials, model, AWS profile, and S3 values.
```

`work/static-print.env` is private and ignored by Git. Every `serve` and
`publish` process automatically reloads this file from its selected
`--work-root`; starting the server does not depend on the parent terminal
having sourced it. Restart the server after editing the file. Explicit CLI
arguments still override file settings. The loader accepts comments, quoted
values, and optional `export KEY=value` syntax, but never evaluates shell code
or prints secret values.

The default subject model is `gpt-image-2.5-sunburst`; subject generation is a
paid image-edit operation. Launch-tier batches default to 50 image-request
starts per minute with at most 50 image requests in flight. Starts are spaced
evenly (1.2 seconds apart) rather than burst at once, which sustains the quota
while reducing avoidable 429 responses. Configure a different account tier in
the private settings with `STATIC_PRINT_IMAGE_REQUESTS_PER_MINUTE` and
`STATIC_PRINT_IMAGE_MAX_CONCURRENCY`, then restart the server. Transient
provider failures retry up to `STATIC_PRINT_IMAGE_MAX_ATTEMPTS` with
exponential delay based on `STATIC_PRINT_IMAGE_RETRY_BASE_SECONDS`. A provider
429 defers the shared queue, so already-waiting workers honor the provider's
`Retry-After` value instead of creating a retry storm. Each generated
subject also receives a low-detail
semantic check with `STATIC_PRINT_SUBJECT_VALIDATION_MODEL` before it replaces
the current preview. The check compares the seed and candidate for preserved
emotional intent plus species-appropriate facial expression, mouth/tongue,
pose, tail, and requested breed/coat/type. A mismatch receives one corrective
retry; if that also fails, the current preview remains unchanged and the batch
item fails for operator review. New
design intake uses a filename locally when it is
meaningful. Only an obvious machine/export filename makes one low-detail vision
request for a descriptive design ID, using `gpt-5.4-mini` by default through
`STATIC_PRINT_DESIGN_ID_MODEL`. Meaningful-name intake, re-importing an already
assigned source, and preview background switching are local and have no API
cost. Exact-size and downsampled product rendering is also local. When a print
target enlarges source pixels, the default `STATIC_PRINT_UPSCALE_BACKEND=bria`
sends the approved subject PNG to Bria for a paid 2× or 4× enhancement before
exact rendering. Configure `BRIA_API_TOKEN` in the private file. Set the
backend to `deterministic` only for local tests: enlargement then remains
flagged as not print-ready. `STATIC_PRINT_NORMALIZE_ALPHA=true` removes
near-invisible fringe and makes near-opaque pixels solid without flattening
meaningful alpha `32-249`. To use `$AWS_PROFILE` in a separate manual AWS CLI
command, source the file into that shell first:

```bash
set -a
source work/static-print.env
set +a
```

## 2. Load a design collection for active review

The quickest intake is a flat local collection. The default `auto` naming mode
uses a meaningful source filename directly as a lowercase kebab-case ID, so it
does not incur an image-analysis API call. Obvious machine/export names such as
`ChatGPT Image ...`, `IMG_1234`, screenshots, UUIDs, hashes, and timestamped
filenames receive one low-detail vision call and a readable 3-8 word theme ID:

```text
/path/to/Design-Collection-1/
  Holiday Patrol.png
  Merry Pawlidays.png
  Winter Watch.png
```

Start the GUI with one or more collections:

```bash
.venv/bin/pawmarvel-static-print serve \
  --review-collection "/path/to/Design-Collection-1" \
  --open
```

Alternatively, open the operator page, enter the absolute collection path in
**Load local design collection**, and choose **Load PNG collection**. Only PNG
files directly inside the selected folder are imported. The import validates
the images and makes IDs unique across every lifecycle pool. A meaningful
filename wins when unused. On collision, the tool first prefixes the collection
ID (for example `holiday-collection-merry-pawlidays`), then appends `-1`, `-2`,
and so on only if that qualified ID also exists. Theme IDs produced from
machine-generated filenames use deterministic numeric suffixes when needed.
The source is then copied into the private active-review pool:

```text
work/static-design-inputs/<design-id>/
  source-design.png
  intake.json
```

`intake.json` records the selected design ID, naming method/model, collection,
original local path, source digest, transparency evidence, inferred canvas
policy, and any persistent subject-generation requirements saved by the
operator. A source with no transparent pixels is classified as
`opaque-full-bleed`; a source containing any alpha transparency is classified
as `preserve-transparency`. Review the assigned IDs shown in the completion
message before collecting votes.

Before graduating a seed, use **Persistent subject-generation requirements** on
its active-review card to record the visual rules that every derived subject
must obey. Capture only design-specific invariants such as required line work,
solid colors, alpha regions, accessories, text, or elements that must never be
added. This contract moves with the seed and is automatically included in every
subject-generation call. Use **Optional generation instruction** later only for
a one-off regeneration adjustment; it does not replace the persistent contract.
Within the persistent requirements, `[PET BREED]` is replaced with the selected
catalog subject's generation description before the image request is submitted.

The source's resolved path pins its ID. Reloading a collection is idempotent:
unchanged designs remain unchanged, modified active candidates retain their ID
and are refreshed without another naming call. Saved subject-generation
requirements are preserved on re-import. Designs already graduated
or abandoned are skipped. The original collection is never renamed or
modified. Moving a source to a different path is treated as a new candidate.

Automatic hybrid naming is the default. It requires `OPENAI_API_KEY` only when
a new source has an obvious machine-generated filename; such a source fails
clearly when image interpretation is unavailable and never silently receives
an opaque export ID. Set `STATIC_PRINT_DESIGN_ID_MODE=semantic` to analyze every
new image, or `STATIC_PRINT_DESIGN_ID_MODE=filename` to analyze none, then
restart the server. Filename mode should be used only when all filenames already
satisfy the catalog naming standard.

For a single design, the same active structure can still be created manually:

```bash
PAWMARVEL_STATIC_SEED_ID='holiday-patrol'
mkdir -p "work/static-design-inputs/$PAWMARVEL_STATIC_SEED_ID"
cp "/path/to/candidate-design.png" \
  "work/static-design-inputs/$PAWMARVEL_STATIC_SEED_ID/source-design.png"
```

The seed is product-independent. It must be a readable PNG. Use the best
available source and keep its intended transparency. The Holiday Selfie
collection is opaque and should report no transparent pixels. The Push
Christmas Tree collection uses transparent backgrounds and should report the
`preserve-transparency` canvas policy. Other collections may intentionally use
transparent borders and are not forced opaque.

## 3. Start the GUI

```bash
.venv/bin/pawmarvel-static-print serve \
  --bind 127.0.0.1 \
  --port 8876 \
  --open
```

- Reviewer gallery: `http://127.0.0.1:8876/`
- Operator workflow: `http://127.0.0.1:8876/operator`

At startup the command prints the resolved workspace and active seed count. The
operator page repeats that absolute workspace path so an accidental
`--work-root` override is immediately visible. Active, graduated, and abandoned
seed pools use separate tabs. **Graduated workflow** is a checkpoint table for
unfinished work, and **Released** contains subject designs whose current pixels
have been published successfully.

The local gallery renders immediately after sign-in. S3 publication status is
refreshed in the background and the publication panel reports that progress;
an expired SSO session or slow network must not hide local review designs.

For trusted-LAN review, bind to `0.0.0.0`. Non-loopback serving requires
different reviewer and operator access codes. Do not expose the plain-HTTP
development server directly to the public Internet.

## 4. Review and disposition the seed

Both views can render the same PNG over:

- raw checkerboard transparency;
- solid blue;
- white; or
- an operator-selected custom color.

Reviewers choose the visible **Approve**, **Needs changes**, or **Reject**
button on each card in one click and may add a comment. Decisions from several
cards remain pending until **Save all changed feedback** is chosen once. The
server saves the batch atomically and rejects it if any source pixels changed
after the page loaded. Refresh and review those new pixels before retrying.
The transparency-background controls, reviewer identity, pending-change count,
and batch-save button remain pinned at the top of the viewport while the
reviewer scrolls through the active design list.

Use the filter bar to narrow the current tab by collection, exact subject,
product, design ID text, transparency result, review/workflow status, and
current selection. A collection recorded by `intake.json` is authoritative.
For manually added seeds without intake metadata, the GUI groups two or more
distinct seed IDs by their shared multi-segment prefix—for example,
`holiday-ornament-*` appears as `holiday-ornament`. Ungrouped manual seeds
remain under **Manual intake**. The subject dropdown lists only generated subjects
present in the workspace and includes both the operator label and stable
subject ID, so all designs for one subject can be reviewed together. In the
**Graduated** and **Released** tabs, selecting a product focuses every subject
row, product preview, print evidence, and development/release status on that
one product profile. Designs whose selected product has not been generated
remain visible as **Not generated**, making coverage gaps actionable. The count
shows the visible and total designs. **Clear filters**
restores the full tab. Operator selections are retained while filters change.

The batch-action tray stays pinned above the design list while you scroll. Its
review, subject-generation, product-generation, and publication panels are
independently collapsible, so only the current checkpoint needs to remain open.
Selection counts in the tray and filter bar make the scope of the next batch
operation explicit.

In **Active review**, select individual cards or choose **Select all filtered**,
then batch **Approve and graduate** or abandon the selection. In **Abandoned**,
the same toolbar batch-restores selected seeds. File-moving batch actions
preflight the complete selection before moving anything. In **Graduated
workflow**, every generated subject preview has a **Select for batch approval**
checkbox. Apply the desired filters, choose **Select all filtered subject
previews**, then use **Approve selected subjects** to record one atomic operator
decision for exactly that visible result. This action replaces the earlier
subject selection so a hidden row cannot be approved accidentally. Approved
subjects expose a separate
**Select for print generation** checkbox, so approval and rendering selections
cannot be confused. Switching workflow tabs clears the prior tab's card
selection to prevent cross-pool actions. **Approve + queue products** combines
the two safe local decisions and avoids selecting the same rows twice.

The **Abandoned** tab also contains the independent **Authoring history
cleanup** panel. It scans all lifecycle pools—not only abandoned seeds—for
superseded local product-version directories. Generate the proposal, filter it
by lifecycle pool, product, or design/subject text, inspect the exact versions
and reclaimable size, select the desired design histories, and choose **Delete
selected unused history**. Proposal generation is read-only. Execution hashes
and rechecks every selected directory and refreshes release references before
permanently deleting anything. A version is never proposed when it is the
current local candidate or is referenced by the local/S3 release catalog.
Source designs, generated subject previews, release bundles, and partial work
are outside this cleanup's target scope.

Cleanup plans and receipts are retained at
`work/static-history-cleanup/{proposals,receipts}/<proposal-id>.json`. A
proposal with an execution receipt cannot be reused, including after an
interruption or partial failure; the receipt preserves completed targets and
the current target, and the operator generates a fresh proposal for any
remaining history. When S3 is configured but its authoritative catalog
cannot be read, proposal generation or execution stops instead of assuming
that a version is unreferenced. Deletion is irreversible, so use the displayed
paths, versions, hashes, and byte counts as the review checkpoint.

The graduated table is organized by workflow checkpoint:

| Checkpoint | Evidence and action |
| --- | --- |
| 1 · Reference design | Approved seed, transparency evidence, and subject-generation controls |
| 2 · Subject preview | Generated subject, approve/rework decision, and a guarded paid regeneration action |
| 3 · Product preview and print | Exact product-preview canvas, every print target, dimensions, and release-candidate selection |
| Workflow status | The single next action: generate, approve, render, publish, or released |

A subject row advances in place. Successful S3 publication moves that current
subject row from **Graduated workflow** to **Released**; no source files are
moved and published objects stay immutable. This is intentional: replacing or
regenerating the subject changes its digest and returns the new pixels to the
graduated table for approval, while the older release receipt remains intact.

Keep iteration scoped to the failed checkpoint:

- abandon a seed while it is still in Active Review;
- use **Needs rework**, **Abandon subject**, or **Regenerate with instruction**
  when a subject replacement fails (regeneration is a paid call);
- use a new local version when product fit or print evidence fails; and
- from Released, open **Reopen subject for improvement** or add another product
  profile. The changed current work returns to Graduated Workflow, without
  changing the earlier release.

Each card reports whether transparent background pixels reach the canvas border,
including the transparent-border percentage. It distinguishes a likely
transparent background from internal/partial alpha. This is evidence, not an
automatic approval: transparency can be intentional. Subject generation derives
its output policy from the approved seed. Opaque seeds request and normalize an
opaque, edge-to-edge result; transparent seeds retain their alpha treatment.

Collect reviewer feedback, then use the operator workflow:

- **Graduate seed** moves the complete seed folder to
  `work/static-graduated-designs/<seed-design-id>/`.
- **Abandon seed** moves it to
  `work/static-abandoned-designs/<seed-design-id>/`.
- **Restore to active review** moves an abandoned seed back to
  `work/static-design-inputs/`.

Only graduated seeds can create subject variants.

For a collection import, moving the private active copy means the original PNG
remains in its local collection while the graduated design follows the stable
artifact structure used by the rest of the workflow.

## 5. Generate subject previews

On a graduated seed card, select all catalog subjects or only the coverage
needed for this iteration. The ordered catalog is
`catalog/static-design-subjects.json`.

Each catalog `subject_id` is an explicit, stable FE product-variant key—not a
slug derived at generation time. Do not rename it as a copy edit. The public
identity is built deterministically as:

```text
design_id = <seed-design-id>--subject-<subject-id>
bundle_id = <seed-design-id>--subject-<subject-id>--<product-profile-id>
```

The loader rejects duplicate/invalid subject IDs and priorities before any paid
generation starts.

For one reference design, expand **Add or regenerate subject coverage** in its
table row. For a batch:

1. Filter the graduated table to the intended references.
2. Choose **Select all filtered references** in **Batch subject-preview
   generation**, then remove any row-level exceptions.
3. Select catalog subjects, or choose **Select all subjects**.
4. Optionally enter one improvement instruction that applies to this batch.
5. Choose **Generate selected references × subjects** and confirm the displayed
   base paid-call count. The dialog also warns that a semantic mismatch can add
   at most one corrective paid image call for that work unit.

Each missing reference/subject pair makes one paid OpenAI image-edit call using
the approved seed as Image A. The worker pool processes independent pairs in
parallel while the shared limiter spaces every initial or corrective image call
at the configured rate. The operation log records the effective rate,
concurrency, per-item completion, and any failures. A failed work unit does not
discard successful siblings; correct its input and use **Resume** to retry only
unfinished units. Existing previews are skipped by default, which makes adding
coverage safe and avoids duplicate API cost. Enable replacement only for an
intentional iteration; every selected existing preview will then be
regenerated. The prompt preserves the complete seed design and replaces only
the featured pet with a generic representative of the selected dog breed, cat
group, or other configured subject. A semantic-validation mismatch may make
one additional paid corrective image call, and that retry uses the same
50-images-per-minute quota.

Outputs are stored at:

```text
work/static-graduated-designs/<seed-design-id>/
  source-design.png
  <subject-id>/
    source-design.png
    subject-generation.json
```

`subject-generation.json` records the seed digest, subject, model, quality,
prompt, explicit generation/species contract, semantic-validation attempts,
optional operator instruction, output digest, generation time, and a
deterministic `print_readiness` pixel report. Every GPT Image request includes
an explicit production-print instruction: keep typography and intentional
detail crisp, keep visible artwork interiors solid, constrain partial alpha to
narrow antialiased edges, and avoid blur, halos, stray pixels, broad translucent
washes, and transparent holes. The GUI and batch workflow use this same prompt;
there is no browser-only shortcut. Marketing
labels such as `Dog lover` are never used as the entity to generate; the private
catalog supplies an unambiguous description such as `exactly one mixed-breed
domestic dog`. Every machine-facing description must explicitly name its
species. The prompt preserves emotional intent rather than blindly copying the
source pet's anatomy: cats do not inherit dog-like panting or a casually
protruding tongue, while dog mouth, ears, tail, and pose remain appropriate to
the requested breed/type. A natural open feline mouth is allowed when the
design context clearly represents meowing, hissing, yawning, grooming, licking,
eating, or drinking. Ambiguous but plausible expressions are retained with an
operator-review recommendation; species-inappropriate expressions fail and are
retried. Validation checks these behaviors together with breed/coat identity.
Replacing a subject preview invalidates its prior operator decision because
decisions are keyed to the source-image digest.

## 6. Approve subject previews

Review every generated subject against all useful background colors. Record
**Approve subject**, **Needs rework**, or **Abandon subject** with an optional
note.

An orange **Animal behavior needs attention** panel identifies a borderline
semantic result and explains why it needs human judgment. The label is stored
against the exact preview digest and is repeated at the subject, product, and
workflow checkpoints until the operator approves or abandons that preview.
Choose **Review attention → Needs attention** to isolate all active reference,
subject, or product warnings. Approval resolves the active warning but retains
the reviewed label for traceability; regenerating the preview replaces the flag
only when the new digest has its own validation evidence.

An orange **Pixel preflight needs attention** panel means the saved generation
failed an intrinsic check such as exact dimensions, visible pixels, or the
seed's opaque/transparent canvas policy. A **Product alpha compatibility needs
attention** panel is narrower: the subject itself passed, but one or more print
profiles need review. Expand it to see the one-line alpha summary for
near-opaque, meaningful-translucency, and high-risk-fringe pixels, followed by
grouped PASS/REVIEW results for apparel, sublimation, paper/canvas, and sticker
profiles. Near-opaque pixels are accepted; meaningful translucency and fringe
are evaluated with process-specific thresholds. Inspect failed target products
at 100% zoom before product approval. These checks cannot judge spelling,
composition, subject accuracy, vendor-specific behavior, or whether painterly
texture is intentional; operator visual review remains required.

For batch review, filter the graduated table, choose **Select all filtered
subject previews**,
then use either **Approve selected subjects** or **Needs work for selected
subjects**. **Approve + queue products** records the approvals and carries the
same selection directly into product generation, avoiding a second selection
pass. All selected subject decisions are written in one transaction.

Only a subject preview with a current `approved` operator decision is selectable
for product print generation. The decision is stored centrally in
`work/static-reviews.sqlite3`; no approval file is copied into the FE bundle.

When a subject needs improvement, enter a concrete instruction beside that
subject and choose **Regenerate with instruction**. The tool replaces only that
subject preview, preserves the reference seed, records the instruction in its
provenance, and returns it to pending review. Use the batch replacement option
only when the same instruction applies to multiple references and subjects.

## 7. Generate and inspect product print candidates

Every product candidate is planned independently as
`(design subject, product profile, version)`. Versions are not shared across a
seed, subject, batch, or workspace. For example, the same subject may have a
published mug `v000003` and an unpublished blanket `v000001`.

For a granular product-feedback iteration:

1. Use the always-visible **Product coverage** matrix on one approved subject
   row. It is collapsed by default to keep large batches compact; expand it for
   individual inspection or actions. It lists every configured profile, its
   exact dimensions, and one of:
   **Not generated**, **Ready to publish**, **Published**, **Update ready**, or
   **Requires attention**.
2. Choose **Generate** beside one missing profile, or select several profiles
   and choose **Generate selected products**. **Select missing products** queues
   every uncovered profile for that subject in one click.
3. Confirm the displayed automatically assigned version in the resulting
   evidence. No version input is required.
4. Inspect the preview and print evidence. Choose **Publish** beside a ready
   candidate for a guarded one-candidate publication, or select multiple ready
   candidates for the batch release controls.

The matrix remains available in both **Graduated workflow** and **Released**.
It is the product-level source of truth for the operator's next action; a design
does not become "complete" merely because its first product was published.

For a larger batch:

1. Narrow the table with the filters, then choose
   **Select all filtered approved subjects**. Individual row checkboxes can add
   or remove exceptions.
2. In **Batch product generation**, select the desired product profiles, or use
   **Select all profiles** for complete coverage.
3. Choose **Generate selected subjects × profiles**.

The action count is the Cartesian product of selected subject rows and selected
profiles. Product rendering is local, and the server assigns the next version
independently for each tuple. Operators never type or synchronize version IDs.
Every batch panel shows both the selected IDs and its effective work count before
execution—for example, `10 subjects × 3 profiles = 30 candidates`. Confirm this
summary before starting a paid subject batch or a product/release batch.

Every operator batch—seed transitions, subject decisions/generation, product
generation/lifecycle, bundle validation, and S3 publication—uses the same
expandable run report. Its one-line summary reports `x complete/published, y
pending, z error, k warning`. The expanded rolling window shows the active work
unit and the latest 12 timestamped outputs, including generated paths and S3
object/catalog updates. Failed output includes the exact error, affected item,
and a recommended correction for common credential, path, conflict, and network
problems. It may be collapsed without affecting the run.

Product evidence is refreshed after each completed candidate, so an operator
may expand that subject's **Product coverage** and begin inspection while the
remaining candidates continue. If the browser connection is
interrupted, reconnecting restores progress from the durable SQLite operation
ledger. If the server or machine stops, startup marks the run interrupted,
removes uncommitted staging directories, restores any interrupted subject
replacement, and quarantines incomplete committed product directories. The
status panel then offers **Resume remaining work** or **Clean up incomplete
work**. Resume preserves completed work units and reuses their planned version
IDs; it does not repeat completed paid API calls.

Each product candidate and subject preview is built in a sibling
`.partial-<operation-id>` directory and promoted with one atomic rename only
after all required files and metadata are valid. A reconnect can therefore see
either the previous complete artifact or the new complete artifact, never a
half-written candidate. Startup recovery details are also available in the
operator workspace state; quarantined paths remain under
`work/recovery-quarantine/<timestamp>/` for inspection.

Only one server may write a workspace at a time. A workspace lock rejects a
second server even when it uses another port. This prevents two processes from
allocating the same version or reconciling each other's staging directories.
If the process stops after an image provider accepted a paid request but before
the response was durably saved, the provider call cannot be recovered locally;
resuming that one unfinished subject may issue it again. Completed, atomically
promoted subjects are never regenerated by Resume.

**Clear product selection** clears pending row/profile selections without deleting
generated artifacts or recorded approvals.

The server validates every planned tuple, current subject approval, existing
local generation record, profile, version, and publication conflict before it
writes the first candidate. If a later work unit fails, earlier committed work
is retained and recorded; resume continues only unfinished work.

The server assigns versions, not the browser: missing coverage starts at
`v000001`, and every subsequent generation advances past the highest local or
published version for only that subject/profile pair. This prevents stale-page
collisions and manual version mistakes. Batch profile checkboxes are only the
requested coverage for that action; they do not become global product state.

Controls that remain shared are deliberately not product state: preview
background and filters are display preferences; model/API credentials are
workspace infrastructure; and a release ID groups the exact bundle candidates
selected for one publication. Subject approval remains bound to the subject
image digest, while publication selection remains bound to one explicit
subject/profile/version tuple.

A subject moves to **Released** after its first current tuple is published. It
can simultaneously have other profiles that are missing, locally generated,
or ready to publish; those profile-local states and actions remain visible in
the Released row. Only replacing the subject pixels invalidates every derived
profile candidate and returns the current subject to approval workflow.

Private outputs are:

```text
work/static-authoring/<seed-design-id>/<subject-id>/<product-profile-id>/<version-id>/
  generation.json
  <design-id>-preview.png
  profile-previews/
    <product-profile-id>.png
  <design-id>--<single-target-profile>.png
  <design-id>--<multi-target-profile>--<target-id>.png
```

The `profile-previews/` files are private operator evidence and do not change
the FE bundle contract. They use the exact bundle preview dimensions and the
same deterministic fit path as publication. Older authoring versions that
used the former subject-level version directory are intentionally not release
candidates; rerun each unpublished subject/profile plan to create its granular
directory and exact checkpoint preview. Published S3 bundles are unaffected.

Static product generation always emits the profile's exact pixel dimensions
without stretching. The default profile composition is `cover`, which
center-crops overflow when source and target aspect ratios differ. A profile
may instead declare `composition: repeat-horizontal`; this places two complete,
contained copies in equal left/right cells for a two-sided wrap. The tumbler
profile uses this mode so portrait mug designs retain their complete title and
lower banner on both sides. Review every composition before publication. For an
opaque full-bleed source, generation also rejects any preview or print output
containing alpha transparency. Transparent-art sources skip that test by
design.

Inspect every preview and exact print target. Generate the subject/profile again
when needed; the GUI assigns its next version automatically. Published bytes
are immutable, and improvements always use a new version.

The evidence view renders each PNG at its natural aspect ratio and shows the
measured transparent-pixel and transparent-border percentages. Checkerboard
space outside the image element is not treated as asset transparency. An
`opaque-full-bleed` artifact must report zero for both measurements.
Every newly generated operator preview, exact product preview, and print target
also receives the same `print_readiness` report in `generation.json`. A failed
report is shown inline as **Not print-ready**, appears under the **Needs
attention** filter, and is copied into the batch operation warning log. Pixel
warnings are advisory so intentional watercolor/translucent media can be
accepted by an operator; existing hard canvas-contract violations remain
blocking.

Expand the pixel-preflight badge to see its one-line alpha summary and the
product/process recommendations. The report separates these bands instead of
treating every non-opaque pixel alike:

| Alpha band | Meaning | Default recommendation |
| --- | --- | --- |
| `250-254` near-opaque | Visually solid model output | Accept; optional final-export normalization to `255` |
| `32-249` meaningful translucency | Fade, shadow, wash, or broad antialiasing | Evaluate against the selected print process |
| `1-31` high-risk fringe | Nearly invisible pixels | Boundary pixels within two pixels of transparency are usually antialiasing; interior low-alpha pixels are more likely to be a wash, hole, or dirty pixel |

Each configured product row reports **PASS** or **FAIL** with its print process,
measured percentages, limits, and a short reason. The high-risk total is split
into **boundary fringe** and **interior low-alpha** so ordinary silhouette
antialiasing is not treated like a broad translucent region. DTG/DTF apparel is intentionally stricter because a
white underbase can expose a pale halo. Dye-sublimation and opaque paper/canvas
allow bounded intentional translucency. Sticker output uses an intermediate
tolerance and still requires vendor cut-line/white-ink confirmation. These are
PawMarvel preflight limits, not a substitute for the vendor proof. Near-opaque
pixels alone never fail the alpha check.
`generation.json` pins one product profile and its canonical semantic digest,
its local version, the exact subject-source digest, fit/composition mode,
inferred canvas policy, alpha-normalization choice, and enhancement provenance.
Replacing a subject preview invalidates
its older local print candidates and requires a new version for each affected
profile, preventing stale pixels from being published under new provenance.
Editing preview dimensions, print targets, DPI, color space, background, or
physical size under an existing profile ID likewise makes older local
candidates require attention and a new version. Local candidates created before
generation v3 recorded both `canvas_policy` and the profile digest are
intentionally not release candidates; generate a new version after restarting
the server. Existing published bundles remain valid and immutable.

## 8. Validate and publish

The MVP destination is:

```text
s3://<configured-static-print-bucket>/<configured-static-print-prefix>/
```

Authenticate before publication:

```bash
aws sso login --profile "$AWS_PROFILE"
aws sts get-caller-identity --profile "$AWS_PROFILE"
```

In the operator workflow:

1. Filter the graduated or released table to the intended candidates and choose
   **Select all filtered release-ready candidates**. Remove row-level
   exceptions as needed.
2. Leave **Release ID** blank for automatic `YYYY-MM-DD.NNN` allocation. Enter
   an explicit ID only when coordinating an existing release plan.
3. Choose **Validate release** and inspect the plan.
4. Choose **Validate and publish to S3**. Validation writes the assigned ID back
   into the field so the following publish uses the exact same draft.

Validation checks the complete immutable S3 object plan before any upload.
Objects that already exist with identical hashes are safe to reuse. A different
hash under the same bundle version is rejected before the first write; generate
the next product version rather than changing only the release ID. The bundle
manifest creation time is pinned to the generated product version, so packaging
the same version in a later release produces byte-identical bundle metadata.

S3 preflight reads and immutable uploads use a bounded concurrent pool. The
default is 16 workers, configured with
`STATIC_PRINT_S3_UPLOAD_MAX_CONCURRENCY`. Assets upload first, bundle manifests
second, and the release catalog commit marker last; the mutable discovery
catalog is still conditionally updated only after every immutable object is
verified. Throttling, timeouts, connection failures, and transient 5xx errors
retry up to `STATIC_PRINT_S3_MAX_ATTEMPTS` with exponential delay based on
`STATIC_PRINT_S3_RETRY_BASE_SECONDS`. Authentication, permission, immutable
hash, and conditional-write conflicts fail immediately. Successfully uploaded
immutable objects are verified and safely reused when the operator retries the
release.

After the publication receipt is recorded, the current subject pixels appear
under **Released**, with the release ID, version, product profile, preview, and
all print dimensions. The released table still permits new product candidate
generation so operators can extend coverage without recreating the seed or
subject. Published evidence is collapsed by default; ready or updated evidence
is expanded so the next decision remains prominent.

Public artifacts are:

```text
s3://<configured-static-print-bucket>/<configured-static-print-prefix>/
  catalog.json
  catalog-history/<timestamp>-<sha-prefix>.json
  bundles/<design-id>--<product-profile-id>/<version-id>/
    bundle.json
    preview.png
    print/<target-id>.png
  releases/<release-id>/catalog.json
```

`catalog.json` is the stable FE discovery contract. It identifies every
design/product, its active or retired lifecycle state, all immutable versions,
and one explicit `latest_version_id`. FE may follow latest deterministically or
pin one exact immutable manifest. Give FE this stable catalog URI rather than
requiring it to merge release history.

`catalog-history/` contains immutable recovery snapshots written after each
successful stable-catalog replacement. It preserves lifecycle decisions if the
stable object must be rebuilt. It is operator infrastructure, not an FE
discovery API; FE must never infer current state by listing that folder.

The operator GUI derives released subject/profile coverage from this catalog.
If `catalog.json` is missing, the GUI can recover inventory from immutable
release catalogs and reports that the discovery catalog must be rebuilt.
Choose **Rebuild FE discovery catalog** to restore it without making a bundle
or release; the next successful publication also creates or refreshes it. When
catalog history exists, rebuild preserves its latest active/retired decisions.
S3 is the publication source of truth. Files under
`work/static-exchange/publications/` are local audit/retry receipts only and
cannot make a product appear released. The tool also refreshes
`work/static-exchange/catalog.json` as a local audit/debug mirror after every
successful catalog mutation; it is not an FE input and cannot make a product
appear released. The GUI lists S3 on refresh, caches
the unchanged catalog body by ETag for responsiveness, and invalidates its cache
after publication. If S3 cannot be checked, the GUI reports **S3 status
unknown** instead of treating local receipts as proof of release.
Published coverage is correlated to the current subject with the source digest
in each hash-verified S3 bundle manifest. Obsolete private generation records
may therefore be removed after S3 verification without making a published
product disappear from the operator inventory.

## 9. Extend an already released design

Published releases and bundle versions are immutable, but their graduated seed
and subject sources remain available locally. To add coverage later:

1. Open **Released** and filter to the seed or subject being extended.
2. Select the reference for batch subject generation to add missing catalog
   subjects, or use **Reopen subject for improvement** and regenerate one
   existing subject with an instruction.
3. Approve the new subject digest. Replaced pixels return to **Graduated
   workflow** until this decision is recorded.
4. In **Product coverage**, choose **Select missing products** and **Generate
   selected products**, or use a row's **Generate** action for one profile. Its
   automatic version starts at `v000001` even when another profile for the same
   subject has a higher version; an existing profile advances to its next
   version.
5. Review the expanded evidence. Choose the row-level **Publish** action for one
   candidate, or select several candidates and use **Validate and publish to
   S3**. Every publication receives a new automatically allocated release ID.

Existing S3 objects remain unchanged. FE can import the new catalog and decide
which new subject/product mappings to activate.

### Retire or reactivate a released product

In the Released product matrix, select one or more published profiles and use
**Retire selected published**, or use the row-level **Retire** action. Enter an
optional reason and confirm. The tool conditionally updates the stable S3
catalog; it does not delete bundles or release history. A retired product stays
visible to the operator and remains pin-accessible to FE for rollback, but FE
must exclude it from default latest discovery.

Use **Reactivate** or **Reactivate selected retired** to return coverage to the
active catalog. Publishing a deliberately reviewed new version also activates
that design/product entry. Coordinate with FE before relying on retirement to
disable a commerce variant: whether an already pinned variant is immediately
disabled remains an application policy.

### Remove pre-contract artifacts after a contract migration

This expert workflow is for artifacts that the current implementation cannot
consume. It does not delete a valid bundle because it is old, retired, or no
longer selected by the stable catalog.

This is separate from the GUI history cleanup above. History cleanup removes
valid but superseded private authoring versions; obsolete-artifact retirement
handles files and S3 objects that no longer satisfy the current contract.

Stop the GUI/server before cleanup. Run a read-only audit first; it prints every
exact local path and S3 key with the rejection reason:

```bash
.venv/bin/pawmarvel-static-print retire-obsolete-artifacts
```

Save the exact plan under a unique ID and auditable reason:

```bash
.venv/bin/pawmarvel-static-print retire-obsolete-artifacts \
  --retirement-id obsolete-contract-cleanup-2026-10-15 \
  --reason "Remove pre-launch artifacts outside the current contract"
```

Review the saved receipt at
`work/static-exchange/retirements/<retirement-id>.json`, then execute that same
plan with the identical ID and reason:

```bash
.venv/bin/pawmarvel-static-print retire-obsolete-artifacts \
  --retirement-id obsolete-contract-cleanup-2026-10-15 \
  --reason "Remove pre-launch artifacts outside the current contract" \
  --execute
```

Execution refuses to recompute a plan or run while the GUI/server holds the
workspace lock. The tool also refuses to retire any bundle or release still
referenced by the stable FE catalog. Local artifacts move to
`work/static-retired-artifacts/<retirement-id>/`; they are not immediately
erased. A local content hash and each remote ETag are captured in the saved
plan, so changed artifacts stop execution. If the process or network is
interrupted, rerun the identical execute command: the receipt resumes pending
work without re-deleting completed items. Run the dry audit again afterward;
both counts must be zero. S3 recovery after deletion depends on bucket
versioning or external backup policy.

## 10. CLI alternatives

The GUI is the guarded workflow. The CLI is an expert recovery path and reads
graduated inputs by default.

```bash
.venv/bin/pawmarvel-static-print generate \
  --design-id holiday-patrol--subject-dog-golden-retriever \
  --version-id v000001 \
  --product-profile profiles/coffee-mug-11oz.json

.venv/bin/pawmarvel-static-print publish \
  --release-id 2026-10-20.001 \
  --selection holiday-patrol--subject-dog-golden-retriever:v000001:coffee-mug-11oz

.venv/bin/pawmarvel-static-print set-product-status \
  --selection holiday-patrol--subject-dog-golden-retriever:coffee-mug-11oz \
  --status retired \
  --reason "Vendor target paused"

.venv/bin/pawmarvel-static-print rebuild-catalog
```

Publication is a dry run unless `--execute` is supplied.
