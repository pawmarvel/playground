# Template Generator documentation

Start here. This page routes each role to the current source of truth. Detailed
commands live in one operating guide; contracts and future proposals do not
duplicate those commands.

## Choose by role

| Role | Start here | Use it for |
| --- | --- | --- |
| Design reviewer | [Reviewer guide](DESIGN_REVIEW_REVIEWER_GUIDE.md) | Vote, leave feedback, revise unsaved choices, and understand review rounds |
| Design/application operator | [Design-review operator guide](DESIGN_REVIEW_OPERATOR_GUIDE.md) | Run the gallery, process design decisions, resume graduated work, publish, and recover failed workflow actions |
| Template author | [MVP operations guide](MVP_OPERATIONS_GUIDE.md) | Develop art, pet runtime, layout, print, bundle, release, and troubleshoot exact CLI steps |
| Portfolio/category owner | [Design development and scaling guide](DESIGN_SCALING_GUIDE.md) | Choose the right flow for a new design, an existing-design improvement, another product profile, or a franchise/category expansion |
| FE engineer | [Bundle catalog and FE contract](MVP_PRODUCTION_BUNDLE_CATALOG_DESIGN.md#11-fe-implementation-quick-reference) | Consume immutable bundles and catalogs; implement name-mode routing, validation, preview/print rendering, activation, and rollback |
| Backend/tooling engineer | [Bundle catalog and FE contract](MVP_PRODUCTION_BUNDLE_CATALOG_DESIGN.md) | Maintain authoring/FE ownership boundaries, schemas, storage, publication, and integrity rules |
| Review-service engineer | [Design-review workflow design](DESIGN_REVIEW_WORKFLOW_DESIGN.md) | Maintain review identity, voting, lifecycle, retention, HTTP endpoints, and operator authorization |
| Automation engineer | [Authoring automation design](AUTHORING_AUTOMATION_DESIGN.md) | Maintain and extend the config-driven workflow without changing the public bundle contract |
| Product/engineering lead | [Future iterations](FUTURE_PERSONALIZATION_ITERATIONS.md) | Prioritize post-MVP quality, vendor, privacy, lifecycle, and scale work |

## Choose by task

| Task | Canonical instruction |
| --- | --- |
| Review proposed designs | [Reviewer guide](DESIGN_REVIEW_REVIEWER_GUIDE.md) |
| Start/operate a review round or move designs between pools | [Design-review operator guide](DESIGN_REVIEW_OPERATOR_GUIDE.md) |
| Bring a graduated design through evidence, layout, print, bundle, and S3 | [Design-review operator guide: bring a graduated design to release](DESIGN_REVIEW_OPERATOR_GUIDE.md#bring-a-graduated-design-to-a-local-release) |
| Develop a brand-new design from scratch-tested prompts | [MVP operations guide](MVP_OPERATIONS_GUIDE.md#5-develop-generated-or-empty-canvas-artpng) |
| Improve a released or in-progress design | [MVP operations guide: iterate after FE feedback](MVP_OPERATIONS_GUIDE.md#10-iterate-after-fe-trial-feedback) |
| Scale a design to another product profile | [Design development and scaling guide](DESIGN_SCALING_GUIDE.md#scale-an-approved-design-to-another-product-profile) |
| Scale a franchise/category from an approved design | [Design development and scaling guide](DESIGN_SCALING_GUIDE.md#scale-a-design-category-or-franchise) |
| Debug a generation or authoring stage | [MVP operations guide: focused debugging](MVP_OPERATIONS_GUIDE.md#15-focused-debugging-and-troubleshooting) |
| Implement or review FE bundle consumption | [FE quick reference](MVP_PRODUCTION_BUNDLE_CATALOG_DESIGN.md#11-fe-implementation-quick-reference) |
| Change a public schema or runtime contract | [Bundle catalog and FE contract](MVP_PRODUCTION_BUNDLE_CATALOG_DESIGN.md) |
| Run or extend workflow automation | [Operations section 11](MVP_OPERATIONS_GUIDE.md#11-config-driven-post-scratch-development-and-scaling) and [authoring automation design](AUTHORING_AUTOMATION_DESIGN.md) |

## Source-of-truth rules

1. **Runnable behavior:** CLI `--help`, schemas, tests, and the
   [MVP operations guide](MVP_OPERATIONS_GUIDE.md).
2. **FE/public contract:**
   [MVP production bundle catalog design](MVP_PRODUCTION_BUNDLE_CATALOG_DESIGN.md).
   Private authoring files and gallery state are never FE inputs.
3. **Review subsystem:**
   [design-review workflow design](DESIGN_REVIEW_WORKFLOW_DESIGN.md).
4. **Authoring automation:**
   [authoring automation design](AUTHORING_AUTOMATION_DESIGN.md). Implemented
   commands are listed in operations section 11 and CLI help; remaining
   milestones are explicitly marked deferred.
5. **Future priorities:**
   [future iterations](FUTURE_PERSONALIZATION_ITERATIONS.md).

When documents appear to conflict, the more specific source above wins. A
published bundle remains immutable regardless of later authoring documentation.

## Documentation maintenance

- Put exact operator commands in the MVP operations guide or the review
  operator guide, not in architecture documents.
- Put FE-facing fields and semantics only in the bundle contract.
- Put implementation history in Git commits. Do not add standalone review
  logs, superseded proposals, or temporary investigation notes to `docs/`.
- Extend the scaling guide with reusable scenario guidance, not one document
  per franchise.
- Keep examples clearly marked as examples; private `work/` paths and generated
  artifacts are not repository documentation or production contracts.

