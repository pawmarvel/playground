"use strict";

const boot = window.PAWMARVEL_BOOTSTRAP;
const state = structuredClone(boot.layout);
const canvas = document.querySelector("#preview");
const context = canvas.getContext("2d");
const referenceCanvas = document.querySelector("#reference");
const referenceContext = referenceCanvas.getContext("2d");
const statusNode = document.querySelector("#status");
const metricsNode = document.querySelector("#text-metrics");
const fontReferenceStatus = document.querySelector("#font-reference-status");
const petNameInput = document.querySelector("#preview-pet-name");
const nameLayerToggle = document.querySelector("#name-layer-enabled");
const nameLayerControls = document.querySelector("#name-layer-controls");
const previewPetSelect = document.querySelector("#preview-pet");
const previewPetUpload = document.querySelector("#preview-pet-upload");
const referenceTextInput = document.querySelector("#reference-text");
const matchReferenceScaleButton = document.querySelector("#match-reference-scale");
const selectPetRegionButton = document.querySelector("#select-pet-region");
const selectNameRegionButton = document.querySelector("#select-name-region");
const applyReferenceLayoutButton = document.querySelector("#apply-reference-layout");
const referenceRegionMode = document.querySelector("#reference-region-mode");
const fontSearchQuery = document.querySelector("#font-search-query");
const fontSearchButton = document.querySelector("#font-search-button");
const fontSearchResults = document.querySelector("#font-search-results");
const fontImportButton = document.querySelector("#font-import-button");
const fontSearchStatus = document.querySelector("#font-search-status");
const saveButtons = [document.querySelector("#save"), document.querySelector("#complete")];

petNameInput.value = boot.petName;
referenceTextInput.value = boot.referenceText;
document.querySelector("#name-mode").textContent =
  "Production starts at one fixed nominal size and only shrinks longer names to fit. Resizing the name box height scales that authored size proportionally.";
canvas.width = boot.canvas.width;
canvas.height = boot.canvas.height;
referenceCanvas.width = boot.referenceCanvas.width;
referenceCanvas.height = boot.referenceCanvas.height;

let previewImage = null;
let nameEnabled = boot.nameEnabled;
let previewTimer = null;
let previewController = null;
let drag = null;
let selectedFontId = boot.selectedFontId;
let selectedPreviewPetId = "pinned";
let stateRevision = 0;
let renderedRevision = -1;
let editorLocked = false;
let fontSpecimen = null;
let fontSelect = null;
let fontHelp = null;
let fontSelectionConfirmed = boot.fontSelectionConfirmed;
let fontRanking = boot.fontRanking || null;
let fontFaceStyle = null;
let fontReferenceRevision = 0;
let rankedReferenceRevision = fontRanking ? 0 : -1;
let rankTimer = null;
let rankController = null;
let referenceDrag = null;
let referenceSelectionMode = boot.layoutReference ? "name" : "pet";
let referenceRegion = boot.fontReference
  ? structuredClone(boot.fontReference.region)
  : boot.layoutReference
    ? structuredClone(boot.layoutReference.name_region)
    : null;
let petReferenceRegion = boot.layoutReference
  ? structuredClone(boot.layoutReference.pet_region)
  : null;
let referenceGeometryApplied = Boolean(boot.layoutReference);

const referenceImage = new Image();
referenceImage.onload = () => {
  drawReference();
  if (referenceRegion && referenceTextInput.value.trim() && !fontRanking) {
    requestFontRanking();
  }
};
referenceImage.src = boot.referenceDataUrl;

function currentFontReference() {
  if (!nameEnabled) return null;
  if (!referenceRegion || !referenceTextInput.value.trim()) return null;
  return {
    region: structuredClone(referenceRegion),
    text: referenceTextInput.value,
  };
}

function currentLayoutReference() {
  if (!nameEnabled) return null;
  if (!petReferenceRegion || !referenceRegion) return null;
  return {
    pet_region: structuredClone(petReferenceRegion),
    name_region: structuredClone(referenceRegion),
  };
}

function setReferenceSelectionMode(mode) {
  referenceSelectionMode = mode;
  referenceRegionMode.textContent = mode === "pet"
    ? "Drag a box around the intended pet placement region."
    : "Drag a box around the complete personalized-name region.";
  selectPetRegionButton.classList.toggle("active", mode === "pet");
  selectNameRegionButton.classList.toggle("active", mode === "name");
}

function setApplyReferenceEnabled() {
  applyReferenceLayoutButton.disabled = editorLocked || (
    nameEnabled ? !currentLayoutReference() : !petReferenceRegion
  );
}

