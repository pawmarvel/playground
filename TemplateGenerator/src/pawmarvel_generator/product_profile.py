from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError

from .image_size import (
    GPT_IMAGE_2_EDGE_MULTIPLE,
    GPT_IMAGE_2_MAX_EDGE,
    GPT_IMAGE_2_MAX_PIXELS,
    ImageSize,
    ImageSizeError,
    validate_gpt_image_2_size,
)


PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?$")


class ProductProfileError(ValueError):
    """A product profile or profile-derived operation is invalid."""


@dataclass(frozen=True)
class ProductProfile:
    path: Path | None
    profile_id: str
    print_size: ImageSize
    preview_art_size: ImageSize
    preview_pet_size: ImageSize
    preview_name_standard_size: ImageSize
    preview_name_long_size: ImageSize
    preview_target_long_edge: int
    reference_fit: str
    print_spec: Mapping[str, Any]

    @property
    def scale(self) -> float:
        return self.print_size.width / self.preview_art_size.width

    def to_dict(self) -> dict[str, Any]:
        ratio_gcd = math.gcd(self.print_size.width, self.print_size.height)
        print_layer_dimensions = {
            "art": self.print_size.to_dict(),
            "transformed_pet": {
                "width": max(1, round(self.preview_pet_size.width * self.scale)),
                "height": max(1, round(self.preview_pet_size.height * self.scale)),
            },
            "name_standard": {
                "width": max(
                    1, round(self.preview_name_standard_size.width * self.scale)
                ),
                "height": max(
                    1, round(self.preview_name_standard_size.height * self.scale)
                ),
            },
            "name_long": {
                "width": max(1, round(self.preview_name_long_size.width * self.scale)),
                "height": max(
                    1, round(self.preview_name_long_size.height * self.scale)
                ),
            },
        }
        return {
            "schema_version": 1,
            "profile_id": self.profile_id,
            "print": {
                "canvas": self.print_size.to_dict(),
                **dict(self.print_spec),
            },
            "preview": {
                "target_long_edge_px": self.preview_target_long_edge,
                "reference_fit": self.reference_fit,
                "art": self.preview_art_size.to_dict(),
                "transformed_pet": self.preview_pet_size.to_dict(),
                "name_standard": self.preview_name_standard_size.to_dict(),
                "name_long": self.preview_name_long_size.to_dict(),
            },
            "geometry": {
                "aspect_ratio": {
                    "width": self.print_size.width // ratio_gcd,
                    "height": self.print_size.height // ratio_gcd,
                },
                "preview_to_print_scale": self.scale,
                "nominal_print_layer_dimensions": print_layer_dimensions,
            },
        }


def _valid_candidates_for_ratio(
    ratio_width: int, ratio_height: int
) -> list[ImageSize]:
    width_step = GPT_IMAGE_2_EDGE_MULTIPLE // math.gcd(
        ratio_width, GPT_IMAGE_2_EDGE_MULTIPLE
    )
    height_step = GPT_IMAGE_2_EDGE_MULTIPLE // math.gcd(
        ratio_height, GPT_IMAGE_2_EDGE_MULTIPLE
    )
    multiplier_step = math.lcm(width_step, height_step)
    maximum_multiplier = min(
        GPT_IMAGE_2_MAX_EDGE // ratio_width,
        GPT_IMAGE_2_MAX_EDGE // ratio_height,
    )
    candidates: list[ImageSize] = []
    for multiplier in range(multiplier_step, maximum_multiplier + 1, multiplier_step):
        candidate = ImageSize(ratio_width * multiplier, ratio_height * multiplier)
        if candidate.pixels > GPT_IMAGE_2_MAX_PIXELS:
            continue
        try:
            validate_gpt_image_2_size(candidate, "derived preview size")
        except ImageSizeError:
            continue
        candidates.append(candidate)
    return candidates


def derive_art_preview_size(
    print_size: ImageSize, target_long_edge: int = 1024
) -> ImageSize:
    if target_long_edge <= 0:
        raise ProductProfileError("preview target long edge must be positive")
    ratio_gcd = math.gcd(print_size.width, print_size.height)
    ratio_width = print_size.width // ratio_gcd
    ratio_height = print_size.height // ratio_gcd
    if max(ratio_width, ratio_height) / min(ratio_width, ratio_height) > 3:
        raise ProductProfileError(
            "print canvas aspect ratio exceeds the GPT Image 2 limit of 3:1"
        )
    candidates = _valid_candidates_for_ratio(ratio_width, ratio_height)
    if not candidates:
        raise ProductProfileError(
            "no exact-aspect GPT Image 2 preview resolution can be derived for this print canvas"
        )
    return min(
        candidates,
        key=lambda value: (
            abs(max(value.width, value.height) - target_long_edge),
            abs(value.pixels - target_long_edge * target_long_edge),
        ),
    )


