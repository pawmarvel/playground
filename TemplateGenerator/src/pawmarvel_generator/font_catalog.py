"""Discover and validate the local OFL font catalog used during authoring."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import ImageFont

from .font_license import FontLicenseError, resolve_ofl_license


class FontCatalogError(ValueError):
    """A configured catalog is unsafe, ambiguous, or cannot be rendered."""


def default_local_font_catalog() -> Path:
    """Return the repository's curated OFL catalog used by the MVP tools."""
    catalog = Path(__file__).resolve().parents[2] / "assets" / "fonts"
    if not catalog.is_dir():
        raise FontCatalogError(
            "default local font catalog is unavailable; pass --font-catalog"
        )
    return catalog


@dataclass(frozen=True)
class FontCandidate:
    candidate_id: str
    label: str
    font: Path
    license: Path
    sha256: str

    @property
    def relative_name(self) -> str:
        return f"fonts/{self.font.name}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _font_label(path: Path) -> str:
    try:
        family, style = ImageFont.truetype(str(path), size=16).getname()
    except OSError as exc:
        raise FontCatalogError(f"font cannot be rendered: {path}: {exc}") from exc
    family = family.strip() or path.stem
    style = style.strip()
    return family if not style or style.lower() == "regular" else f"{family} {style}"


def discover_font_catalog(
    primary_font: Path | None,
    primary_license: Path | None = None,
    *,
    catalog_roots: Iterable[Path] = (),
    additional_fonts: Iterable[Path] = (),
) -> tuple[FontCandidate, ...]:
    """Return validated candidates with the explicit primary font first.

    Every candidate must be a renderable TTF with a recognized sibling OFL
    license. Catalogs are strict: one invalid TTF rejects the authoring run
    rather than silently presenting an unapproved font.
    """

    primary = primary_font.expanduser().resolve() if primary_font else None
    paths = [primary] if primary is not None else []
    for font in additional_fonts:
        candidate = font.expanduser().resolve()
        if candidate.is_file():
            paths.append(candidate)
    for root_value in catalog_roots:
        root = root_value.expanduser().resolve()
        if not root.is_dir():
            raise FontCatalogError(f"font catalog is not a directory: {root}")
        paths.extend(sorted(root.rglob("*.ttf"), key=lambda path: path.as_posix().lower()))

    candidates: list[FontCandidate] = []
    seen_hashes: set[str] = set()
    output_names: dict[str, str] = {}
    for path in paths:
        path = path.expanduser().resolve()
        if not path.is_file():
            raise FontCatalogError(f"font does not exist: {path}")
        try:
            license_path = resolve_ofl_license(
                path, primary_license if primary is not None and path == primary else None
            )
        except FontLicenseError as exc:
            raise FontCatalogError(str(exc)) from exc
        digest = _sha256(path)
        if digest in seen_hashes:
            continue
        collision = output_names.get(path.name)
        if collision is not None and collision != digest:
            raise FontCatalogError(
                "font catalog contains different files with the same output name: "
                f"{path.name}"
            )
        output_names[path.name] = digest
        seen_hashes.add(digest)
        candidates.append(
            FontCandidate(
                candidate_id=f"font-{digest[:16]}",
                label=_font_label(path),
                font=path,
                license=license_path,
                sha256=digest,
            )
        )

    if not candidates:
        raise FontCatalogError("font catalog contains no TTF fonts")
    labels: dict[str, int] = {}
    for candidate in candidates:
        labels[candidate.label] = labels.get(candidate.label, 0) + 1
    return tuple(
        FontCandidate(
            candidate_id=candidate.candidate_id,
            label=(
                f"{candidate.label} ({candidate.font.name})"
                if labels[candidate.label] > 1
                else candidate.label
            ),
            font=candidate.font,
            license=candidate.license,
            sha256=candidate.sha256,
        )
        for candidate in candidates
    )
