# Design review: operator guide

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

Start the complete active pool on the trusted LAN:

```bash
.venv/bin/pawmarvel-gallery \
  --gallery-root "$PWD/work/design-inputs/Test Design Pool" \
  --abandoned-root "$PWD/work/design-inputs/Abandoned Design Pool" \
  --graduation-root "$PWD/work/design-inputs/Graduation Pool" \
  --authoring-root "$PWD/work/authoring" \
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

## Monitor the active review

1. Open `/operator` and enter the operator access code.
2. Enter a stable operator role or team alias, such as `application-owner`.
3. Use **Active review** to inspect the ranked list.
4. Narrow the list by collection, title, bottom line, vote type, or current
   batch selection. Collection uses the value recorded in `concept-index.json`.
5. Review Graduate, Improve, Abandon, total-vote, and net-score values.
6. Expand **Reviewer feedback** before deciding.
7. Use **Refresh** to load votes saved since the last view. Filters and valid
   active selections remain in place.

Rankings are decision support only. Confirm participation and written feedback;
the tool never graduates automatically.

## Process decisions in a batch

Use batch processing when several active designs share the same disposition and
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
every design is active, every source folder exists, and no destination exists.
It then moves all folders and records an immutable decision event for each
design in one transaction. A failure rolls back all completed moves and records;
the error identifies the conflicting path or failed operation. Use the
single-card actions when designs need different reasons or dispositions.

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

## Recovery and data handling

- Never copy one design ID into more than one lifecycle pool.
- Do not manually move only part of a design folder.
- Keep every design's metadata entry in `Test Design Pool/concept-index.json`
  even while its folder is abandoned or graduated.
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
```
