# Config-driven authoring and scaling design

Audience: backend/tooling engineers maintaining authoring automation. Template
authors should use the [operations guide](MVP_OPERATIONS_GUIDE.md#11-config-driven-post-scratch-development-and-scaling).

Status: implemented MVP coordinator through art, pet-smoke, and pet-release
evidence. Deterministic layout proposals and the existing manual/operator
graduation path remain the downstream implementation. Date: 2026-10-09.

## 1. Goal and quality boundary

Reduce repeated configuration and paid reruns for three paths without allowing
automation to approve aesthetics:

1. a new design after its art and pet prompts pass scratch review;
2. an approved design adapted to another product profile;
3. a category/franchise variant derived from an approved bundle and an explicit
   Preserve/Change/Forbid delta.

The coordinator snapshots inputs once, estimates calls before execution, runs
only to a named review checkpoint, and reconciles exact completed artifacts on
retry. A human still reviews image quality, pet identity, transparent edges,
text/props, composition, latency/failures, fixture coverage, layout, and print.

## 2. Contract boundary

The public FE contract is unchanged:

- bundle schema remains `bundle-v1`;
- release catalog remains `release-catalog-v2`;
- layout remains `layout-v2` and product profile remains `product-profile-v1`;
- `template_id`, immutable revision allocation, S3 keys, asset inventory,
  runtime reference order, name-mode routing, normalization, renderer geometry,
  and publication integrity are unchanged;
- every target bundle is self-contained; FE never resolves a base design,
  workflow, source bundle, category preset, or authoring tree;
- FE owns product mapping, activation, rollback, customer approval, and orders.

`workflow.json`, plans, run records, approvals, deltas, and source lineage are
private operator artifacts. The bundle builder cannot inventory them.

## 3. Architecture

`pawmarvel-author workflow` is a narrow coordinator over existing services:

```text
workflow init
  -> validate/snapshot profile, prompts, ordered references, fixtures, protocol
workflow plan
  -> no-cost immutable task list + hashes + paid-call estimate
workflow run
  -> create_experiment / run_attempt / benchmark / compare
workflow approve
  -> immutable, hash-bound human checkpoint
existing tools
  -> decisions -> layout proposal/editor -> assembly -> print -> graduation
  -> bundle -> release -> explicit S3 publication
```

There is no general DAG language, database, queue, provider-side scheduler, or
automatic winner. One process writes a product workspace at a time. Existing
low-level commands remain the debugging and exceptional-operation interface.

The coordinator supports these checkpoints:

| Checkpoint | Automatic output | Required review before continuing |
| --- | --- | --- |
| `art-review` | art experiment, one art attempt, art comparison | Art ownership, fixed content, alpha, target geometry |
| `smoke-review` | art evidence plus one pet attempt per selected smoke fixture and comparison | Art acceptance and initial pet identity/style/latency |
| `release-review` | reconciled art/smoke work plus selected release fixtures and comparison | Requires candidates approval first; inspect complete release evidence |

Release approval is the handoff to ordinary art/pet decisions. It is not a
substitute for the decision files required by layout, graduation, or bundle
assembly.

## 4. Private schemas and storage

The closed private schemas are:

- `schemas/authoring-workflow-v1.schema.json`;
- `schemas/authoring-plan-v1.schema.json`.

Storage is target-owned:

```text
work/workflows/<workflow-id>/
  workflow.json
  inputs/
    product-profile.json
    art-template-<provider>.md       # absent for deterministic empty canvas
    pet-transform-<provider>.md
    references/reference-design-NNNN.<ext>
    variant-delta.json               # category variant only
    evaluation-protocol.json
    fixture-sets/{smoke,release}/{fixture-set.json,selection.json,images/...}
  approvals/{scratch,candidates,release}.json
  plans/<workflow>--<checkpoint>--<fingerprint>.json
  runs/<plan-id>.json

work/authoring/<design-id>/<product-profile-id>/
  experiments/{art,pet,layout}/...
  reviews/{art,pet,layout,assembly}/...
  print-candidates/...
  graduations/...
```

Mutable source paths are read only during initialization. Prompts, target
profile, references, fixture manifests/images/selections, evaluation protocol,
and optional delta are copied under `inputs/`. Execution reads these copies.
The source-bundle path and manifest digest remain provenance; the source
authoring tree is not required.

Plans bind the specification hash, every snapshotted input hash/size, applicable
approval hash, task IDs, resolved experiment/review IDs, warnings, and call
estimate. Editing a snapshot after planning invalidates execution. Approved
input changes require a successor workflow ID rather than overwriting evidence.

## 5. Configuration rules

Initialization records:

- workflow/scenario and target design/profile identity;
- art provider/model/quality or deterministic empty-canvas mode;
- production pet provider/model/quality;
- name mode and representative embedded name when applicable;
- ordered zero-to-four references;
- smoke/release fixture selections and evaluation protocol;
- a maximum paid-call budget;
- optional validated source bundle and category delta.

Provider credentials and AWS settings remain in the ignored shared `.env`; they
are never copied into workflow JSON, plans, events, or bundles.

The production pet runtime remains OpenAI because the current `bundle-v1`
allowlist does not declare Gemini. A future provider expansion requires an
explicit FE contract change. Art may use any provider already supported by the
offline authoring experiment.

Name mode is validated before calls:

| Mode | Pet prompt | Layout contract |
| --- | --- | --- |
| `layout-text` | must omit `{{PET_NAME}}` | separate font/name region later |
| `embedded-in-pet` | must contain `{{PET_NAME}}`; representative name required | no separate name layer |
| `none` | must omit `{{PET_NAME}}` | no name layer |

New designs default to `layout-text`, OpenAI Sunburst, high art quality, and low
pet quality unless explicitly changed. Derived workflows inherit pet runtime,
name mode, and representative QA name from the validated source bundle unless
the operator provides an override. Public bundles do not retain original art
generation settings, so target art settings remain explicit/current defaults
and are freshly reviewed.

## 6. Scenario behavior

### New design

The operator supplies prompt drafts, target profile, and ordered references.
Prompt-only/no-reference is valid. `--empty-canvas` replaces the art prompt and
art API call with a deterministic all-zero transparent PNG. Prompts that were
already scratch-tested may initialize with `--scratch-approved`; otherwise the
operator records a scratch approval after inspecting the copied inputs' output.

### Product-profile expansion

The exact source bundle directory must validate before initialization, and the
target retains its source `design_id` with a different profile ID. The tool:

1. copies source prompts and finished-design references;
2. inherits the declared production pet runtime and name mode;
3. appends a target-profile block to the copied art prompt with source/target
   dimensions and whether the ratio changed;
4. warns when explicit visual reflow review is needed;
5. produces fresh target art and pet evidence.

It never stretches/crops source art, reuses source fixture approval, or creates
a runtime dependency on the source bundle. Equal ratio is still processed via
fresh target evidence in MVP; deterministic source-art import/resize remains a
later optimization because it needs honest private derivation provenance.

### Category/franchise variant

The target uses a new design ID, a validated source bundle, one or more explicit
target finished-design references, and a JSON delta with at least one change.
Supported keys are common and optional layer-specific `preserve`, `change`, and
`forbid` lists plus notes. Initialization appends deterministic requirements to
copied art/pet prompts and warns that they are drafts. The operator edits and
scratch-tests those copies, records scratch approval, then runs the normal
evidence checkpoints. The target cannot silently reuse only the source finished
reference.

## 7. Human gates and budget safety

Scratch approval binds the complete input inventory. Changing a prompt,
reference, fixture selection, profile, or protocol makes that approval stale.
Candidate approval binds the immutable art and smoke evaluations. Release
approval binds the release evaluation. Evaluation warnings require
`--accept-warnings`; hard-gate failures cannot be accepted by that option.

Planning performs no provider call. It reports total planned image calls. The
plan is rejected if the estimate exceeds `execution.max_paid_calls`. A typical
one-art + two-smoke + fifteen-release plan is eighteen calls. Unknown provider
outcomes are not silently retried; a `.partial` attempt stops execution for
operator inspection.

## 8. Restart and mismatch behavior

Experiment, attempt, and review creation retains existing immutable semantics.
On restart:

- a matching experiment is reused only when identity, provider/model/quality,
  prompt hash, ordered reference hashes, and profile identity match;
- a benchmark attempt is reused only when status, experiment hash, input-pet
  hash, and resolved generation request match;
- conflicting existing attempts fail the entire resume preflight before any
  missing paid call starts;
- exact existing review packets are reused; unknown partial attempts stop;
- run state appends reconciliation/success/failure events and preserves the
  original start time.

Provider failures remain visible as incomplete fixture coverage so an
application owner can decide whether to retry or explicitly accept the gap.
The tool never fabricates a success or hides failed latency samples.

Assembly comparison resolves the representative pet pinned by the selected
layout experiment rather than choosing the first sorted successful pet. This
keeps assembly, print, and bundle QA on the same lineage.

## 9. Selective iteration

| Changed input | Re-run | May remain selected |
| --- | --- | --- |
| Art prompt/model/output | art review, layout/assembly, print, graduation/package | unchanged pet runtime evidence |
| Pet prompt/model/quality/references/name mode | smoke/release, layout representative, assembly, pet print, graduation/package | accepted art |
| Layout/font/geometry | layout matrix/decision, assembly, print, graduation/package | art and pet generation |
| Target profile | independent target workflow and all target geometry/print checks | copied source intent only |
| Fixture selection/protocol | evidence bound to that exact selection/protocol | matching image attempts only when resume checks pass |

Sibling prompt/model candidates use different workflow IDs and existing
cross-experiment comparisons. The coordinator deliberately does not introduce
an unbounded model/prompt grid.

## 10. Implemented and remaining milestones

| Milestone | Status | Exit condition |
| --- | --- | --- |
| Private contract baseline | Implemented | workflow/plan schemas validate; existing public schemas unchanged |
| Post-scratch evidence runner | Implemented | new design reaches release review with immutable gates and restart checks |
| Deterministic layout assistance | Implemented by existing `propose-layout` path | exact renderer, bounded local search, manual acceptance |
| Profile expansion initializer/evidence | Implemented MVP path | validated source creates independent target snapshots and fresh evidence |
| Category delta initializer/evidence | Implemented MVP path | reviewed delta creates editable target prompt copies and fresh evidence |
| Guided layout-through-publication coordinator | Not implemented | existing operator GUI/manual commands remain authoritative |
| Exact-ratio art import/resize | Deferred | requires truthful private derivation schema and compatibility tests |
| AI-assisted prompt derivation | Deferred | optional, budgeted, human-reviewed adapter only |

The next implementation increment should connect release approval to ordinary
decision recording and initialize `propose-layout` without retyping paths. It
must preserve the same manual visual checkpoint and bundle contract. Packaging
and S3 publication already exist in the operator workflow and should be reused,
not duplicated inside this coordinator.

## 11. Validation and rollback

Automated tests cover schemas, input snapshots, repeatable planning, call
budgets, stale-input rejection, quality gates, no-cost empty-canvas execution,
category prompt derivation, exact benchmark resume, and layout-pinned assembly.
Provider calls remain opt-in.

Rollback is operational: stop using `pawmarvel-author workflow` and continue
with the existing low-level commands against ordinary artifacts already
created. Never edit a published bundle or immutable attempt to roll back. FE
continues consuming unchanged validated bundle revisions.

Deferred scaling work—automatic segmentation, learned aesthetic ranking,
remote authoring queues, provider expansion, vendor qualification, and richer
lifecycle/retention—is prioritized in
[future iterations](FUTURE_PERSONALIZATION_ITERATIONS.md).
