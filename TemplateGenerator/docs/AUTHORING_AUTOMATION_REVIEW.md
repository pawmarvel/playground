# Authoring automation proposal — review record

Proposal: [Config-driven authoring automation](AUTHORING_AUTOMATION_DESIGN.md).
Review date: 2026-10-04. Scope: documentation and implementation planning only.

Priorities: P0 = breaks FE contract or approval integrity; P1 = material quality,
correctness or operational failure; P2 = workflow/maintenance ambiguity that
should be resolved before implementation. The rounds below are sequential
self-reviews against source and schemas, followed by edits to the proposal.
They are not independent external reviews or evidence of implemented features.

## Round 1 — contract, inputs and evidence

| Priority | Finding | Disposition |
| --- | --- | --- |
| P0 | Merely saying “same schema version” permits relaxed validation or new asset fields to break existing FE consumers | Fixed in section 3: pinned pre-automation validator/schema/renderer compatibility in both directions, no new public fields/assets |
| P1 | Initial config example omitted required inputs and source scenario fields; operators could repeat today's path/default mistakes | Fixed in section 5: complete core example, explicit scenario/source rules, config-relative paths, source-derived defaults and stdout path contract |
| P1 | Generation can differ from review if plans hash mutable paths but execution rereads changed files | Fixed in section 6: snapshot mutable inputs at planning time and execute only pinned bytes |
| P1 | Source code's assembly comparison renders the first sorted successful pet instead of the layout's pinned representative | Added required fix and test in sections 11–12; matrix tests remain separate from exact golden/print identity |
| P1 | Local art copy/resize is not represented by current private experiment transport enum; claiming ordinary API generation would falsify provenance | Fixed in sections 9 and 12: explicit private versioned derivation, retain v1 reading, existing public art fields and canonical prompt only |
| P2 | “Review” could imply smoke approval or generate production decisions prematurely | Fixed in section 5.2: defined shortlist versus art/pet/layout/assembly/graduation decisions and explicit phase plan/execution split |

Round 1 outcome: findings above addressed; proceed to execution/quality review.

## Round 2 — generation quality, costs and restart behavior

| Priority | Finding | Disposition |
| --- | --- | --- |
| P1 | A public bundle has pet runtime settings but not source art model/quality or every art-only reference; complete inheritance was overstated | Fixed in section 5.1: art settings are explicit target configuration and source facts are limited to verified bundle content |
| P1 | Source art plus four references exceeds the current limit; implicit truncation could change design behavior | Fixed in section 5.1: separate resolved art/pet arrays, replace semantics, explicit subset choice and no silent truncation |
| P1 | Coverage could count smoke, failed, changed-image or artistic-name probe runs incorrectly | Fixed in sections 5, 7 and 8: distinct exact inventories, input hashes, successful reviewed representative and separate embedded-name probes |
| P1 | Per-phase budgets and file-existence resume could duplicate paid calls or hide interrupted submissions | Fixed in section 6: cumulative allowance, durable result reconciliation and explicit unknown outcomes/retry planning |
| P1 | Source reviews or their hashes could be mistaken for target quality approval | Fixed in section 9: verified published-source loading, honest target records and new target reviews without fake original attempts |
| P2 | A broad layout search could explode render time/memory or trade away legibility for fit | Fixed in section 8: bounded search, fixed visual minimums, worst-case ranking, streamed preview renders and measured performance |
| P2 | Optional AI prompt editing and manual fallbacks lacked a clear bounded implementation path | Fixed in section 10: explicit analysis configuration, reviewed full prompt bytes, optional adapter, no required new prompt grammar |

Round 2 outcome: findings above addressed; proceed to lifecycle, execution-plan
and roadmap consistency review.

## Round 3 — operator simplicity, lifecycle and delivery

| Priority | Finding | Disposition |
| --- | --- | --- |
| P1 | Existing cleanup protects graduated records but could remove an active workflow's reviewed inputs before completion | Fixed in section 6.1 and M1: minimal active-run reference protection, explicit retirement, preserve selected/receipt history; general retention graph remains deferred |
| P1 | A fresh machine or package-task retry could allocate an already-published revision or allocate twice | Fixed in section 6.2 and M5: explicit revision reservation, task reconciliation, remote/current inventory recovery and unchanged conditional publication |
| P1 | Re-publication of the source bundle can require a source graduation that no longer exists | Fixed in section 6.2: deriving a new target does not implicitly re-list the source in its release |
| P2 | Separate plan/run/review commands at every checkpoint add needless operator steps | Fixed in section 5.2: one review-and-continue action, one config per scenario, automatic downstream path resolution |
| P2 | Six milestone rows lacked an actionable extraction order, ownership and rollback behavior | Fixed in section 13: reviewable implementation slices, dependencies, named roles, pilot set, compatibility exits and operational rollback |
| P2 | Multi-candidate grids and a generic workflow language would enlarge initial scope | Fixed in sections 6 and 10: small task dispatcher, one art/pet candidate per config, existing cross-experiment comparisons |
| P2 | Earlier planned profile-port instructions and the roadmap could conflict with this proposal | Fixed: consolidated the older planned profile-port sections, linked the authoritative proposal, retained currently runnable operations and aligned overlapping roadmap items |

Round 3 outcome: findings above addressed; none is deferred as an unresolved
design P0/P1/P2.

## Final handoff check

Three review/update rounds completed. No identified P0/P1/P2 proposal findings
remain unresolved. Required implementation fixes, including the assembly
representative mismatch, are explicitly assigned to milestones; this is not a
claim that the current code has been fixed or certified.

Documentation checks cover JSON examples, shell syntax, local document links,
whitespace and removal of conflicting planned profile-port instructions. No
application code, production assets or runnable operation commands changed.
Compatibility and quality acceptance tests are milestone exit criteria, not
tests claimed to have run for this documentation-only change.
