from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from . import __version__
from .authoring import design_canvas_policy, render_target, save_png
from .core import (
    ProductProfile,
    StaticPrintError,
    bundle_id_for,
    clean_macos_metadata,
    design_authoring_path,
    design_identity,
    design_source_path,
    image_info,
    product_profile_sha256,
    read_json,
    sha256_file,
    utc_now,
    validate_design_id,
    validate_id,
    validate_release_id,
    validate_version,
    write_json,
)
from .lifecycle import transparency_report


@dataclass(frozen=True)
class BundleSelection:
    design_id: str
    version_id: str
    profile: ProductProfile


@dataclass(frozen=True)
class ReleasePlan:
    release_id: str
    staging_root: Path
    catalog_path: Path
    exchange_catalog_path: Path
    bundle_manifests: tuple[Path, ...]


def parse_selection(value: str, profiles: dict[str, ProductProfile]) -> BundleSelection:
    parts = value.split(":")
    if len(parts) != 3:
        raise StaticPrintError(
            "selection must be design-id:version-id:product-profile-id; "
            f"actual={value!r}"
        )
    design_id = validate_design_id(parts[0], "design ID")
    version_id = validate_version(parts[1])
    profile_id = validate_id(parts[2], "product profile ID")
    profile = profiles.get(profile_id)
    if profile is None:
        raise StaticPrintError(
            f"unknown product profile {profile_id!r}; available={sorted(profiles)}"
        )
    return BundleSelection(design_id, version_id, profile)


def _asset(path: Path, relative: str) -> dict[str, Any]:
    width, height, _ = image_info(path)
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "media_type": "image/png",
        "width": width,
        "height": height,
    }


def _local_print_path(
    authoring: Path,
    design_id: str,
    profile: ProductProfile,
    target_id: str,
) -> Path:
    if len(profile.targets) == 1:
        return authoring / f"{design_id}--{profile.profile_id}.png"
    return authoring / f"{design_id}--{profile.profile_id}--{target_id}.png"


def _require_canvas_policy(path: Path, canvas_policy: str, label: str) -> None:
    if canvas_policy not in {"opaque-full-bleed", "preserve-transparency"}:
        raise StaticPrintError(f"unsupported canvas policy: {canvas_policy!r}")
    report = transparency_report(path)
    if canvas_policy == "opaque-full-bleed" and report["has_transparent_pixels"]:
        raise StaticPrintError(
            f"{label} violates opaque full-bleed canvas policy: {path.resolve()}; "
            f"transparent_pixel_percent={report['transparent_pixel_percent']}; "
            f"transparent_border_percent={report['transparent_border_percent']}"
        )


def _legacy_bundle_created_at(
    exchange_root: Path,
    *,
    bundle_id: str,
    design_id: str,
    product_profile_id: str,
    version_id: str,
) -> str | None:
    """Recover the timestamp used by bundles created before generation timestamps."""

    releases = exchange_root / "releases"
    if not releases.is_dir():
        return None
    for catalog_path in sorted(releases.glob("*/catalog.json")):
        try:
            catalog = read_json(catalog_path)
        except StaticPrintError:
            continue
        created_at = catalog.get("created_at")
        if (
            catalog.get("schema") != "pawmarvel-static-release-catalog-v1"
            or not isinstance(created_at, str)
            or not created_at
        ):
            continue
        entries = catalog.get("bundles")
        if not isinstance(entries, list):
            continue
        if any(
            isinstance(entry, dict)
            and entry.get("bundle_id") == bundle_id
            and entry.get("design_id") == design_id
            and entry.get("product_profile_id") == product_profile_id
            and entry.get("version_id") == version_id
            for entry in entries
        ):
            return created_at
    return None


