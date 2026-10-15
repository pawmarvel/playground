const operatorMode = window.location.pathname === "/operator";
const stateUrl = operatorMode ? "/api/operator/state" : "/api/state";
let currentState = null;
let previewBackground = "checker";
let activeOperatorTab = "active";
const selectedPrintDesignIds = new Set();
const selectedSubjectIds = new Set();
const selectedGenerationSeedIds = new Set();
const batchSubjectIds = new Set();
const batchProfileIds = new Set();
const productPlans = new Map();
const selectedBundleCandidates = new Map();
const selectedLifecycleSeeds = new Set();
const pendingReviewerFeedback = new Map();
const expandedProductCoverage = new Set();
const expectedOperationIds = new Set();
const observedOperationIds = new Set();
const operationCompletedCounts = new Map();
let operationPollTimer = null;
let operationPollRunning = false;
let publicationRefreshTimer = null;

const $ = (id) => document.getElementById(id);
const node = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};

function errorRecommendation(message) {
  const value = String(message).toLocaleLowerCase();
  if (value.includes("token has expired") || value.includes("sso")) {
    return "Run aws sso login for the configured AWS profile, then retry.";
  }
  if (value.includes("config profile") && value.includes("could not be found")) {
    return "Correct AWS_PROFILE in work/static-print.env and restart the server.";
  }
  if (value.includes("accessdenied") || value.includes("access denied")) {
    return "Verify that the configured AWS role can read and write the target S3 prefix.";
  }
  if (value.includes("does not exist") || value.includes("no such file")) {
    return "Inspect the reported absolute path and regenerate or restore the missing artifact.";
  }
  if (value.includes("immutable") || value.includes("already exists") || value.includes("conflict")) {
    return "Refresh the workflow and use the automatically allocated next version or release ID.";
  }
  if (value.includes("timeout") || value.includes("connection") || value.includes("network")) {
    return "Check network connectivity and credentials, then retry or resume the unfinished operation.";
  }
  return "Expand the run report, correct the failed work unit, then retry, resume, or dismiss it.";
}

function showStatus(message, error = false) {
  const status = operatorMode ? $("operator-status") : $("reviewer-status");
  status.classList.remove("hidden", "error");
  if (error) status.classList.add("error");
  if (!operatorMode) {
    status.textContent = message;
    return;
  }
  let value = String(message);
  if (error && !value.toLocaleLowerCase().includes("recommended fix")) {
    value += `\nRecommended fix: ${errorRecommendation(value)}`;
  }
  const lines = value.split("\n");
  $("operator-status-summary").textContent = lines[0];
  const detail = $("operator-status-detail");
  detail.replaceChildren();
  if (lines.length > 1) detail.append(node("p", "", lines.slice(1).join("\n")));
  status.open = lines.length > 1;
}

