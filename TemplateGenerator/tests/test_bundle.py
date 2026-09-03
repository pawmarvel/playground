from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from helpers import copy_font, layout_data, make_image, make_transparent_mark
from pawmarvel_generator.bundle import (
    BundleError,
    load_catalog,
    publish_bundle,
    validate_bundle,
)
from pawmarvel_generator.image_size import ImageSize
from pawmarvel_generator.product_profile import (
    create_product_profile,
    write_product_profile,
)


class BundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.template = self.root / "authoring"
        self.template.mkdir()
        make_transparent_mark(self.template / "art.png", size=(672, 1008))
        fonts = self.template / "fonts"
        fonts.mkdir()
        copy_font(fonts)
        (self.template / "layout.json").write_text(
            json.dumps(layout_data()), encoding="utf-8"
        )
        self.exemplar = make_transparent_mark(self.root / "exemplar.png")
        self.reference = make_image(self.root / "sample.png")
        self.art_prompt = self.root / "art-template-gpt.md"
        self.art_prompt.write_text("Generate reusable fixed artwork.\n", encoding="utf-8")
        self.pet_prompt = self.root / "pet-transform-gpt.md"
        self.pet_prompt.write_text("Transform the user pet for this design.\n", encoding="utf-8")
        self.pet_prompt_gemini = self.root / "pet-transform-gemini.md"
        self.pet_prompt_gemini.write_text(
            "Transform the user pet for this design with Gemini.\n",
            encoding="utf-8",
        )
        self.print_dir = self.root / "print"
        self.print_dir.mkdir()
        self.print_art = make_transparent_mark(
            self.print_dir / "art-print.png", size=(1344, 2016)
        )
        self.product_profile = write_product_profile(
            self.root / "product-profile.json",
            create_product_profile(
                profile_id="test-blanket",
                print_size=ImageSize(1344, 2016),
            ),
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
            design_id="life-is-good",
            product_profile=self.product_profile,
            exemplar=self.exemplar,
            reference_design=self.reference,
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
        )
        layout = validate_bundle(output)
        self.assertEqual(output.name, "life-is-good--test-blanket")
        catalog = load_catalog(self.root / "bundles")
        entry = catalog["templates"][0]
        self.assertEqual(entry["design_id"], "life-is-good")
        self.assertEqual(entry["product_profile_id"], "test-blanket")
        self.assertEqual(entry["template_id"], "life-is-good--test-blanket")
        self.assertEqual(entry["design"], {"id": "life-is-good"})
        self.assertEqual(entry["reference_designs"], ["reference-design.png"])
        self.assertEqual(
            entry["prompts"],
            {
                "art_template": "art-template-gpt.md",
                "pet_transform": "pet-transform-gpt.md",
            },
        )
        self.assertEqual(
            entry["product_profile"]["profile_id"], "test-blanket"
        )
        self.assertEqual(entry["preview"]["canvas"], {"width": 672, "height": 1008})
        self.assertEqual(entry["print"]["canvas"], {"width": 1344, "height": 2016})
        self.assertEqual(layout.runtime_model, "gpt-image-2")
        self.assertEqual(layout.art_relative, "art.png")
        print_layout = json.loads((output / "layout-print.json").read_text())
        self.assertEqual(print_layout["art"], "print/art.png")
        with Image.open(output / "art.png") as preview_art:
            self.assertEqual(preview_art.size, (672, 1008))
        with Image.open(output / "print" / "art.png") as print_art:
            self.assertEqual(print_art.size, (1344, 2016))
        self.assertTrue((output / "fonts" / "OFL.txt").is_file())
        self.assertTrue((output / "qa" / "transformed-pet.png").is_file())
        self.assertTrue((output / "reference-design.png").is_file())
        self.assertEqual(
            (output / "art-template-gpt.md").read_bytes(), self.art_prompt.read_bytes()
        )
        self.assertEqual(
            (output / "pet-transform-gpt.md").read_bytes(), self.pet_prompt.read_bytes()
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
                "art-template-gpt.md",
                "pet-transform-gpt.md",
                "fonts",
            },
        )

    def test_publishes_ordered_supporting_reference_designs(self) -> None:
        second = make_image(self.root / "second.jpg")
        third = make_image(self.root / "third.png")

        output = publish_bundle(
            template_dir=self.template,
            output_dir=self.root / "bundles",
            design_id="multiple-references",
            product_profile=self.product_profile,
            exemplar=self.exemplar,
            reference_design=[self.reference, second, third],
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
        )

        validate_bundle(output)
        supporting = output / "reference-designs"
        self.assertEqual(
            [path.name for path in sorted(supporting.iterdir())],
            ["reference-design-0002.png", "reference-design-0003.png"],
        )
        with Image.open(output / "reference-design.png") as primary:
            self.assertEqual(primary.format, "PNG")
        with Image.open(supporting / "reference-design-0002.png") as reference:
            self.assertEqual(reference.format, "PNG")
        catalog = load_catalog(self.root / "bundles")
        self.assertEqual(
            catalog["templates"][0]["reference_designs"],
            [
                "reference-design.png",
                "reference-designs/reference-design-0002.png",
                "reference-designs/reference-design-0003.png",
            ],
        )

    def test_rejects_empty_reference_design_list(self) -> None:
        with self.assertRaisesRegex(BundleError, "at least one"):
            publish_bundle(
                template_dir=self.template,
                output_dir=self.root / "bundles",
                design_id="missing-references",
                product_profile=self.product_profile,
                exemplar=self.exemplar,
                reference_design=[],
                art_prompt=self.art_prompt,
                pet_prompt=self.pet_prompt,
                print_art=self.print_art,
                print_layout_path=self.print_layout,
            )

    def test_can_publish_default_gemini_route_by_omitting_model(self) -> None:
        output = publish_bundle(
            template_dir=self.template,
            output_dir=self.root / "bundles",
            design_id="gemini-template",
            product_profile=self.product_profile,
            exemplar=self.exemplar,
            reference_design=self.reference,
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt_gemini,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
            runtime_model=None,
        )
        self.assertNotIn("model", json.loads((output / "layout.json").read_text()))
        self.assertNotIn(
            "model", json.loads((output / "layout-print.json").read_text())
        )
        self.assertTrue((output / "pet-transform-gemini.md").is_file())

    def test_rejects_prompt_category_that_disagrees_with_runtime(self) -> None:
        with self.assertRaisesRegex(BundleError, "does not match the gemini"):
            publish_bundle(
                template_dir=self.template,
                output_dir=self.root / "bundles",
                design_id="mismatched-prompt",
                product_profile=self.product_profile,
                exemplar=self.exemplar,
                reference_design=self.reference,
                art_prompt=self.art_prompt,
                pet_prompt=self.pet_prompt,
                print_art=self.print_art,
                print_layout_path=self.print_layout,
                runtime_model=None,
            )

    def test_catalog_distinguishes_product_variants_for_one_design(self) -> None:
        alternate_profile = write_product_profile(
            self.root / "alternate-profile.json",
            create_product_profile(
                profile_id="alternate-blanket",
                print_size=ImageSize(1344, 2016),
            ),
        )
        common = {
            "template_dir": self.template,
            "output_dir": self.root / "bundles",
            "design_id": "life-is-good",
            "exemplar": self.exemplar,
            "reference_design": self.reference,
            "art_prompt": self.art_prompt,
            "pet_prompt": self.pet_prompt,
            "print_art": self.print_art,
            "print_layout_path": self.print_layout,
        }

        first = publish_bundle(product_profile=self.product_profile, **common)
        second = publish_bundle(product_profile=alternate_profile, **common)

        self.assertNotEqual(first, second)
        catalog = load_catalog(self.root / "bundles")
        self.assertEqual(
            [entry["template_id"] for entry in catalog["templates"]],
            [
                "life-is-good--alternate-blanket",
                "life-is-good--test-blanket",
            ],
        )

    def test_catalog_rejects_product_metadata_that_disagrees_with_summary(self) -> None:
        publish_bundle(
            template_dir=self.template,
            output_dir=self.root / "bundles",
            design_id="life-is-good",
            product_profile=self.product_profile,
            exemplar=self.exemplar,
            reference_design=self.reference,
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
        )
        catalog_path = self.root / "bundles" / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["templates"][0]["preview"]["canvas"]["width"] = 999
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

        with self.assertRaisesRegex(BundleError, "summary dimensions"):
            load_catalog(self.root / "bundles")

    def test_publishes_preview_and_high_resolution_art_layout_pairs(self) -> None:
        output = publish_bundle(
            template_dir=self.template,
            output_dir=self.root / "bundles",
            design_id="high-resolution",
            product_profile=self.product_profile,
            exemplar=self.exemplar,
            reference_design=self.reference,
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
        )
        self.assertEqual(validate_bundle(output).canvas_width, 672)
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
                design_id="bad-print-layout",
                product_profile=self.product_profile,
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
                design_id="missing-license",
                product_profile=self.product_profile,
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
                design_id="missing-reference",
                product_profile=self.product_profile,
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
            design_id="test-template",
            product_profile=self.product_profile,
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
                design_id="missing-prompt",
                product_profile=self.product_profile,
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
            design_id="prompt-contract",
            product_profile=self.product_profile,
            exemplar=self.exemplar,
            reference_design=self.reference,
            art_prompt=self.art_prompt,
            pet_prompt=self.pet_prompt,
            print_art=self.print_art,
            print_layout_path=self.print_layout,
        )
        (output / "pet-transform-gpt.md").unlink()
        with self.assertRaisesRegex(BundleError, "exactly one pet-transform"):
            validate_bundle(output)


if __name__ == "__main__":
    unittest.main()