function canSave() {
  const fontReady = !nameEnabled || !boot.autoFont ||
    (fontSelectionConfirmed && rankedReferenceRevision === fontReferenceRevision);
  const referenceGeometryReady = !currentLayoutReference() || referenceGeometryApplied;
  return !editorLocked && renderedRevision === stateRevision && fontReady && referenceGeometryReady;
}

function setSaveEnabled() {
  for (const button of saveButtons) button.disabled = !canSave();
}

function canCalibrateFontSize() {
  return Boolean(
    nameEnabled && fontRanking && selectedFontId &&
    (!boot.autoFont || fontSelectionConfirmed)
  );
}

function layoutForRequest() {
  const layout = structuredClone(state);
  if (!nameEnabled) delete layout.name;
  return layout;
}

function syncNameLayerMode() {
  nameLayerToggle.checked = nameEnabled;
  nameLayerControls.disabled = !nameEnabled;
  selectNameRegionButton.disabled = !nameEnabled || editorLocked;
  referenceTextInput.disabled = !nameEnabled || editorLocked;
  document.querySelector("#rank-fonts").disabled = !nameEnabled || editorLocked;
  matchReferenceScaleButton.disabled = !nameEnabled || editorLocked ||
    !canCalibrateFontSize();
  applyReferenceLayoutButton.disabled = editorLocked ||
    (nameEnabled ? !currentLayoutReference() : !petReferenceRegion);
  metricsNode.textContent = nameEnabled
    ? "Text metrics pending."
    : "Separate pet-name text layer disabled.";
}

function setEditorLocked(locked) {
  editorLocked = locked;
  for (const control of document.querySelectorAll("input, select, button")) {
    control.disabled = locked;
  }
  if (!locked) {
    syncNameLayerMode();
    if (fontSelect) fontSelect.disabled = !nameEnabled || !fontRanking;
    setSaveEnabled();
  }
}

function markDirty() {
  if (editorLocked) return;
  stateRevision += 1;
  renderedRevision = -1;
  if (previewController) previewController.abort();
  canvas.classList.add("stale");
  setSaveEnabled();
  metricsNode.textContent = "Text metrics pending.";
  statusNode.textContent = `Preview stale; rendering revision ${stateRevision}…`;
}

function markFontReferenceDirty() {
  if (editorLocked) return;
  fontReferenceRevision += 1;
  rankedReferenceRevision = -1;
  fontRanking = null;
  if (rankController) rankController.abort();
  if (boot.autoFont) fontSelectionConfirmed = false;
  setSaveEnabled();
  renderFontOptions();
  fontReferenceStatus.textContent =
    "Reference typography changed; analyze fonts again.";
}

function scaledReferenceBox(region) {
  const left = Math.round(region.x * canvas.width / referenceCanvas.width);
  const top = Math.round(region.y * canvas.height / referenceCanvas.height);
  const right = Math.round(
    (region.x + region.width) * canvas.width / referenceCanvas.width
  );
  const bottom = Math.round(
    (region.y + region.height) * canvas.height / referenceCanvas.height
  );
  return {
    x: left,
    y: top,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top),
  };
}

function applyReferenceGeometry() {
  if (!petReferenceRegion) return;
  state.pet.box = scaledReferenceBox(petReferenceRegion);
  if (!nameEnabled) {
    referenceGeometryApplied = true;
    syncInputs();
    markDirty();
    draw();
    schedulePreview();
    return;
  }
  const reference = currentLayoutReference();
  if (!reference) return;
  state.name.box = scaledReferenceBox(reference.name_region);
  state.name.font_size_px = state.name.box.height;
  state.name.min_font_size_px = Math.max(1, Math.round(state.name.box.height * 0.5));
  state.name.padding_px = Math.min(4, Math.max(0, Math.floor((state.name.box.height - 1) / 2)));
  referenceGeometryApplied = true;
  syncInputs();
  markDirty();
  draw();
  schedulePreview();
  if (fontRanking && fontSelectionConfirmed) calibrateFontSize();
  referenceRegionMode.textContent =
    "Applied normalized reference geometry. Review it against the generated art before saving.";
}

function candidateById(candidateId) {
  return boot.fontCandidates.find(value => value.id === candidateId);
}

function selectFont(candidateId, confirmed = true) {
  const candidate = candidateById(candidateId);
  if (!candidate) return;
  const changed = selectedFontId !== candidate.id || state.name.font !== candidate.relativeName;
  selectedFontId = candidate.id;
  state.name.font = candidate.relativeName;
  fontSelectionConfirmed = confirmed;
  if (fontSpecimen) fontSpecimen.style.fontFamily = `"${candidate.id}"`;
  if (changed) {
    markDirty();
    schedulePreview();
  } else {
    setSaveEnabled();
  }
  if (confirmed && fontRanking && currentFontReference()) calibrateFontSize();
}