function newOperationId(kind) {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${kind}-${suffix}`.toLowerCase();
}

function renderOperationStatus(operation) {
  const status = $("operator-status");
  status.classList.remove("hidden", "error");
  if (["failed", "interrupted"].includes(operation.status)) status.classList.add("error");
  const counts = operation.status_counts || {};
  const completed = (counts.succeeded || 0) + (counts.skipped || 0);
  const pending = (counts.pending || 0) + (counts.running || 0);
  const errors = operation.error_count || operation.failed_count || 0;
  const warnings = operation.warning_count || 0;
  const completionWord = operation.kind === "release-publication"
    ? "published"
    : operation.kind === "release-validation"
      ? "validated"
      : "complete";
  const suffix = operation.status === "interrupted"
    ? " · interrupted"
    : operation.status === "failed"
      ? " · failed"
      : operation.status === "cleaned"
        ? " · dismissed/cleaned"
        : "";
  $("operator-status-summary").textContent = `${operation.label} · ${completed} ${completionWord}, ${pending} pending, ${errors} error, ${warnings} warning${suffix}`;
  const detail = $("operator-status-detail");
  detail.replaceChildren();
  if (operation.current) {
    detail.append(node("p", "", `Currently running: ${operation.current}`));
  }
  if (operation.error) detail.append(node("p", "error", operation.error));
  const output = operation.recent_output || [];
  if (output.length) {
    detail.append(node("p", "meta", `Recent output · latest ${output.length}`));
    const list = node("ul", "operation-status-list");
    for (const event of [...output].reverse()) {
      const time = event.created_at ? event.created_at.slice(11, 19) : "";
      const row = node(
        "li",
        event.severity,
        `${time ? `${time} · ` : ""}${event.message}`,
      );
      list.append(row);
    }
    detail.append(list);
  }
  const failures = operation.items.filter(item => item.status === "failed");
  for (const item of failures) {
    detail.append(node("p", "error", `Failed work unit: ${item.label} · ${item.error || "unknown error"}`));
  }
  if (["failed", "interrupted"].includes(operation.status)) {
    const actions = node("div", "operation-actions");
    if (operation.resumable) {
      const resume = node("button", "primary", "Resume remaining work");
      resume.type = "button";
      resume.addEventListener("click", () => runOperationAction(operation, "resume"));
      actions.append(resume);
    }
    const cleanup = node(
      "button",
      "secondary",
      operation.resumable ? "Clean up incomplete work" : "Dismiss failed report",
    );
    cleanup.type = "button";
    cleanup.addEventListener("click", () => runOperationAction(operation, "cleanup"));
    actions.append(cleanup);
    detail.append(actions);
  }
  if (!observedOperationIds.has(operation.operation_id)) {
    observedOperationIds.add(operation.operation_id);
    status.open = true;
  }
}

async function runOperationAction(operation, action) {
  const verb = action === "resume" ? "Resuming" : "Cleaning";
  showStatus(`${verb} ${operation.label}…\nOperation ID: ${operation.operation_id}`);
  if (action === "resume") monitorOperation(operation.operation_id);
  try {
    const result = await request("/api/operator/operation-action", {
      method: "POST",
      body: JSON.stringify({ operation_id: operation.operation_id, action }),
    });
    await loadState();
    showStatus(
      action === "resume"
        ? `${operation.label} resumed successfully.`
        : `${operation.label} incomplete work cleaned.\nRemoved ${(result.removed || []).length} staging path(s); restored ${(result.restored || []).length} backup(s).`,
    );
  } catch (error) {
    showStatus(`${verb} operation failed: ${error.message}`, true);
  } finally {
    finishOperationMonitor(operation.operation_id);
  }
}

async function pollOperations() {
  if (!operatorMode || operationPollRunning) return;
  operationPollRunning = true;
  let keepPolling = expectedOperationIds.size > 0;
  try {
    const value = await request("/api/operator/operations");
    const operations = value.operations || [];
    const running = operations.filter(item => item.status === "running");
    const tracked = running[0]
      || operations.find(item => expectedOperationIds.has(item.operation_id))
      || operations.find(item => ["failed", "interrupted"].includes(item.status))
      || operations.find(item => !observedOperationIds.has(item.operation_id));
    if (tracked) {
      renderOperationStatus(tracked);
      const previous = operationCompletedCounts.get(tracked.operation_id) || 0;
      operationCompletedCounts.set(tracked.operation_id, tracked.completed_count || 0);
      if ((tracked.completed_count || 0) > previous && tracked.kind === "product-generation") {
        await loadState();
      }
    }
    keepPolling = keepPolling || running.length > 0;
  } catch (error) {
    if (expectedOperationIds.size) {
      showStatus(`Operation progress unavailable: ${error.message}`, true);
      keepPolling = true;
    }
  } finally {
    operationPollRunning = false;
    clearTimeout(operationPollTimer);
    operationPollTimer = keepPolling
      ? setTimeout(pollOperations, 1000)
      : null;
  }
}

function monitorOperation(operationId) {
  expectedOperationIds.add(operationId);
  clearTimeout(operationPollTimer);
  operationPollTimer = setTimeout(pollOperations, 150);
}

function finishOperationMonitor(operationId) {
  expectedOperationIds.delete(operationId);
  // Force one terminal refresh so the durable count summary and recent output
  // replace the temporary one-line request message.
  observedOperationIds.delete(operationId);
  if (!operationPollTimer && !operationPollRunning) {
    operationPollTimer = setTimeout(pollOperations, 0);
  }
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const value = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) {
    const error = new Error(value.error || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return value;
}

async function login(accessCode) {
  return request("/api/login", {
    method: "POST",
    body: JSON.stringify({ role: operatorMode ? "operator" : "reviewer", access_code: accessCode }),
  });
}

function applyPreviewBackground(image) {
  image.classList.add("design-image");
  image.classList.toggle("checker-background", previewBackground === "checker");
  image.style.backgroundColor = previewBackground === "checker" ? "" : previewBackground;
}

function updatePreviewBackground(value) {
  previewBackground = value;
  document.querySelectorAll(".design-image").forEach(applyPreviewBackground);
}

function designImage(design) {
  const image = node("img");
  image.src = design.source_url;
  image.alt = design.design_id;
  applyPreviewBackground(image);
  return image;
}

function artifactImage(url, alt) {
  const image = node("img");
  image.src = url;
  image.alt = alt;
  applyPreviewBackground(image);
  return image;
}

function isReleased(design) {
  return design.operator_decision?.decision === "approved"
    && Boolean(design.publication?.current_entries?.length);
}

function summaryBadges(summary) {
  const badges = node("div", "badges");
  for (const verdict of ["approve", "changes", "reject"]) {
    badges.append(node("span", "badge", `${verdict}: ${summary[verdict] || 0}`));
  }
  return badges;
}

function transparencyBadge(design) {
  const report = design.transparency;
  if (design.canvas_policy === "opaque-full-bleed") {
    const pass = report && !report.has_transparent_pixels;
    return node(
      "span",
      `badge ${pass ? "pass" : "warn"}`,
      pass
        ? "Opaque full-bleed source · transparent pixels 0%"
        : `Full-bleed source violation · ${report?.transparent_pixel_percent ?? "unknown"}% non-opaque pixels`,
    );
  }
  const pass = report && report.has_transparent_background;
  return node(
    "span",
    `badge ${pass ? "pass" : "warn"}`,
    pass
      ? `Transparent background detected · ${report.transparent_border_percent}% transparent border`
      : report?.has_transparent_pixels
        ? `Partial alpha only · ${report.transparent_pixel_percent}% non-opaque pixels`
        : "No transparent background detected",
  );
}

function artifactTransparencyBadge(report, canvasPolicy) {
  if (!report) return node("span", "badge warn", "Alpha evidence unavailable");
  if (canvasPolicy === "opaque-full-bleed") {
    const valid = !report.has_transparent_pixels;
    return node(
      "span",
      `badge ${valid ? "pass" : "warn"}`,
      valid
        ? "Opaque full bleed · transparent pixels 0% · border transparency 0%"
        : `Full-bleed violation · ${report.transparent_pixel_percent}% non-opaque pixels · ${report.transparent_border_percent}% transparent border`,
    );
  }
  return node(
    "span",
    "badge pass",
    `Transparency preserved · ${report.transparent_pixel_percent}% non-opaque pixels · ${report.transparent_border_percent}% transparent border`,
  );
}

function intakeBadge(design) {
  if (!design.intake) return null;
  return node("span", "badge", `collection: ${design.intake.collection_id}`);
}

function designCollection(design) {
  return design.intake?.collection_id || "@manual";
}

function matchesReviewFilter(design, value) {
  const summary = design.feedback_summary || {};
  const feedbackCount = ["approve", "changes", "reject"]
    .reduce((total, verdict) => total + (summary[verdict] || 0), 0);
  if (!value) return true;
  if (value === "no-feedback") return feedbackCount === 0;
  if (value === "has-feedback") return feedbackCount > 0;
  if (["approve", "changes", "reject"].includes(value)) return (summary[value] || 0) > 0;
  if (value === "subject-approved") return design.subject_id && design.operator_decision?.decision === "approved";
  if (value === "subject-rework") return design.subject_id && design.operator_decision?.decision === "rework";
  if (value === "subject-abandoned") return design.subject_id && design.operator_decision?.decision === "abandoned";
  if (value === "subject-pending") return design.subject_id && !design.operator_decision;
  const released = isReleased(design);
  if (value === "released") return released;
  if (value === "unreleased") return !released;
  return true;
}

function filteredDesigns(items) {
  const collection = $("collection-filter").value;
  const query = $("design-filter").value.trim().toLocaleLowerCase();
  const transparency = $("transparency-filter").value;
  const review = $("review-filter").value;
  const selection = operatorMode && activeOperatorTab !== "released"
    ? $("selection-filter").value
    : "";
  return items.filter(design => {
    if (collection && designCollection(design) !== collection) return false;
    const searchable = `${design.design_id} ${design.seed_design_id} ${design.subject_id || ""} ${design.intake?.collection_id || "manual"}`.toLocaleLowerCase();
    if (query && !searchable.includes(query)) return false;
    if (transparency && design.transparency?.result !== transparency) return false;
    const selected = isDesignSelected(design);
    if (selection === "selected" && !selected) return false;
    if (selection === "unselected" && selected) return false;
    return matchesReviewFilter(design, review);
  });
}

function isDesignSelected(design) {
  if (!operatorMode) return false;
  if (!design.subject_id && ["active", "abandoned"].includes(design.pool)) {
    return selectedLifecycleSeeds.has(design.design_id);
  }
  if (!design.subject_id && design.pool === "graduated") {
    return selectedGenerationSeedIds.has(design.seed_design_id);
  }
  return Boolean(design.subject_id && selectedSubjectIds.has(design.design_id));
}

function populateCollectionFilter() {
  const select = $("collection-filter");
  const current = select.value;
  const values = [...new Set(currentState.designs.map(designCollection))]
    .sort((left, right) => left.localeCompare(right));
  select.replaceChildren(
    new Option("All collections", ""),
    ...values.map(value => new Option(value === "@manual" ? "Manual intake" : value, value)),
  );
  if (values.includes(current)) select.value = current;
}

function updateFilterCount(visible, total) {
  const plannedProducts = [...productPlans.values()].filter(plan => plan.included).length;
  const selections = operatorMode
    ? ` · ${selectedLifecycleSeeds.size} seed(s) selected · ${selectedGenerationSeedIds.size} reference(s) selected · ${selectedSubjectIds.size} subject(s) selected · ${selectedPrintDesignIds.size} product row(s) selected · ${plannedProducts} product plan(s) · ${selectedBundleCandidates.size} release candidate(s) selected`
    : "";
  $("filter-count").textContent = `Showing ${visible} of ${total} designs${selections}`;
}

function reconcileSelections() {
  const selectableDesigns = new Set(
    currentState.designs
      .filter(design => design.subject_id && design.operator_decision?.decision === "approved")
      .map(design => design.design_id),
  );
  for (const designId of selectedPrintDesignIds) {
    if (!selectableDesigns.has(designId)) selectedPrintDesignIds.delete(designId);
  }
  const subjectDesigns = new Set(
    currentState.designs
      .filter(design => design.pool === "graduated" && design.subject_id)
      .map(design => design.design_id),
  );
  for (const designId of selectedSubjectIds) {
    if (!subjectDesigns.has(designId)) selectedSubjectIds.delete(designId);
  }
  const lifecycleSeeds = new Set(
    currentState.designs
      .filter(design => !design.subject_id && ["active", "abandoned"].includes(design.pool))
      .map(design => design.design_id),
  );
  for (const designId of selectedLifecycleSeeds) {
    if (!lifecycleSeeds.has(designId)) selectedLifecycleSeeds.delete(designId);
  }
  const candidates = new Map(
    currentState.designs.flatMap(design => design.bundle_candidates || [])
      .map(candidate => [candidate.selection, candidate]),
  );
  for (const selection of selectedBundleCandidates.keys()) {
    if (candidates.has(selection)) selectedBundleCandidates.set(selection, candidates.get(selection));
    else selectedBundleCandidates.delete(selection);
  }
  const profiles = new Set(currentState.profiles.map(profile => profile.product_profile_id));
  for (const profileId of batchProfileIds) {
    if (!profiles.has(profileId)) batchProfileIds.delete(profileId);
  }
  for (const [key, plan] of productPlans) {
    if (!selectableDesigns.has(plan.design_id) || !profiles.has(plan.product_profile_id)) {
      productPlans.delete(key);
    }
  }
  const seedIds = new Set(
    currentState.designs
      .filter(design => design.pool === "graduated" && !design.subject_id)
      .map(design => design.seed_design_id),
  );
  for (const seedId of selectedGenerationSeedIds) {
    if (!seedIds.has(seedId)) selectedGenerationSeedIds.delete(seedId);
  }
  const subjectIds = new Set(currentState.subjects.map(subject => subject.subject_id));
  for (const subjectId of batchSubjectIds) {
    if (!subjectIds.has(subjectId)) batchSubjectIds.delete(subjectId);
  }
}

function reviewerCard(design) {
  const card = node("article", "card");
  card.append(node("h2", "", design.design_id), designImage(design));
  const badges = summaryBadges(design.feedback_summary);
  badges.append(transparencyBadge(design));
  const intake = intakeBadge(design);
  if (intake) badges.append(intake);
  card.append(badges);
  const comment = node("textarea");
  comment.placeholder = "What should change, or why should this design advance?";
  const pending = pendingReviewerFeedback.get(design.design_id);
  let selectedVerdict = pending?.verdict || null;
  if (pending) {
    comment.value = pending.comment;
  }
  const verdictButtons = node("div", "review-decision-group");
  verdictButtons.setAttribute("role", "group");
  verdictButtons.setAttribute("aria-label", `Decision for ${design.design_id}`);
  const choices = [
    ["approve", "Approve"],
    ["changes", "Needs changes"],
    ["reject", "Reject"],
  ];
  const buttons = new Map();
  const updateButtons = () => {
    for (const [value, button] of buttons) {
      button.setAttribute("aria-pressed", value === selectedVerdict ? "true" : "false");
    }
  };
  const queue = () => {
    if (!selectedVerdict) return;
    pendingReviewerFeedback.set(design.design_id, {
      design_id: design.design_id,
      source_sha256: design.source_sha256,
      verdict: selectedVerdict,
      comment: comment.value,
    });
    $("pending-review-count").textContent = `${pendingReviewerFeedback.size} changed`;
  };
  for (const [value, label] of choices) {
    const button = node("button", `review-decision review-decision-${value}`, label);
    button.type = "button";
    button.addEventListener("click", () => {
      selectedVerdict = value;
      updateButtons();
      queue();
    });
    buttons.set(value, button);
    verdictButtons.append(button);
  }
  updateButtons();
  comment.addEventListener("input", queue);
  const verdictLabel = node("div", "review-decision-field");
  verdictLabel.append(node("strong", "", "Decision"), verdictButtons);
  const commentLabel = node("label", "", "Feedback");
  commentLabel.append(comment);
  card.append(
    verdictLabel,
    commentLabel,
    node(
      "p",
      "hint",
      pending
        ? "Pending change"
        : "Choose one decision to add this design to the next batch save.",
    ),
  );
  return card;
}

async function saveReviewerFeedback() {
  const reviewer = $("reviewer").value.trim();
  if (!reviewer) return showStatus("Enter your reviewer name or email first.", true);
  const feedback = [...pendingReviewerFeedback.values()];
  if (!feedback.length) return showStatus("No changed feedback to save.", true);
  const button = $("save-review-feedback");
  button.disabled = true;
  showStatus(`Saving ${feedback.length} review response(s)…`);
  try {
    const result = await request("/api/feedback-batch", {
      method: "POST",
      body: JSON.stringify({ reviewer, feedback }),
    });
    pendingReviewerFeedback.clear();
    $("pending-review-count").textContent = "0 changed";
    showStatus(`Saved ${result.count} review response(s).`);
    await loadState();
  } catch (error) { showStatus(`Feedback save failed: ${error.message}`, true); }
  finally { button.disabled = false; }
}

function feedbackBlock(design) {
  const feedback = node("div", "feedback");
  feedback.append(node("h3", "", "Reviewer feedback"));
  if (!design.feedback.length) feedback.append(node("p", "hint", "No feedback yet."));
  for (const item of design.feedback) {
    const entry = node("div", "feedback-entry");
    entry.append(node("strong", "", `${item.verdict} · ${item.reviewer}`));
    entry.append(node("p", "", item.comment || "No comment"));
    feedback.append(entry);
  }
  return feedback;
}

function artifactBlocks(design, card) {
  for (const version of design.versions) {
    const block = node("details", "feedback");
    block.append(node("summary", "", `Generated ${version.version_id}`));
    const artifacts = node("div", "artifacts");
    if (version.preview_url) {
      const figure = node("figure");
      const preview = node("img"); preview.src = version.preview_url;
      figure.append(preview, node("figcaption", "", version.preview));
      artifacts.append(figure);
    }
    for (const asset of version.assets) {
      const figure = node("figure");
      const image = node("img"); image.src = asset.url;
      figure.append(image, node("figcaption", "", asset.name));
      artifacts.append(figure);
    }
    block.append(artifacts);
    card.append(block);
  }
}

async function transitionSeed(design, action, button) {
  if (!window.confirm(`${action} ${design.seed_design_id}?`)) return;
  button.disabled = true;
  showStatus(`${action} in progress · ${design.seed_design_id}`);
  try {
    await request("/api/operator/transition", {
      method: "POST",
      body: JSON.stringify({ seed_design_id: design.seed_design_id, action }),
    });
    selectedLifecycleSeeds.delete(design.seed_design_id);
    showStatus(`${design.seed_design_id} moved successfully.`);
    await loadState();
  } catch (error) {
    await loadState().catch(() => {});
    showStatus(`Seed transition failed: ${error.message}`, true);
  }
  finally { button.disabled = false; }
}

function seedActions(design) {
  const actions = node("div", "button-row");
  if (design.pool === "active") {
    const graduate = node("button", "", "Approve and graduate seed");
    const abandon = node("button", "danger", "Abandon seed");
    graduate.addEventListener("click", () => transitionSeed(design, "graduate", graduate));
    abandon.addEventListener("click", () => transitionSeed(design, "abandon", abandon));
    actions.append(graduate, abandon);
  } else if (design.pool === "abandoned") {
    const restore = node("button", "secondary", "Restore to active review");
    restore.addEventListener("click", () => transitionSeed(design, "restore", restore));
    actions.append(restore);
  }
  return actions;
}

function subjectGenerationControls(seed) {
  const panel = node("div", "feedback");
  panel.append(node("h3", "", "Generate subject previews"));
  panel.append(node("p", "hint", "Paid image-edit calls. The approved seed is Image A; select all catalog subjects or only the coverage needed now."));
  const checks = node("div", "checks subject-checks");
  const existing = new Set(
    currentState.designs
      .filter(item => item.pool === "graduated" && item.seed_design_id === seed.seed_design_id && item.subject_id)
      .map(item => item.subject_id),
  );
  for (const subject of currentState.subjects) {
    const label = node("label", "", `${subject.display_name}${existing.has(subject.subject_id) ? " · generated" : ""}`);
    const input = node("input");
    input.type = "checkbox";
    input.value = subject.subject_id;
    if (!existing.has(subject.subject_id)) input.checked = true;
    label.prepend(input);
    checks.append(label);
  }
  const forceLabel = node("label", "inline-check", "Regenerate and replace existing selected previews");
  const force = node("input"); force.type = "checkbox";
  forceLabel.prepend(force);
  const instructionLabel = node("label", "", "Optional generation instruction");
  const instruction = node("textarea");
  instruction.placeholder = "Example: preserve the hat size; use a shorter muzzle.";
  instructionLabel.append(instruction);
  const button = node("button", "", "Generate selected subjects");
  button.addEventListener("click", async () => {
    const subjectIds = [...checks.querySelectorAll("input:checked")].map(item => item.value);
    if (!subjectIds.length) return showStatus("Select at least one subject.", true);
    const operationId = newOperationId("subject");
    button.disabled = true;
    showStatus(`Generating ${subjectIds.length} subject preview(s) for ${seed.seed_design_id}… This paid image operation may take several minutes.`);
    monitorOperation(operationId);
    try {
      await request("/api/operator/generate-subjects-batch", {
        method: "POST",
        body: JSON.stringify({
          operation_id: operationId,
          seed_design_ids: [seed.seed_design_id],
          subject_ids: subjectIds,
          force: force.checked,
          instruction: instruction.value,
        }),
      });
      showStatus(`Generated ${subjectIds.length} subject preview(s) for ${seed.seed_design_id}. Review and approve them before product generation.`);
      await loadState();
    } catch (error) { showStatus(error.message, true); }
    finally { finishOperationMonitor(operationId); button.disabled = false; }
  });
  panel.append(checks, instructionLabel, forceLabel, button);
  return panel;
}

function subjectDecisionControls(design) {
  const wrapper = node("div", "feedback");
  const current = design.operator_decision;
  wrapper.append(node("h3", "", `Subject decision · ${current ? current.decision : "pending"}`));
  if (current && current.note) wrapper.append(node("p", "hint", current.note));
  const note = node("textarea"); note.placeholder = "Optional operator decision note";
  const actions = node("div", "button-row");
  for (const [decision, label, className] of [
    ["approved", "Approve subject", ""],
    ["rework", "Needs rework", "danger"],
    ["abandoned", "Abandon subject", "danger"],
  ]) {
    const button = node("button", className, label);
    button.addEventListener("click", async () => {
      button.disabled = true;
      showStatus(`Recording ${decision} decision · ${design.design_id}`);
      try {
        await request("/api/operator/subject-decision", {
          method: "POST",
          body: JSON.stringify({ design_id: design.design_id, decision, note: note.value }),
        });
        selectedSubjectIds.delete(design.design_id);
        showStatus(`Recorded ${decision} · ${design.design_id}`);
        await loadState();
      } catch (error) { showStatus(error.message, true); }
      finally { button.disabled = false; }
    });
    actions.append(button);
  }
  wrapper.append(note, actions);
  return wrapper;
}

async function regenerateSubject(design, button, instruction) {
  if (!window.confirm(
    `Regenerate ${design.subject_id} for ${design.seed_design_id}? This is a paid image call and invalidates its current approval.`,
  )) return;
  button.disabled = true;
  const operationId = newOperationId("subject");
  showStatus(`Regenerating subject preview · ${design.design_id}… This may take several minutes.`);
  monitorOperation(operationId);
  try {
    await request("/api/operator/generate-subjects-batch", {
      method: "POST",
      body: JSON.stringify({
        operation_id: operationId,
        seed_design_ids: [design.seed_design_id],
        subject_ids: [design.subject_id],
        force: true,
        instruction,
      }),
    });
    selectedSubjectIds.delete(design.design_id);
    selectedPrintDesignIds.delete(design.design_id);
    showStatus(`Regenerated ${design.design_id}. Review and approve the new subject pixels.`);
    await loadState();
  } catch (error) {
    await loadState().catch(() => {});
    showStatus(`Subject regeneration failed: ${error.message}`, true);
  } finally {
    finishOperationMonitor(operationId);
    button.disabled = false;
  }
}

function subjectSelectionControl(design) {
  const label = node("label", "lifecycle-selection", "Select for batch approval");
  const input = node("input");
  input.type = "checkbox";
  input.checked = selectedSubjectIds.has(design.design_id);
  input.addEventListener("change", () => {
    if (input.checked) selectedSubjectIds.add(design.design_id);
    else selectedSubjectIds.delete(design.design_id);
    render();
  });
  label.prepend(input);
  return label;
}

function printSelectionControl(design) {
  const label = node("label", "select-design", "Include subject row in batch generation");
  const input = node("input");
  input.type = "checkbox";
  input.checked = selectedPrintDesignIds.has(design.design_id);
  input.addEventListener("change", () => {
    if (input.checked) selectedPrintDesignIds.add(design.design_id);
    else selectedPrintDesignIds.delete(design.design_id);
    updateBatchToolbar();
  });
  label.prepend(input);
  return label;
}

function productPlanKey(designId, profileId) {
  return `${designId}::${profileId}`;
}

function versionNumber(versionId) {
  const match = /^v([0-9]{6})$/.exec(versionId || "");
  return match ? Number(match[1]) : 0;
}

function recommendedProductVersion(design, profileId) {
  const generated = design.versions
    .filter(version => version.product_profile_id === profileId)
    .map(version => version.version_id);
  const published = (design.publication?.entries || [])
    .filter(entry => entry.product_profile_id === profileId)
    .map(entry => entry.version_id);
  const highest = Math.max(0, ...generated.map(versionNumber), ...published.map(versionNumber));
  return `v${String(highest + 1).padStart(6, "0")}`;
}

function ensureProductPlan(design, profileId) {
  const key = productPlanKey(design.design_id, profileId);
  if (!productPlans.has(key)) {
    productPlans.set(key, {
      design_id: design.design_id,
      product_profile_id: profileId,
      included: false,
    });
  }
  return productPlans.get(key);
}

function productProfileSnapshot(design, profileId) {
  const outputs = design.versions.flatMap(version =>
    (version.profile_outputs || [])
      .filter(output => output.product_profile_id === profileId)
      .map(output => ({ version, output })),
  ).sort(
    (left, right) => versionNumber(right.version.version_id) - versionNumber(left.version.version_id),
  );
  const published = (design.publication?.current_entries || [])
    .filter(entry => entry.product_profile_id === profileId)
    .sort((left, right) => versionNumber(right.version_id) - versionNumber(left.version_id));
  const unpublished = (design.bundle_candidates || [])
    .filter(candidate =>
      candidate.product_profile_id === profileId
        && !published.some(entry => entry.version_id === candidate.version_id),
    )
    .sort((left, right) => versionNumber(right.version_id) - versionNumber(left.version_id));
  const recorded = (design.product_coverage || []).find(
    item => item.product_profile_id === profileId,
  );
  let state = recorded?.state || "missing";
  let label = "Not generated";
  if (state === "update-ready") {
    label = `${recorded.latest_candidate_version} ready · ${recorded.latest_published_version} published`;
  } else if (state === "ready") {
    label = `${recorded.latest_candidate_version} ready to publish`;
  } else if (state === "published") {
    label = `${recorded.latest_published_version} published · ${recorded.latest_release_id}`;
  } else if (state === "retired") {
    label = `${recorded.latest_published_version} retired · ${recorded.latest_release_id}`;
  } else if (state === "local-attention") {
    label = `${recorded.latest_generated_version} requires attention`;
  } else if (state === "release-status-unavailable") {
    label = "Release status unavailable from S3";
  }
  return {
    state,
    label,
    outputs,
    latestPublished: published[0] || null,
    latestCandidate: unpublished[0] || null,
    lifecycleStatus: recorded?.lifecycle_status || published[0]?.lifecycle_status || null,
  };
}

function productCoverage(design) {
  const snapshots = currentState.profiles.map(profile => ({
    profile,
    snapshot: productProfileSnapshot(design, profile.product_profile_id),
  }));
  return {
    snapshots,
    published: snapshots.filter(item => ["published", "update-ready", "retired"].includes(item.snapshot.state)).length,
    active: snapshots.filter(item => ["published", "update-ready"].includes(item.snapshot.state) && item.snapshot.lifecycleStatus !== "retired").length,
    retired: snapshots.filter(item => item.snapshot.lifecycleStatus === "retired").length,
    ready: snapshots.filter(item => ["ready", "update-ready"].includes(item.snapshot.state)).length,
    attention: snapshots.filter(item => item.snapshot.state === "local-attention").length,
    unknown: snapshots.filter(item => item.snapshot.state === "release-status-unavailable").length,
    missing: snapshots.filter(item => item.snapshot.state === "missing").length,
  };
}

function selectedPlansForDesign(designId) {
  return [...productPlans.values()].filter(
    plan => plan.design_id === designId && plan.included,
  );
}

async function runProductPlans(plans, button, label) {
  if (!plans.length) return showStatus("Select at least one subject/product profile plan.", true);
  const operationId = newOperationId("product");
  button.disabled = true;
  showStatus(`Generating ${plans.length} ${label}…`);
  monitorOperation(operationId);
  try {
    const result = await request("/api/operator/generate", {
      method: "POST",
      body: JSON.stringify({
        operation_id: operationId,
        plans: plans.map(plan => ({
          design_id: plan.design_id,
          product_profile_id: plan.product_profile_id,
          version_id: "auto",
        })),
      }),
    });
    const warnings = result.results.flatMap(item => item.warnings || []);
    const generated = result.results.map(
      item => `${item.design_id} / ${item.product_profile_id} / ${item.version_id}`,
    );
    for (const plan of plans) {
      productPlans.delete(productPlanKey(plan.design_id, plan.product_profile_id));
      selectedPrintDesignIds.delete(plan.design_id);
    }
    showStatus(`Product generation complete:\n- ${generated.join("\n- ")}${warnings.length ? `\nWarnings:\n- ${warnings.join("\n- ")}` : ""}`);
    await loadState();
  } catch (error) {
    await loadState().catch(() => {});
    showStatus(`Product generation failed: ${error.message}`, true);
  }
  finally {
    finishOperationMonitor(operationId);
    button.disabled = false;
  }
}

async function updateProductLifecycle(products, status, button, label) {
  if (!products.length) return showStatus(`Select at least one ${status === "retired" ? "published" : "retired"} product.`, true);
  const reason = window.prompt(
    `${status === "retired" ? "Retire" : "Reactivate"} ${products.length} product(s)${label ? ` for ${label}` : ""}. Optional reason:`,
    "",
  );
  if (reason === null) return;
  if (!window.confirm(
    `${status === "retired" ? "Retire" : "Reactivate"} ${products.map(item => `${item.design_id}/${item.product_profile_id}`).join(", ")} in the FE discovery catalog? Immutable bundle versions will not be deleted.`,
  )) return;
  const operationId = newOperationId("lifecycle");
  button.disabled = true;
  showStatus(`${status === "retired" ? "Retiring" : "Reactivating"} ${products.length} product(s)…`);
  monitorOperation(operationId);
  try {
    const result = await request("/api/operator/product-lifecycle", {
      method: "POST",
      body: JSON.stringify({ operation_id: operationId, products, status, reason }),
    });
    for (const product of products) {
      const design = currentState.designs.find(item => item.design_id === product.design_id);
      if (design) ensureProductPlan(design, product.product_profile_id).included = false;
    }
    showStatus(`${status === "retired" ? "Retired" : "Reactivated"} ${products.length} product(s).\nCatalog: ${result.catalog_uri}`);
    await loadState();
  } catch (error) {
    await loadState().catch(() => {});
    showStatus(`Product lifecycle update failed: ${error.message}`, true);
  } finally { finishOperationMonitor(operationId); button.disabled = false; }
}

async function changeProductLifecycle(design, profileIds, status, button) {
  return updateProductLifecycle(
    profileIds.map(productProfileId => ({
      design_id: design.design_id,
      product_profile_id: productProfileId,
    })),
    status,
    button,
    design.design_id,
  );
}

async function changeSelectedProductLifecycle(status) {
  const button = status === "retired"
    ? $("retire-selected-products")
    : $("reactivate-selected-products");
  const products = [...productPlans.values()]
    .filter(plan => plan.included)
    .filter(plan => {
      const design = currentState.designs.find(item => item.design_id === plan.design_id);
      if (!design) return false;
      const snapshot = productProfileSnapshot(design, plan.product_profile_id);
      return status === "retired"
        ? Boolean(snapshot.latestPublished && snapshot.lifecycleStatus !== "retired")
        : snapshot.lifecycleStatus === "retired";
    })
    .map(plan => ({
      design_id: plan.design_id,
      product_profile_id: plan.product_profile_id,
    }));
  return updateProductLifecycle(products, status, button, "the current batch selection");
}

function productPlanControls(design) {
  const details = node("details", "product-plan-controls");
  const expansionKey = design.design_id;
  details.open = expandedProductCoverage.has(expansionKey);
  details.addEventListener("toggle", () => {
    if (details.open) expandedProductCoverage.add(expansionKey);
    else expandedProductCoverage.delete(expansionKey);
  });
  const coverage = productCoverage(design);
  details.append(node(
    "summary",
    "",
      `Product coverage · ${coverage.active}/${coverage.snapshots.length} active · ${coverage.retired} retired · ${coverage.ready} ready · ${coverage.missing} missing${coverage.unknown ? ` · ${coverage.unknown} S3 status unknown` : ""}`,
  ));
  const rows = node("div", "product-plan-rows");
  for (const { profile, snapshot } of coverage.snapshots) {
    const plan = ensureProductPlan(design, profile.product_profile_id);
    const row = node("div", `product-plan-row product-state-${snapshot.state}`);
    const includeLabel = node("label", "product-plan-profile");
    const include = node("input"); include.type = "checkbox"; include.checked = plan.included;
    include.addEventListener("change", () => {
      plan.included = include.checked;
      updateBatchToolbar();
    });
    includeLabel.append(
      include,
      node("span", "", profile.product_profile_id),
    );
    const dimensions = node(
      "span",
      "product-dimensions",
      `preview ${profile.preview.width}×${profile.preview.height} · print ${profile.targets.map(target => `${target.width}×${target.height}`).join(", ")}`,
    );
    const status = node(
      "span",
      `product-state badge ${snapshot.state === "published" ? "pass" : ["ready", "update-ready"].includes(snapshot.state) ? "ready" : ["local-attention", "release-status-unavailable", "retired"].includes(snapshot.state) ? "warn" : ""}`,
      snapshot.label,
    );
    const actions = node("div", "product-row-actions");
    const generateOne = node(
      "button",
      "secondary compact",
      snapshot.state === "missing" ? "Generate" : "Generate new version",
    );
    generateOne.type = "button";
    generateOne.addEventListener("click", () => runProductPlans(
      [{ design_id: design.design_id, product_profile_id: profile.product_profile_id }],
      generateOne,
      `product for ${design.design_id}`,
    ));
    actions.append(generateOne);
    if (snapshot.latestCandidate && snapshot.state !== "release-status-unavailable") {
      actions.append(releaseCandidateControl(snapshot.latestCandidate));
      const publishOne = node("button", "compact", "Publish");
      publishOne.type = "button";
      publishOne.disabled = !currentState.publication?.enabled;
      if (publishOne.disabled) publishOne.title = "Configure S3 and restart the server before publishing.";
      publishOne.addEventListener("click", () => publishCandidate(snapshot.latestCandidate, publishOne));
      actions.append(publishOne);
    }
    if (snapshot.latestPublished && snapshot.state !== "release-status-unavailable") {
      const nextStatus = snapshot.lifecycleStatus === "retired" ? "active" : "retired";
      const lifecycle = node(
        "button",
        `${nextStatus === "retired" ? "danger" : "secondary"} compact`,
        nextStatus === "retired" ? "Retire" : "Reactivate",
      );
      lifecycle.type = "button";
      lifecycle.addEventListener("click", () => changeProductLifecycle(
        design,
        [profile.product_profile_id],
        nextStatus,
        lifecycle,
      ));
      actions.append(lifecycle);
    }
    row.append(includeLabel, dimensions, status, actions);
    rows.append(row);
  }
  const quickActions = node("div", "button-row product-plan-actions");
  const selectMissing = node("button", "secondary compact", "Select missing products");
  selectMissing.type = "button";
  selectMissing.addEventListener("click", () => {
    for (const { profile, snapshot } of coverage.snapshots) {
      if (snapshot.state === "missing") ensureProductPlan(design, profile.product_profile_id).included = true;
    }
    render();
  });
  const selectAll = node("button", "secondary compact", "Select all products");
  selectAll.type = "button";
  selectAll.addEventListener("click", () => {
    for (const { profile } of coverage.snapshots) {
      ensureProductPlan(design, profile.product_profile_id).included = true;
    }
    render();
  });
  const clear = node("button", "secondary compact", "Clear");
  clear.type = "button";
  clear.addEventListener("click", () => {
    for (const { profile } of coverage.snapshots) {
      ensureProductPlan(design, profile.product_profile_id).included = false;
    }
    render();
  });
  const generate = node("button", "secondary", "Generate selected products");
  generate.type = "button";
  generate.addEventListener("click", () =>
    runProductPlans(selectedPlansForDesign(design.design_id), generate, `plan(s) for ${design.design_id}`),
  );
  const retire = node("button", "danger compact", "Retire selected published");
  retire.type = "button";
  retire.addEventListener("click", () => changeProductLifecycle(
    design,
    selectedPlansForDesign(design.design_id)
      .map(plan => plan.product_profile_id)
      .filter(profileId => {
        const snapshot = productProfileSnapshot(design, profileId);
        return snapshot.latestPublished && snapshot.lifecycleStatus !== "retired";
      }),
    "retired",
    retire,
  ));
  const reactivate = node("button", "secondary compact", "Reactivate selected retired");
  reactivate.type = "button";
  reactivate.addEventListener("click", () => changeProductLifecycle(
    design,
    selectedPlansForDesign(design.design_id)
      .map(plan => plan.product_profile_id)
      .filter(profileId => productProfileSnapshot(design, profileId).lifecycleStatus === "retired"),
    "active",
    reactivate,
  ));
  quickActions.append(selectMissing, selectAll, clear, generate, retire, reactivate);
  details.append(rows, quickActions);
  return details;
}

function releaseCandidateControl(candidate) {
  const label = node("label", "select-design", `Select ${candidate.version_id}`);
  const input = node("input");
  input.type = "checkbox";
  input.checked = selectedBundleCandidates.has(candidate.selection);
  input.addEventListener("change", () => {
    if (input.checked) selectedBundleCandidates.set(candidate.selection, candidate);
    else selectedBundleCandidates.delete(candidate.selection);
    updateBatchToolbar();
  });
  label.prepend(input);
  return label;
}

function referenceCheckpoint(seed, includeGeneration) {
  const cell = node("div", "checkpoint reference-checkpoint");
  cell.append(designImage(seed), node("strong", "checkpoint-id", seed.seed_design_id));
  const badges = node("div", "badges");
  badges.append(transparencyBadge(seed));
  const intake = intakeBadge(seed);
  if (intake) badges.append(intake);
  cell.append(badges);
  if (includeGeneration) {
    const selection = node("label", "lifecycle-selection", "Select reference for batch subject generation");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedGenerationSeedIds.has(seed.seed_design_id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedGenerationSeedIds.add(seed.seed_design_id);
      else selectedGenerationSeedIds.delete(seed.seed_design_id);
      if ($("selection-filter").value) render();
      else updateBatchToolbar();
    });
    selection.prepend(checkbox);
    cell.append(selection);
    const details = node("details", "checkpoint-actions");
    details.append(node("summary", "", "Add or regenerate subject coverage"));
    details.append(subjectGenerationControls(seed));
    cell.append(details);
  }
  return cell;
}

function subjectCheckpoint(design, { released = false } = {}) {
  const cell = node("div", "checkpoint subject-checkpoint");
  if (!design) {
    cell.append(
      node("span", "stage-state pending", "Step 2 · waiting"),
      node("p", "hint", "Generate at least one subject preview from the reference-design checkpoint."),
    );
    return cell;
  }
  cell.append(
    node("span", `stage-state ${design.operator_decision?.decision === "approved" ? "complete" : "pending"}`,
      `Step 2 · ${design.operator_decision?.decision || "review required"}`),
    designImage(design),
    node("strong", "checkpoint-id", design.subject_id),
    node("p", "meta checkpoint-id", design.design_id),
  );
  if (!released) {
    cell.append(subjectSelectionControl(design), subjectDecisionControls(design));
  } else {
    const revise = node("details", "checkpoint-actions");
    revise.append(
      node("summary", "", "Reopen subject for improvement"),
      subjectDecisionControls(design),
    );
    cell.append(revise);
  }
  const instruction = node("textarea");
  instruction.placeholder = "Optional improvement instruction for this regeneration";
  const regenerate = node("button", "secondary", "Regenerate with instruction");
  regenerate.addEventListener("click", () => regenerateSubject(design, regenerate, instruction.value));
  cell.append(instruction, regenerate);
  return cell;
}

function productEvidence(version, output, design) {
  const block = node("details", "product-evidence");
  const alreadyPublished = (design.publication?.current_entries || []).some(item =>
    item.version_id === version.version_id
      && item.product_profile_id === output.product_profile_id
  );
  block.open = !alreadyPublished;
  block.append(node(
    "summary",
    "",
    `${output.product_profile_id} · ${version.version_id}`,
  ));
  const preview = node("figure", "product-figure");
  preview.append(
    artifactImage(output.preview.url, `${output.product_profile_id} preview`),
    node("figcaption", "", `Preview · ${output.preview.width}×${output.preview.height}`),
    artifactTransparencyBadge(output.preview.transparency, output.canvas_policy),
  );
  const gallery = node("div", "product-artifacts");
  gallery.append(preview);
  for (const print of output.prints) {
    const figure = node("figure", "product-figure");
    figure.append(
      artifactImage(print.url, `${output.product_profile_id} ${print.target_id} print`),
      node("figcaption", "", `Print · ${print.target_id} · ${print.width}×${print.height}`),
      artifactTransparencyBadge(print.transparency, output.canvas_policy),
    );
    gallery.append(figure);
  }
  block.append(gallery);
  const candidate = design.bundle_candidates.find(item =>
    item.version_id === version.version_id
      && item.product_profile_id === output.product_profile_id
  );
  if (alreadyPublished) {
    block.append(node("span", "badge pass", "Published"));
  } else if (candidate) block.append(releaseCandidateControl(candidate));
  return block;
}

function productCheckpoint(design) {
  const cell = node("div", "checkpoint product-checkpoint");
  if (!design) {
    cell.append(node("span", "stage-state pending", "Step 3 · waiting"));
    return cell;
  }
  if (design.operator_decision?.decision === "approved") {
    cell.append(printSelectionControl(design), productPlanControls(design));
  } else {
    cell.append(node("p", "hint", "Approve the subject preview before generating product outputs."));
  }
  const outputs = design.versions.flatMap(version =>
    (version.profile_outputs || []).map(output => ({ version, output })),
  );
  if (!outputs.length) {
    cell.prepend(node("span", "stage-state pending", "Step 3 · product generation required"));
    cell.append(node("p", "hint", "Select this approved subject and product profiles in the controls above."));
    return cell;
  }
  const coverage = productCoverage(design);
  cell.prepend(node(
    "span",
    `stage-state ${coverage.ready ? "ready" : coverage.published ? "complete" : "pending"}`,
    `Step 3 · ${coverage.active}/${coverage.snapshots.length} active · ${coverage.retired} retired · ${coverage.ready} ready · ${coverage.missing} missing${coverage.unknown ? ` · ${coverage.unknown} S3 status unknown` : ""}`,
  ));
  for (const { version, output } of outputs) {
    cell.append(productEvidence(version, output, design));
  }
  return cell;
}

function workflowStatusCheckpoint(design) {
  const cell = node("div", "checkpoint workflow-status");
  if (!design) {
    cell.append(node("strong", "", "Next: generate subjects"));
    return cell;
  }
  const coverage = productCoverage(design);
  cell.append(node(
    "p",
    "product-coverage-summary",
    `${coverage.active}/${coverage.snapshots.length} products active · ${coverage.retired} retired · ${coverage.ready} ready · ${coverage.missing} not generated${coverage.attention ? ` · ${coverage.attention} need attention` : ""}${coverage.unknown ? ` · ${coverage.unknown} S3 status unknown` : ""}`,
  ));
  if (coverage.ready) {
    cell.prepend(node("span", "stage-state ready", "Next: publish ready products"));
    cell.append(node("p", "hint", "Review the product evidence, then use Publish in the product list or select several candidates for batch publication."));
  } else if (isReleased(design)) {
    cell.append(node("span", "stage-state complete", "Released"));
    for (const entry of design.publication.current_entries || []) {
      cell.append(node("p", "meta", `${entry.product_profile_id} · ${entry.version_id} · ${entry.release_id}`));
    }
    cell.append(node("p", "hint", coverage.missing
      ? "Select missing products in the product list and generate them to extend released coverage."
      : "All configured products are released. Generate a new version only when an improvement is needed."));
  } else if (design.bundle_candidates.length) {
    cell.append(
      node("span", "stage-state ready", "Ready for release"),
      node("p", "hint", "Select a product candidate, validate the release, then publish it."),
    );
  } else if (design.operator_decision?.decision === "approved") {
    cell.append(
      node("span", "stage-state ready", "Subject approved"),
      node("p", "hint", "Select this subject and generate its product candidates."),
    );
  } else if (design.operator_decision?.decision === "abandoned") {
    cell.append(
      node("span", "stage-state pending", "Subject abandoned"),
      node("p", "hint", "No product work will be generated unless this preview is regenerated or approved later."),
    );
  } else {
    cell.append(
      node("span", "stage-state pending", "Subject review required"),
      node("p", "hint", "Approve, mark for rework, or regenerate the subject preview."),
    );
  }
  return cell;
}

function workflowTable(seeds, subjects, { released = false } = {}) {
  const section = node("section", "workflow-section");
  const title = released ? "Released static designs" : "Graduated design workflow";
  const wrapper = node("div", "workflow-table-wrap");
  const table = node("table", "workflow-table");
  const thead = node("thead");
  const header = node("tr");
  for (const label of ["1 · Reference design", "2 · Subject preview", "3 · Product preview and print", "Workflow status"]) {
    header.append(node("th", "", label));
  }
  thead.append(header);
  const tbody = node("tbody");
  const seedById = new Map(seeds.map(seed => [seed.seed_design_id, seed]));
  const grouped = new Map();
  for (const design of subjects) {
    if (!grouped.has(design.seed_design_id)) grouped.set(design.seed_design_id, []);
    grouped.get(design.seed_design_id).push(design);
  }
  if (!released) {
    for (const seed of seeds) {
      if (!grouped.has(seed.seed_design_id)) grouped.set(seed.seed_design_id, []);
    }
  }
  const rowCount = [...grouped.values()].reduce(
    (total, variants) => total + Math.max(variants.length, 1),
    0,
  );
  section.append(node("h2", "section-title", `${title} · ${rowCount}`));
  for (const [seedId, variants] of grouped) {
    const seed = seedById.get(seedId);
    if (!seed) continue;
    const rows = variants.length ? variants : [null];
    rows.forEach((design, index) => {
      const row = node("tr", design && isDesignSelected(design) ? "selected" : "");
      const referenceCell = node("td");
      referenceCell.append(referenceCheckpoint(seed, index === 0));
      const subjectCell = node("td"); subjectCell.append(subjectCheckpoint(design, { released }));
      const productCell = node("td"); productCell.append(productCheckpoint(design));
      const statusCell = node("td"); statusCell.append(workflowStatusCheckpoint(design));
      row.append(referenceCell, subjectCell, productCell, statusCell);
      tbody.append(row);
    });
  }
  if (!tbody.children.length) {
    const row = node("tr");
    const cell = node("td", "empty-table", released ? "No released static designs match the current filters." : "No graduated designs match the current filters.");
    cell.colSpan = 4; row.append(cell); tbody.append(row);
  }
  table.append(thead, tbody); wrapper.append(table); section.append(wrapper);
  return section;
}

function operatorCard(design) {
  const card = node("article", "card");
  card.classList.toggle("selected", isDesignSelected(design));
  const head = node("div", "card-head");
  head.append(node("h2", "", design.design_id));
  if (!design.subject_id && ["active", "abandoned"].includes(design.pool)) {
    const selection = node("label", "lifecycle-selection", "Select");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedLifecycleSeeds.has(design.design_id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedLifecycleSeeds.add(design.design_id);
      else selectedLifecycleSeeds.delete(design.design_id);
      if ($("selection-filter").value) render();
      else {
        card.classList.toggle("selected", checkbox.checked);
        updateBatchToolbar();
      }
    });
    selection.prepend(checkbox);
    head.append(selection);
  } else if (design.subject_id) {
    const selections = node("div", "card-selections");
    const decisionSelection = node("label", "lifecycle-selection", "Select for batch approval");
    const decisionCheckbox = node("input");
    decisionCheckbox.type = "checkbox";
    decisionCheckbox.checked = selectedSubjectIds.has(design.design_id);
    decisionCheckbox.addEventListener("change", () => {
      if (decisionCheckbox.checked) selectedSubjectIds.add(design.design_id);
      else selectedSubjectIds.delete(design.design_id);
      if ($("selection-filter").value) render();
      else {
        card.classList.toggle("selected", decisionCheckbox.checked);
        updateBatchToolbar();
      }
    });
    decisionSelection.prepend(decisionCheckbox);
    selections.append(decisionSelection);
    if (design.operator_decision?.decision === "approved") {
      const printSelection = node("label", "select-design", "Select for print generation");
      const printCheckbox = node("input");
      printCheckbox.type = "checkbox";
      printCheckbox.className = "design-selection";
      printCheckbox.value = design.design_id;
      printCheckbox.checked = selectedPrintDesignIds.has(design.design_id);
      printCheckbox.addEventListener("change", () => {
        if (printCheckbox.checked) selectedPrintDesignIds.add(design.design_id);
        else selectedPrintDesignIds.delete(design.design_id);
        updateBatchToolbar();
      });
      printSelection.prepend(printCheckbox);
      selections.append(printSelection);
    }
    head.append(selections);
  }
  card.append(head, designImage(design));
  const badges = summaryBadges(design.feedback_summary);
  badges.append(transparencyBadge(design), node("span", "badge", `pool: ${design.pool}`));
  const intake = intakeBadge(design);
  if (intake) badges.append(intake);
  if (design.subject_id) badges.append(node("span", "badge", `subject: ${design.subject_id}`));
  if (design.publication?.release_ids.length) {
    badges.append(node("span", "badge pass", `released · ${design.publication.product_profile_ids.length} profile(s)`));
  }
  card.append(badges, feedbackBlock(design));
  if (!design.subject_id) {
    card.append(seedActions(design));
    if (design.pool === "graduated") card.append(subjectGenerationControls(design));
  } else if (design.pool === "graduated") {
    card.append(subjectDecisionControls(design));
  }
  artifactBlocks(design, card);
  if (design.bundle_candidates.length) {
    const candidates = node("fieldset");
    candidates.append(node("legend", "", "Release candidates"));
    for (const candidate of design.bundle_candidates) {
      const label = node("label", "select-design", candidate.selection);
      const input = node("input");
      input.type = "checkbox";
      input.className = "bundle-selection";
      input.dataset.designId = candidate.design_id;
      input.dataset.versionId = candidate.version_id;
      input.dataset.profileId = candidate.product_profile_id;
      input.checked = selectedBundleCandidates.has(candidate.selection);
      input.addEventListener("change", () => {
        if (input.checked) selectedBundleCandidates.set(candidate.selection, candidate);
        else selectedBundleCandidates.delete(candidate.selection);
        updateBatchToolbar();
      });
      label.prepend(input);
      candidates.append(label);
    }
    card.append(candidates);
  }
  return card;
}

function renderSection(title, items, emptyText) {
  const section = node("section", "workflow-section");
  section.append(node("h2", "section-title", `${title} · ${items.length}`));
  const cards = node("div", "cards");
  if (!items.length) cards.append(node("p", "panel", emptyText));
  for (const design of items) cards.append(operatorCard(design));
  section.append(cards);
  return section;
}

function currentOperatorPoolItems() {
  if (!currentState) return [];
  if (activeOperatorTab === "active") {
    return currentState.designs.filter(item => item.pool === "active" && !item.subject_id);
  }
  if (activeOperatorTab === "graduated") {
    const graduated = currentState.designs.filter(item => item.pool === "graduated");
    const subjects = graduated.filter(item => item.subject_id);
    const unfinishedSubjects = subjects.filter(item => !isReleased(item));
    const unfinishedSeedIds = new Set(unfinishedSubjects.map(item => item.seed_design_id));
    const seedIdsWithSubjects = new Set(subjects.map(item => item.seed_design_id));
    const pendingSeeds = graduated.filter(item =>
      !item.subject_id
        && (unfinishedSeedIds.has(item.seed_design_id) || !seedIdsWithSubjects.has(item.seed_design_id))
    );
    return [...pendingSeeds, ...unfinishedSubjects];
  }
  if (activeOperatorTab === "released") {
    return currentState.designs.filter(item => item.pool === "graduated" && item.subject_id && isReleased(item));
  }
  return currentState.designs.filter(item => item.pool === "abandoned" && !item.subject_id);
}

function currentTabSelectionCount() {
  const poolIds = new Set(currentOperatorPoolItems().map(design => design.design_id));
  const selected = activeOperatorTab === "graduated" ? selectedSubjectIds : selectedLifecycleSeeds;
  return [...selected].filter(designId => poolIds.has(designId)).length;
}

function compactSelection(label, values) {
  const selected = [...values];
  if (!selected.length) return `${label}: none`;
  const visible = selected.slice(0, 4);
  return `${label}: ${visible.join(", ")}${selected.length > visible.length ? ` +${selected.length - visible.length} more` : ""}`;
}

function updateBatchToolbar() {
  if (!operatorMode || !currentState) return;
  const count = currentTabSelectionCount();
  $("batch-selection-count").textContent = `${count} selected`;
  const poolIds = new Set(currentOperatorPoolItems().map(item => item.design_id));
  const reviewSelection = activeOperatorTab === "graduated"
    ? [...selectedSubjectIds].filter(id => poolIds.has(id))
    : [...selectedLifecycleSeeds].filter(id => poolIds.has(id));
  $("review-batch-selection").textContent = compactSelection("Designs", reviewSelection);
  $("graduate-selected").classList.toggle("hidden", activeOperatorTab !== "active");
  $("abandon-selected").classList.toggle("hidden", activeOperatorTab !== "active");
  $("restore-selected").classList.toggle("hidden", activeOperatorTab !== "abandoned");
  $("approve-subjects-selected").classList.toggle("hidden", activeOperatorTab !== "graduated");
  $("approve-queue-subjects-selected").classList.toggle("hidden", activeOperatorTab !== "graduated");
  $("rework-subjects-selected").classList.toggle("hidden", activeOperatorTab !== "graduated");
  $("abandon-subjects-selected").classList.toggle("hidden", activeOperatorTab !== "graduated");
  $("graduate-selected").disabled = activeOperatorTab !== "active" || count === 0;
  $("abandon-selected").disabled = activeOperatorTab !== "active" || count === 0;
  $("restore-selected").disabled = activeOperatorTab !== "abandoned" || count === 0;
  $("approve-subjects-selected").disabled = activeOperatorTab !== "graduated" || count === 0;
  $("approve-queue-subjects-selected").disabled = activeOperatorTab !== "graduated" || count === 0;
  $("rework-subjects-selected").disabled = activeOperatorTab !== "graduated" || count === 0;
  $("abandon-subjects-selected").disabled = activeOperatorTab !== "graduated" || count === 0;
  $("batch-toolbar").classList.toggle("hidden", activeOperatorTab === "released");
  $("selection-filter-control").classList.toggle("hidden", activeOperatorTab === "released");
  $("subject-batch-stat").textContent = (
    `${selectedGenerationSeedIds.size} references × ${batchSubjectIds.size} subjects = `
    + `${selectedGenerationSeedIds.size * batchSubjectIds.size} calls`
  );
  $("subject-batch-selection").textContent = (
    `${compactSelection("References", selectedGenerationSeedIds)} · `
    + compactSelection("Subjects", batchSubjectIds)
  );
  $("product-batch-stat").textContent = (
    `${selectedPrintDesignIds.size} subjects × ${batchProfileIds.size} profiles = `
    + `${selectedPrintDesignIds.size * batchProfileIds.size} candidates`
  );
  $("product-batch-selection").textContent = (
    `${compactSelection("Subjects", selectedPrintDesignIds)} · `
    + compactSelection("Profiles", batchProfileIds)
  );
  $("release-batch-stat").textContent = `${selectedBundleCandidates.size} candidates selected`;
  $("release-batch-selection").textContent = compactSelection(
    "Candidates",
    [...selectedBundleCandidates.values()].map(
      item => `${item.design_id}/${item.product_profile_id}/${item.version_id}`,
    ),
  );
  $("generate-subjects-batch").disabled = !selectedGenerationSeedIds.size || !batchSubjectIds.size;
  $("generate").disabled = !selectedPrintDesignIds.size || !batchProfileIds.size;
  $("validate-release").disabled = selectedBundleCandidates.size === 0;
  $("publish-release").disabled = selectedBundleCandidates.size === 0;
  const poolItems = currentOperatorPoolItems();
  updateFilterCount(filteredDesigns(poolItems).length, poolItems.length);
}

function selectAllFiltered() {
  for (const design of filteredDesigns(currentOperatorPoolItems())) {
    if (!design.subject_id && ["active", "abandoned"].includes(design.pool)) {
      selectedLifecycleSeeds.add(design.design_id);
    } else if (design.subject_id) {
      selectedSubjectIds.add(design.design_id);
    }
  }
  render();
}

function clearCurrentSelection() {
  if (activeOperatorTab === "graduated") {
    selectedSubjectIds.clear();
    selectedPrintDesignIds.clear();
  } else selectedLifecycleSeeds.clear();
  render();
}

function visibleReferenceSeedIds() {
  return new Set(
    filteredDesigns(currentOperatorPoolItems())
      .map(design => design.seed_design_id)
      .filter(Boolean),
  );
}

function selectVisibleReleaseCandidates() {
  let added = 0;
  for (const design of filteredDesigns(currentOperatorPoolItems())) {
    for (const candidate of design.bundle_candidates || []) {
      const alreadyPublished = (design.publication?.current_entries || []).some(entry =>
        entry.version_id === candidate.version_id
          && entry.product_profile_id === candidate.product_profile_id
      );
      if (alreadyPublished) continue;
      if (!selectedBundleCandidates.has(candidate.selection)) added += 1;
      selectedBundleCandidates.set(candidate.selection, candidate);
    }
  }
  showStatus(`Selected ${added} additional release-ready candidate(s).`);
  render();
}

async function decideSelectedSubjects(decision, { queueProducts = false } = {}) {
  const graduatedIds = new Set(
    currentOperatorPoolItems()
      .filter(design => design.subject_id)
      .map(design => design.design_id),
  );
  const designIds = [...selectedSubjectIds].filter(designId => graduatedIds.has(designId));
  if (!designIds.length) return showStatus("Select at least one subject preview.", true);
  const action = decision === "approved"
    ? "approve"
    : decision === "rework"
      ? "mark as needs work"
      : "abandon";
  if (!window.confirm(`${action} for ${designIds.length} selected subject preview(s)?`)) return;
  const button = queueProducts
    ? $("approve-queue-subjects-selected")
    : decision === "approved"
      ? $("approve-subjects-selected")
      : decision === "rework"
        ? $("rework-subjects-selected")
        : $("abandon-subjects-selected");
  const operationId = newOperationId("decision");
  button.disabled = true;
  showStatus(`${action} in progress for ${designIds.length} subject preview(s)…`);
  monitorOperation(operationId);
  try {
    const result = await request("/api/operator/subject-decision-batch", {
      method: "POST",
      body: JSON.stringify({
        operation_id: operationId,
        design_ids: designIds,
        decision,
        note: `Batch ${decision} decision in the static design operator GUI`,
      }),
    });
    if (queueProducts && decision === "approved") {
      for (const designId of designIds) selectedPrintDesignIds.add(designId);
    }
    selectedSubjectIds.clear();
    const completed = decision === "approved" ? "Approved" : decision === "rework" ? "Marked for rework" : "Abandoned";
    showStatus(`${completed} ${result.count} subject preview(s).${queueProducts ? " They are selected for product generation." : ""}`);
    await loadState();
  } catch (error) {
    await loadState().catch(() => {});
    showStatus(`Batch subject decision failed: ${error.message}`, true);
  }
  finally { finishOperationMonitor(operationId); button.disabled = false; }
}

async function transitionSelected(action) {
  const designIds = [...selectedLifecycleSeeds].filter(designId =>
    currentOperatorPoolItems().some(design => design.design_id === designId),
  );
  if (!designIds.length) return showStatus("Select at least one seed in this tab.", true);
  if (!window.confirm(`${action} ${designIds.length} selected seed(s)?`)) return;
  const button = action === "graduate" ? $("graduate-selected") : action === "abandon" ? $("abandon-selected") : $("restore-selected");
  const operationId = newOperationId("seed");
  button.disabled = true;
  showStatus(`${action} in progress for ${designIds.length} seed(s)…`);
  monitorOperation(operationId);
  try {
    const result = await request("/api/operator/transition-batch", {
      method: "POST",
      body: JSON.stringify({ operation_id: operationId, seed_design_ids: designIds, action }),
    });
    selectedLifecycleSeeds.clear();
    showStatus(`${result.action} completed for ${result.count} seed(s).`);
    await loadState();
  } catch (error) { showStatus(error.message, true); }
  finally { finishOperationMonitor(operationId); button.disabled = false; }
}

function render() {
  const designs = $("designs");
  designs.replaceChildren();
  if (!operatorMode) {
    const filtered = filteredDesigns(currentState.designs);
    for (const design of filtered) designs.append(reviewerCard(design));
    updateFilterCount(filtered.length, currentState.designs.length);
    if (!filtered.length) {
      designs.append(node("p", "panel", currentState.designs.length ? "No active seed designs match the current filters." : "No active seed designs found."));
    }
    return;
  }
  const active = currentState.designs.filter(item => item.pool === "active" && !item.subject_id);
  const graduatedSeeds = currentState.designs.filter(item => item.pool === "graduated" && !item.subject_id);
  const subjects = currentState.designs.filter(item => item.pool === "graduated" && item.subject_id);
  const graduatedSubjects = subjects.filter(item => !isReleased(item));
  const releasedSubjects = subjects.filter(isReleased);
  const abandoned = currentState.designs.filter(item => item.pool === "abandoned" && !item.subject_id);
  const seedsWithUnreleasedWork = new Set(graduatedSubjects.map(item => item.seed_design_id));
  const seedsWithAnySubject = new Set(subjects.map(item => item.seed_design_id));
  const pendingSeeds = graduatedSeeds.filter(seed =>
    seedsWithUnreleasedWork.has(seed.seed_design_id)
      || !seedsWithAnySubject.has(seed.seed_design_id)
  );
  $("active-tab-count").textContent = active.length;
  $("graduated-tab-count").textContent = pendingSeeds.length + graduatedSubjects.length;
  $("released-tab-count").textContent = releasedSubjects.length;
  $("abandoned-tab-count").textContent = abandoned.length;
  document.querySelectorAll(".workflow-tab").forEach(button => {
    button.classList.toggle("active", button.dataset.tab === activeOperatorTab);
    button.setAttribute("aria-selected", button.dataset.tab === activeOperatorTab ? "true" : "false");
  });
  document.querySelectorAll(".production-control").forEach(panel => {
    panel.classList.toggle("hidden", !["graduated", "released"].includes(activeOperatorTab));
  });
  document.querySelectorAll(".subject-production-control").forEach(panel => {
    panel.classList.toggle("hidden", !["graduated", "released"].includes(activeOperatorTab));
  });
  if (activeOperatorTab === "active") {
    const visible = filteredDesigns(active);
    designs.append(renderSection("Active seed review", visible, active.length ? "No active seeds match the current filters." : "No seed designs are waiting for review."));
    updateFilterCount(visible.length, active.length);
  } else if (activeOperatorTab === "graduated") {
    const visibleSeeds = filteredDesigns(pendingSeeds);
    const visibleSubjects = filteredDesigns(graduatedSubjects);
    const neededSeedIds = new Set([
      ...visibleSeeds.map(item => item.seed_design_id),
      ...visibleSubjects.map(item => item.seed_design_id),
    ]);
    const tableSeeds = graduatedSeeds.filter(seed => neededSeedIds.has(seed.seed_design_id));
    designs.append(workflowTable(tableSeeds, visibleSubjects));
    updateFilterCount(visibleSeeds.length + visibleSubjects.length, pendingSeeds.length + graduatedSubjects.length);
  } else if (activeOperatorTab === "released") {
    const visibleSubjects = filteredDesigns(releasedSubjects);
    const neededSeedIds = new Set(visibleSubjects.map(item => item.seed_design_id));
    const tableSeeds = graduatedSeeds.filter(seed => neededSeedIds.has(seed.seed_design_id));
    designs.append(workflowTable(tableSeeds, visibleSubjects, { released: true }));
    updateFilterCount(visibleSubjects.length, releasedSubjects.length);
  } else {
    const visible = filteredDesigns(abandoned);
    designs.append(renderSection("Abandoned seeds", visible, abandoned.length ? "No abandoned seeds match the current filters." : "No abandoned seeds."));
    updateFilterCount(visible.length, abandoned.length);
  }
  const workspace = currentState.workspace;
  const workspaceSummary = $("operator-workspace");
  workspaceSummary.replaceChildren();
  workspaceSummary.append(node("p", "meta", `Workspace: ${workspace.work_root}`));
  const recovery = workspace.recovery || {};
  const recoveryCount = (recovery.removed_staging || []).length
    + (recovery.restored_backups || []).length
    + (recovery.quarantined || []).length;
  if (recoveryCount) {
    workspaceSummary.append(node(
      "p",
      "warning",
      `Startup recovery handled ${recoveryCount} path(s): ${(recovery.removed_staging || []).length} staging path(s) removed, ${(recovery.restored_backups || []).length} backup(s) restored, ${(recovery.quarantined || []).length} incomplete candidate(s) quarantined.`,
    ));
  }
  const importForm = node("form", "collection-import");
  const importLabel = node("label", "", "Load local design collection");
  const importInput = node("input");
  importInput.type = "text";
  importInput.placeholder = "/absolute/path/to/Design-Collection-1";
  importLabel.append(importInput);
  const importButton = node("button", "secondary", "Load PNG collection");
  importButton.type = "submit";
  importForm.append(importLabel, importButton);
  importForm.addEventListener("submit", async event => {
    event.preventDefault();
    const collectionRoot = importInput.value.trim();
    if (!collectionRoot) return showStatus("Enter the local design collection folder.", true);
    importButton.disabled = true;
    showStatus(`Loading design collection from ${collectionRoot}… Only machine-generated filenames require image-theme analysis.`);
    try {
      const result = await request("/api/operator/import-collection", {
        method: "POST",
        body: JSON.stringify({ collection_root: collectionRoot }),
      });
      activeOperatorTab = "active";
      const skipped = result.skipped.map(item => `${item.design_id}: ${item.reason}`);
      const assigned = [...result.imported, ...result.updated]
        .map(item => `${item.source.split(/[\\/]/).pop()} → ${item.design_id}`);
      showStatus(
        `Loaded ${result.collection_id}: ${result.imported.length} new, ${result.updated.length} updated, ${result.unchanged.length} unchanged, ${result.skipped.length} skipped; ${result.semantic_id_calls || 0} semantic naming call(s).${assigned.length ? `\nAssigned IDs:\n- ${assigned.join("\n- ")}` : ""}${skipped.length ? `\nSkipped:\n- ${skipped.join("\n- ")}` : ""}`,
      );
      await loadState();
    } catch (error) { showStatus(error.message, true); }
    finally { importButton.disabled = false; }
  });
  workspaceSummary.append(importForm);
  if (active.length === 0) {
    workspaceSummary.append(node(
      "p",
      "warning",
      `No active seeds were found at ${workspace.pools.active.root}. Confirm the server --work-root points to the directory containing static-design-inputs.`,
    ));
  }
  const options = $("profile-options"); options.replaceChildren();
  for (const profile of currentState.profiles) {
    const label = node("label", "", `${profile.product_profile_id} (preview ${profile.preview.width}×${profile.preview.height}; print ${profile.targets.map(t => `${t.width}×${t.height}`).join(", ")})`);
    const input = node("input"); input.type = "checkbox"; input.value = profile.product_profile_id; input.className = "profile-selection";
    input.checked = batchProfileIds.has(profile.product_profile_id);
    input.addEventListener("change", () => {
      if (input.checked) batchProfileIds.add(profile.product_profile_id);
      else batchProfileIds.delete(profile.product_profile_id);
      updateBatchToolbar();
    });
    label.prepend(input); options.append(label);
  }
  const subjectOptions = $("batch-subject-options"); subjectOptions.replaceChildren();
  for (const subject of currentState.subjects) {
    const label = node("label", "", subject.display_name);
    const input = node("input");
    input.type = "checkbox";
    input.value = subject.subject_id;
    input.checked = batchSubjectIds.has(subject.subject_id);
    input.addEventListener("change", () => {
      if (input.checked) batchSubjectIds.add(subject.subject_id);
      else batchSubjectIds.delete(subject.subject_id);
      updateBatchToolbar();
    });
    label.prepend(input);
    subjectOptions.append(label);
  }
  const publication = currentState.publication;
  const publicationConfig = $("publication-config");
  $("rebuild-discovery-catalog").disabled = !publication.enabled;
  if (!publication.enabled) {
    publicationConfig.textContent = "S3 is disabled. Set STATIC_PRINT_S3_BUCKET and restart the server.";
    publicationConfig.classList.add("warning");
  } else if (publication.status?.refreshing) {
    const known = publication.status?.available
      ? ` Showing the last catalog checked ${publication.status.checked_at}.`
      : " Local designs are available now; release status will appear automatically.";
    publicationConfig.textContent = `Refreshing S3 release status in the background.${known}`;
    publicationConfig.classList.add("warning");
    if (!publicationRefreshTimer) {
      publicationRefreshTimer = setTimeout(async () => {
        publicationRefreshTimer = null;
        await loadState();
      }, 3000);
    }
  } else if (publication.status?.available) {
    publicationConfig.textContent = `Destination: s3://${publication.bucket}/${publication.prefix}/ · FE discovery: s3://${publication.bucket}/${publication.prefix}/catalog.json · source: ${publication.status.source} · ${publication.status.bundle_count} product(s) · checked ${publication.status.checked_at}${publication.status.catalog_rebuild_required ? " · Recovery required: rebuild the FE discovery catalog" : ""}`;
    publicationConfig.classList.remove("warning");
  } else {
    publicationConfig.textContent = `Destination: s3://${publication.bucket}/${publication.prefix}/ · S3 release status ${publication.status?.stale ? "is stale" : "is unavailable"}: ${publication.status?.error || "unknown error"}`;
    publicationConfig.classList.add("warning");
  }
  updateBatchToolbar();
}

