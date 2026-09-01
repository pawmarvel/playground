"""Materialize a pinned remote OFL font shortlist into a validated local cache."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


class ExpandedFontCatalogError(ValueError):
    """The expanded index or a downloaded artifact failed validation."""


@dataclass(frozen=True)
class RemoteFont:
    family: str
    style: str
    filename: str
    font_url: str
    license_url: str
    font_sha256: str
    license_sha256: str
    priority: int


Downloader = Callable[[str], bytes]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(url: str) -> bytes:
    if urlparse(url).scheme != "https":
        raise ExpandedFontCatalogError("expanded font URLs must use HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "PawMarvel/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read(20 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ExpandedFontCatalogError(f"font download failed: {url}: {exc}") from exc


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExpandedFontCatalogError(f"{label} must be a non-empty string")
    return value.strip()


def load_expanded_index(path: Path) -> tuple[str, tuple[RemoteFont, ...]]:
    resolved = path.expanduser().resolve()
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpandedFontCatalogError(f"cannot read expanded font index: {resolved}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ExpandedFontCatalogError("expanded font index schema_version must be 1")
    revision = _required_string(raw.get("source_revision"), "source_revision")
    entries = raw.get("fonts")
    if not isinstance(entries, list) or not entries:
        raise ExpandedFontCatalogError("expanded font index must contain fonts")
    result: list[RemoteFont] = []
    seen: set[tuple[str, str]] = set()
    for number, value in enumerate(entries):
        label = f"fonts[{number}]"
        if not isinstance(value, dict):
            raise ExpandedFontCatalogError(f"{label} must be an object")
        filename = _required_string(value.get("filename"), f"{label}.filename")
        if Path(filename).name != filename or not filename.lower().endswith(".ttf"):
            raise ExpandedFontCatalogError(f"{label}.filename must be a plain .ttf filename")
        font_url = _required_string(value.get("font_url"), f"{label}.font_url")
        license_url = _required_string(value.get("license_url"), f"{label}.license_url")
        if urlparse(font_url).scheme != "https" or urlparse(license_url).scheme != "https":
            raise ExpandedFontCatalogError(f"{label} URLs must use HTTPS")
        font_hash = _required_string(value.get("font_sha256"), f"{label}.font_sha256").lower()
        license_hash = _required_string(value.get("license_sha256"), f"{label}.license_sha256").lower()
        if any(len(item) != 64 or any(char not in "0123456789abcdef" for char in item) for item in (font_hash, license_hash)):
            raise ExpandedFontCatalogError(f"{label} checksums must be SHA-256 hex")
        family = _required_string(value.get("family"), f"{label}.family")
        style = _required_string(value.get("style"), f"{label}.style")
        key = (family.casefold(), style.casefold())
        if key in seen:
            raise ExpandedFontCatalogError(f"duplicate expanded font family/style: {family} {style}")
        seen.add(key)
        priority = value.get("priority", 100)
        if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
            raise ExpandedFontCatalogError(f"{label}.priority must be a non-negative integer")
        result.append(RemoteFont(family, style, filename, font_url, license_url, font_hash, license_hash, priority))
    result.sort(key=lambda item: (item.priority, item.family.casefold(), item.style.casefold()))
    return revision, tuple(result)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validated_bytes(data: bytes, expected: str, label: str) -> bytes:
    if len(data) > 20 * 1024 * 1024:
        raise ExpandedFontCatalogError(f"{label} exceeds the 20 MiB limit")
    if _sha256(data) != expected:
        raise ExpandedFontCatalogError(f"{label} checksum does not match the pinned index")
    return data


def materialize_expanded_fonts(
    index: Path,
    cache: Path,
    *,
    limit: int = 24,
    offline: bool = False,
    downloader: Downloader = _download,
) -> tuple[Path, ...]:
    """Return cached font paths for a deterministic bounded expanded shortlist."""
    if limit < 1 or limit > 100:
        raise ExpandedFontCatalogError("expanded font shortlist limit must be 1..100")
    revision, entries = load_expanded_index(index)
    cache_root = cache.expanduser().resolve() / revision
    fonts: list[Path] = []
    failures: list[str] = []
    for entry in entries[:limit]:
        family_key = hashlib.sha256(f"{entry.family}\0{entry.style}".encode()).hexdigest()[:16]
        family_dir = cache_root / family_key
        font_path = family_dir / entry.filename
        license_path = family_dir / "OFL.txt"
        valid_cache = (
            font_path.is_file()
            and license_path.is_file()
            and _sha256(font_path.read_bytes()) == entry.font_sha256
            and _sha256(license_path.read_bytes()) == entry.license_sha256
            and b"SIL OPEN FONT LICENSE Version 1.1" in license_path.read_bytes()
        )
        if not valid_cache and not offline:
            try:
                font_data = _validated_bytes(downloader(entry.font_url), entry.font_sha256, entry.filename)
                license_data = _validated_bytes(downloader(entry.license_url), entry.license_sha256, f"{entry.family} OFL.txt")
                if b"SIL OPEN FONT LICENSE Version 1.1" not in license_data:
                    raise ExpandedFontCatalogError(f"{entry.family} license is not OFL 1.1")
                _atomic_write(font_path, font_data)
                _atomic_write(license_path, license_data)
                valid_cache = True
            except ExpandedFontCatalogError as exc:
                failures.append(str(exc))
        if valid_cache:
            fonts.append(font_path)
        elif offline:
            failures.append(f"not cached: {entry.family} {entry.style}")
    if not fonts:
        detail = failures[0] if failures else "no candidates"
        raise ExpandedFontCatalogError(f"expanded font catalog unavailable ({detail})")
    return tuple(fonts)
