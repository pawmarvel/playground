# Design review: operator guide

Audience: application owners and design-review operators. For template
authoring commands or engineering contracts, use the
[documentation index](README.md).

## Start a shared review

From the TemplateGenerator repository, install the editable command if needed:

```bash
.venv/bin/python -m pip install -e .
```

Set two different access codes without placing them in shell history:

```bash
read -s 'PAWMARVEL_GALLERY_REVIEWER_ACCESS_CODE?Reviewer access code: '
export PAWMARVEL_GALLERY_REVIEWER_ACCESS_CODE
printf '\n'
read -s 'PAWMARVEL_GALLERY_OPERATOR_ACCESS_CODE?Operator access code: '
export PAWMARVEL_GALLERY_OPERATOR_ACCESS_CODE
printf '\n'
```

Load the shared, design-independent AWS/S3 configuration before starting the
gallery. It should define `AWS_PROFILE`, `AWS_REGION`,
`PAWMARVEL_S3_BUCKET`, and `PAWMARVEL_S3_PREFIX`:

```bash
source "$PWD/work/configs/pawmarvel-shared.env"
aws sso login --profile "$AWS_PROFILE"
aws sts get-caller-identity --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

The gallery reads these values once at startup. It never stores AWS access or
secret keys in a design artifact or browser response.

Start the complete active pool on the trusted LAN:

```bash
.venv/bin/pawmarvel-gallery \
  --gallery-root "$PWD/work/design-inputs/Test Design Pool" \
  --abandoned-root "$PWD/work/design-inputs/Abandoned Design Pool" \
  --graduation-root "$PWD/work/design-inputs/Graduation Pool" \
  --release-root "$PWD/work/design-inputs/Release Pool" \
  --authoring-root "$PWD/work/authoring" \
  --exchange-root "$PWD/work/exchange" \
  --bind 0.0.0.0 \
  --port 8765 \
  --show-results
```

On macOS, obtain the Wi-Fi host address with `ipconfig getifaddr en0`. Keep the
terminal running for the duration of the review.

Share only the reviewer URL and reviewer code with the team:

```text
http://<team-host>:8765/
```

Keep the operator URL and operator code restricted to application owners:

```text
http://<team-host>:8765/operator
```

Do not expose the raw HTTP listener to the public internet. For remote review,
bind to loopback and use an organization-approved authenticated HTTPS tunnel.

### Start an external HTTPS review

Use this flow when reviewers are outside the operator's local network. A
`192.168.x.x` or `10.x.x.x` address is private and cannot be reached through the
public internet.

First confirm that Cloudflare Tunnel is installed:

```bash
cloudflared --version
```

Install it with `brew install cloudflared` if the command is unavailable. Set
different reviewer and operator access codes as shown above. Use strong random
values; share only the reviewer code with reviewers.

In terminal 1, start the authenticated gallery on loopback. Do not use
`--bind 0.0.0.0` for this external flow. Run this from the TemplateGenerator
repository root:

```bash
.venv/bin/pawmarvel-gallery \
  --gallery-root "$PWD/work/design-inputs/Test Design Pool" \
  --abandoned-root "$PWD/work/design-inputs/Abandoned Design Pool" \
  --graduation-root "$PWD/work/design-inputs/Graduation Pool" \
  --release-root "$PWD/work/design-inputs/Release Pool" \
  --authoring-root "$PWD/work/authoring" \
  --exchange-root "$PWD/work/exchange" \
  --bind 127.0.0.1 \
  --port 8765 \
  --show-results
```

In terminal 2, create a temporary HTTPS tunnel to that loopback listener:

```bash
cloudflared tunnel \
  --url http://127.0.0.1:8765 \
  --no-autoupdate
```

Wait for the `https://<random-name>.trycloudflare.com` address. Share only:

```text
https://<random-name>.trycloudflare.com/
```

Keep the following URL and the operator access code restricted to application
owners:

```text
https://<random-name>.trycloudflare.com/operator
```