async function loadState() {
  try {
    currentState = await request(stateUrl);
    populateCollectionFilter();
    reconcileSelections();
    $("login").classList.add("hidden");
    $("reviewer-sticky-controls").classList.remove("hidden");
    $("reviewer-sticky-controls").classList.toggle("reviewer-mode", !operatorMode);
    $("preview-controls").classList.remove("hidden");
    $("filter-controls").classList.remove("hidden");
    if (operatorMode) {
      $("operator-tabs").classList.remove("hidden");
      $("operator-workspace").classList.remove("hidden");
      $("operator-actions").classList.remove("hidden");
      $("selection-filter-control").classList.remove("hidden");
    } else {
      $("reviewer-identity").classList.remove("hidden");
      $("reviewer-controls").classList.remove("hidden");
    }
    render();
    if (operatorMode && !operationPollTimer && !operationPollRunning) {
      operationPollTimer = setTimeout(pollOperations, 0);
    }
  } catch (error) {
    if (error.status === 401) $("login").classList.remove("hidden");
    else showStatus(error.message, true);
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await login($("access-code").value); await loadState(); }
  catch (error) { $("login-error").textContent = error.message; }
});

document.querySelectorAll(".preview-background").forEach(button => {
  button.addEventListener("click", () => updatePreviewBackground(button.dataset.preview));
});
document.querySelectorAll(".workflow-tab").forEach(button => {
  button.addEventListener("click", () => {
    activeOperatorTab = button.dataset.tab;
    selectedLifecycleSeeds.clear();
    selectedSubjectIds.clear();
    selectedPrintDesignIds.clear();
    selectedBundleCandidates.clear();
    selectedGenerationSeedIds.clear();
    render();
  });
});
for (const filter of [$("collection-filter"), $("transparency-filter"), $("review-filter"), $("selection-filter")]) {
  filter.addEventListener("change", render);
}
$("design-filter").addEventListener("input", render);
$("clear-filters").addEventListener("click", () => {
  $("collection-filter").value = "";
  $("design-filter").value = "";
  $("transparency-filter").value = "";
  $("review-filter").value = "";
  $("selection-filter").value = "";
  render();
});
$("select-visible").addEventListener("click", selectAllFiltered);
$("clear-selection").addEventListener("click", clearCurrentSelection);
$("graduate-selected").addEventListener("click", () => transitionSelected("graduate"));
$("abandon-selected").addEventListener("click", () => transitionSelected("abandon"));
$("restore-selected").addEventListener("click", () => transitionSelected("restore"));
$("approve-subjects-selected").addEventListener("click", () => decideSelectedSubjects("approved"));
$("approve-queue-subjects-selected").addEventListener("click", () => decideSelectedSubjects("approved", { queueProducts: true }));
$("rework-subjects-selected").addEventListener("click", () => decideSelectedSubjects("rework"));
$("abandon-subjects-selected").addEventListener("click", () => decideSelectedSubjects("abandoned"));
$("custom-preview-color").addEventListener("input", event => updatePreviewBackground(event.target.value));

