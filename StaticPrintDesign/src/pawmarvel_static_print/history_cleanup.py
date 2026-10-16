"""Reviewable cleanup plans for superseded local authoring history."""

from __future__ import annotations

import hashlib
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from .core import (
    StaticPrintError,
    design_authoring_path,
    design_identity,
    read_json,
    utc_now,
    validate_id,
    validate_version,
    write_json,
)

HISTORY_CLEANUP_SCHEMA = "pawmarvel-static-history-cleanup-v1"


def _tree_inventory(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    byte_count = 0
    file_count = 0
    for item in sorted(
        candidate for candidate in path.rglob("*") if candidate.is_file()
    ):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        size = item.stat().st_size
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(size.to_bytes(8, "big"))
        with item.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        byte_count += size
        file_count += 1
    return {
        "sha256": digest.hexdigest(),
        "bytes": byte_count,
        "files": file_count,
    }


def _pool_for_seed(work_root: Path, seed_design_id: str) -> str:
    pools = (
        ("active", "static-design-inputs"),
        ("graduated", "static-graduated-designs"),
        ("abandoned", "static-abandoned-designs"),
    )
    matches = [
        name
        for name, directory in pools
        if (work_root / directory / seed_design_id).is_dir()
    ]
    if len(matches) > 1:
        raise StaticPrintError(
            "seed design exists in multiple lifecycle pools; repair the workspace "
            f"before cleanup: seed_design_id={seed_design_id!r}; pools={matches}"
        )
    return matches[0] if matches else "orphaned"


def _authoring_versions(authoring_root: Path) -> list[dict[str, Any]]:
    root = authoring_root.resolve()
    versions: list[dict[str, Any]] = []
    if not root.is_dir():
        return versions
    for generation_path in sorted(root.rglob("generation.json")):
        version_dir = generation_path.parent.resolve()
        if root not in version_dir.parents:
            continue
        try:
            generation = read_json(generation_path)
            design_id = design_identity(str(generation.get("design_id", ""))).design_id
            profile_id = validate_id(
                str(generation.get("product_profile_id", "")),
                "product profile ID",
            )
            version_id = validate_version(str(generation.get("version_id", "")))
        except StaticPrintError:
            # Invalid and legacy trees belong to the separate obsolete-artifact
            # retirement workflow. History cleanup never guesses their identity.
            continue
        expected = (
            design_authoring_path(root, design_id) / profile_id / version_id
        ).resolve()
        if expected != version_dir:
            continue
        versions.append(
            {
                "design_id": design_id,
                "seed_design_id": design_identity(design_id).seed_design_id,
                "subject_id": design_identity(design_id).subject_id,
                "product_profile_id": profile_id,
                "version_id": version_id,
                "path": version_dir,
            }
        )
    return versions


def _normalize_release_references(
    values: set[tuple[str, str, str]] | list[tuple[str, str, str]],
) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for design_id, profile_id, version_id in values:
        result.add(
            (
                design_identity(design_id).design_id,
                validate_id(profile_id, "product profile ID"),
                validate_version(version_id),
            )
        )
    return result


def cleanup_root(work_root: Path) -> Path:
    return work_root.resolve() / "static-history-cleanup"


def latest_cleanup_state(work_root: Path) -> dict[str, Any] | None:
    root = cleanup_root(work_root)
    paths = list((root / "proposals").glob("*.json"))
    if not paths:
        return None
    latest = max(paths, key=lambda path: (path.stat().st_mtime_ns, path.name))
    proposal = read_json(latest)
    receipt = root / "receipts" / latest.name
    if receipt.is_file():
        proposal = {**proposal, "receipt": read_json(receipt)}
    return proposal


def _build_history_cleanup_proposal(
    *,
    work_root: Path,
    authoring_root: Path,
    release_references: set[tuple[str, str, str]],
    proposal_id: str,
) -> dict[str, Any]:
    """Inventory superseded version directories without changing authoring data."""

    proposal_id = validate_id(proposal_id, "history cleanup proposal ID")
    releases = _normalize_release_references(release_references)
    released_design_ids = {design_id for design_id, _, _ in releases}
    versions = _authoring_versions(authoring_root)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in versions:
        grouped[(item["design_id"], item["product_profile_id"])].append(item)

    protected: set[tuple[str, str, str]] = set(releases)
    for (design_id, profile_id), items in grouped.items():
        pool = _pool_for_seed(work_root.resolve(), items[0]["seed_design_id"])
        if pool in {"active", "graduated"}:
            latest = max(items, key=lambda value: value["version_id"])
            protected.add((design_id, profile_id, latest["version_id"]))

    by_design: dict[str, dict[str, Any]] = {}
    authoring = authoring_root.resolve()
    for item in versions:
        identity = (
            item["design_id"],
            item["product_profile_id"],
            item["version_id"],
        )
        if identity in protected:
            continue
        path: Path = item["path"]
        if authoring not in path.parents:
            raise StaticPrintError(
                f"cleanup candidate escapes authoring root: path={path}; root={authoring}"
            )
        inventory = _tree_inventory(path)
        pool = _pool_for_seed(work_root.resolve(), item["seed_design_id"])
        design = by_design.setdefault(
            item["design_id"],
            {
                "design_id": item["design_id"],
                "seed_design_id": item["seed_design_id"],
                "subject_id": item["subject_id"],
                "pool": pool,
                "lifecycle": (
                    "released" if item["design_id"] in released_design_ids else pool
                ),
                "bytes": 0,
                "files": 0,
                "versions": [],
            },
        )
        design["bytes"] += inventory["bytes"]
        design["files"] += inventory["files"]
        design["versions"].append(
            {
                "product_profile_id": item["product_profile_id"],
                "version_id": item["version_id"],
                "relative_path": path.relative_to(authoring).as_posix(),
                **inventory,
                "reason": (
                    "abandoned design history is not referenced by a release"
                    if pool == "abandoned"
                    else "superseded local version is neither current nor release-referenced"
                ),
            }
        )

    designs = sorted(by_design.values(), key=lambda value: value["design_id"])
    proposal = {
        "schema": HISTORY_CLEANUP_SCHEMA,
        "proposal_id": proposal_id,
        "status": "planned",
        "created_at": utc_now(),
        "authoring_root": str(authoring),
        "design_count": len(designs),
        "version_count": sum(len(item["versions"]) for item in designs),
        "reclaimable_bytes": sum(item["bytes"] for item in designs),
        "designs": designs,
    }
    return proposal


def create_history_cleanup_proposal(
    *,
    work_root: Path,
    authoring_root: Path,
    release_references: set[tuple[str, str, str]],
    proposal_id: str,
) -> dict[str, Any]:
    proposal = _build_history_cleanup_proposal(
        work_root=work_root,
        authoring_root=authoring_root,
        release_references=release_references,
        proposal_id=proposal_id,
    )
    path = cleanup_root(work_root) / "proposals" / f"{proposal_id}.json"
    write_json(path, proposal, exclusive=True)
    return proposal


def execute_history_cleanup(
    *,
    work_root: Path,
    authoring_root: Path,
    release_references: set[tuple[str, str, str]],
    proposal_id: str,
    design_ids: list[str],
) -> dict[str, Any]:
    """Delete reviewed histories after checking identity, references, and hashes again."""

    proposal_id = validate_id(proposal_id, "history cleanup proposal ID")
    selected = list(
        dict.fromkeys(design_identity(value).design_id for value in design_ids)
    )
    if not selected:
        raise StaticPrintError("history cleanup requires at least one selected design")
    root = cleanup_root(work_root)
    proposal_path = root / "proposals" / f"{proposal_id}.json"
    proposal = read_json(proposal_path)
    if proposal.get("schema") != HISTORY_CLEANUP_SCHEMA:
        raise StaticPrintError(
            f"history cleanup proposal schema mismatch: path={proposal_path.resolve()}"
        )
    receipt_path = root / "receipts" / f"{proposal_id}.json"
    if receipt_path.exists():
        raise StaticPrintError(
            f"history cleanup proposal was already executed: {receipt_path.resolve()}"
        )
    proposed = {
        str(item.get("design_id")): item
        for item in proposal.get("designs", [])
        if isinstance(item, dict)
    }
    unknown = sorted(set(selected) - set(proposed))
    if unknown:
        raise StaticPrintError(
            f"selected design is not in cleanup proposal {proposal_id!r}: {unknown}"
        )

    # Rebuild eligibility immediately before deletion. A new local candidate or
    # publication turns the reviewed proposal stale and blocks the whole action.
    current = _build_history_cleanup_proposal(
        work_root=work_root,
        authoring_root=authoring_root,
        release_references=release_references,
        proposal_id=proposal_id,
    )
    current_by_design = {item["design_id"]: item for item in current["designs"]}
    targets: list[tuple[str, dict[str, Any], Path]] = []
    authoring = authoring_root.resolve()
    for design_id in selected:
        before = proposed[design_id]
        now = current_by_design.get(design_id)
        if now is None or now.get("versions") != before.get("versions"):
            raise StaticPrintError(
                "history cleanup proposal became stale; generate and review a new "
                f"proposal: design_id={design_id!r}"
            )
        for version in before["versions"]:
            relative = Path(str(version["relative_path"]))
            path = (authoring / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or authoring not in path.parents
            ):
                raise StaticPrintError(
                    f"history cleanup path is unsafe: design_id={design_id!r}; path={path}"
                )
            if not path.is_dir() or _tree_inventory(path) != {
                "sha256": version["sha256"],
                "bytes": version["bytes"],
                "files": version["files"],
            }:
                raise StaticPrintError(
                    "history changed after proposal review; generate a new proposal: "
                    f"design_id={design_id!r}; path={path}"
                )
            targets.append((design_id, version, path))

    receipt = {
        "schema": HISTORY_CLEANUP_SCHEMA,
        "proposal_id": proposal_id,
        "status": "running",
        "started_at": utc_now(),
        "completed_at": None,
        "selected_design_ids": selected,
        "current_target": None,
        "removed_version_count": 0,
        "reclaimed_bytes": 0,
        "removed_versions": [],
        "error": None,
    }
    write_json(receipt_path, receipt, exclusive=True)
    try:
        for design_id, version, path in targets:
            receipt["current_target"] = version["relative_path"]
            write_json(receipt_path, receipt)
            shutil.rmtree(path)
            removed = {
                "design_id": design_id,
                "product_profile_id": version["product_profile_id"],
                "version_id": version["version_id"],
                "relative_path": version["relative_path"],
                "bytes": version["bytes"],
                "sha256": version["sha256"],
            }
            receipt["removed_versions"].append(removed)
            receipt["removed_version_count"] += 1
            receipt["reclaimed_bytes"] += version["bytes"]
            receipt["current_target"] = None
            write_json(receipt_path, receipt)
            parent = path.parent
            while parent != authoring and authoring in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        write_json(receipt_path, receipt)
        raise
    receipt["status"] = "complete"
    receipt["completed_at"] = utc_now()
    write_json(receipt_path, receipt)
    return receipt
