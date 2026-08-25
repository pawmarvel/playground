from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .cli import _atomic_write_bytes
from .config import ConfigError, Layout, Rect, load_layout


ALPHA_THRESHOLD = 8


class RenderError(ValueError):
    """A pet, text, or generated-name input cannot be rendered."""


def _open_rgba(source: Path | BinaryIO, label: str) -> Image.Image:
    try:
        with Image.open(source) as image:
            return image.convert("RGBA")
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise RenderError(f"{label} is not a readable image: {source}") from exc


def _visible_bounds(image: Image.Image, label: str) -> tuple[int, int, int, int]:
    alpha = image.getchannel("A")
    visible = alpha.point(lambda value: 255 if value > ALPHA_THRESHOLD else 0)
    bounds = visible.getbbox()
    if bounds is None:
        raise RenderError(f"{label} is fully transparent")
    return bounds


def _trim_visible(image: Image.Image, label: str) -> Image.Image:
    return image.crop(_visible_bounds(image, label))


def _open_name_image(source: Path | BinaryIO) -> Image.Image:
    try:
        with Image.open(source) as image:
            image.load()
            if image.format != "PNG":
                raise RenderError("name image must be a PNG")
            if "A" not in image.getbands() and "transparency" not in image.info:
                raise RenderError("name image must contain an alpha channel")
            rgba = image.convert("RGBA")
    except RenderError:
        raise
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise RenderError(f"name image is not a readable image: {source}") from exc

    alpha_min, _ = rgba.getchannel("A").getextrema()
    if alpha_min > ALPHA_THRESHOLD:
        raise RenderError("name image must contain a transparent background")
    _visible_bounds(rgba, "name image")
    return rgba


def validate_name_image(source: Path | BinaryIO) -> tuple[int, int]:
    """Validate a generated name asset and return its visible alpha dimensions."""
    image = _open_name_image(source)
    left, top, right, bottom = _visible_bounds(image, "name image")
    return right - left, bottom - top


def _fit_contain(image: Image.Image, box: Rect) -> Image.Image:
    scale = min(box.width / image.width, box.height / image.height)
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _place_pet(canvas: Image.Image, pet: Image.Image, layout: Layout) -> tuple[int, int, int, int]:
    pet = _fit_contain(_trim_visible(pet, "pet image"), layout.pet_box)
    if layout.pet_rotation_degrees:
        pet = pet.rotate(
            layout.pet_rotation_degrees,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )
    x = layout.pet_box.x + (layout.pet_box.width - pet.width) // 2
    y = layout.pet_box.y + layout.pet_box.height - pet.height
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(pet, (x, y), pet)
    canvas.alpha_composite(layer)
    return x, y, x + pet.width, y + pet.height


def _place_name_image(
    canvas: Image.Image, name_image: Image.Image, layout: Layout
) -> tuple[int, int, int, int]:
    name_image = _fit_contain(
        _trim_visible(name_image, "name image"), layout.name_box
    )
    box = layout.name_box
    if layout.horizontal_align == "left":
        x = box.x
    elif layout.horizontal_align == "right":
        x = box.right - name_image.width
    else:
        x = box.x + (box.width - name_image.width) // 2

    if layout.vertical_align == "top":
        y = box.y
    elif layout.vertical_align == "bottom":
        y = box.bottom - name_image.height
    else:
        y = box.y + (box.height - name_image.height) // 2

    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(name_image, (x, y), name_image)
    canvas.alpha_composite(layer)
    return x, y, x + name_image.width, y + name_image.height


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text, font=font)


def _select_font(
    draw: ImageDraw.ImageDraw, text: str, layout: Layout
) -> tuple[ImageFont.FreeTypeFont, tuple[int, int, int, int]]:
    for size in range(layout.font_size_px, layout.min_font_size_px - 1, -1):
        font = ImageFont.truetype(str(layout.font_path), size=size)
        bounds = _text_bbox(draw, text, font)
        if bounds[2] - bounds[0] <= layout.name_box.width and bounds[3] - bounds[1] <= layout.name_box.height:
            return font, bounds
    raise RenderError(
        f"pet name does not fit the configured box at minimum font size {layout.min_font_size_px}"
    )


