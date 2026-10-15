# Static Print Design Operator Guide

## 1. Install and configure once

```bash
cd /Users/qbit/Documents/PawMarvel/Code/playground/StaticPrintDesign
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/pawmarvel-static-print init-workspace

cp static-print.env.example work/static-print.env
# Edit access codes, OPENAI_API_KEY, model, AWS profile, and S3 values.
```

`work/static-print.env` is private and ignored by Git. Every `serve` and
`publish` process automatically reloads this file from its selected
`--work-root`; starting the server does not depend on the parent terminal
having sourced it. Restart the server after editing the file. Explicit CLI
arguments still override file settings. The loader accepts comments, quoted
values, and optional `export KEY=value` syntax, but never evaluates shell code
or prints secret values.

The default subject model is `gpt-image-2.5-sunburst`; subject generation is a
paid image-edit operation. New design intake also makes one low-detail vision
request per new PNG to generate a descriptive design ID. It defaults to
`gpt-5.4-mini` through `STATIC_PRINT_DESIGN_ID_MODEL`. Product rendering,
re-importing an already assigned source, and preview background switching are
local and have no API cost. To use `$AWS_PROFILE` in a separate manual AWS CLI
command, source the file into that shell first:

```bash
set -a
source work/static-print.env
set +a
```

## 2. Load a design collection for active review

The quickest intake is a flat local collection. Source filenames can be camera
or export names; the tool analyzes each new PNG and proposes a 3-8 word
lowercase kebab-case ID that describes its visible theme:

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
the images, makes each generated ID unique across every lifecycle pool (`-2`,
`-3`, and so on when themes collide), and copies them into the private
active-review pool:

```text
work/static-design-inputs/<design-id>/
  source-design.png
  intake.json
```

`intake.json` records the selected design ID, naming method/model, collection,
original local path, source digest, transparency evidence, and inferred canvas
policy. A source with no transparent pixels is classified as
`opaque-full-bleed`; a source containing any alpha transparency is classified
as `preserve-transparency`. Review the assigned IDs shown in the completion
message before collecting votes.

The source's resolved path pins its ID. Reloading a collection is idempotent:
unchanged designs remain unchanged, modified active candidates retain their ID
and are refreshed without another naming call, and designs already graduated
or abandoned are skipped. The original collection is never renamed or
modified. Moving a source to a different path is treated as a new candidate.

Semantic naming is the default and fails clearly when the API key or request is
unavailable; it never silently falls back to an opaque export filename. For an
intentional offline import only, set `STATIC_PRINT_DESIGN_ID_MODE=filename` in
`work/static-print.env` and restart the server. Filename mode is an explicit
escape hatch and should be used only when filenames already satisfy the catalog
naming standard.

For a single design, the same active structure can still be created manually:

```bash
PAWMARVEL_STATIC_SEED_ID='holiday-patrol'
mkdir -p "work/static-design-inputs/$PAWMARVEL_STATIC_SEED_ID"
cp "/path/to/candidate-design.png" \
  "work/static-design-inputs/$PAWMARVEL_STATIC_SEED_ID/source-design.png"
```

The seed is product-independent. It must be a readable PNG. Use the best
available source and keep its intended transparency. The Holiday Selfie and
Push Christmas Tree collections are opaque full-canvas designs: their source
files should report no transparent pixels. Other collections may intentionally
use transparent borders and are not forced opaque.

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

Use the filter bar to narrow the current tab by intake collection, design ID or
subject text, transparency result, review/workflow status, and current
selection. The count shows the visible and total designs. **Clear filters**
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
checkbox; use **Approve selected subjects** to record one atomic operator
decision for the filtered selection. Approved subjects expose a separate
**Select for print generation** checkbox, so approval and rendering selections
cannot be confused. Switching workflow tabs clears the prior tab's card
selection to prevent cross-pool actions. **Approve + queue products** combines
the two safe local decisions and avoids selecting the same rows twice.

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
   maximum paid-call count.

Each missing reference/subject pair makes one paid OpenAI image-edit call using
the approved seed as Image A. Existing previews are skipped by default, which
makes adding coverage safe and avoids duplicate API cost. Enable replacement
only for an intentional iteration; every selected existing preview will then be
regenerated. The prompt preserves the complete seed design and replaces only
the featured pet with a generic representative of the selected dog breed, cat
group, or other configured subject.

Outputs are stored at:

```text
work/static-graduated-designs/<seed-design-id>/
  source-design.png
  <subject-id>/
    source-design.png
    subject-generation.json
```

`subject-generation.json` records the seed digest, subject, model, quality,
prompt, optional operator instruction, output digest, and generation time.
Replacing a subject preview invalidates its prior operator decision because
decisions are keyed to the source-image digest.

## 6. Approve subject previews

Review every generated subject against all useful background colors. Record
**Approve subject**, **Needs rework**, or **Abandon subject** with an optional
note.

For batch review, filter the graduated table, choose **Select all filtered**,
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

Static product generation always uses `cover`: it fills every preview and
print canvas at the profile's exact pixel dimensions, center-cropping overflow
when source and target aspect ratios differ. It never stretches pixels. Review
the crop shown for every target before publication. For an opaque full-bleed
source, generation also rejects any preview or print output containing alpha
transparency. Transparent-art sources skip that test by design.

Inspect every preview and exact print target. Generate the subject/profile again
when needed; the GUI assigns its next version automatically. Published bytes
are immutable, and improvements always use a new version.

The evidence view renders each PNG at its natural aspect ratio and shows the
measured transparent-pixel and transparent-border percentages. Checkerboard
space outside the image element is not treated as asset transparency. An
`opaque-full-bleed` artifact must report zero for both measurements.
`generation.json` pins one product profile and its canonical semantic digest,
its local version, the exact subject-source digest, fit mode, and inferred
canvas policy. Replacing a subject preview invalidates
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
