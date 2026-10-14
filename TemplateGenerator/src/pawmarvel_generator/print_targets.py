"""Prepare, review, and package additive multi-output print targets.

The existing bundle-v1 preview and primary print contract stays untouched.  A
reviewed extension adds ``print-targets.json`` plus self-contained
``print/<width>x<height>/`` template directories to a new immutable revision.
"""

from __future__ import annotations

import os
import re
import shutil
import warnings
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from .artifact_io import atomic_json, mismatch, read_json, sha256, utc_now
from .bundle import BundleError, media_type, validate_raster, validate_utc_timestamp
from .config import ConfigError, load_layout
from .production_bundle import validate_production_bundle
from .renderer import ALPHA_THRESHOLD


TARGET_ID = re.compile(r"^(?P<width>[1-9][0-9]*)x(?P<height>[1-9][0-9]*)$")
DEFAULT_ORNAMENT_TARGETS = ("992x992", "1098x1063", "780x1087", "1004x933")


class PrintTargetsError(ValueError):
    """A multi-output candidate or bundle extension is invalid."""


def parse_target(value: str) -> tuple[str, int, int]:
    normalized = value.strip().lower().replace("×", "x").replace("*", "x")
    match = TARGET_ID.fullmatch(normalized)
    if match is None:
        raise PrintTargetsError(
            f"print target must use WIDTHxHEIGHT with positive integers: {value!r}"
        )
    return normalized, int(match.group("width")), int(match.group("height"))


def _json(path: Path, label: str) -> dict[str, Any]:
    return read_json(
        path,
        label=label,
        error_type=PrintTargetsError,
        require_object=True,
        correction="regenerate the print-target candidate before retrying.",
    )


def _centered_box(width: int, height: int, coverage: float) -> dict[str, int]:
    box_width = max(1, round(width * coverage))
    box_height = max(1, round(height * coverage))
    return {
        "x": (width - box_width) // 2,
        "y": (height - box_height) // 2,
        "width": box_width,
        "height": box_height,
    }


def _target_layout(width: int, height: int, coverage: float) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "art": "art.png",
        "pet": {"box": _centered_box(width, height, coverage)},
    }


def _render_centered_target(
    *, template_dir: Path, pet_image: Path, output: Path
) -> None:
    """Render target-local art with visible-alpha contain and center anchoring."""
    layout = load_layout(template_dir)
    with Image.open(layout.art_path) as art_source, Image.open(pet_image) as pet_source:
        canvas = art_source.convert("RGBA")
        pet = pet_source.convert("RGBA")
        alpha = pet.getchannel("A")
        visible = alpha.point(lambda value: 255 if value > ALPHA_THRESHOLD else 0)
        bounds = visible.getbbox()
        if bounds is None:
            raise PrintTargetsError(f"fixture pet is fully transparent: {pet_image}")
        pet = pet.crop(bounds)
        scale = min(
            layout.pet_box.width / pet.width,
            layout.pet_box.height / pet.height,
        )
        fitted_size = (
            max(1, round(pet.width * scale)),
            max(1, round(pet.height * scale)),
        )
        fitted = (
            pet.convert("RGBa")
            .resize(fitted_size, Image.Resampling.LANCZOS)
            .convert("RGBA")
        )
        x = layout.pet_box.x + (layout.pet_box.width - fitted.width) // 2
        y = layout.pet_box.y + (layout.pet_box.height - fitted.height) // 2
        canvas.alpha_composite(fitted, (x, y))
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output, format="PNG")


def _source_bundle_hash(bundle: Path) -> str:
    return sha256(bundle / "bundle.json")