def _bundle_created_at(
    *,
    generation_path: Path,
    exchange_root: Path,
    bundle_id: str,
    design_id: str,
    product_profile_id: str,
    version_id: str,
    fallback: str,
) -> str:
    """Lock a stable bundle timestamp to the immutable generated product version."""

    generation = read_json(generation_path)
    created_at = generation.get("created_at")
    if created_at is not None:
        if not isinstance(created_at, str) or not created_at:
            raise StaticPrintError(
                f"generation created_at is invalid: {generation_path.resolve()}"
            )
        return created_at
    created_at = _legacy_bundle_created_at(
        exchange_root,
        bundle_id=bundle_id,
        design_id=design_id,
        product_profile_id=product_profile_id,
        version_id=version_id,
    ) or fallback
    generation["created_at"] = created_at
    write_json(generation_path, generation)
    return created_at


def build_bundle(
    *,
    inputs_root: Path,
    authoring_root: Path,
    staging_root: Path,
    selection: BundleSelection,
    created_at: str,
) -> Path:
    design_id = design_identity(selection.design_id).design_id
    version_id = validate_version(selection.version_id)
    profile = selection.profile
    bundle_id = bundle_id_for(design_id, profile.profile_id)
    local = (
        design_authoring_path(authoring_root, design_id, inputs_root=inputs_root)
        / profile.profile_id
        / version_id
    )
    operator_preview = local / f"{design_id}-preview.png"
    source = design_source_path(inputs_root, design_id)
    generation_path = local / "generation.json"
    if not operator_preview.is_file():
        raise StaticPrintError(
            f"generated preview is missing: {operator_preview.resolve()}; "
            "graduate and generate this design first"
        )
    if not source.is_file():
        raise StaticPrintError(f"source design is missing: {source.resolve()}")
    generation = read_json(generation_path)
    canvas_policy = design_canvas_policy(inputs_root, design_id)
    if (
        generation.get("schema") != "pawmarvel-static-generation-v3"
        or generation.get("design_id") != design_id
        or generation.get("product_profile_id") != profile.profile_id
        or generation.get("product_profile_sha256") != product_profile_sha256(profile)
        or generation.get("version_id") != version_id
        or generation.get("source_sha256") != sha256_file(source)
        or generation.get("fit") != "cover"
        or generation.get("composition", "cover") != profile.composition
        or generation.get("canvas_policy") != canvas_policy
    ):
        raise StaticPrintError(
            f"generated version does not match the current subject preview/profile: "
            f"record={generation_path.resolve()}; source={source.resolve()}; "
            f"profile={profile.profile_id!r}; regenerate with a new version ID"
        )
    bundle = staging_root / "bundles" / bundle_id / version_id
    if bundle.exists():
        shutil.rmtree(bundle)
    (bundle / "print").mkdir(parents=True)
    preview = bundle / "preview.png"
    first_target = profile.targets[0]
    preview_source = _local_print_path(
        local, design_id, profile, first_target.target_id
    )
    if not preview_source.is_file():
        raise StaticPrintError(
            f"generated preview source target is missing: {preview_source.resolve()}; "
            "graduate and generate this profile first"
        )
    with Image.open(preview_source) as opened:
        opened.load()
        rendered_preview = render_target(
            opened,
            size=(profile.preview_width, profile.preview_height),
            fit="cover",
            background=first_target.background,
        )
        save_png(rendered_preview, preview)
    _require_canvas_policy(preview, canvas_policy, "bundle preview")
    assets = [_asset(preview, "preview.png")]
    target_records: list[dict[str, Any]] = []
    for target in profile.targets:
        local_print = _local_print_path(local, design_id, profile, target.target_id)
        if not local_print.is_file():
            raise StaticPrintError(
                f"generated print target is missing: {local_print.resolve()}; "
                "graduate and generate this profile first"
            )
        destination = bundle / "print" / f"{target.target_id}.png"
        shutil.copy2(local_print, destination)
        _require_canvas_policy(
            destination, canvas_policy, f"bundle print target {target.target_id!r}"
        )
        width, height, _ = image_info(destination)
        if (width, height) != (target.width, target.height):
            raise StaticPrintError(
                f"print target dimension mismatch: {local_print.resolve()}; "
                f"expected={target.width}x{target.height}; actual={width}x{height}"
            )
        relative = f"print/{target.target_id}.png"
        target_record: dict[str, Any] = {
            "target_id": target.target_id,
            "path": relative,
            "width": target.width,
            "height": target.height,
            "dpi": target.dpi,
            "color_space": target.color_space,
            "background": target.background,
        }
        if target.physical_width_inches is not None:
            target_record["physical_width_inches"] = target.physical_width_inches
        if target.physical_height_inches is not None:
            target_record["physical_height_inches"] = target.physical_height_inches
        target_records.append(target_record)
        assets.append(_asset(destination, relative))
    preview_width, preview_height, _ = image_info(preview)
    if (preview_width, preview_height) != (
        profile.preview_width,
        profile.preview_height,
    ):
        raise StaticPrintError(
            f"generated preview dimension mismatch: {preview}; "
            f"expected={profile.preview_width}x{profile.preview_height}; "
            f"actual={preview_width}x{preview_height}"
        )
    manifest = {
        "schema": "pawmarvel-static-bundle-v1",
        "bundle_type": "static-design",
        "bundle_id": bundle_id,
        "design_id": design_id,
        "product_profile_id": profile.profile_id,
        "version_id": version_id,
        "created_at": created_at,
        "source_sha256": sha256_file(source),
        "preview": {
            "path": "preview.png",
            "width": profile.preview_width,
            "height": profile.preview_height,
        },
        "print_targets": target_records,
        "provenance": {"generator_version": __version__},
        "assets": sorted(assets, key=lambda item: item["path"]),
    }
    manifest_path = bundle / "bundle.json"
    write_json(manifest_path, manifest)
    validate_bundle(bundle, canvas_policy=canvas_policy)
    return manifest_path


