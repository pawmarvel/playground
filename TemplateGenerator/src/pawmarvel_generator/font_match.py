"""Rank eligible OFL fonts against lettering visible in a reference screenshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from .font_catalog import FontCandidate
from .font_reference import FontReference


@dataclass(frozen=True)
class FontMatch:
    candidate: FontCandidate
    score: float
    confidence: float
    confidence_level: str


@dataclass(frozen=True)
class FontScaleRecommendation:
    font_size_px: int
    horizontal_fill: float
    vertical_fill: float

    def to_dict(self) -> dict[str, int | float]:
        return {
            "font_size_px": self.font_size_px,
            "reference_horizontal_fill": round(self.horizontal_fill, 4),
            "reference_vertical_fill": round(self.vertical_fill, 4),
        }


def _ink_mask(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image).filter(ImageFilter.GaussianBlur(0.8))
    # Lettering normally differs from the dominant surrounding artwork. Compare
    # both polarities and retain the less dominant foreground.
    mean = ImageStat.Stat(gray).mean[0]
    dark = gray.point(lambda value: 255 if value < mean - 18 else 0)
    light = gray.point(lambda value: 255 if value > mean + 18 else 0)
    foreground = (
        dark
        if ImageStat.Stat(dark).mean[0] < ImageStat.Stat(light).mean[0]
        else light
    )
    # Web references often contain fabric grain and deliberately distressed
    # lettering. Remove isolated specks and close small holes so ranking compares
    # the underlying glyph silhouette rather than the decorative texture.
    return (
        foreground.filter(ImageFilter.MedianFilter(3))
        .filter(ImageFilter.MaxFilter(3))
        .filter(ImageFilter.MinFilter(3))
    )


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


def _binary(mask: Image.Image) -> Image.Image:
    return mask.point(lambda value: 255 if value >= 128 else 0)


def _ink_pixels(mask: Image.Image) -> float:
    histogram = mask.histogram()
    return sum(index * count for index, count in enumerate(histogram)) / 255


def _overlap(left: Image.Image, right: Image.Image) -> float:
    return _ink_pixels(ImageChops.multiply(left, right))


def recommend_font_size(
    reference: Path,
    font_reference: FontReference,
    font: Path,
    *,
    box_width: int,
    box_height: int,
    padding: int,
) -> FontScaleRecommendation:
    """Return one fixed nominal size that preserves the reference ink fill.

    This is an authoring calibration, not a per-customer auto-fit rule. Runtime
    still starts every name at the saved nominal size and only shrinks names
    that do not fit.
    """
    with Image.open(reference) as source:
        region = font_reference.region
        crop = source.convert("RGB").crop(
            (
                region.x,
                region.y,
                region.x + region.width,
                region.y + region.height,
            )
        )
    mask = _binary(_ink_mask(crop))
    bounds = mask.getbbox()
    if bounds is None:
        raise ValueError("confirmed reference text region contains no visible lettering")
    ink_width = bounds[2] - bounds[0]
    ink_height = bounds[3] - bounds[1]
    horizontal_fill = ink_width / crop.width
    vertical_fill = ink_height / crop.height
    available_width = box_width - 2 * padding
    available_height = box_height - 2 * padding
    if available_width <= 0 or available_height <= 0:
        raise ValueError("name padding leaves no usable name-box area")
    target_width = max(1, min(available_width, round(box_width * horizontal_fill)))
    target_height = max(1, min(available_height, round(box_height * vertical_fill)))

    def fits(size: int) -> bool:
        rendered = ImageFont.truetype(str(font), size=size)
        left, top, right, bottom = rendered.getbbox(font_reference.text)
        return right - left <= target_width and bottom - top <= target_height

    low = 1
    high = max(box_width, box_height) * 2
    best = 1
    while low <= high:
        middle = (low + high) // 2
        if fits(middle):
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return FontScaleRecommendation(
        font_size_px=best,
        horizontal_fill=horizontal_fill,
        vertical_fill=vertical_fill,
    )


def recommend_min_font_size_for_capacity(
    font: Path,
    *,
    box_width: int,
    box_height: int,
    padding: int,
    character_count: int = 12,
) -> int:
    """Return the largest lower-bound size that fits a conservative name.

    The probe uses the widest repeated glyph from the common printable name
    alphabet. This makes the default useful for names up to ``character_count``
    characters without coupling production rendering to one QA pet name.
    Operators may still override the resulting minimum in the layout editor.
    """
    if character_count < 1:
        raise ValueError("minimum-font capacity character count must be positive")
    available_width = box_width - 2 * padding
    available_height = box_height - 2 * padding
    if available_width <= 0 or available_height <= 0:
        raise ValueError("name padding leaves no usable name-box area")
    glyphs = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 '-"

    def fits(size: int) -> bool:
        rendered = ImageFont.truetype(str(font), size=size)
        for glyph in glyphs:
            left, top, right, bottom = rendered.getbbox(glyph * character_count)
            if right - left > available_width or bottom - top > available_height:
                return False
        return True

    low = 1
    high = max(box_width, box_height) * 2
    best = 1
    while low <= high:
        middle = (low + high) // 2
        if fits(middle):
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def rank_fonts(
    reference: Path,
    font_reference: FontReference,
    candidates: tuple[FontCandidate, ...],
) -> tuple[FontMatch, ...]:
    """Rank fonts against one confirmed reference crop containing the same text."""
    with Image.open(reference) as source:
        ref = source.convert("RGB")
    region = font_reference.region
    target_size = (320, 96)
    crop = ref.crop(
        (
            region.x,
            region.y,
            region.x + region.width,
            region.y + region.height,
        )
    )
    target = _normalized_mask(
        _ink_mask(crop.resize(target_size, Image.Resampling.LANCZOS)),
        target_size,
    )
    if target.getbbox() is None:
        return tuple(
            FontMatch(candidate, 0.0, 0.0, "low") for candidate in candidates
        )
    target = _binary(target)
    target_ink = max(1.0, _ink_pixels(target))
    target_dilated = target.filter(ImageFilter.MaxFilter(7))
    target_bounds = target.getbbox()
    assert target_bounds is not None
    target_aspect = (target_bounds[2] - target_bounds[0]) / max(
        1, target_bounds[3] - target_bounds[1]
    )
    scored: list[tuple[FontCandidate, float]] = []
    for candidate in candidates:
        rendered = _render_mask(font_reference.text, candidate.font, target_size)
        rendered = _binary(rendered)
        rendered_ink = max(1.0, _ink_pixels(rendered))
        rendered_dilated = rendered.filter(ImageFilter.MaxFilter(7))
        rendered_bounds = rendered.getbbox()
        assert rendered_bounds is not None
        rendered_aspect = (rendered_bounds[2] - rendered_bounds[0]) / max(
            1, rendered_bounds[3] - rendered_bounds[1]
        )
        intersection = _overlap(target, rendered)
        dice = 2 * intersection / (target_ink + rendered_ink)
        target_coverage = _overlap(target, rendered_dilated) / target_ink
        rendered_coverage = _overlap(rendered, target_dilated) / rendered_ink
        tolerant_overlap = (target_coverage + rendered_coverage) / 2
        density_similarity = 1.0 - min(
            1.0, abs(target_ink - rendered_ink) / max(target_ink, rendered_ink)
        )
        aspect_similarity = 1.0 - min(
            1.0,
            abs(target_aspect - rendered_aspect) / max(target_aspect, 0.01),
        )
        score = (
            0.50 * tolerant_overlap
            + 0.25 * dice
            + 0.15 * density_similarity
            + 0.10 * aspect_similarity
        )
        scored.append((candidate, score))
    scored.sort(key=lambda item: (-item[1], item[0].label.lower()))
    top_score = scored[0][1]
    runner_up_score = scored[1][1] if len(scored) > 1 else 0.0
    top_separation_strength = max(
        0.0, min(1.0, (top_score - runner_up_score) / 0.05)
    )
    # Confidence is deliberately conservative and is not a probability. The
    # absolute match must be strong, and an ambiguous first/second result
    # reduces every candidate's evidence score. This also keeps confidence
    # monotonic with similarity instead of making a lower-ranked candidate look
    # more certain merely because it happens to be far from the next one.
    ranking_certainty = 0.35 + 0.65 * top_separation_strength
    matches: list[FontMatch] = []
    for candidate, score in scored:
        absolute_strength = max(0.0, min(1.0, (score - 0.55) / 0.35))
        confidence = absolute_strength * ranking_certainty
        if confidence >= 0.67:
            level = "high"
        elif confidence >= 0.35:
            level = "medium"
        else:
            level = "low"
        matches.append(
            FontMatch(
                candidate=candidate,
                score=round(score, 4),
                confidence=round(confidence, 4),
                confidence_level=level,
            )
        )
    return tuple(matches)
