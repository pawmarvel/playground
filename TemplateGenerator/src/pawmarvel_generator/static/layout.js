"use strict";

const boot = window.PAWMARVEL_BOOTSTRAP;
const state = structuredClone(boot.layout);
const canvas = document.querySelector("#preview");
const context = canvas.getContext("2d");
const statusNode = document.querySelector("#status");
const reference = document.querySelector("#reference");
reference.src = boot.referenceDataUrl;
document.querySelector("#name-mode").textContent =
  "Preview source: configured font. Production chooses the largest fitting size and ink-centers vertically.";
canvas.width = boot.canvas.width;
canvas.height = boot.canvas.height;

let previewImage = null;
let previewTimer = null;
let drag = null;
let selectedFontId = boot.selectedFontId;

function selectFont(candidateId) {
  const candidate = boot.fontCandidates.find(value => value.id === candidateId);
  if (!candidate) return;
  selectedFontId = candidate.id;
  state.name.font = candidate.relativeName;
  schedulePreview();
}

function buildFontCatalog() {
  const host = document.querySelector("#font-catalog");
  const description = document.createElement("p");
  description.className = "font-help";
  const recommended = boot.fontCandidates.find(value => value.recommended);
  description.textContent = `Recommended from reference: ${recommended.label} (${Math.round(recommended.confidence * 100)}% visual confidence). Choose one of the five highest-ranked eligible OFL fonts.`;
  host.append(description);
  const style = document.createElement("style");
  for (const candidate of boot.fontCandidates) {
    style.textContent += `@font-face { font-family: "${candidate.id}"; src: url("/fonts/${candidate.id}") format("truetype"); }\n`;
  }
  document.head.append(style);

  const ranked = [...boot.fontCandidates]
    .sort((left, right) => left.rank - right.rank)
    .filter(candidate => candidate.rank <= 5 || candidate.id === selectedFontId);
  const label = document.createElement("label");
  label.className = "font-select-label";
  label.textContent = "Top font recommendations";
  const select = document.createElement("select");
  select.id = "font-recommendations";
  select.setAttribute("aria-label", "Top five font recommendations");
  for (const candidate of ranked) {
    const option = document.createElement("option");
    option.value = candidate.id;
    const suffix = candidate.recommended ? " — recommended" : "";
    option.textContent = `#${candidate.rank} ${candidate.label} — ${Math.round(candidate.confidence * 100)}% confidence${suffix}`;
    option.selected = candidate.id === selectedFontId;
    select.append(option);
  }
  const specimen = document.createElement("div");
  specimen.className = "font-specimen";
  specimen.textContent = boot.petName;
  function updateSelection() {
    const candidate = ranked.find(value => value.id === select.value);
    if (!candidate) return;
    specimen.style.fontFamily = `"${candidate.id}"`;
    selectFont(candidate.id);
  }
  select.addEventListener("change", updateSelection);
  label.append(select);
  host.append(label, specimen);
  updateSelection();
}

buildFontCatalog();

const numericFields = [
  ["pet", "x", "Pet x"], ["pet", "y", "Pet y"],
  ["pet", "width", "Pet width"], ["pet", "height", "Pet height"],
  ["pet", "rotation_degrees", "Pet rotation"],
  ["name", "x", "Name x"], ["name", "y", "Name y"],
  ["name", "width", "Name width"], ["name", "height", "Name height"],
  ["name", "font_size_px", "Font size"],
  ["name", "min_font_size_px", "Minimum font size"],
];

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
  if (section === "name" && ["font_size_px", "min_font_size_px"].includes(key)) {
    input.disabled = true;
    input.title = "Legacy schema hint; ignored by the production renderer";
  }
  input.addEventListener("input", () => {
    const number = Number(input.value);
    if (Number.isFinite(number)) {
      setValue(section, key, key === "rotation_degrees" ? number : Math.round(number));
      draw();
      schedulePreview();
    }
  });
  label.append(input);
  host.append(label);
}

for (const field of numericFields) addNumberControl(...field);

