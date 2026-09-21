"""Shared validation primitives for the immutable production bundle contract."""

from __future__ import annotations

import re
import warnings
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .artifact_io import mismatch
from .config import Layout, Rect
from .geometry import round_half_up


IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROMPT_FILENAME_PATTERN = re.compile(
    r"^(art-template|pet-transform)-(gpt|gemini)\.md$"
)
AUTHORING_PROMPT_FILENAME_PATTERN = re.compile(
    r"^(art-template|pet-transform)-(gpt|gemini)"
    r"(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?\.md$"
)
PROMPT_MAX_BYTES = 1024 * 1024
MAX_RUNTIME_REFERENCES = 4
SUPPORTING_REFERENCES_DIR = "reference-designs"
UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)


class BundleError(ValueError):
    """A template bundle violates the production consumer contract."""


def validate_utc_timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise BundleError(f"{label} must be a UTC timestamp in YYYY-MM-DDTHH:MM:SSZ form")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BundleError(f"{label} is not a valid timestamp") from exc
    return value


def catalog_template_id(design_id: str, product_profile_id: str) -> str:
    if not 3 <= len(design_id) <= 64 or not IDENTIFIER_PATTERN.fullmatch(design_id):
        raise BundleError(
            "design id must be 3-64 lowercase letters, numbers, or internal hyphens"
        )
    if not 1 <= len(product_profile_id) <= 64 or not IDENTIFIER_PATTERN.fullmatch(
        product_profile_id
    ):
        raise BundleError(
            "product profile id must use lowercase letters, numbers, or internal hyphens"
        )
    value = f"{design_id}--{product_profile_id}"
    if len(value) > 127 or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]*[a-z0-9]", value
    ):
        raise BundleError("design and product profile IDs form an invalid template id")
    return value


def _validated_prompt(path: Path, kind: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise BundleError(f"{kind} prompt does not exist: {resolved}")
    if resolved.stat().st_size > PROMPT_MAX_BYTES:
        raise BundleError(f"{kind} prompt exceeds the 1 MiB bundle limit: {resolved}")
    try:
        contents = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"{kind} prompt must be UTF-8 text: {resolved}") from exc
    except OSError as exc:
        raise BundleError(f"{kind} prompt is not readable: {resolved}") from exc
    if not contents.strip() or "\x00" in contents:
        raise BundleError(f"{kind} prompt must be nonempty UTF-8 text without NUL bytes")
    return resolved


def _provider_category(provider: str) -> str:
    expected_category = {"openai": "gpt", "gemini": "gemini"}.get(provider)
    if expected_category is None:
        raise BundleError(f"unsupported image provider: {provider}")
    return expected_category


def prompt_contract(path: Path, kind: str, provider: str) -> tuple[Path, str]:
    """Validate the canonical provider-qualified filename exposed to FE."""
    resolved = _validated_prompt(path, kind)
    match = PROMPT_FILENAME_PATTERN.fullmatch(resolved.name)
    if match is None or match.group(1) != kind:
        raise BundleError(
            f"{kind} bundle prompt filename mismatch; actual={resolved.name!r}; "
            f"expected={kind}-{{gpt|gemini}}.md"
        )
    expected_category = _provider_category(provider)
    if match.group(2) != expected_category:
        raise BundleError(
            f"{resolved.name} does not match the selected {provider} provider"
        )
    return resolved, resolved.name


def authoring_prompt_contract(
    path: Path, kind: str, provider: str
) -> tuple[Path, str]:
    """Validate an experiment prompt and return its canonical bundle name."""
    resolved = _validated_prompt(path, kind)
    match = AUTHORING_PROMPT_FILENAME_PATTERN.fullmatch(resolved.name)
    if match is None or match.group(1) != kind:
        raise BundleError(
            f"{kind} authoring prompt filename mismatch; actual={resolved.name!r}; "
            f"expected={kind}-{{gpt|gemini}}[-<variant>].md"
        )
    expected_category = _provider_category(provider)
    if match.group(2) != expected_category:
        raise BundleError(
            f"{resolved.name} does not match the selected {provider} provider"
        )
    return resolved, f"{kind}-{expected_category}.md"


def validate_raster(
    path: Path,
    label: str,
    *,
    require_png: bool = False,
    require_alpha: bool = False,
    expected_size: tuple[int, int] | None = None,
    allow_large: bool = False,
) -> tuple[int, int]:
    resolved = path.expanduser().resolve()
    try:
        with warnings.catch_warnings():
            if allow_large:
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(resolved) as image:
                image.load()
                if require_png and image.format != "PNG":
                    raise BundleError(f"{label} must be PNG: {resolved}")
                if image.width <= 0 or image.height <= 0:
                    raise BundleError(f"{label} has invalid dimensions: {resolved}")
                if expected_size is not None and image.size != expected_size:
                    raise BundleError(
                        f"{label} dimensions are {image.width}x{image.height}, expected "
                        f"{expected_size[0]}x{expected_size[1]}"
                    )
                if require_alpha:
                    if "A" not in image.getbands() and "transparency" not in image.info:
                        raise BundleError(f"{label} must contain an alpha channel: {resolved}")
                    low, high = image.convert("RGBA").getchannel("A").getextrema()
                    if high == 0:
                        raise BundleError(f"{label} is fully transparent: {resolved}")
                    if low == 255:
                        raise BundleError(f"{label} has no transparent pixels: {resolved}")
                return image.size
    except BundleError:
        raise
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise BundleError(f"{label} is not a readable image: {resolved}") from exc