function buildFontCatalog() {
  const host = document.querySelector("#font-catalog");
  fontFaceStyle = document.createElement("style");
  for (const candidate of boot.fontCandidates) {
    addFontFace(candidate);
  }
  document.head.append(fontFaceStyle);

  fontHelp = document.createElement("p");
  fontHelp.className = "font-help";
  const label = document.createElement("label");
  label.className = "font-select-label";
  label.textContent = "Top font recommendations";
  fontSelect = document.createElement("select");
  fontSelect.id = "font-recommendations";
  fontSelect.setAttribute("aria-label", "Top 15 font recommendations");
  fontSelect.addEventListener("change", () => {
    if (fontSelect.value) {
      selectFont(fontSelect.value, true);
      renderFontOptions();
    }
  });
  label.append(fontSelect);
  fontSpecimen = document.createElement("div");
  fontSpecimen.className = "font-specimen";
  fontSpecimen.textContent = petNameInput.value;
  const current = candidateById(selectedFontId);
  if (current) fontSpecimen.style.fontFamily = `"${current.id}"`;
  host.append(fontHelp, label, fontSpecimen);
  renderFontOptions();
}

function addFontFace(candidate) {
  if (!fontFaceStyle) return;
  fontFaceStyle.textContent +=
    `@font-face { font-family: "${candidate.id}"; src: url("/fonts/${candidate.id}") format("truetype"); }\n`;
}