def _closest_square(target_edge: int) -> ImageSize:
    candidates: list[ImageSize] = []
    for edge in range(GPT_IMAGE_2_EDGE_MULTIPLE, GPT_IMAGE_2_MAX_EDGE + 1, 16):
        candidate = ImageSize(edge, edge)
        try:
            validate_gpt_image_2_size(candidate, "derived pet size")
        except ImageSizeError:
            continue
        candidates.append(candidate)
    return min(candidates, key=lambda value: abs(value.width - target_edge))


def _name_size(target_height: int) -> ImageSize:
    candidates: list[ImageSize] = []
    for height in range(GPT_IMAGE_2_EDGE_MULTIPLE, GPT_IMAGE_2_MAX_EDGE + 1, 16):
        candidate = ImageSize(height * 3, height)
        if candidate.width > GPT_IMAGE_2_MAX_EDGE:
            break
        try:
            validate_gpt_image_2_size(candidate, "derived name size")
        except ImageSizeError:
            continue
        candidates.append(candidate)
    return min(candidates, key=lambda value: abs(value.height - target_height))


def create_product_profile(
    *,
    profile_id: str,
    print_size: ImageSize,
    preview_target_long_edge: int = 1024,
    reference_fit: str = "contain",
    dpi: int | None = None,
    color_space: str = "sRGB",
    background: str = "transparent",
    output_format: str = "png",
    bleed_px: int = 0,
    safe_margin_px: int = 0,
    max_file_bytes: int | None = None,
    vendor_requirements_confirmed: bool = False,
) -> ProductProfile:
    if not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ProductProfileError(
            "profile ID must contain lowercase letters, numbers, and internal hyphens"
        )
    if print_size.width <= 0 or print_size.height <= 0:
        raise ProductProfileError("print dimensions must be positive")
    if reference_fit not in {"cover", "contain"}:
        raise ProductProfileError("reference fit must be cover or contain")
    if dpi is not None and dpi <= 0:
        raise ProductProfileError("DPI must be positive")
    if bleed_px < 0 or safe_margin_px < 0:
        raise ProductProfileError("bleed and safe margin must not be negative")
    if bleed_px * 2 >= min(print_size.width, print_size.height):
        raise ProductProfileError("bleed leaves no positive interior print region")
    if safe_margin_px * 2 >= min(print_size.width, print_size.height):
        raise ProductProfileError("safe margin leaves no positive interior print region")
    if max_file_bytes is not None and max_file_bytes <= 0:
        raise ProductProfileError("maximum file bytes must be positive")

    art = derive_art_preview_size(print_size, preview_target_long_edge)
    if print_size.width <= art.width or print_size.height <= art.height:
        raise ProductProfileError(
            "print canvas must be larger than the derived preview canvas in both axes"
        )
    pet = _closest_square(min(art.width, art.height))
    standard_name = _name_size(480)
    long_name = _name_size(round(480 * math.sqrt(2)))
    physical_size = (
        {
            "width": print_size.width / dpi,
            "height": print_size.height / dpi,
            "unit": "in",
        }
        if dpi is not None
        else None
    )
    print_spec = {
        "dpi": dpi,
        "physical_size": physical_size,
        "bleed_px": bleed_px,
        "safe_margin_px": safe_margin_px,
        "color_space": color_space,
        "icc_profile": None,
        "background": background,
        "output_format": output_format,
        "max_file_bytes": max_file_bytes,
        "vendor_requirements_confirmed": vendor_requirements_confirmed,
        "delivery_status": (
            "vendor-profile-confirmed"
            if vendor_requirements_confirmed
            else "print-candidate"
        ),
    }
    return ProductProfile(
        path=None,
        profile_id=profile_id,
        print_size=print_size,
        preview_art_size=art,
        preview_pet_size=pet,
        preview_name_standard_size=standard_name,
        preview_name_long_size=long_name,
        preview_target_long_edge=preview_target_long_edge,
        reference_fit=reference_fit,
        print_spec=print_spec,
    )


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductProfileError(f"{label} must be an object")
    return value


def _size_from_mapping(value: Any, label: str) -> ImageSize:
    data = _require_mapping(value, label)
    width = data.get("width")
    height = data.get("height")
    if isinstance(width, bool) or not isinstance(width, int):
        raise ProductProfileError(f"{label}.width must be an integer")
    if isinstance(height, bool) or not isinstance(height, int):
        raise ProductProfileError(f"{label}.height must be an integer")
    if width <= 0 or height <= 0:
        raise ProductProfileError(f"{label} dimensions must be positive")
    return ImageSize(width, height)