Verify the external route from a third terminal:

```bash
PAWMARVEL_GALLERY_PUBLIC_URL="https://<random-name>.trycloudflare.com"

curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  "$PAWMARVEL_GALLERY_PUBLIC_URL/"
curl -sS -o /dev/null -w '%{http_code}\n' \
  "$PAWMARVEL_GALLERY_PUBLIC_URL/api/gallery"
```

The first command should return `303` and redirect to `/login`; the unauthenticated
API check should return `401`. Keep both terminals running during review. Press
Control-C in terminal 2 to remove external access, then stop terminal 1. A Quick
Tunnel has no uptime guarantee and receives a new URL after restart; use a named,
organization-managed tunnel before relying on this flow beyond temporary review.

## Monitor the active review

1. Open `/operator` and enter the operator access code.
2. Enter a stable operator role or team alias, such as `application-owner`.
3. Use **Active review** to inspect the ranked list.
4. Narrow the list by collection, title, bottom line, vote type, or current
   batch selection. Collection uses the value recorded in `concept-index.json`.
5. Review Graduate, Improve, Abandon, total-vote, and net-score values.
6. Expand **Reviewer feedback** before deciding.
7. Use **Refresh** to load votes saved since the last view. Filters and valid
   selections in the current lifecycle pool remain in place.

Rankings are decision support only. Confirm participation and written feedback;
the tool never graduates automatically.

## Process decisions in a batch

Use batch processing when several designs in one lifecycle tab share the same
decision reason:

1. In **Active review**, optionally filter the list, then select designs
   individually or use **Select all visible**. Selections remain selected when
   hidden by another filter.
2. Recheck the selected count and the highlighted cards.
3. Use **Current selection → Selected only** for a final batch review.
4. Select **Graduate selected** or **Abandon selected**.
5. Review the exact design IDs in the confirmation dialog.
6. Enter one reason that applies to every selected design.
7. Verify the success count and the destination lifecycle tab.

The batch is all-or-nothing. Before moving anything, the server verifies that
every design is in the lifecycle state required by the action, every source
folder exists, and no destination exists. It then moves all folders and records
an immutable decision event for each design in one transaction. A failure rolls
back all completed moves and records; the error identifies the conflicting path
or failed operation. Use the single-card actions when designs need different
reasons or dispositions.

To restore several abandoned or graduated designs:

1. Open **Abandoned** or **Graduated** and optionally filter the pool.
2. Select designs individually or choose **Select all visible**.
3. Use **Current selection → Selected only** to verify the exact batch.
4. Select **Restore selected to active review**, confirm the design IDs, and
   record one shared reason.
5. Verify every design moved to **Active review**.

Batch restore has the same all-or-nothing preflight, folder rollback, and
decision-event guarantees. A batch is limited to one visible lifecycle pool;
switching tabs clears selections that do not belong to the newly opened pool.

## Start a new round after improvement

Keep the design in `Test Design Pool` while improving its reference image or
prompt inputs. After the updated files have been reviewed locally:

1. Select **Start new review round**.
2. Confirm the exact design ID.
3. Enter a short improvement summary.
4. Verify the round number increased and all active counts returned to zero.
5. Ask reviewers to evaluate the updated design again.

The old round is excluded immediately. Its raw feedback is retained privately
for at most 30 days and its aggregate snapshot is preserved in the decision
event.

## Abandon a design

1. Review its vote totals and comments.
2. Select **Abandon** and confirm the design ID.
3. Enter the decision reason.
4. Verify it disappears from **Active review** and appears under **Abandoned**.
5. Verify the folder moved to:

```text
work/design-inputs/Abandoned Design Pool/<design-id>/
```

The design asset is not automatically deleted. Raw feedback expires after 30
days. Before expiration, **Restore to active review** returns the folder and its
votes. Start a new round afterward if the design was changed.

## Graduate a design

