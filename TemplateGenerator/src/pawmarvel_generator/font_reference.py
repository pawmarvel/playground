"""Validate the authoring-only reference region used for font matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError

from .artifact_io import mismatch, read_json, sha256
from .config import Rect


MAX_REFERENCE_TEXT_LENGTH = 64


class FontReferenceError(ValueError):
    """A font-reference artifact is invalid for its reference image."""


@dataclass(frozen=True)
class FontReference:
    reference_image_sha256: str
    region: Rect
    text: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "reference_image_sha256": self.reference_image_sha256,
            "region": self.region.to_dict(),
            "text": self.text,
        }


def _reference_size(reference: Path) -> tuple[int, int]:
    try:
        with Image.open(reference) as image:
            image.load()
            return image.size
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise FontReferenceError(
            f"reference image is not readable: {reference}"
        ) from exc


def parse_font_reference(
    value: Any,
    reference: Path,
    *,
    require_image_hash: bool = True,
) -> FontReference:
    reference = reference.expanduser().resolve()
    if not isinstance(value, Mapping):
        raise FontReferenceError("font reference must be an object")
    expected = {"schema_version", "reference_image_sha256", "region", "text"}
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing:
        raise FontReferenceError(
            f"font reference is missing: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise FontReferenceError(
            f"font reference has unsupported fields: {', '.join(sorted(unknown))}"
        )
    if value["schema_version"] != 1:
        raise FontReferenceError("font reference schema_version must be 1")

    expected_hash = sha256(reference)
    image_hash = value["reference_image_sha256"]
    if (
        not isinstance(image_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", image_hash) is None
    ):
        raise FontReferenceError(
            "font reference reference_image_sha256 must be a SHA-256 digest"
        )
    if require_image_hash and image_hash != expected_hash:
        raise FontReferenceError(
            mismatch(
                f"font reference does not match supplied image ({reference})",
                expected=expected_hash,
                actual=image_hash,
            )
        )

    region_value = value["region"]
    if not isinstance(region_value, Mapping):
        raise FontReferenceError("font reference region must be an object")
    region_keys = {"x", "y", "width", "height"}
    if set(region_value) != region_keys:
        raise FontReferenceError(
            "font reference region must contain only x, y, width, and height"
        )
    coordinates: dict[str, int] = {}
    for key in region_keys:
        coordinate = region_value[key]
        if isinstance(coordinate, bool) or not isinstance(coordinate, int):
            raise FontReferenceError(f"font reference region.{key} must be an integer")
        coordinates[key] = coordinate
    region = Rect(**coordinates)
    if region.x < 0 or region.y < 0:
        raise FontReferenceError("font reference region x and y must not be negative")
    if region.width <= 0 or region.height <= 0:
        raise FontReferenceError(
            "font reference region width and height must be positive"
        )
    image_width, image_height = _reference_size(reference)
    if (
        region.x + region.width > image_width
        or region.y + region.height > image_height
    ):
        raise FontReferenceError(
            "font reference region must be fully inside the reference image"
        )

    text = value["text"]
    if not isinstance(text, str):
        raise FontReferenceError("font reference text must be a string")
    text = text.strip()
    if not text:
        raise FontReferenceError("font reference text must not be empty")
    if len(text) > MAX_REFERENCE_TEXT_LENGTH:
        raise FontReferenceError(
            f"font reference text must not exceed {MAX_REFERENCE_TEXT_LENGTH} characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise FontReferenceError("font reference text must not contain control characters")

    return FontReference(
        reference_image_sha256=expected_hash,
        region=region,
        text=text,
    )


def font_reference_from_editor(
    *, reference: Path, region: Any, text: Any
) -> FontReference:
    reference = reference.expanduser().resolve()
    return parse_font_reference(
        {
            "schema_version": 1,
            "reference_image_sha256": sha256(reference),
            "region": region,
            "text": text,
        },
        reference,
    )


def load_font_reference(path: Path, reference: Path) -> FontReference:
    value = read_json(
        path,
        label="font reference",
        error_type=FontReferenceError,
        require_object=True,
        correction="fix the file or recapture the font reference in the layout editor.",
    )
    try:
        return parse_font_reference(value, reference)
    except FontReferenceError as exc:
        raise FontReferenceError(
            f"font reference validation failed; artifact={path.expanduser().resolve()}; "
            f"error={exc}"
        ) from exc
