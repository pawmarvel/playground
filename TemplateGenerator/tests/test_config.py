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
        self.assertEqual(layout.runtime_model, "gpt-image-2")

    def test_model_is_optional_for_default_gemini_route(self) -> None:
        data = layout_data()
        data.pop("model")
        self.assertIsNone(parse_layout(data, self.root).runtime_model)

    def test_rejects_unknown_runtime_model(self) -> None:
        data = layout_data()
        data["model"] = "other"
        with self.assertRaisesRegex(ConfigError, "gpt-image-2"):
            parse_layout(data, self.root)

    def test_rejects_unknown_fields(self) -> None:
        data = layout_data()
        data["future"] = True
        with self.assertRaisesRegex(ConfigError, "unsupported"):
            parse_layout(data, self.root)

    def test_rejects_asset_path_escape(self) -> None:
        data = layout_data()
        data["art"] = "../art.png"
        with self.assertRaisesRegex(ConfigError, "escapes"):
            parse_layout(data, self.root)

    def test_rejects_box_outside_canvas(self) -> None:
        data = layout_data()
        data["pet"]["box"]["x"] = 500
        with self.assertRaisesRegex(ConfigError, "does not intersect"):
            parse_layout(data, self.root)

    def test_rejects_rotation_outside_consumer_range(self) -> None:
        data = layout_data()
        data["pet"]["rotation_degrees"] = 361
        with self.assertRaisesRegex(ConfigError, "between -360 and 360"):
            parse_layout(data, self.root)


if __name__ == "__main__":
    unittest.main()
