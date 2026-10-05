# Design review workflow and data design

## Purpose

This workflow turns candidate reference designs into a controlled team review.
Reviewers recommend **Graduate**, **Improve**, or **Abandon** and may leave
feedback. An operator—not the vote count—owns the lifecycle decision. The
workflow is local/offline MVP tooling and does not change the production bundle
or FE contract.

## Roles and trust boundary

| Role | Can do | Cannot do |
|---|---|---|
| Reviewer | See active designs, inspect available generated evidence, save or revise their own current-round vote and comment | See rankings, exports, other reviewer identities, reset rounds, or move designs |
| Operator | See rankings and all feedback, start a new round, graduate, abandon, restore, and export results | Publish a production bundle merely by graduating a concept |

Reviewer and operator sessions use different access codes, cookies, pages, and
server-side API authorization. UI hiding is not treated as authorization. For a
LAN/shared listener, both access codes are mandatory and must differ. This is a
trusted-team MVP control; use SSO before broader deployment.

## Lifecycle

```text
Test Design Pool (active)
  |
  +-- improve --> update design --> new clean review round --> active
  |
  +-- abandon --------------------------------> Abandoned Design Pool
  |                                                   |
  |                                                   +-- restore --> active
  |
  +-- graduate -------------------------------> Graduation Pool
                                                      |
                                                      +-- restore --> active
```

Graduation selects a concept for later bundle development. It is not art,
layout, print, bundle, or production approval.

The operator console is the only supported lifecycle writer. It moves the
complete design directory and updates SQLite under one process-level lock. The
destination must not exist. If persistence fails after the move, the tool moves
the directory back; startup reconciliation also detects externally interrupted
moves.

## Filesystem contract

```text
work/design-inputs/
  Test Design Pool/
    concept-index.json                 # stable metadata inventory
    <active-design-id>/
  Abandoned Design Pool/
    <abandoned-design-id>/
  Graduation Pool/
    <graduated-design-id>/

work/gallery-reviews/patrol-franchise/
  gallery-votes.sqlite3
  decisions/
    <design-id>/
      <timestamp>-<event>-<event-id>.json
    _reconciliation/
      <design-id>.json                   # externally moved/missing repair summary
```

`concept-index.json` remains the metadata inventory for active and inactive
designs. Operator transitions move the design folder only; they do not remove
the inventory entry. A design ID may exist in exactly one lifecycle pool.
Duplicates are a hard startup error. An existing folder without
`reference-design.png` is also a hard error. An inventory entry absent from all
three pools is reported for repair.

The SQLite database is deliberately outside every design pool so moving a
folder cannot remove votes.

## Review-round and vote model

The MVP keeps current votes in `votes` and moves prior-round raw votes to
`archived_votes` when the operator starts a new round.

```text
design_registry
  design_id                 primary key
  metadata_json
  lifecycle_state           active | abandoned | graduated
  current_pool_path
  review_round              monotonically increasing integer
  removed_at
  purge_after
  raw_purged_at
  disposition and decision metadata

votes
  reviewer_id + design_id   primary key
  reviewer_name
  choice                    graduate | consider | pass
  comment
  updated_at

archived_votes
  design_id + review_round + reviewer_id
  raw prior-round vote fields
  purge_after

design_events
  immutable reset/graduate/abandon/restore event
  lifecycle transition
  operator identity
  reason
  review round
  aggregate vote snapshot
```

The storage values `consider` and `pass` are rendered as **Improve** and
**Abandon**. This preserves the existing database constraint while presenting
the intended workflow language.

Starting a new review round immediately removes previous votes from the active
ranking and reviewer UI. Raw prior-round votes remain operator-only for at most
30 days; aggregate event counts remain after raw data expires.

Reviewer filters operate entirely in the browser over the active designs and
the current reviewer's own saved or unsaved choices. They support collection,
title/design ID, bottom line, vote choice, and whether a recommendation is
currently selected. They never expose aggregate votes or another reviewer's
data. Filtering does not remove hidden changes from the atomic batch save.

## Graduation, abandonment, restoration, and retention

On graduation or abandonment:

1. Capture aggregate vote counts in an immutable event.
2. Move the design folder to the selected lifecycle pool.
3. Remove it from the reviewer gallery.
4. Keep current raw votes and comments for 30 days.
5. Purge reviewer identities, raw votes, and comments after expiration.
6. Keep the design assets and non-PII aggregate decision event until an
   operator explicitly cleans them up.

Restoring within the retention window preserves the current round and its
votes. The operator may then deliberately start a clean round after an
improvement. Restoring after raw data was purged starts the next round with no
votes.

Database and export backups containing reviewer data must obey the same
30-day maximum. SQLite secure deletion is enabled and its WAL is truncated when
purging; backup retention remains an operator responsibility.

## Operator ranking

Only active designs and current-round votes participate. Ranking is stable and
transparent:

1. Graduate count, descending
2. Net score (`graduate - abandon`), descending
3. Total vote count, descending
4. Design ID, ascending

The console always displays all three counts, total participation, written
feedback, and round number. Operators can filter the current lifecycle pool by
collection, title/design ID, bottom line, presence of a
specific vote, and current batch selection. Filtering is client-side decision
support and never changes ranking, selection, or lifecycle data. It never
auto-graduates a design.

## HTTP contract

Reviewer surface:

```text
GET  /                         gallery
GET  /api/gallery              active designs and reviewer's own votes
POST /api/vote                 upsert one current-round vote (compatibility)
POST /api/votes                atomically upsert all changed reviewer votes
GET  /assets/...               active reference images only
GET  /review-assets/...        available generated review evidence
```

Operator surface:

```text
GET  /operator
GET  /api/operator/designs     ranked active and inactive pool views
POST /api/operator/action      new-round | abandon | graduate | restore
POST /api/operator/actions     atomically abandon or graduate selected designs
GET  /operator-assets/...      reference images from any lifecycle pool
GET  /api/results.json
GET  /api/results.csv
```

Reviewer sessions receive `403` for operator APIs and exports.

Batch lifecycle decisions use one operator identity and reason for all selected
designs. The server preflights every source and destination before making a
change, then records one immutable event per design in one database transaction.
If any folder move or event write fails, the complete batch is rolled back.
Starting a new review round and restoring a design remain individual actions
because their recovery context is design-specific.

## MVP boundaries

- One running gallery process and one serialized operator transition or batch at
  a time.
- Access codes rather than SSO/RBAC.
- Local SQLite and same-filesystem atomic directory moves.
- No quorum enforcement or automatic decision.
- No automatic deletion of abandoned design assets.
- No production bundle publication from this interface.