1. Review rankings, generated evidence, and written feedback.
2. Select **Graduate** and confirm the design ID.
3. Enter the selection reason.
4. Verify it appears under **Graduated** and its folder moved to:

```text
work/design-inputs/Graduation Pool/<design-id>/
```

The graduation pool is the queue for the existing art, pet, layout, print, and
bundle development workflow. Concept graduation is not production approval.
Use **Restore to active review** for an accidental decision or another concept
iteration.

## Bring a graduated design to a local release

The **Graduated** tab is an operator UI over the same immutable authoring
artifacts used by `MVP_OPERATIONS_GUIDE.md`. It does not introduce another
bundle format or edit an existing experiment in place. Work is grouped by
`<design-id>/<product-profile-id>` and shown in six stages. Each product shows
one **Next** badge. A stage with an immutable decision is rendered as a closed
`finished` disclosure by default; expand it to inspect its prompt, comparison,
and decision evidence without losing context around the outstanding step.

Every finished art, pet, and layout disclosure also offers **Redo / improve**.
Use it when later composed QA exposes a problem. Supply an audit reason, then
create a successor experiment/review and record a new winner through the normal
controls. Reopening never deletes or edits the earlier evidence. It writes
supersession records, removes the old choice from the active decision chain,
and invalidates only its dependents:

- reopening art keeps pet-runtime evidence but requires new layout and composed
  QA;
- reopening pet keeps art but requires new layout and composed QA; and
- reopening layout keeps art, pet, and the ranked layout proposals, allowing a
  different proposal to be selected before composed QA is regenerated.

Any unpublished local release is retained as superseded evidence and is no
longer offered for S3 publication. A later successful chain creates a new
bundle revision and release catalog. Published releases remain immutable and
cannot be reopened from this graduated-design control.

Expand **Operation guide → GUI coverage and offline handoffs** above the pool
filters for the complete CLI/UI boundary. Initial installation, private
configuration, disposable scratch tuning, and the first immutable art/pet
experiment remain offline. When a product or source experiment is missing, its
workflow card names the exact `MVP_OPERATIONS_GUIDE.md` section and tells the
operator which artifact to create before returning and selecting **Refresh**.
Provider/model/quality/reference/name-mode changes also remain offline because
a prompt-only GUI edit must not silently change the runtime contract. Manual
layout authoring is the offline fallback when no ranked proposal is acceptable.

The stages are:

1. **Art template.** Inspect every available comparison. To iterate, edit the
   copied prompt, enter a new experiment/review ID, and select **Run art +
   comparison**. This starts one background paid call and compares the source
   and new experiment side by side. Select a passing candidate and record its
   immutable winner decision.
2. **Pet transformation.** Edit the copied prompt and give the iteration a new
   ID. Use **Run smoke as new experiment** while tuning. After that experiment
   passes, refresh and use **Run release on current experiment** so smoke and
   release evidence stay on the same immutable runtime. **Run release as new
   experiment** is available when intentionally skipping smoke or evaluating a
   changed prompt directly. These actions run in the background and create the
   normal fixture selection, attempts, evaluation, and contact sheet. Review
   the result and record the winning reusable pet experiment.
   Only a release-tier pet review may become the production pet decision;
   smoke evidence remains visible but is not selectable as the winner.
3. **Layout.** After art and pet decisions exist, select
   **Generate deterministic proposals**. This is local and makes no paid model
   call. Inspect the ranked sheet and candidate previews. Selecting one imports
   its `layout.json` as an immutable attempt, creates and records the layout
   review decision, then generates the composed release-fixture comparison.
   If a layout decision already exists, the UI shows its comparison instead.
4. **Composed release QA.** Inspect the selected art, pet, and layout together
   against the release fixtures. If a layout decision came from the manual CLI
   and has no composition packet, select **Generate composed evidence**.
