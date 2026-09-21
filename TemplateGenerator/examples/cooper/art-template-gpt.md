# GPT-Image-2 — Generate `art.png`

## INPUT

You will receive one or more finished personalized design references:

- IMAGE A is the primary reference. It is authoritative for composition,
  normalized element positions, crop, palette, fixed content, and visual
  hierarchy.
- IMAGE B, IMAGE C, and later images are optional supporting references. Use
  them only to clarify rendering style, texture, distress, and details that are
  ambiguous in IMAGE A. They must not override IMAGE A's geometry, palette,
  fixed content, or personalized-element locations.

If references disagree, IMAGE A always wins. Do not average layouts or combine
animals, names, backgrounds, or design variants across references.

Generate:

**`art.png`**

This file will be used as the fixed reusable artwork layer for later local compositing of:

1. a replacement pet
2. a replacement pet name

---

## Core Rule: Treat IMAGE A as a Locked Normalized Coordinate Map

Treat IMAGE A as a **locked master composition**, not as inspiration.

The requested API output dimensions are authoritative and may have a different
aspect ratio from IMAGE A because IMAGE A may be a web screenshot rather than a
production canvas. Fill the entire requested output canvas and map IMAGE A's
**normalized x/y position, size, spacing, and alignment of every fixed design
element** into that canvas.

Do not preserve IMAGE A's pixel dimensions, add letterboxing, crop fixed
artwork, or stretch individual elements to imitate its aspect ratio. When the
ratios differ, adapt surrounding negative space while keeping the normalized
composition visually faithful. The result must use the exact requested output
dimensions.

Do NOT:

- reflow
- rebalance
- reinterpret
- recenter
- compress
- expand
- scale the remaining composition
- move fixed artwork after personalization is removed

Removing personalization must create empty space where necessary.

**Never close that empty space by moving fixed artwork.**

---

## Remove Only

Remove exactly:

1. the personalized pet portrait
2. the personalized pet name
3. any pet-specific shadow, contact shadow, grounding shadow, glow, halo, or support/base effect visually tied to that pet

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

All fixed elements must remain at the same normalized coordinates they occupy
in IMAGE A, mapped into the requested output canvas.

Target positional fidelity: within approximately **1–2% of the requested canvas
dimensions** after normalized mapping from IMAGE A.

Do **not** classify pet-specific shadows, contact shadows, grounding effects, or pet-only base shapes as fixed artwork unless they are unmistakably part of the permanent design independent of the pet.

---

## Critical: No-Reflow Rule

If removing the personalized pet name creates an empty horizontal band, **keep that band empty**.

Do NOT move text below the name upward.

Example:

If the primary composition is:

PET
PET NAME
FIXED TAGLINE

then `art.png` must remain:

background artwork
EMPTY PET/NAME COMPOSITING AREA
FIXED TAGLINE AT ITS ORIGINAL Y POSITION

The fixed tagline must remain exactly where it appeared in the source.

Do not change it to:

background artwork
FIXED TAGLINE MOVED UP

This rule is mandatory.

---

## Critical: Restore Fixed Artwork Behind the Pet

Remove the personalized pet but reconstruct any **independent fixed artwork** that logically continues behind it.

If the pet overlaps:

- a rainbow
- circle
- pattern
- landscape
- geometric motif
- texture
- frame
- background graphic

restore that fixed artwork seamlessly across the pet’s former location.

There must be:

- no pet-shaped hole
- no white patch
- no transparent pet cutout inside fixed printed artwork
- no placeholder silhouette
- no empty window inside fixed background artwork

The future pet will be composited **on top of `art.png`**.

Restore only independent fixed background/design elements.

Do **not** restore or preserve:

- pet shadows
- contact shadows
- floor shading caused by the pet
- pet-specific glow or halo
- isolated oval shadow shapes
- pet-only base/platform elements

---

## Critical: Pet-Specific Shadows and Grounding Effects Are Not Fixed Artwork

Treat any visual effect that exists primarily to ground, support, or visually anchor the personalized pet as part of the **personalized pet region**, not part of the reusable fixed template.

This includes, when present:

- oval shadow under the pet
- soft ground shadow
- cast shadow from the pet body
- contact shadow under paws, chest, or body
- pet-specific glow, halo, or highlight
- pet-specific platform, floor patch, or base shape that only serves the pet

If such an element is visually tied to the pet rather than clearly part of the overall fixed design language, **remove it together with the pet**.

Do **not** preserve, recreate, or infer a standalone shadow shape after the pet is removed.

If the region beneath the pet contains no independent fixed artwork, leave that area transparent.

If fixed artwork genuinely passes behind the pet, reconstruct **only** that fixed artwork — **not** the removed pet’s own shadow or grounding effect.

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

Output a **true-alpha PNG**.

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
- any pet-specific shadow, grounding, contact shadow, glow, halo, or support/base effect

### Fixed design
Everything else.

Remove the minimum necessary personalized content.

Do not assume a specific rainbow, slogan, color palette, style, or layout.

Judge each input by separating:

1. **personalized elements** tied to the individual pet and customer name
2. **fixed reusable design elements** that belong to the template

When uncertain, prefer preserving true fixed design elements while removing pet-specific grounding effects.

---

## Final Output

Return one registered artwork layer:

**`art.png`**

### Requirements

- exact API-requested canvas dimensions, even when their aspect ratio differs
  from IMAGE A
- IMAGE A's normalized composition mapped faithfully into the requested canvas
- fixed elements at original coordinates
- pet removed
- pet-specific shadow / grounding effects removed
- pet name removed
- background art behind pet reconstructed only where it is genuine fixed artwork
- empty name band retained where appropriate
- fixed elements below the name do **not** move
- no placeholders
- no guide boxes
- no pet-shaped cutout
- no standalone oval shadow under the former pet area unless it is unmistakably fixed design independent of the pet
- true transparent background
