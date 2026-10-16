from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import sleep
from typing import Any

from .bundles import ReleasePlan, validate_release
from .catalog import build_catalog, set_product_status, validate_catalog
from .core import (
    StaticPrintError,
    read_json,
    sha256_file,
    utc_now,
    validate_design_id,
    validate_id,
    validate_release_id,
    validate_version,
    write_json,
)

DEFAULT_S3_UPLOAD_MAX_CONCURRENCY = 16
DEFAULT_S3_MAX_ATTEMPTS = 4
DEFAULT_S3_RETRY_BASE_SECONDS = 1.0
MAX_S3_CONCURRENCY = 128


def _positive_environment_int(name: str, default: int, *, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw.strip())
    except (AttributeError, ValueError) as exc:
        raise StaticPrintError(
            f"{name} must be a positive integer; actual={raw!r}"
        ) from exc
    if not 1 <= value <= maximum:
        raise StaticPrintError(
            f"{name} must be between 1 and {maximum}; actual={value}"
        )
    return value


def _positive_environment_float(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw.strip())
    except (AttributeError, ValueError) as exc:
        raise StaticPrintError(
            f"{name} must be a positive number; actual={raw!r}"
        ) from exc
    if not 0 < value <= 300:
        raise StaticPrintError(
            f"{name} must be greater than 0 and at most 300; actual={value}"
        )
    return value


def _s3_retry_settings() -> tuple[int, int, float]:
    return (
        _positive_environment_int(
            "STATIC_PRINT_S3_UPLOAD_MAX_CONCURRENCY",
            DEFAULT_S3_UPLOAD_MAX_CONCURRENCY,
            maximum=MAX_S3_CONCURRENCY,
        ),
        _positive_environment_int(
            "STATIC_PRINT_S3_MAX_ATTEMPTS",
            DEFAULT_S3_MAX_ATTEMPTS,
            maximum=20,
        ),
        _positive_environment_float(
            "STATIC_PRINT_S3_RETRY_BASE_SECONDS",
            DEFAULT_S3_RETRY_BASE_SECONDS,
        ),
    )


def _retryable_s3_error(error: BaseException) -> bool:
    message = f"{type(error).__name__}: {error}".lower()
    terminal_tokens = (
        "accessdenied",
        "access denied",
        "expiredtoken",
        "token has expired",
        "config profile",
        "could not be found",
        "invalidaccesskeyid",
        "signaturedoesnotmatch",
        "immutable s3 object conflict",
        "preconditionfailed",
    )
    if any(token in message for token in terminal_tokens):
        return False
    return any(
        token in message
        for token in (
            "slowdown",
            "throttl",
            "toomanyrequests",
            "too many requests",
            "http 429",
            "requesttimeout",
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
            "could not connect",
            "endpointconnectionerror",
            "temporarily unavailable",
            "serviceunavailable",
            "internalerror",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
        )
    )


def _retry_s3_call(
    operation: Callable[[], Any],
    *,
    label: str,
    max_attempts: int,
    retry_base_seconds: float,
) -> Any:
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except StaticPrintError as exc:
            if not _retryable_s3_error(exc) or attempt >= max_attempts:
                if attempt <= 1:
                    raise
                raise StaticPrintError(
                    f"{label} failed after {attempt} attempt(s); last_error={exc}"
                ) from exc
            sleep(retry_base_seconds * (2 ** (attempt - 1)))
    raise AssertionError("unreachable S3 retry loop")


def validate_prefix(prefix: str) -> str:
    if prefix != prefix.strip() or prefix.startswith("/") or prefix.endswith("/"):
        raise StaticPrintError(
            "S3 prefix must have no surrounding whitespace or leading/trailing slash; "
            f"actual={prefix!r}"
        )
    if "//" in prefix:
        raise StaticPrintError(
            f"S3 prefix must not contain empty path segments: {prefix!r}"
        )
    return prefix


def _aws_base(profile: str | None, region: str | None) -> list[str]:
    command = ["aws"]
    if profile:
        command += ["--profile", profile]
    if region:
        command += ["--region", region]
    return command


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise StaticPrintError(
            "AWS CLI was not found; install/configure AWS CLI v2 before publishing"
        ) from exc


