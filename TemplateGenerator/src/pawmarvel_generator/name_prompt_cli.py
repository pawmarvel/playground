# CLI purpose:
# Build reusable, layout-aware AI lettering configuration and create validated
# per-name prompts while rejecting names that cannot fit the approved name box.

from __future__ import annotations

import argparse
import json
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .cli import _atomic_write_bytes
from .config import ConfigError, Layout, load_layout
from .image_size import ImageSizeError, validate_generation_size
from .product_profile import ProductProfileError, load_product_profile


CONFIG_NAME = "name-generation.json"
PROMPT_TEMPLATE_NAME = "name-prompt-template.md"
STYLE_REFERENCE_NAME = "name-style-reference.png"
DEBUG_NAME = "name-slot-debug.png"
NAME_PLACEHOLDER = "{{PET_NAME}}"
NAME_PATTERN = r"^[A-Z]+(?:[ '\-][A-Z]+)*$"


class NamePromptError(ValueError):
    """A name-prompt configuration or input is invalid."""


PROMPT_TEMPLATE = """Create only the personalized pet-name lettering shown by the reference image.

TEXT TO RENDER: {{PET_NAME}}

Requirements:
- Spell the text exactly as supplied, with every character present once and in the same order.
- Render one single horizontal line of lettering only.
- Match the reference lettering's visual identity: letter construction, proportions, spacing, outline, fill, texture, decorative treatment, and overall finish.
- Preserve natural letter proportions. Do not stretch, squeeze, curve, stack, wrap, or abbreviate the name.
- Center the visible lettering in the canvas and make its visible height approximately 65-75% of the canvas height.
- Keep comfortable transparent padding on every side; no visible element may touch or leave the canvas.
- Output the name lettering alone on a fully transparent background.
- Do not include a pet, background art, mockup, garment, border, label, quotation marks, or any additional text.

Before returning the image, verify the spelling against TEXT TO RENDER character by character.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-name-prompt",
        description=(
            "Experimental future-extension tool: configure design-specific "
            "pet-name prompting and validate one pet name."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser(
        "configure",
        help="derive a reusable name-generation configuration from approved assets",
    )
    configure_parser.add_argument("--sample-design", type=Path, required=True)
    configure_parser.add_argument("--art", type=Path, required=True)
    configure_parser.add_argument("--layout", type=Path, required=True)
    configure_parser.add_argument("--output-dir", type=Path, required=True)
    configure_parser.add_argument("--min-characters", type=int, default=2)
    configure_parser.add_argument("--max-characters-advisory", type=int, default=15)
    configure_parser.add_argument("--min-natural-width-ratio", type=float, default=0.20)
    configure_parser.add_argument("--min-font-scale-ratio", type=float, default=0.60)
    configure_parser.add_argument("--long-name-scale-threshold", type=float, default=0.80)
    configure_parser.add_argument("--crop-padding-ratio", type=float, default=0.0)
    configure_parser.add_argument(
        "--style-reference-mode",
        choices=("mapped", "full"),
        default="mapped",
        help=(
            "mapped crops by layout coordinates; full treats the sample as "
            "non-geometric visual context"
        ),
    )
    configure_parser.add_argument(
        "--product-profile",
        type=Path,
        help="use profile-derived standard and long name generation canvases",
    )
    configure_parser.add_argument("--force", action="store_true")

    create_parser = subparsers.add_parser(
        "create",
        help="validate one pet name and create its concrete GPT Image prompt",
    )
    create_parser.add_argument("--config", type=Path, required=True)
    create_parser.add_argument("--pet-name", required=True)
    create_parser.add_argument("--output", type=Path, required=True)
    create_parser.add_argument(
        "--request-output",
        type=Path,
        help="request metadata JSON (default: <output stem>.request.json)",
    )
    create_parser.add_argument("--force", action="store_true")
    return parser


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise NamePromptError(f"{label} does not exist or is not a file: {resolved}")
    return resolved


def _read_image(path: Path, label: str) -> Image.Image:
    path = _regular_file(path, label)
    try:
        with Image.open(path) as source:
            source.load()
            return source.convert("RGBA")
    except UnidentifiedImageError as exc:
        raise NamePromptError(f"{label} is not a supported image: {path}") from exc


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _validate_configuration_options(args: argparse.Namespace) -> None:
    if args.min_characters < 1:
        raise NamePromptError("--min-characters must be at least 1")
    if args.max_characters_advisory < args.min_characters:
        raise NamePromptError(
            "--max-characters-advisory must not be less than --min-characters"
        )
    for label in (
        "min_natural_width_ratio",
        "min_font_scale_ratio",
        "long_name_scale_threshold",
    ):
        value = getattr(args, label)
        if not 0 < value <= 1:
            option = label.replace("_", "-")
            raise NamePromptError(
                f"--{option} must be greater than 0 and at most 1"
            )
    if not 0 <= args.crop_padding_ratio <= 1:
        raise NamePromptError("--crop-padding-ratio must be between 0 and 1")


def _relative_path(path: Path, root: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise NamePromptError(f"{label} must be inside --output-dir: {path}") from exc


def _snapshot(layout: Layout) -> dict[str, Any]:
    return {
        "canvas": {
            "width": layout.canvas_width,
            "height": layout.canvas_height,
        },
        "name_box": layout.name_box.to_dict(),
        "font": layout.font_relative,
        "font_size_px": layout.font_size_px,
        "min_font_size_px": layout.min_font_size_px,
        "horizontal_align": layout.horizontal_align,
        "vertical_align": layout.vertical_align,
    }


def _mapped_name_boxes(
    layout: Layout, sample_size: tuple[int, int], padding_ratio: float
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    sample_width, sample_height = sample_size
    scale_x = sample_width / layout.canvas_width
    scale_y = sample_height / layout.canvas_height
    box = layout.name_box
    left = max(0, min(sample_width, round(box.x * scale_x)))
    top = max(0, min(sample_height, round(box.y * scale_y)))
    right = max(0, min(sample_width, round(box.right * scale_x)))
    bottom = max(0, min(sample_height, round(box.bottom * scale_y)))
    if right <= left or bottom <= top:
        raise NamePromptError("the mapped name box does not intersect the sample design")
    pad_x = round((right - left) * padding_ratio)
    pad_y = round((bottom - top) * padding_ratio)
    crop = (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(sample_width, right + pad_x),
        min(sample_height, bottom + pad_y),
    )
    return (left, top, right, bottom), crop


def configure(args: argparse.Namespace) -> dict[str, Path]:
    _validate_configuration_options(args)
    sample_path = _regular_file(args.sample_design, "sample design")
    art_path = _regular_file(args.art, "art")
    layout_path = _regular_file(args.layout, "layout")
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir != layout_path.parent:
        raise NamePromptError(
            "for the MVP, --output-dir must be the directory containing --layout"
        )

    layout = load_layout(output_dir, layout_path)
    if art_path != layout.art_path:
        raise NamePromptError(
            f"--art must match the art configured by layout.json: {layout.art_path}"
        )

    sample = _read_image(sample_path, "sample design")
    art = _read_image(art_path, "art")
    if art.size != (layout.canvas_width, layout.canvas_height):
        raise NamePromptError("art dimensions changed while loading the layout")
    style_reference_mode = getattr(args, "style_reference_mode", "mapped")
    if style_reference_mode == "mapped":
        sample_ratio = sample.width / sample.height
        art_ratio = art.width / art.height
        if abs(sample_ratio - art_ratio) / art_ratio > 0.02:
            raise NamePromptError(
                "sample design and art aspect ratios differ by more than 2%; "
                "use --style-reference-mode full for a non-geometric web reference"
            )
        exact_box, crop_box = _mapped_name_boxes(
            layout, sample.size, args.crop_padding_ratio
        )
    else:
        exact_box = None
        crop_box = (0, 0, sample.width, sample.height)
    profile_path = getattr(args, "product_profile", None)
    product_profile = (
        load_product_profile(profile_path) if profile_path is not None else None
    )
    standard_size = (
        product_profile.preview_name_standard_size.api_value()
        if product_profile is not None
        else "1536x512"
    )
    long_name_size = (
        product_profile.preview_name_long_size.api_value()
        if product_profile is not None
        else "2048x688"
    )
    style_reference = sample.crop(crop_box)
    debug = sample.copy()
    debug_draw = ImageDraw.Draw(debug)
    debug_draw.rectangle(crop_box, outline=(0, 220, 90, 255), width=3)
    if exact_box is not None:
        debug_draw.rectangle(exact_box, outline=(0, 130, 255, 255), width=3)

    targets = {
        "config": output_dir / CONFIG_NAME,
        "prompt_template": output_dir / PROMPT_TEMPLATE_NAME,
        "style_reference": output_dir / STYLE_REFERENCE_NAME,
        "debug": output_dir / "qa" / DEBUG_NAME,
    }
    existing = [path for path in targets.values() if path.exists()]
    if existing and not args.force:
        raise NamePromptError(
            "output already exists; pass --force to replace it: "
            + ", ".join(str(path) for path in existing)
        )

    config: dict[str, Any] = {
        "schema_version": 1,
        "source_sample_design": str(sample_path),
        "layout": _relative_path(layout_path, output_dir, "layout"),
        "art": _relative_path(art_path, output_dir, "art"),
        "style_reference": STYLE_REFERENCE_NAME,
        "style_reference_mode": style_reference_mode,
        "prompt_template": PROMPT_TEMPLATE_NAME,
        "layout_snapshot": _snapshot(layout),
        "normalization": {
            "case": "upper",
            "trim_whitespace": True,
            "single_line": True,
            "allowed_pattern": NAME_PATTERN,
        },
        "constraints": {
            "min_characters": args.min_characters,
            "max_characters_advisory": args.max_characters_advisory,
            "min_natural_width_ratio": args.min_natural_width_ratio,
            "min_font_scale_ratio": args.min_font_scale_ratio,
        },
        "generation": {
            "model": "gpt-image-2",
            "product_profile_id": (
                product_profile.profile_id if product_profile is not None else None
            ),
            "standard_size": standard_size,
            "long_name_size": long_name_size,
            "long_name_scale_threshold": args.long_name_scale_threshold,
            "quality": "high",
            "background": "transparent",
            "output_format": "png",
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(targets["prompt_template"], PROMPT_TEMPLATE.encode("utf-8"))
    _atomic_write_bytes(targets["style_reference"], _png_bytes(style_reference))
    _atomic_write_bytes(targets["debug"], _png_bytes(debug))
    _atomic_write_bytes(targets["config"], _json_bytes(config))
    return targets


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NamePromptError(f"{label} must be an object")
    return value


def _resolve_config_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise NamePromptError(f"{label} must be a nonempty relative path")
    relative = Path(value)
    if relative.is_absolute():
        raise NamePromptError(f"{label} must be relative to the config directory")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise NamePromptError(f"{label} escapes the config directory") from exc
    return _regular_file(resolved, label)


def _load_name_config(path: Path) -> tuple[Path, Mapping[str, Any]]:
    path = _regular_file(path, "name-generation config")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise NamePromptError(f"config must be UTF-8 JSON: {path}") from exc
    except json.JSONDecodeError as exc:
        raise NamePromptError(f"config contains invalid JSON: {path}: {exc}") from exc
    config = _require_mapping(value, "config")
    if config.get("schema_version") != 1:
        raise NamePromptError("name-generation schema_version must be 1")
    return path, config


def _require_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NamePromptError(f"{label} must be a number")
    return float(value)


def _validate_snapshot(config: Mapping[str, Any], layout: Layout) -> None:
    if config.get("layout_snapshot") != _snapshot(layout):
        raise NamePromptError(
            "layout.json no longer matches name-generation.json; rerun configure"
        )


def _normalize_name(raw_name: str, normalization: Mapping[str, Any]) -> str:
    if normalization.get("case") != "upper":
        raise NamePromptError("only upper-case normalization is supported in schema v1")
    name = raw_name.strip().upper()
    if "\n" in name or "\r" in name:
        raise NamePromptError("pet name must be a single line")
    pattern = normalization.get("allowed_pattern")
    if not isinstance(pattern, str):
        raise NamePromptError("normalization.allowed_pattern must be a string")
    try:
        matched = re.fullmatch(pattern, name)
    except re.error as exc:
        raise NamePromptError("normalization.allowed_pattern is invalid") from exc
    if not matched:
        raise NamePromptError(
            "pet name may contain ASCII letters separated by single spaces, "
            "apostrophes, or hyphens"
        )
    return name


def _measure_name(name: str, layout: Layout) -> dict[str, float | int]:
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))

    def bounds(font_size: int) -> tuple[int, int]:
        font = ImageFont.truetype(str(layout.font_path), font_size)
        left, top, right, bottom = draw.textbbox((0, 0), name, font=font)
        return right - left, bottom - top

    natural_width, natural_height = bounds(layout.font_size_px)
    selected_size: int | None = None
    selected_width = 0
    selected_height = 0
    for size in range(layout.font_size_px, layout.min_font_size_px - 1, -1):
        width, height = bounds(size)
        if width <= layout.name_box.width and height <= layout.name_box.height:
            selected_size = size
            selected_width = width
            selected_height = height
            break
    if selected_size is None:
        raise NamePromptError(
            "pet name cannot fit name.box at or above name.min_font_size_px"
        )
    return {
        "natural_width_px": natural_width,
        "natural_height_px": natural_height,
        "natural_width_ratio": natural_width / layout.name_box.width,
        "selected_font_size_px": selected_size,
        "selected_width_px": selected_width,
        "selected_height_px": selected_height,
        "font_scale_ratio": selected_size / layout.font_size_px,
    }


def create_prompt(args: argparse.Namespace) -> dict[str, Any]:
    config_path, config = _load_name_config(args.config)
    root = config_path.parent
    layout_path = _resolve_config_path(root, config.get("layout"), "layout")
    art_path = _resolve_config_path(root, config.get("art"), "art")
    style_reference = _resolve_config_path(
        root, config.get("style_reference"), "style_reference"
    )
    prompt_template_path = _resolve_config_path(
        root, config.get("prompt_template"), "prompt_template"
    )
    layout = load_layout(root, layout_path)
    if layout.art_path != art_path:
        raise NamePromptError("configured art does not match layout.json")
    _validate_snapshot(config, layout)

    normalization = _require_mapping(config.get("normalization"), "normalization")
    constraints = _require_mapping(config.get("constraints"), "constraints")
    generation = _require_mapping(config.get("generation"), "generation")
    name = _normalize_name(args.pet_name, normalization)
    letter_count = sum("A" <= character <= "Z" for character in name)
    min_characters = constraints.get("min_characters")
    advisory_max = constraints.get("max_characters_advisory")
    if isinstance(min_characters, bool) or not isinstance(min_characters, int):
        raise NamePromptError("constraints.min_characters must be an integer")
    if isinstance(advisory_max, bool) or not isinstance(advisory_max, int):
        raise NamePromptError("constraints.max_characters_advisory must be an integer")
    if letter_count < min_characters:
        raise NamePromptError(
            f"pet name has {letter_count} letters; at least {min_characters} are required"
        )

    measurement = _measure_name(name, layout)
    min_width_ratio = _require_number(
        constraints.get("min_natural_width_ratio"),
        "constraints.min_natural_width_ratio",
    )
    min_scale_ratio = _require_number(
        constraints.get("min_font_scale_ratio"),
        "constraints.min_font_scale_ratio",
    )
    if measurement["natural_width_ratio"] < min_width_ratio:
        raise NamePromptError(
            "pet name is too visually short for this template's name box "
            f"({measurement['natural_width_ratio']:.3f} < {min_width_ratio:.3f})"
        )
    if measurement["font_scale_ratio"] < min_scale_ratio:
        raise NamePromptError(
            "pet name is too long for this template's legibility constraint "
            f"({measurement['font_scale_ratio']:.3f} < {min_scale_ratio:.3f})"
        )

    try:
        template = prompt_template_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise NamePromptError("prompt template must be UTF-8 text") from exc
    if template.count(NAME_PLACEHOLDER) != 1:
        raise NamePromptError(
            f"prompt template must contain {NAME_PLACEHOLDER} exactly once"
        )
    prompt = template.replace(NAME_PLACEHOLDER, name)

    scale_threshold = _require_number(
        generation.get("long_name_scale_threshold"),
        "generation.long_name_scale_threshold",
    )
    use_long_size = (
        letter_count > advisory_max
        or measurement["font_scale_ratio"] < scale_threshold
    )
    size_key = "long_name_size" if use_long_size else "standard_size"
    size = generation.get(size_key)
    if not isinstance(size, str) or not re.fullmatch(r"\d+x\d+", size):
        raise NamePromptError(f"generation.{size_key} must use WIDTHxHEIGHT")
    try:
        size = validate_generation_size(
            size,
            model=str(generation.get("model")),
            label=f"generation.{size_key}",
            allow_auto=False,
        )
    except ImageSizeError as exc:
        raise NamePromptError(str(exc)) from exc

    output = args.output.expanduser().resolve()
    request_output = (
        args.request_output.expanduser().resolve()
        if args.request_output is not None
        else output.with_name(f"{output.stem}.request.json")
    )
    if output.suffix.lower() != ".md":
        raise NamePromptError("--output must use the .md suffix")
    if request_output.suffix.lower() != ".json":
        raise NamePromptError("--request-output must use the .json suffix")
    if output == request_output:
        raise NamePromptError("prompt and request output paths must differ")
    existing = [path for path in (output, request_output) if path.exists()]
    if existing and not args.force:
        raise NamePromptError(
            "output already exists; pass --force to replace it: "
            + ", ".join(str(path) for path in existing)
        )

    warning = (
        f"name exceeds the advisory {advisory_max}-letter limit but passed "
        "the geometry check"
        if letter_count > advisory_max
        else None
    )
    request: dict[str, Any] = {
        "schema_version": 1,
        "pet_name": name,
        "prompt_file": str(output),
        "style_reference": str(style_reference),
        "api_parameters": {
            "model": generation.get("model"),
            "size": size,
            "quality": generation.get("quality"),
            "background": generation.get("background"),
            "output_format": generation.get("output_format"),
        },
        "validation": {
            "letter_count": letter_count,
            **measurement,
            "advisory": warning,
        },
    }
    _atomic_write_bytes(output, prompt.encode("utf-8"))
    _atomic_write_bytes(request_output, _json_bytes(request))
    return {
        "prompt": output,
        "request": request_output,
        "style_reference": style_reference,
        "pet_name": name,
        "api_parameters": request["api_parameters"],
        "validation": request["validation"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "configure":
            outputs = configure(args)
            print("Name prompt configuration created:")
            for label, path in outputs.items():
                print(f"  {label}: {path}")
        else:
            result = create_prompt(args)
            print("Name prompt created:")
            print(f"  pet_name: {result['pet_name']}")
            print(f"  prompt: {result['prompt']}")
            print(f"  request: {result['request']}")
            print(f"  style_reference: {result['style_reference']}")
            print(
                "  API parameters: "
                + json.dumps(result["api_parameters"], sort_keys=True)
            )
            warning = result["validation"].get("advisory")
            if warning:
                print(f"  warning: {warning}", file=sys.stderr)
        return 0
    except (ConfigError, NamePromptError, ProductProfileError, OSError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
