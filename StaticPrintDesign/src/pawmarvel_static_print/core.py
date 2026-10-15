from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_PATTERN = re.compile(r"^v[0-9]{6}$")
RELEASE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]{3}$")
SUBJECT_SEPARATOR = "--subject-"


class StaticPrintError(ValueError):
    """User-correctable static print workflow error."""


@dataclass(frozen=True)
class PrintTarget:
    target_id: str
    width: int
    height: int
    dpi: int
    color_space: str
    background: str
    physical_width_inches: float | None = None
    physical_height_inches: float | None = None


@dataclass(frozen=True)
class ProductProfile:
    profile_id: str
    preview_width: int
    preview_height: int
    targets: tuple[PrintTarget, ...]
    path: Path


@dataclass(frozen=True)
class DesignIdentity:
    seed_design_id: str
    subject_id: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.seed_design_id, "seed design ID")
        if self.subject_id is not None:
            validate_id(self.subject_id, "subject ID")

    @property
    def design_id(self) -> str:
        if self.subject_id is None:
            return self.seed_design_id
        return f"{self.seed_design_id}{SUBJECT_SEPARATOR}{self.subject_id}"

    @property
    def relative_directory(self) -> Path:
        if self.subject_id is None:
            return Path(self.seed_design_id)
        return Path(self.seed_design_id) / self.subject_id


def validate_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise StaticPrintError(
            f"{label} must be lowercase kebab-case; actual={value!r}"
        )
    return value


def design_identity(design_id: str) -> DesignIdentity:
    if not isinstance(design_id, str) or not design_id:
        raise StaticPrintError(
            f"design ID must be a non-empty string; actual={design_id!r}"
        )
    if SUBJECT_SEPARATOR not in design_id:
        return DesignIdentity(seed_design_id=validate_id(design_id, "seed design ID"))
    if design_id.count(SUBJECT_SEPARATOR) != 1:
        raise StaticPrintError(
            "subject design ID must be "
            "<seed-design-id>--subject-<subject-id>; "
            f"actual={design_id!r}"
        )
    seed_design_id, subject_id = design_id.split(SUBJECT_SEPARATOR, 1)
    return DesignIdentity(
        seed_design_id=validate_id(seed_design_id, "seed design ID"),
        subject_id=validate_id(subject_id, "subject ID"),
    )


def validate_design_id(value: str, label: str = "design ID") -> str:
    try:
        return design_identity(value).design_id
    except StaticPrintError as exc:
        raise StaticPrintError(
            f"{label} must be a seed ID or canonical subject design ID "
            f"(<seed-design-id>--subject-<subject-id>); actual={value!r}"
        ) from exc


def bundle_id_for(design_id: str, product_profile_id: str) -> str:
    identity = design_identity(design_id)
    if identity.subject_id is None:
        raise StaticPrintError(
            f"static product bundle requires a subject design ID; actual={design_id!r}"
        )
    profile_id = validate_id(product_profile_id, "product profile ID")
    return f"{identity.design_id}--{profile_id}"


def resolve_design_identity(inputs_root: Path, design_id: str) -> DesignIdentity:
    del inputs_root
    return design_identity(design_id)


def design_source_path(inputs_root: Path, design_id: str) -> Path:
    identity = resolve_design_identity(inputs_root, design_id)
    return inputs_root / identity.relative_directory / "source-design.png"


def design_authoring_path(
    authoring_root: Path, design_id: str, *, inputs_root: Path | None = None
) -> Path:
    identity = (
        resolve_design_identity(inputs_root, design_id)
        if inputs_root is not None
        else design_identity(design_id)
    )
    return authoring_root / identity.relative_directory


def validate_version(value: str) -> str:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise StaticPrintError(
            f"version ID must match vNNNNNN, for example v000001; actual={value!r}"
        )
    return value


def validate_release_id(value: str) -> str:
    if not isinstance(value, str) or not RELEASE_PATTERN.fullmatch(value):
        raise StaticPrintError(
            "release ID must match YYYY-MM-DD.NNN, for example 2026-10-20.001; "
            f"actual={value!r}"
        )
    return value


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def product_profile_sha256(profile: ProductProfile) -> str:
    """Hash the semantic profile contract, independent of JSON formatting."""

    value = {
        "product_profile_id": profile.profile_id,
        "preview": {
            "width": profile.preview_width,
            "height": profile.preview_height,
        },
        "print_targets": [
            {
                "target_id": target.target_id,
                "width": target.width,
                "height": target.height,
                "dpi": target.dpi,
                "color_space": target.color_space,
                "background": target.background,
                **(
                    {"physical_width_inches": target.physical_width_inches}
                    if target.physical_width_inches is not None
                    else {}
                ),
                **(
                    {"physical_height_inches": target.physical_height_inches}
                    if target.physical_height_inches is not None
                    else {}
                ),
            }
            for target in profile.targets
        ],
    }
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise StaticPrintError(f"JSON file does not exist: {resolved}")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StaticPrintError(
            f"JSON file is not readable: {resolved}; error={exc}"
        ) from exc
    if not isinstance(value, dict):
        raise StaticPrintError(f"JSON root must be an object: {resolved}")
    return value