async function remoteFontRequest(path, payload, operation, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error(`${operation} timed out after ${Math.round(timeoutMs / 1000)} seconds. Check GitHub access and retry.`);
    }
    throw new Error(
      `${operation} could not reach the local layout service. ` +
      "Check the layout CLI terminal for the upstream GitHub error and retry."
    );
  } finally {
    clearTimeout(timeout);
  }
  const body = await response.text();
  let result;
  try {
    result = body ? JSON.parse(body) : {};
  } catch (_error) {
    throw new Error(`${operation} received an invalid response from the local layout service (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    throw new Error(result.error || `${operation} failed with HTTP ${response.status}`);
  }
  return result;
}

async function searchFonts() {
  const query = fontSearchQuery.value.trim();
  if (!query) {
    fontSearchStatus.textContent = "Enter a font family name first.";
    return;
  }
  fontSearchButton.disabled = true;
  fontImportButton.hidden = true;
  fontSearchResults.hidden = true;
  fontSearchStatus.textContent = "Searching the local catalog…";
  try {
    const result = await remoteFontRequest(
      "/search-fonts", {query}, "Font search", 45000
    );
    fontSearchResults.replaceChildren();
    const matches = [...result.local, ...result.remote];
    for (const match of matches) {
      const option = document.createElement("option");
      option.value = match.source === "local" ? match.font_id : match.family_id;
      option.dataset.source = match.source;
      option.textContent = match.source === "local"
        ? `${match.label} — already local`
        : `${match.label} — Google Fonts OFL`;
      fontSearchResults.append(option);
    }
    if (!matches.length) {
      fontSearchStatus.textContent =
        "No matching OFL family was found. Check the family spelling.";
      return;
    }
    fontSearchResults.hidden = false;
    fontImportButton.hidden = false;
    fontImportButton.textContent = "Use or download selected font";
    fontSearchStatus.textContent = result.remote.length
      ? "Local suggestions and official Google Fonts OFL matches are shown."
      : "The requested family is already available locally.";
  } catch (error) {
    fontSearchStatus.textContent = `Font search failed: ${error.message}`;
  } finally {
    fontSearchButton.disabled = editorLocked;
  }
}

async function importOrSelectFont() {
  const option = fontSearchResults.selectedOptions[0];
  if (!option) return;
  if (option.dataset.source === "local") {
    selectFont(option.value, true);
    renderFontOptions();
    fontSearchStatus.textContent =
      `Using local font ${candidateById(option.value)?.label || option.textContent}.`;
    return;
  }
  fontImportButton.disabled = true;
  fontSearchStatus.textContent = "Downloading and validating OFL artifacts…";
  try {
    const result = await remoteFontRequest(
      "/import-font",
      {family_id: option.value},
      "Font download",
      120000,
    );
    for (const candidate of result.candidates) {
      if (!candidateById(candidate.id)) {
        boot.fontCandidates.push(candidate);
        addFontFace(candidate);
      }
    }
    const selected = result.candidates[0];
    if (!selected) throw new Error("downloaded family contains no usable TTF font");
    if (currentFontReference()) {
      await requestFontRanking({preserveSelection: true});
    }
    selectFont(selected.id, true);
    renderFontOptions();
    fontSearchResults.replaceChildren();
    for (const candidate of result.candidates) {
      const styleOption = document.createElement("option");
      styleOption.value = candidate.id;
      styleOption.dataset.source = "local";
      styleOption.textContent = `${candidate.label} — downloaded this session`;
      fontSearchResults.append(styleOption);
    }
    fontImportButton.textContent = "Use selected imported font";
    fontSearchStatus.textContent =
      `Downloaded ${result.family}. The selected style is now active; choose another family style here if needed.`;
  } catch (error) {
    fontSearchStatus.textContent = `Font download failed: ${error.message}`;
  } finally {
    fontImportButton.disabled = editorLocked;
  }
}

function renderFontOptions() {
  if (!fontSelect || !fontHelp) return;
  fontSelect.replaceChildren();
  if (!fontRanking) {
    const option = document.createElement("option");
    option.textContent = boot.autoFont
      ? "Analyze the confirmed reference lettering first"
      : `Explicit font: ${candidateById(selectedFontId)?.label || state.name.font}`;
    option.value = "";
    option.selected = true;
    fontSelect.append(option);
    fontSelect.disabled = true;
    matchReferenceScaleButton.disabled = true;
    fontHelp.textContent = boot.autoFont
      ? "No font is automatically accepted until the visible reference text and its exact region are analyzed."
      : "The explicit font is active. Reference analysis is optional.";
    return;
  }

  const recommendation = fontRanking.recommendation;
  const ranked = fontRanking.ranked_options.slice(0, 15);
  if (!ranked.some(value => value.font_id === selectedFontId)) {
    const selected = fontRanking.ranked_options.find(
      value => value.font_id === selectedFontId
    );
    if (selected) ranked.push(selected);
  }
  if (boot.autoFont && !fontSelectionConfirmed) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select a font after reviewing the candidates";
    placeholder.selected = true;
    fontSelect.append(placeholder);
  }
  for (const match of ranked) {
    const option = document.createElement("option");
    option.value = match.font_id;
    const suffix = match.rank === 1 ? " — recommended" : "";
    option.textContent =
      `#${match.rank} ${match.label} — similarity ${Math.round(match.similarity_score * 100)}%, ` +
      `evidence ${Math.round(match.confidence_score * 100)}% (${match.confidence_level})${suffix}`;
    option.selected = fontSelectionConfirmed && match.font_id === selectedFontId;
    fontSelect.append(option);
  }
  fontSelect.disabled = false;
  matchReferenceScaleButton.disabled = !canCalibrateFontSize();
  fontHelp.textContent =
    `Recommendation: ${candidateById(recommendation.font_id)?.label || recommendation.font} ` +
    `(${recommendation.confidence_level} confidence; ` +
    `${Math.round(recommendation.similarity_score * 100)}% similarity). ` +
    (recommendation.auto_select
      ? "High-confidence recommendation applied automatically."
      : fontSelectionConfirmed
        ? "Manual font selection confirmed."
        : "Manual selection is required.");
}

buildFontCatalog();

const numericFields = [
  ["pet", "x", "Pet x"], ["pet", "y", "Pet y"],
  ["pet", "width", "Pet width"], ["pet", "height", "Pet height"],
  ["name", "x", "Name x"], ["name", "y", "Name y"],
  ["name", "width", "Name width"], ["name", "height", "Name height"],
  ["name", "font_size_px", "Nominal font size"],
  ["name", "min_font_size_px", "Minimum font size"],
  ["name", "padding_px", "Safety padding"],
];

function scaleNameTypography(original, nextHeight, nextWidth) {
  if (!original || original.height <= 0 || nextHeight <= 0) return;
  const ratio = nextHeight / original.height;
  state.name.font_size_px = Math.max(1, Math.round(original.fontSize * ratio));
  state.name.min_font_size_px = Math.min(
    state.name.font_size_px,
    Math.max(1, Math.round(original.minFontSize * ratio)),
  );
  const maximumPadding = Math.max(0, Math.floor((Math.min(nextWidth, nextHeight) - 1) / 2));
  state.name.padding_px = Math.min(
    maximumPadding,
    Math.max(0, Math.round(original.padding * ratio)),
  );
}

function valueFor(section, key) {
  if (["x", "y", "width", "height"].includes(key)) return state[section].box[key];
  return state[section][key];
}

