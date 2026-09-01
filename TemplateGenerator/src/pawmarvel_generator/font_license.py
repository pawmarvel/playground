from __future__ import annotations

from pathlib import Path


OFL_MARKER = "SIL OPEN FONT LICENSE VERSION 1.1"
LICENSE_CANDIDATES = ("OFL.txt", "LICENSE.txt", "LICENSE", "license.txt")


class FontLicenseError(ValueError):
    """A font is missing the redistributable OFL license required by bundles."""


def resolve_ofl_license(font: Path, explicit: Path | None = None) -> Path:
    font = font.expanduser().resolve()
    if not font.is_file():
        raise FontLicenseError(f"font does not exist: {font}")
    if font.suffix.lower() != ".ttf":
        raise FontLicenseError("production bundle fonts must use the .ttf suffix")

    if explicit is not None:
        candidates = [explicit.expanduser().resolve()]
    else:
        candidates = [font.parent / name for name in LICENSE_CANDIDATES]
    license_path = next((path for path in candidates if path.is_file()), None)
    if license_path is None:
        raise FontLicenseError(
            f"font requires an OFL license file; place OFL.txt next to {font.name} "
            "or pass --font-license"
        )
    try:
        contents = license_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FontLicenseError(f"font license must be UTF-8 text: {license_path}") from exc
    normalized = " ".join(contents.upper().split())
    if OFL_MARKER not in normalized:
        raise FontLicenseError(
            f"font license is not recognized as SIL Open Font License 1.1: {license_path}"
        )
    return license_path
