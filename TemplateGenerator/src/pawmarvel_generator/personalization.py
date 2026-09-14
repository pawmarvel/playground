"""Shared pet-name normalization and bundle-policy primitives."""

from __future__ import annotations

import unicodedata
from typing import Any


DEFAULT_MAX_NAME_CODE_POINTS = 12
MIN_MAX_NAME_CODE_POINTS = 1
MAX_MAX_NAME_CODE_POINTS = 64
ALLOWED_CHARACTER_POLICY = (
    "unicode-letters-marks-numbers-space-apostrophes-ascii-hyphen"
)


class PersonalizationError(ValueError):
    """A customer personalization value violates the declared bundle policy."""


def pet_name_policy(max_code_points: int) -> dict[str, Any]:
    if (
        isinstance(max_code_points, bool)
        or not isinstance(max_code_points, int)
        or not MIN_MAX_NAME_CODE_POINTS
        <= max_code_points
        <= MAX_MAX_NAME_CODE_POINTS
    ):
        raise PersonalizationError(
            "pet name maximum must be an integer from 1 through 64"
        )
    return {
        "normalization": "NFC",
        "whitespace": "trim-and-collapse",
        "length_unit": "unicode-code-points",
        "min_length": 1,
        "max_length": max_code_points,
        "allowed_characters": ALLOWED_CHARACTER_POLICY,
    }


def validate_pet_name(value: object, policy: dict[str, Any]) -> str:
    expected = pet_name_policy(policy.get("max_length"))
    if policy != expected:
        raise PersonalizationError("pet name policy is unsupported")
    if not isinstance(value, str):
        raise PersonalizationError("pet name must be text")
    normalized = unicodedata.normalize("NFC", " ".join(value.split()))
    if not expected["min_length"] <= len(normalized) <= expected["max_length"]:
        raise PersonalizationError(
            "pet name must contain "
            f"{expected['min_length']}-{expected['max_length']} Unicode code points"
        )
    for character in normalized:
        if (
            character == " "
            or character in {"'", "’", "-"}
            or unicodedata.category(character)[0] in {"L", "M", "N"}
        ):
            continue
        raise PersonalizationError(
            "pet name contains a character outside the bundle policy"
        )
    return normalized