function setValue(section, key, value) {
  if (["x", "y", "width", "height"].includes(key)) state[section].box[key] = value;
  else state[section][key] = value;
}

function addNumberControl(section, key, labelText) {
  const host = document.querySelector(section === "pet" ? "#pet-controls" : "#name-controls");
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "number";
  input.value = valueFor(section, key);
  input.dataset.section = section;
  input.dataset.key = key;
  input.addEventListener("input", () => {
    const number = Number(input.value);
    if (Number.isFinite(number)) {
      const typography = section === "name" && key === "height"
        ? {
            height: state.name.box.height,
            fontSize: state.name.font_size_px,
            minFontSize: state.name.min_font_size_px,
            padding: state.name.padding_px,
          }
        : null;
      setValue(section, key, Math.round(number));
      if (typography && number > 0) {
        scaleNameTypography(typography, Math.round(number), state.name.box.width);
        syncInputs();
      }
      markDirty();
      draw();
      schedulePreview();
    }
  });
  label.append(input);
  host.append(label);
}

for (const field of numericFields) addNumberControl(...field);

function addSelect(key, labelText, values) {
  const host = document.querySelector("#name-controls");
  const label = document.createElement("label");
  label.textContent = labelText;
  const select = document.createElement("select");
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = state.name[key] === value;
    select.append(option);
  }
  select.addEventListener("change", () => {
    state.name[key] = select.value;
    markDirty();
    schedulePreview();
  });
  label.append(select);
  host.append(label);
}

addSelect("horizontal_align", "Horizontal alignment", ["left", "center", "right"]);

const colorLabel = document.createElement("label");
colorLabel.textContent = "Text color";
const colorInput = document.createElement("input");
colorInput.type = "color";
colorInput.value = state.name.color.slice(0, 7);
colorInput.addEventListener("input", () => {
  state.name.color = `${colorInput.value}FF`;
  markDirty();
  schedulePreview();
});
colorLabel.append(colorInput);
document.querySelector("#name-controls").append(colorLabel);

petNameInput.addEventListener("input", () => {
  if (fontSpecimen) fontSpecimen.textContent = petNameInput.value || " ";
  markDirty();
  schedulePreview();
});

previewPetSelect.addEventListener("change", () => {
  selectedPreviewPetId = previewPetSelect.value;
  markDirty();
  schedulePreview();
});

previewPetUpload.addEventListener("change", async () => {
  const file = previewPetUpload.files?.[0];
  if (!file) return;
  previewPetUpload.disabled = true;
  statusNode.textContent = `Loading transformed pet ${file.name}…`;
  try {
    const response = await fetch("/preview-pet", {
      method: "POST",
      headers: {
        "Content-Type": file.type || "application/octet-stream",
        "X-PawMarvel-Pet-Name": encodeURIComponent(file.name),
      },
      body: file,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || response.statusText);
    let option = Array.from(previewPetSelect.options).find(
      value => value.value === result.id,
    );
    if (!option) {
      option = document.createElement("option");
      option.value = result.id;
      previewPetSelect.append(option);
    }
    option.textContent = file.name;
    previewPetSelect.value = result.id;
    selectedPreviewPetId = result.id;
    markDirty();
    schedulePreview();
  } catch (error) {
    statusNode.textContent = `Transformed pet could not be loaded: ${error.message}`;
  } finally {
    previewPetUpload.value = "";
    previewPetUpload.disabled = editorLocked;
  }
});

referenceTextInput.addEventListener("input", () => {
  markFontReferenceDirty();
  scheduleFontRanking();
});

function syncInputs() {
  for (const input of document.querySelectorAll("input[data-section]")) {
    input.value = valueFor(input.dataset.section, input.dataset.key);
  }
}

function drawBox(box, color) {
  context.strokeStyle = color;
  context.lineWidth = Math.max(2, canvas.width / 400);
  context.strokeRect(box.x, box.y, box.width, box.height);
  const handle = Math.max(10, canvas.width / 50);
  context.fillStyle = color;
  context.fillRect(box.x + box.width - handle, box.y + box.height - handle, handle, handle);
}

function draw() {
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (previewImage) context.drawImage(previewImage, 0, 0, canvas.width, canvas.height);
  drawBox(state.pet.box, "#ff4f4f");
  if (nameEnabled) drawBox(state.name.box, "#40c0ff");
}

function drawReference() {
  referenceContext.clearRect(0, 0, referenceCanvas.width, referenceCanvas.height);
  if (referenceImage.complete && referenceImage.naturalWidth) {
    referenceContext.drawImage(
      referenceImage, 0, 0, referenceCanvas.width, referenceCanvas.height
    );
  }
  if (petReferenceRegion) {
    referenceContext.fillStyle = "rgba(255, 79, 79, 0.12)";
    referenceContext.fillRect(
      petReferenceRegion.x,
      petReferenceRegion.y,
      petReferenceRegion.width,
      petReferenceRegion.height,
    );
    referenceContext.strokeStyle = "#ff4f4f";
    referenceContext.lineWidth = Math.max(1, referenceCanvas.width / 240);
    referenceContext.strokeRect(
      petReferenceRegion.x,
      petReferenceRegion.y,
      petReferenceRegion.width,
      petReferenceRegion.height,
    );
  }
  if (referenceRegion) {
    referenceContext.fillStyle = "rgba(64, 192, 255, 0.16)";
    referenceContext.fillRect(
      referenceRegion.x,
      referenceRegion.y,
      referenceRegion.width,
      referenceRegion.height,
    );
    referenceContext.strokeStyle = "#40c0ff";
    referenceContext.lineWidth = Math.max(1, referenceCanvas.width / 240);
    referenceContext.strokeRect(
      referenceRegion.x,
      referenceRegion.y,
      referenceRegion.width,
      referenceRegion.height,
    );
  }
}

function pointInCanvas(event, target) {
  const rect = target.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(target.width - 1, (event.clientX - rect.left) * target.width / rect.width)),
    y: Math.max(0, Math.min(target.height - 1, (event.clientY - rect.top) * target.height / rect.height)),
  };
}

