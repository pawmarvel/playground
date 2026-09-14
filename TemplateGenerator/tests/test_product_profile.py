from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from pawmarvel_generator.image_size import ImageSize
from pawmarvel_generator.product_profile import (
    ProductProfileError,
    create_product_profile,
    load_product_profile,
    write_product_profile,
)


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
        self.assertEqual(profile.scale, 11.71875)
        self.assertEqual(profile.print_spec["physical_size"]["width"], 31.25)

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


    def test_rejects_print_ratio_outside_model_limit(self) -> None:
        with self.assertRaisesRegex(ProductProfileError, "3:1"):
            create_product_profile(
                profile_id="banner",
                print_size=ImageSize(4000, 1000),
            )

    def test_rejects_unknown_contract_fields(self) -> None:
        profile = create_product_profile(
            profile_id="test-profile",
            print_size=ImageSize(1344, 2016),
        )
        path = write_product_profile(self.root / "profile.json", profile)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["preview"]["obsolete"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ProductProfileError, "unsupported fields"):
            load_product_profile(path)

    def test_rejects_geometry_that_conflicts_with_profile_sizes(self) -> None:
        profile = create_product_profile(
            profile_id="test-profile",
            print_size=ImageSize(1344, 2016),
        )
        path = write_product_profile(self.root / "profile.json", profile)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["geometry"]["preview_to_print_scale"] = 3
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ProductProfileError, "conflicts"):
            load_product_profile(path)

    def test_rejects_preview_size_that_was_not_derived_from_profile(self) -> None:
        profile = create_product_profile(
            profile_id="test-profile",
            print_size=ImageSize(1344, 2016),
        )
        path = write_product_profile(self.root / "profile.json", profile)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["preview"]["target_long_edge_px"] = 1500
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ProductProfileError, "preview.art conflicts"):
            load_product_profile(path)


if __name__ == "__main__":
    unittest.main()