def validate_bundle(
    bundle: Path, *, canvas_policy: str | None = None
) -> dict[str, Any]:
    root = bundle.resolve()
    clean_macos_metadata(root)
    manifest_path = root / "bundle.json"
    manifest = read_json(manifest_path)
    required = {
        "schema",
        "bundle_type",
        "bundle_id",
        "design_id",
        "product_profile_id",
        "version_id",
        "created_at",
        "source_sha256",
        "preview",
        "print_targets",
        "provenance",
        "assets",
    }
    if set(manifest) != required:
        raise StaticPrintError(
            f"bundle manifest fields mismatch: {manifest_path}; "
            f"missing={sorted(required - set(manifest))}; "
            f"unknown={sorted(set(manifest) - required)}"
        )
    if (
        manifest["schema"] != "pawmarvel-static-bundle-v1"
        or manifest["bundle_type"] != "static-design"
    ):
        raise StaticPrintError(f"unsupported static bundle contract: {manifest_path}")
    if not isinstance(manifest["created_at"], str) or not manifest["created_at"]:
        raise StaticPrintError(f"bundle created_at is invalid: {manifest_path}")
    source_digest = manifest["source_sha256"]
    if (
        not isinstance(source_digest, str)
        or len(source_digest) != 64
        or any(character not in "0123456789abcdef" for character in source_digest)
    ):
        raise StaticPrintError(f"bundle source_sha256 is invalid: {manifest_path}")
    provenance = manifest["provenance"]
    if (
        not isinstance(provenance, dict)
        or set(provenance) != {"generator_version"}
        or not isinstance(provenance["generator_version"], str)
        or not provenance["generator_version"]
    ):
        raise StaticPrintError(f"bundle provenance is invalid: {manifest_path}")
    design_id = validate_design_id(manifest["design_id"], "design ID")
    profile_id = validate_id(manifest["product_profile_id"], "product profile ID")
    validate_version(manifest["version_id"])
    expected_bundle_id = bundle_id_for(design_id, profile_id)
    if manifest["bundle_id"] != expected_bundle_id:
        raise StaticPrintError(
            f"bundle identity mismatch: {manifest_path}; expected={expected_bundle_id!r}; "
            f"actual={manifest['bundle_id']!r}"
        )
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise StaticPrintError(
            f"bundle assets must be a non-empty array: {manifest_path}"
        )
    actual_files = sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file() and path.name != "bundle.json"
    )
    declared_paths = [item.get("path") for item in assets if isinstance(item, dict)]
    if sorted(declared_paths) != actual_files or len(set(declared_paths)) != len(
        declared_paths
    ):
        raise StaticPrintError(
            f"bundle asset inventory mismatch: {manifest_path}; "
            f"expected={actual_files}; actual={declared_paths}"
        )
    descriptors: dict[str, dict[str, Any]] = {}
    for item in assets:
        if not isinstance(item, dict):
            raise StaticPrintError(f"bundle asset must be an object: {manifest_path}")
        path = root / str(item["path"])
        expected = _asset(path, str(item["path"]))
        if item != expected:
            raise StaticPrintError(
                f"bundle asset descriptor mismatch: {path}; expected={expected}; actual={item}"
            )
        descriptors[str(item["path"])] = item
    preview = manifest.get("preview")
    if (
        not isinstance(preview, dict)
        or set(preview) != {"path", "width", "height"}
        or preview.get("path") != "preview.png"
    ):
        raise StaticPrintError(f"bundle preview contract is invalid: {manifest_path}")
    preview_asset = descriptors.get("preview.png")
    if preview_asset is None or (preview.get("width"), preview.get("height")) != (
        preview_asset["width"],
        preview_asset["height"],
    ):
        raise StaticPrintError(f"bundle preview dimensions mismatch: {manifest_path}")
    if canvas_policy is not None:
        _require_canvas_policy(root / "preview.png", canvas_policy, "bundle preview")
    targets = manifest.get("print_targets")
    if not isinstance(targets, list) or not targets:
        raise StaticPrintError(
            f"bundle print_targets must be non-empty: {manifest_path}"
        )
    target_ids: set[str] = set()
    for target in targets:
        if not isinstance(target, dict):
            raise StaticPrintError(
                f"bundle print target must be an object: {manifest_path}"
            )
        required_target = {
            "target_id",
            "path",
            "width",
            "height",
            "dpi",
            "color_space",
            "background",
        }
        optional_target = {"physical_width_inches", "physical_height_inches"}
        if not required_target <= set(target) or not set(target) <= (
            required_target | optional_target
        ):
            raise StaticPrintError(
                f"bundle print target fields mismatch: {manifest_path}; "
                f"missing={sorted(required_target - set(target))}; "
                f"unknown={sorted(set(target) - required_target - optional_target)}"
            )
        target_id = validate_id(target.get("target_id"), "target ID")
        if target_id in target_ids:
            raise StaticPrintError(
                f"duplicate bundle target {target_id!r}: {manifest_path}"
            )
        target_ids.add(target_id)
        path = target.get("path")
        expected_path = f"print/{target_id}.png"
        if path != expected_path:
            raise StaticPrintError(
                f"bundle target path mismatch: {manifest_path}; "
                f"expected={expected_path!r}; actual={path!r}"
            )
        if not isinstance(target.get("dpi"), int) or target["dpi"] <= 0:
            raise StaticPrintError(
                f"bundle target {target_id!r} dpi must be a positive integer: {manifest_path}"
            )
        if target.get("color_space") != "sRGB":
            raise StaticPrintError(
                f"bundle target {target_id!r} color_space must be sRGB: {manifest_path}"
            )
        background = target.get("background")
        if not isinstance(background, str) or not (
            background == "transparent"
            or (
                len(background) == 7
                and background.startswith("#")
                and all(
                    character in "0123456789abcdefABCDEF"
                    for character in background[1:]
                )
            )
        ):
            raise StaticPrintError(
                f"bundle target {target_id!r} background is invalid: {manifest_path}"
            )
        for field in optional_target:
            value = target.get(field)
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value <= 0
            ):
                raise StaticPrintError(
                    f"bundle target {target_id!r} {field} must be positive: {manifest_path}"
                )
        asset = descriptors.get(path)
        if asset is None:
            raise StaticPrintError(
                f"bundle target {target_id!r} references an untracked asset: {path!r}"
            )
        if (target.get("width"), target.get("height")) != (
            asset["width"],
            asset["height"],
        ):
            raise StaticPrintError(
                f"bundle target {target_id!r} dimensions mismatch: {manifest_path}"
            )
        if canvas_policy is not None:
            _require_canvas_policy(
                root / str(path),
                canvas_policy,
                f"bundle print target {target_id!r}",
            )
    return manifest