$("select-reference-visible").addEventListener("click", () => {
  let added = 0;
  for (const seedId of visibleReferenceSeedIds()) {
    if (!selectedGenerationSeedIds.has(seedId)) added += 1;
    selectedGenerationSeedIds.add(seedId);
  }
  showStatus(`Selected ${added} additional filtered reference design(s).`);
  render();
});

$("clear-reference-selection").addEventListener("click", () => {
  selectedGenerationSeedIds.clear();
  showStatus("Cleared the reference-design selection.");
  render();
});

$("select-all-subjects").addEventListener("click", () => {
  for (const subject of currentState.subjects) batchSubjectIds.add(subject.subject_id);
  showStatus(`Selected all ${batchSubjectIds.size} subject catalog entries.`);
  render();
});

$("clear-subjects").addEventListener("click", () => {
  batchSubjectIds.clear();
  showStatus("Cleared the subject catalog selection.");
  render();
});

$("generate-subjects-batch").addEventListener("click", async () => {
  const seedIds = [...selectedGenerationSeedIds];
  const subjectIds = [...batchSubjectIds];
  if (!seedIds.length || !subjectIds.length) {
    return showStatus("Select at least one reference design and one subject.", true);
  }
  const callCount = seedIds.length * subjectIds.length;
  const force = $("batch-subject-force").checked;
  const replacement = force ? " Existing selected previews will be replaced." : "";
  if (!window.confirm(
    `Run up to ${callCount} paid subject-generation call(s) across ${seedIds.length} reference(s) and ${subjectIds.length} subject(s)? Existing previews are skipped by default.${replacement}`,
  )) return;
  const button = $("generate-subjects-batch");
  const operationId = newOperationId("subject");
  button.disabled = true;
  showStatus(`Generating ${callCount} subject preview(s)… This paid operation may take several minutes.`);
  monitorOperation(operationId);
  try {
    const result = await request("/api/operator/generate-subjects-batch", {
      method: "POST",
      body: JSON.stringify({
        operation_id: operationId,
        seed_design_ids: seedIds,
        subject_ids: subjectIds,
        force,
        instruction: $("batch-subject-instruction").value,
      }),
    });
    selectedGenerationSeedIds.clear();
    showStatus(
      `Generated ${result.generated_count} subject preview(s) across ${result.seed_count} reference design(s). `
      + `Skipped ${result.skipped_existing.length} existing preview(s).`,
    );
    await loadState();
  } catch (error) {
    await loadState().catch(() => {});
    showStatus(`Batch subject generation failed: ${error.message}`, true);
  }
  finally {
    finishOperationMonitor(operationId);
    button.disabled = false;
  }
});