def _selected_pet_attempts(
    *, source_bundle: Path, authoring_product: Path
) -> list[tuple[str, Path]]:
    manifest = validate_production_bundle(source_bundle)
    experiment_id = manifest.get("provenance", {}).get("pet_experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise PrintTargetsError("source bundle does not identify its pet experiment")
    experiment = authoring_product / "experiments" / "pet" / experiment_id
    if not experiment.is_dir():
        raise PrintTargetsError(
            "source bundle pet experiment is unavailable locally; "
            f"expected={experiment.resolve()}"
        )
    attempts: list[tuple[str, Path]] = []
    for run_path in sorted(experiment.glob("attempts/*/run.json")):
        run = _json(run_path, "pet attempt")
        pet = run_path.parent / "outputs" / "transformed-pet.png"
        if run.get("status") == "succeeded" and pet.is_file():
            attempts.append((run_path.parent.name, pet.resolve()))
    if not attempts:
        raise PrintTargetsError(
            "selected pet experiment has no reusable succeeded fixture outputs; "
            f"experiment={experiment.resolve()}"
        )
    return attempts


def _make_contact_sheet(
    *,
    rows: list[tuple[str, list[tuple[str, Path]]]],
    output: Path,
) -> None:
    columns = max((len(cells) for _, cells in rows), default=0)
    if not rows or columns == 0:
        raise PrintTargetsError("contact sheet requires at least one fixture and target")
    thumb = 260
    header = 54
    label_width = 235
    padding = 16
    canvas = Image.new(
        "RGB",
        (label_width + columns * (thumb + padding) + padding, header + len(rows) * (thumb + 54)),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for column, (target_id, _) in enumerate(rows[0][1]):
        x = label_width + padding + column * (thumb + padding)
        draw.text((x, 18), target_id, fill="black", font=font)
    for row_index, (fixture_id, cells) in enumerate(rows):
        y = header + row_index * (thumb + 54)
        draw.text((padding, y + 8), fixture_id, fill="black", font=font)
        for column, (target_id, image_path) in enumerate(cells):
            x = label_width + padding + column * (thumb + padding)
            with Image.open(image_path) as source:
                image = source.convert("RGBA")
                checker = Image.new("RGB", image.size, "#eeeeee")
                checker.paste(image.convert("RGB"), mask=image.getchannel("A"))
                checker.thumbnail((thumb, thumb), Image.Resampling.LANCZOS)
                tile = Image.new("RGB", (thumb, thumb), "#dddddd")
                tile.paste(
                    checker,
                    ((thumb - checker.width) // 2, (thumb - checker.height) // 2),
                )
                canvas.paste(tile, (x, y))
            draw.text((x, y + thumb + 8), target_id, fill="#444444", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG")


def prepare_print_target_candidate(
    *,
    source_bundle: Path,
    authoring_product: Path,
    candidate_id: str,
    targets: Iterable[str] = DEFAULT_ORNAMENT_TARGETS,
    coverage: float = 0.95,
) -> Path:
    """Create a local, API-free review candidate from approved fixture cutouts."""
    source_bundle = source_bundle.expanduser().resolve()
    authoring_product = authoring_product.expanduser().resolve()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", candidate_id):
        raise PrintTargetsError(f"invalid candidate ID: {candidate_id!r}")
    if not 0.5 <= coverage <= 1.0:
        raise PrintTargetsError("coverage must be between 0.5 and 1.0")
    manifest = validate_production_bundle(source_bundle)
    if (
        authoring_product.name != manifest["product_profile_id"]
        or authoring_product.parent.name != manifest["design_id"]
    ):
        raise PrintTargetsError(
            mismatch(
                "source bundle/authoring product identity",
                expected=(manifest["design_id"], manifest["product_profile_id"]),
                actual=(authoring_product.parent.name, authoring_product.name),
            )
        )
    parsed = [parse_target(value) for value in targets]
    if not parsed or len({item[0] for item in parsed}) != len(parsed):
        raise PrintTargetsError("print targets must be a nonempty unique list")
    with Image.open(source_bundle / "print" / "art.png") as primary_art:
        primary = f"{primary_art.width}x{primary_art.height}"
    if primary not in {item[0] for item in parsed}:
        raise PrintTargetsError(
            f"targets must retain the legacy primary print target {primary!r}"
        )
    attempts = _selected_pet_attempts(
        source_bundle=source_bundle, authoring_product=authoring_product
    )
    candidates_root = authoring_product / "print-target-candidates"
    destination = candidates_root / candidate_id
    partial = candidates_root / f".{candidate_id}.partial"
    if destination.exists() or partial.exists():
        raise PrintTargetsError(
            f"print-target candidate already exists or is partial: {destination}"
        )
    candidates_root.mkdir(parents=True, exist_ok=True)
    partial.mkdir()
    try:
        target_entries: list[dict[str, Any]] = []
        compositions: dict[str, list[tuple[str, Path]]] = {
            fixture_id: [] for fixture_id, _ in attempts
        }
        for target_id, width, height in parsed:
            target_dir = partial / "print" / target_id
            target_dir.mkdir(parents=True)
            Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(
                target_dir / "art.png", format="PNG"
            )
            atomic_json(target_dir / "layout.json", _target_layout(width, height, coverage))
            composition_dir = partial / "compositions" / target_id
            composition_dir.mkdir(parents=True)
            for fixture_id, pet in attempts:
                output = composition_dir / f"{fixture_id}.png"
                _render_centered_target(
                    template_dir=target_dir, pet_image=pet, output=output
                )
                compositions[fixture_id].append((target_id, output))
            target_entries.append(
                {
                    "target_id": target_id,
                    "canvas": {"width": width, "height": height},
                    "art": f"print/{target_id}/art.png",
                    "layout": f"print/{target_id}/layout.json",
                    "art_sha256": sha256(target_dir / "art.png"),
                    "layout_sha256": sha256(target_dir / "layout.json"),
                }
            )
        contact_sheet = partial / "artifacts" / "print-target-comparison.png"
        _make_contact_sheet(rows=list(compositions.items()), output=contact_sheet)
        source_manifest = source_bundle / "bundle.json"
        record = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "created_at": utc_now(),
            "status": "pending-review",
            "design_id": manifest["design_id"],
            "product_profile_id": manifest["product_profile_id"],
            "source_bundle": {
                "path": str(source_bundle),
                "template_id": manifest["template_id"],
                "bundle_revision": manifest["bundle_revision"],
                "manifest_sha256": sha256(source_manifest),
            },
            "reuse": {
                "pet_experiment_id": manifest["provenance"]["pet_experiment_id"],
                "fixture_attempt_ids": [item[0] for item in attempts],
            },
            "layout_policy": {
                "coverage": coverage,
                "horizontal_alignment": "center",
                "vertical_alignment": "center",
                "pet_fit": "contain-visible-alpha",
                "pet_anchor": "center",
            },
            "primary_target_id": primary,
            "targets": target_entries,
            "contact_sheet": "artifacts/print-target-comparison.png",
        }
        atomic_json(partial / "candidate.json", record)
        os.replace(partial, destination)
        return destination
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        raise


def _asset(path: Path, root: Path) -> dict[str, Any]:
    value: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "media_type": media_type(path),
    }
    if path.suffix.lower() == ".png":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                value["width"], value["height"] = image.size
    return value


def _next_revision(template_root: Path, product_root: Path, template_id: str) -> int:
    values = [
        int(path.name[1:])
        for path in template_root.glob("v[0-9][0-9][0-9][0-9][0-9][0-9]")
        if path.is_dir()
    ]
    for receipt_path in product_root.glob("graduations/*/publications/*.json"):
        receipt = _json(receipt_path, "publication receipt")
        revision = receipt.get("bundle_revision")
        if (
            receipt.get("template_id") == template_id
            and isinstance(revision, int)
            and not isinstance(revision, bool)
            and revision > 0
        ):
            values.append(revision)
    return max(values, default=0) + 1


def build_multi_output_revision(
    *,
    candidate: Path,
    output_dir: Path,
    approved_by: str,
    approval_notes: str,
) -> Path:
    """Approve a candidate and create a new immutable additive bundle revision."""
    candidate = candidate.expanduser().resolve()
    record = _json(candidate / "candidate.json", "print-target candidate")
    if record.get("status") != "pending-review":
        raise PrintTargetsError("print-target candidate is not pending manual review")
    if not approved_by.strip() or not approval_notes.strip():
        raise PrintTargetsError("approved_by and approval_notes are required")
    existing_approval = candidate / "approval.json"
    if existing_approval.is_file():
        approval = _json(existing_approval, "print-target approval")
        bundle = Path(str(approval.get("bundle", ""))).resolve()
        if (
            approval.get("approved_by") != approved_by.strip()
            or approval.get("notes") != approval_notes.strip()
        ):
            raise PrintTargetsError(
                "candidate is already approved with different operator or notes; "
                f"approval={existing_approval.resolve()}"
            )
        validate_production_bundle(bundle)
        if approval.get("bundle_manifest_sha256") != sha256(bundle / "bundle.json"):
            raise PrintTargetsError(
                f"approved bundle hash no longer matches: {bundle.resolve()}"
            )
        return bundle
    source_bundle = Path(str(record.get("source_bundle", {}).get("path", ""))).resolve()
    source_manifest = validate_production_bundle(source_bundle)
    expected_source = record.get("source_bundle", {})
    actual_source = {
        "template_id": source_manifest["template_id"],
        "bundle_revision": source_manifest["bundle_revision"],
        "manifest_sha256": _source_bundle_hash(source_bundle),
    }
    for key, actual in actual_source.items():
        if expected_source.get(key) != actual:
            raise PrintTargetsError(
                mismatch(
                    f"candidate source bundle {key}",
                    expected=expected_source.get(key),
                    actual=actual,
                )
            )
    output_dir = output_dir.expanduser().resolve()
    template_root = output_dir / source_manifest["template_id"]
    product_root = candidate.parent.parent
    revision = _next_revision(
        template_root, product_root, str(source_manifest["template_id"])
    )
    destination = template_root / f"v{revision:06d}"
    partial = template_root / f".v{revision:06d}.partial"
    if destination.exists() or partial.exists():
        raise PrintTargetsError(f"bundle revision already exists or is partial: {destination}")
    template_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_bundle, partial)
    try:
        for item in record.get("targets", []):
            target_id = str(item.get("target_id", ""))
            parse_target(target_id)
            source = candidate / "print" / target_id
            target = partial / "print" / target_id
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
        print_targets = {
            "schema_version": 1,
            "primary_target_id": record["primary_target_id"],
            "source_bundle": actual_source,
            "layout_policy": record["layout_policy"],
            "targets": record["targets"],
            "approval": {
                "status": "approved",
                "approved_at": utc_now(),
                "approved_by": approved_by.strip(),
                "notes": approval_notes.strip(),
                "candidate_id": record["candidate_id"],
                "candidate_sha256": sha256(candidate / "candidate.json"),
                "contact_sheet_sha256": sha256(
                    candidate / str(record["contact_sheet"])
                ),
            },
        }
        atomic_json(partial / "print-targets.json", print_targets)
        manifest = _json(partial / "bundle.json", "bundle manifest")
        manifest["bundle_revision"] = revision
        manifest["created_at"] = utc_now()
        manifest["assets"] = [
            _asset(path, partial)
            for path in sorted(partial.rglob("*"))
            if path.is_file() and path.name != "bundle.json"
        ]
        atomic_json(partial / "bundle.json", manifest)
        os.replace(partial, destination)
        validate_production_bundle(destination)
        approval = {
            "schema_version": 1,
            "candidate_id": record["candidate_id"],
            "approved_at": print_targets["approval"]["approved_at"],
            "approved_by": approved_by.strip(),
            "notes": approval_notes.strip(),
            "bundle": str(destination),
            "bundle_revision": revision,
            "bundle_manifest_sha256": sha256(destination / "bundle.json"),
        }
        atomic_json(candidate / "approval.json", approval)
        return destination
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        if destination.exists():
            shutil.rmtree(destination)
        raise


def validate_print_targets(bundle_root: Path) -> dict[str, Any] | None:
    """Validate an optional approved multi-output extension in a bundle."""
    bundle_root = bundle_root.expanduser().resolve()
    path = bundle_root / "print-targets.json"
    if not path.exists():
        return None
    value = _json(path, "print targets")
    required = {
        "schema_version",
        "primary_target_id",
        "source_bundle",
        "layout_policy",
        "targets",
        "approval",
    }
    if set(value) != required or value.get("schema_version") != 1:
        raise PrintTargetsError("print-targets.json must match the closed v1 contract")
    policy = value.get("layout_policy")
    if policy != {
        "coverage": 0.95,
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
        "pet_fit": "contain-visible-alpha",
        "pet_anchor": "center",
    }:
        raise PrintTargetsError("print-target layout policy must be centered at 95% coverage")
    approval = value.get("approval")
    approval_fields = {
        "status",
        "approved_at",
        "approved_by",
        "notes",
        "candidate_id",
        "candidate_sha256",
        "contact_sheet_sha256",
    }
    if (
        not isinstance(approval, dict)
        or set(approval) != approval_fields
        or approval.get("status") != "approved"
    ):
        raise PrintTargetsError("published print targets require recorded manual approval")
    try:
        validate_utc_timestamp(approval.get("approved_at"), "print target approved_at")
    except BundleError as exc:
        raise PrintTargetsError(str(exc)) from exc
    for field in ("approved_by", "notes", "candidate_id"):
        if not isinstance(approval.get(field), str) or not approval[field].strip():
            raise PrintTargetsError(f"print target approval {field} must be nonempty")
    for field in ("candidate_sha256", "contact_sheet_sha256"):
        if not isinstance(approval.get(field), str) or re.fullmatch(
            r"[0-9a-f]{64}", approval[field]
        ) is None:
            raise PrintTargetsError(f"print target approval {field} must be SHA-256")
    source = value.get("source_bundle")
    source_fields = {"template_id", "bundle_revision", "manifest_sha256"}
    current = _json(bundle_root / "bundle.json", "bundle manifest")
    if (
        not isinstance(source, dict)
        or set(source) != source_fields
        or source.get("template_id") != current.get("template_id")
        or not isinstance(source.get("bundle_revision"), int)
        or isinstance(source.get("bundle_revision"), bool)
        or source["bundle_revision"] < 1
        or source["bundle_revision"] >= current.get("bundle_revision", 0)
        or not isinstance(source.get("manifest_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", source["manifest_sha256"]) is None
    ):
        raise PrintTargetsError(
            "print target source bundle must identify an earlier revision of the same template"
        )
    targets = value.get("targets")
    if not isinstance(targets, list) or not targets:
        raise PrintTargetsError("print-targets.targets must be a nonempty array")
    seen: set[str] = set()
    for item in targets:
        if not isinstance(item, dict) or set(item) != {
            "target_id",
            "canvas",
            "art",
            "layout",
            "art_sha256",
            "layout_sha256",
        }:
            raise PrintTargetsError("print target entry has unsupported fields")
        target_id, width, height = parse_target(str(item.get("target_id", "")))
        if target_id in seen:
            raise PrintTargetsError(f"duplicate print target: {target_id}")
        seen.add(target_id)
        expected_paths = {
            "art": f"print/{target_id}/art.png",
            "layout": f"print/{target_id}/layout.json",
        }
        if item.get("canvas") != {"width": width, "height": height} or any(
            item.get(key) != expected for key, expected in expected_paths.items()
        ):
            raise PrintTargetsError(f"print target paths/canvas disagree: {target_id}")
        target_dir = bundle_root / "print" / target_id
        art = target_dir / "art.png"
        layout_path = target_dir / "layout.json"
        if item.get("art_sha256") != sha256(art) or item.get("layout_sha256") != sha256(layout_path):
            raise PrintTargetsError(f"print target hash mismatch: {target_id}")
        try:
            layout = load_layout(target_dir)
        except ConfigError as exc:
            raise PrintTargetsError(str(exc)) from exc
        if (layout.canvas_width, layout.canvas_height) != (width, height):
            raise PrintTargetsError(f"print target canvas mismatch: {target_id}")
        try:
            validate_raster(
                art,
                f"print target art {target_id}",
                require_png=True,
                require_alpha=True,
                allow_fully_transparent=True,
                expected_size=(width, height),
                allow_large=True,
            )
        except BundleError as exc:
            raise PrintTargetsError(str(exc)) from exc
        if layout.has_name or layout.pet_box.to_dict() != _centered_box(width, height, 0.95):
            raise PrintTargetsError(
                f"print target layout must contain only a centered 95% pet box: {target_id}"
            )
    if value.get("primary_target_id") not in seen:
        raise PrintTargetsError("primary print target is not present in targets")
    with Image.open(bundle_root / "print" / "art.png") as primary_art:
        legacy_primary = f"{primary_art.width}x{primary_art.height}"
    if value.get("primary_target_id") != legacy_primary:
        raise PrintTargetsError(
            mismatch(
                "primary print target/legacy print art",
                expected=legacy_primary,
                actual=value.get("primary_target_id"),
            )
        )
    return value
