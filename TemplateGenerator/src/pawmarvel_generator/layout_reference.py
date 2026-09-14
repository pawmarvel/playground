"""Validate authoring-only reference regions used to initialize composition."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError

from .artifact_io import mismatch, read_json, sha256
from .config import Rect
from .geometry import round_half_up


class LayoutReferenceError(ValueError):
    """A layout-reference artifact is invalid for its reference image."""


@dataclass(frozen=True)
class LayoutReference:
    reference_image_sha256: str
    pet_region: Rect
    name_region: Rect
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "reference_image_sha256": self.reference_image_sha256,
            "pet_region": self.pet_region.to_dict(),
            "name_region": self.name_region.to_dict(),
        }


def map_reference_box(
    box: Rect,
    *,
    reference_size: tuple[int, int],
    canvas_size: tuple[int, int],
) -> Rect:
    """Map screenshot coordinates to a target canvas using normalized edges."""
    if min(*reference_size, *canvas_size) <= 0:
        raise LayoutReferenceError("reference and canvas dimensions must be positive")
    left = round_half_up(box.x * canvas_size[0] / reference_size[0])
    top = round_half_up(box.y * canvas_size[1] / reference_size[1])
    right = round_half_up(
        (box.x + box.width) * canvas_size[0] / reference_size[0]
    )
    bottom = round_half_up(
        (box.y + box.height) * canvas_size[1] / reference_size[1]
    )
    return Rect(
        x=left,
        y=top,
        width=max(1, right - left),
        height=max(1, bottom - top),
    )


def _image_size(reference: Path) -> tuple[int, int]:
    try:
        with Image.open(reference) as image:
            image.load()
            return image.size
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise LayoutReferenceError(
            f"reference image is not readable: {reference}"
        ) from exc


def _box(value: Any, label: str, image_size: tuple[int, int]) -> Rect:
    if not isinstance(value, Mapping) or set(value) != {
        "x",
        "y",
        "width",
        "height",
    }:
        raise LayoutReferenceError(
            f"layout reference {label} must contain only x, y, width, and height"
        )
    coordinates: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        coordinate = value[key]
        if isinstance(coordinate, bool) or not isinstance(coordinate, int):
            raise LayoutReferenceError(
                f"layout reference {label}.{key} must be an integer"
            )
        coordinates[key] = coordinate
    result = Rect(**coordinates)
    if result.x < 0 or result.y < 0:
        raise LayoutReferenceError(
            f"layout reference {label} x and y must not be negative"
        )
    if result.width <= 0 or result.height <= 0:
        raise LayoutReferenceError(
            f"layout reference {label} width and height must be positive"
        )
    if (
        result.x + result.width > image_size[0]
        or result.y + result.height > image_size[1]
    ):
        raise LayoutReferenceError(
            f"layout reference {label} must be fully inside the reference image"
        )
    return result


def parse_layout_reference(value: Any, reference: Path) -> LayoutReference:
    reference = reference.expanduser().resolve()
    if not isinstance(value, Mapping):
        raise LayoutReferenceError("layout reference must be an object")
    expected = {
        "schema_version",
        "reference_image_sha256",
        "pet_region",
        "name_region",
    }
    if set(value) != expected:
        missing = expected - set(value)
        unknown = set(value) - expected
        details = []
        if missing:
            details.append(f"missing {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"unsupported {', '.join(sorted(unknown))}")
        raise LayoutReferenceError(
            f"layout reference fields are invalid: {'; '.join(details)}"
        )
    if value["schema_version"] != 1:
        raise LayoutReferenceError("layout reference schema_version must be 1")
    image_hash = value["reference_image_sha256"]
    if (
        not isinstance(image_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", image_hash) is None
    ):
        raise LayoutReferenceError(
            "layout reference reference_image_sha256 must be a SHA-256 digest"
        )
    expected_hash = sha256(reference)
    if image_hash != expected_hash:
        raise LayoutReferenceError(
            mismatch(
                f"layout reference image hash ({reference})",
                expected=expected_hash,
                actual=image_hash,
            )
        )
    image_size = _image_size(reference)
    return LayoutReference(
        reference_image_sha256=expected_hash,
        pet_region=_box(value["pet_region"], "pet_region", image_size),
        name_region=_box(value["name_region"], "name_region", image_size),
    )


def layout_reference_from_editor(
    *, reference: Path, pet_region: Any, name_region: Any
) -> LayoutReference:
    reference = reference.expanduser().resolve()
    return parse_layout_reference(
        {
            "schema_version": 1,
            "reference_image_sha256": sha256(reference),
            "pet_region": pet_region,
            "name_region": name_region,
        },
        reference,
    )


def load_layout_reference(path: Path, reference: Path) -> LayoutReference:
    value = read_json(
        path,
        label="layout reference",
        error_type=LayoutReferenceError,
        require_object=True,
        correction="fix the file or recapture the regions in the layout editor.",
    )
    try:
        return parse_layout_reference(value, reference)
    except LayoutReferenceError as exc:
        raise LayoutReferenceError(
            f"layout reference validation failed; artifact={path.expanduser().resolve()}; "
            f"error={exc}"
        ) from exc
