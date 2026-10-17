"""Deterministic pixel-level print-readiness checks for generated PNG assets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter

OPAQUE_FULL_BLEED = "opaque-full-bleed"
PRESERVE_TRANSPARENCY = "preserve-transparency"
PRINT_READINESS_SCHEMA = "pawmarvel-static-print-readiness-v2"

# Alpha bands deliberately separate nearly solid model output from opacity that can
# change the printed result. GPT image output commonly uses alpha 250-254 throughout
# otherwise solid artwork; counting those pixels as equivalent to alpha 1 created
# misleading 99% partial-alpha warnings.
HIGH_RISK_FRINGE_ALPHA = range(1, 32)
MEANINGFUL_TRANSLUCENCY_ALPHA = range(32, 250)
NEAR_OPAQUE_ALPHA = range(250, 255)
SPATIAL_ALPHA_MAX_EDGE = 512

# PawMarvel preflight tolerances, not printer guarantees. They permit a bounded
# antialiased edge while reflecting the different behavior of white-underbase
# garment printing, sublimation, opaque paper/canvas, and sticker production.
PRINT_PROCESS_POLICIES: dict[str, dict[str, Any]] = {
    "dtg-dtf-apparel": {
        "label": "DTG/DTF apparel",
        "max_meaningful_percent": 1.0,
        "max_boundary_fringe_percent": 0.5,
        "max_interior_fringe_percent": 0.25,
        "pass_reason": (
            "bounded antialiasing is within the apparel tolerance; near-opaque "
            "pixels are acceptable"
        ),
        "fail_reason": (
            "partial alpha can trigger a white underbase or transfer adhesive and "
            "produce a pale halo on colored or dark garments; harden the outer edge "
            "or replace intentional fades with opaque halftones"
        ),
    },
    "dye-sublimation": {
        "label": "Dye-sublimation products",
        "max_meaningful_percent": 20.0,
        "max_boundary_fringe_percent": 10.0,
        "max_interior_fringe_percent": 5.0,
        "pass_reason": (
            "the measured translucency is acceptable for transfer onto a light or "
            "white sublimation substrate; confirm that any fade is intentional"
        ),
        "fail_reason": (
            "broad translucency or fringe can print weak, washed out, or soft; "
            "flatten intentional fades against the target substrate or repair the edge"
        ),
    },
    "paper-canvas": {
        "label": "Paper/canvas inkjet",
        "max_meaningful_percent": 20.0,
        "max_boundary_fringe_percent": 10.0,
        "max_interior_fringe_percent": 5.0,
        "pass_reason": (
            "the measured translucency is acceptable when composited onto the "
            "intended opaque paper or canvas color"
        ),
        "fail_reason": (
            "broad translucency can weaken color and detail after compositing; flatten "
            "against the intended substrate and inspect at final print size"
        ),
    },
    "sticker": {
        "label": "Sticker printing",
        "max_meaningful_percent": 5.0,
        "max_boundary_fringe_percent": 1.0,
        "max_interior_fringe_percent": 0.5,
        "pass_reason": (
            "the bounded edge opacity is acceptable for a white-base sticker workflow; "
            "verify the vendor cut line and white-ink policy"
        ),
        "fail_reason": (
            "low-opacity edge pixels can create a weak halo or ambiguous white-ink/cut "
            "edge; clean the silhouette and confirm the vendor cut-line contract"
        ),
    },
}

PRODUCT_PRINT_PROCESS = {
    "beach-towel": "dye-sublimation",
    "coffee-mug-11oz": "dye-sublimation",
    "fleece-sherpa-blankets": "dye-sublimation",
    "mens-tee-house-style": "dtg-dtf-apparel",
    "mousepad": "dye-sublimation",
    "ornament": "dye-sublimation",
    "pillow": "dye-sublimation",
    "poster-modern-portrait": "paper-canvas",
    "sticker": "sticker",
    "tumbler-20oz": "dye-sublimation",
    "unisex-tee-crewneck-hoodie-tanks": "dtg-dtf-apparel",
    "womens-tee-bella-6400": "dtg-dtf-apparel",
    "wrapped-canvas": "paper-canvas",
}


def _percent(part: int, whole: int) -> float:
    return round((part / whole) * 100, 3) if whole else 0.0


def _spatial_fringe_breakdown(
    alpha: Image.Image, high_risk_fringe: int
) -> tuple[int, int, dict[str, Any]]:
    """Estimate edge/interior context at a normalized, bounded analysis size."""

    sampled = alpha.copy()
    if max(sampled.size) > SPATIAL_ALPHA_MAX_EDGE:
        sampled.thumbnail(
            (SPATIAL_ALPHA_MAX_EDGE, SPATIAL_ALPHA_MAX_EDGE),
            Image.Resampling.NEAREST,
        )
    sampled_high = sampled.point(
        lambda value: 255 if value in HIGH_RISK_FRINGE_ALPHA else 0
    )
    sampled_high_count = sum(
        sampled_high.histogram()[value] for value in range(1, 256)
    )
    sampled_transparent = sampled.point(lambda value: 255 if value == 0 else 0)
    transparent_neighborhood = sampled_transparent.filter(ImageFilter.MaxFilter(5))
    sampled_boundary = ImageChops.multiply(
        sampled_high, transparent_neighborhood
    ).histogram()[255]
    boundary_share = (
        sampled_boundary / sampled_high_count if sampled_high_count else 0.0
    )
    boundary = round(high_risk_fringe * boundary_share)
    interior = high_risk_fringe - boundary
    return boundary, interior, {
        "normalized_max_edge": SPATIAL_ALPHA_MAX_EDGE,
        "sample_size": {"width": sampled.width, "height": sampled.height},
        "source_size": {"width": alpha.width, "height": alpha.height},
        "sampled": sampled.size != alpha.size,
        "boundary_distance_pixels_at_sample_size": 2,
        "method": "nearest-neighbor normalized alpha mask",
    }


def _process_report(
    process: str,
    *,
    meaningful_percent: float,
    boundary_fringe_percent: float,
    interior_fringe_percent: float,
    product_profile_id: str | None = None,
) -> dict[str, Any]:
    policy = PRINT_PROCESS_POLICIES[process]
    meaningful_limit = float(policy["max_meaningful_percent"])
    boundary_limit = float(policy["max_boundary_fringe_percent"])
    interior_limit = float(policy["max_interior_fringe_percent"])
    passed = (
        meaningful_percent <= meaningful_limit
        and boundary_fringe_percent <= boundary_limit
        and interior_fringe_percent <= interior_limit
    )
    measured = (
        f"meaningful={meaningful_percent}% (max {meaningful_limit}%); "
        f"boundary fringe={boundary_fringe_percent}% (max {boundary_limit}%); "
        f"interior low-alpha={interior_fringe_percent}% (max {interior_limit}%)"
    )
    reason = str(policy["pass_reason"] if passed else policy["fail_reason"])
    return {
        "product_profile_id": product_profile_id,
        "print_process": process,
        "label": policy["label"],
        "pass": passed,
        "status": "pass" if passed else "fail",
        "limits": {
            "meaningful_translucency_visible_percent": meaningful_limit,
            "boundary_high_risk_fringe_visible_percent": boundary_limit,
            "interior_high_risk_fringe_visible_percent": interior_limit,
        },
        "summary": f"{'PASS' if passed else 'FAIL'} — {measured}; {reason}.",
    }


def _product_compatibility(
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize profile results without treating one source as globally unsafe."""

    valid = [
        report
        for report in reports
        if isinstance(report.get("product_profile_id"), str)
        and isinstance(report.get("pass"), bool)
    ]
    passed = sorted(
        str(report["product_profile_id"])
        for report in valid
        if report["pass"] is True
    )
    review = sorted(
        str(report["product_profile_id"])
        for report in valid
        if report["pass"] is False
    )
    groups: list[dict[str, Any]] = []
    grouped: dict[tuple[str, bool], dict[str, Any]] = {}
    for report in valid:
        key = (str(report.get("print_process", "unknown")), bool(report["pass"]))
        group = grouped.setdefault(
            key,
            {
                "print_process": key[0],
                "label": str(report.get("label", key[0])),
                "pass": key[1],
                "status": "pass" if key[1] else "review",
                "product_profile_ids": [],
                "summary": str(report.get("summary", "")),
            },
        )
        group["product_profile_ids"].append(str(report["product_profile_id"]))
    for group in grouped.values():
        group["product_profile_ids"].sort()
        groups.append(group)
    groups.sort(key=lambda item: (not item["pass"], item["print_process"]))
    status = "pass" if not review else "review"
    summary_parts = [
        f"PASS profiles: {', '.join(passed)}" if passed else "PASS profiles: none",
        (
            f"REVIEW profiles: {', '.join(review)}"
            if review
            else "REVIEW profiles: none"
        ),
    ]
    return {
        "status": status,
        "pass_product_profile_ids": passed,
        "review_product_profile_ids": review,
        "groups": groups,
        "summary": "; ".join(summary_parts),
    }