function addSelect(key, labelText, values, disabled = false) {
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
  select.addEventListener("change", () => { state.name[key] = select.value; schedulePreview(); });
  select.disabled = disabled;
  if (disabled) select.title = "Legacy schema hint; production ink-centers vertically";
  label.append(select);
  host.append(label);
}

addSelect("horizontal_align", "Horizontal alignment", ["left", "center", "right"]);
addSelect("vertical_align", "Vertical alignment", ["top", "middle", "bottom"], true);

const colorLabel = document.createElement("label");
colorLabel.textContent = "Text color";
const colorInput = document.createElement("input");
colorInput.type = "color";
colorInput.value = state.name.color.slice(0, 7);
colorInput.addEventListener("input", () => { state.name.color = `${colorInput.value}FF`; schedulePreview(); });
colorLabel.append(colorInput);
document.querySelector("#name-controls").append(colorLabel);

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
  drawBox(state.name.box, "#40c0ff");
}

function canvasPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) * canvas.width / rect.width,
    y: (event.clientY - rect.top) * canvas.height / rect.height,
  };
}

function hit(box, point) {
  const handle = Math.max(12, canvas.width / 40);
  if (Math.abs(point.x - (box.x + box.width)) <= handle && Math.abs(point.y - (box.y + box.height)) <= handle) return "resize";
  if (point.x >= box.x && point.x <= box.x + box.width && point.y >= box.y && point.y <= box.y + box.height) return "move";
  return null;
}

canvas.addEventListener("pointerdown", event => {
  const point = canvasPoint(event);
  for (const section of ["name", "pet"]) {
    const mode = hit(state[section].box, point);
    if (mode) {
      drag = { section, mode, start: point, original: structuredClone(state[section].box) };
      canvas.setPointerCapture(event.pointerId);
      return;
    }
  }
});

canvas.addEventListener("pointermove", event => {
  if (!drag) return;
  const point = canvasPoint(event);
  const dx = Math.round(point.x - drag.start.x);
  const dy = Math.round(point.y - drag.start.y);
  const box = state[drag.section].box;
  if (drag.mode === "move") {
    box.x = drag.original.x + dx;
    box.y = drag.original.y + dy;
  } else {
    box.width = Math.max(1, drag.original.width + dx);
    box.height = Math.max(1, drag.original.height + dy);
  }
  syncInputs();
  draw();
});

canvas.addEventListener("pointerup", () => { if (drag) schedulePreview(); drag = null; });

async function requestPreview() {
  statusNode.textContent = "Rendering with Pillow…";
  try {
    const response = await fetch("/preview", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({layout: state, font_id: selectedFontId}),
    });
    if (!response.ok) throw new Error((await response.json()).error || response.statusText);
    const image = new Image();
    image.onload = () => { previewImage = image; draw(); URL.revokeObjectURL(image.src); };
    image.src = URL.createObjectURL(await response.blob());
    statusNode.textContent = "Preview ready.";
  } catch (error) {
    statusNode.textContent = `Preview failed: ${error.message}`;
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
  statusNode.textContent = "Saving…";
  const response = await fetch("/save", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({layout: state, font_id: selectedFontId, overwrite}),
  });
  const result = await response.json();
  if (response.status === 409 && !overwrite && window.confirm(`${result.error}. Replace them?`)) return save(true, closeAfter);
  if (!response.ok) throw new Error(result.error || response.statusText);
  statusNode.textContent = `Saved ${result.layout} and ${result.calibration}`;
  if (closeAfter) await closeEditor();
}

document.querySelector("#refresh").addEventListener("click", requestPreview);
document.querySelector("#save").addEventListener("click", () => save().catch(error => { statusNode.textContent = `Save failed: ${error.message}`; }));
document.querySelector("#complete").addEventListener("click", () => save(false, true).catch(error => { statusNode.textContent = `Save failed: ${error.message}`; }));
setInterval(() => fetch("/heartbeat", {method: "POST"}).catch(() => {}), 1500);
window.addEventListener("pagehide", () => {
  fetch("/close", {method: "POST", keepalive: true}).catch(() => {});
});
requestPreview();