function hit(box, point) {
  const handle = Math.max(12, canvas.width / 40);
  if (Math.abs(point.x - (box.x + box.width)) <= handle && Math.abs(point.y - (box.y + box.height)) <= handle) return "resize";
  if (point.x >= box.x && point.x <= box.x + box.width && point.y >= box.y && point.y <= box.y + box.height) return "move";
  return null;
}

canvas.addEventListener("pointerdown", event => {
  if (editorLocked) return;
  const point = pointInCanvas(event, canvas);
  for (const section of (nameEnabled ? ["name", "pet"] : ["pet"])) {
    const mode = hit(state[section].box, point);
    if (mode) {
      markDirty();
      drag = {
        section,
        mode,
        start: point,
        original: structuredClone(state[section].box),
        typography: section === "name" && mode === "resize"
          ? {
              height: state.name.box.height,
              fontSize: state.name.font_size_px,
              minFontSize: state.name.min_font_size_px,
              padding: state.name.padding_px,
            }
          : null,
      };
      canvas.setPointerCapture(event.pointerId);
      return;
    }
  }
});

canvas.addEventListener("pointermove", event => {
  if (!drag) return;
  const point = pointInCanvas(event, canvas);
  const dx = Math.round(point.x - drag.start.x);
  const dy = Math.round(point.y - drag.start.y);
  const box = state[drag.section].box;
  if (drag.mode === "move") {
    box.x = drag.original.x + dx;
    box.y = drag.original.y + dy;
  } else {
    box.width = Math.max(1, drag.original.width + dx);
    box.height = Math.max(1, drag.original.height + dy);
    if (drag.section === "name") {
      scaleNameTypography(drag.typography, box.height, box.width);
    }
  }
  syncInputs();
  draw();
});

canvas.addEventListener("pointerup", () => {
  if (drag) schedulePreview();
  drag = null;
});

referenceCanvas.addEventListener("pointerdown", event => {
  if (editorLocked) return;
  const point = pointInCanvas(event, referenceCanvas);
  referenceDrag = {start: point, mode: referenceSelectionMode};
  const region = {x: Math.round(point.x), y: Math.round(point.y), width: 1, height: 1};
  if (referenceSelectionMode === "pet") {
    petReferenceRegion = region;
  } else {
    referenceRegion = region;
    markFontReferenceDirty();
  }
  referenceGeometryApplied = false;
  setSaveEnabled();
  setApplyReferenceEnabled();
  referenceCanvas.setPointerCapture(event.pointerId);
  drawReference();
});

referenceCanvas.addEventListener("pointermove", event => {
  if (!referenceDrag) return;
  const point = pointInCanvas(event, referenceCanvas);
  const left = Math.round(Math.min(referenceDrag.start.x, point.x));
  const top = Math.round(Math.min(referenceDrag.start.y, point.y));
  const right = Math.round(Math.max(referenceDrag.start.x, point.x));
  const bottom = Math.round(Math.max(referenceDrag.start.y, point.y));
  const region = {
    x: left,
    y: top,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top),
  };
  if (referenceDrag.mode === "pet") petReferenceRegion = region;
  else referenceRegion = region;
  setApplyReferenceEnabled();
  drawReference();
});

