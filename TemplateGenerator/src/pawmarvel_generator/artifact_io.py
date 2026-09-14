"""Small, dependency-free helpers for immutable artifact metadata and writes."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar


ErrorT = TypeVar("ErrorT", bound=Exception)


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mismatch(label: str, *, expected: object, actual: object) -> str:
    """Return a consistent, actionable validation error without hiding values."""
    return f"{label} mismatch; expected={expected!r}; actual={actual!r}"


def read_json(
    path: Path,
    *,
    label: str,
    error_type: type[ErrorT],
    require_object: bool = False,
    correction: str | None = None,
) -> Any:
    """Read JSON with stable, path-rich diagnostics for command-line callers."""
    resolved = path.expanduser().resolve()
    hint = f" Correction: {correction}" if correction else ""
    if not resolved.exists():
        raise error_type(f"{label} does not exist: {resolved}.{hint}")
    if not resolved.is_file():
        raise error_type(f"{label} is not a regular file: {resolved}.{hint}")
    try:
        contents = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise error_type(
            f"{label} is not valid UTF-8 JSON: {resolved}; byte={exc.start}.{hint}"
        ) from exc
    except OSError as exc:
        raise error_type(
            f"cannot read {label}: {resolved}; error={exc.strerror or exc}.{hint}"
        ) from exc
    try:
        value = json.loads(contents)
    except json.JSONDecodeError as exc:
        raise error_type(
            f"{label} contains malformed JSON: {resolved}; line={exc.lineno}; "
            f"column={exc.colno}; error={exc.msg}.{hint}"
        ) from exc
    if require_object and not isinstance(value, dict):
        raise error_type(f"{label} must contain a JSON object: {resolved}.{hint}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(value, target, indent=2)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
