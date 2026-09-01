from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from helpers import make_image
from pawmarvel_generator.image_size import ImageSize
from pawmarvel_generator.product_profile import (
    ProductProfileError,
    create_product_profile,
    load_product_profile,
    normalize_reference,
    write_product_profile,
)
from pawmarvel_generator.profile_cli import build_parser


class ProductProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_derives_exact_ratio_api_valid_preview_layers(self) -> None:
        profile = create_product_profile(
            profile_id="blanket-king-9375x12375",
            print_size=ImageSize(9375, 12375),
            dpi=300,
        )
        self.assertEqual(profile.preview_art_size, ImageSize(800, 1056))
        self.assertEqual(profile.preview_pet_size, ImageSize(816, 816))
        self.assertEqual(profile.preview_name_standard_size, ImageSize(1440, 480))
        self.assertEqual(profile.preview_name_long_size, ImageSize(2016, 672))
        self.assertEqual(profile.scale, 11.71875)
        self.assertEqual(profile.print_spec["physical_size"]["width"], 31.25)
        self.assertEqual(profile.reference_fit, "contain")

    def test_profile_cli_defaults_reference_fit_to_contain(self) -> None:
        args = build_parser().parse_args(
            [
                "create",
                "--profile-id", "blanket-king-9375x12375",
                "--print-size", "9375x12375",
                "--output", str(self.root / "profile.json"),
            ]
        )
        self.assertEqual(args.reference_fit, "contain")

    def test_round_trips_multiple_independent_profiles(self) -> None:
        portrait = create_product_profile(
            profile_id="portrait-product",
            print_size=ImageSize(9375, 12375),
        )
        square = create_product_profile(
            profile_id="square-product",
            print_size=ImageSize(9000, 9000),
        )
        portrait_path = write_product_profile(self.root / "portrait.json", portrait)
        square_path = write_product_profile(self.root / "square.json", square)
        self.assertEqual(load_product_profile(portrait_path).preview_art_size, ImageSize(800, 1056))
        self.assertEqual(load_product_profile(square_path).preview_art_size, ImageSize(1024, 1024))

    def test_normalizes_screenshot_to_profile_canvas(self) -> None:
        source = make_image(self.root / "screenshot.png", size=(1200, 900))
        output = self.root / "reference-design.png"
        normalize_reference(
            source,
            output,
            ImageSize(800, 1056),
            fit="cover",
        )
        with Image.open(output) as image:
            self.assertEqual(image.size, (800, 1056))

    def test_contain_normalization_preserves_full_image_and_adds_padding(self) -> None:
        source = make_image(self.root / "screenshot.png", size=(1200, 900))
        output = self.root / "reference-design.png"
        normalize_reference(
            source,
            output,
            ImageSize(800, 1056),
            fit="contain",
        )
        with Image.open(output).convert("RGBA") as image:
            self.assertEqual(image.size, (800, 1056))
            self.assertEqual(image.getpixel((400, 0))[3], 0)
            self.assertEqual(image.getpixel((400, 528))[3], 255)

    def test_rejects_print_ratio_outside_model_limit(self) -> None:
        with self.assertRaisesRegex(ProductProfileError, "3:1"):
            create_product_profile(
                profile_id="banner",
                print_size=ImageSize(4000, 1000),
            )


if __name__ == "__main__":
    unittest.main()
