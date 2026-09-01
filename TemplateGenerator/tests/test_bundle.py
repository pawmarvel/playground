from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from helpers import copy_font, layout_data, make_image, make_transparent_mark
from pawmarvel_generator.bundle import BundleError, publish_bundle, validate_bundle


class BundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.template = self.root / "authoring"
        self.template.mkdir()
        make_transparent_mark(self.template / "art.png", size=(200, 300))
        fonts = self.template / "fonts"
        fonts.mkdir()
        copy_font(fonts)
        (self.template / "layout.json").write_text(
            json.dumps(layout_data()), encoding="utf-8"
        )
        self.exemplar = make_transparent_mark(self.root / "exemplar.png")
        self.reference = make_image(self.root / "sample.png")
        self.art_prompt = self.root / "art-template.md"
        self.art_prompt.write_text("Generate reusable fixed artwork.\n", encoding="utf-8")
        self.pet_prompt = self.root / "pet-transform.md"
        self.pet_prompt.write_text("Transform the user pet for this design.\n", encoding="utf-8")
        self.print_dir = self.root / "print"
        self.print_dir.mkdir()
        self.print_art = make_transparent_mark(
            self.print_dir / "art-print.png", size=(400, 600)
        )
        print_layout = layout_data()
        print_layout["art"] = "art-print.png"
        for section in ("pet", "name"):
            for key in ("x", "y", "width", "height"):
                print_layout[section]["box"][key] *= 2
        print_layout["name"]["font_size_px"] *= 2
        print_layout["name"]["min_font_size_px"] *= 2
        self.print_layout = self.print_dir / "layout-print.json"
        self.print_layout.write_text(json.dumps(print_layout), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_publishes_clean_contract_bundle(self) -> None:
        output = publish_bundle(
            template_dir=self.template,
            output_dir=self.root / "bundles",
            template_id="life-is-good",
            exemplar=self.exemplar,
            reference_design=self.reference,
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
        )
        layout = validate_bundle(output)
        self.assertEqual(layout.runtime_model, "gpt-image-2")
        self.assertEqual(layout.art_relative, "art.png")
        print_layout = json.loads((output / "layout-print.json").read_text())
        self.assertEqual(print_layout["art"], "print/art.png")
        with Image.open(output / "art.png") as preview_art:
            self.assertEqual(preview_art.size, (200, 300))
        with Image.open(output / "print" / "art.png") as print_art:
            self.assertEqual(print_art.size, (400, 600))
        self.assertTrue((output / "fonts" / "OFL.txt").is_file())
        self.assertTrue((output / "qa" / "transformed-pet.png").is_file())
        self.assertTrue((output / "reference-design.png").is_file())
        self.assertEqual(
            (output / "art-template.md").read_bytes(), self.art_prompt.read_bytes()
        )
        self.assertEqual(
            (output / "pet-transform.md").read_bytes(), self.pet_prompt.read_bytes()
        )
        self.assertEqual(
            {path.name for path in output.iterdir()},
            {
                "layout.json",
                "layout-print.json",
                "art.png",
                "print",
                "qa",
                "reference-design.png",
                "art-template.md",
                "pet-transform.md",
                "fonts",
            },
        )

    def test_can_publish_default_gemini_route_by_omitting_model(self) -> None:
        output = publish_bundle(
            template_dir=self.template,
            output_dir=self.root / "bundles",
            template_id="gemini-template",
            exemplar=self.exemplar,
            reference_design=self.reference,
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
            runtime_model=None,
        )
        self.assertNotIn("model", json.loads((output / "layout.json").read_text()))
        self.assertNotIn(
            "model", json.loads((output / "layout-print.json").read_text())
        )

    def test_publishes_preview_and_high_resolution_art_layout_pairs(self) -> None:
        output = publish_bundle(
            template_dir=self.template,
            output_dir=self.root / "bundles",
            template_id="high-resolution",
            exemplar=self.exemplar,
            reference_design=self.reference,
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
        )
        self.assertEqual(validate_bundle(output).canvas_width, 200)
        print_layout = json.loads((output / "layout-print.json").read_text())
        self.assertEqual(print_layout["pet"]["box"]["width"], 200)

    def test_rejects_print_layout_not_scaled_from_preview(self) -> None:
        data = json.loads(self.print_layout.read_text())
        data["pet"]["box"]["x"] += 1
        self.print_layout.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(BundleError, "scaled preview pet.box"):
            publish_bundle(
                template_dir=self.template,
                output_dir=self.root / "bundles",
                template_id="bad-print-layout",
                exemplar=self.exemplar,
                reference_design=self.reference,
                art_prompt=self.art_prompt,
                pet_prompt=self.pet_prompt,
                print_art=self.print_art,
                print_layout_path=self.print_layout,
            )

    def test_rejects_missing_ofl_license(self) -> None:
        (self.template / "fonts" / "OFL.txt").unlink()
        with self.assertRaisesRegex(BundleError, "OFL license"):
            publish_bundle(
                template_dir=self.template,
                output_dir=self.root / "bundles",
                template_id="missing-license",
                exemplar=self.exemplar,
                reference_design=self.reference,
                art_prompt=self.art_prompt,
                pet_prompt=self.pet_prompt,
                print_art=self.print_art,
                print_layout_path=self.print_layout,
            )

    def test_rejects_missing_finished_reference_design(self) -> None:
        with self.assertRaisesRegex(BundleError, "finished reference design"):
            publish_bundle(
                template_dir=self.template,
                output_dir=self.root / "bundles",
                template_id="missing-reference",
                exemplar=self.exemplar,
                reference_design=self.root / "missing.png",
                art_prompt=self.art_prompt,
                pet_prompt=self.pet_prompt,
                print_art=self.print_art,
                print_layout_path=self.print_layout,
            )

    def test_validator_rejects_unexpected_nested_content(self) -> None:
        output = publish_bundle(
            template_dir=self.template,
            output_dir=self.root / "bundles",
            template_id="test-template",
            exemplar=self.exemplar,
            reference_design=self.reference,
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
        )
        (output / "qa" / "unexpected").mkdir()
        with self.assertRaisesRegex(BundleError, "qa must contain exactly"):
            validate_bundle(output)

    def test_rejects_missing_design_prompt(self) -> None:
        with self.assertRaisesRegex(BundleError, "art template prompt"):
            publish_bundle(
                template_dir=self.template,
                output_dir=self.root / "bundles",
                template_id="missing-prompt",
                exemplar=self.exemplar,
                reference_design=self.reference,
                art_prompt=self.root / "missing.md",
                pet_prompt=self.pet_prompt,
                print_art=self.print_art,
                print_layout_path=self.print_layout,
            )

    def test_validator_requires_both_prompt_artifacts(self) -> None:
        output = publish_bundle(
            template_dir=self.template,
            output_dir=self.root / "bundles",
            template_id="prompt-contract",
            exemplar=self.exemplar,
            reference_design=self.reference,
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
        )
        (output / "pet-transform.md").unlink()
        with self.assertRaisesRegex(BundleError, "pet-transform.md"):
            validate_bundle(output)


if __name__ == "__main__":
    unittest.main()
