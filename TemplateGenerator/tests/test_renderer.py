from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from helpers import copy_font, layout_data, make_image
from pawmarvel_generator.renderer import RenderError, render_preview, render_to_files


class RendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        make_image(self.root / "art.png", size=(200, 300), color=(0, 0, 0, 0))
        fonts = self.root / "fonts"
        fonts.mkdir()
        copy_font(fonts)
        (self.root / "layout.json").write_text(
            json.dumps(layout_data()), encoding="utf-8"
        )
        pet = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        ImageDraw.Draw(pet).rectangle((20, 20, 59, 59), fill=(255, 0, 0, 255))
        self.pet = self.root / "pet.png"
        pet.save(self.pet)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_render_trims_and_bottom_centers_pet(self) -> None:
        output = self.root / "output.png"
        debug = self.root / "debug.png"
        render_to_files(
            template_dir=self.root,
            pet_image=self.pet,
            pet_name="BUDDY",
            output=output,
            debug_output=debug,
        )
        with Image.open(output) as image:
            self.assertEqual(image.size, (200, 300))
            self.assertEqual(image.getpixel((100, 169))[:3], (255, 0, 0))
        self.assertTrue(debug.is_file())

    def test_render_preview_returns_png(self) -> None:
        result = render_preview(self.root, self.pet, "BUDDY")
        self.assertTrue(result.startswith(b"\x89PNG"))

    def test_empty_name_fails(self) -> None:
        with self.assertRaisesRegex(RenderError, "must not be empty"):
            render_preview(self.root, self.pet, "  ")

    def test_font_size_hints_do_not_change_contract_rendering(self) -> None:
        baseline = render_preview(self.root, self.pet, "BUDDY")
        layout = layout_data()
        layout["name"]["font_size_px"] = 2
        layout["name"]["min_font_size_px"] = 1
        (self.root / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
        self.assertEqual(render_preview(self.root, self.pet, "BUDDY"), baseline)

    def test_vertical_alignment_hint_does_not_change_contract_rendering(self) -> None:
        baseline = render_preview(self.root, self.pet, "BUDDY")
        layout = layout_data()
        layout["name"]["vertical_align"] = "bottom"
        (self.root / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
        self.assertEqual(render_preview(self.root, self.pet, "BUDDY"), baseline)

    def test_fully_transparent_pet_fails(self) -> None:
        transparent = self.root / "transparent.png"
        make_image(transparent, color=(0, 0, 0, 0))
        with self.assertRaisesRegex(RenderError, "fully transparent"):
            render_preview(self.root, transparent, "BUDDY")


if __name__ == "__main__":
    unittest.main()
