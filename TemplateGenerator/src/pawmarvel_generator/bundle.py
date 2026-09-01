from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .config import ConfigError, Layout, Rect, load_layout, parse_layout
from .font_license import FontLicenseError, resolve_ofl_license


TEMPLATE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
PROMPT_MAX_BYTES = 1024 * 1024


class BundleError(ValueError):
    """A template bundle violates the production consumer contract."""


def _validate_png_alpha(path: Path, label: str) -> None:
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG":
                raise BundleError(f"{label} must be a PNG: {path}")
            if "A" not in image.getbands() and "transparency" not in image.info:
                raise BundleError(f"{label} must contain an alpha channel: {path}")
            alpha = (
                image.getchannel("A")
                if "A" in image.getbands()
                else image.convert("RGBA").getchannel("A")
            )
            low, high = alpha.getextrema()
            if high == 0:
                raise BundleError(f"{label} is fully transparent: {path}")
            if low == 255:
                raise BundleError(f"{label} has no transparent pixels: {path}")
    except BundleError:
        raise
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise BundleError(f"{label} is not a readable image: {path}") from exc


def _validate_reference(path: Path, label: str) -> None:
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise BundleError(f"{label} must be PNG or JPEG: {path}")
    try:
        with Image.open(path) as image:
            image.load()
            if image.width <= 0 or image.height <= 0:
                raise BundleError(f"{label} has invalid dimensions: {path}")
    except BundleError:
        raise
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise BundleError(f"{label} is not a readable image: {path}") from exc


def _validate_prompt(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise BundleError(f"{label} does not exist: {resolved}")
    if resolved.stat().st_size > PROMPT_MAX_BYTES:
        raise BundleError(f"{label} exceeds the 1 MiB bundle limit: {resolved}")
    try:
        contents = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"{label} must be UTF-8 text: {resolved}") from exc
    if not contents.strip():
        raise BundleError(f"{label} must not be empty: {resolved}")
    if "\x00" in contents:
        raise BundleError(f"{label} must not contain NUL bytes: {resolved}")
    return resolved


def _scaled_rect(rect: Rect, scale: float) -> Rect:
    left = round(rect.x * scale)
    top = round(rect.y * scale)
    right = round(rect.right * scale)
    bottom = round(rect.bottom * scale)
    return Rect(left, top, right - left, bottom - top)


def _validate_resolution_pair(preview: Layout, print_layout: Layout) -> None:
    if (
        print_layout.canvas_width <= preview.canvas_width
        or print_layout.canvas_height <= preview.canvas_height
    ):
        raise BundleError("print art must be larger than preview art in both dimensions")
    if (
        print_layout.canvas_width * preview.canvas_height
        != print_layout.canvas_height * preview.canvas_width
    ):
        raise BundleError("preview and print art must have exactly matching aspect ratios")
    scale = print_layout.canvas_width / preview.canvas_width
    if print_layout.pet_box != _scaled_rect(preview.pet_box, scale):
        raise BundleError("layout-print pet.box is not the scaled preview pet.box")
    if print_layout.name_box != _scaled_rect(preview.name_box, scale):
        raise BundleError("layout-print name.box is not the scaled preview name.box")
    if print_layout.pet_rotation_degrees != preview.pet_rotation_degrees:
        raise BundleError("preview and print pet rotation must match")
    if print_layout.font_relative != preview.font_relative:
        raise BundleError("preview and print layouts must use the same bundled font")
    if print_layout.font_size_px != max(1, round(preview.font_size_px * scale)):
        raise BundleError("layout-print font_size_px is not scaled from the preview layout")
    if print_layout.min_font_size_px != max(
        1, round(preview.min_font_size_px * scale)
    ):
        raise BundleError(
            "layout-print min_font_size_px is not scaled from the preview layout"
        )
    if (
        print_layout.color != preview.color
        or print_layout.horizontal_align != preview.horizontal_align
        or print_layout.vertical_align != preview.vertical_align
    ):
        raise BundleError("preview and print name rendering settings must match")


def validate_bundle(root: Path, *, validate_template_id: bool = True) -> Layout:
    root = root.expanduser().resolve()
    if validate_template_id and not TEMPLATE_ID_PATTERN.fullmatch(root.name):
        raise BundleError(f"bundle directory name is not a valid template id: {root.name}")
    if not root.is_dir():
        raise BundleError(f"bundle directory does not exist: {root}")
    allowed_entries = {
        "layout.json",
        "layout-print.json",
        "art.png",
        "print",
        "qa",
        "reference-design.png",
        "art-template.md",
        "pet-transform.md",
        "fonts",
    }
    unknown_entries = {path.name for path in root.iterdir()} - allowed_entries
    if unknown_entries:
        raise BundleError(
            "bundle contains unsupported top-level entries: "
            + ", ".join(sorted(unknown_entries))
        )
    try:
        layout = load_layout(root)
        print_layout = load_layout(root, layout_path=root / "layout-print.json")
    except ConfigError as exc:
        raise BundleError(str(exc)) from exc
    if layout.art_relative != "art.png":
        raise BundleError("layout.art must be art.png in a published bundle")
    if print_layout.art_relative != "print/art.png":
        raise BundleError(
            "layout-print.art must be print/art.png in a published bundle"
        )
    _validate_png_alpha(root / "art.png", "art.png")
    _validate_png_alpha(root / "print" / "art.png", "print/art.png")
    _validate_resolution_pair(layout, print_layout)
    _validate_png_alpha(root / "qa" / "transformed-pet.png", "qa/transformed-pet.png")
    _validate_reference(root / "reference-design.png", "reference-design.png")
    _validate_prompt(root / "art-template.md", "art-template.md")
    _validate_prompt(root / "pet-transform.md", "pet-transform.md")
    try:
        resolve_ofl_license(layout.font_path)
    except FontLicenseError as exc:
        raise BundleError(str(exc)) from exc
    fonts_dir = root / "fonts"
    if not fonts_dir.is_dir():
        raise BundleError("fonts must be a directory")
    font_entries = {path.name for path in fonts_dir.iterdir()}
    if font_entries != {layout.font_path.name, "OFL.txt"}:
        raise BundleError("fonts must contain exactly the configured TTF and OFL.txt")
    if not all(path.is_file() for path in fonts_dir.iterdir()):
        raise BundleError("fonts may contain files only")
    qa_dir = root / "qa"
    if not qa_dir.is_dir():
        raise BundleError("qa must be a directory")
    qa_entries = {path.name for path in qa_dir.iterdir()}
    if qa_entries != {"transformed-pet.png"}:
        raise BundleError("qa must contain exactly transformed-pet.png")
    if not all(path.is_file() for path in qa_dir.iterdir()):
        raise BundleError("qa may contain files only")
    print_dir = root / "print"
    if not print_dir.is_dir():
        raise BundleError("print must be a directory")
    print_entries = {path.name for path in print_dir.iterdir()}
    if print_entries != {"art.png"}:
        raise BundleError("print must contain exactly art.png")
    if not all(path.is_file() for path in print_dir.iterdir()):
        raise BundleError("print may contain files only")
    return layout


