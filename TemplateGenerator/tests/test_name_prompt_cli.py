from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from helpers import copy_font, layout_data, make_image
from pawmarvel_generator.name_prompt_cli import (
    NAME_PLACEHOLDER,
    NamePromptError,
    build_parser,
    configure,
    create_prompt,
)
from pawmarvel_generator.image_size import ImageSize
from pawmarvel_generator.product_profile import (
    create_product_profile,
    write_product_profile,
)


class NamePromptCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        make_image(self.root / "art.png", size=(200, 300))
        make_image(self.root / "sample.png", size=(400, 600))
        (self.root / "fonts").mkdir()
        copy_font(self.root / "fonts")
        (self.root / "layout.json").write_text(
            json.dumps(layout_data()), encoding="utf-8"
        )
        self.parser = build_parser()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def configure_args(self, *extra: str):
        return self.parser.parse_args(
            [
                "configure",
                "--sample-design",
                str(self.root / "sample.png"),
                "--art",
                str(self.root / "art.png"),
                "--layout",
                str(self.root / "layout.json"),
                "--output-dir",
                str(self.root),
                *extra,
            ]
        )

    def create_args(self, name: str, *extra: str):
        return self.parser.parse_args(
            [
                "create",
                "--config",
                str(self.root / "name-generation.json"),
                "--pet-name",
                name,
                "--output",
                str(self.root / "qa" / f"name-{name}.md"),
                *extra,
            ]
        )

    def test_configure_creates_reusable_artifacts(self) -> None:
        outputs = configure(self.configure_args())

        self.assertTrue(all(path.is_file() for path in outputs.values()))
        config = json.loads(outputs["config"].read_text(encoding="utf-8"))
        self.assertEqual(config["layout"], "layout.json")
        self.assertEqual(config["art"], "art.png")
        self.assertEqual(config["layout_snapshot"]["name_box"]["width"], 160)
        self.assertIn(
            NAME_PLACEHOLDER,
            outputs["prompt_template"].read_text(encoding="utf-8"),
        )
        with Image.open(outputs["style_reference"]) as image:
            self.assertEqual(image.size, (320, 100))

    def test_full_style_reference_does_not_map_layout_onto_web_screenshot(self) -> None:
        make_image(self.root / "sample.png", size=(500, 300))

        outputs = configure(
            self.configure_args("--style-reference-mode", "full")
        )

        config = json.loads(outputs["config"].read_text(encoding="utf-8"))
        self.assertEqual(config["style_reference_mode"], "full")
        with Image.open(outputs["style_reference"]) as image:
            self.assertEqual(image.size, (500, 300))

    def test_create_normalizes_name_and_writes_request(self) -> None:
        configure(self.configure_args())

        result = create_prompt(self.create_args("sausage"))

        self.assertEqual(result["pet_name"], "SAUSAGE")
        prompt = result["prompt"].read_text(encoding="utf-8")
        self.assertIn("TEXT TO RENDER: SAUSAGE", prompt)
        self.assertNotIn(NAME_PLACEHOLDER, prompt)
        request = json.loads(result["request"].read_text(encoding="utf-8"))
        self.assertEqual(request["pet_name"], "SAUSAGE")
        self.assertEqual(request["api_parameters"]["background"], "transparent")
        self.assertIn(request["api_parameters"]["size"], {"1536x512", "2048x688"})

    def test_rejects_too_short_name(self) -> None:
        configure(self.configure_args())

        with self.assertRaisesRegex(NamePromptError, "at least 2"):
            create_prompt(self.create_args("A"))

    def test_rejects_visually_underfilled_name(self) -> None:
        configure(self.configure_args())

        with self.assertRaisesRegex(NamePromptError, "visually short"):
            create_prompt(self.create_args("II"))

    def test_rejects_name_that_cannot_fit(self) -> None:
        configure(self.configure_args())

        with self.assertRaisesRegex(NamePromptError, "cannot fit|too long"):
            create_prompt(self.create_args("WWWWWWWWWWWWWWWWWWWWWWWWWWWW"))

    def test_rejects_stale_layout_snapshot(self) -> None:
        configure(self.configure_args())
        changed = layout_data()
        changed["name"]["box"]["width"] = 140
        (self.root / "layout.json").write_text(
            json.dumps(changed), encoding="utf-8"
        )

        with self.assertRaisesRegex(NamePromptError, "rerun configure"):
            create_prompt(self.create_args("BUDDY"))

    def test_refuses_to_overwrite_configuration_without_force(self) -> None:
        configure(self.configure_args())

        with self.assertRaisesRegex(NamePromptError, "--force"):
            configure(self.configure_args())

    def test_product_profile_controls_name_generation_dimensions(self) -> None:
        make_image(self.root / "art.png", size=(800, 1200))
        make_image(self.root / "sample.png", size=(800, 1200))
        scaled = layout_data()
        for box_name in ("pet", "name"):
            box = scaled[box_name]["box"]
            for field in ("x", "y", "width", "height"):
                box[field] *= 4
        scaled["name"]["font_size_px"] *= 4
        scaled["name"]["min_font_size_px"] *= 4
        (self.root / "layout.json").write_text(json.dumps(scaled), encoding="utf-8")
        profile = write_product_profile(
            self.root / "profile.json",
            create_product_profile(
                profile_id="profile-name-test",
                print_size=ImageSize(1600, 2400),
            ),
        )

        configure(
            self.configure_args(
                "--product-profile",
                str(profile),
            )
        )
        result = create_prompt(self.create_args("SAUSAGE"))
        self.assertIn(
            result["api_parameters"]["size"],
            {"1440x480", "2016x672"},
        )


if __name__ == "__main__":
    unittest.main()