def load_product_profile(path: Path) -> ProductProfile:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ProductProfileError(f"product profile does not exist: {resolved}")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ProductProfileError("product profile must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise ProductProfileError(f"product profile contains invalid JSON: {exc}") from exc
    root = _require_mapping(value, "product profile")
    if root.get("schema_version") != 1:
        raise ProductProfileError("product profile schema_version must be 1")
    profile_id = root.get("profile_id")
    if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ProductProfileError("product profile ID is invalid")
    print_data = _require_mapping(root.get("print"), "print")
    preview = _require_mapping(root.get("preview"), "preview")
    print_size = _size_from_mapping(print_data.get("canvas"), "print.canvas")
    art = _size_from_mapping(preview.get("art"), "preview.art")
    pet = _size_from_mapping(preview.get("transformed_pet"), "preview.transformed_pet")
    standard_name = _size_from_mapping(preview.get("name_standard"), "preview.name_standard")
    long_name = _size_from_mapping(preview.get("name_long"), "preview.name_long")
    for label, size in (
        ("preview.art", art),
        ("preview.transformed_pet", pet),
        ("preview.name_standard", standard_name),
        ("preview.name_long", long_name),
    ):
        try:
            validate_gpt_image_2_size(size, label)
        except ImageSizeError as exc:
            raise ProductProfileError(str(exc)) from exc
    if print_size.width * art.height != print_size.height * art.width:
        raise ProductProfileError(
            "preview.art aspect ratio must exactly match print.canvas"
        )
    target = preview.get("target_long_edge_px")
    if isinstance(target, bool) or not isinstance(target, int) or target <= 0:
        raise ProductProfileError("preview.target_long_edge_px must be positive")
    reference_fit = preview.get("reference_fit")
    if reference_fit not in {"cover", "contain"}:
        raise ProductProfileError("preview.reference_fit must be cover or contain")
    print_spec = {key: item for key, item in print_data.items() if key != "canvas"}
    required_print_fields = {
        "dpi",
        "physical_size",
        "bleed_px",
        "safe_margin_px",
        "color_space",
        "icc_profile",
        "background",
        "output_format",
        "max_file_bytes",
        "vendor_requirements_confirmed",
        "delivery_status",
    }
    missing_print_fields = required_print_fields - set(print_spec)
    if missing_print_fields:
        raise ProductProfileError(
            "print is missing required fields: "
            + ", ".join(sorted(missing_print_fields))
        )
    dpi = print_spec.get("dpi")
    if dpi is not None and (
        isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0
    ):
        raise ProductProfileError("print.dpi must be a positive integer or null")
    for field in ("bleed_px", "safe_margin_px"):
        number = print_spec.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise ProductProfileError(f"print.{field} must be a nonnegative integer")
        if number * 2 >= min(print_size.width, print_size.height):
            raise ProductProfileError(
                f"print.{field} leaves no positive interior print region"
            )
    physical_size = print_spec.get("physical_size")
    if dpi is None:
        if physical_size is not None:
            raise ProductProfileError("print.physical_size must be null when DPI is null")
    else:
        physical = _require_mapping(physical_size, "print.physical_size")
        physical_width = physical.get("width")
        physical_height = physical.get("height")
        if (
            isinstance(physical_width, bool)
            or not isinstance(physical_width, (int, float))
            or isinstance(physical_height, bool)
            or not isinstance(physical_height, (int, float))
            or physical.get("unit") != "in"
        ):
            raise ProductProfileError(
                "print.physical_size must contain numeric width/height and unit 'in'"
            )
        if not math.isclose(physical_width, print_size.width / dpi, abs_tol=1e-9):
            raise ProductProfileError("print.physical_size.width conflicts with canvas/DPI")
        if not math.isclose(physical_height, print_size.height / dpi, abs_tol=1e-9):
            raise ProductProfileError("print.physical_size.height conflicts with canvas/DPI")
    if print_spec.get("background") not in {"transparent", "opaque"}:
        raise ProductProfileError("print.background must be transparent or opaque")
    if not isinstance(print_spec.get("color_space"), str) or not print_spec.get(
        "color_space"
    ).strip():
        raise ProductProfileError("print.color_space must be a nonempty string")
    confirmed = print_spec.get("vendor_requirements_confirmed")
    if not isinstance(confirmed, bool):
        raise ProductProfileError(
            "print.vendor_requirements_confirmed must be true or false"
        )
    expected_status = "vendor-profile-confirmed" if confirmed else "print-candidate"
    if print_spec.get("delivery_status") != expected_status:
        raise ProductProfileError(
            f"print.delivery_status must be {expected_status!r} for this confirmation state"
        )
    icc_profile = print_spec.get("icc_profile")
    if icc_profile is not None and (
        not isinstance(icc_profile, str) or not icc_profile.strip()
    ):
        raise ProductProfileError("print.icc_profile must be a nonempty string or null")
    maximum = print_spec.get("max_file_bytes")
    if maximum is not None and (
        isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0
    ):
        raise ProductProfileError("print.max_file_bytes must be positive or null")
    if print_spec.get("output_format") != "png":
        raise ProductProfileError("MVP product profiles currently require PNG output")
    if print_size.width <= art.width or print_size.height <= art.height:
        raise ProductProfileError(
            "print canvas must be larger than preview.art in both axes"
        )
    return ProductProfile(
        path=resolved,
        profile_id=profile_id,
        print_size=print_size,
        preview_art_size=art,
        preview_pet_size=pet,
        preview_name_standard_size=standard_name,
        preview_name_long_size=long_name,
        preview_target_long_edge=target,
        reference_fit=reference_fit,
        print_spec=print_spec,
    )


def write_product_profile(path: Path, profile: ProductProfile, *, force: bool = False) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() != ".json":
        raise ProductProfileError("product profile output must use the .json suffix")
    if resolved.exists() and not force:
        raise ProductProfileError(
            f"product profile already exists: {resolved} (pass --force to replace it)"
        )
    contents = (json.dumps(profile.to_dict(), indent=2) + "\n").encode("utf-8")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{resolved.stem}-", suffix=".tmp", dir=resolved.parent, delete=False
        ) as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
            temporary = output.name
        os.replace(temporary, resolved)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return resolved


