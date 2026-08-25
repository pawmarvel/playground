from __future__ import annotations

import base64
import io
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw


FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
)


def system_font() -> Path:
    configured = os.environ.get("PAWMARVEL_TEST_FONT")
    candidates = ((Path(configured),) if configured else ()) + FONT_CANDIDATES
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("set PAWMARVEL_TEST_FONT to a usable TTF font")


def copy_font(root: Path, name: str = "TestFont.ttf") -> Path:
    destination = root / name
    shutil.copyfile(system_font(), destination)
    return destination


def make_image(
    path: Path,
    *,
    size: tuple[int, int] = (64, 64),
    color: tuple[int, int, int, int] = (200, 100, 50, 255),
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path, format="PNG")
    return path


def make_transparent_mark(
    path: Path,
    *,
    size: tuple[int, int] = (100, 40),
    color: tuple[int, int, int, int] = (0, 255, 0, 255),
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    left = max(1, size[0] // 10)
    top = max(1, size[1] // 4)
    right = max(left, size[0] - left - 1)
    bottom = max(top, size[1] - top - 1)
    ImageDraw.Draw(image).rectangle((left, top, right, bottom), fill=color)
    image.save(path, format="PNG")
    return path


def layout_data(font_name: str = "fonts/TestFont.ttf") -> dict:
    return {
        "schema_version": 1,
        "art": "art.png",
        "pet": {
            "box": {"x": 50, "y": 50, "width": 100, "height": 120},
            "rotation_degrees": 0,
        },
        "name": {
            "box": {"x": 20, "y": 190, "width": 160, "height": 50},
            "font": font_name,
            "font_size_px": 30,
            "min_font_size_px": 10,
            "color": "#F7E7C6FF",
            "horizontal_align": "center",
            "vertical_align": "middle",
        },
    }


class FakeImages:
    def __init__(self) -> None:
        self.kwargs = None
        self.call_count = 0

    def edit(self, **kwargs):
        self.kwargs = kwargs
        self.call_count += 1
        size_value = kwargs.get("size", "auto")
        if size_value == "auto":
            size = (64, 64)
        else:
            width, height = size_value.split("x", 1)
            size = (int(width), int(height))
        output_format = kwargs.get("output_format", "png")
        mode = "RGB" if output_format == "jpeg" else "RGBA"
        color = (80, 120, 180) if mode == "RGB" else (80, 120, 180, 255)
        image = Image.new(mode, size, color)
        buffer = io.BytesIO()
        image.save(buffer, format=output_format.upper())
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(buffer.getvalue()).decode())]
        )


class FakeClient:
    def __init__(self) -> None:
        self.images = FakeImages()