5. **Print finalist.** Select **Approve composition and prepare print finalist**
   only after the composed comparison passes visual review. The background job
   records assembly approval and creates high-resolution final/debug images,
   but does not graduate or bundle anything. Open both images and inspect them
   at full resolution. If they expose an art, pet, or layout issue, use that
   stage's **Redo / improve** action. Otherwise select **Approve print and build
   local release**; that action creates the graduation selection, next bundle
   revision, and local release catalog from the exact inspected finalist.
6. **Bundle and S3 release.** A local release remains local until a second
   explicit action. Verify the destination displayed in the card, then select
   **Publish verified release to S3**. The background job uses the same
   immutable publisher as `pawmarvel-catalog publish-s3 --execute`: assets are
   uploaded with conditional writes, read back and checksum-verified, the
   catalog is published last, and the publication receipt is recorded. Only
   after those steps succeed is the complete design-input folder moved from
   `Graduation Pool/<design-id>` to `Release Pool/<design-id>`. The refreshed
   operator view then moves the design from **Graduated** to **Released**.
   If S3 verification and receipt creation succeed but the local folder move
   fails, the Released evidence shows **Complete release-pool move**. Retrying
   that action reuses the existing receipt and performs only the pending local
   reconciliation; it does not upload a second release.

   Publication preflight automatically removes only `.DS_Store` and AppleDouble
   `._*` files from bundles referenced by that release and reports every removed
   absolute path in job progress. Strict validation then runs unchanged. Any
   other unexpected file remains a blocking integrity error for the operator to
   investigate.

Every comparison card presents the decision evidence that accompanies its
image sheet:

- hard-gate status and review mode;
- provider/model, successful calls, hard-gate pass rate, and minimum, median,
  and maximum latency for each candidate;
- selected fixture coverage and missing fixture IDs;
- failed attempts and their recorded error messages;
- top-level and candidate-specific warnings; and
- links to the immutable raw `evaluation.json` and recorded `decision.json`.

Warnings are displayed in amber and are never implied only by a later prompt.
When a selected art or pet candidate has warnings, the decision action repeats
the exact warning text and requires notes explaining why the warning is
acceptable. Candidates without warnings are recorded without an unnecessary
notes prompt. Composed release evidence is not a separate winner decision; it
is inspected before print preparation. If any selected component or composed
evidence still carries warnings, print preparation shows all of them and
requires explicit acceptance notes. Warning-bearing candidates are handled
individually rather than through the batch action. The later local-release
action requires a separate full-resolution print approval note.

The operator page and the running Python gallery process exchange an explicit
workflow-action capability contract. If source files are updated while the
gallery is already running, the page shows **Gallery server restart required**
and refuses to queue an action that the loaded backend does not support.
Restart the gallery process and hard-refresh the browser; do not retry until
the version warning clears. Unsupported actions are rejected before a
background-job receipt is created.

Deterministic layout proposals similarly show proposal runtime, name mode,
advisory warnings, score, maximum art overlap, and minimum edge clearance.
These are decision aids, not substitutes for visually checking every proposed
preview and the composed fixture sheet. Proposal warnings are carried into the
selected layout evaluation and decision; accepting such a proposal requires an
individual explanatory note and cannot be hidden inside a generic batch action.

The **Background work** panel reports queued/running/failed work and refreshes
while a job is active. The same status is shown beside the affected product, so
an operator does not need to scroll back to the global panel. Queued and running
jobs show a spinner, elapsed time, and the latest checkpoint (for example,
submitting a paid image call or building a local comparison). Controls for that
product are disabled until the job finishes, preventing an accidental duplicate
operation. Other products remain usable.

If a composition job receipt says `failed` but a valid composed comparison is
already present (for example, it was created by the preceding layout-acceptance
job), the immutable failed receipt remains on disk for diagnosis but the stale
failure is suppressed from both the global background-work panel and the
design/product workflow card. The workflow advances from the actual review
artifact rather than asking the operator to regenerate it.

Superseded reviews remain visible inside their stage with the operator, reason,
and immutable supersession record. They do not satisfy later workflow gates,
cannot be selected implicitly, and do not contribute warnings to the new active
release chain.