def _unreadable_report(
    *,
    expected_size: tuple[int, int],
    canvas_policy: str,
    upscale_ratio: float,
    error: str,
) -> dict[str, Any]:
    return {
        "schema": PRINT_READINESS_SCHEMA,
        "print_ready": False,
        "status": "not-ready",
        "canvas_policy": canvas_policy,
        "expected_size": {"width": expected_size[0], "height": expected_size[1]},
        "actual_size": None,
        "dimension_exact": False,
        "upscale_ratio": round(upscale_ratio, 6),
        "pixel_counts": None,
        "pixel_percent": None,
        "alpha_summary": "alpha categories unavailable because the image is unreadable",
        "alpha_categories": [],
        "process_reports": [],
        "product_reports": [],
        "product_report": None,
        "product_compatibility": {
            "status": "unavailable",
            "pass_product_profile_ids": [],
            "review_product_profile_ids": [],
            "groups": [],
            "summary": "Product compatibility is unavailable because the image is unreadable.",
        },
        "failures": [error],
        "warnings": [],
        "operator_follow_up": [
            "Regenerate the artifact before operator approval or publication."
        ],
        "scope": "Deterministic pixel preflight could not decode the image.",
    }


def print_readiness_report(
    image: Image.Image,
    *,
    expected_size: tuple[int, int],
    canvas_policy: str,
    upscale_ratio: float = 1.0,
    product_profile_id: str | None = None,
    print_process: str | None = None,
) -> dict[str, Any]:
    """Return a stable, non-AI preflight report for a generated image."""

    if canvas_policy not in {OPAQUE_FULL_BLEED, PRESERVE_TRANSPARENCY}:
        raise ValueError(f"unsupported canvas policy: {canvas_policy!r}")
    if expected_size[0] <= 0 or expected_size[1] <= 0:
        raise ValueError(f"expected_size must be positive; actual={expected_size!r}")
    if upscale_ratio <= 0:
        raise ValueError(f"upscale_ratio must be positive; actual={upscale_ratio!r}")
    if print_process is None and product_profile_id is not None:
        print_process = PRODUCT_PRINT_PROCESS.get(product_profile_id)
    if print_process is not None and print_process not in PRINT_PROCESS_POLICIES:
        raise ValueError(
            f"unsupported print process: {print_process!r}; "
            f"allowed={sorted(PRINT_PROCESS_POLICIES)}"
        )

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    histogram = alpha.histogram()
    total = rgba.width * rgba.height
    transparent = histogram[0]
    opaque = histogram[255]
    high_risk_fringe = sum(histogram[value] for value in HIGH_RISK_FRINGE_ALPHA)
    meaningful = sum(
        histogram[value] for value in MEANINGFUL_TRANSLUCENCY_ALPHA
    )
    near_opaque = sum(histogram[value] for value in NEAR_OPAQUE_ALPHA)
    partial = high_risk_fringe + meaningful + near_opaque
    visible = total - transparent
    partial_visible_percent = _percent(partial, visible)
    near_opaque_percent = _percent(near_opaque, visible)
    meaningful_percent = _percent(meaningful, visible)
    fringe_percent = _percent(high_risk_fringe, visible)
    # Normalize large outputs before the spatial split so the analysis cost is
    # bounded and a two-pixel neighborhood means the same thing at every print size.
    boundary_high_risk, interior_high_risk, spatial_analysis = (
        _spatial_fringe_breakdown(alpha, high_risk_fringe)
    )
    boundary_fringe_percent = _percent(boundary_high_risk, visible)
    interior_fringe_percent = _percent(interior_high_risk, visible)
    boundary_share = _percent(boundary_high_risk, high_risk_fringe)

    alpha_summary = (
        f"near-opaque={near_opaque_percent}% (acceptable); "
        f"meaningful translucency={meaningful_percent}% (method-dependent); "
        f"high-risk fringe={fringe_percent}% "
        f"(boundary={boundary_fringe_percent}%, interior={interior_fringe_percent}%)"
    )
    alpha_categories = [
        {
            "category": "near-opaque",
            "alpha_range": "250-254",
            "pixel_count": near_opaque,
            "visible_percent": near_opaque_percent,
            "assessment": "acceptable",
            "recommendation": (
                "Accept for printing; optionally normalize alpha 250-254 to 255 "
                "during final export for deterministic solid ink coverage."
            ),
        },
        {
            "category": "meaningful-translucency",
            "alpha_range": "32-249",
            "pixel_count": meaningful,
            "visible_percent": meaningful_percent,
            "assessment": "method-dependent" if meaningful else "acceptable",
            "recommendation": (
                "Evaluate against the target print process; retain only when the fade "
                "or translucent texture is intentional."
                if meaningful
                else "No meaningful translucency detected."
            ),
        },
        {
            "category": "high-risk-fringe",
            "alpha_range": "1-31",
            "pixel_count": high_risk_fringe,
            "visible_percent": fringe_percent,
            "assessment": "method-dependent" if high_risk_fringe else "acceptable",
            "recommendation": (
                "Boundary fringe is usually antialiasing; interior low-alpha pixels "
                "are more likely to be a wash, hole, or dirty pixel. Inspect both on "
                "light and dark backgrounds and harden them for white-underbase printing."
                if high_risk_fringe
                else "No high-risk fringe detected."
            ),
            "breakdown": {
                "boundary_pixel_count": boundary_high_risk,
                "boundary_visible_percent": boundary_fringe_percent,
                "boundary_share_percent": boundary_share,
                "interior_pixel_count": interior_high_risk,
                "interior_visible_percent": interior_fringe_percent,
                "estimated_from_normalized_sample": spatial_analysis["sampled"],
            },
        },
    ]
    process_reports = [
        _process_report(
            process,
            meaningful_percent=meaningful_percent,
            boundary_fringe_percent=boundary_fringe_percent,
            interior_fringe_percent=interior_fringe_percent,
        )
        for process in PRINT_PROCESS_POLICIES
    ]
    product_reports = [
        _process_report(
            process,
            meaningful_percent=meaningful_percent,
            boundary_fringe_percent=boundary_fringe_percent,
            interior_fringe_percent=interior_fringe_percent,
            product_profile_id=profile_id,
        )
        for profile_id, process in PRODUCT_PRINT_PROCESS.items()
    ]
    product_report = (
        _process_report(
            print_process,
            meaningful_percent=meaningful_percent,
            boundary_fringe_percent=boundary_fringe_percent,
            interior_fringe_percent=interior_fringe_percent,
            product_profile_id=product_profile_id,
        )
        if print_process is not None
        else None
    )
    product_compatibility = _product_compatibility(product_reports)

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
    if product_report is not None and not product_report["pass"]:
        warnings.append(
            f"product alpha compatibility failed for {product_profile_id or print_process}; "
            f"{product_report['summary']}"
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
        "expected_size": {"width": expected_size[0], "height": expected_size[1]},
        "actual_size": {"width": rgba.width, "height": rgba.height},
        "dimension_exact": rgba.size == expected_size,
        "upscale_ratio": round(upscale_ratio, 6),
        "pixel_counts": {
            "total": total,
            "visible": visible,
            "opaque": opaque,
            "partial_alpha": partial,
            "near_opaque": near_opaque,
            "meaningful_translucency": meaningful,
            "high_risk_fringe": high_risk_fringe,
            "boundary_high_risk_fringe": boundary_high_risk,
            "interior_high_risk_fringe": interior_high_risk,
            "low_alpha": high_risk_fringe,
            "transparent": transparent,
        },
        "pixel_percent": {
            "visible": _percent(visible, total),
            "opaque_visible": _percent(opaque, visible),
            "partial_alpha_visible": partial_visible_percent,
            "near_opaque_visible": near_opaque_percent,
            "meaningful_translucency_visible": meaningful_percent,
            "high_risk_fringe_visible": fringe_percent,
            "boundary_high_risk_fringe_visible": boundary_fringe_percent,
            "interior_high_risk_fringe_visible": interior_fringe_percent,
            "boundary_high_risk_fringe_share": boundary_share,
            "low_alpha_visible": fringe_percent,
            "transparent": _percent(transparent, total),
        },
        "alpha_summary": alpha_summary,
        "alpha_categories": alpha_categories,
        "spatial_alpha_analysis": spatial_analysis,
        "process_reports": process_reports,
        "product_reports": product_reports,
        "product_report": product_report,
        "product_compatibility": product_compatibility,
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
            "Deterministic pixel preflight with PawMarvel process tolerances only; "
            "vendor proofing, composition, spelling, semantic accuracy, and artistic "
            "quality still require operator review."
        ),
    }


