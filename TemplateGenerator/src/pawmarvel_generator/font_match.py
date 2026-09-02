"""Rank eligible OFL fonts against lettering visible in a reference screenshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from .font_catalog import FontCandidate


@dataclass(frozen=True)
class FontMatch:
    candidate: FontCandidate
    score: float
    confidence: float


def _ink_mask(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    # Lettering normally differs from the dominant surrounding artwork. Compare
    # both polarities and retain the less dominant foreground.
    mean = ImageStat.Stat(gray).mean[0]
    dark = gray.point(lambda value: 255 if value < mean - 18 else 0)
    light = gray.point(lambda value: 255 if value > mean + 18 else 0)
    return dark if ImageStat.Stat(dark).mean[0] < ImageStat.Stat(light).mean[0] else light


def _normalized_mask(mask: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Trim and consistently fit ink so crop offsets do not dominate matching."""
    output = Image.new("L", size, 0)
    bounds = mask.getbbox()
    if bounds is None:
        return output
    ink = mask.crop(bounds)
    scale = min((size[0] - 12) / ink.width, (size[1] - 10) / ink.height)
    fitted_size = (
        max(1, round(ink.width * scale)),
        max(1, round(ink.height * scale)),
    )
    ink = ink.resize(fitted_size, Image.Resampling.LANCZOS)
    output.paste(
        ink,
        ((size[0] - fitted_size[0]) // 2, (size[1] - fitted_size[1]) // 2),
    )
    return output


def _render_mask(text: str, font: Path, size: tuple[int, int]) -> Image.Image:
    width, height = size
    probe = ImageFont.truetype(str(font), max(12, height * 2))
    box = probe.getbbox(text, stroke_width=0)
    ink_width, ink_height = max(1, box[2] - box[0]), max(1, box[3] - box[1])
    scale = min(width * 0.92 / ink_width, height * 0.82 / ink_height)
    fitted = ImageFont.truetype(str(font), max(8, round(probe.size * scale)))
    mask = Image.new("L", (width * 2, height * 2), 0)
    draw = ImageDraw.Draw(mask)
    final = draw.textbbox((0, 0), text, font=fitted)
    draw.text((4 - final[0], 4 - final[1]), text, font=fitted, fill=255)
    return _normalized_mask(mask, size)


def rank_fonts(
    reference: Path,
    name_box: dict[str, int],
    canvas_size: tuple[int, int],
    pet_name: str,
    candidates: tuple[FontCandidate, ...],
) -> tuple[FontMatch, ...]:
    """Return best-first visual matches using the normalized layout name region.

    This lightweight MVP matcher deliberately uses no network model. It compares
    normalized silhouettes, density, and edge structure. Confidence is the
    bounded visual-similarity score, so the editor keeps a human override.
    """
    with Image.open(reference) as source:
        ref = source.convert("RGB")
    cw, ch = canvas_size
    x0 = round(name_box["x"] * ref.width / cw)
    y0 = round(name_box["y"] * ref.height / ch)
    x1 = round((name_box["x"] + name_box["width"]) * ref.width / cw)
    y1 = round((name_box["y"] + name_box["height"]) * ref.height / ch)
    target_size = (320, 96)
    # An existing layout can be only approximately aligned with a web
    # screenshot. Search nearby vertical slots rather than treating its current
    # name box as exact reference geometry.
    slot_height = max(1, y1 - y0)
    targets: list[tuple[Image.Image, Image.Image, float]] = []
    for offset in (-2, -1, 0, 1, 2):
        top = max(0, y0 + offset * slot_height)
        bottom = min(ref.height, y1 + offset * slot_height)
        if bottom <= top:
            continue
        crop = ref.crop((max(0, x0), top, min(ref.width, x1), bottom))
        target = _normalized_mask(
            _ink_mask(crop.resize(target_size, Image.Resampling.LANCZOS)),
            target_size,
        )
        if target.getbbox() is None:
            continue
        targets.append(
            (
                target,
                target.filter(ImageFilter.FIND_EDGES),
                ImageStat.Stat(target).mean[0] / 255,
            )
        )
    if not targets:
        return tuple(FontMatch(candidate, 0.0, 0.0) for candidate in candidates)
    scored: list[tuple[FontCandidate, float]] = []
    for candidate in candidates:
        rendered = _render_mask(pet_name, candidate.font, target_size)
        rendered_edges = rendered.filter(ImageFilter.FIND_EDGES)
        density = ImageStat.Stat(rendered).mean[0] / 255
        candidate_scores: list[float] = []
        for target, target_edges, target_density in targets:
            pixel_error = ImageStat.Stat(ImageChops.difference(target, rendered)).mean[0] / 255
            edge_error = ImageStat.Stat(ImageChops.difference(target_edges, rendered_edges)).mean[0] / 255
            density_error = min(1.0, abs(target_density - density) * 4)
            candidate_scores.append(1.0 - (0.55 * pixel_error + 0.3 * edge_error + 0.15 * density_error))
        scored.append((candidate, max(candidate_scores)))
    scored.sort(key=lambda item: (-item[1], item[0].label.lower()))
    return tuple(
        FontMatch(
            candidate,
            round(score, 4),
            round(max(0.0, min(0.99, score)), 4),
        )
        for candidate, score in scored
    )
