"""Guarded retirement of local and S3 artifacts outside the current contract."""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .catalog import build_catalog, validate_catalog
from .core import (
    StaticPrintError,
    bundle_id_for,
    design_identity,
    read_json,
    utc_now,
    validate_id,
    validate_release_id,
    validate_version,
    write_json,
)
from .publisher import _aws_base, _aws_json, _download_s3_json, _run, validate_prefix

RETIREMENT_SCHEMA = "pawmarvel-static-obsolete-retirement-v1"
GENERATION_SCHEMA = "pawmarvel-static-generation-v3"


def _path_sha256(path: Path) -> str:
    """Hash one file or a directory tree including relative filenames."""

    digest = hashlib.sha256()
    if path.is_file():
        paths = [path]
        root = path.parent
    elif path.is_dir():
        paths = sorted(item for item in path.rglob("*") if item.is_file())
        root = path
    else:
        raise StaticPrintError(f"artifact path does not exist: {path.resolve()}")
    for item in paths:
        relative = str(item.relative_to(root)).replace("\\", "/").encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with item.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _plan_sha256(
    *,
    bucket: str,
    prefix: str,
    local: list[dict[str, Any]],
    remote: list[dict[str, Any]],
) -> str:
    encoded = json.dumps(
        {
            "bucket": bucket,
            "prefix": prefix,
            "local_artifacts": local,
            "s3_objects": remote,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_saved_plan(
    *,
    work_root: Path,
    prefix: str,
    local: list[Any],
    remote: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reject malformed or path-escaping entries before a saved plan is used."""

    work = work_root.resolve()
    normalized_prefix = validate_prefix(prefix)
    local_result: list[dict[str, Any]] = []
    local_paths: set[str] = set()
    for index, value in enumerate(local):
        if not isinstance(value, dict):
            raise StaticPrintError(
                f"retirement local plan item {index} must be an object"
            )
        required = {"path", "relative_path", "reason", "sha256"}
        if set(value) != required:
            raise StaticPrintError(
                f"retirement local plan item {index} fields mismatch; "
                f"missing={sorted(required - set(value))}; "
                f"unknown={sorted(set(value) - required)}"
            )
        relative = Path(str(value["relative_path"]))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise StaticPrintError(
                f"retirement local plan path is unsafe: item={index}; "
                f"relative_path={value['relative_path']!r}"
            )
        expected = (work / relative).resolve()
        actual = Path(str(value["path"])).expanduser().resolve()
        if expected != actual or (actual != work and work not in actual.parents):
            raise StaticPrintError(
                f"retirement local plan path escapes or disagrees with work root: "
                f"item={index}; expected={expected}; actual={actual}"
            )
        digest = value["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise StaticPrintError(
                f"retirement local plan SHA-256 is invalid: item={index}; "
                f"actual={digest!r}"
            )
        reason = value["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise StaticPrintError(
                f"retirement local plan reason is empty: item={index}"
            )
        key = str(actual)
        if key in local_paths:
            raise StaticPrintError(
                f"retirement local plan contains duplicate path: {actual}"
            )
        local_paths.add(key)
        local_result.append(dict(value))

    remote_result: list[dict[str, Any]] = []
    remote_keys: set[str] = set()
    root = f"{normalized_prefix}/" if normalized_prefix else ""
    for index, value in enumerate(remote):
        if not isinstance(value, dict):
            raise StaticPrintError(f"retirement S3 plan item {index} must be an object")
        required = {
            "key",
            "relative_path",
            "etag",
            "bytes",
            "last_modified",
            "reason",
        }
        if set(value) != required:
            raise StaticPrintError(
                f"retirement S3 plan item {index} fields mismatch; "
                f"missing={sorted(required - set(value))}; "
                f"unknown={sorted(set(value) - required)}"
            )
        relative = str(value["relative_path"])
        expected_key = f"{root}{relative}"
        key = str(value["key"])
        if (
            key != expected_key
            or relative.startswith("/")
            or ".." in relative.split("/")
        ):
            raise StaticPrintError(
                f"retirement S3 plan key disagrees with prefix: item={index}; "
                f"expected={expected_key!r}; actual={key!r}"
            )
        if relative == "catalog.json":
            raise StaticPrintError(
                "the stable FE catalog cannot be a retirement target"
            )
        if not isinstance(value["etag"], str) or not value["etag"]:
            raise StaticPrintError(f"retirement S3 plan ETag is invalid: item={index}")
        size = value["bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise StaticPrintError(
                f"retirement S3 plan byte count is invalid: item={index}"
            )
        reason = value["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise StaticPrintError(f"retirement S3 plan reason is empty: item={index}")
        if key in remote_keys:
            raise StaticPrintError(f"retirement S3 plan contains duplicate key: {key}")
        remote_keys.add(key)
        remote_result.append(dict(value))
    return local_result, remote_result


def _assert_pending_s3_targets_not_catalog_referenced(
    *,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    pending: list[dict[str, Any]],
) -> None:
    """Recheck FE references immediately before executing a reviewed plan."""

    target_versions: dict[tuple[str, str], str] = {}
    target_releases: dict[str, str] = {}
    for item in pending:
        relative = str(item["relative_path"])
        parts = relative.split("/")
        if len(parts) >= 3 and parts[0] == "bundles":
            target_versions[(parts[1], parts[2])] = str(item["key"])
        elif len(parts) >= 2 and parts[0] == "releases":
            target_releases[parts[1]] = str(item["key"])
    if not target_versions and not target_releases:
        return
    objects = _list_s3_objects(
        bucket=bucket, prefix=prefix, profile=profile, region=region
    )
    catalog_item = next(
        (item for item in objects if item["relative_path"] == "catalog.json"), None
    )
    if catalog_item is None:
        raise StaticPrintError(
            "refusing retirement execution because the stable FE catalog is missing; "
            "restore catalog.json and create a new reviewed plan"
        )
    catalog = _download_s3_json(
        bucket=bucket,
        key=str(catalog_item["key"]),
        profile=profile,
        region=region,
    )
    catalog_versions, catalog_releases = _catalog_references(catalog)
    version_conflicts = sorted(set(target_versions) & catalog_versions)
    if version_conflicts:
        identity = version_conflicts[0]
        raise StaticPrintError(
            "retirement plan became unsafe after review because the stable FE "
            f"catalog now references bundle version {identity[0]}/{identity[1]}; "
            f"object={target_versions[identity]}; create a new plan"
        )
    release_conflicts = sorted(set(target_releases) & catalog_releases)
    if release_conflicts:
        release_id = release_conflicts[0]
        raise StaticPrintError(
            "retirement plan became unsafe after review because the stable FE "
            f"catalog now references release {release_id}; "
            f"object={target_releases[release_id]}; create a new plan"
        )


@contextmanager
def _workspace_lock(work_root: Path) -> Iterator[None]:
    """Exclude the GUI/publisher and a second retirement execution."""

    lock_path = work_root.resolve() / ".static-print-server.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StaticPrintError(
                "the static-print workspace is in use; stop the GUI/server or other "
                f"retirement command before executing cleanup: lock={lock_path}"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _canonical_bundle_identity(bundle_id: str) -> tuple[str, str]:
    parts = bundle_id.split("--")
    if len(parts) != 3 or not parts[1].startswith("subject-"):
        raise StaticPrintError(
            "bundle ID must be <seed-design-id>--subject-<subject-id>--"
            f"<product-profile-id>; actual={bundle_id!r}"
        )
    design_id = f"{parts[0]}--{parts[1]}"
    identity = design_identity(design_id)
    if identity.subject_id is None:
        raise StaticPrintError(f"bundle ID has no subject identity: {bundle_id!r}")
    profile_id = validate_id(parts[2], "bundle product profile ID")
    if bundle_id_for(design_id, profile_id) != bundle_id:
        raise StaticPrintError(f"bundle ID is not canonical: {bundle_id!r}")
    return design_id, profile_id


def _catalog_references(
    catalog: dict[str, Any],
) -> tuple[set[tuple[str, str]], set[str]]:
    """Return immutable bundle versions and releases pinned by FE discovery."""

    validate_catalog(catalog)
    versions: set[tuple[str, str]] = set()
    releases: set[str] = set()
    for product in catalog["products"]:
        for version in product["versions"]:
            versions.add((str(product["bundle_id"]), str(version["version_id"])))
            releases.add(str(version["release_id"]))
    return versions, releases


def _bundle_key_reason(relative: str) -> str | None:
    parts = relative.split("/")
    if len(parts) < 4 or parts[0] != "bundles":
        return "bundle object path must include bundle ID, version, and asset path"
    bundle_id, version_id = parts[1], parts[2]
    try:
        _canonical_bundle_identity(bundle_id)
        validate_version(version_id)
    except StaticPrintError as exc:
        return str(exc)
    asset = parts[3:]
    if asset in (["bundle.json"], ["preview.png"]):
        return None
    if len(asset) == 2 and asset[0] == "print" and asset[1].endswith(".png"):
        try:
            validate_id(asset[1].removesuffix(".png"), "print target ID")
        except StaticPrintError as exc:
            return str(exc)
        return None
    return "bundle object path is not declared by the current bundle contract"


def _list_s3_objects(
    *, bucket: str, prefix: str, profile: str | None, region: str | None
) -> list[dict[str, Any]]:
    root = f"{validate_prefix(prefix)}/" if prefix else ""
    objects: list[dict[str, Any]] = []
    continuation: str | None = None
    while True:
        command = _aws_base(profile, region) + [
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--prefix",
            root,
            "--output",
            "json",
        ]
        if continuation:
            command += ["--continuation-token", continuation]
        page = _aws_json(command, operation="list static-design objects")
        contents = page.get("Contents", [])
        if not isinstance(contents, list):
            raise StaticPrintError(
                f"AWS object listing returned invalid Contents for s3://{bucket}/{root}"
            )
        for item in contents:
            if not isinstance(item, dict):
                continue
            key = str(item.get("Key", ""))
            if not key.startswith(root):
                continue
            objects.append(
                {
                    "key": key,
                    "relative_path": key.removeprefix(root),
                    "etag": str(item.get("ETag", "")),
                    "bytes": int(item.get("Size", 0)),
                    "last_modified": str(item.get("LastModified", "")),
                }
            )
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
        if not isinstance(token, str) or not token:
            raise StaticPrintError(
                "AWS object listing was truncated without a continuation token"
            )
        continuation = token
    return sorted(objects, key=lambda item: item["key"])


def plan_obsolete_s3_artifacts(
    *, bucket: str, prefix: str, profile: str | None, region: str | None
) -> list[dict[str, Any]]:
    """Return exact S3 objects that fail the current identity/schema contract."""

    objects = _list_s3_objects(
        bucket=bucket, prefix=prefix, profile=profile, region=region
    )
    obsolete: dict[str, dict[str, Any]] = {}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    catalog_items = [
        item for item in objects if item["relative_path"] == "catalog.json"
    ]
    if len(catalog_items) > 1:  # S3 keys are unique; this guards mocked transports.
        raise StaticPrintError("S3 inventory returned duplicate stable catalog objects")
    if not catalog_items and any(
        str(item["relative_path"]).startswith(("bundles/", "releases/"))
        for item in objects
    ):
        raise StaticPrintError(
            "refusing obsolete-artifact cleanup because the S3 stable catalog is "
            "missing while bundle or release objects exist; rebuild/restore catalog.json "
            "and rerun the audit"
        )
    protected_versions: set[tuple[str, str]] = set()
    protected_releases: set[str] = set()
    if catalog_items:
        catalog = _download_s3_json(
            bucket=bucket,
            key=str(catalog_items[0]["key"]),
            profile=profile,
            region=region,
        )
        protected_versions, protected_releases = _catalog_references(catalog)

    def mark(item: dict[str, Any], reason: str) -> None:
        relative = str(item["relative_path"])
        parts = relative.split("/")
        protected = None
        if (
            len(parts) >= 3
            and parts[0] == "bundles"
            and (parts[1], parts[2]) in protected_versions
        ):
            protected = f"bundle version {parts[1]}/{parts[2]}"
        elif (
            len(parts) >= 2
            and parts[0] == "releases"
            and parts[1] in protected_releases
        ):
            protected = f"release {parts[1]}"
        if protected:
            raise StaticPrintError(
                f"refusing to retire {protected} because the stable FE catalog "
                f"references it; object={item['key']}; validation_error={reason}; "
                "repair or deliberately replace the catalog before cleanup"
            )
        obsolete[item["key"]] = {**item, "reason": reason}

    for item in objects:
        relative = str(item["relative_path"])
        if relative == "catalog.json":
            continue
        if relative.startswith("bundles/"):
            reason = _bundle_key_reason(relative)
            if reason:
                mark(item, reason)
                continue
            parts = relative.split("/")
            groups.setdefault((parts[1], parts[2]), []).append(item)
            continue
        if relative.startswith("catalog-history/"):
            parts = relative.split("/")
            if len(parts) != 2 or not parts[1].endswith(".json"):
                mark(item, "catalog-history path is not canonical")
                continue
            try:
                validate_catalog(
                    _download_s3_json(
                        bucket=bucket,
                        key=item["key"],
                        profile=profile,
                        region=region,
                    )
                )
            except StaticPrintError as exc:
                mark(item, f"catalog-history object is outside current contract: {exc}")
            continue
        if relative.startswith("releases/"):
            parts = relative.split("/")
            try:
                if len(parts) != 3 or parts[2] != "catalog.json":
                    raise StaticPrintError("release path is not canonical")
                release_id = validate_release_id(parts[1])
                release = _download_s3_json(
                    bucket=bucket,
                    key=item["key"],
                    profile=profile,
                    region=region,
                )
                if release.get("release_id") != release_id:
                    raise StaticPrintError("release path and payload identity differ")
                build_catalog([release])
            except StaticPrintError as exc:
                mark(item, f"release object is outside current contract: {exc}")
            continue
        mark(item, "object is outside the current static-design artifact roots")

    for (bundle_id, version_id), items in groups.items():
        manifest_item = next(
            (
                item
                for item in items
                if item["relative_path"]
                == f"bundles/{bundle_id}/{version_id}/bundle.json"
            ),
            None,
        )
        reason: str | None = None
        if manifest_item is None:
            reason = "canonical bundle version is incomplete: bundle.json is missing"
        else:
            try:
                design_id, profile_id = _canonical_bundle_identity(bundle_id)
                manifest = _download_s3_json(
                    bucket=bucket,
                    key=manifest_item["key"],
                    profile=profile,
                    region=region,
                )
                expected = {
                    "schema": "pawmarvel-static-bundle-v1",
                    "bundle_id": bundle_id,
                    "design_id": design_id,
                    "product_profile_id": profile_id,
                    "version_id": version_id,
                }
                mismatches = {
                    field: {"expected": value, "actual": manifest.get(field)}
                    for field, value in expected.items()
                    if manifest.get(field) != value
                }
                if mismatches:
                    raise StaticPrintError(f"manifest identity mismatch: {mismatches}")
            except StaticPrintError as exc:
                reason = f"bundle version is outside current contract: {exc}"
        if reason:
            for item in items:
                mark(item, reason)

    return [obsolete[key] for key in sorted(obsolete)]


def plan_obsolete_local_artifacts(work_root: Path) -> list[dict[str, str]]:
    """Find private records whose schema or path cannot be consumed now."""

    work = work_root.resolve()
    obsolete: dict[Path, str] = {}
    authoring = work / "static-authoring"
    if authoring.is_dir():
        for record_path in sorted(authoring.rglob("generation.json")):
            reason: str | None = None
            relative = record_path.relative_to(authoring)
            parts = relative.parts
            try:
                record = read_json(record_path)
                if len(parts) != 5 or parts[-1] != "generation.json":
                    raise StaticPrintError("generation path is not product-granular")
                design_id = f"{parts[0]}--subject-{parts[1]}"
                identity = design_identity(design_id)
                profile_id = validate_id(parts[2], "generation product profile ID")
                version_id = validate_version(parts[3])
                expected = {
                    "schema": GENERATION_SCHEMA,
                    "design_id": identity.design_id,
                    "product_profile_id": profile_id,
                    "version_id": version_id,
                }
                mismatches = {
                    field: {"expected": value, "actual": record.get(field)}
                    for field, value in expected.items()
                    if record.get(field) != value
                }
                if mismatches:
                    raise StaticPrintError(
                        f"generation identity mismatch: {mismatches}"
                    )
            except StaticPrintError as exc:
                reason = str(exc)
            if reason:
                obsolete[record_path.parent] = reason

    exchange = work / "static-exchange"
    releases = exchange / "releases"
    if releases.is_dir():
        for catalog_path in sorted(releases.glob("*/catalog.json")):
            try:
                release_id = validate_release_id(catalog_path.parent.name)
                release = read_json(catalog_path)
                if release.get("release_id") != release_id:
                    raise StaticPrintError(
                        "release directory and payload identity differ"
                    )
                build_catalog([release])
            except StaticPrintError as exc:
                obsolete[catalog_path.parent] = str(exc)
    publications = exchange / "publications"
    if publications.is_dir():
        for receipt_path in sorted(publications.glob("*.json")):
            try:
                receipt = read_json(receipt_path)
                release_id = validate_release_id(receipt_path.stem)
                if (
                    receipt.get("schema") != "pawmarvel-static-publication-receipt-v1"
                    or receipt.get("release_id") != release_id
                    or not (releases / release_id / "catalog.json").is_file()
                ):
                    raise StaticPrintError(
                        "publication receipt has no matching current release catalog"
                    )
            except StaticPrintError as exc:
                obsolete[receipt_path] = str(exc)
    catalog_path = exchange / "catalog.json"
    if catalog_path.exists():
        try:
            validate_catalog(read_json(catalog_path))
        except StaticPrintError as exc:
            obsolete[catalog_path] = str(exc)

    return [
        {
            "path": str(path.resolve()),
            "relative_path": str(path.resolve().relative_to(work)),
            "reason": obsolete[path],
            "sha256": _path_sha256(path),
        }
        for path in sorted(obsolete)
    ]


def _s3_object_etag(
    *,
    bucket: str,
    key: str,
    profile: str | None,
    region: str | None,
) -> str | None:
    result = _run(
        _aws_base(profile, region)
        + [
            "s3api",
            "head-object",
            "--bucket",
            bucket,
            "--key",
            key,
            "--output",
            "json",
        ]
    )
    if result.returncode != 0:
        message = f"{result.stderr}\n{result.stdout}".lower()
        if any(token in message for token in ("not found", "404", "nosuchkey")):
            return None
        raise StaticPrintError(
            f"could not inspect obsolete S3 object s3://{bucket}/{key}; "
            f"aws_exit={result.returncode}; "
            f"error={result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise StaticPrintError(
            f"AWS head-object returned invalid JSON for s3://{bucket}/{key}"
        ) from exc
    if not isinstance(value, dict):
        raise StaticPrintError(
            f"AWS head-object returned a non-object for s3://{bucket}/{key}"
        )
    etag = value.get("ETag")
    if not isinstance(etag, str) or not etag:
        raise StaticPrintError(f"AWS head-object omitted ETag for s3://{bucket}/{key}")
    return etag


def _preflight_s3_object(
    *,
    bucket: str,
    item: dict[str, Any],
    profile: str | None,
    region: str | None,
) -> bool:
    actual = _s3_object_etag(
        bucket=bucket,
        key=str(item["key"]),
        profile=profile,
        region=region,
    )
    if actual is None:
        return False
    if actual != item["etag"]:
        raise StaticPrintError(
            f"obsolete S3 object changed after planning: s3://{bucket}/{item['key']}; "
            f"expected_etag={item['etag']!r}; actual_etag={actual!r}; rerun the audit"
        )
    return True


def _delete_s3_object(
    *,
    bucket: str,
    key: str,
    etag: str,
    profile: str | None,
    region: str | None,
) -> None:
    result = _run(
        _aws_base(profile, region)
        + [
            "s3api",
            "delete-object",
            "--bucket",
            bucket,
            "--key",
            key,
            "--if-match",
            etag,
        ]
    )
    if result.returncode != 0:
        raise StaticPrintError(
            f"could not retire S3 object s3://{bucket}/{key}; "
            f"aws_exit={result.returncode}; "
            f"error={result.stderr.strip() or result.stdout.strip()}"
        )


def _retire_obsolete_artifacts(
    *,
    work_root: Path,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    retirement_id: str | None,
    reason: str,
    execute: bool,
) -> dict[str, Any]:
    """Audit obsolete artifacts and optionally retire their exact paths/keys."""

    normalized = validate_prefix(prefix)
    receipt_path: Path | None = None
    existing_receipt: dict[str, Any] | None = None
    if retirement_id:
        retirement_id = validate_id(retirement_id, "retirement ID")
        if not reason.strip():
            raise StaticPrintError("--reason is required with --retirement-id")
        receipt_path = (
            work_root.resolve()
            / "static-exchange"
            / "retirements"
            / f"{retirement_id}.json"
        )
        if receipt_path.exists():
            existing_receipt = read_json(receipt_path)
            expected = {
                "schema": RETIREMENT_SCHEMA,
                "retirement_id": retirement_id,
                "bucket": bucket,
                "prefix": normalized,
                "reason": reason.strip(),
            }
            mismatches = {
                field: {"expected": value, "actual": existing_receipt.get(field)}
                for field, value in expected.items()
                if existing_receipt.get(field) != value
            }
            if mismatches:
                raise StaticPrintError(
                    f"retirement receipt cannot be resumed because its identity differs: "
                    f"receipt={receipt_path.resolve()}; mismatches={mismatches}"
                )
            if existing_receipt.get("status") == "complete":
                raise StaticPrintError(
                    f"retirement is already complete: {receipt_path.resolve()}"
                )
    elif execute:
        raise StaticPrintError("--retirement-id is required with --execute")

    if execute and existing_receipt is None:
        raise StaticPrintError(
            "retirement execution requires a reviewed saved plan; rerun without "
            "--execute using the same --retirement-id and --reason, inspect the plan, "
            "then execute it"
        )

    if existing_receipt is not None:
        local_value = existing_receipt.get("local_artifacts")
        remote_value = existing_receipt.get("s3_objects")
        if not isinstance(local_value, list) or not isinstance(remote_value, list):
            raise StaticPrintError(
                f"retirement receipt plan arrays are invalid: {receipt_path}"
            )
        local, remote = _validate_saved_plan(
            work_root=work_root,
            prefix=normalized,
            local=list(local_value),
            remote=list(remote_value),
        )
        actual_plan_sha256 = _plan_sha256(
            bucket=bucket,
            prefix=normalized,
            local=local,
            remote=remote,
        )
        if existing_receipt.get("plan_sha256") != actual_plan_sha256:
            raise StaticPrintError(
                f"retirement receipt plan digest mismatch: {receipt_path}; "
                f"expected={existing_receipt.get('plan_sha256')!r}; "
                f"actual={actual_plan_sha256!r}"
            )
    else:
        planned_local = plan_obsolete_local_artifacts(work_root)
        planned_remote = plan_obsolete_s3_artifacts(
            bucket=bucket,
            prefix=normalized,
            profile=profile,
            region=region,
        )
        local, remote = _validate_saved_plan(
            work_root=work_root,
            prefix=normalized,
            local=planned_local,
            remote=planned_remote,
        )
    result: dict[str, Any] = {
        "schema": RETIREMENT_SCHEMA,
        "generated_at": utc_now(),
        "status": "planned",
        "bucket": bucket,
        "prefix": normalized,
        "reason": reason.strip(),
        "local_artifacts": local,
        "s3_objects": remote,
        "summary": {
            "local_artifacts": len(local),
            "s3_objects": len(remote),
            "s3_bytes": sum(int(item["bytes"]) for item in remote),
        },
    }
    result["plan_sha256"] = _plan_sha256(
        bucket=bucket,
        prefix=normalized,
        local=local,
        remote=remote,
    )
    if not execute:
        if retirement_id:
            assert receipt_path is not None
            if existing_receipt is not None:
                return {
                    **existing_receipt,
                    "receipt": str(receipt_path.resolve()),
                }
            planned_receipt = {
                **result,
                "retirement_id": retirement_id,
                "retired_local_artifacts": [],
                "retired_s3_objects": [],
            }
            if existing_receipt is None:
                write_json(receipt_path, planned_receipt, exclusive=True)
            return {**planned_receipt, "receipt": str(receipt_path.resolve())}
        return result
    assert retirement_id is not None
    assert receipt_path is not None
    assert existing_receipt is not None
    receipt = existing_receipt
    receipt.pop("error", None)
    receipt["status"] = "running"
    receipt["execution_started_at"] = receipt.get("execution_started_at", utc_now())
    if receipt.get("retired_local_artifacts") or receipt.get("retired_s3_objects"):
        receipt["resumed_at"] = utc_now()
    write_json(receipt_path, receipt)

    retired_local = {str(item["path"]) for item in receipt["retired_local_artifacts"]}
    retired_remote = {str(item["key"]) for item in receipt["retired_s3_objects"]}
    quarantine = work_root.resolve() / "static-retired-artifacts" / retirement_id
    try:
        _assert_pending_s3_targets_not_catalog_referenced(
            bucket=bucket,
            prefix=normalized,
            profile=profile,
            region=region,
            pending=[item for item in remote if str(item["key"]) not in retired_remote],
        )
        for item in local:
            if str(item["path"]) in retired_local:
                continue
            source = Path(item["path"])
            destination = quarantine / item["relative_path"]
            if source.exists():
                if destination.exists():
                    raise StaticPrintError(
                        "planned local artifact exists at both source and quarantine; "
                        f"source={source.resolve()}; quarantine={destination.resolve()}"
                    )
                actual_sha256 = _path_sha256(source)
                if actual_sha256 != item.get("sha256"):
                    raise StaticPrintError(
                        "planned local artifact changed after review; "
                        f"source={source.resolve()}; expected_sha256={item.get('sha256')!r}; "
                        f"actual_sha256={actual_sha256!r}; create a new retirement plan"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
            elif destination.exists():
                actual_sha256 = _path_sha256(destination)
                if actual_sha256 != item.get("sha256"):
                    raise StaticPrintError(
                        "quarantined local artifact differs from the reviewed plan; "
                        f"quarantine={destination.resolve()}; "
                        f"expected_sha256={item.get('sha256')!r}; "
                        f"actual_sha256={actual_sha256!r}"
                    )
            else:
                raise StaticPrintError(
                    "planned local artifact is missing from both source and quarantine: "
                    f"source={source.resolve()}; quarantine={destination.resolve()}"
                )
            receipt["retired_local_artifacts"].append(
                {**item, "retired_path": str(destination.resolve())}
            )
            write_json(receipt_path, receipt)
        for item in remote:
            if str(item["key"]) in retired_remote:
                continue
            exists = _preflight_s3_object(
                bucket=bucket, item=item, profile=profile, region=region
            )
            status = "deleted"
            if exists:
                _delete_s3_object(
                    bucket=bucket,
                    key=str(item["key"]),
                    etag=str(item["etag"]),
                    profile=profile,
                    region=region,
                )
            else:
                status = "already-absent-during-resume"
            receipt["retired_s3_objects"].append({**item, "status": status})
            write_json(receipt_path, receipt)
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["error"] = str(exc)
        write_json(receipt_path, receipt)
        raise
    receipt["status"] = "complete"
    receipt["completed_at"] = utc_now()
    write_json(receipt_path, receipt)
    return {**receipt, "receipt": str(receipt_path.resolve())}


def retire_obsolete_artifacts(
    *,
    work_root: Path,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    retirement_id: str | None,
    reason: str,
    execute: bool,
) -> dict[str, Any]:
    """Plan or execute a guarded retirement against one private workspace."""

    if not execute:
        return _retire_obsolete_artifacts(
            work_root=work_root,
            bucket=bucket,
            prefix=prefix,
            profile=profile,
            region=region,
            retirement_id=retirement_id,
            reason=reason,
            execute=False,
        )
    with _workspace_lock(work_root):
        return _retire_obsolete_artifacts(
            work_root=work_root,
            bucket=bucket,
            prefix=prefix,
            profile=profile,
            region=region,
            retirement_id=retirement_id,
            reason=reason,
            execute=True,
        )