referenceCanvas.addEventListener("pointerup", () => {
  const mode = referenceDrag?.mode;
  referenceDrag = null;
  if (mode === "name") scheduleFontRanking();
});

selectPetRegionButton.addEventListener("click", () => setReferenceSelectionMode("pet"));
selectNameRegionButton.addEventListener("click", () => setReferenceSelectionMode("name"));
applyReferenceLayoutButton.addEventListener("click", applyReferenceGeometry);
setReferenceSelectionMode(referenceSelectionMode);
setApplyReferenceEnabled();

async function requestFontRanking(options = {}) {
  clearTimeout(rankTimer);
  const fontReference = currentFontReference();
  if (!fontReference || !fontReference.text.trim()) {
    fontReferenceStatus.textContent =
      "Select a text region and enter the exact visible text.";
    return;
  }
  const requestedRevision = fontReferenceRevision;
  if (rankController) rankController.abort();
  const controller = new AbortController();
  rankController = controller;
  fontReferenceStatus.textContent = "Analyzing the confirmed lettering region…";
  try {
    const response = await fetch("/rank-fonts", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({font_reference: fontReference}),
      signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || response.statusText);
    if (requestedRevision !== fontReferenceRevision) return;
    fontRanking = result;
    rankedReferenceRevision = requestedRevision;
    const recommendation = result.recommendation;
    if (boot.autoFont && options.preserveSelection !== true) {
      selectFont(recommendation.font_id, recommendation.auto_select);
      if (!recommendation.auto_select) calibrateFontSize();
    }
    renderFontOptions();
    fontReferenceStatus.textContent =
      `Reference region analyzed at revision ${requestedRevision}.`;
    setSaveEnabled();
  } catch (error) {
    if (error.name !== "AbortError" && requestedRevision === fontReferenceRevision) {
      fontRanking = null;
      rankedReferenceRevision = -1;
      renderFontOptions();
      fontReferenceStatus.textContent = `Font analysis failed: ${error.message}`;
      setSaveEnabled();
    }
  } finally {
    if (rankController === controller) rankController = null;
  }
}

async function calibrateFontSize() {
  const fontReference = currentFontReference();
  if (!fontReference || !fontRanking || !selectedFontId) {
    fontReferenceStatus.textContent =
      "Analyze the confirmed reference lettering before matching its scale.";
    return;
  }
  const requestedStateRevision = stateRevision;
  const requestedReferenceRevision = fontReferenceRevision;
  const requestedFontId = selectedFontId;
  matchReferenceScaleButton.disabled = true;
  fontReferenceStatus.textContent = "Matching the reference lettering scale…";
  try {
    const response = await fetch("/calibrate-font-size", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        layout: layoutForRequest(),
        font_id: requestedFontId,
        font_reference: fontReference,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || response.statusText);
    if (
      requestedStateRevision !== stateRevision ||
      requestedReferenceRevision !== fontReferenceRevision ||
      requestedFontId !== selectedFontId
    ) return;
    state.name.font_size_px = result.font_size_px;
    state.name.min_font_size_px = result.min_font_size_px;
    syncInputs();
    markDirty();
    schedulePreview();
    fontReferenceStatus.textContent =
      `Applied fixed ${result.font_size_px}px nominal size from ` +
      `${Math.round(result.reference_horizontal_fill * 100)}% × ` +
      `${Math.round(result.reference_vertical_fill * 100)}% reference ink fill.`;
  } catch (error) {
    fontReferenceStatus.textContent = `Reference scale matching failed: ${error.message}`;
  } finally {
    matchReferenceScaleButton.disabled = !canCalibrateFontSize();
  }
}

function scheduleFontRanking() {
  clearTimeout(rankTimer);
  rankTimer = setTimeout(requestFontRanking, 300);
}

document.querySelector("#rank-fonts").addEventListener("click", () => requestFontRanking());
matchReferenceScaleButton.addEventListener("click", calibrateFontSize);
fontSearchButton.addEventListener("click", searchFonts);
fontImportButton.addEventListener("click", importOrSelectFont);
fontSearchQuery.addEventListener("keydown", event => {
  if (event.key === "Enter") {
    event.preventDefault();
    searchFonts();
  }
});

