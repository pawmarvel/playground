# Gemini Image — Generate art.png

This variant is tuned for Gemini image editing. Return one image response only;
do not include explanatory text. Treat all spatial and transparency constraints
below as literal output requirements.

INPUT: one finished personalized design example.

Generate `art.png`: the fixed reusable artwork layer for later local compositing of a new pet and pet name.

## Core Rule: Treat the Input as a Locked Coordinate Map

Preserve the original canvas aspect ratio and the **absolute normalized x/y position, size, spacing, and alignment of every fixed design element**.

Do NOT reflow, rebalance, recenter, compress, expand, or move remaining artwork after personalized elements are removed.

Removing personalization must create empty space where necessary.  
**Never close that empty space by moving fixed artwork.**

---

## Remove Only

Remove exactly:

1. the personalized pet portrait
2. the personalized pet name

Do not remove or move anything else.

---

## Preserve Exactly

Preserve all fixed artwork, including:

- fixed headline / slogan text
- fixed tagline text
- background motifs
- circles / rainbows / geometric shapes
- icons
- paw prints
- frames
- decorative elements
- textures and distress
- colors
- typography
- spacing
- relative and absolute positions

All fixed elements must remain at the same coordinates they occupy in the source design.

Target positional fidelity: within approximately 1–2% of the source canvas dimensions.

---

## Critical: No-Reflow Rule

If removing the personalized pet name creates an empty horizontal band, **keep that band empty**.

Do NOT move text below the name upward.

Example:

If the source composition is:

PET  
PET NAME  
FIXED TAGLINE

then `art.png` must remain:

background artwork  
EMPTY PET/NAME compositing area  
FIXED TAGLINE AT ITS ORIGINAL Y POSITION

The fixed tagline must remain exactly where it appeared in the source.

Do not change it to:

background artwork  
FIXED TAGLINE MOVED UP

This rule is mandatory.

---

## Critical: Restore Fixed Artwork Behind the Pet

Remove the personalized pet but reconstruct any fixed artwork that logically continues behind it.

If the pet overlaps:

- a rainbow
- circle
- pattern
- landscape
- geometric motif
- texture
- frame
- background graphic

restore that artwork seamlessly across the pet's former location.

There must be:

- no pet-shaped hole
- no white patch
- no transparent pet cutout
- no placeholder silhouette
- no empty window inside fixed background artwork

The future pet will be composited **on top of art.png**.

---

## Pet-Name Removal

Remove the customer-specific pet name only.

Do not replace it with:

- `[PET NAME]`
- placeholder text
- brackets
- lines
- boxes
- colored bars
- labels

If no fixed artwork exists in the pet-name region, leave that region transparent.

Most importantly, preserve the exact vertical position of any fixed text underneath it.

---

## Transparency

Output a true-alpha PNG.

Everything outside actual fixed printed artwork must be transparent.

Do NOT render:

- white background
- black background
- garment color
- fabric
- checkerboard
- mockup
- wall
- paper
- poster background

Transparency must be actual alpha.

---

## Generalization

This prompt will be used for many different personalized pet designs.

Infer:

### Variable personalization
- customer pet portrait
- customer pet name

### Fixed design
Everything else.

Remove the minimum necessary content.

Do not assume a specific rainbow, slogan, color palette, style, or layout.

---

## Final Output

Return one registered artwork layer:

`art.png`

Requirements:

- same canvas/aspect ratio as source
- fixed elements at original coordinates
- pet removed
- pet name removed
- background art behind pet reconstructed
- empty name band retained
- fixed elements below the name do NOT move
- no placeholders
- no guide boxes
- true transparent background
