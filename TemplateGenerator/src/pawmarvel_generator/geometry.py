"""Shared pixel-geometry rounding used by preview-to-print derivation."""

from __future__ import annotations

import math


def round_half_up(value: float) -> int:
    """Round halves away from zero so every renderer derives the same pixels."""
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)
