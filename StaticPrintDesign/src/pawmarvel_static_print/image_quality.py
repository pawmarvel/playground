"""Deterministic pixel-level print-readiness checks for generated PNG assets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

OPAQUE_FULL_BLEED = "opaque-full-bleed"
PRESERVE_TRANSPARENCY = "preserve-transparency"
PRINT_READINESS_SCHEMA = "pawmarvel-static-print-readiness-v1"

# Antialiasing belongs on a narrow silhouette edge. When more than this share of
# visible artwork is partially transparent, the image is commonly a model-created
# translucent wash rather than solid printable pixels. This remains an operator
# warning because watercolor and other intentional media can legitimately exceed it.
MAX_PARTIAL_ALPHA_VISIBLE_PERCENT = 20.0
MAX_LOW_ALPHA_VISIBLE_PERCENT = 5.0


def _percent(part: int, whole: int) -> float:
    return round((part / whole) * 100, 3) if whole else 0.0


def print_readiness_report(
    image: Image.Image,
    *,
    expected_size: tuple[int, int],
    canvas_policy: str,
    upscale_ratio: float = 1.0,
) -> dict[str, Any]:
    """Return a stable, non-AI preflight report for a generated image.

    The report intentionally measures objective pixel properties only. It does not
    claim to judge composition, typography, semantic accuracy, or artistic quality.
    """

    if canvas_policy not in {OPAQUE_FULL_BLEED, PRESERVE_TRANSPARENCY}:
        raise ValueError(f"unsupported canvas policy: {canvas_policy!r}")
    if expected_size[0] <= 0 or expected_size[1] <= 0:
        raise ValueError(f"expected_size must be positive; actual={expected_size!r}")
    if upscale_ratio <= 0:
        raise ValueError(f"upscale_ratio must be positive; actual={upscale_ratio!r}")

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    histogram = alpha.histogram()
    total = rgba.width * rgba.height
    transparent = histogram[0]
    opaque = histogram[255]
    partial = total - transparent - opaque
    visible = total - transparent
    low_alpha = sum(histogram[1:32])
    partial_visible_percent = _percent(partial, visible)
    low_alpha_visible_percent = _percent(low_alpha, visible)

    failures: list[str] = []
    warnings: list[str] = []
    if rgba.size != expected_size:
        failures.append(
            "pixel dimensions do not match the requested canvas; "
            f"expected={expected_size[0]}x{expected_size[1]}; "
            f"actual={rgba.width}x{rgba.height}"
        )
    if visible == 0:
        failures.append("image contains no visible artwork pixels")
    if canvas_policy == OPAQUE_FULL_BLEED and transparent + partial:
        failures.append(
            "opaque full-bleed output contains non-opaque pixels; "
            f"non_opaque_percent={_percent(transparent + partial, total)}"
        )
    if canvas_policy == PRESERVE_TRANSPARENCY and transparent == 0:
        failures.append(
            "transparent-canvas output contains no fully transparent background pixels"
        )
    if visible and partial_visible_percent > MAX_PARTIAL_ALPHA_VISIBLE_PERCENT:
        warnings.append(
            "partially transparent pixels cover too much visible artwork; "
            f"partial_visible_percent={partial_visible_percent}; "
            f"recommended_max={MAX_PARTIAL_ALPHA_VISIBLE_PERCENT}"
        )
    if visible and low_alpha_visible_percent > MAX_LOW_ALPHA_VISIBLE_PERCENT:
        warnings.append(
            "low-alpha fringe covers too much visible artwork; "
            f"low_alpha_visible_percent={low_alpha_visible_percent}; "
            f"recommended_max={MAX_LOW_ALPHA_VISIBLE_PERCENT}"
        )
    if upscale_ratio > 1.0:
        warnings.append(
            "artifact was enlarged from its source and may have soft or blurred "
            f"pixels; upscale_ratio={upscale_ratio:.3f}"
        )

    print_ready = not failures and not warnings
    return {
        "schema": PRINT_READINESS_SCHEMA,
        "print_ready": print_ready,
        "status": "ready" if print_ready else "not-ready",
        "canvas_policy": canvas_policy,
        "expected_size": {
            "width": expected_size[0],
            "height": expected_size[1],
        },
        "actual_size": {"width": rgba.width, "height": rgba.height},
        "dimension_exact": rgba.size == expected_size,
        "upscale_ratio": round(upscale_ratio, 6),
        "pixel_counts": {
            "total": total,
            "visible": visible,
            "opaque": opaque,
            "partial_alpha": partial,
            "low_alpha": low_alpha,
            "transparent": transparent,
        },
        "pixel_percent": {
            "visible": _percent(visible, total),
            "opaque_visible": _percent(opaque, visible),
            "partial_alpha_visible": partial_visible_percent,
            "low_alpha_visible": low_alpha_visible_percent,
            "transparent": _percent(transparent, total),
        },
        "failures": failures,
        "warnings": warnings,
        "operator_follow_up": (
            []
            if print_ready
            else [
                "Inspect the generated image at 100% zoom before approval.",
                "Regenerate or repair the asset if softness, halos, stray pixels, "
                "transparent holes, or broad translucent artwork are visible.",
            ]
        ),
        "scope": (
            "Deterministic pixel preflight only; composition, spelling, semantic "
            "accuracy, and artistic quality still require operator review."
        ),
    }


def print_readiness_report_for_path(
    path: Path,
    *,
    expected_size: tuple[int, int],
    canvas_policy: str,
    upscale_ratio: float = 1.0,
) -> dict[str, Any]:
    """Decode a saved image and run the same preflight used by GUI workflows."""

    resolved = path.expanduser().resolve()
    try:
        with Image.open(resolved) as opened:
            opened.load()
            return print_readiness_report(
                opened,
                expected_size=expected_size,
                canvas_policy=canvas_policy,
                upscale_ratio=upscale_ratio,
            )
    except (OSError, ValueError) as exc:
        return {
            "schema": PRINT_READINESS_SCHEMA,
            "print_ready": False,
            "status": "not-ready",
            "canvas_policy": canvas_policy,
            "expected_size": {
                "width": expected_size[0],
                "height": expected_size[1],
            },
            "actual_size": None,
            "dimension_exact": False,
            "upscale_ratio": round(upscale_ratio, 6),
            "pixel_counts": None,
            "pixel_percent": None,
            "failures": [f"image is not readable: path={resolved}; error={exc}"],
            "warnings": [],
            "operator_follow_up": [
                "Regenerate the artifact before operator approval or publication."
            ],
            "scope": "Deterministic pixel preflight could not decode the image.",
        }


def print_readiness_warning(label: str, report: dict[str, Any]) -> str | None:
    """Format one actionable operator warning without losing report detail."""

    if report.get("print_ready") is True:
        return None
    findings = [
        str(item)
        for item in [*report.get("failures", []), *report.get("warnings", [])]
        if str(item).strip()
    ]
    detail = "; ".join(findings) or "pixel preflight did not pass"
    return (
        f"{label} is not print-ready: {detail}. Inspect at 100% zoom and "
        "regenerate or repair before approval."
    )
