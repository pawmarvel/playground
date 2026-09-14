from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import copy_font, layout_data, make_image
from pawmarvel_generator.config import ConfigError, load_layout, parse_layout


class ConfigTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_loads_layout_and_infers_canvas(self) -> None:
        layout = load_layout(self.root)
        self.assertEqual((layout.canvas_width, layout.canvas_height), (200, 300))
        self.assertEqual(layout.art_relative, "art.png")
        self.assertEqual(layout.schema_version, 2)

    def test_rejects_layout_v1(self) -> None:
        data = layout_data()
        data["schema_version"] = 1
        with self.assertRaisesRegex(ConfigError, "must be 2"):
            parse_layout(data, self.root)

    def test_rejects_runtime_model_in_composition_layout(self) -> None:
        data = layout_data()
        data["model"] = "gpt-image-2"
        with self.assertRaisesRegex(ConfigError, "unsupported"):
            parse_layout(data, self.root)

    def test_rejects_unknown_fields(self) -> None:
        data = layout_data()
        data["future"] = True
        with self.assertRaisesRegex(ConfigError, "unsupported"):
            parse_layout(data, self.root)

        data = layout_data()
        data["pet"]["rotation_degrees"] = 10
        with self.assertRaisesRegex(ConfigError, "pet has unsupported fields"):
            parse_layout(data, self.root)

    def test_rejects_asset_path_escape(self) -> None:
        data = layout_data()
        data["art"] = "../art.png"
        with self.assertRaisesRegex(ConfigError, "escapes"):
            parse_layout(data, self.root)

    def test_rejects_noncanonical_font_path(self) -> None:
        data = layout_data()
        data["name"]["font"] = "font.ttf"
        with self.assertRaisesRegex(ConfigError, "fonts/<filename>"):
            parse_layout(data, self.root)

    def test_rejects_box_outside_canvas(self) -> None:
        data = layout_data()
        data["pet"]["box"]["x"] = 500
        with self.assertRaisesRegex(ConfigError, "does not intersect"):
            parse_layout(data, self.root)

    def test_rejects_incomplete_name_contract(self) -> None:
        data = layout_data()
        del data["name"]["font_size_px"]
        with self.assertRaisesRegex(ConfigError, "name is missing: font_size_px"):
            parse_layout(data, self.root)

    def test_rejects_invalid_shrink_only_constraints(self) -> None:
        data = layout_data()
        data["name"]["min_font_size_px"] = 43
        with self.assertRaisesRegex(ConfigError, "must not exceed"):
            parse_layout(data, self.root)

        data = layout_data()
        data["name"]["fit"] = "grow_to_fit"
        with self.assertRaisesRegex(ConfigError, "must be shrink_only"):
            parse_layout(data, self.root)

        data = layout_data()
        data["name"]["padding_px"] = 25
        with self.assertRaisesRegex(ConfigError, "no usable"):
            parse_layout(data, self.root)


if __name__ == "__main__":
    unittest.main()
