"""Search and download OFL font families from the official Google Fonts repo."""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .font_catalog import FontCandidate, discover_font_catalog


GOOGLE_FONTS_API = "https://api.github.com/repos/google/fonts"
GOOGLE_FONTS_RAW_PREFIX = "https://raw.githubusercontent.com/google/fonts/"
MAX_REMOTE_FILE_BYTES = 12 * 1024 * 1024
MAX_FAMILY_BYTES = 32 * 1024 * 1024
MAX_FAMILY_FONTS = 16
REMOTE_REQUEST_ATTEMPTS = 2
REMOTE_RETRY_SECONDS = 0.25
_FAMILY_ID = re.compile(r"[a-z0-9]{2,80}")
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9._,\[\]-]{1,160}")


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


@lru_cache(maxsize=1)
def _font_aliases() -> dict[str, tuple[RemoteFontFamily, ...]]:
    path = (
        Path(__file__).resolve().parents[2]
        / "assets"
        / "fonts"
        / "remote-font-aliases.json"
    )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        aliases = value["aliases"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RemoteFontError(
            f"remote font alias catalog is invalid: {path}: {exc}"
        ) from exc
    if value.get("schema_version") != 1 or not isinstance(aliases, dict):
        raise RemoteFontError(f"remote font alias catalog must use schema_version 1: {path}")
    result: dict[str, tuple[RemoteFontFamily, ...]] = {}
    for raw_alias, raw_families in aliases.items():
        alias = normalize_google_font_family(str(raw_alias))
        if (
            alias != raw_alias
            or not isinstance(raw_families, list)
            or not raw_families
        ):
            raise RemoteFontError(
                f"invalid remote font alias entry {raw_alias!r}: {path}"
            )
        families: list[RemoteFontFamily] = []
        for raw_family in raw_families:
            if not isinstance(raw_family, dict):
                raise RemoteFontError(
                    f"invalid remote font alias family for {raw_alias!r}: {path}"
                )
            family_id = normalize_google_font_family(
                str(raw_family.get("family_id", ""))
            )
            label = raw_family.get("label")
            if not isinstance(label, str) or not label.strip():
                raise RemoteFontError(
                    f"remote font alias family has no label for {raw_alias!r}: {path}"
                )
            families.append(
                RemoteFontFamily(family_id=family_id, label=label.strip())
            )
        result[alias] = tuple(families)
    return result


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
    raw = b""
    for attempt in range(REMOTE_REQUEST_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(MAX_REMOTE_FILE_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise RemoteFontError("font family was not found in Google Fonts OFL") from exc
            if exc.code == 403:
                raise RemoteFontError(
                    "Google Fonts search was rate-limited; retry later or set GITHUB_TOKEN"
                ) from exc
            if (
                exc.code not in {429, 500, 502, 503, 504}
                or attempt + 1 == REMOTE_REQUEST_ATTEMPTS
            ):
                raise RemoteFontError(
                    f"Google Fonts request failed with HTTP {exc.code}: {url}"
                ) from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
        ) as exc:
            if attempt + 1 == REMOTE_REQUEST_ATTEMPTS:
                raise RemoteFontError(
                    f"Google Fonts request failed after {REMOTE_REQUEST_ATTEMPTS} attempts: "
                    f"url={url}; cause={exc}"
                ) from exc
        time.sleep(REMOTE_RETRY_SECONDS * (2**attempt))
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
    content = b""
    for attempt in range(REMOTE_REQUEST_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                content = response.read(MAX_REMOTE_FILE_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if (
                exc.code not in {429, 500, 502, 503, 504}
                or attempt + 1 == REMOTE_REQUEST_ATTEMPTS
            ):
                raise RemoteFontError(
                    f"font download failed with HTTP {exc.code}: {url}"
                ) from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
        ) as exc:
            if attempt + 1 == REMOTE_REQUEST_ATTEMPTS:
                raise RemoteFontError(
                    f"font download failed after {REMOTE_REQUEST_ATTEMPTS} attempts: "
                    f"url={url}; cause={exc}"
                ) from exc
        time.sleep(REMOTE_RETRY_SECONDS * (2**attempt))
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


def search_google_ofl(query: str, *, limit: int = 8) -> tuple[RemoteFontFamily, ...]:
    """Return verified exact or curated-alias OFL families without a global tree fetch."""
    family_id = normalize_google_font_family(query)
    aliases = _font_aliases().get(family_id)
    if aliases:
        verified = []
        for family in aliases[: max(1, min(limit, 12))]:
            try:
                _family_listing(family.family_id)
            except RemoteFontError as exc:
                if "not found" in str(exc):
                    continue
                raise
            verified.append(family)
        return tuple(verified)
    try:
        _family_listing(family_id)
    except RemoteFontError as exact_error:
        if "not found" not in str(exact_error):
            raise
    else:
        return (RemoteFontFamily(family_id=family_id, label=query.strip()),)
    return ()


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
