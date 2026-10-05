# Private design-review gallery

Use `pawmarvel-gallery` to share active reference-design candidates with a
small trusted team, collect one updatable recommendation and comment per
reviewer/design, and let an application owner manage the concept lifecycle.
This is a shortlist workflow; graduation here does not approve art, layout,
print, a bundle, or a production release.

The workflow has two deliberately separate surfaces:

- Reviewers use `/` to inspect active designs and submit **Graduate**,
  **Improve**, or **Abandon** feedback, then save all changed cards in one
  atomic action. They cannot see rankings, other reviewers' feedback, exports,
  or lifecycle controls.
- Operators use `/operator` to see ranked current-round results and feedback,
  start a clean round after an improvement, or move a design to the abandoned
  or graduation pool. Active designs with a shared decision can be graduated
  or abandoned as one all-or-nothing batch. Operators may restore either
  outcome.

Use these documents as the source of truth:

- [Workflow and data design](DESIGN_REVIEW_WORKFLOW_DESIGN.md)
- [Reviewer guide](DESIGN_REVIEW_REVIEWER_GUIDE.md)
- [Operator guide](DESIGN_REVIEW_OPERATOR_GUIDE.md)

The durable SQLite database and decision events live outside the design pools
under `work/gallery-reviews/patrol-franchise/`. Abandoned and graduated raw
review feedback is retained for at most 30 days. The design folders themselves
are moved between `Test Design Pool`, `Abandoned Design Pool`, and
`Graduation Pool`; the tool does not automatically delete them.

For a shared LAN listener, configure two different access codes. Share only
the reviewer URL and reviewer code with the review team. The access-code gate
is an MVP control for a trusted team, not a replacement for SSO, TLS, or
organization authorization.
