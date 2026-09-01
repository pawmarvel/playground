from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError


RGBA_PATTERN = re.compile(r"^#[0-9A-Fa-f]{8}$")
HORIZONTAL_ALIGNMENTS = {"left", "center", "right"}
VERTICAL_ALIGNMENTS = {"top", "middle", "bottom"}


class ConfigError(ValueError):
    """A layout or template input is invalid."""


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def to_dict(self) -> dict[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class Layout:
    template_dir: Path
    art_relative: str
    art_path: Path
    canvas_width: int
    canvas_height: int
    pet_box: Rect
    pet_rotation_degrees: float
    font_relative: str
    font_path: Path
    name_box: Rect
    font_size_px: int
    min_font_size_px: int
    color: str
    horizontal_align: str
    vertical_align: str
    runtime_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        rotation: int | float = self.pet_rotation_degrees
        if float(rotation).is_integer():
            rotation = int(rotation)
        result = {
            "schema_version": 1,
            "art": self.art_relative,
            "pet": {
                "box": self.pet_box.to_dict(),
                "rotation_degrees": rotation,
            },
            "name": {
                "box": self.name_box.to_dict(),
                "font": self.font_relative,
                "font_size_px": self.font_size_px,
                "min_font_size_px": self.min_font_size_px,
                "color": self.color.upper(),
                "horizontal_align": self.horizontal_align,
                "vertical_align": self.vertical_align,
            },
        }
        if self.runtime_model is not None:
            result["model"] = self.runtime_model
        return result


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{label} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing:
        raise ConfigError(f"{label} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ConfigError(f"{label} has unsupported fields: {', '.join(sorted(unknown))}")


def _require_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{label} must be an integer")
    return value


def _parse_rect(value: Any, label: str) -> Rect:
    data = _require_mapping(value, label)
    _require_exact_keys(data, {"x", "y", "width", "height"}, label)
    rect = Rect(
        x=_require_int(data["x"], f"{label}.x"),
        y=_require_int(data["y"], f"{label}.y"),
        width=_require_int(data["width"], f"{label}.width"),
        height=_require_int(data["height"], f"{label}.height"),
    )
    if rect.width <= 0 or rect.height <= 0:
        raise ConfigError(f"{label} width and height must be positive")
    return rect


def _resolve_inside(template_dir: Path, relative: Any, label: str) -> tuple[str, Path]:
    if not isinstance(relative, str) or not relative:
        raise ConfigError(f"{label} must be a nonempty relative path")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ConfigError(f"{label} must be relative to the template directory")
    resolved = (template_dir / candidate).resolve()
    try:
        resolved.relative_to(template_dir)
    except ValueError as exc:
        raise ConfigError(f"{label} escapes the template directory") from exc
    return candidate.as_posix(), resolved


def _validate_image(path: Path, label: str) -> tuple[int, int]:
    if not path.is_file():
        raise ConfigError(f"{label} does not exist: {path}")
    try:
        with Image.open(path) as image:
            image.load()
            if image.width <= 0 or image.height <= 0:
                raise ConfigError(f"{label} has invalid dimensions: {path}")
            return image.width, image.height
    except UnidentifiedImageError as exc:
        raise ConfigError(f"{label} is not a supported image: {path}") from exc


def _validate_rect_intersection(rect: Rect, width: int, height: int, label: str) -> None:
    if rect.right <= 0 or rect.bottom <= 0 or rect.x >= width or rect.y >= height:
        raise ConfigError(f"{label} does not intersect the art canvas")


def parse_layout(
    value: Any,
    template_dir: Path,
    *,
    art_override: Path | None = None,
    font_override: Path | None = None,
) -> Layout:
    template_dir = template_dir.expanduser().resolve()
    data = _require_mapping(value, "layout")
    required_keys = {"schema_version", "art", "pet", "name"}
    missing = required_keys - set(data)
    unknown = set(data) - required_keys - {"model"}
    if missing:
        raise ConfigError(f"layout is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ConfigError(
            f"layout has unsupported fields: {', '.join(sorted(unknown))}"
        )
    if data["schema_version"] != 1:
        raise ConfigError("schema_version must be 1")
    runtime_model = data.get("model")
    if runtime_model is not None and runtime_model != "gpt-image-2":
        raise ConfigError("model must be gpt-image-2 when present")

    art_relative, configured_art = _resolve_inside(template_dir, data["art"], "art")
    art_path = art_override.expanduser().resolve() if art_override else configured_art
    canvas_width, canvas_height = _validate_image(art_path, "art")

    pet = _require_mapping(data["pet"], "pet")
    _require_exact_keys(pet, {"box", "rotation_degrees"}, "pet")
    pet_box = _parse_rect(pet["box"], "pet.box")
    rotation = pet["rotation_degrees"]
    if isinstance(rotation, bool) or not isinstance(rotation, (int, float)):
        raise ConfigError("pet.rotation_degrees must be a number")
    if not math.isfinite(rotation):
        raise ConfigError("pet.rotation_degrees must be finite")
    if not -360 <= rotation <= 360:
        raise ConfigError("pet.rotation_degrees must be between -360 and 360")

    name = _require_mapping(data["name"], "name")
    _require_exact_keys(
        name,
        {
            "box",
            "font",
            "font_size_px",
            "min_font_size_px",
            "color",
            "horizontal_align",
            "vertical_align",
        },
        "name",
    )
    name_box = _parse_rect(name["box"], "name.box")
    font_relative, configured_font = _resolve_inside(template_dir, name["font"], "font")
    font_path = font_override.expanduser().resolve() if font_override else configured_font
    if not font_path.is_file():
        raise ConfigError(f"font does not exist: {font_path}")

    font_size = _require_int(name["font_size_px"], "name.font_size_px")
    min_font_size = _require_int(name["min_font_size_px"], "name.min_font_size_px")
    if min_font_size <= 0 or font_size <= 0 or min_font_size > font_size:
        raise ConfigError(
            "name font sizes must be positive and min_font_size_px must not exceed font_size_px"
        )
    color = name["color"]
    if not isinstance(color, str) or not RGBA_PATTERN.fullmatch(color):
        raise ConfigError("name.color must use #RRGGBBAA")
    horizontal = name["horizontal_align"]
    vertical = name["vertical_align"]
    if horizontal not in HORIZONTAL_ALIGNMENTS:
        raise ConfigError(
            f"name.horizontal_align must be one of {', '.join(sorted(HORIZONTAL_ALIGNMENTS))}"
        )
    if vertical not in VERTICAL_ALIGNMENTS:
        raise ConfigError(
            f"name.vertical_align must be one of {', '.join(sorted(VERTICAL_ALIGNMENTS))}"
        )

    _validate_rect_intersection(pet_box, canvas_width, canvas_height, "pet.box")
    _validate_rect_intersection(name_box, canvas_width, canvas_height, "name.box")

    return Layout(
        template_dir=template_dir,
        art_relative=art_relative,
        art_path=art_path,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        pet_box=pet_box,
        pet_rotation_degrees=float(rotation),
        font_relative=font_relative,
        font_path=font_path,
        name_box=name_box,
        font_size_px=font_size,
        min_font_size_px=min_font_size,
        color=color.upper(),
        horizontal_align=horizontal,
        vertical_align=vertical,
        runtime_model=runtime_model,
    )


def load_layout(template_dir: Path, layout_path: Path | None = None) -> Layout:
    template_dir = template_dir.expanduser().resolve()
    path = (layout_path or template_dir / "layout.json").expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"layout does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ConfigError(f"layout must be UTF-8 JSON: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"layout contains invalid JSON: {path}: {exc}") from exc
    return parse_layout(data, template_dir)


def write_layout(path: Path, layout: Layout) -> None:
    contents = (json.dumps(layout.to_dict(), indent=2) + "\n").encode("utf-8")
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.stem}-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temp:
            temp.write(contents)
            temp.flush()
            os.fsync(temp.fileno())
            temp_name = temp.name
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
