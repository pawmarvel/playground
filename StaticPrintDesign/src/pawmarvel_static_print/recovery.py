"""Crash recovery for locally generated static-print work units."""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from .core import ProductProfile, read_json

_PARTIAL = re.compile(r"^\..+\.partial-(?P<operation>[a-z0-9][a-z0-9._-]*)$")
_BACKUP = re.compile(
    r"^\.(?P<subject>[a-z0-9][a-z0-9._-]*)\.backup-(?P<operation>[a-z0-9][a-z0-9._-]*)$"
)
_VERSION = re.compile(r"^v[0-9]{6}$")


def _remove_tree(path: Path, removed: list[str]) -> None:
    shutil.rmtree(path)
    removed.append(str(path.resolve()))


def _png_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def _validate_generation(directory: Path, profiles: dict[str, ProductProfile]) -> None:
    record = read_json(directory / "generation.json")
    if record.get("schema") != "pawmarvel-static-generation-v3":
        raise ValueError(f"unsupported generation schema {record.get('schema')!r}")
    if record.get("version_id") != directory.name:
        raise ValueError("version ID does not match directory")
    profile_id = str(record.get("product_profile_id", ""))
    profile = profiles.get(profile_id)
    if profile is None:
        raise ValueError(f"unknown product profile {profile_id!r}")
    design_id = str(record.get("design_id", ""))
    preview = directory / f"{design_id}-preview.png"
    _png_size(preview)
    profile_preview = directory / "profile-previews" / f"{profile_id}.png"
    if _png_size(profile_preview) != (profile.preview_width, profile.preview_height):
        raise ValueError("profile preview dimensions do not match product profile")
    multiple = len(profile.targets) > 1
    for target in profile.targets:
        filename = (
            f"{design_id}--{profile_id}--{target.target_id}.png"
            if multiple
            else f"{design_id}--{profile_id}.png"
        )
        if _png_size(directory / filename) != (target.width, target.height):
            raise ValueError(
                f"print dimensions do not match target {target.target_id!r}"
            )


def reconcile_workspace(
    *,
    work_root: Path,
    authoring_root: Path,
    graduated_root: Path,
    profiles: dict[str, ProductProfile],
) -> dict[str, Any]:
    """Remove staging debris, restore interrupted replacements, quarantine bad commits."""
    removed: list[str] = []
    restored: list[str] = []
    quarantined: list[dict[str, str]] = []

    for root in (authoring_root, graduated_root):
        if not root.is_dir():
            continue
        partials = sorted(
            (path for path in root.rglob(".*.partial-*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for path in partials:
            if _PARTIAL.match(path.name):
                _remove_tree(path, removed)

    if graduated_root.is_dir():
        backups = sorted(
            (path for path in graduated_root.rglob(".*.backup-*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for backup in backups:
            match = _BACKUP.match(backup.name)
            if match is None:
                continue
            destination = backup.parent / match.group("subject")
            if destination.exists():
                _remove_tree(backup, removed)
            else:
                os.replace(backup, destination)
                restored.append(str(destination.resolve()))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    quarantine_root = work_root / "recovery-quarantine" / timestamp / "static-authoring"
    if authoring_root.is_dir():
        candidates = sorted(
            path
            for path in authoring_root.rglob("v[0-9][0-9][0-9][0-9][0-9][0-9]")
            if path.is_dir() and _VERSION.match(path.name)
        )
        for candidate in candidates:
            try:
                _validate_generation(candidate, profiles)
            # A malformed or partially written candidate must be quarantined even
            # when its decoder raises an unexpected library exception.
            except Exception as exc:  # noqa: BLE001
                relative = candidate.relative_to(authoring_root)
                destination = quarantine_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(candidate, destination)
                quarantined.append(
                    {
                        "source": str(candidate.resolve()),
                        "destination": str(destination.resolve()),
                        "reason": str(exc),
                    }
                )
    return {
        "removed_staging": removed,
        "restored_backups": restored,
        "quarantined": quarantined,
    }


def cleanup_operation_staging(
    *, authoring_root: Path, graduated_root: Path, operation_id: str
) -> dict[str, list[str]]:
    removed: list[str] = []
    restored: list[str] = []
    for root in (authoring_root, graduated_root):
        if not root.is_dir():
            continue
        for path in sorted(
            (
                item
                for item in root.rglob(f".*.partial-{operation_id}")
                if item.is_dir()
            ),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            _remove_tree(path, removed)
    if graduated_root.is_dir():
        for backup in sorted(
            (
                item
                for item in graduated_root.rglob(f".*.backup-{operation_id}")
                if item.is_dir()
            ),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            match = _BACKUP.match(backup.name)
            if match is None:
                continue
            destination = backup.parent / match.group("subject")
            if destination.exists():
                _remove_tree(backup, removed)
            else:
                os.replace(backup, destination)
                restored.append(str(destination.resolve()))
    return {"removed": removed, "restored": restored}