def publish_bundle(
    *,
    template_dir: Path,
    output_dir: Path,
    template_id: str,
    exemplar: Path,
    reference_design: Path,
    art_prompt: Path,
    pet_prompt: Path,
    print_art: Path,
    print_layout_path: Path,
    font_license: Path | None = None,
    runtime_model: str | None = "gpt-image-2",
    force: bool = False,
) -> Path:
    if not TEMPLATE_ID_PATTERN.fullmatch(template_id):
        raise BundleError(
            "template id must be 3-64 lowercase letters, numbers, or internal hyphens"
        )
    template_dir = template_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    destination = output_dir / template_id
    exemplar = exemplar.expanduser().resolve()
    reference_design = reference_design.expanduser().resolve()
    art_prompt = _validate_prompt(art_prompt, "art template prompt")
    pet_prompt = _validate_prompt(pet_prompt, "pet transformation prompt")
    _validate_png_alpha(exemplar, "approved exemplar")
    _validate_reference(reference_design, "finished reference design")

    selected_print_art = print_art.expanduser().resolve()
    selected_print_layout_path = print_layout_path.expanduser().resolve()
    try:
        preview_layout = load_layout(template_dir)
        try:
            print_layout_value = json.loads(
                selected_print_layout_path.read_text(encoding="utf-8")
            )
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleError(
                f"print layout is not readable JSON: {selected_print_layout_path}"
            ) from exc
        print_layout = parse_layout(
            print_layout_value,
            template_dir,
            art_override=selected_print_art,
        )
    except ConfigError as exc:
        raise BundleError(str(exc)) from exc
    _validate_png_alpha(preview_layout.art_path, "preview art")
    _validate_png_alpha(selected_print_art, "print art")
    _validate_resolution_pair(preview_layout, print_layout)
    try:
        license_path = resolve_ofl_license(preview_layout.font_path, font_license)
    except FontLicenseError as exc:
        raise BundleError(str(exc)) from exc

    if destination.exists() and not force:
        raise BundleError(f"bundle already exists: {destination} (pass --force to replace it)")
    output_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{template_id}-", dir=output_dir))
    backup: Path | None = None
    try:
        (stage / "qa").mkdir()
        (stage / "fonts").mkdir()
        (stage / "print").mkdir()
        shutil.copyfile(preview_layout.art_path, stage / "art.png")
        shutil.copyfile(selected_print_art, stage / "print" / "art.png")
        shutil.copyfile(exemplar, stage / "qa" / "transformed-pet.png")
        shutil.copyfile(art_prompt, stage / "art-template.md")
        shutil.copyfile(pet_prompt, stage / "pet-transform.md")
        with Image.open(reference_design) as source_reference:
            source_reference.convert("RGB").save(stage / "reference-design.png", "PNG")
        shutil.copyfile(
            preview_layout.font_path,
            stage / "fonts" / preview_layout.font_path.name,
        )
        shutil.copyfile(license_path, stage / "fonts" / "OFL.txt")
        layout_data = preview_layout.to_dict()
        layout_data["art"] = "art.png"
        layout_data["name"]["font"] = f"fonts/{preview_layout.font_path.name}"
        print_layout_data = print_layout.to_dict()
        print_layout_data["art"] = "print/art.png"
        print_layout_data["name"]["font"] = (
            f"fonts/{preview_layout.font_path.name}"
        )
        if runtime_model is None:
            layout_data.pop("model", None)
            print_layout_data.pop("model", None)
        else:
            layout_data["model"] = runtime_model
            print_layout_data["model"] = runtime_model
        try:
            parse_layout(layout_data, stage)
            parse_layout(print_layout_data, stage)
        except ConfigError as exc:
            raise BundleError(str(exc)) from exc
        (stage / "layout.json").write_text(
            json.dumps(layout_data, indent=2) + "\n", encoding="utf-8"
        )
        (stage / "layout-print.json").write_text(
            json.dumps(print_layout_data, indent=2) + "\n", encoding="utf-8"
        )
        validate_bundle(stage, validate_template_id=False)

        if destination.exists():
            backup = output_dir / f".{template_id}-previous"
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(destination, backup)
        os.replace(stage, destination)
        validate_bundle(destination)
        if backup is not None:
            shutil.rmtree(backup)
        return destination
    except Exception:
        if backup is not None and backup.exists():
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(backup, destination)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