def _run_bytes(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run AWS without newline conversion so content hashes remain exact."""

    try:
        return subprocess.run(command, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise StaticPrintError(
            "AWS CLI was not found; install/configure AWS CLI v2 before publishing"
        ) from exc


def _aws_json(command: list[str], *, operation: str) -> dict[str, Any]:
    result = _run(command)
    if result.returncode != 0:
        raise StaticPrintError(
            f"AWS {operation} failed; aws_exit={result.returncode}; "
            f"error={result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise StaticPrintError(f"AWS {operation} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise StaticPrintError(f"AWS {operation} must return a JSON object")
    return value


def _download_s3_json(
    *, bucket: str, key: str, profile: str | None, region: str | None
) -> dict[str, Any]:
    command = _aws_base(profile, region) + [
        "s3",
        "cp",
        f"s3://{bucket}/{key}",
        "-",
        "--only-show-errors",
    ]
    result = _run(command)
    if result.returncode != 0:
        raise StaticPrintError(
            f"could not read authoritative S3 JSON object "
            f"s3://{bucket}/{key}; aws_exit={result.returncode}; "
            f"error={result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise StaticPrintError(
            f"S3 object is not valid JSON: s3://{bucket}/{key}"
        ) from exc
    if not isinstance(value, dict):
        raise StaticPrintError(f"S3 JSON root must be an object: s3://{bucket}/{key}")
    return value


def _remote_object(
    *, bucket: str, key: str, profile: str | None, region: str | None
) -> dict[str, str] | None:
    command = _aws_base(profile, region) + [
        "s3api",
        "head-object",
        "--bucket",
        bucket,
        "--key",
        key,
        "--output",
        "json",
    ]
    result = _run(command)
    if result.returncode != 0:
        message = f"{result.stderr}\n{result.stdout}".lower()
        if any(token in message for token in ("not found", "404", "nosuchkey")):
            return None
        raise StaticPrintError(
            f"could not inspect s3://{bucket}/{key}; aws_exit={result.returncode}; "
            f"error={result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise StaticPrintError(
            f"AWS head-object returned invalid JSON for s3://{bucket}/{key}"
        ) from exc
    metadata = value.get("Metadata", {})
    digest = metadata.get("sha256") if isinstance(metadata, dict) else None
    if not digest:
        raise StaticPrintError(
            f"existing S3 object lacks required sha256 metadata: s3://{bucket}/{key}; "
            "move the conflicting object or republish it with the static-print tool"
        )
    etag = value.get("ETag")
    if not isinstance(etag, str) or not etag:
        raise StaticPrintError(f"AWS head-object omitted ETag for s3://{bucket}/{key}")
    return {"sha256": str(digest), "etag": etag}


def _catalog_key(prefix: str) -> str:
    normalized = validate_prefix(prefix)
    return f"{normalized}/catalog.json" if normalized else "catalog.json"


def load_s3_catalog(
    *,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    catalog_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Load the stable FE catalog and return its S3 concurrency state."""

    key = _catalog_key(prefix)
    remote = _remote_object(bucket=bucket, key=key, profile=profile, region=region)
    if remote is None:
        return None, None
    cached = (catalog_cache or {}).get(key)
    if isinstance(cached, dict) and cached.get("etag") == remote["etag"]:
        value = cached.get("catalog")
    else:
        value = _download_s3_json(
            bucket=bucket, key=key, profile=profile, region=region
        )
    if not isinstance(value, dict):
        raise StaticPrintError(
            f"cached S3 discovery catalog is invalid: s3://{bucket}/{key}"
        )
    validate_catalog(value)
    if catalog_cache is not None:
        catalog_cache[key] = {"etag": remote["etag"], "catalog": value}
    return value, remote


def _load_latest_catalog_history(
    *,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    catalog_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    normalized = validate_prefix(prefix)
    history_prefix = (
        f"{normalized}/catalog-history/" if normalized else "catalog-history/"
    )
    objects: list[dict[str, str]] = []
    continuation: str | None = None
    while True:
        command = _aws_base(profile, region) + [
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--prefix",
            history_prefix,
            "--output",
            "json",
        ]
        if continuation:
            command += ["--continuation-token", continuation]
        page = _aws_json(command, operation="list catalog-history objects")
        contents = page.get("Contents", [])
        if not isinstance(contents, list):
            raise StaticPrintError(
                f"AWS catalog-history listing returned invalid Contents for "
                f"s3://{bucket}/{history_prefix}"
            )
        objects.extend(
            {
                "key": str(item.get("Key", "")),
                "etag": str(item.get("ETag", "")),
                "last_modified": str(item.get("LastModified", "")),
            }
            for item in contents
            if isinstance(item, dict)
            and str(item.get("Key", "")).startswith(history_prefix)
            and str(item.get("Key", "")).endswith(".json")
        )
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
        if not isinstance(token, str) or not token:
            raise StaticPrintError(
                "AWS catalog-history listing was truncated without a continuation token"
            )
        continuation = token
    objects.sort(key=lambda item: (item["last_modified"], item["key"]))
    if not objects:
        return None
    item = objects[-1]
    cached = (catalog_cache or {}).get(item["key"])
    if isinstance(cached, dict) and cached.get("etag") == item["etag"]:
        value = cached.get("catalog")
    else:
        value = _download_s3_json(
            bucket=bucket,
            key=item["key"],
            profile=profile,
            region=region,
        )
    if not isinstance(value, dict):
        raise StaticPrintError(
            f"cached catalog-history object is invalid: s3://{bucket}/{item['key']}"
        )
    validate_catalog(value)
    if catalog_cache is not None:
        catalog_cache[item["key"]] = {
            "etag": item["etag"],
            "catalog": value,
        }
    return value


def _previous_catalog(
    *,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    catalog_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    catalog, expected = load_s3_catalog(
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        catalog_cache=catalog_cache,
    )
    if catalog is not None:
        return catalog, expected
    if expected is not None:
        return None, expected
    return (
        _load_latest_catalog_history(
            bucket=bucket,
            prefix=prefix,
            profile=profile,
            region=region,
            catalog_cache=catalog_cache,
        ),
        None,
    )


def _entries_from_catalog(
    catalog: dict[str, Any],
    *,
    bucket: str | None = None,
    prefix: str = "",
    profile: str | None = None,
    region: str | None = None,
    catalog_cache: dict[str, dict[str, Any]] | None = None,
    include_source_sha256: bool = False,
    fetch_missing_source_sha256: bool = True,
) -> dict[str, list[dict[str, str]]]:
    """Project the FE catalog into operator publication entries.

    Source provenance lives in immutable bundle manifests rather than the FE
    catalog. Resolve cache misses concurrently because a mature catalog can
    contain hundreds of versions. Callers rendering a last-known local catalog
    may disable fetching and use whatever verified provenance is already cached.
    """

    published: dict[str, list[dict[str, str]]] = {}
    versions: list[tuple[dict[str, Any], dict[str, Any]]] = [
        (product, version)
        for product in catalog["products"]
        for version in product["versions"]
    ]
    source_digests: dict[tuple[str, str, str], str] = {}
    if include_source_sha256:
        if not bucket:
            raise StaticPrintError(
                "S3 bucket is required to load bundle source provenance"
            )

        def resolve_source(
            item: tuple[dict[str, Any], dict[str, Any]],
        ) -> tuple[tuple[str, str, str], str | None]:
            product, version = item
            identity = (
                str(product["design_id"]),
                str(product["product_profile_id"]),
                str(version["version_id"]),
            )
            manifest_path = str(version["manifest_path"])
            key = (
                f"{validate_prefix(prefix)}/{manifest_path}"
                if prefix
                else manifest_path
            )
            cached = (catalog_cache or {}).get(f"manifest:{key}")
            if (
                isinstance(cached, dict)
                and cached.get("manifest_sha256") == version["manifest_sha256"]
                and isinstance(cached.get("source_sha256"), str)
            ):
                return identity, str(cached["source_sha256"])
            if not fetch_missing_source_sha256:
                return identity, None
            return identity, _manifest_source_sha256(
                bucket=bucket,
                prefix=prefix,
                profile=profile,
                region=region,
                design_id=identity[0],
                product_profile_id=identity[1],
                version=version,
                catalog_cache=catalog_cache,
            )

        worker_count = min(8, max(1, len(versions)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for identity, digest in executor.map(resolve_source, versions):
                if digest is not None:
                    source_digests[identity] = digest

    for product, version in versions:
        source_sha256 = source_digests.get(
            (
                str(product["design_id"]),
                str(product["product_profile_id"]),
                str(version["version_id"]),
            )
        )
        entry = {
            "release_id": version["release_id"],
            "product_profile_id": product["product_profile_id"],
            "version_id": version["version_id"],
            "lifecycle_status": product["status"],
            "latest_version_id": product["latest_version_id"],
        }
        if source_sha256 is not None:
            entry["source_sha256"] = source_sha256
        published.setdefault(product["design_id"], []).append(entry)
    return published


def cached_release_entries(
    catalog: dict[str, Any],
    *,
    bucket: str,
    prefix: str,
    catalog_cache: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    """Build a non-network last-known publication view from a validated catalog."""

    validate_catalog(catalog)
    return _entries_from_catalog(
        catalog,
        bucket=bucket,
        prefix=prefix,
        catalog_cache=catalog_cache,
        include_source_sha256=True,
        fetch_missing_source_sha256=False,
    )


def _manifest_source_sha256(
    *,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    design_id: str,
    product_profile_id: str,
    version: dict[str, Any],
    catalog_cache: dict[str, dict[str, Any]] | None,
) -> str:
    manifest_path = str(version["manifest_path"])
    expected_sha256 = str(version["manifest_sha256"])
    key = f"{validate_prefix(prefix)}/{manifest_path}" if prefix else manifest_path
    cache_key = f"manifest:{key}"
    cached = (catalog_cache or {}).get(cache_key)
    if (
        isinstance(cached, dict)
        and cached.get("manifest_sha256") == expected_sha256
        and isinstance(cached.get("source_sha256"), str)
    ):
        return str(cached["source_sha256"])
    command = _aws_base(profile, region) + [
        "s3",
        "cp",
        f"s3://{bucket}/{key}",
        "-",
        "--only-show-errors",
    ]
    result = _run_bytes(command)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        raise StaticPrintError(
            f"could not read immutable S3 bundle manifest s3://{bucket}/{key}; "
            f"aws_exit={result.returncode}; "
            f"error={stderr or stdout}"
        )
    actual_sha256 = hashlib.sha256(result.stdout).hexdigest()
    if actual_sha256 != expected_sha256:
        raise StaticPrintError(
            f"S3 bundle manifest digest mismatch: s3://{bucket}/{key}; "
            f"expected_sha256={expected_sha256}; actual_sha256={actual_sha256}"
        )
    try:
        manifest = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StaticPrintError(
            f"S3 bundle manifest is not valid JSON: s3://{bucket}/{key}"
        ) from exc
    if not isinstance(manifest, dict):
        raise StaticPrintError(
            f"S3 bundle manifest root must be an object: s3://{bucket}/{key}"
        )
    expected_identity = {
        "schema": "pawmarvel-static-bundle-v1",
        "design_id": design_id,
        "product_profile_id": product_profile_id,
        "version_id": str(version["version_id"]),
    }
    mismatches = {
        field: {"expected": expected, "actual": manifest.get(field)}
        for field, expected in expected_identity.items()
        if manifest.get(field) != expected
    }
    if mismatches:
        raise StaticPrintError(
            f"S3 bundle manifest identity mismatch: s3://{bucket}/{key}; "
            f"mismatches={mismatches}"
        )
    source_sha256 = manifest.get("source_sha256")
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
    ):
        raise StaticPrintError(
            f"S3 bundle manifest source_sha256 is invalid: s3://{bucket}/{key}"
        )
    if catalog_cache is not None:
        catalog_cache[cache_key] = {
            "manifest_sha256": expected_sha256,
            "source_sha256": source_sha256,
        }
    return source_sha256


def load_s3_release_entries(
    *,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    catalog_cache: dict[str, dict[str, Any]] | None = None,
    include_source_sha256: bool = False,
) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
    """Read published versions from S3; release catalogs recover a missing index."""

    if not bucket.strip():
        raise StaticPrintError("S3 bucket must not be empty")
    catalog, _ = load_s3_catalog(
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        catalog_cache=catalog_cache,
    )
    if catalog is not None:
        published = _entries_from_catalog(
            catalog,
            bucket=bucket,
            prefix=prefix,
            profile=profile,
            region=region,
            catalog_cache=catalog_cache,
            include_source_sha256=include_source_sha256,
        )
        return published, {
            "source": "s3-catalog-index",
            "catalog_count": 1,
            "bundle_count": len(catalog["products"]),
            "version_count": sum(
                len(product["versions"]) for product in catalog["products"]
            ),
            "prefix": _catalog_key(prefix),
            "updated_at": catalog["updated_at"],
        }
    release_catalogs, inventory = _load_s3_release_catalogs(
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        catalog_cache=catalog_cache,
    )
    recovered = build_catalog(release_catalogs)
    return _entries_from_catalog(
        recovered,
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        catalog_cache=catalog_cache,
        include_source_sha256=include_source_sha256,
    ), {
        **inventory,
        "source": "s3-release-catalogs-recovery",
        "catalog_rebuild_required": bool(release_catalogs),
    }


def _load_s3_release_catalogs(
    *,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    catalog_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load all immutable release catalogs for index recovery."""

    normalized_prefix = validate_prefix(prefix)
    releases_prefix = (
        f"{normalized_prefix}/releases/" if normalized_prefix else "releases/"
    )
    objects: list[dict[str, str]] = []
    continuation: str | None = None
    while True:
        command = _aws_base(profile, region) + [
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--prefix",
            releases_prefix,
            "--output",
            "json",
        ]
        if continuation:
            command += ["--continuation-token", continuation]
        page = _aws_json(command, operation="list-objects-v2")
        contents = page.get("Contents", [])
        if not isinstance(contents, list):
            raise StaticPrintError(
                f"AWS list-objects-v2 returned invalid Contents for "
                f"s3://{bucket}/{releases_prefix}"
            )
        for item in contents:
            if not isinstance(item, dict):
                continue
            key = str(item.get("Key", ""))
            relative = key.removeprefix(releases_prefix)
            parts = relative.split("/")
            if len(parts) != 2 or parts[1] != "catalog.json":
                continue
            try:
                release_id = validate_release_id(parts[0])
            except StaticPrintError:
                continue
            objects.append(
                {
                    "key": key,
                    "etag": str(item.get("ETag", "")),
                    "release_id": release_id,
                }
            )
        if not page.get("IsTruncated"):
            break
        raw_token = page.get("NextContinuationToken")
        if not isinstance(raw_token, str) or not raw_token:
            raise StaticPrintError(
                "AWS list-objects-v2 truncated its result without a continuation token"
            )
        continuation = raw_token

    active_cache: dict[str, dict[str, Any]] = {}
    catalogs: list[dict[str, Any]] = []
    for item in sorted(objects, key=lambda value: value["key"]):
        key = item["key"]
        cached = (catalog_cache or {}).get(key)
        if isinstance(cached, dict) and cached.get("etag") == item["etag"]:
            catalog = cached.get("catalog")
        else:
            catalog = _download_s3_json(
                bucket=bucket,
                key=key,
                profile=profile,
                region=region,
            )
        if not isinstance(catalog, dict):
            raise StaticPrintError(
                f"cached S3 release catalog is invalid: s3://{bucket}/{key}"
            )
        if catalog.get("schema") != "pawmarvel-static-release-catalog-v1":
            raise StaticPrintError(
                f"unsupported S3 release catalog schema: s3://{bucket}/{key}; "
                f"actual={catalog.get('schema')!r}"
            )
        if catalog.get("release_id") != item["release_id"]:
            raise StaticPrintError(
                f"S3 release catalog identity mismatch: s3://{bucket}/{key}; "
                f"expected={item['release_id']!r}; actual={catalog.get('release_id')!r}"
            )
        bundles = catalog.get("bundles")
        if not isinstance(bundles, list) or not bundles:
            raise StaticPrintError(
                f"S3 release catalog must contain bundles: s3://{bucket}/{key}"
            )
        for index, entry in enumerate(bundles):
            if not isinstance(entry, dict):
                raise StaticPrintError(
                    f"S3 release catalog bundle {index} must be an object: "
                    f"s3://{bucket}/{key}"
                )
            design_id = validate_design_id(
                str(entry.get("design_id", "")), "S3 catalog design ID"
            )
            profile_id = validate_id(
                str(entry.get("product_profile_id", "")),
                "S3 catalog product profile ID",
            )
            version_id = validate_version(str(entry.get("version_id", "")))
            del design_id, profile_id, version_id
        catalogs.append(catalog)
        active_cache[key] = {"etag": item["etag"], "catalog": catalog}

    if catalog_cache is not None:
        catalog_cache.clear()
        catalog_cache.update(active_cache)
    return catalogs, {
        "catalog_count": len(catalogs),
        "bundle_count": sum(len(catalog["bundles"]) for catalog in catalogs),
        "prefix": releases_prefix,
    }


def _remote_hash(
    *, bucket: str, key: str, profile: str | None, region: str | None
) -> str | None:
    value = _remote_object(bucket=bucket, key=key, profile=profile, region=region)
    return value["sha256"] if value is not None else None


def _upload_once(
    *,
    path: Path,
    bucket: str,
    key: str,
    profile: str | None,
    region: str | None,
    known_missing: bool = False,
) -> str:
    digest = sha256_file(path)
    existing = (
        None
        if known_missing
        else _remote_hash(bucket=bucket, key=key, profile=profile, region=region)
    )
    if existing is not None:
        if existing == digest:
            return "existing-match"
        raise StaticPrintError(
            f"immutable S3 object conflict: s3://{bucket}/{key}; "
            f"expected_sha256={digest}; remote_sha256={existing or '<missing>'}"
        )
    content_type = "application/json" if path.suffix == ".json" else "image/png"
    command = _aws_base(profile, region) + [
        "s3api",
        "put-object",
        "--bucket",
        bucket,
        "--key",
        key,
        "--body",
        str(path.resolve()),
        "--content-type",
        content_type,
        "--metadata",
        f"sha256={digest}",
        "--if-none-match",
        "*",
    ]
    result = _run(command)
    if result.returncode != 0:
        remote = _remote_hash(bucket=bucket, key=key, profile=profile, region=region)
        if remote == digest:
            return "concurrent-existing-match"
        raise StaticPrintError(
            f"S3 upload failed: s3://{bucket}/{key}; aws_exit={result.returncode}; "
            f"error={result.stderr.strip() or result.stdout.strip()}"
        )
    verified = _remote_hash(bucket=bucket, key=key, profile=profile, region=region)
    if verified != digest:
        raise StaticPrintError(
            f"S3 verification failed: s3://{bucket}/{key}; "
            f"expected_sha256={digest}; remote_sha256={verified or '<missing>'}"
        )
    return "uploaded"


def _upload(
    *,
    path: Path,
    bucket: str,
    key: str,
    profile: str | None,
    region: str | None,
    known_missing: bool = False,
    max_attempts: int | None = None,
    retry_base_seconds: float | None = None,
) -> str:
    _, configured_attempts, configured_backoff = _s3_retry_settings()
    attempts = max_attempts or configured_attempts
    backoff = retry_base_seconds or configured_backoff
    return str(
        _retry_s3_call(
            lambda: _upload_once(
                path=path,
                bucket=bucket,
                key=key,
                profile=profile,
                region=region,
                known_missing=known_missing,
            ),
            label=f"S3 immutable upload s3://{bucket}/{key}",
            max_attempts=attempts,
            retry_base_seconds=backoff,
        )
    )


def _upload_catalog_once(
    *,
    path: Path,
    bucket: str,
    key: str,
    profile: str | None,
    region: str | None,
    expected: dict[str, str] | None,
) -> str:
    """Conditionally replace the one mutable FE discovery object."""

    digest = sha256_file(path)
    command = _aws_base(profile, region) + [
        "s3api",
        "put-object",
        "--bucket",
        bucket,
        "--key",
        key,
        "--body",
        str(path.resolve()),
        "--content-type",
        "application/json",
        "--cache-control",
        "no-cache",
        "--metadata",
        f"sha256={digest}",
    ]
    if expected is None:
        command += ["--if-none-match", "*"]
    else:
        command += ["--if-match", expected["etag"]]
    result = _run(command)
    if result.returncode != 0:
        current = _remote_object(bucket=bucket, key=key, profile=profile, region=region)
        if current is not None and current["sha256"] == digest:
            return "concurrent-existing-match"
        raise StaticPrintError(
            f"S3 discovery catalog update conflict: s3://{bucket}/{key}; "
            f"aws_exit={result.returncode}; "
            f"error={result.stderr.strip() or result.stdout.strip()}; "
            "another operator may have published concurrently—refresh S3 state and retry"
        )
    verified = _remote_object(bucket=bucket, key=key, profile=profile, region=region)
    if verified is None or verified["sha256"] != digest:
        raise StaticPrintError(
            f"S3 discovery catalog verification failed: s3://{bucket}/{key}; "
            f"expected_sha256={digest}; "
            f"remote_sha256={verified['sha256'] if verified else '<missing>'}"
        )
    return "updated" if expected is not None else "created"


def _upload_catalog(
    *,
    path: Path,
    bucket: str,
    key: str,
    profile: str | None,
    region: str | None,
    expected: dict[str, str] | None,
    max_attempts: int | None = None,
    retry_base_seconds: float | None = None,
) -> str:
    _, configured_attempts, configured_backoff = _s3_retry_settings()
    attempts = max_attempts or configured_attempts
    backoff = retry_base_seconds or configured_backoff
    return str(
        _retry_s3_call(
            lambda: _upload_catalog_once(
                path=path,
                bucket=bucket,
                key=key,
                profile=profile,
                region=region,
                expected=expected,
            ),
            label=f"S3 discovery catalog update s3://{bucket}/{key}",
            max_attempts=attempts,
            retry_base_seconds=backoff,
        )
    )


def _publish_catalog_version(
    *,
    path: Path,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    expected: dict[str, str] | None,
) -> dict[str, str]:
    catalog = read_json(path)
    validate_catalog(catalog)
    digest = sha256_file(path)
    compact_time = (
        str(catalog["updated_at"]).replace("-", "").replace(":", "").replace("+", "")
    )
    normalized = validate_prefix(prefix)
    relative = f"catalog-history/{compact_time}-{digest[:16]}.json"
    history_key = f"{normalized}/{relative}" if normalized else relative
    stable_key = _catalog_key(prefix)
    stable_status = _upload_catalog(
        path=path,
        bucket=bucket,
        key=stable_key,
        profile=profile,
        region=region,
        expected=expected,
    )
    # Only snapshot a catalog after its conditional stable update succeeds.
    # A losing concurrent publisher must not leave an uncommitted state that a
    # later recovery could mistake for authoritative history.
    history_status = _upload(
        path=path,
        bucket=bucket,
        key=history_key,
        profile=profile,
        region=region,
    )
    return {
        "history_uri": f"s3://{bucket}/{history_key}",
        "history_status": history_status,
        "stable_uri": f"s3://{bucket}/{stable_key}",
        "stable_status": stable_status,
    }


def _rebuild_and_upload_catalog(
    *,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    output_path: Path,
    activate_bundle_ids: set[str] | None = None,
    catalog_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    previous, expected = _previous_catalog(
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        catalog_cache=catalog_cache,
    )
    releases, _ = _load_s3_release_catalogs(
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        catalog_cache=catalog_cache,
    )
    catalog = build_catalog(
        releases,
        previous=previous,
        activate_bundle_ids=activate_bundle_ids,
    )
    write_json(output_path, catalog)
    publication = _publish_catalog_version(
        path=output_path,
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        expected=expected,
    )
    return {
        "uri": publication["stable_uri"],
        "sha256": sha256_file(output_path),
        "status": publication["stable_status"],
        "history_uri": publication["history_uri"],
        "history_status": publication["history_status"],
        "product_count": len(catalog["products"]),
    }


def _store_local_catalog(exchange_root: Path, source: Path) -> str:
    """Keep a non-authoritative local mirror for operator audit/debugging."""

    destination = exchange_root / "catalog.json"
    catalog = read_json(source)
    validate_catalog(catalog)
    write_json(destination, catalog)
    return str(destination.resolve())


def rebuild_discovery_catalog(
    *,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    exchange_root: Path,
    catalog_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rebuild the stable FE catalog without creating a release."""

    staging = exchange_root / ".catalog-staging"
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / "catalog.json"
    result = _rebuild_and_upload_catalog(
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        output_path=path,
        catalog_cache=catalog_cache,
    )
    result["local_catalog"] = _store_local_catalog(exchange_root, path)
    shutil.rmtree(staging)
    if catalog_cache is not None:
        catalog_cache.clear()
    return result


def update_product_lifecycle(
    *,
    bucket: str,
    prefix: str,
    profile: str | None,
    region: str | None,
    exchange_root: Path,
    bundle_ids: list[str],
    status: str,
    reason: str,
    catalog_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Publish a non-destructive active/retired catalog transition."""

    catalog, expected = _previous_catalog(
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        catalog_cache=catalog_cache,
    )
    if catalog is None:
        releases, _ = _load_s3_release_catalogs(
            bucket=bucket,
            prefix=prefix,
            profile=profile,
            region=region,
            catalog_cache=catalog_cache,
        )
        catalog = build_catalog(releases)
    updated = set_product_status(
        catalog,
        bundle_ids=bundle_ids,
        status=status,
        reason=reason,
    )
    staging = exchange_root / ".catalog-staging"
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / "catalog.json"
    write_json(path, updated)
    publication = _publish_catalog_version(
        path=path,
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        region=region,
        expected=expected,
    )
    catalog_sha256 = sha256_file(path)
    local_catalog = _store_local_catalog(exchange_root, path)
    shutil.rmtree(staging)
    if catalog_cache is not None:
        catalog_cache.clear()
    return {
        "catalog_uri": publication["stable_uri"],
        "catalog_history_uri": publication["history_uri"],
        "catalog_sha256": catalog_sha256,
        "local_catalog": local_catalog,
        "status": status,
        "bundle_ids": sorted(set(bundle_ids)),
        "upload_status": publication["stable_status"],
    }


def publication_plan(
    plan: ReleasePlan, *, bucket: str, prefix: str
) -> list[dict[str, Any]]:
    validate_release(plan)
    prefix = validate_prefix(prefix)
    files = [path for path in plan.staging_root.rglob("*") if path.is_file()]
    catalog = plan.catalog_path.resolve()
    manifests = {path.resolve() for path in plan.bundle_manifests}
    ordered = sorted(
        files,
        key=lambda path: (
            2 if path.resolve() == catalog else 1 if path.resolve() in manifests else 0,
            str(path),
        ),
    )
    result: list[dict[str, Any]] = []
    for path in ordered:
        relative = str(path.relative_to(plan.staging_root)).replace("\\", "/")
        key = f"{prefix}/{relative}" if prefix else relative
        result.append(
            {
                "source": str(path.resolve()),
                "key": key,
                "uri": f"s3://{bucket}/{key}",
                "sha256": sha256_file(path),
            }
        )
    return result


def _preflight_immutable_objects(
    objects: list[dict[str, Any]],
    *,
    bucket: str,
    profile: str | None,
    region: str | None,
    max_concurrency: int | None = None,
    max_attempts: int | None = None,
    retry_base_seconds: float | None = None,
) -> dict[str, str]:
    """Validate the complete immutable upload plan before writing any object."""

    configured_workers, configured_attempts, configured_backoff = (
        _s3_retry_settings()
    )
    worker_count = min(
        max_concurrency or configured_workers,
        max(1, len(objects)),
    )
    attempts = max_attempts or configured_attempts
    backoff = retry_base_seconds or configured_backoff
    statuses: dict[str, str] = {}
    conflicts: list[dict[str, str]] = []
    failures: list[str] = []

    def inspect(item: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        key = str(item["key"])
        remote = _retry_s3_call(
            lambda: _remote_hash(
                bucket=bucket,
                key=key,
                profile=profile,
                region=region,
            ),
            label=f"S3 immutable preflight s3://{bucket}/{key}",
            max_attempts=attempts,
            retry_base_seconds=backoff,
        )
        return item, remote

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="static-s3-preflight",
    ) as executor:
        futures = {executor.submit(inspect, item): item for item in objects}
        for future in as_completed(futures):
            item = futures[future]
            try:
                _, remote = future.result()
            except Exception as exc:  # noqa: BLE001 - report every failed object
                failures.append(f"{item['uri']}: {exc}")
                continue
            key = str(item["key"])
            if remote is None:
                statuses[key] = "missing"
            elif remote == item["sha256"]:
                statuses[key] = "existing-match"
            else:
                conflicts.append(
                    {
                        "uri": str(item["uri"]),
                        "expected_sha256": str(item["sha256"]),
                        "remote_sha256": remote,
                    }
                )
    if failures:
        raise StaticPrintError(
            "immutable S3 preflight could not inspect every object; "
            f"failure_count={len(failures)}; first_failures={failures[:3]}"
        )
    if conflicts:
        raise StaticPrintError(
            "immutable S3 preflight failed before upload; "
            f"conflict_count={len(conflicts)}; conflicts={conflicts}; "
            "an existing bundle version cannot be changed—generate the next product "
            "version, or restore the local artifact that originally produced the "
            "published manifest"
        )
    return statuses


def publish_release(
    plan: ReleasePlan,
    *,
    exchange_root: Path,
    bucket: str,
    prefix: str,
    aws_profile: str | None,
    region: str | None,
    execute: bool,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
    upload_max_concurrency: int | None = None,
    upload_max_attempts: int | None = None,
    upload_retry_base_seconds: float | None = None,
) -> dict[str, Any]:
    if not bucket.strip():
        raise StaticPrintError("S3 bucket must not be empty")
    configured_workers, configured_attempts, configured_backoff = (
        _s3_retry_settings()
    )
    worker_limit = (
        configured_workers
        if upload_max_concurrency is None
        else upload_max_concurrency
    )
    max_attempts = (
        configured_attempts if upload_max_attempts is None else upload_max_attempts
    )
    retry_base_seconds = (
        configured_backoff
        if upload_retry_base_seconds is None
        else upload_retry_base_seconds
    )
    if isinstance(worker_limit, bool) or not 1 <= worker_limit <= MAX_S3_CONCURRENCY:
        raise StaticPrintError(
            "upload_max_concurrency must be between 1 and "
            f"{MAX_S3_CONCURRENCY}; actual={worker_limit!r}"
        )
    if isinstance(max_attempts, bool) or not 1 <= max_attempts <= 20:
        raise StaticPrintError(
            f"upload_max_attempts must be between 1 and 20; actual={max_attempts!r}"
        )
    if (
        isinstance(retry_base_seconds, bool)
        or not 0 < retry_base_seconds <= 300
    ):
        raise StaticPrintError(
            "upload_retry_base_seconds must be greater than 0 and at most 300; "
            f"actual={retry_base_seconds!r}"
        )
    objects = publication_plan(plan, bucket=bucket, prefix=prefix)
    public_objects = [
        {key: value for key, value in item.items() if key != "source"}
        for item in objects
    ]
    result: dict[str, Any] = {
        "release_id": plan.release_id,
        "execute": execute,
        "bucket": bucket,
        "prefix": validate_prefix(prefix),
        "catalog": objects[-1]["uri"],
        "discovery_catalog": f"s3://{bucket}/{_catalog_key(prefix)}",
        "objects": public_objects,
        "upload_scheduler": {
            "max_concurrency": worker_limit,
            "max_attempts": max_attempts,
            "retry_base_seconds": retry_base_seconds,
        },
    }
    if progress:
        progress(
            "preflight-start",
            {"release_id": plan.release_id, "object_count": len(objects)},
        )
    preflight = _preflight_immutable_objects(
        objects,
        bucket=bucket,
        profile=aws_profile,
        region=region,
        max_concurrency=worker_limit,
        max_attempts=max_attempts,
        retry_base_seconds=retry_base_seconds,
    )
    if progress:
        progress(
            "preflight-complete",
            {
                "release_id": plan.release_id,
                "object_count": len(objects),
                "existing_count": sum(
                    status == "existing-match" for status in preflight.values()
                ),
                "missing_count": sum(
                    status == "missing" for status in preflight.values()
                ),
            },
        )
    if not execute:
        if progress:
            progress(
                "validation-complete",
                {"release_id": plan.release_id, "object_count": len(objects)},
            )
        shutil.rmtree(plan.staging_root)
        return result
    uploaded_by_key: dict[str, dict[str, str]] = {}

    def upload_stage(stage: str, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        failures: list[str] = []
        worker_count = min(worker_limit, len(items))
        if progress:
            progress(
                "upload-stage-start",
                {
                    "stage": stage,
                    "object_count": len(items),
                    "worker_count": worker_count,
                },
            )
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix=f"static-s3-{stage}",
        ) as executor:
            futures = {}
            for item in items:
                if progress:
                    progress("upload-start", dict(item))
                status = preflight[str(item["key"])]
                if status == "existing-match":
                    uploaded_by_key[str(item["key"])] = {
                        "uri": str(item["uri"]),
                        "status": status,
                    }
                    if progress:
                        progress("upload-complete", {**item, "status": status})
                    continue
                future = executor.submit(
                    _upload,
                    path=Path(item["source"]),
                    bucket=bucket,
                    key=str(item["key"]),
                    profile=aws_profile,
                    region=region,
                    known_missing=True,
                    max_attempts=max_attempts,
                    retry_base_seconds=retry_base_seconds,
                )
                futures[future] = item
            for future in as_completed(futures):
                item = futures[future]
                try:
                    status = str(future.result())
                except Exception as exc:  # noqa: BLE001 - preserve sibling uploads
                    failures.append(f"{item['uri']}: {exc}")
                    if progress:
                        progress("upload-failed", {**item, "error": str(exc)})
                    continue
                uploaded_by_key[str(item["key"])] = {
                    "uri": str(item["uri"]),
                    "status": status,
                }
                if progress:
                    progress("upload-complete", {**item, "status": status})
        if failures:
            raise StaticPrintError(
                f"S3 {stage} upload stage failed after retries; "
                f"failure_count={len(failures)}; first_failures={failures[:3]}; "
                "matching successful immutable uploads are safe to reuse on retry"
            )

    manifest_sources = {str(path.resolve()) for path in plan.bundle_manifests}
    catalog_source = str(plan.catalog_path.resolve())
    assets = [
        item
        for item in objects
        if str(item["source"]) not in manifest_sources
        and str(item["source"]) != catalog_source
    ]
    manifests = [
        item for item in objects if str(item["source"]) in manifest_sources
    ]
    release_catalogs = [
        item for item in objects if str(item["source"]) == catalog_source
    ]
    if len(release_catalogs) != 1:
        raise StaticPrintError(
            "publication plan must contain exactly one release catalog; "
            f"actual={len(release_catalogs)}"
        )
    # Assets must exist before their bundle manifests; the release catalog is
    # the immutable commit marker and is therefore uploaded last.
    upload_stage("asset", assets)
    upload_stage("manifest", manifests)
    upload_stage("release-catalog", release_catalogs)
    uploaded = [uploaded_by_key[str(item["key"])] for item in objects]
    release_catalog = read_json(plan.catalog_path)
    activated = {str(entry["bundle_id"]) for entry in release_catalog["bundles"]}
    if progress:
        progress("catalog-start", {"release_id": plan.release_id})
    discovery = _rebuild_and_upload_catalog(
        bucket=bucket,
        prefix=prefix,
        profile=aws_profile,
        region=region,
        output_path=plan.staging_root / "catalog.json",
        activate_bundle_ids=activated,
    )
    discovery["local_catalog"] = _store_local_catalog(
        exchange_root, plan.staging_root / "catalog.json"
    )
    receipt = {
        "schema": "pawmarvel-static-publication-receipt-v1",
        "release_id": plan.release_id,
        "published_at": utc_now(),
        "bucket": bucket,
        "prefix": validate_prefix(prefix),
        "catalog_uri": objects[-1]["uri"],
        "catalog_sha256": sha256_file(plan.catalog_path),
        "discovery_catalog_uri": discovery["uri"],
        "discovery_catalog_sha256": discovery["sha256"],
        "discovery_catalog_history_uri": discovery["history_uri"],
        "objects": uploaded,
    }
    receipt_path = exchange_root / "publications" / f"{plan.release_id}.json"
    write_json(receipt_path, receipt, exclusive=True)
    result["receipt"] = str(receipt_path.resolve())
    result["discovery_catalog_update"] = discovery
    if progress:
        progress(
            "publication-complete",
            {
                "release_id": plan.release_id,
                "object_count": len(uploaded),
                "catalog_uri": discovery["uri"],
            },
        )
    shutil.rmtree(plan.staging_root)
    return result