async function requestPreview() {
  clearTimeout(previewTimer);
  const requestedRevision = stateRevision;
  const requestedLayout = layoutForRequest();
  const requestedPetName = petNameInput.value;
  if (previewController) previewController.abort();
  const controller = new AbortController();
  previewController = controller;
  statusNode.textContent = `Rendering revision ${requestedRevision} with Pillow…`;
  try {
    const response = await fetch("/preview", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        revision: requestedRevision,
        layout: requestedLayout,
        pet_name: requestedPetName,
        preview_pet_id: selectedPreviewPetId,
        font_id: selectedFontId,
      }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error((await response.json()).error || response.statusText);
    const responseRevision = Number(response.headers.get("X-PawMarvel-Preview-Revision"));
    const appliedSize = response.headers.get("X-PawMarvel-Applied-Font-Size");
    const textFit = response.headers.get("X-PawMarvel-Text-Fit");
    const blob = await response.blob();
    if (responseRevision !== requestedRevision || requestedRevision !== stateRevision) return;
    const imageUrl = URL.createObjectURL(blob);
    await new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => {
        if (requestedRevision === stateRevision) {
          previewImage = image;
          renderedRevision = requestedRevision;
          canvas.classList.remove("stale");
          draw();
          metricsNode.textContent = nameEnabled
            ? `Applied font size: ${appliedSize}px (${textFit}).`
            : "Separate pet-name text layer disabled.";
          statusNode.textContent = `Preview revision ${requestedRevision} ready.`;
          setSaveEnabled();
        }
        URL.revokeObjectURL(imageUrl);
        resolve();
      };
      image.onerror = () => {
        URL.revokeObjectURL(imageUrl);
        reject(new Error("preview image could not be decoded"));
      };
      image.src = imageUrl;
    });
  } catch (error) {
    if (error.name !== "AbortError" && requestedRevision === stateRevision) {
      renderedRevision = -1;
      setSaveEnabled();
      statusNode.textContent = `Preview failed: ${error.message}`;
    }
  } finally {
    if (previewController === controller) previewController = null;
  }
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(requestPreview, 180);
}

async function closeEditor() {
  await fetch("/close", {method: "POST", keepalive: true});
  statusNode.textContent = "Layout saved. You may close this window.";
  window.close();
}

async function save(overwrite = false, closeAfter = false) {
  if (renderedRevision !== stateRevision) throw new Error("preview the current revision before saving");
  if (nameEnabled && boot.autoFont && !fontSelectionConfirmed) {
    throw new Error("select a ranked font before saving");
  }
  const revision = stateRevision;
  const payload = {
    revision,
    layout: layoutForRequest(),
    pet_name: petNameInput.value,
    preview_pet_id: selectedPreviewPetId,
    font_id: selectedFontId,
    font_reference: nameEnabled ? currentFontReference() : null,
    layout_reference: nameEnabled ? currentLayoutReference() : null,
    font_selection_confirmed: fontSelectionConfirmed,
    overwrite,
  };
  setEditorLocked(true);
  statusNode.textContent = `Saving previewed revision ${revision}…`;
  try {
    let allowOverwrite = overwrite;
    while (true) {
      payload.overwrite = allowOverwrite;
      const response = await fetch("/save", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (response.status === 409 && result.code === "overwrite_required" && !allowOverwrite && window.confirm(`${result.error}. Replace them?`)) {
        allowOverwrite = true;
        continue;
      }
      if (!response.ok) throw new Error(result.error || response.statusText);
      if (result.revision !== revision || result.layout_sha256 === undefined) {
        throw new Error("server saved an unexpected layout revision");
      }
      statusNode.textContent = `Saved revision ${revision}: ${result.layout}`;
      if (closeAfter) await closeEditor();
      return;
    }
  } finally {
    if (!closeAfter) setEditorLocked(false);
  }
}

document.querySelector("#refresh").addEventListener("click", () => {
  markDirty();
  requestPreview();
});
nameLayerToggle.addEventListener("change", () => {
  nameEnabled = nameLayerToggle.checked;
  if (!nameEnabled && referenceSelectionMode === "name") {
    setReferenceSelectionMode("pet");
  }
  syncNameLayerMode();
  markDirty();
  draw();
  schedulePreview();
});
document.querySelector("#save").addEventListener("click", () => save().catch(error => {
  statusNode.textContent = `Save failed: ${error.message}`;
}));
document.querySelector("#complete").addEventListener("click", () => save(false, true).catch(error => {
  setEditorLocked(false);
  statusNode.textContent = `Save failed: ${error.message}`;
}));
setInterval(() => fetch("/heartbeat", {method: "POST"}).catch(() => {}), 1500);
window.addEventListener("pagehide", () => {
  fetch("/close", {method: "POST", keepalive: true}).catch(() => {});
});

setSaveEnabled();
syncNameLayerMode();
requestPreview();