def prepare_release(
    *,
    release_id: str,
    selections: Iterable[BundleSelection],
    inputs_root: Path,
    authoring_root: Path,
    exchange_root: Path,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> ReleasePlan:
    release_id = validate_release_id(release_id)
    selected = list(selections)
    if not selected:
        raise StaticPrintError("at least one bundle selection is required")
    identity = [
        (item.design_id, item.profile.profile_id, item.version_id) for item in selected
    ]
    if len(set(identity)) != len(identity):
        raise StaticPrintError(f"duplicate release selection: {identity}")
    receipt = exchange_root / "publications" / f"{release_id}.json"
    if receipt.exists():
        raise StaticPrintError(
            f"release {release_id!r} has already been published: {receipt.resolve()}"
        )
    exchange_catalog = exchange_root / "releases" / release_id / "catalog.json"
    existing_catalog = (
        read_json(exchange_catalog) if exchange_catalog.exists() else None
    )
    if existing_catalog is not None:
        if (
            existing_catalog.get("schema") != "pawmarvel-static-release-catalog-v1"
            or existing_catalog.get("release_id") != release_id
            or not isinstance(existing_catalog.get("created_at"), str)
        ):
            raise StaticPrintError(
                f"existing release draft is invalid: {exchange_catalog.resolve()}"
            )
        release_created_at = existing_catalog["created_at"]
    else:
        release_created_at = utc_now()
    staging = exchange_root / ".staging" / release_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    manifests: list[Path] = []
    entries: list[dict[str, Any]] = []
    for selection in sorted(
        selected,
        key=lambda item: (item.design_id, item.profile.profile_id, item.version_id),
    ):
        details = {
            "design_id": selection.design_id,
            "product_profile_id": selection.profile.profile_id,
            "version_id": selection.version_id,
        }
        if progress:
            progress("bundle-start", details)
        design_id = design_identity(selection.design_id).design_id
        version_id = validate_version(selection.version_id)
        bundle_id = bundle_id_for(design_id, selection.profile.profile_id)
        generation_path = (
            design_authoring_path(
                authoring_root, design_id, inputs_root=inputs_root
            )
            / selection.profile.profile_id
            / version_id
            / "generation.json"
        )
        bundle_created_at = _bundle_created_at(
            generation_path=generation_path,
            exchange_root=exchange_root,
            bundle_id=bundle_id,
            design_id=design_id,
            product_profile_id=selection.profile.profile_id,
            version_id=version_id,
            fallback=release_created_at,
        )
        manifest_path = build_bundle(
            inputs_root=inputs_root,
            authoring_root=authoring_root,
            staging_root=staging,
            selection=selection,
            created_at=bundle_created_at,
        )
        manifest = read_json(manifest_path)
        relative = str(manifest_path.relative_to(staging)).replace("\\", "/")
        entries.append(
            {
                "bundle_id": manifest["bundle_id"],
                "design_id": manifest["design_id"],
                "product_profile_id": manifest["product_profile_id"],
                "version_id": manifest["version_id"],
                "manifest_path": relative,
                "manifest_sha256": sha256_file(manifest_path),
            }
        )
        manifests.append(manifest_path)
        if progress:
            progress(
                "bundle-complete",
                {**details, "manifest_path": str(manifest_path.resolve())},
            )
    catalog = {
        "schema": "pawmarvel-static-release-catalog-v1",
        "release_id": release_id,
        "created_at": release_created_at,
        "bundles": entries,
    }
    catalog_path = staging / "releases" / release_id / "catalog.json"
    write_json(catalog_path, catalog)
    if existing_catalog is not None and catalog != existing_catalog:
        shutil.rmtree(staging)
        raise StaticPrintError(
            f"release draft {release_id!r} already exists with different bundle bytes or "
            f"selections: {exchange_catalog.resolve()}; use a new release ID"
        )
    if existing_catalog is None:
        write_json(exchange_catalog, catalog, exclusive=True)
    if progress:
        progress(
            "release-validated",
            {
                "release_id": release_id,
                "bundle_count": len(entries),
                "catalog_path": str(catalog_path.resolve()),
            },
        )
    return ReleasePlan(
        release_id=release_id,
        staging_root=staging,
        catalog_path=catalog_path,
        exchange_catalog_path=exchange_catalog,
        bundle_manifests=tuple(manifests),
    )


def validate_release(plan: ReleasePlan) -> dict[str, Any]:
    catalog = read_json(plan.catalog_path)
    required_catalog = {"schema", "release_id", "created_at", "bundles"}
    if set(catalog) != required_catalog:
        raise StaticPrintError(
            f"release catalog fields mismatch: {plan.catalog_path}; "
            f"missing={sorted(required_catalog - set(catalog))}; "
            f"unknown={sorted(set(catalog) - required_catalog)}"
        )
    if catalog.get("schema") != "pawmarvel-static-release-catalog-v1":
        raise StaticPrintError(f"unsupported release catalog: {plan.catalog_path}")
    catalog_release_id = validate_release_id(catalog.get("release_id"))
    if catalog_release_id != plan.release_id:
        raise StaticPrintError(
            f"release catalog identity mismatch: {plan.catalog_path}"
        )
    if not isinstance(catalog.get("created_at"), str) or not catalog["created_at"]:
        raise StaticPrintError(f"release created_at is invalid: {plan.catalog_path}")
    entries = catalog.get("bundles")
    if not isinstance(entries, list) or not entries:
        raise StaticPrintError(
            f"release catalog bundles must be non-empty: {plan.catalog_path}"
        )
    identities: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise StaticPrintError(
                f"release entry must be an object: {plan.catalog_path}"
            )
        required_entry = {
            "bundle_id",
            "design_id",
            "product_profile_id",
            "version_id",
            "manifest_path",
            "manifest_sha256",
        }
        if set(entry) != required_entry:
            raise StaticPrintError(
                f"release entry fields mismatch: {plan.catalog_path}; "
                f"missing={sorted(required_entry - set(entry))}; "
                f"unknown={sorted(set(entry) - required_entry)}"
            )
        design_id = validate_design_id(entry.get("design_id"), "release design ID")
        profile_id = validate_id(
            entry.get("product_profile_id"), "release product profile ID"
        )
        version_id = validate_version(entry.get("version_id"))
        bundle_id = bundle_id_for(design_id, profile_id)
        if entry.get("bundle_id") != bundle_id:
            raise StaticPrintError(
                f"release bundle identity mismatch: {plan.catalog_path}; "
                f"expected={bundle_id!r}; actual={entry.get('bundle_id')!r}"
            )
        identity = (bundle_id, version_id)
        if identity in identities:
            raise StaticPrintError(f"duplicate release bundle identity: {identity}")
        identities.add(identity)
        expected_manifest_path = f"bundles/{bundle_id}/{version_id}/bundle.json"
        if entry.get("manifest_path") != expected_manifest_path:
            raise StaticPrintError(
                f"release manifest path mismatch: {plan.catalog_path}; "
                f"expected={expected_manifest_path!r}; "
                f"actual={entry.get('manifest_path')!r}"
            )
        manifest = (plan.staging_root / expected_manifest_path).resolve()
        staging = plan.staging_root.resolve()
        if staging not in manifest.parents:
            raise StaticPrintError(f"release manifest escapes staging root: {manifest}")
        value = validate_bundle(manifest.parent)
        if value["bundle_id"] != identity[0] or value["version_id"] != identity[1]:
            raise StaticPrintError(f"release-to-bundle identity mismatch: {manifest}")
        actual_hash = sha256_file(manifest)
        if entry.get("manifest_sha256") != actual_hash:
            raise StaticPrintError(
                f"release manifest hash mismatch: {manifest}; "
                f"expected={entry.get('manifest_sha256')}; actual={actual_hash}"
            )
    return catalog