$("select-all-profiles").addEventListener("click", () => {
  for (const profile of currentState.profiles) {
    batchProfileIds.add(profile.product_profile_id);
  }
  showStatus(`Selected all ${batchProfileIds.size} product profiles for this batch.`);
  render();
});

$("clear-profiles").addEventListener("click", () => {
  batchProfileIds.clear();
  showStatus("Cleared the batch product-profile selection.");
  render();
});

$("select-product-visible").addEventListener("click", () => {
  let added = 0;
  for (const design of filteredDesigns(currentOperatorPoolItems())) {
    if (design.subject_id && design.operator_decision?.decision === "approved") {
      if (!selectedPrintDesignIds.has(design.design_id)) added += 1;
      selectedPrintDesignIds.add(design.design_id);
    }
  }
  showStatus(`Included ${added} additional approved row(s) for product planning.`);
  render();
});

$("clear-product-plans").addEventListener("click", () => {
  selectedPrintDesignIds.clear();
  for (const plan of productPlans.values()) plan.included = false;
  showStatus("Cleared all pending product row and profile plans.");
  render();
});

$("generate").addEventListener("click", () => {
  if (!selectedPrintDesignIds.size) {
    return showStatus("Select at least one approved subject row first.", true);
  }
  if (!batchProfileIds.size) {
    return showStatus("Select at least one profile to apply.", true);
  }
  const designs = new Map(currentState.designs.map(design => [design.design_id, design]));
  const plans = [];
  for (const designId of selectedPrintDesignIds) {
    const design = designs.get(designId);
    if (!design || design.operator_decision?.decision !== "approved") continue;
    for (const profileId of batchProfileIds) {
      plans.push({ design_id: designId, product_profile_id: profileId });
    }
  }
  return runProductPlans(plans, $("generate"), "selected subject/profile candidate(s)");
});