One job per design/product may run at a time, and a batch runs at most three
jobs concurrently to avoid an unbounded paid-provider burst. A failed job shows
the underlying error both globally and beside its product. It keeps the
immutable attempt/error record produced by the authoring tool; inspect that
record, correct the prompt or configuration, and use a new experiment, review,
attempt, or proposal ID as appropriate. A successful retry supersedes the stale
failure in the active UI, while every durable receipt remains on disk for audit.

If the gallery process stops while work is queued or running, startup converts
those persisted jobs to an explicit `OperatorProcessRestarted` failure. The UI
therefore never leaves an interrupted call looking active. Inspect provider and
authoring artifacts before retrying: a provider response may have arrived just
before the process stopped even though the operator receipt could not be
completed.

### Batch the shared next step

In **Graduated**, select multiple design cards. The green batch bar derives the
next outstanding step for each selected design. It enables one operation only
when all selected designs:

- have exactly one unfinished product profile, and
- are waiting on the same step.

Use the **Workflow step** filter, then **Select all visible**, to form a batch
without manually scanning every card. The filter is active only in Graduated.

The batch action uses each design's own immutable prompt, model configuration,
fixture evidence, and product profile. For art or release-pet decisions, choose
the winning experiment/attempt in each expanded pending review before selecting
**Record ... decision** in the batch bar. For layout, inspect each proposal and
select **Select for batch** on one candidate first. Paid art/pet batches show an
additional confirmation. Print-preparation batches require confirmation that
every selected composed comparison was reviewed; local-release batches require
that every generated finalist was reviewed at full resolution.

The selection is retained while its jobs run. The batch control is disabled
for those design/products and automatically advances to their next shared step
after the background jobs finish, so the operator does not need to find and
select the same group again.

Mixed-step selections are intentionally blocked with a list of each design's
current step. This avoids silently skipping QA or applying one design's IDs to
another. Designs with multiple unfinished product profiles remain individual
operations in the MVP so the target profile is explicit. S3 publication stays
an individual action in the MVP because it changes external state and requires
the operator to confirm the displayed bucket/prefix for each local release.

Once a product profile has both a local release entry and the verified
publication receipt, the tool moves the whole design-input folder from the
graduation pool to the release pool and the design moves from **Graduated** to
**Released** in the operator view. A local catalog without that receipt remains
**pending S3 publication** and stays in the graduation pool. The immutable
authoring, bundle, catalog, and receipt artifacts remain in their established
roots. The **Released** tab is a read-only trace of every local revision and its
publication state. Its default card shows the reference design without
expanding generated artifacts. Select
**View release evidence** for a product profile to inspect its art comparison,
pet comparison, pet release composition, layout evidence, print finalist, and
publication record. Published designs present only in the release
catalog/authoring records are also listed,
using the bundle reference image (or `art.png` for a no-reference bundle).

The CLI publication sequence in `MVP_OPERATIONS_GUIDE.md` remains the recovery
and automation alternative. The operator action and CLI share the same
publisher and receipt contract; a local catalog entry alone is never proof that
upload succeeded.

## Recovery and data handling

- Never copy one design ID into more than one lifecycle pool.
- Do not manually move only part of a design folder.
- Keep every design's metadata entry in `Test Design Pool/concept-index.json`
  even while its folder is abandoned, graduated, or released.
- Restart the gallery after any emergency manual filesystem repair.
- Treat `/api/results.json`, `/api/results.csv`, and the SQLite database as
  private reviewer data.
- Delete backups and exports containing removed-design feedback no later than
  the same 30-day deadline.
- Review startup warnings for missing, duplicated, restored, or purged designs.

The durable local review store is:

```text
work/gallery-reviews/patrol-franchise/
  gallery-votes.sqlite3
  decisions/<design-id>/<timestamp>-<event>-<event-id>.json
  operator-jobs/<job-id>.json
```
