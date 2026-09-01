from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from PIL import Image

from helpers import copy_font, make_image, make_transparent_mark
from pawmarvel_generator.image_size import ImageSize
from pawmarvel_generator.print_upscale import (
    PrintUpscaleError,
    prepare_print_assets,
    prepare_print_pet,
    prepare_print_template,
    verify_print_bundle,
)
from pawmarvel_generator.product_profile import (
    create_product_profile,
    write_product_profile,
)
from pawmarvel_generator.render_cli import main as render_main
from pawmarvel_generator.upscale_cli import main as upscale_main


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ManualPrintFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.template = self.root / "template"
        self.template.mkdir()
        make_image(
            self.template / "art.png",
            size=(800, 1200),
            color=(20, 30, 40, 255),
        )
        fonts = self.template / "fonts"
        fonts.mkdir()
        copy_font(fonts)
        layout = {
            "schema_version": 1,
            "art": "art.png",
            "pet": {
                "box": {"x": 200, "y": 200, "width": 400, "height": 480},
                "rotation_degrees": 0,
            },
            "name": {
                "box": {"x": 80, "y": 760, "width": 640, "height": 200},
                "font": "fonts/TestFont.ttf",
                "font_size_px": 120,
                "min_font_size_px": 40,
                "color": "#FFFFFFFF",
                "horizontal_align": "center",
                "vertical_align": "middle",
            },
        }
        (self.template / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
        self.pet = make_transparent_mark(
            self.root / "transformed-pet.png", size=(816, 816)
        )
        profile = create_product_profile(
            profile_id="test-shirt",
            print_size=ImageSize(1600, 2400),
            dpi=300,
        )
        self.profile = write_product_profile(
            self.template / "product-profile.json", profile
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _prepare(self):
        return prepare_print_assets(
            template_dir=self.template,
            layout_path=self.template / "layout.json",
            transformed_pet=self.pet,
            target_size=(1600, 2400),
            output_dir=self.root / "print",
            product_profile=self.profile,
        )

    def test_manual_artifacts_upscale_and_render_without_run_manifest(self) -> None:
        outputs = self._prepare()
        final = self.root / "print" / "final-print.png"
        result = render_main(
            [
                "--template-dir", str(self.root / "print"),
                "--layout", str(outputs.layout),
                "--pet", str(outputs.pet),
                "--pet-name", "BUDDY",
                "--product-profile", str(outputs.product_profile),
                "--output", str(final),
            ]
        )
        self.assertEqual(result, 0)
        with Image.open(final) as image:
            self.assertEqual(image.size, (1600, 2400))
            self.assertAlmostEqual(image.info["dpi"][0], 300, delta=1)
        review = json.loads(
            (self.root / "print" / "final-print.manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(review["status"], "ready-for-final-print-review")
        self.assertEqual(review["output_sha256"], sha256(final))
        self.assertFalse(any(self.root.rglob("run.json")))
        self.assertFalse(any(self.root.rglob("approval.json")))

    def test_upscale_cli_accepts_explicit_manual_artifacts(self) -> None:
        result = upscale_main(
            [
                "--template-dir", str(self.template),
                "--transformed-pet", str(self.pet),
                "--product-profile", str(self.profile),
                "--output-dir", str(self.root / "print"),
            ]
        )
        self.assertEqual(result, 0)
        self.assertTrue((self.root / "print" / "layout-print.json").is_file())
        manifest = json.loads(
            (self.root / "print" / "print-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            Path(manifest["source"]["layout"]),
            (self.template / "layout.json").resolve(),
        )
        self.assertFalse(any(self.root.rglob("run.json")))
        self.assertFalse(any(self.root.rglob("approval.json")))

    def test_upscale_cli_accepts_target_size_without_profile(self) -> None:
        result = upscale_main(
            [
                "--template-dir", str(self.template),
                "--transformed-pet", str(self.pet),
                "--target-size", "1600x2400",
                "--output-dir", str(self.root / "print"),
            ]
        )
        self.assertEqual(result, 0)
        self.assertFalse((self.root / "print" / "product-profile.json").exists())

    def test_split_upscale_manifests_support_profile_validated_render(self) -> None:
        print_dir = self.root / "print"
        template_outputs = prepare_print_template(
            template_dir=self.template,
            target_size=(1600, 2400),
            output_dir=print_dir,
            product_profile=self.profile,
        )
        pet_outputs = prepare_print_pet(
            template_dir=self.template,
            transformed_pet=self.pet,
            print_layout_path=template_outputs.layout,
            output_dir=print_dir,
        )
        final = print_dir / "final-print.png"
        result = render_main(
            [
                "--template-dir", str(print_dir),
                "--layout", str(template_outputs.layout),
                "--pet", str(pet_outputs.pet),
                "--pet-name", "BUDDY",
                "--product-profile", str(template_outputs.product_profile),
                "--output", str(final),
            ]
        )
        self.assertEqual(result, 0)
        self.assertTrue(final.is_file())

    def test_upscale_cli_explains_profile_preview_aspect_mismatch(self) -> None:
        blanket_profile = create_product_profile(
            profile_id="blanket-king-9375x12375",
            print_size=ImageSize(9375, 12375),
            dpi=300,
        )
        profile_path = write_product_profile(
            self.root / "blanket-profile.json", blanket_profile
        )
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            upscale_main(
                [
                    "--template-dir", str(self.template),
                    "--transformed-pet", str(self.pet),
                    "--product-profile", str(profile_path),
                    "--output-dir", str(self.root / "print"),
                ]
            )
        message = stderr.getvalue()
        self.assertIn("preview art is 800x1200", message)
        self.assertIn("print target is 9375x12375", message)
        self.assertIn("requires preview art 800x1056", message)
        self.assertIn("does not resize art.png or recalculate layout.json", message)

    def test_changed_upscaled_layer_is_rejected(self) -> None:
        outputs = self._prepare()
        outputs.pet.write_bytes(b"changed")
        with self.assertRaisesRegex(PrintUpscaleError, "changed after upscale"):
            verify_print_bundle(
                template_dir=self.root / "print",
                layout_path=outputs.layout,
                transformed_pet=outputs.pet,
                name_image=None,
                product_profile=outputs.product_profile,
            )

    def test_changed_font_license_is_rejected(self) -> None:
        outputs = self._prepare()
        (self.root / "print" / "fonts" / "OFL.txt").write_text(
            "changed\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(PrintUpscaleError, "changed after upscale"):
            verify_print_bundle(
                template_dir=self.root / "print",
                layout_path=outputs.layout,
                transformed_pet=outputs.pet,
                name_image=None,
                product_profile=outputs.product_profile,
            )


if __name__ == "__main__":
    unittest.main()
