"""Search and download OFL font families from the official Google Fonts repo."""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .font_catalog import FontCandidate, discover_font_catalog


GOOGLE_FONTS_API = "https://api.github.com/repos/google/fonts"
GOOGLE_FONTS_RAW_PREFIX = "https://raw.githubusercontent.com/google/fonts/"
MAX_REMOTE_FILE_BYTES = 12 * 1024 * 1024
MAX_FAMILY_BYTES = 32 * 1024 * 1024
MAX_FAMILY_FONTS = 16
_FAMILY_ID = re.compile(r"[a-z0-9]{2,80}")
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9._,\[\]-]{1,160}")
_tree_family_ids: tuple[str, ...] | None = None


class RemoteFontError(ValueError):
    """A remote font search or import could not be completed safely."""


@dataclass(frozen=True)
class RemoteFontFamily:
    family_id: str
    label: str
    source: str = "google-fonts-ofl"

    def to_dict(self) -> dict[str, str]:
        return {
            "family_id": self.family_id,
            "label": self.label,
            "source": self.source,
        }


@dataclass(frozen=True)
class ImportedFontFamily:
    family: RemoteFontFamily
    candidates: tuple[FontCandidate, ...]
    license: Path
    metadata: Path
    source_url: str


def normalize_google_font_family(value: str) -> str:
    normalized = "".join(
        character for character in value.lower() if character.isalnum()
    )
    if _FAMILY_ID.fullmatch(normalized) is None:
        raise RemoteFontError(
            "font family must contain 2-80 letters or numbers, for example 'Amatic SC'"
        )
    return normalized


