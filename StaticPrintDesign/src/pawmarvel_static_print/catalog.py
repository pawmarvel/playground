from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from .core import (
    StaticPrintError,
    bundle_id_for,
    utc_now,
    validate_design_id,
    validate_id,
    validate_release_id,
    validate_version,
)

CATALOG_SCHEMA = "pawmarvel-static-catalog-v1"


def _version_number(version_id: str) -> int:
    return int(validate_version(version_id).removeprefix("v"))


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise StaticPrintError(f"{label} must be a non-empty RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StaticPrintError(
            f"{label} must be an RFC 3339 timestamp; actual={value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise StaticPrintError(f"{label} must include a timezone; actual={value!r}")
    return value


def validate_catalog(value: dict[str, Any]) -> dict[str, Any]:
    """Validate the stable FE discovery catalog without filesystem access."""

    required = {"schema", "updated_at", "products"}
    if set(value) != required:
        raise StaticPrintError(
            "static catalog fields mismatch; "
            f"missing={sorted(required - set(value))}; "
            f"unknown={sorted(set(value) - required)}"
        )
    if value.get("schema") != CATALOG_SCHEMA:
        raise StaticPrintError(
            f"unsupported static catalog schema: {value.get('schema')!r}"
        )
    _timestamp(value.get("updated_at"), "static catalog updated_at")
    products = value.get("products")
    if not isinstance(products, list):
        raise StaticPrintError("static catalog products must be an array")
    seen_products: set[str] = set()
    for product_index, product in enumerate(products):
        if not isinstance(product, dict):
            raise StaticPrintError(
                f"static catalog product {product_index} must be an object"
            )
        required_product = {
            "bundle_id",
            "design_id",
            "product_profile_id",
            "status",
            "latest_version_id",
            "versions",
        }
        optional_product = {"status_changed_at", "status_reason"}
        if not required_product <= set(product) or not set(product) <= (
            required_product | optional_product
        ):
            raise StaticPrintError(
                f"static catalog product {product_index} fields mismatch; "
                f"missing={sorted(required_product - set(product))}; "
                f"unknown={sorted(set(product) - required_product - optional_product)}"
            )
        design_id = validate_design_id(product.get("design_id"), "catalog design ID")
        profile_id = validate_id(
            product.get("product_profile_id"), "catalog product profile ID"
        )
        bundle_id = bundle_id_for(design_id, profile_id)
        if product.get("bundle_id") != bundle_id:
            raise StaticPrintError(
                f"static catalog bundle identity mismatch at product {product_index}; "
                f"expected={bundle_id!r}; actual={product.get('bundle_id')!r}"
            )
        if bundle_id in seen_products:
            raise StaticPrintError(f"duplicate static catalog product: {bundle_id!r}")
        seen_products.add(bundle_id)
        if product.get("status") not in {"active", "retired"}:
            raise StaticPrintError(
                f"static catalog product status must be active or retired: {bundle_id!r}"
            )
        versions = product.get("versions")
        if not isinstance(versions, list) or not versions:
            raise StaticPrintError(
                f"static catalog product versions must be non-empty: {bundle_id!r}"
            )
        seen_versions: set[str] = set()
        previous_number = 0
        for version_index, version in enumerate(versions):
            if not isinstance(version, dict):
                raise StaticPrintError(
                    f"catalog version {bundle_id}[{version_index}] must be an object"
                )
            required_version = {
                "version_id",
                "release_id",
                "published_at",
                "manifest_path",
                "manifest_sha256",
            }
            if set(version) != required_version:
                raise StaticPrintError(
                    f"catalog version {bundle_id}[{version_index}] fields mismatch; "
                    f"missing={sorted(required_version - set(version))}; "
                    f"unknown={sorted(set(version) - required_version)}"
                )
            version_id = validate_version(version.get("version_id"))
            number = _version_number(version_id)
            if version_id in seen_versions or number <= previous_number:
                raise StaticPrintError(
                    f"catalog versions must be unique and ascending: {bundle_id!r}"
                )
            seen_versions.add(version_id)
            previous_number = number
            validate_release_id(version.get("release_id"))
            _timestamp(
                version.get("published_at"),
                f"catalog version published_at for {bundle_id!r}/{version_id}",
            )
            expected_path = f"bundles/{bundle_id}/{version_id}/bundle.json"
            if version.get("manifest_path") != expected_path:
                raise StaticPrintError(
                    f"catalog manifest path mismatch: {bundle_id!r}/{version_id}; "
                    f"expected={expected_path!r}; actual={version.get('manifest_path')!r}"
                )
            digest = version.get("manifest_sha256")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise StaticPrintError(
                    f"catalog manifest digest is invalid: {bundle_id!r}/{version_id}"
                )
        if product.get("latest_version_id") != versions[-1]["version_id"]:
            raise StaticPrintError(
                f"catalog latest version mismatch: {bundle_id!r}; "
                f"expected={versions[-1]['version_id']!r}; "
                f"actual={product.get('latest_version_id')!r}"
            )
        for field in optional_product:
            if field in product and (
                not isinstance(product[field], str) or not product[field]
            ):
                raise StaticPrintError(
                    f"catalog {field} must be a non-empty string: {bundle_id!r}"
                )
        if "status_changed_at" in product:
            _timestamp(
                product["status_changed_at"],
                f"catalog status_changed_at for {bundle_id!r}",
            )
    return value


def build_catalog(
    release_catalogs: Iterable[dict[str, Any]],
    *,
    previous: dict[str, Any] | None = None,
    activate_bundle_ids: set[str] | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Derive one deterministic FE index from immutable release catalogs."""

    previous_status: dict[str, dict[str, str]] = {}
    if previous is not None:
        validate_catalog(previous)
        previous_status = {
            str(product["bundle_id"]): {
                key: str(product[key])
                for key in ("status", "status_changed_at", "status_reason")
                if key in product
            }
            for product in previous["products"]
        }
    grouped: dict[str, dict[str, Any]] = {}
    seen_versions: dict[tuple[str, str], dict[str, Any]] = {}
    ordered_releases = sorted(
        release_catalogs, key=lambda item: str(item.get("release_id", ""))
    )
    for release in ordered_releases:
        required_release = {"schema", "release_id", "created_at", "bundles"}
        if set(release) != required_release:
            raise StaticPrintError(
                "release catalog fields mismatch while building discovery catalog; "
                f"missing={sorted(required_release - set(release))}; "
                f"unknown={sorted(set(release) - required_release)}"
            )
        if release.get("schema") != "pawmarvel-static-release-catalog-v1":
            raise StaticPrintError(
                f"unsupported release catalog while building discovery catalog: "
                f"{release.get('schema')!r}"
            )
        release_id = validate_release_id(release.get("release_id"))
        published_at = _timestamp(
            release.get("created_at"),
            f"release catalog created_at for {release_id!r}",
        )
        entries = release.get("bundles")
        if not isinstance(entries, list) or not entries:
            raise StaticPrintError(
                f"release catalog bundles must be non-empty: release_id={release_id!r}"
            )
        for entry in entries:
            if not isinstance(entry, dict):
                raise StaticPrintError(
                    f"release catalog bundle must be an object: release_id={release_id!r}"
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
                    f"release catalog entry fields mismatch: release_id={release_id!r}; "
                    f"missing={sorted(required_entry - set(entry))}; "
                    f"unknown={sorted(set(entry) - required_entry)}"
                )
            design_id = validate_design_id(entry.get("design_id"), "release design ID")
            profile_id = validate_id(
                entry.get("product_profile_id"), "release product profile ID"
            )
            bundle_id = bundle_id_for(design_id, profile_id)
            if entry.get("bundle_id") != bundle_id:
                raise StaticPrintError(
                    f"release catalog bundle identity mismatch: release_id={release_id!r}; "
                    f"expected={bundle_id!r}; actual={entry.get('bundle_id')!r}"
                )
            version_id = validate_version(entry.get("version_id"))
            version = {
                "version_id": version_id,
                "release_id": release_id,
                "published_at": published_at,
                "manifest_path": entry.get("manifest_path"),
                "manifest_sha256": entry.get("manifest_sha256"),
            }
            identity = (bundle_id, version_id)
            existing = seen_versions.get(identity)
            if existing is not None:
                if (
                    existing["manifest_path"] != version["manifest_path"]
                    or existing["manifest_sha256"] != version["manifest_sha256"]
                ):
                    raise StaticPrintError(
                        f"immutable bundle version appears with conflicting manifest "
                        f"metadata: bundle_id={bundle_id!r}; version_id={version_id!r}"
                    )
                # A later release may intentionally re-list an unchanged bundle.
                # Preserve the first publication event as the version provenance.
                continue
            seen_versions[identity] = version
            product = grouped.setdefault(
                bundle_id,
                {
                    "bundle_id": bundle_id,
                    "design_id": design_id,
                    "product_profile_id": profile_id,
                    "versions": [],
                },
            )
            if version not in product["versions"]:
                product["versions"].append(version)
    active = activate_bundle_ids or set()
    products: list[dict[str, Any]] = []
    for bundle_id in sorted(grouped):
        product = grouped[bundle_id]
        product["versions"].sort(key=lambda item: _version_number(item["version_id"]))
        product["latest_version_id"] = product["versions"][-1]["version_id"]
        prior = previous_status.get(bundle_id, {})
        product["status"] = (
            "active" if bundle_id in active else prior.get("status", "active")
        )
        if bundle_id not in active:
            for field in ("status_changed_at", "status_reason"):
                if field in prior:
                    product[field] = prior[field]
        products.append(product)
    catalog = {
        "schema": CATALOG_SCHEMA,
        "updated_at": updated_at or utc_now(),
        "products": products,
    }
    return validate_catalog(catalog)


def set_product_status(
    catalog: dict[str, Any],
    *,
    bundle_ids: Iterable[str],
    status: str,
    reason: str,
    changed_at: str | None = None,
) -> dict[str, Any]:
    """Return a validated catalog copy with product lifecycle changes."""

    validate_catalog(catalog)
    if status not in {"active", "retired"}:
        raise StaticPrintError(
            f"product lifecycle status must be active or retired; actual={status!r}"
        )
    requested = set(bundle_ids)
    available = {str(product["bundle_id"]) for product in catalog["products"]}
    unknown = sorted(requested - available)
    if not requested:
        raise StaticPrintError("at least one bundle ID is required")
    if unknown:
        raise StaticPrintError(
            f"catalog lifecycle selection contains unknown bundle IDs: {unknown}"
        )
    if len(reason.strip()) > 1000:
        raise StaticPrintError(
            "product lifecycle reason must not exceed 1000 characters"
        )
    timestamp = changed_at or utc_now()
    products: list[dict[str, Any]] = []
    for original in catalog["products"]:
        product = dict(original)
        if product["bundle_id"] in requested:
            product["status"] = status
            product["status_changed_at"] = timestamp
            if reason.strip():
                product["status_reason"] = reason.strip()
            else:
                product.pop("status_reason", None)
        products.append(product)
    updated = {**catalog, "updated_at": timestamp, "products": products}
    return validate_catalog(updated)