async function runRelease(candidates, execute, button, releaseId, { clearGlobal = false } = {}) {
  const selected = candidates.map(candidate => ({
    design_id: candidate.design_id,
    version_id: candidate.version_id,
    product_profile_id: candidate.product_profile_id,
  }));
  if (!selected.length) return showStatus("Select at least one release candidate.", true);
  const operationId = newOperationId(execute ? "publish" : "validate");
  button.disabled = true;
  showStatus(`${execute ? "Publishing" : "Validating"} ${selected.length} bundle(s)…`);
  monitorOperation(operationId);
  try {
    const result = await request("/api/operator/publish", {
      method: "POST",
      body: JSON.stringify({ operation_id: operationId, release_id: releaseId, selections: selected, execute }),
    });
    if (clearGlobal) $("release-id").value = result.release_id;
    showStatus(`${execute ? "Published" : "Validated"} release ${result.release_id}.\nCatalog: ${result.catalog}\nObjects: ${result.objects.length}`);
    if (execute) {
      for (const candidate of candidates) {
        selectedBundleCandidates.delete(candidate.selection);
      }
      if (clearGlobal) $("release-id").value = "";
    }
    await loadState();
  } catch (error) {
    await loadState().catch(() => {});
    showStatus(`${execute ? "Publication" : "Release validation"} failed: ${error.message}`, true);
  }
  finally { finishOperationMonitor(operationId); button.disabled = false; }
}

