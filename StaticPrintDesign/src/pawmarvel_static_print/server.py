from __future__ import annotations

import fcntl
import json
import mimetypes
import secrets
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import unquote, urlparse

from .authoring import (
    design_canvas_policy,
    discover_designs,
    generate_design,
    initialize_workspace,
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
)
from .lifecycle import (
    generate_subject_variants,
    import_review_collection,
    load_subject_catalog,
    move_seed,
    move_seeds,
    pool_root,
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
        return "Refresh operator state and retry with the automatically allocated next version or release ID."
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
        self.generation_lock = Lock()
        self.subject_generation_lock = Lock()
        self.release_lock = Lock()
        self.s3_status_lock = Lock()
        self.s3_catalog_cache: dict[str, dict[str, Any]] = {}
        self.s3_published_cache: dict[str, list[dict[str, str]]] | None = None
        self.s3_status_cache: dict[str, Any] | None = None
        self.s3_checked_at = 0.0
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

    def operation_snapshot(self) -> list[dict[str, Any]]:
        return operation_snapshots(self.config.database)

    def s3_publications(
        self,
        *,
        force: bool = False,
        allow_stale: bool = True,
    ) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
        config = self.config
        if not config.s3_bucket:
            raise StaticPrintError(
                "S3 release status is not configured; set STATIC_PRINT_S3_BUCKET"
            )
        with self.s3_status_lock:
            now = monotonic()
            if (
                not force
                and self.s3_published_cache is not None
                and now - self.s3_checked_at < 15
            ):
                return self.s3_published_cache, dict(self.s3_status_cache or {})
            try:
                published, inventory = load_s3_release_entries(
                    bucket=config.s3_bucket,
                    prefix=config.s3_prefix,
                    profile=config.aws_profile,
                    region=config.aws_region,
                    catalog_cache=self.s3_catalog_cache,
                    include_source_sha256=True,
                )
            except StaticPrintError as exc:
                if allow_stale and self.s3_published_cache is not None:
                    status = {
                        **(self.s3_status_cache or {}),
                        "available": False,
                        "stale": True,
                        "error": str(exc),
                    }
                    return self.s3_published_cache, status
                raise
            self.s3_published_cache = published
            self.s3_checked_at = now
            self.s3_status_cache = {
                "source": "s3-catalog-index",
                "available": True,
                "stale": False,
                "checked_at": utc_now(),
                "error": None,
                **inventory,
            }
            return published, dict(self.s3_status_cache)

    def invalidate_s3_publications(self) -> None:
        with self.s3_status_lock:
            self.s3_checked_at = 0.0


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
        feedback = list_feedback(config.database)
        decisions = list_operator_decisions(config.database) if operator else {}
        profiles = discover_profiles(config.profiles_root)
        for design in designs:
            design_id = design["design_id"]
            design_root = dict(roots)[design["pool"]]
            design["source_url"] = f"/asset/source/{design['pool']}/{design_id}"
            design["transparency"] = design.get(
                "source_transparency"
            ) or transparency_report(Path(design["source"]))
            design["canvas_policy"] = design_canvas_policy(design_root, design_id)
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
                    recorded_print_transparency = generation_transparency.get("prints")
                    print_transparency = (
                        recorded_print_transparency
                        if isinstance(recorded_print_transparency, dict)
                        else {}
                    )
                    print_assets = []
                    for target in profile.targets:
                        filename = (
                            f"{design_id}--{profile.profile_id}.png"
                            if len(profile.targets) == 1
                            else f"{design_id}--{profile.profile_id}--{target.target_id}.png"
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
                            }
                        )
                    expected = {item["filename"] for item in print_assets}
                    current_generation = (
                        isinstance(generation, dict)
                        and generation.get("schema") == "pawmarvel-static-generation-v3"
                        and generation.get("source_sha256") == design["source_sha256"]
                        and generation.get("product_profile_id") == profile_id
                        and generation.get("product_profile_sha256")
                        == product_profile_sha256(profile)
                        and generation.get("fit") == "cover"
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
                        profile_preview_name = f"{profile.profile_id}.png"
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
                                },
                                "canvas_policy": design["canvas_policy"],
                                "prints": print_assets,
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
                remote_entries, publication_status = self.server.s3_publications()
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
                        publication_status["available"] or publication_status["stale"]
                    ),
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
        return result

    def do_GET(self) -> None:
        path = urlparse(self.path).path
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
                self._json(
                    HTTPStatus.OK,
                    {"operations": self.server.operation_snapshot()},
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
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except StaticPrintError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - safety net
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"unexpected {type(exc).__name__}: {exc}"},
            )

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
        result = import_review_collection(
            self.server.config.work_root,
            Path(raw_path.strip()),
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
        results: list[dict[str, Any]] = []
        skipped_existing: list[dict[str, str]] = []
        generated_count = 0
        with self.server.subject_generation_lock:
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
            current_item: str | None = None
            try:
                for seed_id in seed_ids:
                    generated_subjects: list[dict[str, Any]] = []
                    for subject in subjects:
                        item_label = f"{seed_id} / {subject.subject_id}"
                        current_item = item_label
                        if item_label in completed_items:
                            continue
                        self.server.update_operation(
                            operation_id, item_label, "running"
                        )
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
                        generated = generate_subject_variants(
                            graduated_root=self.server.config.graduated_root,
                            seed_design_id=seed_id,
                            subjects=[subject],
                            force=force,
                            instruction=instruction,
                            operation_id=operation_id,
                        )
                        generated_count += len(generated)
                        generated_subjects.extend(generated)
                        for artifact in generated:
                            self.server.operation_event(
                                operation_id,
                                f"Generated subject preview: {artifact['output']}",
                                item_label=item_label,
                            )
                        self.server.update_operation(
                            operation_id, item_label, "succeeded"
                        )
                    results.append(
                        {
                            "seed_design_id": seed_id,
                            "subjects": generated_subjects,
                        }
                    )
            except Exception as exc:
                if current_item:
                    self.server.update_operation(
                        operation_id, current_item, "failed", error=str(exc)
                    )
                self.server.finish_operation(operation_id, error=str(exc))
                raise
            else:
                self.server.finish_operation(operation_id)
        self._json(
            HTTPStatus.OK,
            {
                "operation_id": operation_id,
                "seed_count": len(seed_ids),
                "subject_count": len(subjects),
                "generated_count": generated_count,
                "skipped_existing": skipped_existing,
                "results": results,
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
        if self.server.config.s3_bucket:
            published, _ = self.server.s3_publications(force=True, allow_stale=False)
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
            request={"plans": plans},
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
                force=True, allow_stale=False
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
        current_item: str | None = None
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
            nonlocal current_item
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
                current_item = matched_item
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
                    current_item = None
            elif event == "catalog-start":
                current_item = None
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

        try:
            plan = prepare_release(
                release_id=release_id,
                selections=selections,
                inputs_root=config.graduated_root,
                authoring_root=config.authoring_root,
                exchange_root=config.exchange_root,
                progress=prepare_progress,
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
                self.server.invalidate_s3_publications()
        except Exception as exc:
            if current_item:
                self.server.update_operation(
                    operation_id, current_item, "failed", error=str(exc)
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
                self.server.invalidate_s3_publications()
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
            self.server.invalidate_s3_publications()
        self._json(HTTPStatus.OK, result)


def create_server(host: str, port: int, config: ServerConfig) -> StaticPrintHTTPServer:
    initialize_workspace(config.work_root)
    return StaticPrintHTTPServer((host, port), config)
