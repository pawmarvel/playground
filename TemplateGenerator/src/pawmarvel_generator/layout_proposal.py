"""Generate deterministic, locally ranked layout candidates for operator review."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image, ImageChops, ImageDraw

from .artifact_io import atomic_json, read_json, sha256, utc_now
from .config import Layout, Rect, parse_layout
from .font_catalog import FontCandidate, FontCatalogError, discover_font_catalog
from .font_match import (
    rank_fonts,
    recommend_font_size,
    recommend_min_font_size_for_capacity,
)
from .font_reference import FontReferenceError, load_font_reference
from .font_license import resolve_ofl_license
from .layout_reference import LayoutReferenceError, load_layout_reference, map_reference_box
from .personalization import DEFAULT_MAX_NAME_CODE_POINTS
from .renderer import (
    RenderError,
    prepare_pet_placement,
    prepare_text_placement,
    render_to_files,
)


ALPHA_THRESHOLD = 8
MAX_SEARCH_CANDIDATES = 60
MAX_FINALISTS = 3
DEFAULT_LAYOUT_TEXT_NAMES = ("PET", "CHARLIE", "MARSHMALLOW")
NAME_MODES = {"auto", "layout-text", "embedded-in-pet", "none"}
_ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")


class LayoutProposalError(ValueError):
    """A deterministic layout proposal cannot be created safely."""


@dataclass(frozen=True)
class PetEvidence:
    attempt_id: str
    path: Path
    sha256: str
    image: Image.Image


@dataclass(frozen=True)
class Candidate:
    pet_box: Rect
    name_box: Rect | None
    font: FontCandidate | None
    font_size_px: int | None
    min_font_size_px: int | None
    padding_px: int | None
    score: float
    metrics: dict[str, Any]
    sort_key: tuple[Any, ...]


def _json(path: Path, label: str) -> dict[str, Any]:
    return read_json(
        path,
        label=label,
        error_type=LayoutProposalError,
        require_object=True,
        correction="fix or regenerate the referenced authoring artifact.",
    )


def _id(value: str, label: str) -> str:
    if len(value) > 96 or _ID_PATTERN.fullmatch(value) is None:
        raise LayoutProposalError(
            f"{label} must use 1-96 lowercase letters, numbers, and internal hyphens: {value!r}"
        )
    return value


def _input_path(experiment: Path, descriptor: Any, label: str) -> Path:
    if not isinstance(descriptor, Mapping) or not isinstance(descriptor.get("path"), str):
        raise LayoutProposalError(f"layout experiment {label} descriptor is invalid")
    path = (experiment / descriptor["path"]).resolve()
    try:
        path.relative_to(experiment)
    except ValueError as exc:
        raise LayoutProposalError(
            f"layout experiment {label} escapes the experiment root: {path}"
        ) from exc
    if not path.exists():
        raise LayoutProposalError(f"layout experiment {label} is missing: {path}")
    expected_hash = descriptor.get("sha256")
    if path.is_file() and isinstance(expected_hash, str) and sha256(path) != expected_hash:
        raise LayoutProposalError(f"layout experiment {label} hash mismatch: {path}")
    return path


def _product_path(product: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise LayoutProposalError(f"{label} path is missing")
    path = (product / relative).resolve()
    try:
        path.relative_to(product)
    except ValueError as exc:
        raise LayoutProposalError(f"{label} escapes the authoring product: {path}") from exc
    return path


def _open_rgba(path: Path, label: str) -> Image.Image:
    try:
        with Image.open(path) as image:
            image.load()
            return image.convert("RGBA")
    except OSError as exc:
        raise LayoutProposalError(f"{label} is not a readable image: {path}") from exc


def _alpha_mask(image: Image.Image) -> Image.Image:
    return image.getchannel("A").point(
        lambda value: 255 if value > ALPHA_THRESHOLD else 0
    )


def _ink_mass(mask: Image.Image) -> float:
    histogram = mask.histogram()
    return sum(index * count for index, count in enumerate(histogram)) / 255.0


def _pet_evidence(
    experiment: Path,
    meta: Mapping[str, Any],
    *,
    attempt_prefix: str,
) -> tuple[list[PetEvidence], dict[str, Any]]:
    product = experiment.parents[2]
    inputs = meta.get("inputs")
    if not isinstance(inputs, Mapping):
        raise LayoutProposalError("layout experiment inputs are invalid")
    pet_attempt_descriptor = inputs.get("pet_attempt")
    if not isinstance(pet_attempt_descriptor, Mapping):
        raise LayoutProposalError("layout experiment does not pin a pet attempt")
    pinned_attempt = _product_path(
        product, pet_attempt_descriptor.get("path"), "pinned pet attempt"
    )
    pinned_run_path = pinned_attempt / "run.json"
    expected_pinned_hash = pet_attempt_descriptor.get("sha256")
    if not pinned_run_path.is_file():
        raise LayoutProposalError(f"pinned pet attempt is missing: {pinned_run_path}")
    if (
        isinstance(expected_pinned_hash, str)
        and sha256(pinned_run_path) != expected_pinned_hash
    ):
        raise LayoutProposalError(
            f"pinned pet attempt hash mismatch: {pinned_run_path}"
        )
    pet_experiment = pinned_attempt.parent.parent
    pet_meta = _json(pet_experiment / "experiment.json", "pet experiment")
    if pet_meta.get("kind") != "pet":
        raise LayoutProposalError(f"pinned pet experiment is not kind=pet: {pet_experiment}")

    attempts = sorted(
        pet_experiment.glob(f"attempts/{attempt_prefix}*/run.json"),
        key=lambda path: (
            path.parent.resolve() != pinned_attempt,
            path.parent.name,
        ),
    )
    if not attempts:
        raise LayoutProposalError(
            f"no pet attempts match prefix {attempt_prefix!r}: "
            f"{pet_experiment / 'attempts'}"
        )
    evidence: list[PetEvidence] = []
    failures: list[str] = []
    seen_hashes: set[str] = set()
    for run_path in attempts:
        run = _json(run_path, "pet attempt")
        if run.get("status") != "succeeded":
            failures.append(run_path.parent.name)
            continue
        output = run_path.parent / "outputs" / "transformed-pet.png"
        if not output.is_file():
            failures.append(run_path.parent.name)
            continue
        digest = sha256(output)
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        evidence.append(
            PetEvidence(
                attempt_id=run_path.parent.name,
                path=output,
                sha256=digest,
                image=_open_rgba(output, "transformed pet"),
            )
        )
    if not evidence:
        raise LayoutProposalError(
            f"no successful transformed-pet evidence found in {pet_experiment}; "
            f"attempt_prefix={attempt_prefix!r}; failed={failures}"
        )
    pinned_run = _json(pinned_run_path, "pinned pet attempt")
    prompt_variables = pinned_run.get("prompt_variables")
    embedded_name = (
        prompt_variables.get("pet_name")
        if isinstance(prompt_variables, Mapping)
        else None
    )
    return evidence, {
        "pet_experiment": pet_experiment,
        "pinned_attempt": pinned_attempt,
        "embedded_name": embedded_name if isinstance(embedded_name, str) else None,
        "failed_attempt_ids": failures,
    }


def _resolve_name_mode(
    requested: str,
    *,
    embedded_name: str | None,
) -> str:
    if requested not in NAME_MODES:
        raise LayoutProposalError(
            f"name mode must be one of: {', '.join(sorted(NAME_MODES))}"
        )
    mode = requested
    if mode == "auto":
        mode = "embedded-in-pet" if embedded_name else "layout-text"
    if mode == "embedded-in-pet" and not embedded_name:
        raise LayoutProposalError(
            "embedded-in-pet proposal requires a pinned pet attempt with an applied pet name"
        )
    if mode == "layout-text" and embedded_name:
        raise LayoutProposalError(
            "layout-text proposal cannot use a pinned pet attempt with embedded lettering"
        )
    return mode


def _seed_boxes(
    *,
    experiment: Path,
    meta: Mapping[str, Any],
    canvas_size: tuple[int, int],
) -> tuple[Rect, Rect]:
    width, height = canvas_size
    pet_box = Rect(
        x=round(width * 0.2),
        y=round(height * 0.18),
        width=round(width * 0.6),
        height=round(height * 0.56),
    )
    name_box = Rect(
        x=round(width * 0.1),
        y=round(height * 0.76),
        width=round(width * 0.8),
        height=round(height * 0.14),
    )
    inputs = meta.get("inputs")
    if not isinstance(inputs, Mapping) or "layout_reference" not in inputs:
        return pet_box, name_box
    if "reference" not in inputs:
        raise LayoutProposalError("layout reference requires a finished-design reference")
    reference = _input_path(experiment, inputs["reference"], "reference")
    layout_reference_path = _input_path(
        experiment, inputs["layout_reference"], "layout reference"
    )
    try:
        layout_reference = load_layout_reference(layout_reference_path, reference)
        with Image.open(reference) as image:
            reference_size = image.size
    except (LayoutReferenceError, OSError) as exc:
        raise LayoutProposalError(str(exc)) from exc
    return (
        map_reference_box(
            layout_reference.pet_region,
            reference_size=reference_size,
            canvas_size=canvas_size,
        ),
        map_reference_box(
            layout_reference.name_region,
            reference_size=reference_size,
            canvas_size=canvas_size,
        ),
    )


def _font_candidates(
    *,
    experiment: Path,
    meta: Mapping[str, Any],
) -> tuple[tuple[FontCandidate, ...], list[dict[str, Any]], Any | None, Path | None]:
    inputs = meta.get("inputs")
    if not isinstance(inputs, Mapping):
        raise LayoutProposalError("layout experiment inputs are invalid")
    roots = tuple(
        _input_path(experiment, descriptor, "font catalog")
        for descriptor in inputs.get("font_catalogs", [])
    )
    if not roots:
        raise LayoutProposalError(
            "layout-text proposal requires at least one snapshotted font catalog"
        )
    try:
        candidates = discover_font_catalog(None, catalog_roots=roots)
    except FontCatalogError as exc:
        raise LayoutProposalError(str(exc)) from exc
    ranking: list[dict[str, Any]] = []
    font_reference = None
    reference = None
    if "font_reference" in inputs and "reference" in inputs:
        reference = _input_path(experiment, inputs["reference"], "reference")
        path = _input_path(experiment, inputs["font_reference"], "font reference")
        try:
            font_reference = load_font_reference(path, reference)
            matches = rank_fonts(reference, font_reference, candidates)
        except (FontReferenceError, ValueError) as exc:
            raise LayoutProposalError(str(exc)) from exc
        candidates = tuple(match.candidate for match in matches)
        ranking = [
            {
                "rank": index,
                "font_id": match.candidate.candidate_id,
                "label": match.candidate.label,
                "similarity_score": match.score,
                "confidence_score": match.confidence,
                "confidence_level": match.confidence_level,
            }
            for index, match in enumerate(matches[:15], 1)
        ]
    else:
        ranking = [
            {
                "rank": index,
                "font_id": candidate.candidate_id,
                "label": candidate.label,
                "similarity_score": None,
                "confidence_score": None,
                "confidence_level": "unranked",
            }
            for index, candidate in enumerate(candidates[:15], 1)
        ]
    return candidates, ranking, font_reference, reference


def _clamped_scaled_box(
    seed: Rect,
    *,
    scale: float,
    dx: float,
    dy: float,
    canvas_size: tuple[int, int],
) -> Rect:
    canvas_width, canvas_height = canvas_size
    width = max(1, min(canvas_width, round(seed.width * scale)))
    height = max(1, min(canvas_height, round(seed.height * scale)))
    center_x = seed.x + seed.width / 2 + dx * canvas_width
    center_y = seed.y + seed.height / 2 + dy * canvas_height
    x = max(0, min(canvas_width - width, round(center_x - width / 2)))
    y = max(0, min(canvas_height - height, round(center_y - height / 2)))
    return Rect(x=x, y=y, width=width, height=height)


def _pet_boxes(seed: Rect, canvas_size: tuple[int, int]) -> list[Rect]:
    offsets = (
        (0.0, 0.0),
        (0.0, -0.04),
        (-0.04, 0.0),
        (0.04, 0.0),
        (0.0, 0.04),
        (-0.04, -0.04),
        (0.04, -0.04),
        (-0.04, 0.04),
        (0.04, 0.04),
    )
    result: list[Rect] = []
    seen: set[tuple[int, int, int, int]] = set()
    for scale in (1.0, 1.08, 0.92):
        for dx, dy in offsets:
            box = _clamped_scaled_box(
                seed, scale=scale, dx=dx, dy=dy, canvas_size=canvas_size
            )
            key = (box.x, box.y, box.width, box.height)
            if key not in seen:
                seen.add(key)
                result.append(box)
    return result


def _shifted_name_box(seed: Rect, dy: float, canvas_size: tuple[int, int]) -> Rect:
    width, height = canvas_size
    y = max(0, min(height - seed.height, seed.y + round(dy * height)))
    x = max(0, min(width - seed.width, seed.x))
    return Rect(x=x, y=y, width=seed.width, height=seed.height)


def _layout(
    *,
    template_dir: Path,
    art: Path,
    pet_box: Rect,
    name_box: Rect | None,
    font: FontCandidate | None,
    font_size_px: int | None,
    min_font_size_px: int | None,
    padding_px: int | None,
) -> Layout:
    value: dict[str, Any] = {
        "schema_version": 2,
        "art": "art.png",
        "pet": {"box": pet_box.to_dict()},
    }
    font_override = None
    if name_box is not None:
        assert font is not None
        assert font_size_px is not None
        assert min_font_size_px is not None
        assert padding_px is not None
        value["name"] = {
            "box": name_box.to_dict(),
            "font": f"fonts/{font.font.name}",
            "font_size_px": font_size_px,
            "min_font_size_px": min_font_size_px,
            "fit": "shrink_only",
            "padding_px": padding_px,
            "color": "#F7E7C6FF",
            "horizontal_align": "center",
        }
        font_override = font.font
    return parse_layout(
        value,
        template_dir,
        art_override=art,
        font_override=font_override,
    )


def _overlap_with_art(placement: Any, art_alpha: Image.Image) -> float:
    pet_alpha = _alpha_mask(placement.image)
    mass = max(1.0, _ink_mass(pet_alpha))
    crop = art_alpha.crop(placement.bounds)
    return _ink_mass(ImageChops.multiply(pet_alpha, crop)) / mass


def _overlap_with_text(placement: Any, text: Any, name: str) -> float:
    left, top, right, bottom = text.bounds
    intersect_left = max(left, placement.x)
    intersect_top = max(top, placement.y)
    intersect_right = min(right, placement.x + placement.image.width)
    intersect_bottom = min(bottom, placement.y + placement.image.height)
    if intersect_left >= intersect_right or intersect_top >= intersect_bottom:
        return 0.0
    text_mask = Image.new("L", (max(1, right - left), max(1, bottom - top)), 0)
    ImageDraw.Draw(text_mask).text(
        (text.origin[0] - left, text.origin[1] - top),
        name,
        font=text.font,
        fill=255,
    )
    text_crop = text_mask.crop(
        (
            intersect_left - left,
            intersect_top - top,
            intersect_right - left,
            intersect_bottom - top,
        )
    )
    pet_alpha = _alpha_mask(placement.image).crop(
        (
            intersect_left - placement.x,
            intersect_top - placement.y,
            intersect_right - placement.x,
            intersect_bottom - placement.y,
        )
    )
    return _ink_mass(ImageChops.multiply(pet_alpha, text_crop)) / max(
        1.0, _ink_mass(text_mask)
    )


def _score_candidate(
    *,
    layout: Layout,
    pets: list[PetEvidence],
    art_alpha: Image.Image,
    names: tuple[str, ...],
    seed_pet: Rect,
) -> tuple[float, dict[str, Any]]:
    art_overlaps: list[float] = []
    pet_scales: list[float] = []
    pet_heights: list[float] = []
    name_overlaps: list[float] = []
    text_scales: list[float] = []
    for pet in pets:
        placement = prepare_pet_placement(pet.image, layout.pet_box)
        art_overlaps.append(_overlap_with_art(placement, art_alpha))
        pet_scales.append(placement.scale)
        pet_heights.append(placement.image.height / layout.canvas_height)
        for name in names:
            text = prepare_text_placement(layout, name)
            text_scales.append(
                text.metrics.applied_font_size_px / text.metrics.requested_font_size_px
            )
            name_overlaps.append(_overlap_with_text(placement, text, name))
    max_art_overlap = max(art_overlaps)
    mean_art_overlap = sum(art_overlaps) / len(art_overlaps)
    max_name_overlap = max(name_overlaps, default=0.0)
    minimum_text_scale = min(text_scales, default=1.0)
    minimum_pet_scale = min(pet_scales)
    minimum_pet_height_ratio = min(pet_heights)
    displacement = (
        abs(layout.pet_box.x - seed_pet.x) / layout.canvas_width
        + abs(layout.pet_box.y - seed_pet.y) / layout.canvas_height
        + abs(layout.pet_box.width - seed_pet.width) / layout.canvas_width
        + abs(layout.pet_box.height - seed_pet.height) / layout.canvas_height
    )
    score = (
        100.0
        - 500.0 * max_art_overlap
        - 180.0 * mean_art_overlap
        - 500.0 * max_name_overlap
        - 80.0 * (1.0 - minimum_text_scale)
        - 18.0 * displacement
        + 25.0 * minimum_pet_height_ratio
    )
    metrics = {
        "score": round(score, 6),
        "maximum_art_alpha_overlap_ratio": round(max_art_overlap, 6),
        "mean_art_alpha_overlap_ratio": round(mean_art_overlap, 6),
        "maximum_pet_name_alpha_overlap_ratio": round(max_name_overlap, 6),
        "minimum_pet_source_scale": round(minimum_pet_scale, 6),
        "minimum_pet_canvas_height_ratio": round(minimum_pet_height_ratio, 6),
        "minimum_name_font_scale": round(minimum_text_scale, 6),
        "normalized_seed_displacement": round(displacement, 6),
    }
    return score, metrics


def _checkerboard(size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", size, (244, 244, 244))
    draw = ImageDraw.Draw(image)
    block = 16
    for y in range(0, size[1], block):
        for x in range(0, size[0], block):
            if (x // block + y // block) % 2:
                draw.rectangle(
                    (x, y, min(x + block - 1, size[0] - 1), min(y + block - 1, size[1] - 1)),
                    fill=(216, 216, 216),
                )
    return image


def _contact_sheet(entries: Iterable[tuple[str, Path, str]], output: Path) -> None:
    values = list(entries)
    columns = min(3, len(values))
    rows = (len(values) + columns - 1) // columns
    cell_width, cell_height = 360, 430
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), (235, 235, 235))
    draw = ImageDraw.Draw(sheet)
    for index, (label, path, detail) in enumerate(values):
        left = (index % columns) * cell_width
        top = (index // columns) * cell_height
        draw.rectangle(
            (left + 8, top + 8, left + cell_width - 8, top + cell_height - 8),
            fill=(255, 255, 255),
            outline=(180, 180, 180),
        )
        draw.text((left + 16, top + 16), label[:52], fill=(20, 20, 20))
        draw.text((left + 16, top + 34), detail[:58], fill=(70, 70, 70))
        checker = _checkerboard((320, 320))
        with Image.open(path) as source:
            preview = source.convert("RGBA")
            preview.thumbnail((320, 320), Image.Resampling.LANCZOS)
        checker.paste(
            preview,
            ((320 - preview.width) // 2, (320 - preview.height) // 2),
            preview,
        )
        sheet.paste(checker, (left + 20, top + 64))
        draw.text((left + 16, top + 394), f"sha256={sha256(path)[:16]}...", fill=(70, 70, 70))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="PNG")


def _copy_font(candidate: FontCandidate, destination: Path) -> None:
    destination.mkdir(parents=True)
    shutil.copyfile(candidate.font, destination / candidate.font.name)
    shutil.copyfile(resolve_ofl_license(candidate.font), destination / "OFL.txt")
    source_metadata = candidate.font.parent / "source.json"
    family_metadata = candidate.font.parent / "METADATA.pb"
    if source_metadata.exists() != family_metadata.exists():
        raise LayoutProposalError(
            f"font provenance requires both source.json and METADATA.pb: {candidate.font.parent}"
        )
    if source_metadata.is_file():
        shutil.copyfile(source_metadata, destination / "source.json")
        shutil.copyfile(family_metadata, destination / "METADATA.pb")


def propose_layout(
    *,
    experiment: Path,
    proposal_id: str,
    name_mode: str = "auto",
    pet_names: Iterable[str] = (),
    attempt_prefix: str = "release-",
    max_candidates: int = MAX_SEARCH_CANDIDATES,
    finalists: int = MAX_FINALISTS,
) -> Path:
    """Write an immutable local layout proposal and return its directory."""
    started = time.monotonic()
    _id(proposal_id, "proposal ID")
    if not 1 <= max_candidates <= MAX_SEARCH_CANDIDATES:
        raise LayoutProposalError(
            f"max candidates must be between 1 and {MAX_SEARCH_CANDIDATES}"
        )
    if not 1 <= finalists <= MAX_FINALISTS:
        raise LayoutProposalError(f"finalists must be between 1 and {MAX_FINALISTS}")
    if (
        not attempt_prefix
        or attempt_prefix.strip() != attempt_prefix
        or any(character in attempt_prefix for character in "/*?[]\\")
    ):
        raise LayoutProposalError(
            "attempt prefix must be non-empty text without whitespace padding, "
            "path separators, or glob characters"
        )
    experiment = experiment.expanduser().resolve()
    meta = _json(experiment / "experiment.json", "layout experiment")
    if meta.get("kind") != "layout":
        raise LayoutProposalError(f"proposal requires a layout experiment: {experiment}")
    if meta.get("status") == "discarded":
        raise LayoutProposalError(f"cannot propose from discarded experiment: {experiment}")
    inputs = meta.get("inputs")
    if not isinstance(inputs, Mapping):
        raise LayoutProposalError("layout experiment inputs are invalid")
    art = _input_path(experiment, inputs.get("art"), "art")
    art_image = _open_rgba(art, "art")
    canvas_size = art_image.size
    art_alpha = _alpha_mask(art_image)
    pets, pet_context = _pet_evidence(
        experiment, meta, attempt_prefix=attempt_prefix
    )
    resolved_mode = _resolve_name_mode(
        name_mode, embedded_name=pet_context["embedded_name"]
    )
    names = tuple(dict.fromkeys(name.strip() for name in pet_names if name.strip()))
    if resolved_mode == "layout-text":
        names = names or DEFAULT_LAYOUT_TEXT_NAMES
    elif names:
        raise LayoutProposalError(
            f"pet-name probes are valid only for layout-text; name_mode={resolved_mode}"
        )
    seed_pet, seed_name = _seed_boxes(
        experiment=experiment, meta=meta, canvas_size=canvas_size
    )
    fonts: tuple[FontCandidate, ...] = ()
    font_ranking: list[dict[str, Any]] = []
    font_reference = None
    font_reference_image = None
    if resolved_mode == "layout-text":
        fonts, font_ranking, font_reference, font_reference_image = _font_candidates(
            experiment=experiment, meta=meta
        )

    search: list[tuple[Rect, Rect | None, FontCandidate | None, int | None, int | None, int | None]] = []
    for pet_box in _pet_boxes(seed_pet, canvas_size):
        if resolved_mode != "layout-text":
            search.append((pet_box, None, None, None, None, None))
            continue
        font = fonts[0]
        for name_dy, font_scale in ((0.0, 1.0), (-0.02, 0.9)):
            name_box = _shifted_name_box(seed_name, name_dy, canvas_size)
            padding = min(4, max(0, (name_box.height - 1) // 2))
            if font_reference is not None and font_reference_image is not None:
                nominal = recommend_font_size(
                    font_reference_image,
                    font_reference,
                    font.font,
                    box_width=name_box.width,
                    box_height=name_box.height,
                    padding=padding,
                ).font_size_px
            else:
                nominal = name_box.height
            nominal = max(1, round(nominal * font_scale))
            minimum = min(
                nominal,
                recommend_min_font_size_for_capacity(
                    font.font,
                    box_width=name_box.width,
                    box_height=name_box.height,
                    padding=padding,
                    character_count=DEFAULT_MAX_NAME_CODE_POINTS,
                ),
            )
            search.append((pet_box, name_box, font, nominal, minimum, padding))
    search = search[:max_candidates]

    scored: list[Candidate] = []
    rejected = 0
    for index, (pet_box, name_box, font, nominal, minimum, padding) in enumerate(search):
        try:
            layout = _layout(
                template_dir=experiment / "inputs",
                art=art,
                pet_box=pet_box,
                name_box=name_box,
                font=font,
                font_size_px=nominal,
                min_font_size_px=minimum,
                padding_px=padding,
            )
            score, metrics = _score_candidate(
                layout=layout,
                pets=pets,
                art_alpha=art_alpha,
                names=names,
                seed_pet=seed_pet,
            )
        except (RenderError, ValueError, OSError):
            rejected += 1
            continue
        sort_key = (
            -score,
            metrics["maximum_art_alpha_overlap_ratio"],
            metrics["maximum_pet_name_alpha_overlap_ratio"],
            metrics["normalized_seed_displacement"],
            pet_box.x,
            pet_box.y,
            pet_box.width,
            pet_box.height,
            index,
        )
        scored.append(
            Candidate(
                pet_box=pet_box,
                name_box=name_box,
                font=font,
                font_size_px=nominal,
                min_font_size_px=minimum,
                padding_px=padding,
                score=score,
                metrics=metrics,
                sort_key=sort_key,
            )
        )
    scored.sort(key=lambda item: item.sort_key)
    selected = scored[:finalists]
    if not selected:
        raise LayoutProposalError(
            f"bounded search produced no valid layout candidates; evaluated={len(search)}; rejected={rejected}"
        )

    destination = experiment / "proposals" / proposal_id
    partial = destination.with_name(destination.name + ".partial")
    if destination.exists() or partial.exists():
        raise LayoutProposalError(
            f"layout proposal already exists or is partial: {destination}"
        )
    partial.mkdir(parents=True)
    try:
        candidate_records: list[dict[str, Any]] = []
        ranked_entries: list[tuple[str, Path, str]] = []
        for rank, candidate in enumerate(selected, 1):
            candidate_id = f"rank-{rank:02d}"
            candidate_dir = partial / "candidates" / candidate_id
            candidate_dir.mkdir(parents=True)
            shutil.copyfile(art, candidate_dir / "art.png")
            pinned_pet = pets[0]
            shutil.copyfile(pinned_pet.path, candidate_dir / "transformed-pet.png")
            if candidate.font is not None:
                _copy_font(candidate.font, candidate_dir / "fonts")
            layout = _layout(
                template_dir=candidate_dir,
                art=candidate_dir / "art.png",
                pet_box=candidate.pet_box,
                name_box=candidate.name_box,
                font=candidate.font,
                font_size_px=candidate.font_size_px,
                min_font_size_px=candidate.min_font_size_px,
                padding_px=candidate.padding_px,
            )
            atomic_json(candidate_dir / "layout.json", layout.to_dict())
            preview_name = names[min(1, len(names) - 1)] if names else None
            render_to_files(
                template_dir=candidate_dir,
                pet_image=candidate_dir / "transformed-pet.png",
                pet_name=preview_name,
                output=candidate_dir / "preview.png",
                debug_output=candidate_dir / "preview-debug.png",
            )
            matrix_entries: list[tuple[str, Path, str]] = []
            matrix_records: list[dict[str, Any]] = []
            test_names = names or (None,)
            for pet in pets:
                for name_index, name in enumerate(test_names, 1):
                    safe_name = re.sub(r"[^a-z0-9]+", "-", (name or "no-name").lower()).strip("-")
                    filename = f"{pet.attempt_id}--{name_index:02d}-{safe_name}.png"
                    output = candidate_dir / "qa" / "matrix" / filename
                    render_to_files(
                        template_dir=candidate_dir,
                        pet_image=pet.path,
                        pet_name=name,
                        output=output,
                    )
                    label = pet.attempt_id
                    if name is not None:
                        label += f" / {name}"
                    matrix_entries.append((label, output, candidate_id))
                    matrix_records.append(
                        {
                            "pet_attempt_id": pet.attempt_id,
                            "pet_sha256": pet.sha256,
                            "pet_name": name,
                            "preview": output.relative_to(partial).as_posix(),
                            "preview_sha256": sha256(output),
                        }
                    )
            matrix_sheet = candidate_dir / "qa" / "fixture-name-matrix.png"
            _contact_sheet(matrix_entries, matrix_sheet)
            metrics_path = candidate_dir / "metrics.json"
            atomic_json(
                metrics_path,
                {
                    "schema_version": 1,
                    "candidate_id": candidate_id,
                    "rank": rank,
                    "metrics": candidate.metrics,
                    "fixture_name_matrix": matrix_records,
                },
            )
            ranked_entries.append(
                (
                    candidate_id,
                    candidate_dir / "preview.png",
                    f"score={candidate.metrics['score']:.2f} overlap={candidate.metrics['maximum_art_alpha_overlap_ratio']:.3f}",
                )
            )
            candidate_records.append(
                {
                    "rank": rank,
                    "candidate_id": candidate_id,
                    "layout": (candidate_dir / "layout.json").relative_to(partial).as_posix(),
                    "layout_sha256": sha256(candidate_dir / "layout.json"),
                    "preview": (candidate_dir / "preview.png").relative_to(partial).as_posix(),
                    "preview_sha256": sha256(candidate_dir / "preview.png"),
                    "debug_preview": (candidate_dir / "preview-debug.png").relative_to(partial).as_posix(),
                    "matrix": matrix_sheet.relative_to(partial).as_posix(),
                    "matrix_sha256": sha256(matrix_sheet),
                    "metrics": candidate.metrics,
                }
            )
        comparison = partial / "ranked-layout-proposals.png"
        _contact_sheet(ranked_entries, comparison)
        product = experiment.parents[2]
        record = {
            "schema_version": 1,
            "proposal_id": proposal_id,
            "kind": "deterministic-layout-proposal",
            "design_id": meta.get("design_id"),
            "product_profile_id": meta.get("product_profile_id"),
            "layout_experiment": experiment.relative_to(product).as_posix(),
            "layout_experiment_sha256": sha256(experiment / "experiment.json"),
            "created_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "name_mode": resolved_mode,
            "search": {
                "method": "bounded-alpha-grid-v1",
                "candidate_limit": max_candidates,
                "evaluated_candidates": len(search),
                "valid_candidates": len(scored),
                "rejected_candidates": rejected,
                "finalist_limit": finalists,
                "seed_pet_box": seed_pet.to_dict(),
                "seed_name_box": seed_name.to_dict() if resolved_mode == "layout-text" else None,
                "pet_name_probes": list(names),
            },
            "pet_evidence": {
                "attempt_prefix": attempt_prefix,
                "successful_attempts": [
                    {
                        "attempt_id": pet.attempt_id,
                        "sha256": pet.sha256,
                    }
                    for pet in pets
                ],
                "failed_attempt_ids": pet_context["failed_attempt_ids"],
            },
            "font_ranking": font_ranking,
            "ranked_comparison": {
                "path": comparison.relative_to(partial).as_posix(),
                "sha256": sha256(comparison),
            },
            "candidates": candidate_records,
            "operator_action": {
                "review": "Inspect ranked-layout-proposals.png and each finalist fixture/name matrix.",
                "accept": "Import the selected candidates/<rank>/layout.json with pawmarvel-author run-attempt --layout-file.",
                "fallback": "Run the existing interactive layout attempt when no proposal is visually acceptable.",
            },
        }
        atomic_json(partial / "proposal.json", record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, destination)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return destination
