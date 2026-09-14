from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .cli import _atomic_write_bytes
from .config import ConfigError, Layout, Rect, load_layout


ALPHA_THRESHOLD = 8


class RenderError(ValueError):
    """A pet or text input cannot be rendered."""


@dataclass(frozen=True)
class TextRenderMetrics:
    requested_font_size_px: int
    applied_font_size_px: int
    fit: str
    visible_width_px: int
    visible_height_px: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "requested_font_size_px": self.requested_font_size_px,
            "applied_font_size_px": self.applied_font_size_px,
            "fit": self.fit,
            "visible_width_px": self.visible_width_px,
            "visible_height_px": self.visible_height_px,
        }


@dataclass(frozen=True)
class RenderedComposition:
    image: Image.Image
    text: TextRenderMetrics


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


def _fit_contain(image: Image.Image, box: Rect) -> Image.Image:
    scale = min(box.width / image.width, box.height / image.height)
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _place_pet(canvas: Image.Image, pet: Image.Image, layout: Layout) -> tuple[int, int, int, int]:
    pet = _fit_contain(_trim_visible(pet, "pet image"), layout.pet_box)
    x = layout.pet_box.x + (layout.pet_box.width - pet.width) // 2
    y = layout.pet_box.y + layout.pet_box.height - pet.height
    canvas.alpha_composite(pet, (x, y))
    return x, y, x + pet.width, y + pet.height


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text, font=font)


def _select_font(
    draw: ImageDraw.ImageDraw, text: str, layout: Layout
) -> tuple[ImageFont.FreeTypeFont, tuple[int, int, int, int], TextRenderMetrics]:
    available_width = layout.name_box.width - 2 * layout.name_padding_px
    available_height = layout.name_box.height - 2 * layout.name_padding_px

    def measured(size: int) -> tuple[ImageFont.FreeTypeFont, tuple[int, int, int, int]]:
        font = ImageFont.truetype(str(layout.font_path), size=size)
        bounds = _text_bbox(draw, text, font)
        return font, bounds

    def fits(bounds: tuple[int, int, int, int]) -> bool:
        return (
            bounds[2] - bounds[0] <= available_width
            and bounds[3] - bounds[1] <= available_height
        )

    requested = measured(layout.font_size_px)
    if fits(requested[1]):
        bounds = requested[1]
        return requested[0], bounds, TextRenderMetrics(
            requested_font_size_px=layout.font_size_px,
            applied_font_size_px=layout.font_size_px,
            fit="nominal",
            visible_width_px=bounds[2] - bounds[0],
            visible_height_px=bounds[3] - bounds[1],
        )

    minimum = measured(layout.min_font_size_px)
    if not fits(minimum[1]):
        raise RenderError(
            "pet name does not fit the padded name box at "
            f"min_font_size_px={layout.min_font_size_px}"
        )

    best = minimum
    best_size = layout.min_font_size_px
    low = layout.min_font_size_px + 1
    high = layout.font_size_px - 1
    while low <= high:
        middle = (low + high) // 2
        candidate = measured(middle)
        if fits(candidate[1]):
            best = candidate
            best_size = middle
            low = middle + 1
        else:
            high = middle - 1
    bounds = best[1]
    return best[0], bounds, TextRenderMetrics(
        requested_font_size_px=layout.font_size_px,
        applied_font_size_px=best_size,
        fit="shrunk",
        visible_width_px=bounds[2] - bounds[0],
        visible_height_px=bounds[3] - bounds[1],
    )


def _aligned_text_origin(layout: Layout, bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = bounds
    width = right - left
    height = bottom - top
    padding = layout.name_padding_px
    box = Rect(
        x=layout.name_box.x + padding,
        y=layout.name_box.y + padding,
        width=layout.name_box.width - 2 * padding,
        height=layout.name_box.height - 2 * padding,
    )
    if layout.horizontal_align == "left":
        x = box.x - left
    elif layout.horizontal_align == "right":
        x = box.right - width - left
    else:
        x = box.x + (box.width - width) // 2 - left

    y = box.y + (box.height - height) // 2 - top
    return x, y


def _parse_rgba(value: str) -> tuple[int, int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in range(1, 9, 2))  # type: ignore[return-value]


def render_composition(
    layout: Layout,
    pet_image: Path | BinaryIO,
    pet_name: str,
    *,
    debug: bool = False,
) -> RenderedComposition:
    pet_name = pet_name.strip()
    if not pet_name:
        raise RenderError("pet name must not be empty")
    canvas = _open_rgba(layout.art_path, "art")
    if canvas.size != (layout.canvas_width, layout.canvas_height):
        raise ConfigError("art dimensions changed after layout validation")
    pet = _open_rgba(pet_image, "pet")
    pet_bounds = _place_pet(canvas, pet, layout)

    draw = ImageDraw.Draw(canvas)
    font, text_bounds, text_metrics = _select_font(draw, pet_name, layout)
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
    return RenderedComposition(image=canvas, text=text_metrics)


def render_with_layout(
    layout: Layout,
    pet_image: Path | BinaryIO,
    pet_name: str,
    *,
    debug: bool = False,
) -> Image.Image:
    return render_composition(
        layout,
        pet_image,
        pet_name,
        debug=debug,
    ).image


def _png_bytes(
    image: Image.Image, *, dpi: tuple[float, float] | None = None
) -> bytes:
    buffer = BytesIO()
    options = {"format": "PNG"}
    if dpi is not None:
        options["dpi"] = dpi
    image.save(buffer, **options)
    return buffer.getvalue()


def render_preview(
    template_dir: Path,
    pet_image: Path | BinaryIO,
    pet_name: str,
    *,
    layout_path: Path | None = None,
) -> bytes:
    layout = load_layout(template_dir, layout_path=layout_path)
    return _png_bytes(render_with_layout(layout, pet_image, pet_name))


def render_to_files(
    *,
    template_dir: Path,
    pet_image: Path,
    pet_name: str,
    output: Path,
    debug_output: Path | None = None,
    layout_path: Path | None = None,
    png_dpi: tuple[float, float] | None = None,
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

    layout = load_layout(template_dir, layout_path=layout_path)
    final_image = render_with_layout(layout, pet_image, pet_name)
    _atomic_write_bytes(output, _png_bytes(final_image, dpi=png_dpi))
    final_image.close()
    if debug_output:
        debug_image = render_with_layout(layout, pet_image, pet_name, debug=True)
        _atomic_write_bytes(debug_output, _png_bytes(debug_image, dpi=png_dpi))
        debug_image.close()
    return output, debug_output
