"""Pure construction and validation for immutable release catalogs."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from .artifact_io import mismatch, sha256, utc_now
from .bundle import BundleError, catalog_template_id, validate_utc_timestamp
from .production_bundle import validate_production_bundle


RELEASE_ID_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]{3}$")


def _validate_release_id(value: object) -> str:
    if not isinstance(value, str) or RELEASE_ID_PATTERN.fullmatch(value) is None:
        raise BundleError("release ID must use YYYY-MM-DD.NNN form")
    try:
        datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError as exc:
        raise BundleError("release ID must start with a valid calendar date") from exc
    return value


def _write_exclusive(path: Path, value: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            # Hard-linking a fully flushed temporary file atomically publishes
            # it while refusing an existing destination.
            os.link(name, path)
        except FileExistsError as exc:
            raise BundleError(f"release catalog already exists: {path}") from exc
    finally:
        Path(name).unlink(missing_ok=True)


def _validate_https_url(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise BundleError(f"{label} must be an HTTPS URL")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise BundleError(f"{label} is not a valid HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise BundleError(
            f"{label} must be an HTTPS URL without credentials, query, or fragment"
        )
    return value


def manifest_path(template_id: str, revision: int) -> str:
    return f"bundles/{template_id}/v{revision:06d}/bundle.json"


def build_release(
    *,
    release_id: str,
    bundles: list[Path],
    exchange_root: Path,
    asset_base_url: str | None,
) -> Path:
    _validate_release_id(release_id)
    if not bundles:
        raise BundleError("at least one --bundle is required")
    exchange_root = exchange_root.expanduser().resolve()
    output = exchange_root / "releases" / release_id / "catalog.json"
    if asset_base_url is not None:
        _validate_https_url(asset_base_url, "asset base URL")
    entries = []
    identities = set()
    for bundle in bundles:
        root = bundle.expanduser().resolve()
        manifest = validate_production_bundle(root)
        identity = (manifest["template_id"], manifest["bundle_revision"])
        if identity in identities:
            raise BundleError(f"duplicate release bundle identity: {identity}")
        identities.add(identity)
        relative_manifest = manifest_path(
            manifest["template_id"], int(manifest["bundle_revision"])
        )
        expected_root = exchange_root / PurePosixPath(relative_manifest).parent
        if root != expected_root:
            raise BundleError(
                "release bundles must use the canonical exchange path: "
                f"{expected_root}"
            )
        entry = {
            "template_id": manifest["template_id"],
            "design_id": manifest["design_id"],
            "product_profile_id": manifest["product_profile_id"],
            "bundle_revision": manifest["bundle_revision"],
            "manifest_path": relative_manifest,
            "manifest_sha256": sha256(root / "bundle.json"),
        }
        if asset_base_url:
            entry["manifest_url"] = (
                asset_base_url.rstrip("/") + "/" + relative_manifest
            )
        entries.append(entry)
    entries.sort(key=lambda item: (item["template_id"], item["bundle_revision"]))
    value = {
        "schema_version": 2,
        "release_id": release_id,
        "created_at": utc_now(),
        "templates": entries,
    }
    _write_exclusive(output, value)
    return output


def _validate_release(
    path: Path, *, exchange_root: Path | None = None
) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise BundleError(
            f"release catalog does not exist: {resolved}; build it first with "
            "`pawmarvel-catalog build-release --release-id <release-id> "
            "--bundle <bundle-dir> --exchange-root <exchange-root>`, or select "
            "an existing PAWMARVEL_RELEASE_ID"
        )
    if not resolved.is_file():
        raise BundleError(f"release catalog path is not a file: {resolved}")
    try:
        contents = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise BundleError(f"could not read release catalog {resolved}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise BundleError(
            f"release catalog is not UTF-8 text: {resolved}; "
            f"byte offset={exc.start}"
        ) from exc
    try:
        value = json.loads(contents)
    except json.JSONDecodeError as exc:
        raise BundleError(
            f"release catalog contains invalid JSON: {resolved}; "
            f"line={exc.lineno}; column={exc.colno}; error={exc.msg}"
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "release_id", "created_at", "templates"}
        or value.get("schema_version") != 2
        or not isinstance(value.get("templates"), list)
        or not value["templates"]
    ):
        raise BundleError(
            "release catalog must match the closed release-catalog-v2 contract"
        )
    _validate_release_id(value.get("release_id"))
    validate_utc_timestamp(value.get("created_at"), "release created_at")
    seen = set()
    for entry in value["templates"]:
        required = {
            "template_id",
            "design_id",
            "product_profile_id",
            "bundle_revision",
            "manifest_path",
            "manifest_sha256",
        }
        if (
            not isinstance(entry, dict)
            or not required <= set(entry)
            or not (set(entry) - required) <= {"manifest_url"}
        ):
            raise BundleError(
                "release entries must match the closed catalog-entry contract"
            )
        template_id = entry.get("template_id")
        revision = entry.get("bundle_revision")
        design_id = entry.get("design_id")
        profile_id = entry.get("product_profile_id")
        if (
            not isinstance(template_id, str)
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or not isinstance(design_id, str)
            or not isinstance(profile_id, str)
            or template_id != catalog_template_id(design_id, profile_id)
        ):
            raise BundleError("release entries contain invalid identity")
        identity = (template_id, revision)
        if identity in seen:
            raise BundleError("release entries contain duplicate identity")
        expected_path = manifest_path(template_id, revision)
        if entry.get("manifest_path") != expected_path:
            raise BundleError(
                mismatch(
                    f"release entry {template_id} revision {revision} manifest_path",
                    expected=expected_path,
                    actual=entry.get("manifest_path"),
                )
            )
        manifest_url = entry.get("manifest_url")
        if manifest_url is not None:
            parsed_url = urlsplit(_validate_https_url(manifest_url, "manifest_url"))
            if not parsed_url.path.endswith("/" + expected_path):
                raise BundleError(
                    mismatch(
                        "release entry manifest_url path suffix",
                        expected="/" + expected_path,
                        actual=parsed_url.path,
                    )
                )
        digest = entry.get("manifest_sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise BundleError("release entry manifest_sha256 must be SHA-256 hex")
        seen.add(identity)
    if exchange_root is not None:
        root = exchange_root.expanduser().resolve()
        for entry in value["templates"]:
            relative = PurePosixPath(entry["manifest_path"])
            local_manifest = root.joinpath(*relative.parts).resolve()
            if not local_manifest.is_relative_to(root):
                raise BundleError("release manifest path escapes the exchange root")
            if not local_manifest.is_file():
                raise BundleError(
                    f"release bundle manifest does not exist: {local_manifest}"
                )
            if sha256(local_manifest) != entry["manifest_sha256"]:
                raise BundleError(
                    mismatch(
                        f"release bundle manifest hash ({local_manifest})",
                        expected=entry["manifest_sha256"],
                        actual=sha256(local_manifest),
                    )
                )
            manifest = validate_production_bundle(local_manifest.parent)
            for key in (
                "template_id",
                "design_id",
                "product_profile_id",
                "bundle_revision",
            ):
                if manifest.get(key) != entry[key]:
                    raise BundleError(
                        mismatch(
                            f"release entry {key} for {local_manifest}",
                            expected=entry[key],
                            actual=manifest.get(key),
                        )
                    )
    return value


def validate_release(
    path: Path, *, exchange_root: Path | None = None
) -> dict[str, Any]:
    """Validate a release while retaining its absolute path in every failure."""
    resolved = path.expanduser().resolve()
    try:
        return _validate_release(resolved, exchange_root=exchange_root)
    except BundleError as exc:
        raise BundleError(
            f"release catalog validation failed; catalog={resolved}; error={exc}"
        ) from exc
