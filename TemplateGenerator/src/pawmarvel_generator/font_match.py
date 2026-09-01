"""Rank approved fonts against lettering visible in a reference screenshot."""

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


def _render_mask(text: str, font: Path, size: tuple[int, int]) -> Image.Image:
    width, height = size
    probe = ImageFont.truetype(str(font), max(12, height * 2))
    box = probe.getbbox(text, stroke_width=0)
    ink_width, ink_height = max(1, box[2] - box[0]), max(1, box[3] - box[1])
    scale = min(width * 0.92 / ink_width, height * 0.82 / ink_height)
    fitted = ImageFont.truetype(str(font), max(8, round(probe.size * scale)))
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    final = draw.textbbox((0, 0), text, font=fitted)
    x = (width - (final[2] - final[0])) // 2 - final[0]
    y = (height - (final[3] - final[1])) // 2 - final[1]
    draw.text((x, y), text, font=fitted, fill=255)
    return mask


def rank_fonts(
    reference: Path,
    name_box: dict[str, int],
    canvas_size: tuple[int, int],
    pet_name: str,
    candidates: tuple[FontCandidate, ...],
) -> tuple[FontMatch, ...]:
    """Return best-first visual matches using the normalized layout name region.

    This lightweight MVP matcher deliberately uses no network model. It compares
    normalized silhouettes, density, and edge structure and reports conservative
    confidence so the editor can keep a human override available.
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
        target = _ink_mask(crop.resize(target_size, Image.Resampling.LANCZOS))
        targets.append(
            (
                target,
                target.filter(ImageFilter.FIND_EDGES),
                ImageStat.Stat(target).mean[0] / 255,
            )
        )
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
    margin = scored[0][1] - scored[1][1] if len(scored) > 1 else scored[0][1]
    confidence = max(0.0, min(0.99, 0.45 + margin * 4))
    return tuple(FontMatch(candidate, round(score, 4), round(confidence if index == 0 else 0.0, 4)) for index, (candidate, score) in enumerate(scored))