def _aligned_text_origin(layout: Layout, bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = bounds
    width = right - left
    height = bottom - top
    box = layout.name_box
    if layout.horizontal_align == "left":
        x = box.x - left
    elif layout.horizontal_align == "right":
        x = box.right - width - left
    else:
        x = box.x + (box.width - width) // 2 - left

    if layout.vertical_align == "top":
        y = box.y - top
    elif layout.vertical_align == "bottom":
        y = box.bottom - height - top
    else:
        y = box.y + (box.height - height) // 2 - top
    return x, y


def _parse_rgba(value: str) -> tuple[int, int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in range(1, 9, 2))  # type: ignore[return-value]


def render_with_layout(
    layout: Layout,
    pet_image: Path | BinaryIO,
    pet_name: str,
    *,
    name_image: Path | BinaryIO | None = None,
    debug: bool = False,
) -> Image.Image:
    pet_name = pet_name.strip()
    if not pet_name:
        raise RenderError("pet name must not be empty")
    canvas = _open_rgba(layout.art_path, "art")
    if canvas.size != (layout.canvas_width, layout.canvas_height):
        raise ConfigError("art dimensions changed after layout validation")
    pet = _open_rgba(pet_image, "pet")
    pet_bounds = _place_pet(canvas, pet, layout)

    draw = ImageDraw.Draw(canvas)
    rendered_name_bounds: tuple[int, int, int, int] | None = None
    if name_image is not None:
        rendered_name_bounds = _place_name_image(
            canvas, _open_name_image(name_image), layout
        )
    else:
        font, text_bounds = _select_font(draw, pet_name, layout)
        text_origin = _aligned_text_origin(layout, text_bounds)
        draw.text(text_origin, pet_name, font=font, fill=_parse_rgba(layout.color))

    if debug:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle(
            (layout.pet_box.x, layout.pet_box.y, layout.pet_box.right - 1, layout.pet_box.bottom - 1),
            outline=(255, 64, 64, 255),
            width=2,
        )
        draw.rectangle(pet_bounds, outline=(255, 196, 0, 255), width=2)
        draw.rectangle(
            (layout.name_box.x, layout.name_box.y, layout.name_box.right - 1, layout.name_box.bottom - 1),
            outline=(64, 192, 255, 255),
            width=2,
        )
        if rendered_name_bounds:
            draw.rectangle(
                rendered_name_bounds,
                outline=(64, 255, 128, 255),
                width=2,
            )
    return canvas


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def render_preview(
    template_dir: Path,
    pet_image: Path | BinaryIO,
    pet_name: str,
    *,
    name_image: Path | BinaryIO | None = None,
) -> bytes:
    layout = load_layout(template_dir)
    return _png_bytes(
        render_with_layout(layout, pet_image, pet_name, name_image=name_image)
    )


def render_to_files(
    *,
    template_dir: Path,
    pet_image: Path,
    pet_name: str,
    name_image: Path | None = None,
    output: Path,
    debug_output: Path | None = None,
    force: bool = False,
) -> tuple[Path, Path | None]:
    output = output.expanduser().resolve()
    debug_output = debug_output.expanduser().resolve() if debug_output else None
    targets = [output] + ([debug_output] if debug_output else [])
    if len(set(targets)) != len(targets):
        raise RenderError("output and debug output must be different files")
    existing = [path for path in targets if path.exists()]
    if existing and not force:
        raise RenderError(
            f"output already exists: {existing[0]} (pass --force to replace it)"
        )

    layout = load_layout(template_dir)
    final_image = render_with_layout(
        layout, pet_image, pet_name, name_image=name_image
    )
    debug_image = (
        render_with_layout(
            layout, pet_image, pet_name, name_image=name_image, debug=True
        )
        if debug_output
        else None
    )
    _atomic_write_bytes(output, _png_bytes(final_image))
    if debug_output and debug_image:
        _atomic_write_bytes(debug_output, _png_bytes(debug_image))
    return output, debug_output
