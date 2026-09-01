from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from helpers import copy_font, layout_data, make_image, make_transparent_mark
from pawmarvel_generator.config import load_layout
from pawmarvel_generator.print_upscale import (
    PET_PRINT_MANIFEST_NAME,
    TEMPLATE_PRINT_MANIFEST_NAME,
    PrintUpscaleError,
    parse_target_size,
    prepare_print_assets,
    prepare_print_pet,
    prepare_print_template,
)
from pawmarvel_generator.renderer import render_to_files


class PrintUpscaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.template = self.root / "template"
        self.template.mkdir()
        make_image(
            self.template / "art.png", size=(200, 300), color=(20, 30, 40, 255)
        )
        fonts = self.template / "fonts"
        fonts.mkdir()
        copy_font(fonts)
        (self.template / "layout.json").write_text(
            json.dumps(layout_data()), encoding="utf-8"
        )
        self.pet = make_transparent_mark(self.root / "transformed-pet.png", size=(80, 60))
        self.output = self.root / "print"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parses_common_dimension_separators(self) -> None:
        self.assertEqual(parse_target_size("400x600"), (400, 600))
        self.assertEqual(parse_target_size("400*600"), (400, 600))
        self.assertEqual(parse_target_size("400×600"), (400, 600))

    def test_prepares_scaled_bundle(self) -> None:
        outputs = prepare_print_assets(
            template_dir=self.template,
            transformed_pet=self.pet,
            target_size=(400, 600),
            output_dir=self.output,
        )
        with Image.open(outputs.art) as image:
            self.assertEqual(image.size, (400, 600))
        with Image.open(outputs.pet) as image:
            self.assertEqual(image.size, (160, 120))

        layout = load_layout(self.output, layout_path=outputs.layout)
        self.assertEqual(layout.art_relative, "art-print.png")
        self.assertEqual(
            layout.pet_box.to_dict(),
            {"x": 100, "y": 100, "width": 200, "height": 240},
        )
        self.assertEqual(
            layout.name_box.to_dict(),
            {"x": 40, "y": 380, "width": 320, "height": 100},
        )
        self.assertEqual(layout.font_size_px, 60)
        self.assertEqual(layout.min_font_size_px, 20)
        self.assertEqual(layout.runtime_model, "gpt-image-2")
        self.assertTrue((self.output / "fonts" / "TestFont.ttf").is_file())
        self.assertTrue((self.output / "fonts" / "OFL.txt").is_file())

        manifest = json.loads(outputs.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["print"]["scale"], 2.0)
        self.assertEqual(
            manifest["print"]["layer_dimensions"]["pet"],
            {"width": 160, "height": 120},
        )

    def test_template_and_pet_can_be_upscaled_independently(self) -> None:
        template_outputs = prepare_print_template(
            template_dir=self.template,
            target_size=(400, 600),
            output_dir=self.output,
        )
        self.assertTrue(template_outputs.art.is_file())
        self.assertTrue((self.output / TEMPLATE_PRINT_MANIFEST_NAME).is_file())
        self.assertFalse((self.output / "transformed-pet-print.png").exists())

        art_hash = template_outputs.art.read_bytes()
        customer_output = self.root / "customer-print"
        pet_outputs = prepare_print_pet(
            template_dir=self.template,
            transformed_pet=self.pet,
            print_layout_path=template_outputs.layout,
            output_dir=customer_output,
        )
        with Image.open(pet_outputs.pet) as image:
            self.assertEqual(image.size, (160, 120))
        self.assertTrue((customer_output / PET_PRINT_MANIFEST_NAME).is_file())
        self.assertEqual(template_outputs.art.read_bytes(), art_hash)
        self.assertFalse((customer_output / "art-print.png").exists())
        self.assertFalse((customer_output / "layout-print.json").exists())

        manifest = json.loads(pet_outputs.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["artifact_kind"], "pet-print")
        self.assertEqual(
            manifest["template_binding"]["print_layout_sha256"],
            hashlib.sha256(template_outputs.layout.read_bytes()).hexdigest(),
        )

    def test_pet_upscale_rejects_layout_not_derived_from_preview(self) -> None:
        template_outputs = prepare_print_template(
            template_dir=self.template,
            target_size=(400, 600),
            output_dir=self.output,
        )
        data = json.loads(template_outputs.layout.read_text(encoding="utf-8"))
        data["pet"]["box"]["x"] += 1
        template_outputs.layout.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(PrintUpscaleError, "uniformly scaled"):
            prepare_print_pet(
                template_dir=self.template,
                transformed_pet=self.pet,
                print_layout_path=template_outputs.layout,
                output_dir=self.root / "customer-print",
            )

    def test_fractional_scale_uses_scaled_edges(self) -> None:
        outputs = prepare_print_assets(
            template_dir=self.template,
            transformed_pet=self.pet,
            target_size=(300, 450),
            output_dir=self.output,
        )
        layout = load_layout(self.output, layout_path=outputs.layout)
        self.assertEqual(layout.pet_box.to_dict(), {"x": 75, "y": 75, "width": 150, "height": 180})
        self.assertEqual(layout.name_box.to_dict(), {"x": 30, "y": 285, "width": 240, "height": 75})
        with Image.open(outputs.pet) as image:
            self.assertEqual(image.size, (120, 90))

    def test_rejects_aspect_ratio_change(self) -> None:
        with self.assertRaisesRegex(PrintUpscaleError, "aspect ratio"):
            prepare_print_assets(
                template_dir=self.template,
                transformed_pet=self.pet,
                target_size=(400, 500),
                output_dir=self.output,
            )

    def test_refuses_overwrite_without_force(self) -> None:
        self.output.mkdir()
        (self.output / "art-print.png").write_bytes(b"existing")
        with self.assertRaisesRegex(PrintUpscaleError, "--force"):
            prepare_print_assets(
                template_dir=self.template,
                transformed_pet=self.pet,
                target_size=(400, 600),
                output_dir=self.output,
            )

    def test_bria_backend_uses_native_scale_and_preserves_source_alpha(self) -> None:
        calls: list[tuple[int, bool]] = []

        def provider(contents: bytes, increase: int, preserve_alpha: bool) -> bytes:
            calls.append((increase, preserve_alpha))
            with Image.open(BytesIO(contents)) as source:
                result = source.convert("RGBA").resize(
                    (source.width * increase, source.height * increase),
                    Image.Resampling.NEAREST,
                )
            result.putalpha(255)
            output = BytesIO()
            result.save(output, format="PNG")
            return output.getvalue()

        outputs = prepare_print_assets(
            template_dir=self.template,
            transformed_pet=self.pet,
            target_size=(600, 900),
            output_dir=self.output,
            backend="bria",
            bria_provider=provider,
        )
        self.assertEqual(calls, [(4, True), (4, True)])
        with Image.open(outputs.pet) as image:
            low, high = image.getchannel("A").getextrema()
            self.assertEqual(low, 0)
            self.assertEqual(high, 255)

    def test_bria_uses_best_native_increase_then_normalizes_beyond_four_x(self) -> None:
        calls: list[int] = []

        def provider(contents: bytes, increase: int, preserve_alpha: bool) -> bytes:
            calls.append(increase)
            with Image.open(BytesIO(contents)) as source:
                result = source.convert("RGBA").resize(
                    (source.width * increase, source.height * increase)
                )
            output = BytesIO()
            result.save(output, format="PNG")
            return output.getvalue()

        outputs = prepare_print_assets(
            template_dir=self.template,
            transformed_pet=self.pet,
            target_size=(1000, 1500),
            output_dir=self.output,
            backend="bria",
            bria_provider=provider,
        )
        self.assertEqual(calls, [4, 4])
        with Image.open(outputs.art) as image:
            self.assertEqual(image.size, (1000, 1500))

    def test_print_bundle_recomposes_through_shared_renderer(self) -> None:
        outputs = prepare_print_assets(
            template_dir=self.template,
            transformed_pet=self.pet,
            target_size=(400, 600),
            output_dir=self.output,
        )
        final = self.output / "final-print.png"
        debug = self.output / "final-print-debug.png"
        render_to_files(
            template_dir=self.output,
            layout_path=outputs.layout,
            pet_image=outputs.pet,
            pet_name="BUDDY",
            output=final,
            debug_output=debug,
        )
        with Image.open(final) as image:
            self.assertEqual(image.size, (400, 600))


if __name__ == "__main__":
    unittest.main()