def normalize_reference(
    source: Path, output: Path, size: ImageSize, *, fit: str, force: bool = False
) -> Path:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_file():
        raise ProductProfileError(f"reference design does not exist: {source}")
    if fit not in {"cover", "contain"}:
        raise ProductProfileError("reference fit must be cover or contain")
    if output.exists() and not force:
        raise ProductProfileError(
            f"normalized reference already exists: {output} (pass --force to replace it)"
        )
    try:
        with Image.open(source) as original:
            original.load()
            image = original.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ProductProfileError(f"reference design is not a readable image: {source}") from exc

    target = (size.width, size.height)
    if fit == "cover":
        source_ratio = image.width / image.height
        target_ratio = size.width / size.height
        if source_ratio > target_ratio:
            crop_width = max(1, round(image.height * target_ratio))
            left = (image.width - crop_width) // 2
            image = image.crop((left, 0, left + crop_width, image.height))
        elif source_ratio < target_ratio:
            crop_height = max(1, round(image.width / target_ratio))
            top = (image.height - crop_height) // 2
            image = image.crop((0, top, image.width, top + crop_height))
        normalized = image.resize(target, Image.Resampling.LANCZOS)
    else:
        image.thumbnail(target, Image.Resampling.LANCZOS)
        normalized = Image.new("RGBA", target, (0, 0, 0, 0))
        normalized.alpha_composite(
            image, ((size.width - image.width) // 2, (size.height - image.height) // 2)
        )
    buffer = BytesIO()
    normalized.save(buffer, format="PNG")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{output.stem}-", suffix=".tmp", dir=output.parent, delete=False
        ) as destination:
            destination.write(buffer.getvalue())
            destination.flush()
            os.fsync(destination.fileno())
            temporary = destination.name
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return output


def validate_print_output(profile: ProductProfile, path: Path) -> None:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ProductProfileError(f"print output does not exist: {resolved}")
    try:
        with Image.open(resolved) as image:
            image.load()
            if image.size != (profile.print_size.width, profile.print_size.height):
                raise ProductProfileError(
                    "print output dimensions do not match product profile: "
                    f"{image.width}x{image.height} != {profile.print_size.api_value()}"
                )
            expected_format = str(profile.print_spec.get("output_format", "png")).lower()
            actual_format = (image.format or "").lower()
            if actual_format != expected_format:
                raise ProductProfileError(
                    f"print output format is {actual_format or 'unknown'}, expected {expected_format}"
                )
            expected_dpi = profile.print_spec.get("dpi")
            if expected_dpi is not None:
                actual_dpi = image.info.get("dpi")
                if (
                    not isinstance(actual_dpi, tuple)
                    or len(actual_dpi) != 2
                    or not all(
                        math.isclose(float(value), float(expected_dpi), abs_tol=1.0)
                        for value in actual_dpi
                    )
                ):
                    raise ProductProfileError(
                        f"print output DPI does not match product profile: {actual_dpi!r}"
                    )
            background = profile.print_spec.get("background")
            if background == "opaque" and image.convert("RGBA").getchannel("A").getextrema()[0] < 255:
                raise ProductProfileError("print output must be opaque for this product profile")
    except ProductProfileError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise ProductProfileError(f"print output is not a readable image: {resolved}") from exc
    maximum = profile.print_spec.get("max_file_bytes")
    if isinstance(maximum, int) and resolved.stat().st_size > maximum:
        raise ProductProfileError(
            f"print output exceeds product profile maximum file size of {maximum} bytes"
        )
