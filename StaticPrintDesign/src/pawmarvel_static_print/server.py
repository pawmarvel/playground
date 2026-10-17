from __future__ import annotations

import fcntl
import hashlib
import json
import mimetypes
import os
import secrets
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from time import monotonic, sleep
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .authoring import (
    annotate_design_collections,
    design_canvas_policy,
    discover_designs,
    generate_design,
    initialize_workspace,
    render_scale,
)
from .bundles import BundleSelection, prepare_release
from .core import (
    StaticPrintError,
    bundle_id_for,
    design_authoring_path,
    design_identity,
    design_source_path,
    discover_profiles,
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
from .history_cleanup import (
    create_history_cleanup_proposal,
    execute_history_cleanup,
    latest_cleanup_state,
)
from .image_quality import (
    PRINT_READINESS_SCHEMA,
    print_readiness_report_for_path,
    print_readiness_warning,
)
from .lifecycle import (
    OPAQUE_FULL_BLEED,
    PRESERVE_TRANSPARENCY,
    canvas_policy_for_report,
    generate_subject_variants,
    import_review_collection,
    load_subject_catalog,
    move_seed,
    move_seeds,
    pool_root,
    save_subject_generation_requirements,
    transparency_report,
)
from .operations import (
    append_operation_event,
    finish_operation,
    mark_operation_cleaned,
    mark_running_operations_interrupted,
    operation_snapshot,
    operation_snapshots,
    start_operation,
    update_operation_item,
)
from .publisher import (
    cached_release_entries,
    load_s3_release_entries,
    publish_release,
    rebuild_discovery_catalog,
    update_product_lifecycle,
)
from .recovery import cleanup_operation_staging, reconcile_workspace
from .reviews import (
    list_feedback,
    list_operator_decisions,
    save_feedbacks,
    save_operator_decision,
    save_operator_decisions,
)

SEED_TRANSITIONS = {
    "graduate": ("active", "graduated"),
    "abandon": ("active", "abandoned"),
    "restore": ("abandoned", "active"),
}

PUBLICATION_PROVENANCE_CACHE_SCHEMA = (
    "pawmarvel-static-publication-provenance-cache-v1"
)
S3_STATUS_REFRESH_SECONDS = 60
DEFAULT_IMAGE_REQUESTS_PER_MINUTE = 50
DEFAULT_IMAGE_MAX_CONCURRENCY = 50
MAX_IMAGE_BATCH_SETTING = 500


class _ImageRequestRateLimiter:
    """Queue paid image starts and apply account-wide throttle deferrals."""

    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self.requests_per_minute = requests_per_minute
        self.minimum_interval_seconds = 60.0 / requests_per_minute
        self._clock = clock
        self._sleeper = sleeper
        self._lock = Lock()
        self._next_start = 0.0
        self._blocked_until = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                request_start = max(self._next_start, self._blocked_until)
                if now >= request_start:
                    self._next_start = now + self.minimum_interval_seconds
                    return
                delay = request_start - now
            # Do not reserve a future slot before sleeping. Other workers can
            # therefore observe a later global defer after a provider 429.
            self._sleeper(delay)

    def defer(self, delay_seconds: float) -> None:
        if delay_seconds <= 0:
            return
        with self._lock:
            self._blocked_until = max(
                self._blocked_until, self._clock() + delay_seconds
            )


def _positive_batch_setting(
    body: dict[str, Any],
    *,
    request_key: str,
    environment_key: str,
    default: int,
) -> int:
    raw = body.get(request_key)
    if raw is None:
        raw = os.environ.get(environment_key, str(default))
    if isinstance(raw, bool):
        raise StaticPrintError(
            f"{request_key} must be a positive integer; actual={raw!r}"
        )
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise StaticPrintError(
            f"{request_key} must be a positive integer; actual={raw!r}; "
            f"environment={environment_key}"
        ) from exc
    if not 1 <= value <= MAX_IMAGE_BATCH_SETTING:
        raise StaticPrintError(
            f"{request_key} must be between 1 and {MAX_IMAGE_BATCH_SETTING}; "
            f"actual={value}; environment={environment_key}"
        )
    return value


def _subject_batch_limits(body: dict[str, Any]) -> tuple[int, int]:
    """Resolve and validate the durable paid-image scheduling limits."""

    requests_per_minute = _positive_batch_setting(
        body,
        request_key="image_requests_per_minute",
        environment_key="STATIC_PRINT_IMAGE_REQUESTS_PER_MINUTE",
        default=DEFAULT_IMAGE_REQUESTS_PER_MINUTE,
    )
    max_concurrency = _positive_batch_setting(
        body,
        request_key="image_max_concurrency",
        environment_key="STATIC_PRINT_IMAGE_MAX_CONCURRENCY",
        default=DEFAULT_IMAGE_MAX_CONCURRENCY,
    )
    return requests_per_minute, max_concurrency


def _positive_float_setting(
    body: dict[str, Any],
    *,
    request_key: str,
    environment_key: str,
    default: float,
) -> float:
    raw = body.get(request_key)
    if raw is None:
        raw = os.environ.get(environment_key, str(default))
    if isinstance(raw, bool):
        raise StaticPrintError(
            f"{request_key} must be a positive number; actual={raw!r}"
        )
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise StaticPrintError(
            f"{request_key} must be a positive number; actual={raw!r}; "
            f"environment={environment_key}"
        ) from exc
    if not 0 < value <= 300:
        raise StaticPrintError(
            f"{request_key} must be greater than 0 and at most 300; "
            f"actual={value}; environment={environment_key}"
        )
    return value


def _boolean_setting(
    body: dict[str, Any],
    *,
    request_key: str,
    environment_key: str,
    default: bool,
) -> bool:
    raw = body.get(request_key)
    if raw is None:
        raw = os.environ.get(environment_key, "true" if default else "false")
    if isinstance(raw, bool):
        return raw
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise StaticPrintError(
        f"{request_key} must be true or false; actual={raw!r}; "
        f"environment={environment_key}"
    )


def _subject_retry_settings(body: dict[str, Any]) -> tuple[int, float]:
    max_attempts = _positive_batch_setting(
        body,
        request_key="image_max_attempts",
        environment_key="STATIC_PRINT_IMAGE_MAX_ATTEMPTS",
        default=4,
    )
    retry_base_seconds = _positive_float_setting(
        body,
        request_key="image_retry_base_seconds",
        environment_key="STATIC_PRINT_IMAGE_RETRY_BASE_SECONDS",
        default=10.0,
    )
    return max_attempts, retry_base_seconds


def _operation_recommendation(message: str) -> str:
    lowered = message.lower()
    if "token has expired" in lowered or "sso" in lowered:
        return "Run `aws sso login --profile <profile>`, then resume the operation."
    if "config profile" in lowered and "could not be found" in lowered:
        return "Correct AWS_PROFILE in work/static-print.env and restart the server."
    if "accessdenied" in lowered or "access denied" in lowered:
        return "Verify the AWS role can read and write the configured S3 prefix."
    if "no such file" in lowered or "does not exist" in lowered:
        return "Open the reported absolute path and regenerate or restore the missing artifact."
    if "immutable" in lowered or "already exists" in lowered or "conflict" in lowered:
        return (
            "Refresh operator state. If the published version has different bytes, "
            "generate the next product version; changing only the release ID cannot "
            "replace an immutable bundle version."
        )
    if "timeout" in lowered or "connection" in lowered or "network" in lowered:
        return "Check network connectivity and credentials, then retry the batch or resume when that action is available."
    return "Inspect the failed work unit and its absolute paths, correct the input, then retry, resume when available, or dismiss the report."


def _seed_transition(action: str) -> tuple[str, str]:
    if action not in SEED_TRANSITIONS:
        raise StaticPrintError(
            f"action must be one of {sorted(SEED_TRANSITIONS)}; actual={action!r}"
        )
    return SEED_TRANSITIONS[action]


def _published_entries(exchange_root: Path) -> dict[str, list[dict[str, str]]]:
    """Return locally receipted publication entries grouped by design ID."""
    published: dict[str, list[dict[str, str]]] = {}
    publications = exchange_root / "publications"
    if not publications.is_dir():
        return published
    for receipt_path in sorted(publications.glob("*.json")):
        try:
            receipt = read_json(receipt_path)
            release_id = str(receipt.get("release_id", ""))
            catalog = read_json(
                exchange_root / "releases" / release_id / "catalog.json"
            )
        except StaticPrintError:
            continue
        for entry in catalog.get("bundles", []):
            if not isinstance(entry, dict):
                continue
            design_id = str(entry.get("design_id", ""))
            published.setdefault(design_id, []).append(
                {
                    "release_id": release_id,
                    "product_profile_id": str(entry.get("product_profile_id", "")),
                    "version_id": str(entry.get("version_id", "")),
                }
            )
    return published


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _load_publication_provenance_cache(
    path: Path,
    *,
    bucket: str,
    prefix: str,
) -> dict[str, dict[str, Any]]:
    """Load the verified, operator-only manifest provenance cache."""

    if not path.is_file():
        return {}
    value = read_json(path)
    required = {"schema", "bucket", "prefix", "updated_at", "manifests"}
    if set(value) != required:
        raise StaticPrintError(
            f"publication provenance cache fields mismatch: {path.resolve()}"
        )
    if value.get("schema") != PUBLICATION_PROVENANCE_CACHE_SCHEMA:
        raise StaticPrintError(
            f"unsupported publication provenance cache schema: {path.resolve()}"
        )
    if value.get("bucket") != bucket or value.get("prefix") != prefix:
        return {}
    manifests = value.get("manifests")
    if not isinstance(manifests, list):
        raise StaticPrintError(
            f"publication provenance cache manifests must be an array: {path.resolve()}"
        )
    cache: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(manifests):
        if not isinstance(item, dict) or set(item) != {
            "object_key",
            "manifest_sha256",
            "source_sha256",
        }:
            raise StaticPrintError(
                "publication provenance cache manifest fields mismatch; "
                f"path={path.resolve()}; index={index}"
            )
        object_key = item.get("object_key")
        if not isinstance(object_key, str) or not object_key:
            raise StaticPrintError(
                "publication provenance cache object_key is invalid; "
                f"path={path.resolve()}; index={index}"
            )
        if not _valid_sha256(item.get("manifest_sha256")) or not _valid_sha256(
            item.get("source_sha256")
        ):
            raise StaticPrintError(
                "publication provenance cache digest is invalid; "
                f"path={path.resolve()}; index={index}"
            )
        cache[f"manifest:{object_key}"] = {
            "manifest_sha256": item["manifest_sha256"],
            "source_sha256": item["source_sha256"],
        }
    return cache


def _write_publication_provenance_cache(
    path: Path,
    *,
    bucket: str,
    prefix: str,
    cache: dict[str, dict[str, Any]],
) -> None:
    manifests = []
    for cache_key, item in sorted(cache.items()):
        if not cache_key.startswith("manifest:"):
            continue
        object_key = cache_key.removeprefix("manifest:")
        if not _valid_sha256(item.get("manifest_sha256")) or not _valid_sha256(
            item.get("source_sha256")
        ):
            continue
        manifests.append(
            {
                "object_key": object_key,
                "manifest_sha256": item["manifest_sha256"],
                "source_sha256": item["source_sha256"],
            }
        )
    write_json(
        path,
        {
            "schema": PUBLICATION_PROVENANCE_CACHE_SCHEMA,
            "bucket": bucket,
            "prefix": prefix,
            "updated_at": utc_now(),
            "manifests": manifests,
        },
    )


def _product_coverage_records(
    design: dict[str, Any],
    profile_ids: list[str],
    *,
    publication_known: bool = True,
) -> list[dict[str, Any]]:
    """Project immutable local/public artifacts into operator product states."""

    current_publications = design.get("publication", {}).get("current_entries", [])
    candidates = design.get("bundle_candidates", [])
    records: list[dict[str, Any]] = []
    for profile_id in profile_ids:
        generated_versions = sorted(
            {
                str(version["version_id"])
                for version in design.get("versions", [])
                if any(
                    output.get("product_profile_id") == profile_id
                    for output in version.get("profile_outputs", [])
                )
            },
            reverse=True,
        )
        published = sorted(
            (
                entry
                for entry in current_publications
                if entry.get("product_profile_id") == profile_id
            ),
            key=lambda entry: (entry["version_id"], entry["release_id"]),
            reverse=True,
        )
        published_versions = {entry["version_id"] for entry in published}
        lifecycle_status = (
            str(published[0].get("lifecycle_status", "active")) if published else None
        )
        unpublished_candidates = sorted(
            (
                candidate
                for candidate in candidates
                if candidate.get("product_profile_id") == profile_id
                and candidate.get("version_id") not in published_versions
            ),
            key=lambda candidate: str(candidate["version_id"]),
            reverse=True,
        )
        if not publication_known:
            state = "release-status-unavailable"
        elif unpublished_candidates:
            state = "update-ready" if published else "ready"
        elif published:
            state = "retired" if lifecycle_status == "retired" else "published"
        elif generated_versions:
            state = "local-attention"
        else:
            state = "missing"
        records.append(
            {
                "product_profile_id": profile_id,
                "state": state,
                "latest_generated_version": (
                    generated_versions[0] if generated_versions else None
                ),
                "latest_candidate_version": (
                    str(unpublished_candidates[0]["version_id"])
                    if unpublished_candidates
                    else None
                ),
                "latest_published_version": (
                    str(published[0]["version_id"]) if published else None
                ),
                "latest_release_id": (
                    str(published[0]["release_id"]) if published else None
                ),
                "lifecycle_status": lifecycle_status,
            }
        )
    return records


def _next_release_id(
    exchange_root: Path, published_release_ids: set[str] | None = None
) -> str:
    """Allocate the next local release ID for today's UTC date."""
    date = utc_now()[:10]
    used: set[int] = set()
    for root in (
        exchange_root / "releases",
        exchange_root / "publications",
    ):
        if not root.is_dir():
            continue
        for path in root.iterdir():
            candidate = path.stem if path.is_file() else path.name
            if not candidate.startswith(f"{date}."):
                continue
            try:
                validate_release_id(candidate)
            except StaticPrintError:
                continue
            used.add(int(candidate.rsplit(".", 1)[1]))
    for candidate in published_release_ids or set():
        if not candidate.startswith(f"{date}."):
            continue
        try:
            validate_release_id(candidate)
        except StaticPrintError:
            continue
        used.add(int(candidate.rsplit(".", 1)[1]))
    next_number = max(used, default=0) + 1
    if next_number > 999:
        raise StaticPrintError(
            f"automatic release IDs are exhausted for {date}; enter a valid explicit release ID"
        )
    return f"{date}.{next_number:03d}"


def _generation_plans(
    value: Any, available_profile_ids: set[str]
) -> list[dict[str, str]]:
    """Validate explicit design/profile/version work units before any writes."""
    if not isinstance(value, list) or not value:
        raise StaticPrintError("plans must be a non-empty array")
    plans: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    required = {"design_id", "product_profile_id"}
    allowed = required | {"version_id"}
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise StaticPrintError(
                f"generation plan at index {index} must be an object"
            )
        missing = required - set(raw)
        unknown = set(raw) - allowed
        if missing or unknown:
            raise StaticPrintError(
                f"generation plan fields mismatch at index {index}; "
                f"missing={sorted(missing)}; unknown={sorted(unknown)}"
            )
        design_id = design_identity(str(raw["design_id"])).design_id
        profile_id = str(raw["product_profile_id"])
        if profile_id not in available_profile_ids:
            raise StaticPrintError(
                f"unknown product profile in generation plan at index {index}; "
                f"actual={profile_id!r}; available={sorted(available_profile_ids)}"
            )
        raw_version = str(raw.get("version_id", "auto"))
        version_id = "auto" if raw_version == "auto" else validate_version(raw_version)
        key = (design_id, profile_id)
        if key in seen:
            raise StaticPrintError(
                f"duplicate generation plan at index {index}; "
                f"design_id={design_id!r}; product_profile_id={profile_id!r}; "
                "only one version may be generated per subject/profile in one batch"
            )
        seen.add(key)
        plans.append(
            {
                "design_id": design_id,
                "product_profile_id": profile_id,
                "version_id": version_id,
            }
        )
    return plans


def _resolve_generation_versions(
    plans: list[dict[str, str]],
    *,
    authoring_root: Path,
    inputs_root: Path,
    published: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    """Resolve `auto` independently for every design/profile candidate."""
    resolved: list[dict[str, str]] = []
    for plan in plans:
        if plan["version_id"] != "auto":
            resolved.append(dict(plan))
            continue
        profile_root = (
            design_authoring_path(
                authoring_root,
                plan["design_id"],
                inputs_root=inputs_root,
            )
            / plan["product_profile_id"]
        )
        numbers: list[int] = []
        if profile_root.is_dir():
            for candidate in profile_root.iterdir():
                if not candidate.is_dir():
                    continue
                try:
                    version = validate_version(candidate.name)
                except StaticPrintError:
                    continue
                numbers.append(int(version.removeprefix("v")))
        numbers.extend(
            int(entry["version_id"].removeprefix("v"))
            for entry in published.get(plan["design_id"], [])
            if entry["product_profile_id"] == plan["product_profile_id"]
            and entry["version_id"].startswith("v")
            and entry["version_id"][1:].isdigit()
        )
        version_id = f"v{max(numbers, default=0) + 1:06d}"
        resolved.append({**plan, "version_id": version_id})
    return resolved


@dataclass(frozen=True)
class ServerConfig:
    project_root: Path
    work_root: Path
    profiles_root: Path
    reviewer_access_code: str | None = None
    operator_access_code: str | None = None
    s3_bucket: str | None = None
    s3_prefix: str = "templates/static_design"
    aws_profile: str | None = None
    aws_region: str | None = None

    @property
    def inputs_root(self) -> Path:
        return self.work_root / "static-design-inputs"

    @property
    def authoring_root(self) -> Path:
        return self.work_root / "static-authoring"

    @property
    def graduated_root(self) -> Path:
        return self.work_root / "static-graduated-designs"

    @property
    def abandoned_root(self) -> Path:
        return self.work_root / "static-abandoned-designs"

    @property
    def exchange_root(self) -> Path:
        return self.work_root / "static-exchange"

    @property
    def database(self) -> Path:
        return self.work_root / "static-reviews.sqlite3"

    @property
    def subject_catalog(self) -> Path:
        return self.project_root / "catalog/static-design-subjects.json"


class StaticPrintHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: ServerConfig):
        self.config = config
        self.sessions: dict[str, str] = {}
        self.intake_lock = Lock()
        self.generation_lock = Lock()
        self.subject_generation_lock = Lock()
        self.release_lock = Lock()
        self.history_cleanup_lock = Lock()
        self.s3_status_lock = Lock()
        self.s3_catalog_cache: dict[str, dict[str, Any]] = {}
        self.s3_cache_warning: str | None = None
        self.s3_published_cache: dict[str, list[dict[str, str]]] | None = None
        self.s3_status_cache: dict[str, Any] | None = None
        self.s3_checked_at = 0.0
        self.s3_refresh_thread: Thread | None = None
        self.print_readiness_cache_lock = Lock()
        self.print_readiness_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.workspace_lock = None
        super().__init__(address, StaticPrintHandler)
        try:
            lock_path = config.work_root / ".static-print-server.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            self.workspace_lock = lock_path.open("a+")
            try:
                fcntl.flock(self.workspace_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StaticPrintError(
                    "another StaticPrintDesign server is already using this workspace; "
                    f"lock={lock_path.resolve()}"
                ) from exc
            interrupted = mark_running_operations_interrupted(config.database)
            self.recovery_report = reconcile_workspace(
                work_root=config.work_root,
                authoring_root=config.authoring_root,
                graduated_root=config.graduated_root,
                profiles=discover_profiles(config.profiles_root),
            )
            self.recovery_report["interrupted_operations"] = interrupted
            self._load_publication_cache()
            self._bootstrap_local_publications()
        except Exception:
            self.server_close()
            raise

    def server_close(self) -> None:
        try:
            if self.workspace_lock is not None:
                fcntl.flock(self.workspace_lock.fileno(), fcntl.LOCK_UN)
                self.workspace_lock.close()
                self.workspace_lock = None
        finally:
            super().server_close()

    def begin_operation(
        self,
        operation_id: str,
        *,
        kind: str,
        label: str,
        items: list[str],
        request: dict[str, Any],
        resume: bool = False,
    ) -> None:
        start_operation(
            self.config.database,
            operation_id=operation_id,
            kind=kind,
            label=label,
            request=request,
            items=[(item, item) for item in items],
            resume=resume,
        )

    def update_operation(
        self,
        operation_id: str,
        item_label: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        update_operation_item(
            self.config.database,
            operation_id=operation_id,
            item_key=item_label,
            status=status,
            error=error,
        )
        if status == "running":
            message = f"Started: {item_label}"
            severity = "info"
        elif status == "succeeded":
            message = f"Completed: {item_label}"
            severity = "info"
        elif status == "skipped":
            message = f"Skipped: {item_label}"
            severity = "info"
        else:
            message = (
                f"Failed: {item_label}; error={error or 'unknown error'}; "
                f"recommended_fix={_operation_recommendation(error or '')}"
            )
            severity = "error"
        append_operation_event(
            self.config.database,
            operation_id=operation_id,
            item_key=item_label,
            severity=severity,
            message=message,
        )

    def operation_event(
        self,
        operation_id: str,
        message: str,
        *,
        severity: str = "info",
        item_label: str | None = None,
    ) -> None:
        append_operation_event(
            self.config.database,
            operation_id=operation_id,
            item_key=item_label,
            severity=severity,
            message=message,
        )

    def finish_operation(self, operation_id: str, *, error: str | None = None) -> None:
        if error:
            self.operation_event(
                operation_id,
                f"Operation stopped: {error}; recommended_fix={_operation_recommendation(error)}",
                severity="error",
            )
        finish_operation(self.config.database, operation_id=operation_id, error=error)

    def operation_snapshot(
        self, operation_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the operation feed or one exact operation for live polling."""

        return operation_snapshots(
            self.config.database,
            operation_id=operation_id,
        )

    def current_print_readiness(
        self,
        recorded: Any,
        *,
        path: Path,
        expected_size: tuple[int, int],
        canvas_policy: str,
        upscale_ratio: float,
        product_profile_id: str | None,
    ) -> dict[str, Any] | None:
        """Upgrade a legacy saved report once per unchanged local artifact."""

        if not isinstance(recorded, dict):
            return None
        if recorded.get("schema") == PRINT_READINESS_SCHEMA:
            return recorded
        if not path.is_file():
            return recorded
        stat = path.stat()
        key = (
            str(path.resolve()),
            stat.st_mtime_ns,
            stat.st_size,
            expected_size,
            canvas_policy,
            round(upscale_ratio, 8),
            product_profile_id,
            PRINT_READINESS_SCHEMA,
        )
        with self.print_readiness_cache_lock:
            cached = self.print_readiness_cache.get(key)
        if cached is not None:
            return cached
        encoded_key = json.dumps(key, separators=(",", ":"), default=list)
        cache_path = (
            self.config.work_root
            / "cache/print-readiness-v2"
            / f"{hashlib.sha256(encoded_key.encode('utf-8')).hexdigest()}.json"
        )
        if cache_path.is_file():
            try:
                cached_record = read_json(cache_path)
            except (OSError, StaticPrintError):
                cached_record = None
            if (
                isinstance(cached_record, dict)
                and cached_record.get("schema")
                == "pawmarvel-static-print-readiness-cache-v1"
                and cached_record.get("key") == json.loads(encoded_key)
                and isinstance(cached_record.get("report"), dict)
                and cached_record["report"].get("schema")
                == PRINT_READINESS_SCHEMA
            ):
                current = cached_record["report"]
                with self.print_readiness_cache_lock:
                    self.print_readiness_cache[key] = current
                return current
        current = print_readiness_report_for_path(
            path,
            expected_size=expected_size,
            canvas_policy=canvas_policy,
            upscale_ratio=upscale_ratio,
            product_profile_id=product_profile_id,
        )
        with self.print_readiness_cache_lock:
            self.print_readiness_cache[key] = current
        write_json(
            cache_path,
            {
                "schema": "pawmarvel-static-print-readiness-cache-v1",
                "key": json.loads(encoded_key),
                "report": current,
            },
        )
        return current

    @property
    def publication_cache_path(self) -> Path:
        return self.config.work_root / "cache/s3-publication-provenance.json"

    def _load_publication_cache(self) -> None:
        config = self.config
        if not config.s3_bucket:
            return
        try:
            self.s3_catalog_cache.update(
                _load_publication_provenance_cache(
                    self.publication_cache_path,
                    bucket=config.s3_bucket,
                    prefix=config.s3_prefix,
                )
            )
        except StaticPrintError as exc:
            # This file is a rebuildable cache, never an authority. Keep the GUI
            # available and explain the one-time remote rebuild in status.
            self.s3_cache_warning = str(exc)

    def _persist_publication_cache(self) -> None:
        config = self.config
        if not config.s3_bucket or not hasattr(config, "work_root"):
            return
        _write_publication_provenance_cache(
            self.publication_cache_path,
            bucket=config.s3_bucket,
            prefix=config.s3_prefix,
            cache=self.s3_catalog_cache,
        )

    def _bootstrap_local_publications(self, *, authoritative: bool = False) -> None:
        """Serve the last successful local S3 catalog while refreshing remotely."""

        config = self.config
        catalog_path = config.exchange_root / "catalog.json"
        if not config.s3_bucket or not catalog_path.is_file():
            return
        catalog = read_json(catalog_path)
        published = cached_release_entries(
            catalog,
            bucket=config.s3_bucket,
            prefix=config.s3_prefix,
            catalog_cache=self.s3_catalog_cache,
        )
        version_count = sum(
            len(product["versions"]) for product in catalog["products"]
        )
        provenance_count = sum(
            "source_sha256" in entry
            for entries in published.values()
            for entry in entries
        )
        with self.s3_status_lock:
            self.s3_published_cache = published
            self.s3_status_cache = {
                "source": (
                    "confirmed-publication-result"
                    if authoritative
                    else "local-catalog-bootstrap"
                ),
                "available": authoritative,
                "stale": not authoritative,
                "checked_at": utc_now(),
                "error": self.s3_cache_warning,
                "catalog_count": 1,
                "bundle_count": len(catalog["products"]),
                "version_count": version_count,
                "source_provenance_count": provenance_count,
                "source_provenance_complete": provenance_count == version_count,
                "prefix": f"{config.s3_prefix}/catalog.json",
                "updated_at": catalog["updated_at"],
            }
            self.s3_checked_at = (
                monotonic()
                if authoritative and provenance_count == version_count
                else 0.0
            )

    def cache_prepared_bundle_provenance(
        self, records: list[dict[str, str]]
    ) -> None:
        """Persist provenance already validated and uploaded by publication."""

        with self.s3_status_lock:
            for record in records:
                self.s3_catalog_cache[f"manifest:{record['object_key']}"] = {
                    "manifest_sha256": record["manifest_sha256"],
                    "source_sha256": record["source_sha256"],
                }
            self._persist_publication_cache()

    def s3_publications(
        self,
        *,
        force: bool = False,
        allow_stale: bool = True,
        include_source_sha256: bool = True,
    ) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
        config = self.config
        if not config.s3_bucket:
            raise StaticPrintError(
                "S3 release status is not configured; set STATIC_PRINT_S3_BUCKET"
            )
        now = monotonic()
        with self.s3_status_lock:
            if (
                not force
                and self.s3_published_cache is not None
                and now - self.s3_checked_at < S3_STATUS_REFRESH_SECONDS
            ):
                return self.s3_published_cache, dict(self.s3_status_cache or {})
        # Never hold the cache mutex across AWS CLI/network I/O. Operator-state
        # requests must be able to return local evidence while this runs.
        try:
            published, inventory = load_s3_release_entries(
                bucket=config.s3_bucket,
                prefix=config.s3_prefix,
                profile=config.aws_profile,
                region=config.aws_region,
                catalog_cache=self.s3_catalog_cache,
                include_source_sha256=include_source_sha256,
            )
        except StaticPrintError as exc:
            with self.s3_status_lock:
                if allow_stale and self.s3_published_cache is not None:
                    status = {
                        **(self.s3_status_cache or {}),
                        "available": False,
                        "stale": True,
                        "error": str(exc),
                    }
                    return self.s3_published_cache, status
            raise
        if not include_source_sha256:
            return published, {
                "source": "s3-catalog-index",
                "available": True,
                "stale": False,
                "checked_at": utc_now(),
                "error": None,
                **inventory,
            }
        self._persist_publication_cache()
        version_count = sum(len(entries) for entries in published.values())
        provenance_count = sum(
            "source_sha256" in entry
            for entries in published.values()
            for entry in entries
        )
        with self.s3_status_lock:
            self.s3_published_cache = published
            self.s3_checked_at = monotonic()
            self.s3_status_cache = {
                "source": "s3-catalog-index",
                "available": True,
                "stale": False,
                "checked_at": utc_now(),
                "error": None,
                "source_provenance_count": provenance_count,
                "source_provenance_complete": provenance_count == version_count,
                **inventory,
            }
            return published, dict(self.s3_status_cache)

    def cached_s3_publications(
        self,
    ) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
        """Return immediately and refresh remote publication state in background."""

        config = self.config
        if not config.s3_bucket:
            raise StaticPrintError(
                "S3 release status is not configured; set STATIC_PRINT_S3_BUCKET"
            )
        thread_to_start: Thread | None = None
        with self.s3_status_lock:
            now = monotonic()
            refreshing = self.s3_refresh_thread is not None
            refresh_due = (
                self.s3_checked_at == 0
                or now - self.s3_checked_at >= S3_STATUS_REFRESH_SECONDS
            )
            if refresh_due and not refreshing:
                thread_to_start = Thread(
                    target=self._refresh_s3_publications,
                    daemon=True,
                    name="static-print-s3-status",
                )
                self.s3_refresh_thread = thread_to_start
                refreshing = True
            published = {
                design_id: [dict(entry) for entry in entries]
                for design_id, entries in (self.s3_published_cache or {}).items()
            }
            status = {
                "source": "s3-catalog-index",
                "available": self.s3_published_cache is not None,
                "stale": False,
                "checked_at": None,
                "error": None,
                **(self.s3_status_cache or {}),
                "refreshing": refreshing,
            }
        if thread_to_start is not None:
            try:
                thread_to_start.start()
            except Exception:
                with self.s3_status_lock:
                    if self.s3_refresh_thread is thread_to_start:
                        self.s3_refresh_thread = None
                raise
        return published, status

    def _refresh_s3_publications(self) -> None:
        try:
            _, status = self.s3_publications(force=True)
            with self.s3_status_lock:
                # Failed refreshes can return the last known inventory as stale.
                # Persist that status so the GUI explains the degraded state.
                if status.get("stale"):
                    self.s3_status_cache = dict(status)
                    self.s3_checked_at = monotonic()
        except StaticPrintError as exc:
            with self.s3_status_lock:
                self.s3_checked_at = monotonic()
                self.s3_status_cache = {
                    "source": "s3-catalog-index",
                    "available": False,
                    "stale": self.s3_published_cache is not None,
                    "checked_at": utc_now(),
                    "error": str(exc),
                }
        finally:
            with self.s3_status_lock:
                self.s3_refresh_thread = None

class StaticPrintHandler(BaseHTTPRequestHandler):
    server: StaticPrintHTTPServer

    def _json(self, status: int, value: Any) -> None:
        data = (json.dumps(value, indent=2) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise StaticPrintError("invalid Content-Length") from exc
        if length < 0 or length > 2_000_000:
            raise StaticPrintError("request body is too large")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise StaticPrintError(f"request body is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise StaticPrintError("request body must be a JSON object")
        return value

    def _cookies(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for segment in self.headers.get("Cookie", "").split(";"):
            if "=" not in segment:
                continue
            key, value = segment.strip().split("=", 1)
            result[key] = value
        return result

    def _role(self) -> str | None:
        token = self._cookies().get("static_print_session")
        if token and token in self.server.sessions:
            return self.server.sessions[token]
        return None

    def _require(self, role: str) -> bool:
        actual = self._role()
        if actual == "operator" or actual == role:
            return True
        config = self.server.config
        required_code = (
            config.operator_access_code
            if role == "operator"
            else config.reviewer_access_code
        )
        if required_code is None and role == "reviewer":
            return True
        if (
            required_code is None
            and role == "operator"
            and config.operator_access_code is None
        ):
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"error": f"{role} login required"})
        return False

    def _static(self, name: str) -> None:
        root = Path(__file__).resolve().parent / "static"
        path = (root / name).resolve()
        if path.parent != root or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _asset(self, path: str) -> None:
        role = "operator" if path.startswith("/asset/authoring/") else "reviewer"
        if not self._require(role):
            return
        config = self.server.config
        if path.startswith("/asset/source/"):
            relative = unquote(path.removeprefix("/asset/source/"))
            parts = relative.split("/", 1)
            if len(parts) != 2 or parts[0] not in {"active", "graduated", "abandoned"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            pool, design_id = parts
            root = pool_root(config.work_root, pool).resolve()
            candidate = design_source_path(root, design_id)
        elif path.startswith("/asset/authoring/"):
            relative = unquote(path.removeprefix("/asset/authoring/"))
            candidate = config.authoring_root / relative
            root = config.authoring_root.resolve()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        resolved = candidate.resolve()
        if (
            root not in resolved.parents
            or not resolved.is_file()
            or resolved.suffix.lower() != ".png"
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _state(self, operator: bool) -> dict[str, Any]:
        config = self.server.config
        roots = (
            [("active", config.inputs_root)]
            if not operator
            else [
                ("active", config.inputs_root),
                ("graduated", config.graduated_root),
                ("abandoned", config.abandoned_root),
            ]
        )
        designs: list[dict[str, Any]] = []
        for pool, root in roots:
            for design in discover_designs(root, config.authoring_root):
                design["pool"] = pool
                designs.append(design)
        annotate_design_collections(designs)
        feedback = list_feedback(config.database)
        decisions = list_operator_decisions(config.database) if operator else {}
        profiles = discover_profiles(config.profiles_root)
        for design in designs:
            design_id = design["design_id"]
            design["source_url"] = f"/asset/source/{design['pool']}/{design_id}"
            design["transparency"] = design.get(
                "source_transparency"
            ) or transparency_report(Path(design["source"]))
            recorded_canvas_policy = (design.get("intake") or {}).get(
                "canvas_policy"
            )
            design["canvas_policy"] = (
                recorded_canvas_policy
                if recorded_canvas_policy
                in {OPAQUE_FULL_BLEED, PRESERVE_TRANSPARENCY}
                else canvas_policy_for_report(design["transparency"])
            )
            reviews = [
                item
                for item in feedback.get(design_id, [])
                if item["source_sha256"] == design["source_sha256"]
            ]
            design["feedback_summary"] = {
                verdict: sum(1 for item in reviews if item["verdict"] == verdict)
                for verdict in ("approve", "changes", "reject")
            }
            if operator:
                design["feedback"] = reviews
                current_decision = next(
                    (
                        item
                        for item in decisions.get(design_id, [])
                        if item["source_sha256"] == design["source_sha256"]
                    ),
                    None,
                )
                design["operator_decision"] = current_decision
                attention: list[dict[str, Any]] = []
                feedback_attention = (
                    design["feedback_summary"].get("changes", 0)
                    + design["feedback_summary"].get("reject", 0)
                )
                if feedback_attention and current_decision is None:
                    attention.append(
                        {
                            "kind": "reference-feedback",
                            "active": True,
                            "label": "Reviewer feedback needs attention",
                            "reason": (
                                f"{feedback_attention} current needs-changes/reject "
                                "review(s) require an operator decision."
                            ),
                        }
                    )
                subject_attention = design.get("subject_review_attention")
                if isinstance(subject_attention, dict):
                    resolved = bool(
                        current_decision
                        and current_decision.get("decision")
                        in {"approved", "abandoned"}
                    )
                    attention.append(
                        {
                            **subject_attention,
                            "active": not resolved,
                            "label": (
                                "Animal behavior reviewed"
                                if resolved
                                else "Animal behavior needs attention"
                            ),
                        }
                    )
                subject_print_readiness = design.get("subject_print_readiness")
                if isinstance(subject_print_readiness, dict):
                    subject_print_readiness = self.server.current_print_readiness(
                        subject_print_readiness,
                        path=Path(design["source"]),
                        expected_size=(
                            design["source_width"],
                            design["source_height"],
                        ),
                        canvas_policy=design["canvas_policy"],
                        upscale_ratio=1.0,
                        product_profile_id=None,
                    )
                    design["subject_print_readiness"] = subject_print_readiness
                product_compatibility = (
                    subject_print_readiness.get("product_compatibility")
                    if isinstance(subject_print_readiness, dict)
                    else None
                )
                compatibility_needs_review = bool(
                    isinstance(product_compatibility, dict)
                    and product_compatibility.get("review_product_profile_ids")
                )
                if (
                    isinstance(subject_print_readiness, dict)
                    and (
                        subject_print_readiness.get("print_ready") is False
                        or compatibility_needs_review
                    )
                ):
                    resolved = bool(
                        current_decision
                        and current_decision.get("decision")
                        in {"approved", "abandoned"}
                    )
                    reason = print_readiness_warning(
                        f"subject preview {design_id}", subject_print_readiness
                    )
                    attention.append(
                        {
                            "kind": "print-quality",
                            "active": not resolved,
                            "label": (
                                "Product alpha compatibility reviewed"
                                if resolved
                                else "Product alpha compatibility needs attention"
                            )
                            if compatibility_needs_review
                            and subject_print_readiness.get("print_ready") is True
                            else (
                                "Pixel preflight reviewed"
                                if resolved
                                else "Pixel preflight needs attention"
                            ),
                            "reason": reason
                            or "The subject preview did not pass pixel preflight.",
                        }
                    )
                design["review_attention"] = attention
                candidates: list[dict[str, str]] = []
                for version in design["versions"]:
                    version_id = version["version_id"]
                    generation = version.get("generation")
                    asset_base = version["authoring_relative"]
                    if version["preview"]:
                        version["preview_url"] = (
                            f"/asset/authoring/{asset_base}/{version['preview']}"
                        )
                    version["assets"] = [
                        {
                            "name": filename,
                            "url": f"/asset/authoring/{asset_base}/{filename}",
                        }
                        for filename in version["prints"]
                    ]
                    version["profile_outputs"] = []
                    profile_id = version.get("product_profile_id")
                    profile = profiles.get(profile_id)
                    if profile is None:
                        continue
                    recorded_transparency = (
                        generation.get("transparency")
                        if isinstance(generation, dict)
                        else None
                    )
                    generation_transparency = (
                        recorded_transparency
                        if isinstance(recorded_transparency, dict)
                        else {}
                    )
                    recorded_print_readiness = (
                        generation.get("print_readiness")
                        if isinstance(generation, dict)
                        else None
                    )
                    generation_print_readiness = (
                        recorded_print_readiness
                        if isinstance(recorded_print_readiness, dict)
                        else {}
                    )
                    render_source_width = design["source_width"]
                    render_source_height = design["source_height"]
                    render_pipeline = (
                        generation.get("render_pipeline")
                        if isinstance(generation, dict)
                        else None
                    )
                    if isinstance(render_pipeline, dict):
                        upscale = render_pipeline.get("upscale")
                        output_size = (
                            upscale.get("output_size")
                            if isinstance(upscale, dict)
                            else None
                        )
                        if (
                            isinstance(output_size, dict)
                            and isinstance(output_size.get("width"), int)
                            and output_size["width"] > 0
                            and isinstance(output_size.get("height"), int)
                            and output_size["height"] > 0
                        ):
                            render_source_width = output_size["width"]
                            render_source_height = output_size["height"]
                    recorded_print_reports = generation_print_readiness.get("prints")
                    print_readiness_reports = (
                        recorded_print_reports
                        if isinstance(recorded_print_reports, dict)
                        else {}
                    )
                    recorded_print_transparency = generation_transparency.get("prints")
                    print_transparency = (
                        recorded_print_transparency
                        if isinstance(recorded_print_transparency, dict)
                        else {}
                    )
                    version_root = self.server.config.authoring_root / asset_base
                    print_assets = []
                    runtime_print_reports: dict[str, dict[str, Any] | None] = {}
                    for target in profile.targets:
                        filename = (
                            f"{design_id}--{profile.profile_id}.png"
                            if len(profile.targets) == 1
                            else f"{design_id}--{profile.profile_id}--{target.target_id}.png"
                        )
                        print_path = version_root / filename
                        runtime_report = self.server.current_print_readiness(
                            print_readiness_reports.get(target.target_id),
                            path=print_path,
                            expected_size=(target.width, target.height),
                            canvas_policy=design["canvas_policy"],
                            upscale_ratio=render_scale(
                                (render_source_width, render_source_height),
                                (target.width, target.height),
                                profile.composition,
                            ),
                            product_profile_id=profile.profile_id,
                        )
                        runtime_print_reports[target.target_id] = (
                            runtime_report
                            if isinstance(runtime_report, dict)
                            else None
                        )
                        print_assets.append(
                            {
                                "target_id": target.target_id,
                                "filename": filename,
                                "url": f"/asset/authoring/{asset_base}/{filename}",
                                "width": target.width,
                                "height": target.height,
                                "transparency": print_transparency.get(
                                    target.target_id
                                ),
                                "print_readiness": runtime_print_reports[
                                    target.target_id
                                ],
                            }
                        )
                    profile_preview_name = f"{profile.profile_id}.png"
                    profile_preview_path = (
                        version_root / "profile-previews" / profile_preview_name
                    )
                    first_target = profile.targets[0]
                    runtime_profile_report = self.server.current_print_readiness(
                        generation_print_readiness.get("profile_preview"),
                        path=profile_preview_path,
                        expected_size=(
                            profile.preview_width,
                            profile.preview_height,
                        ),
                        canvas_policy=design["canvas_policy"],
                        upscale_ratio=max(
                            profile.preview_width / first_target.width,
                            profile.preview_height / first_target.height,
                        ),
                        product_profile_id=profile.profile_id,
                    )
                    report_pairs = [
                        (f"{profile.profile_id} preview", runtime_profile_report),
                        *[
                            (
                                f"{profile.profile_id}/{target.target_id}",
                                runtime_print_reports.get(target.target_id),
                            )
                            for target in profile.targets
                        ],
                    ]
                    runtime_warnings = [
                        warning
                        for artifact_label, report in report_pairs
                        if isinstance(report, dict)
                        for warning in [
                            print_readiness_warning(artifact_label, report)
                        ]
                        if warning
                    ]
                    expected = {item["filename"] for item in print_assets}
                    current_generation = (
                        isinstance(generation, dict)
                        and generation.get("schema") == "pawmarvel-static-generation-v3"
                        and generation.get("source_sha256") == design["source_sha256"]
                        and generation.get("product_profile_id") == profile_id
                        and generation.get("product_profile_sha256")
                        == product_profile_sha256(profile)
                        and generation.get("fit") == "cover"
                        and generation.get("composition", "cover")
                        == profile.composition
                        and generation.get("canvas_policy") == design["canvas_policy"]
                        and isinstance(
                            generation_transparency.get("profile_preview"), dict
                        )
                        and all(
                            isinstance(print_transparency.get(target.target_id), dict)
                            for target in profile.targets
                        )
                    )
                    if (
                        current_generation
                        and expected <= set(version["prints"])
                        and version["preview"]
                    ):
                        exact_preview = profile_preview_name in version.get(
                            "profile_previews", []
                        )
                        if not exact_preview:
                            continue
                        version["profile_outputs"].append(
                            {
                                "product_profile_id": profile.profile_id,
                                "preview": {
                                    "url": (
                                        f"/asset/authoring/{asset_base}/profile-previews/"
                                        f"{profile_preview_name}"
                                    ),
                                    "width": profile.preview_width,
                                    "height": profile.preview_height,
                                    "exact": exact_preview,
                                    "transparency": generation_transparency[
                                        "profile_preview"
                                    ],
                                    "print_readiness": runtime_profile_report,
                                },
                                "canvas_policy": design["canvas_policy"],
                                "prints": print_assets,
                                "warnings": runtime_warnings,
                            }
                        )
                        for artifact_label, report in report_pairs:
                            if (
                                isinstance(report, dict)
                                and report.get("print_ready") is False
                            ):
                                reason = print_readiness_warning(
                                    artifact_label, report
                                )
                                attention.append(
                                    {
                                        "kind": "product-print-quality",
                                        "active": True,
                                        "label": "Product pixels need attention",
                                        "reason": reason
                                        or "Product pixel preflight did not pass.",
                                        "product_profile_id": profile.profile_id,
                                        "version_id": version_id,
                                    }
                                )
                        candidates.append(
                            {
                                "selection": f"{design_id}:{version_id}:{profile.profile_id}",
                                "design_id": design_id,
                                "version_id": version_id,
                                "product_profile_id": profile.profile_id,
                            }
                        )
                design["bundle_candidates"] = candidates
        published: dict[str, dict[str, Any]] = {}
        publication_status: dict[str, Any] = {
            "source": "s3-catalog-index",
            "available": False,
            "stale": False,
            "checked_at": None,
            "error": None,
        }
        if operator:
            try:
                remote_entries, publication_status = (
                    self.server.cached_s3_publications()
                )
            except StaticPrintError as exc:
                remote_entries = {}
                publication_status["error"] = str(exc)
            for design_id, entries in remote_entries.items():
                published[design_id] = {
                    "release_ids": {item["release_id"] for item in entries},
                    "product_profile_ids": {
                        item["product_profile_id"] for item in entries
                    },
                    "entries": entries,
                }
            for design in designs:
                coverage = published.get(design["design_id"])
                current_entries: list[dict[str, str]] = []
                if coverage:
                    current_entries = [
                        entry
                        for entry in coverage["entries"]
                        if entry.get("source_sha256") == design["source_sha256"]
                    ]
                design["publication"] = (
                    {
                        "release_ids": sorted(coverage["release_ids"]),
                        "product_profile_ids": sorted(coverage["product_profile_ids"]),
                        "entries": sorted(
                            coverage["entries"],
                            key=lambda item: (
                                item["release_id"],
                                item["product_profile_id"],
                                item["version_id"],
                            ),
                        ),
                        "current_entries": sorted(
                            current_entries,
                            key=lambda item: (
                                item["release_id"],
                                item["product_profile_id"],
                                item["version_id"],
                            ),
                        ),
                    }
                    if coverage
                    else {
                        "release_ids": [],
                        "product_profile_ids": [],
                        "entries": [],
                        "current_entries": [],
                    }
                )
                design["product_coverage"] = _product_coverage_records(
                    design,
                    sorted(profiles),
                    publication_known=(
                        (
                            publication_status["available"]
                            or publication_status["stale"]
                        )
                        and publication_status.get(
                            "source_provenance_complete", False
                        )
                    ),
                )
                attention = design.setdefault("review_attention", [])
                published_versions = {
                    (entry["product_profile_id"], entry["version_id"])
                    for entry in design["publication"]["current_entries"]
                }
                for item in attention:
                    if (
                        item.get("kind") == "product-print-quality"
                        and (
                            item.get("product_profile_id"),
                            item.get("version_id"),
                        )
                        in published_versions
                    ):
                        item["active"] = False
                        item["label"] = "Product pixel warning reviewed and published"
                for product in design["product_coverage"]:
                    if product["state"] == "local-attention":
                        attention.append(
                            {
                                "kind": "product-artifact",
                                "active": True,
                                "label": "Product artifact needs attention",
                                "reason": (
                                    "A generated product version is not a current "
                                    "release candidate. Regenerate or inspect it before "
                                    "publication."
                                ),
                                "product_profile_id": product[
                                    "product_profile_id"
                                ],
                            }
                        )
                design["needs_review_attention"] = any(
                    item.get("active") is True for item in attention
                )
        _, subjects = load_subject_catalog(config.subject_catalog)
        result: dict[str, Any] = {
            "designs": designs,
            "subjects": [subject.__dict__ for subject in subjects],
            "profiles": [
                {
                    "product_profile_id": profile.profile_id,
                    "preview": {
                        "width": profile.preview_width,
                        "height": profile.preview_height,
                    },
                    "targets": [
                        {
                            "target_id": target.target_id,
                            "width": target.width,
                            "height": target.height,
                        }
                        for target in profile.targets
                    ],
                }
                for profile in profiles.values()
            ],
        }
        if operator:
            result["publication"] = {
                "enabled": bool(config.s3_bucket),
                "bucket": config.s3_bucket,
                "prefix": config.s3_prefix,
                "status": publication_status,
            }
            result["workspace"] = {
                "work_root": str(config.work_root.resolve()),
                "recovery": self.server.recovery_report,
                "pools": {
                    pool: {
                        "root": str(root.resolve()),
                        "seed_count": sum(
                            1
                            for item in designs
                            if item["pool"] == pool and item["subject_id"] is None
                        ),
                        "subject_count": sum(
                            1
                            for item in designs
                            if item["pool"] == pool and item["subject_id"] is not None
                        ),
                    }
                    for pool, root in (
                        ("active", config.inputs_root),
                        ("graduated", config.graduated_root),
                        ("abandoned", config.abandoned_root),
                    )
                },
            }
            result["history_cleanup"] = latest_cleanup_state(config.work_root)
        return result

    def do_GET(self) -> None:
        request_url = urlparse(self.path)
        path = request_url.path
        if path in {"/", "/operator"}:
            self._static("index.html")
        elif path == "/static/app.js":
            self._static("app.js")
        elif path == "/static/app.css":
            self._static("app.css")
        elif path == "/api/state":
            if self._require("reviewer"):
                self._json(HTTPStatus.OK, self._state(False))
        elif path == "/api/operator/state":
            if self._require("operator"):
                self._json(HTTPStatus.OK, self._state(True))
        elif path == "/api/operator/operations":
            if self._require("operator"):
                values = parse_qs(request_url.query, keep_blank_values=True).get(
                    "operation_id", []
                )
                if len(values) > 1:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "operation_id must be supplied at most once"},
                    )
                else:
                    try:
                        operation_id = (
                            validate_id(values[0], "operation ID") if values else None
                        )
                    except StaticPrintError as exc:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    else:
                        self._json(
                            HTTPStatus.OK,
                            {
                                "operations": self.server.operation_snapshot(
                                    operation_id
                                )
                            },
                        )
        elif path.startswith("/asset/"):
            self._asset(path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/login":
                self._login(body)
            elif path == "/api/feedback-batch":
                if self._require("reviewer"):
                    raw_feedback = body.get("feedback")
                    if not isinstance(raw_feedback, list) or not raw_feedback:
                        raise StaticPrintError("feedback must be a non-empty array")
                    normalized: list[dict[str, str]] = []
                    for index, item in enumerate(raw_feedback):
                        if not isinstance(item, dict):
                            raise StaticPrintError(
                                f"feedback item {index} must be an object"
                            )
                        design_id = str(item.get("design_id", ""))
                        source = design_source_path(
                            self.server.config.inputs_root, design_id
                        )
                        if not source.is_file():
                            raise StaticPrintError(
                                f"reviewed design does not exist: "
                                f"design_id={design_id!r}; path={source.resolve()}"
                            )
                        current_digest = sha256_file(source)
                        requested_digest = str(item.get("source_sha256", ""))
                        if requested_digest != current_digest:
                            raise StaticPrintError(
                                "reviewed design changed after it was loaded; refresh before "
                                f"saving feedback: design_id={design_id!r}; "
                                f"expected_source_sha256={current_digest}; "
                                f"actual_source_sha256={requested_digest or '<missing>'}"
                            )
                        normalized.append(
                            {
                                "design_id": design_id,
                                "source_sha256": current_digest,
                                "verdict": str(item.get("verdict", "")),
                                "comment": str(item.get("comment", "")),
                            }
                        )
                    values = save_feedbacks(
                        self.server.config.database,
                        reviewer=str(body.get("reviewer", "")),
                        feedback=normalized,
                    )
                    self._json(
                        HTTPStatus.OK,
                        {"count": len(values), "feedback": values},
                    )
            elif path == "/api/operator/generate":
                if self._require("operator"):
                    self._generate(body)
            elif path == "/api/operator/transition":
                if self._require("operator"):
                    self._transition(body)
            elif path == "/api/operator/transition-batch":
                if self._require("operator"):
                    self._transition_batch(body)
            elif path == "/api/operator/import-collection":
                if self._require("operator"):
                    self._import_collection(body)
            elif path == "/api/operator/subject-generation-requirements":
                if self._require("operator"):
                    self._save_subject_generation_requirements(body)
            elif path == "/api/operator/generate-subjects":
                if self._require("operator"):
                    self._generate_subjects(body)
            elif path == "/api/operator/generate-subjects-batch":
                if self._require("operator"):
                    self._generate_subjects_batch(body)
            elif path == "/api/operator/operation-action":
                if self._require("operator"):
                    self._operation_action(body)
            elif path == "/api/operator/subject-decision":
                if self._require("operator"):
                    self._subject_decision(body)
            elif path == "/api/operator/subject-decision-batch":
                if self._require("operator"):
                    self._subject_decision_batch(body)
            elif path == "/api/operator/publish":
                if self._require("operator"):
                    self._publish(body)
            elif path == "/api/operator/product-lifecycle":
                if self._require("operator"):
                    self._product_lifecycle(body)
            elif path == "/api/operator/rebuild-catalog":
                if self._require("operator"):
                    self._rebuild_catalog()
            elif path == "/api/operator/history-cleanup-proposal":
                if self._require("operator"):
                    self._history_cleanup_proposal(body)
            elif path == "/api/operator/history-cleanup-execute":
                if self._require("operator"):
                    self._history_cleanup_execute(body)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except StaticPrintError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - safety net
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"unexpected {type(exc).__name__}: {exc}"},
            )

    def _history_release_references(self) -> set[tuple[str, str, str]]:
        config = self.server.config
        combined = _published_entries(config.exchange_root)
        local_catalog = config.exchange_root / "catalog.json"
        if local_catalog.is_file():
            catalog = read_json(local_catalog)
            for product in catalog.get("products", []):
                if not isinstance(product, dict):
                    continue
                design_id = str(product.get("design_id", ""))
                for version in product.get("versions", []):
                    if not isinstance(version, dict):
                        continue
                    combined.setdefault(design_id, []).append(
                        {
                            "product_profile_id": str(
                                product.get("product_profile_id", "")
                            ),
                            "version_id": str(version.get("version_id", "")),
                        }
                    )
        if config.s3_bucket:
            remote, _ = self.server.s3_publications(
                force=True,
                allow_stale=False,
                include_source_sha256=False,
            )
            for design_id, entries in remote.items():
                combined.setdefault(design_id, []).extend(entries)
        references: set[tuple[str, str, str]] = set()
        for design_id, entries in combined.items():
            for entry in entries:
                references.add(
                    (
                        design_id,
                        str(entry.get("product_profile_id", "")),
                        str(entry.get("version_id", "")),
                    )
                )
        return references

    def _history_cleanup_proposal(self, body: dict[str, Any]) -> None:
        proposal_id = str(body.get("proposal_id", "")).strip() or (
            f"history-{secrets.token_hex(8)}"
        )
        with self.server.history_cleanup_lock:
            proposal = create_history_cleanup_proposal(
                work_root=self.server.config.work_root,
                authoring_root=self.server.config.authoring_root,
                release_references=self._history_release_references(),
                proposal_id=proposal_id,
            )
        self._json(HTTPStatus.OK, proposal)

    def _history_cleanup_execute(self, body: dict[str, Any]) -> None:
        proposal_id = str(body.get("proposal_id", "")).strip()
        raw_ids = body.get("design_ids")
        if (
            not isinstance(raw_ids, list)
            or not raw_ids
            or not all(isinstance(item, str) for item in raw_ids)
        ):
            raise StaticPrintError("design_ids must be a non-empty string array")
        design_ids = list(dict.fromkeys(validate_design_id(item) for item in raw_ids))
        operation_id = str(body.get("operation_id", "")).strip() or (
            f"history-{secrets.token_hex(8)}"
        )
        self.server.begin_operation(
            operation_id,
            kind="history-cleanup",
            label="Clean superseded authoring history",
            items=design_ids,
            request={"proposal_id": proposal_id, "design_ids": design_ids},
        )
        try:
            for design_id in design_ids:
                self.server.update_operation(operation_id, design_id, "running")
            with (
                self.server.history_cleanup_lock,
                self.server.generation_lock,
                self.server.release_lock,
            ):
                receipt = execute_history_cleanup(
                    work_root=self.server.config.work_root,
                    authoring_root=self.server.config.authoring_root,
                    release_references=self._history_release_references(),
                    proposal_id=proposal_id,
                    design_ids=design_ids,
                )
            for design_id in design_ids:
                self.server.update_operation(operation_id, design_id, "succeeded")
        except Exception as exc:
            for design_id in design_ids:
                try:
                    self.server.update_operation(
                        operation_id, design_id, "failed", error=str(exc)
                    )
                except StaticPrintError:
                    pass
            self.server.finish_operation(operation_id, error=str(exc))
            raise
        else:
            self.server.finish_operation(operation_id)
        self._json(HTTPStatus.OK, {**receipt, "operation_id": operation_id})

    def _login(self, body: dict[str, Any]) -> None:
        role = body.get("role")
        if role not in {"reviewer", "operator"}:
            raise StaticPrintError("login role must be reviewer or operator")
        supplied = body.get("access_code")
        expected = (
            self.server.config.operator_access_code
            if role == "operator"
            else self.server.config.reviewer_access_code
        )
        if expected is not None and not secrets.compare_digest(str(supplied), expected):
            self._json(HTTPStatus.FORBIDDEN, {"error": "invalid access code"})
            return
        token = secrets.token_urlsafe(32)
        self.server.sessions[token] = role
        data = json.dumps({"role": role}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Set-Cookie",
            f"static_print_session={token}; HttpOnly; SameSite=Strict; Path=/",
        )
        self.end_headers()
        self.wfile.write(data)

    def _transition(self, body: dict[str, Any]) -> None:
        seed_design_id = str(body.get("seed_design_id", ""))
        action = str(body.get("action", ""))
        source, target = _seed_transition(action)
        destination = move_seed(
            self.server.config.work_root,
            seed_design_id=seed_design_id,
            source=source,
            target=target,
        )
        self._json(
            HTTPStatus.OK,
            {
                "seed_design_id": seed_design_id,
                "action": action,
                "pool": target,
                "destination": str(destination),
            },
        )

    def _import_collection(self, body: dict[str, Any]) -> None:
        raw_path = body.get("collection_root")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise StaticPrintError(
                "collection_root must be a non-empty local directory path"
            )
        # ID allocation scans all lifecycle pools before assigning a collision
        # suffix. Serialize intake requests so concurrent operators cannot claim
        # the same identifier between that scan and the atomic source copy.
        with self.server.intake_lock:
            result = import_review_collection(
                self.server.config.work_root,
                Path(raw_path.strip()),
            )
        self._json(HTTPStatus.OK, result)

    def _save_subject_generation_requirements(
        self, body: dict[str, Any]
    ) -> None:
        requirements = body.get("subject_generation_requirements", "")
        if not isinstance(requirements, str):
            raise StaticPrintError(
                "subject_generation_requirements must be a string"
            )
        with self.server.intake_lock:
            result = save_subject_generation_requirements(
                self.server.config.inputs_root,
                seed_design_id=str(body.get("seed_design_id", "")),
                requirements=requirements,
                expected_source_sha256=str(
                    body.get("expected_source_sha256", "")
                ),
            )
        self._json(HTTPStatus.OK, result)

    def _transition_batch(self, body: dict[str, Any]) -> None:
        raw_ids = body.get("seed_design_ids")
        if (
            not isinstance(raw_ids, list)
            or not raw_ids
            or not all(isinstance(item, str) for item in raw_ids)
        ):
            raise StaticPrintError("seed_design_ids must be a non-empty string array")
        action = str(body.get("action", ""))
        source, target = _seed_transition(action)
        seed_ids = [
            validate_id(item, "seed design ID") for item in dict.fromkeys(raw_ids)
        ]
        operation_id = str(body.get("operation_id", "")).strip() or (
            f"seed-{secrets.token_hex(8)}"
        )
        self.server.begin_operation(
            operation_id,
            kind="seed-transition",
            label=f"Batch seed {action}",
            items=seed_ids,
            request={"seed_design_ids": seed_ids, "action": action},
        )
        try:
            destinations = move_seeds(
                self.server.config.work_root,
                seed_design_ids=seed_ids,
                source=source,
                target=target,
            )
            for seed_id, destination in zip(seed_ids, destinations, strict=True):
                self.server.operation_event(
                    operation_id,
                    f"Moved to {target}: {destination}",
                    item_label=seed_id,
                )
                self.server.update_operation(operation_id, seed_id, "succeeded")
        except Exception as exc:
            self.server.finish_operation(operation_id, error=str(exc))
            raise
        else:
            self.server.finish_operation(operation_id)
        self._json(
            HTTPStatus.OK,
            {
                "action": action,
                "pool": target,
                "count": len(destinations),
                "destinations": [str(path) for path in destinations],
                "operation_id": operation_id,
            },
        )

    def _generate_subjects(self, body: dict[str, Any]) -> None:
        normalized = dict(body)
        normalized["seed_design_ids"] = [str(body.get("seed_design_id", ""))]
        self._generate_subjects_batch(normalized)

    def _generate_subjects_batch(
        self, body: dict[str, Any], *, resume: bool = False
    ) -> None:
        raw_seed_ids = body.get("seed_design_ids")
        if (
            not isinstance(raw_seed_ids, list)
            or not raw_seed_ids
            or not all(isinstance(item, str) for item in raw_seed_ids)
        ):
            raise StaticPrintError("seed_design_ids must be a non-empty string array")
        seed_ids = [
            validate_id(item, "seed design ID") for item in dict.fromkeys(raw_seed_ids)
        ]
        requested = body.get("subject_ids")
        if (
            not isinstance(requested, list)
            or not requested
            or not all(isinstance(item, str) for item in requested)
        ):
            raise StaticPrintError("subject_ids must be a non-empty string array")
        _, catalog = load_subject_catalog(self.server.config.subject_catalog)
        by_id = {subject.subject_id: subject for subject in catalog}
        unknown = sorted(set(requested) - set(by_id))
        if unknown:
            raise StaticPrintError(
                f"unknown subject IDs {unknown}; available={sorted(by_id)}"
            )
        subjects = [by_id[subject_id] for subject_id in dict.fromkeys(requested)]
        force = body.get("force") is True
        instruction = body.get("instruction", "")
        if not isinstance(instruction, str):
            raise StaticPrintError("instruction must be a string")
        instruction = instruction.strip()
        if len(instruction) > 2000:
            raise StaticPrintError(
                "subject generation instruction must not exceed 2000 characters"
            )
        for seed_id in seed_ids:
            seed = self.server.config.graduated_root / seed_id / "source-design.png"
            if not seed.is_file():
                raise StaticPrintError(
                    f"graduated seed design does not exist: {seed.resolve()}"
                )
        operation_id = str(body.get("operation_id", "")).strip() or (
            f"subject-{secrets.token_hex(8)}"
        )
        work_items = [
            f"{seed_id} / {subject.subject_id}"
            for seed_id in seed_ids
            for subject in subjects
        ]
        request = {
            "seed_design_ids": seed_ids,
            "subject_ids": [subject.subject_id for subject in subjects],
            "force": force,
            "instruction": instruction,
        }
        requests_per_minute, max_concurrency = _subject_batch_limits(body)
        image_max_attempts, image_retry_base_seconds = _subject_retry_settings(body)
        results_by_seed: dict[str, list[dict[str, Any]]] = {
            seed_id: [] for seed_id in seed_ids
        }
        skipped_existing: list[dict[str, str]] = []
        generated_count = 0
        with self.server.subject_generation_lock:
            request["image_requests_per_minute"] = requests_per_minute
            request["image_max_concurrency"] = max_concurrency
            request["image_max_attempts"] = image_max_attempts
            request["image_retry_base_seconds"] = image_retry_base_seconds
            self.server.begin_operation(
                operation_id,
                kind="subject-generation",
                label="Batch subject-preview generation",
                items=work_items,
                request=request,
                resume=resume,
            )
            completed_items = {
                item["item_key"]
                for item in operation_snapshot(
                    self.server.config.database, operation_id
                )["items"]
                if item["status"] in {"succeeded", "skipped"}
            }
            limiter = _ImageRequestRateLimiter(requests_per_minute)
            pending: list[tuple[str, str, Any]] = []
            try:
                for seed_id in seed_ids:
                    for subject in subjects:
                        item_label = f"{seed_id} / {subject.subject_id}"
                        if item_label in completed_items:
                            continue
                        output = (
                            self.server.config.graduated_root
                            / seed_id
                            / subject.subject_id
                            / "source-design.png"
                        )
                        provenance_path = output.parent / "subject-generation.json"
                        if resume and output.is_file() and provenance_path.is_file():
                            provenance = read_json(provenance_path)
                            if provenance.get("operation_id") == operation_id:
                                self.server.update_operation(
                                    operation_id, item_label, "succeeded"
                                )
                                continue
                        if output.is_file() and not force:
                            skipped_existing.append(
                                {
                                    "seed_design_id": seed_id,
                                    "subject_id": subject.subject_id,
                                    "output": str(output.resolve()),
                                }
                            )
                            self.server.update_operation(
                                operation_id, item_label, "skipped"
                            )
                            continue
                        pending.append((item_label, seed_id, subject))

                worker_count = min(max_concurrency, len(pending))
                self.server.operation_event(
                    operation_id,
                    "Paid image scheduler: "
                    f"{requests_per_minute} request starts/minute, "
                    f"{worker_count} active worker(s) with a configured maximum "
                    f"of {max_concurrency}, "
                    f"{limiter.minimum_interval_seconds:.2f}s start spacing, "
                    f"{len(pending)} queued work unit(s). Corrective image retries "
                    "and provider retries use the same quota; "
                    f"provider_max_attempts={image_max_attempts}.",
                )
                failures: list[tuple[str, str]] = []
                if pending:

                    def generate_one(
                        item_label: str, seed_id: str, subject: Any
                    ) -> list[dict[str, Any]]:
                        first_request = True

                        def before_image_request() -> None:
                            nonlocal first_request
                            limiter.wait()
                            if first_request:
                                self.server.update_operation(
                                    operation_id, item_label, "running"
                                )
                                first_request = False
                            else:
                                self.server.operation_event(
                                    operation_id,
                                    "Started subsequent rate-limited image request.",
                                    item_label=item_label,
                                )

                        def image_retry_event(details: dict[str, Any]) -> None:
                            self.server.operation_event(
                                operation_id,
                                "Image provider retry queued: "
                                f"attempt={details['provider_attempt']}; "
                                f"next_attempt={details['next_provider_attempt']}; "
                                f"delay_seconds={details['delay_seconds']}; "
                                f"rate_limited={details['rate_limited']}; "
                                f"error={details['error']}",
                                severity="warning",
                                item_label=item_label,
                            )

                        return generate_subject_variants(
                            graduated_root=self.server.config.graduated_root,
                            seed_design_id=seed_id,
                            subjects=[subject],
                            force=force,
                            instruction=instruction,
                            operation_id=operation_id,
                            before_image_request=before_image_request,
                            provider_max_attempts=image_max_attempts,
                            provider_retry_base_seconds=image_retry_base_seconds,
                            throttle_image_requests=limiter.defer,
                            image_retry_event=image_retry_event,
                        )

                    with ThreadPoolExecutor(
                        max_workers=worker_count,
                        thread_name_prefix="static-subject",
                    ) as executor:
                        future_items = {}
                        for item_label, seed_id, subject in pending:
                            future = executor.submit(
                                generate_one, item_label, seed_id, subject
                            )
                            future_items[future] = (item_label, seed_id)
                        for future in as_completed(future_items):
                            item_label, seed_id = future_items[future]
                            try:
                                generated = future.result()
                            except Exception as exc:  # noqa: BLE001 - isolate work item
                                message = str(exc)
                                failures.append((item_label, message))
                                self.server.update_operation(
                                    operation_id,
                                    item_label,
                                    "failed",
                                    error=message,
                                )
                                continue
                            generated_count += len(generated)
                            results_by_seed[seed_id].extend(generated)
                            for artifact in generated:
                                self.server.operation_event(
                                    operation_id,
                                    f"Generated subject preview: {artifact['output']}",
                                    item_label=item_label,
                                )
                                if artifact.get("operator_review_recommended"):
                                    validation = (
                                        artifact.get("semantic_validation") or {}
                                    )
                                    self.server.operation_event(
                                        operation_id,
                                        "Operator review recommended for this subject "
                                        "preview: "
                                        f"{validation.get('reason', 'borderline animal behavior')}",
                                        severity="warning",
                                        item_label=item_label,
                                    )
                                print_warning = artifact.get(
                                    "print_readiness_warning"
                                )
                                if print_warning:
                                    self.server.operation_event(
                                        operation_id,
                                        str(print_warning),
                                        severity="warning",
                                        item_label=item_label,
                                    )
                            self.server.update_operation(
                                operation_id, item_label, "succeeded"
                            )
                if failures:
                    examples = "; ".join(
                        f"{item}: {message}" for item, message in failures[:3]
                    )
                    raise StaticPrintError(
                        "subject generation batch completed with "
                        f"{len(failures)} failed work unit(s) and "
                        f"{generated_count} successful generation(s); "
                        f"first_failures={examples}; fix the failed inputs and resume "
                        f"operation {operation_id!r} to retain successful outputs"
                    )
                for generated_subjects in results_by_seed.values():
                    generated_subjects.sort(
                        key=lambda artifact: str(artifact.get("subject_id", ""))
                    )
            except Exception as exc:
                self.server.finish_operation(operation_id, error=str(exc))
                raise
            else:
                self.server.finish_operation(operation_id)
        results = [
            {"seed_design_id": seed_id, "subjects": results_by_seed[seed_id]}
            for seed_id in seed_ids
        ]
        self._json(
            HTTPStatus.OK,
            {
                "operation_id": operation_id,
                "seed_count": len(seed_ids),
                "subject_count": len(subjects),
                "generated_count": generated_count,
                "skipped_existing": skipped_existing,
                "results": results,
                "image_scheduler": {
                    "requests_per_minute": requests_per_minute,
                    "max_concurrency": max_concurrency,
                    "max_attempts": image_max_attempts,
                    "retry_base_seconds": image_retry_base_seconds,
                    "minimum_start_interval_seconds": round(
                        limiter.minimum_interval_seconds, 3
                    ),
                },
            },
        )

    def _subject_decision(self, body: dict[str, Any]) -> None:
        design_id = str(body.get("design_id", ""))
        identity = design_identity(design_id)
        if identity.subject_id is None:
            raise StaticPrintError(
                "subject decisions require a derived subject design ID"
            )
        source = design_source_path(self.server.config.graduated_root, design_id)
        if not source.is_file():
            raise StaticPrintError(
                f"graduated subject preview does not exist: {source.resolve()}"
            )
        result = save_operator_decision(
            self.server.config.database,
            design_id=design_id,
            source_sha256=sha256_file(source),
            decision=str(body.get("decision", "")),
            note=str(body.get("note", "")),
        )
        self._json(HTTPStatus.OK, result)

    def _subject_decision_batch(self, body: dict[str, Any]) -> None:
        raw_ids = body.get("design_ids")
        if (
            not isinstance(raw_ids, list)
            or not raw_ids
            or not all(isinstance(item, str) for item in raw_ids)
        ):
            raise StaticPrintError("design_ids must be a non-empty string array")
        designs: list[tuple[str, str]] = []
        for design_id in dict.fromkeys(raw_ids):
            identity = design_identity(design_id)
            if identity.subject_id is None:
                raise StaticPrintError(
                    f"batch subject approval requires derived subject designs; "
                    f"actual seed={design_id!r}"
                )
            source = design_source_path(self.server.config.graduated_root, design_id)
            if not source.is_file():
                raise StaticPrintError(
                    f"graduated subject preview does not exist: "
                    f"design_id={design_id!r}; path={source.resolve()}"
                )
            designs.append((design_id, sha256_file(source)))
        decision = str(body.get("decision", ""))
        note = str(body.get("note", ""))
        operation_id = str(body.get("operation_id", "")).strip() or (
            f"decision-{secrets.token_hex(8)}"
        )
        item_labels = [design_id for design_id, _ in designs]
        self.server.begin_operation(
            operation_id,
            kind="subject-decision",
            label=f"Batch subject decision: {decision}",
            items=item_labels,
            request={"design_ids": item_labels, "decision": decision, "note": note},
        )
        try:
            results = save_operator_decisions(
                self.server.config.database,
                designs=designs,
                decision=decision,
                note=note,
            )
            for design_id in item_labels:
                self.server.update_operation(operation_id, design_id, "succeeded")
        except Exception as exc:
            self.server.finish_operation(operation_id, error=str(exc))
            raise
        else:
            self.server.finish_operation(operation_id)
        self._json(
            HTTPStatus.OK,
            {
                "count": len(results),
                "results": results,
                "operation_id": operation_id,
            },
        )

    def _generate(self, body: dict[str, Any]) -> None:
        with self.server.generation_lock:
            self._generate_locked(body)

    def _generate_locked(self, body: dict[str, Any], *, resume: bool = False) -> None:
        profiles = discover_profiles(self.server.config.profiles_root)
        plans = _generation_plans(body.get("plans"), set(profiles))
        upscale_backend = str(
            body.get("upscale_backend")
            or os.environ.get("STATIC_PRINT_UPSCALE_BACKEND", "deterministic")
        ).strip()
        if upscale_backend not in {"deterministic", "bria"}:
            raise StaticPrintError(
                "upscale_backend must be deterministic or bria; "
                f"actual={upscale_backend!r}; environment=STATIC_PRINT_UPSCALE_BACKEND"
            )
        normalize_print_alpha = _boolean_setting(
            body,
            request_key="normalize_print_alpha",
            environment_key="STATIC_PRINT_NORMALIZE_ALPHA",
            default=False,
        )
        if self.server.config.s3_bucket:
            published, _ = self.server.s3_publications(
                force=True,
                allow_stale=False,
                include_source_sha256=False,
            )
        else:
            published = _published_entries(self.server.config.exchange_root)
        plans = _resolve_generation_versions(
            plans,
            authoring_root=self.server.config.authoring_root,
            inputs_root=self.server.config.graduated_root,
            published=published,
        )
        decisions = list_operator_decisions(self.server.config.database)
        source_digests: dict[str, str] = {}
        canvas_policies: dict[str, str] = {}
        for design_id in dict.fromkeys(plan["design_id"] for plan in plans):
            identity = design_identity(design_id)
            if identity.subject_id is None:
                raise StaticPrintError(
                    f"product generation is allowed only for approved subject variants; "
                    f"actual seed={design_id!r}"
                )
            source = design_source_path(self.server.config.graduated_root, design_id)
            digest = sha256_file(source)
            source_digests[design_id] = digest
            canvas_policies[design_id] = design_canvas_policy(
                self.server.config.graduated_root, design_id
            )
            current = next(
                (
                    item
                    for item in decisions.get(design_id, [])
                    if item["source_sha256"] == digest
                ),
                None,
            )
            if current is None or current["decision"] != "approved":
                raise StaticPrintError(
                    f"subject preview must have an approved operator decision before "
                    f"product generation: design_id={design_id!r}; source={source.resolve()}"
                )
        for plan in plans:
            generation_path = (
                design_authoring_path(
                    self.server.config.authoring_root,
                    plan["design_id"],
                    inputs_root=self.server.config.graduated_root,
                )
                / plan["product_profile_id"]
                / plan["version_id"]
                / "generation.json"
            )
            if not generation_path.is_file():
                continue
            generation = read_json(generation_path)
            expected = {
                "schema": "pawmarvel-static-generation-v3",
                "design_id": plan["design_id"],
                "product_profile_id": plan["product_profile_id"],
                "product_profile_sha256": product_profile_sha256(
                    profiles[plan["product_profile_id"]]
                ),
                "version_id": plan["version_id"],
                "source_sha256": source_digests[plan["design_id"]],
                "fit": "cover",
                "composition": profiles[plan["product_profile_id"]].composition,
                "canvas_policy": canvas_policies[plan["design_id"]],
            }
            mismatches = {
                key: {"expected": value, "actual": generation.get(key)}
                for key, value in expected.items()
                if generation.get(key) != value
            }
            if mismatches:
                raise StaticPrintError(
                    "existing product candidate conflicts with the requested plan; "
                    "choose a new version for this subject/profile before retrying "
                    f"the batch: record={generation_path.resolve()}; "
                    f"mismatches={mismatches}"
                )
        conflicts = sorted(
            f"{plan['design_id']}:{plan['version_id']}:{plan['product_profile_id']}"
            for plan in plans
            if any(
                entry["version_id"] == plan["version_id"]
                and entry["product_profile_id"] == plan["product_profile_id"]
                for entry in published.get(plan["design_id"], [])
            )
        )
        if conflicts:
            raise StaticPrintError(
                "published product candidates are immutable; choose a new version ID "
                f"before regenerating these selections: {conflicts}"
            )
        operation_id = str(body.get("operation_id", "")).strip() or (
            f"product-{secrets.token_hex(8)}"
        )
        item_labels = [
            f"{plan['design_id']} / {plan['product_profile_id']} / {plan['version_id']}"
            for plan in plans
        ]
        self.server.begin_operation(
            operation_id,
            kind="product-generation",
            label="Batch product preview and print generation",
            items=item_labels,
            request={
                "plans": plans,
                "upscale_backend": upscale_backend,
                "normalize_print_alpha": normalize_print_alpha,
            },
            resume=resume,
        )
        completed_items = {
            item["item_key"]
            for item in operation_snapshot(self.server.config.database, operation_id)[
                "items"
            ]
            if item["status"] in {"succeeded", "skipped"}
        }
        results = []
        try:
            for plan, item_label in zip(plans, item_labels, strict=True):
                if item_label in completed_items:
                    continue
                self.server.update_operation(operation_id, item_label, "running")
                try:
                    result = generate_design(
                        inputs_root=self.server.config.graduated_root,
                        authoring_root=self.server.config.authoring_root,
                        design_id=plan["design_id"],
                        version_id=plan["version_id"],
                        profiles=[profiles[plan["product_profile_id"]]],
                        fit="cover",
                        operation_id=operation_id,
                        upscale_backend=upscale_backend,
                        normalize_print_alpha=normalize_print_alpha,
                    )
                except Exception as exc:
                    self.server.update_operation(
                        operation_id, item_label, "failed", error=str(exc)
                    )
                    raise
                for warning in result.get("warnings", []):
                    self.server.operation_event(
                        operation_id,
                        str(warning),
                        severity="warning",
                        item_label=item_label,
                    )
                self.server.operation_event(
                    operation_id,
                    f"Generated preview={result['preview']['path']}; "
                    f"print_outputs={len(result['outputs'])}",
                    item_label=item_label,
                )
                results.append(result)
                self.server.update_operation(operation_id, item_label, "succeeded")
        except Exception as exc:
            self.server.finish_operation(operation_id, error=str(exc))
            raise
        else:
            self.server.finish_operation(operation_id)
        self._json(
            HTTPStatus.OK,
            {"operation_id": operation_id, "results": results},
        )

    def _operation_action(self, body: dict[str, Any]) -> None:
        operation_id = validate_id(str(body.get("operation_id", "")), "operation ID")
        action = str(body.get("action", ""))
        operation = operation_snapshot(self.server.config.database, operation_id)
        if action == "cleanup":
            if operation["status"] == "running":
                raise StaticPrintError(
                    f"cannot clean a running operation: operation_id={operation_id!r}"
                )
            result = cleanup_operation_staging(
                authoring_root=self.server.config.authoring_root,
                graduated_root=self.server.config.graduated_root,
                operation_id=operation_id,
            )
            mark_operation_cleaned(self.server.config.database, operation_id)
            self._json(
                HTTPStatus.OK,
                {"operation_id": operation_id, "status": "cleaned", **result},
            )
            return
        if action != "resume":
            raise StaticPrintError("operation action must be resume or cleanup")
        if operation["status"] not in {"failed", "interrupted"}:
            raise StaticPrintError(
                "only failed or interrupted operations can be resumed; "
                f"operation_id={operation_id!r}; status={operation['status']!r}"
            )
        request = dict(operation["request"])
        request["operation_id"] = operation_id
        if operation["kind"] == "subject-generation":
            self._generate_subjects_batch(request, resume=True)
            return
        if operation["kind"] == "product-generation":
            with self.server.generation_lock:
                self._generate_locked(request, resume=True)
            return
        raise StaticPrintError(
            f"operation kind is not resumable: {operation['kind']!r}"
        )

    def _publish(self, body: dict[str, Any]) -> None:
        with self.server.release_lock:
            self._publish_locked(body)

    def _publish_locked(self, body: dict[str, Any]) -> None:
        raw_selections = body.get("selections")
        if not isinstance(raw_selections, list) or not raw_selections:
            raise StaticPrintError("selections must be a non-empty array")
        profiles = discover_profiles(self.server.config.profiles_root)
        selections: list[BundleSelection] = []
        for raw in raw_selections:
            if not isinstance(raw, dict):
                raise StaticPrintError("each publication selection must be an object")
            profile_id = str(raw.get("product_profile_id", ""))
            profile = profiles.get(profile_id)
            if profile is None:
                raise StaticPrintError(
                    f"unknown product profile {profile_id!r}; available={sorted(profiles)}"
                )
            selections.append(
                BundleSelection(
                    design_id=str(raw.get("design_id", "")),
                    version_id=str(raw.get("version_id", "")),
                    profile=profile,
                )
            )
        config = self.server.config
        if not config.s3_bucket:
            raise StaticPrintError(
                "S3 publication is not configured; set STATIC_PRINT_S3_BUCKET and restart the server"
            )
        execute = body.get("execute") is True
        requested_release_id = str(body.get("release_id", "")).strip()
        operation_id = str(body.get("operation_id", "")).strip() or (
            f"release-{secrets.token_hex(8)}"
        )
        selection_values = [
            {
                "design_id": selection.design_id,
                "product_profile_id": selection.profile.profile_id,
                "version_id": selection.version_id,
            }
            for selection in selections
        ]
        item_by_identity = {
            (
                selection.design_id,
                selection.profile.profile_id,
                selection.version_id,
            ): (
                f"{selection.design_id} / {selection.profile.profile_id} / "
                f"{selection.version_id}"
            )
            for selection in selections
        }
        item_labels = list(item_by_identity.values())
        self.server.begin_operation(
            operation_id,
            kind="release-publication" if execute else "release-validation",
            label=(
                "Batch bundle publication to S3"
                if execute
                else "Batch bundle validation"
            ),
            items=item_labels,
            request={
                "release_id": requested_release_id or "auto",
                "selections": selection_values,
                "execute": execute,
            },
        )
        try:
            self.server.operation_event(
                operation_id,
                "Reading the current S3 discovery catalog and release history.",
            )
            remote_publications, _ = self.server.s3_publications(
                force=True,
                allow_stale=False,
                include_source_sha256=False,
            )
            remote_release_ids = {
                entry["release_id"]
                for entries in remote_publications.values()
                for entry in entries
            }
            decisions = list_operator_decisions(config.database)
            for selection in selections:
                identity = design_identity(selection.design_id)
                if identity.subject_id is None:
                    raise StaticPrintError(
                        f"release publication accepts subject variants only; "
                        f"actual seed={selection.design_id!r}"
                    )
                source = design_source_path(config.graduated_root, selection.design_id)
                digest = sha256_file(source)
                current = next(
                    (
                        item
                        for item in decisions.get(selection.design_id, [])
                        if item["source_sha256"] == digest
                    ),
                    None,
                )
                if current is None or current["decision"] != "approved":
                    raise StaticPrintError(
                        f"release selection has no current approved subject decision: "
                        f"design_id={selection.design_id!r}; source={source.resolve()}"
                    )
            release_id = (
                _next_release_id(config.exchange_root, remote_release_ids)
                if requested_release_id in {"", "auto"}
                else validate_release_id(requested_release_id)
            )
            self.server.operation_event(
                operation_id,
                f"Preflight complete; release_id={release_id}",
            )
        except Exception as exc:
            self.server.finish_operation(operation_id, error=str(exc))
            raise
        started_items: set[str] = set()
        manifest_to_item: dict[str, str] = {}
        bundle_roots: list[tuple[Path, str]] = []

        def prepare_progress(event: str, details: dict[str, Any]) -> None:
            identity = (
                str(details.get("design_id", "")),
                str(details.get("product_profile_id", "")),
                str(details.get("version_id", "")),
            )
            item = item_by_identity.get(identity)
            if event == "bundle-start" and item:
                self.server.operation_event(
                    operation_id,
                    f"Building and validating bundle: {item}",
                    item_label=item,
                )
            elif event == "bundle-complete" and item:
                manifest_path = str(details["manifest_path"])
                manifest_to_item[manifest_path] = item
                bundle_roots.append((Path(manifest_path).parent, item))
                if execute:
                    self.server.operation_event(
                        operation_id,
                        f"Bundle validated; waiting for S3 upload: {item}",
                        item_label=item,
                    )
                else:
                    self.server.update_operation(operation_id, item, "succeeded")
            elif event == "release-validated":
                self.server.operation_event(
                    operation_id,
                    f"Release catalog validated: {details['catalog_path']}",
                )

        def publish_progress(event: str, details: dict[str, Any]) -> None:
            source = Path(str(details.get("source", ""))).resolve()
            matched_item = next(
                (
                    item
                    for root, item in bundle_roots
                    if source == root or root in source.parents
                ),
                None,
            )
            if event == "upload-start":
                if matched_item and matched_item not in started_items:
                    self.server.update_operation(operation_id, matched_item, "running")
                    started_items.add(matched_item)
                self.server.operation_event(
                    operation_id,
                    f"Uploading: {details['uri']}",
                    item_label=matched_item,
                )
            elif event == "upload-complete":
                self.server.operation_event(
                    operation_id,
                    f"S3 {details['status']}: {details['uri']}",
                    item_label=matched_item,
                )
                manifest_item = manifest_to_item.get(str(source))
                if manifest_item:
                    self.server.update_operation(
                        operation_id, manifest_item, "succeeded"
                    )
            elif event == "upload-failed":
                self.server.operation_event(
                    operation_id,
                    f"S3 upload failed after retries: {details['uri']}; "
                    f"error={details['error']}",
                    severity="error",
                    item_label=matched_item,
                )
                if matched_item:
                    self.server.update_operation(
                        operation_id,
                        matched_item,
                        "failed",
                        error=str(details["error"]),
                    )
            elif event == "upload-stage-start":
                self.server.operation_event(
                    operation_id,
                    "Starting concurrent S3 upload stage: "
                    f"stage={details['stage']}; "
                    f"objects={details['object_count']}; "
                    f"workers={details['worker_count']}.",
                )
            elif event == "catalog-start":
                self.server.operation_event(
                    operation_id,
                    "Updating the stable FE discovery catalog and immutable history.",
                )
            elif event == "publication-complete":
                self.server.operation_event(
                    operation_id,
                    f"Publication complete: {details['object_count']} object(s); "
                    f"catalog={details['catalog_uri']}",
                )
            elif event == "validation-complete":
                self.server.operation_event(
                    operation_id,
                    f"Validation complete: {details['object_count']} object(s) planned.",
                )
            elif event == "preflight-start":
                self.server.operation_event(
                    operation_id,
                    f"Checking {details['object_count']} immutable S3 object(s) before upload.",
                )
            elif event == "preflight-complete":
                self.server.operation_event(
                    operation_id,
                    "S3 preflight complete: "
                    f"{details['existing_count']} existing match(es), "
                    f"{details['missing_count']} new object(s).",
                )

        try:
            plan = prepare_release(
                release_id=release_id,
                selections=selections,
                inputs_root=config.graduated_root,
                authoring_root=config.authoring_root,
                exchange_root=config.exchange_root,
                progress=prepare_progress,
            )
            prepared_provenance: list[dict[str, str]] = []
            if execute:
                for manifest_path in plan.bundle_manifests:
                    manifest = read_json(manifest_path)
                    relative = str(
                        manifest_path.relative_to(plan.staging_root)
                    ).replace("\\", "/")
                    object_key = (
                        f"{config.s3_prefix}/{relative}"
                        if config.s3_prefix
                        else relative
                    )
                    prepared_provenance.append(
                        {
                            "object_key": object_key,
                            "manifest_sha256": sha256_file(manifest_path),
                            "source_sha256": str(manifest["source_sha256"]),
                        }
                    )
            result = publish_release(
                plan,
                exchange_root=config.exchange_root,
                bucket=config.s3_bucket,
                prefix=config.s3_prefix,
                aws_profile=config.aws_profile,
                region=config.aws_region,
                execute=execute,
                progress=publish_progress,
            )
            if execute:
                self.server.cache_prepared_bundle_provenance(prepared_provenance)
                self.server._bootstrap_local_publications(authoritative=True)
        except Exception as exc:
            snapshot = operation_snapshot(config.database, operation_id)
            for item in snapshot["items"]:
                if item["status"] not in {"pending", "running"}:
                    continue
                self.server.operation_event(
                    operation_id,
                    "Skipped after a sibling upload or release stage failed; "
                    "retrying the release will safely reuse matching immutable objects.",
                    severity="warning",
                    item_label=str(item["item_key"]),
                )
                self.server.update_operation(
                    operation_id, str(item["item_key"]), "skipped"
                )
            self.server.finish_operation(operation_id, error=str(exc))
            raise
        else:
            self.server.finish_operation(operation_id)
        result["operation_id"] = operation_id
        self._json(HTTPStatus.OK, result)

    def _product_lifecycle(self, body: dict[str, Any]) -> None:
        config = self.server.config
        if not config.s3_bucket:
            raise StaticPrintError(
                "S3 product lifecycle is not configured; set STATIC_PRINT_S3_BUCKET "
                "and restart the server"
            )
        raw = body.get("products")
        if not isinstance(raw, list) or not raw:
            raise StaticPrintError("products must be a non-empty array")
        bundle_ids: list[str] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict) or set(item) != {
                "design_id",
                "product_profile_id",
            }:
                raise StaticPrintError(
                    f"product lifecycle selection {index} must contain exactly "
                    "design_id and product_profile_id"
                )
            design_id = validate_design_id(
                str(item["design_id"]), "product lifecycle design ID"
            )
            profile_id = validate_id(
                str(item["product_profile_id"]),
                "product lifecycle profile ID",
            )
            bundle_ids.append(bundle_id_for(design_id, profile_id))
        status = str(body.get("status", ""))
        reason = body.get("reason", "")
        if not isinstance(reason, str):
            raise StaticPrintError("product lifecycle reason must be a string")
        operation_id = str(body.get("operation_id", "")).strip() or (
            f"lifecycle-{secrets.token_hex(8)}"
        )
        self.server.begin_operation(
            operation_id,
            kind="product-lifecycle",
            label=f"Batch product lifecycle: {status}",
            items=bundle_ids,
            request={"products": raw, "status": status, "reason": reason},
        )
        try:
            with self.server.release_lock:
                self.server.operation_event(
                    operation_id,
                    "Applying the lifecycle update to the stable FE discovery catalog.",
                )
                result = update_product_lifecycle(
                    bucket=config.s3_bucket,
                    prefix=config.s3_prefix,
                    profile=config.aws_profile,
                    region=config.aws_region,
                    exchange_root=config.exchange_root,
                    bundle_ids=bundle_ids,
                    status=status,
                    reason=reason,
                    catalog_cache=self.server.s3_catalog_cache,
                )
                self.server._bootstrap_local_publications(authoritative=True)
            for bundle_id in bundle_ids:
                self.server.update_operation(operation_id, bundle_id, "succeeded")
        except Exception as exc:
            self.server.finish_operation(operation_id, error=str(exc))
            raise
        else:
            self.server.finish_operation(operation_id)
        result["operation_id"] = operation_id
        self._json(HTTPStatus.OK, result)

    def _rebuild_catalog(self) -> None:
        config = self.server.config
        if not config.s3_bucket:
            raise StaticPrintError(
                "S3 catalog rebuild is not configured; set STATIC_PRINT_S3_BUCKET "
                "and restart the server"
            )
        with self.server.release_lock:
            result = rebuild_discovery_catalog(
                bucket=config.s3_bucket,
                prefix=config.s3_prefix,
                profile=config.aws_profile,
                region=config.aws_region,
                exchange_root=config.exchange_root,
                catalog_cache=self.server.s3_catalog_cache,
            )
            self.server._bootstrap_local_publications(authoritative=True)
        self._json(HTTPStatus.OK, result)


def create_server(host: str, port: int, config: ServerConfig) -> StaticPrintHTTPServer:
    initialize_workspace(config.work_root)
    return StaticPrintHTTPServer((host, port), config)