def _request_json(url: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "pawmarvel-template-generator/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_REMOTE_FILE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RemoteFontError("font family was not found in Google Fonts OFL") from exc
        if exc.code == 403:
            raise RemoteFontError(
                "Google Fonts search was rate-limited; retry later or set GITHUB_TOKEN"
            ) from exc
        raise RemoteFontError(
            f"Google Fonts request failed with HTTP {exc.code}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RemoteFontError(f"Google Fonts request failed: {exc}") from exc
    if len(raw) > MAX_REMOTE_FILE_BYTES:
        raise RemoteFontError("Google Fonts response exceeded the safety limit")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteFontError("Google Fonts returned invalid metadata") from exc


def _download(url: str) -> bytes:
    if not url.startswith(GOOGLE_FONTS_RAW_PREFIX):
        raise RemoteFontError(f"refusing an untrusted font download URL: {url}")
    request = urllib.request.Request(
        url, headers={"User-Agent": "pawmarvel-template-generator/0.1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read(MAX_REMOTE_FILE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RemoteFontError(f"font download failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RemoteFontError(f"font download failed: {exc}") from exc
    if len(content) > MAX_REMOTE_FILE_BYTES:
        raise RemoteFontError("downloaded font artifact exceeded the safety limit")
    return content


def _family_listing(family_id: str) -> list[dict[str, Any]]:
    value = _request_json(f"{GOOGLE_FONTS_API}/contents/ofl/{family_id}")
    if not isinstance(value, list):
        raise RemoteFontError(
            "Google Fonts family metadata was not a directory listing"
        )
    return [item for item in value if isinstance(item, dict)]


def _all_family_ids() -> tuple[str, ...]:
    global _tree_family_ids
    if _tree_family_ids is not None:
        return _tree_family_ids
    value = _request_json(f"{GOOGLE_FONTS_API}/git/trees/main?recursive=1")
    tree = value.get("tree") if isinstance(value, dict) else None
    if not isinstance(tree, list) or value.get("truncated") is True:
        raise RemoteFontError("Google Fonts family index was incomplete")
    found = {
        match.group(1)
        for item in tree
        if isinstance(item, dict) and isinstance(item.get("path"), str)
        if (match := re.fullmatch(r"ofl/([a-z0-9]+)/METADATA\.pb", item["path"]))
    }
    _tree_family_ids = tuple(sorted(found))
    return _tree_family_ids


def search_google_ofl(query: str, *, limit: int = 8) -> tuple[RemoteFontFamily, ...]:
    """Return exact or fuzzy OFL family matches without downloading font binaries."""
    family_id = normalize_google_font_family(query)
    try:
        _family_listing(family_id)
    except RemoteFontError as exact_error:
        if "not found" not in str(exact_error):
            raise
    else:
        return (RemoteFontFamily(family_id=family_id, label=query.strip()),)

    scored = []
    for candidate in _all_family_ids():
        score = difflib.SequenceMatcher(None, family_id, candidate).ratio()
        if family_id in candidate or candidate in family_id or score >= 0.58:
            scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(
        RemoteFontFamily(family_id=candidate, label=candidate)
        for _, candidate in scored[: max(1, min(limit, 12))]
    )


def import_google_ofl_family(family_id: str, destination: Path) -> ImportedFontFamily:
    """Download and validate one Google Fonts OFL family into a session directory."""
    family_id = normalize_google_font_family(family_id)
    listing = _family_listing(family_id)
    by_name = {
        item.get("name"): item
        for item in listing
        if isinstance(item.get("name"), str)
        and _SAFE_FILENAME.fullmatch(item["name"])
    }
    license_item = by_name.get("OFL.txt")
    metadata_item = by_name.get("METADATA.pb")
    font_items = [
        item for name, item in by_name.items() if name.lower().endswith(".ttf")
    ]
    if not isinstance(license_item, dict) or not isinstance(metadata_item, dict):
        raise RemoteFontError("Google Fonts family is missing OFL.txt or METADATA.pb")
    if not font_items:
        raise RemoteFontError("Google Fonts family contains no TTF files")
    if len(font_items) > MAX_FAMILY_FONTS:
        raise RemoteFontError(
            f"Google Fonts family has too many TTF files ({len(font_items)}); limit={MAX_FAMILY_FONTS}"
        )

    root = destination.expanduser().resolve()
    family_dir = root / family_id
    partial = root / f".{family_id}.partial"
    if family_dir.exists() or partial.exists():
        raise RemoteFontError("font family already exists in the session cache")
    partial.mkdir(parents=True)
    downloaded_names: dict[str, str] = {}
    try:
        total = 0
        for item in [license_item, metadata_item, *font_items]:
            name = str(item["name"])
            url = item.get("download_url")
            if not isinstance(url, str):
                raise RemoteFontError(
                    f"Google Fonts artifact has no download URL: {name}"
                )
            content = _download(url)
            total += len(content)
            if total > MAX_FAMILY_BYTES:
                raise RemoteFontError(
                    "Google Fonts family exceeded the download safety limit"
                )
            target_name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
            (partial / target_name).write_bytes(content)
            downloaded_names[name] = target_name

        metadata_text = (partial / downloaded_names["METADATA.pb"]).read_text(
            encoding="utf-8", errors="replace"
        )
        if not re.search(r'^license:\s*"OFL"\s*$', metadata_text, re.MULTILINE):
            raise RemoteFontError(
                "Google Fonts metadata does not declare the OFL license"
            )
        name_match = re.search(
            r'^name:\s*"([^"]+)"\s*$', metadata_text, re.MULTILINE
        )
        label = name_match.group(1) if name_match else family_id
        partial.replace(family_dir)
        candidates = discover_font_catalog(None, catalog_roots=(family_dir,))
        metadata = family_dir / downloaded_names["METADATA.pb"]
        return ImportedFontFamily(
            family=RemoteFontFamily(family_id=family_id, label=label),
            candidates=candidates,
            license=family_dir / downloaded_names["OFL.txt"],
            metadata=metadata,
            source_url=f"https://github.com/google/fonts/tree/main/ofl/{family_id}",
        )
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        shutil.rmtree(family_dir, ignore_errors=True)
        raise