def canonical_reference_paths(count: int) -> list[str]:
    if not 1 <= count <= MAX_RUNTIME_REFERENCES:
        raise BundleError(
            f"runtime requires one to {MAX_RUNTIME_REFERENCES} finished-design references"
        )
    return ["reference-design.png"] + [
        f"{SUPPORTING_REFERENCES_DIR}/reference-design-{index:04d}.png"
        for index in range(2, count + 1)
    ]


def _scaled_rect(rect: Rect, scale: float) -> Rect:
    left = round_half_up(rect.x * scale)
    top = round_half_up(rect.y * scale)
    right = round_half_up(rect.right * scale)
    bottom = round_half_up(rect.bottom * scale)
    return Rect(left, top, right - left, bottom - top)


def validate_layout_pair(preview: Layout, print_layout: Layout) -> None:
    if (
        print_layout.canvas_width <= preview.canvas_width
        or print_layout.canvas_height <= preview.canvas_height
    ):
        raise BundleError(
            mismatch(
                "print canvas must be larger than preview canvas",
                expected=f"> {preview.canvas_width}x{preview.canvas_height} in both dimensions",
                actual=f"{print_layout.canvas_width}x{print_layout.canvas_height}",
            )
        )
    if (
        print_layout.canvas_width * preview.canvas_height
        != print_layout.canvas_height * preview.canvas_width
    ):
        raise BundleError(
            mismatch(
                "preview/print aspect ratio",
                expected=f"{preview.canvas_width}:{preview.canvas_height}",
                actual=f"{print_layout.canvas_width}:{print_layout.canvas_height}",
            )
        )
    scale = print_layout.canvas_width / preview.canvas_width
    expected_pet_box = _scaled_rect(preview.pet_box, scale)
    if print_layout.pet_box != expected_pet_box:
        raise BundleError(
            mismatch(
                "layout-print pet.box",
                expected=expected_pet_box,
                actual=print_layout.pet_box,
            )
        )
    if print_layout.has_name != preview.has_name:
        raise BundleError(
            mismatch(
                "layout-print name-layer mode",
                expected=preview.has_name,
                actual=print_layout.has_name,
            )
        )
    if not preview.has_name:
        return
    assert preview.name_box is not None
    assert print_layout.name_box is not None
    assert preview.font_size_px is not None
    assert preview.min_font_size_px is not None
    assert preview.name_padding_px is not None
    expected_name_box = _scaled_rect(preview.name_box, scale)
    if print_layout.name_box != expected_name_box:
        raise BundleError(
            mismatch(
                "layout-print name.box",
                expected=expected_name_box,
                actual=print_layout.name_box,
            )
        )
    scaled_name_values = {
        "font_size_px": max(1, round_half_up(preview.font_size_px * scale)),
        "min_font_size_px": max(
            1, round_half_up(preview.min_font_size_px * scale)
        ),
        "name_padding_px": max(
            0, round_half_up(preview.name_padding_px * scale)
        ),
    }
    for field, expected in scaled_name_values.items():
        if getattr(print_layout, field) != expected:
            json_field = "padding_px" if field == "name_padding_px" else field
            raise BundleError(
                mismatch(
                    f"layout-print name.{json_field}",
                    expected=expected,
                    actual=getattr(print_layout, field),
                )
            )
    if print_layout.font_relative != preview.font_relative:
        raise BundleError(
            mismatch(
                "layout-print name.font",
                expected=preview.font_relative,
                actual=print_layout.font_relative,
            )
        )
    if (
        print_layout.name_fit != preview.name_fit
        or print_layout.color != preview.color
        or print_layout.horizontal_align != preview.horizontal_align
    ):
        raise BundleError(
            mismatch(
                "layout-print name rendering settings",
                expected={
                    "fit": preview.name_fit,
                    "color": preview.color,
                    "horizontal_align": preview.horizontal_align,
                },
                actual={
                    "fit": print_layout.name_fit,
                    "color": print_layout.color,
                    "horizontal_align": print_layout.horizontal_align,
                },
            )
        )


def media_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".json": "application/json",
        ".md": "text/markdown",
        ".ttf": "font/ttf",
        ".txt": "text/plain",
        ".pb": "text/plain",
    }.get(path.suffix.lower(), "application/octet-stream")