async function release(execute) {
  const candidates = [...selectedBundleCandidates.values()];
  const releaseId = $("release-id").value.trim() || "auto";
  const button = execute ? $("publish-release") : $("validate-release");
  return runRelease(candidates, execute, button, releaseId, { clearGlobal: true });
}

async function publishCandidate(candidate, button) {
  if (!window.confirm(
    `Validate and publish ${candidate.design_id} / ${candidate.product_profile_id} / ${candidate.version_id} in a new immutable release?`,
  )) return;
  return runRelease([candidate], true, button, "auto");
}

async function rebuildDiscoveryCatalog() {
  if (!window.confirm(
    "Rebuild the stable FE discovery catalog from immutable S3 release history? This creates no bundle or release.",
  )) return;
  const button = $("rebuild-discovery-catalog");
  button.disabled = true;
  showStatus("Rebuilding FE discovery catalog from S3 release history…");
  try {
    const result = await request("/api/operator/rebuild-catalog", {
      method: "POST",
      body: "{}",
    });
    showStatus(`FE discovery catalog rebuilt.\nCatalog: ${result.uri}\nProducts: ${result.product_count}`);
    await loadState();
  } catch (error) {
    await loadState().catch(() => {});
    showStatus(`Discovery catalog rebuild failed: ${error.message}`, true);
  } finally { button.disabled = false; }
}

$("select-release-visible").addEventListener("click", selectVisibleReleaseCandidates);
$("clear-release-selection").addEventListener("click", () => {
  selectedBundleCandidates.clear();
  showStatus("Cleared the release-candidate selection.");
  render();
});
$("validate-release").addEventListener("click", () => release(false));
$("publish-release").addEventListener("click", () => release(true));
$("rebuild-discovery-catalog").addEventListener("click", rebuildDiscoveryCatalog);
$("retire-selected-products").addEventListener("click", () => changeSelectedProductLifecycle("retired"));
$("reactivate-selected-products").addEventListener("click", () => changeSelectedProductLifecycle("active"));
if (!operatorMode) $("save-review-feedback").addEventListener("click", saveReviewerFeedback);

if (operatorMode) {
  $("page-title").textContent = "Static print operator workflow";
  $("page-subtitle").textContent = "Review seeds, expand subjects, approve previews, generate print candidates, and publish immutable releases.";
  $("mode-link").textContent = "Reviewer gallery";
  $("mode-link").href = "/";
}
loadState();