def write_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            try:
                os.link(temporary_path, path)
            except FileExistsError as exc:
                raise StaticPrintError(
                    f"immutable metadata already exists: {path.resolve()}"
                ) from exc
        else:
            os.replace(temporary_path, path)
        try:
            parent_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        except OSError:
            # Some filesystems do not permit directory fsync. The file itself is
            # still fully synced and atomically installed.
            pass
    finally:
        temporary_path.unlink(missing_ok=True)


def image_info(path: Path) -> tuple[int, int, str]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise StaticPrintError(f"image does not exist: {resolved}")
    try:
        with Image.open(resolved) as image:
            image.load()
            return image.width, image.height, image.mode
    except (OSError, ValueError) as exc:
        raise StaticPrintError(
            f"image is not readable: {resolved}; error={exc}"
        ) from exc


def require_png(path: Path, label: str) -> tuple[int, int, str]:
    if path.suffix.lower() != ".png":
        raise StaticPrintError(f"{label} must be a PNG: {path.resolve()}")
    width, height, mode = image_info(path)
    return width, height, mode


def load_profile(path: Path) -> ProductProfile:
    resolved = path.expanduser().resolve()
    value = read_json(resolved)
    if value.get("schema") != "pawmarvel-static-product-profile-v1":
        raise StaticPrintError(
            f"unsupported product profile schema: {resolved}; "
            f"actual={value.get('schema')!r}"
        )
    profile_id = validate_id(value.get("product_profile_id"), "product profile ID")
    raw_preview = value.get("preview")
    if not isinstance(raw_preview, dict) or set(raw_preview) != {"width", "height"}:
        raise StaticPrintError(
            f"product profile preview must contain exactly width and height: {resolved}"
        )
    preview_width = raw_preview.get("width")
    preview_height = raw_preview.get("height")
    if not all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0
        for item in (preview_width, preview_height)
    ):
        raise StaticPrintError(
            f"product profile preview width and height must be positive integers: {resolved}"
        )
    raw_targets = value.get("print_targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise StaticPrintError(f"product profile must define print_targets: {resolved}")
    targets: list[PrintTarget] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, dict):
            raise StaticPrintError(
                f"print target {index} must be an object: {resolved}"
            )
        target_id = validate_id(raw.get("target_id"), f"print target {index} ID")
        if target_id in seen:
            raise StaticPrintError(
                f"duplicate print target ID {target_id!r}: {resolved}"
            )
        seen.add(target_id)
        width = raw.get("width")
        height = raw.get("height")
        dpi = raw.get("dpi")
        if not all(
            isinstance(item, int) and not isinstance(item, bool) and item > 0
            for item in (width, height, dpi)
        ):
            raise StaticPrintError(
                f"print target {target_id!r} width, height, and dpi must be positive integers: {resolved}"
            )
        color_space = raw.get("color_space")
        if color_space != "sRGB":
            raise StaticPrintError(
                f"print target {target_id!r} supports only sRGB; actual={color_space!r}"
            )
        background = raw.get("background")
        if not isinstance(background, str) or not (
            background == "transparent" or re.fullmatch(r"#[0-9A-Fa-f]{6}", background)
        ):
            raise StaticPrintError(
                f"print target {target_id!r} background must be transparent or #RRGGBB"
            )
        physical_width = raw.get("physical_width_inches")
        physical_height = raw.get("physical_height_inches")
        for label, item in (
            ("physical_width_inches", physical_width),
            ("physical_height_inches", physical_height),
        ):
            if item is not None and (not isinstance(item, (int, float)) or item <= 0):
                raise StaticPrintError(
                    f"print target {target_id!r} {label} must be positive when present"
                )
        targets.append(
            PrintTarget(
                target_id=target_id,
                width=width,
                height=height,
                dpi=dpi,
                color_space=color_space,
                background=background,
                physical_width_inches=(
                    float(physical_width) if physical_width else None
                ),
                physical_height_inches=(
                    float(physical_height) if physical_height else None
                ),
            )
        )
    return ProductProfile(
        profile_id=profile_id,
        preview_width=preview_width,
        preview_height=preview_height,
        targets=tuple(targets),
        path=resolved,
    )


def discover_profiles(root: Path) -> dict[str, ProductProfile]:
    profiles: dict[str, ProductProfile] = {}
    if not root.is_dir():
        return profiles
    for path in sorted(root.glob("*.json")):
        profile = load_profile(path)
        if profile.profile_id in profiles:
            raise StaticPrintError(
                f"duplicate product profile ID {profile.profile_id!r}: "
                f"{profiles[profile.profile_id].path} and {path.resolve()}"
            )
        profiles[profile.profile_id] = profile
    return profiles


def clean_macos_metadata(root: Path) -> list[Path]:
    removed: list[Path] = []
    if not root.exists():
        return removed
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and (path.name == ".DS_Store" or path.name.startswith("._")):
            path.unlink()
            removed.append(path)
    return removed