def print_readiness_report_for_path(
    path: Path,
    *,
    expected_size: tuple[int, int],
    canvas_policy: str,
    upscale_ratio: float = 1.0,
    product_profile_id: str | None = None,
    print_process: str | None = None,
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
                product_profile_id=product_profile_id,
                print_process=print_process,
            )
    except (OSError, ValueError) as exc:
        return _unreadable_report(
            expected_size=expected_size,
            canvas_policy=canvas_policy,
            upscale_ratio=upscale_ratio,
            error=f"image is not readable: path={resolved}; error={exc}",
        )


def print_readiness_warning(label: str, report: dict[str, Any]) -> str | None:
    """Format one actionable operator warning without losing report detail."""

    product_report = report.get("product_report")
    compatibility = report.get("product_compatibility")
    failed_profiles = (
        compatibility.get("review_product_profile_ids", [])
        if product_report is None and isinstance(compatibility, dict)
        else []
    )
    if report.get("print_ready") is True and not failed_profiles:
        return None
    alpha_summary = str(report.get("alpha_summary", "")).strip()
    if report.get("print_ready") is True and failed_profiles:
        compatibility_summary = str(compatibility.get("summary", "")).strip()
        detail = "; ".join(
            part for part in (alpha_summary, compatibility_summary) if part
        )
        return (
            f"{label} passed intrinsic pixel checks but alpha compatibility varies "
            f"by product: {detail}. Review the target product result before product "
            "approval; near-opaque pixels are acceptable, while meaningful "
            "translucency and high-risk fringe are process-dependent."
        )
    findings = [
        str(item)
        for item in [*report.get("failures", []), *report.get("warnings", [])]
        if str(item).strip()
    ]
    detail = "; ".join(findings) or "pixel preflight did not pass"
    if alpha_summary:
        detail = f"{alpha_summary}; {detail}"
    if isinstance(product_report, dict):
        product_id = product_report.get("product_profile_id") or product_report.get(
            "label", "selected product"
        )
        return (
            f"{label} needs product-specific alpha review for {product_id}: {detail}. "
            "Inspect at 100% zoom and regenerate or repair before product approval."
        )
    return (
        f"{label} is not print-ready: {detail}. Inspect at 100% zoom and "
        "regenerate or repair before approval."
    )
