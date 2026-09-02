from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from helpers import copy_font, layout_data, make_image
from pawmarvel_generator.bundle import load_catalog, validate_bundle
from pawmarvel_generator.cli import _atomic_write_bytes
from pawmarvel_generator.pipeline_cli import PipelineError, build_parser, run_pipeline
from pawmarvel_generator.image_size import ImageSize
from pawmarvel_generator.product_profile import (
    create_product_profile,
    write_product_profile,
)


class CombinedImages:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def edit(self, **kwargs):
        self.calls.append(kwargs)
        width, height = (int(value) for value in kwargs["size"].split("x", 1))
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle(
            (width // 5, height // 5, width * 4 // 5, height * 4 // 5),
            fill=(80, 120, 180, 255),
        )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=encoded)])


class CombinedClient:
    def __init__(self) -> None:
        self.images = CombinedImages()


class PipelineCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sample = make_image(self.root / "sample.png", size=(200, 300))
        self.pet = make_image(self.root / "pet.png", size=(80, 80))
        self.art_prompt = self.root / "art-prompt.md"
        self.art_prompt.write_text(
            "Create fixed background artwork only on a transparent background.",
            encoding="utf-8",
        )
        self.pet_prompt = self.root / "pet-transform.md"
        self.pet_prompt.write_text(
            "Use the user pet for identity and the finished reference design for "
            "style, pose, expression, and crop. Return transparent pet artwork.",
            encoding="utf-8",
        )
        self.font = copy_font(self.root)
        self.key = self.root / "OPENAI_API_KEY.rtf"
        self.key.write_text("sk-test_1234567890abcdefghij", encoding="utf-8")
        self.template = self.root / "template"
        self.run = self.root / "run"
        self.parser = build_parser()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self, *extra: str) -> argparse.Namespace:
        return self.parser.parse_args(
            [
                "--sample-design",
                str(self.sample),
                "--art-prompt",
                str(self.art_prompt),
                "--pet-prompt",
                str(self.pet_prompt),
                "--pet-image",
                str(self.pet),
                "--pet-name",
                "SAUSAGE",
                "--font",
                str(self.font),
                "--template-dir",
                str(self.template),
                "--run-dir",
                str(self.run),
                "--api-key-file",
                str(self.key),
                "--art-resolution",
                "800x1200",
                "--pet-size",
                "816x816",
                "--quality",
                "low",
                *extra,
            ]
        )

    @staticmethod
    def save_layout(config, **kwargs) -> None:
        fonts = config.output.parent / "fonts"
        fonts.mkdir(parents=True, exist_ok=True)
        bundled = fonts / config.font.name
        _atomic_write_bytes(bundled, config.font.read_bytes())
        assert config.font_license is not None
        _atomic_write_bytes(fonts / "OFL.txt", config.font_license.read_bytes())
        config.output.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(
            config.output,
            (json.dumps(layout_data(f"fonts/{config.font.name}")) + "\n").encode(),
        )
        make_image(
            config.output.parent / "qa" / "calibration-preview.png",
            size=(200, 300),
        )

    def test_font_pipeline_creates_template_and_tracked_run(self) -> None:
        client = CombinedClient()

        outputs = run_pipeline(
            self.args(), client=client, layout_runner=self.save_layout
        )

        self.assertEqual(client.images.call_count, 2)
        for key in (
            "art",
            "layout",
            "transformed_pet",
            "preview",
            "preview_debug",
            "layout_snapshot",
            "manifest",
        ):
            self.assertTrue(outputs[key].is_file(), key)
        record = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(record["pipeline"]["pet_name"], "SAUSAGE")
        self.assertEqual(record["pipeline"]["runtime_model"], "gpt-image-2")
        self.assertIsNotNone(record["artifact_sha256"]["font"])
        self.assertIsNotNone(record["artifact_sha256"]["font_license"])
        self.assertEqual(
            record["sources"]["pet_prompt"]["sha256"],
            hashlib.sha256(self.pet_prompt.read_bytes()).hexdigest(),
        )
        self.assertNotIn("sk-test", outputs["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(
            [Path(file.name).name for file in client.images.calls[1]["image"]],
            ["input-pet.png", "source-reference-design.png"],
        )

    def test_pipeline_preserves_and_forwards_ordered_reference_designs(self) -> None:
        supporting = make_image(self.root / "supporting.jpg")
        client = CombinedClient()

        outputs = run_pipeline(
            self.args("--sample-design", str(supporting)),
            client=client,
            layout_runner=self.save_layout,
        )

        staged_supporting = (
            self.template
            / "source-reference-designs"
            / "reference-design-0002.jpg"
        )
        self.assertTrue(staged_supporting.is_file())
        self.assertEqual(
            [Path(file.name).name for file in client.images.calls[0]["image"]],
            ["source-reference-design.png", "reference-design-0002.jpg"],
        )
        self.assertEqual(
            [Path(file.name).name for file in client.images.calls[1]["image"]],
            [
                "input-pet.png",
                "source-reference-design.png",
                "reference-design-0002.jpg",
            ],
        )
        record = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(len(record["sources"]["sample_designs"]), 2)
        self.assertEqual(
            [item["role"] for item in record["sources"]["sample_designs"]],
            ["primary", "supporting"],
        )
        self.assertEqual(len(record["sources"]["staged_source_references"]), 2)

    def test_preflight_stops_before_paid_calls(self) -> None:
        self.run.mkdir()
        (self.run / "preview.png").write_bytes(b"existing")
        client = CombinedClient()

        with self.assertRaisesRegex(PipelineError, "--force"):
            run_pipeline(self.args(), client=client, layout_runner=self.save_layout)

        self.assertEqual(client.images.call_count, 0)

    def test_invalid_font_catalog_stops_before_paid_calls(self) -> None:
        catalog = self.root / "font-catalog" / "unlicensed"
        catalog.mkdir(parents=True)
        (catalog / "Unlicensed.ttf").write_bytes(self.font.read_bytes())
        client = CombinedClient()

        with self.assertRaisesRegex(PipelineError, "OFL"):
            run_pipeline(
                self.args("--font-catalog", str(catalog.parent)),
                client=client,
                layout_runner=self.save_layout,
            )

        self.assertEqual(client.images.call_count, 0)

    def test_rerun_art_only_reuses_pet_and_layout(self) -> None:
        run_pipeline(
            self.args(), client=CombinedClient(), layout_runner=self.save_layout
        )
        previous_pet = hashlib.sha256(
            (self.run / "transformed-pet.png").read_bytes()
        ).hexdigest()
        self.art_prompt.write_text(
            "Create a revised fixed background on transparency.", encoding="utf-8"
        )
        client = CombinedClient()

        def unexpected_layout(*args, **kwargs) -> None:
            self.fail("art-only rerun must not open the layout editor")

        outputs = run_pipeline(
            self.args("--rerun-step", "art"),
            client=client,
            layout_runner=unexpected_layout,
        )

        self.assertEqual(client.images.call_count, 1)
        self.assertEqual(
            previous_pet,
            hashlib.sha256(outputs["transformed_pet"].read_bytes()).hexdigest(),
        )
        record = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(record["pipeline"]["run_mode"], "selective-rerun")
        self.assertEqual(record["pipeline"]["rerun_steps"], ["art"])

        self.pet_prompt.write_text("Changed pet instructions.", encoding="utf-8")
        rejected_client = CombinedClient()
        with self.assertRaisesRegex(PipelineError, "include --rerun-step pet"):
            run_pipeline(
                self.args("--rerun-step", "art"),
                client=rejected_client,
                layout_runner=unexpected_layout,
            )
        self.assertEqual(rejected_client.images.call_count, 0)

    def test_rerun_pet_only_accepts_new_pet_source(self) -> None:
        run_pipeline(
            self.args(), client=CombinedClient(), layout_runner=self.save_layout
        )
        previous_art = hashlib.sha256(
            (self.template / "art.png").read_bytes()
        ).hexdigest()
        replacement = make_image(self.root / "replacement-pet.png", size=(96, 72))
        rerun = self.args("--rerun-step", "pet")
        rerun.pet_image = replacement
        client = CombinedClient()

        outputs = run_pipeline(rerun, client=client, layout_runner=self.save_layout)

        self.assertEqual(client.images.call_count, 1)
        self.assertEqual(
            previous_art,
            hashlib.sha256(outputs["art"].read_bytes()).hexdigest(),
        )
        self.assertEqual(
            [Path(file.name).name for file in client.images.calls[0]["image"]],
            ["input-pet.png", "source-reference-design.png"],
        )

    def test_rerun_layout_only_does_not_need_api_key(self) -> None:
        run_pipeline(
            self.args(), client=CombinedClient(), layout_runner=self.save_layout
        )
        self.key.unlink()
        client = CombinedClient()
        layout_calls = 0

        def save_again(config, **kwargs) -> None:
            nonlocal layout_calls
            layout_calls += 1
            self.save_layout(config, **kwargs)

        outputs = run_pipeline(
            self.args("--rerun-step", "layout"),
            client=client,
            layout_runner=save_again,
        )

        self.assertEqual(layout_calls, 1)
        self.assertEqual(client.images.call_count, 0)
        record = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
        self.assertIsNone(record["pipeline"]["api_key_source"])

    def test_selective_rerun_requires_previous_run_and_rejects_force(self) -> None:
        client = CombinedClient()
        with self.assertRaisesRegex(PipelineError, "existing pipeline manifest"):
            run_pipeline(
                self.args("--rerun-step", "art"),
                client=client,
                layout_runner=self.save_layout,
            )
        self.assertEqual(client.images.call_count, 0)

        with self.assertRaisesRegex(PipelineError, "cannot be combined"):
            run_pipeline(
                self.args("--rerun-step", "art", "--force"),
                client=client,
                layout_runner=self.save_layout,
            )

    def test_selective_rerun_rejects_changed_supporting_reference(self) -> None:
        supporting = make_image(self.root / "supporting.png")
        run_pipeline(
            self.args("--sample-design", str(supporting)),
            client=CombinedClient(),
            layout_runner=self.save_layout,
        )
        make_image(supporting, color=(10, 20, 30, 255))
        client = CombinedClient()

        with self.assertRaisesRegex(PipelineError, "sample_designs"):
            run_pipeline(
                self.args(
                    "--sample-design",
                    str(supporting),
                    "--rerun-step",
                    "pet",
                ),
                client=client,
                layout_runner=self.save_layout,
            )

        self.assertEqual(client.images.call_count, 0)

    def test_dry_run_only_prints_plan(self) -> None:
        outputs = run_pipeline(
            self.args("--dry-run"),
            client=CombinedClient(),
            layout_runner=self.save_layout,
        )
        self.assertEqual(outputs["template_dir"], self.template.resolve())
        self.assertFalse(self.template.exists())

    def test_art_resolution_controls_output_instead_of_sample_dimensions(self) -> None:
        client = CombinedClient()
        args = self.args()
        args.art_size = "816x1216"
        outputs = run_pipeline(args, client=client, layout_runner=self.save_layout)
        with Image.open(outputs["art"]) as art:
            self.assertEqual(art.size, (816, 1216))
        self.assertEqual(client.images.calls[0]["size"], "816x1216")

    def test_art_resolution_is_required(self) -> None:
        with self.assertRaises(SystemExit):
            self.parser.parse_args(
                [
                    "--sample-design",
                    str(self.sample),
                    "--art-prompt",
                    str(self.art_prompt),
                    "--pet-prompt",
                    str(self.pet_prompt),
                    "--pet-image",
                    str(self.pet),
                    "--pet-name",
                    "SAUSAGE",
                    "--font",
                    str(self.font),
                    "--template-dir",
                    str(self.template),
                ]
            )

    def test_product_profile_preserves_reference_and_controls_layer_sizes(self) -> None:
        profile_path = write_product_profile(
            self.root / "product.json",
            create_product_profile(
                profile_id="blanket-king-9375x12375",
                print_size=ImageSize(9375, 12375),
            ),
        )
        values = [
            "--sample-design", str(self.sample),
            "--art-prompt", str(self.art_prompt),
            "--pet-prompt", str(self.pet_prompt),
            "--pet-image", str(self.pet),
            "--pet-name", "SAUSAGE",
            "--font", str(self.font),
            "--template-dir", str(self.template),
            "--run-dir", str(self.run),
            "--api-key-file", str(self.key),
            "--product-profile", str(profile_path),
            "--quality", "low",
        ]
        client = CombinedClient()
        editor_inputs: dict[str, Path] = {}

        def save_profile_layout(config, **kwargs) -> None:
            editor_inputs["reference"] = config.reference
            self.save_layout(config, **kwargs)

        outputs = run_pipeline(
            self.parser.parse_args(values),
            client=client,
            layout_runner=save_profile_layout,
        )
        with Image.open(self.template / "source-reference-design.png") as source:
            self.assertEqual(source.size, (200, 300))
        self.assertFalse((self.template / "reference-aligned.png").exists())
        self.assertEqual(
            editor_inputs["reference"],
            (self.template / "source-reference-design.png").resolve(),
        )
        self.assertEqual(client.images.calls[0]["size"], "800x1056")
        self.assertEqual(
            Path(client.images.calls[0]["image"][0].name),
            (self.template / "source-reference-design.png").resolve(),
        )
        self.assertEqual(client.images.calls[1]["size"], "816x816")
        record = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(record["pipeline"]["print_size"], "9375x12375")
        self.assertNotIn("approval", record)
        self.assertNotIn("approval_artifacts", record)
        self.assertIsNotNone(record["artifact_sha256"]["product_profile"])
        self.assertEqual(
            record["sources"]["staged_source_reference"]["path"],
            str((self.template / "source-reference-design.png").resolve()),
        )
        self.assertEqual(
            record["sources"]["staged_source_reference"]["role"],
            "visual-context-only-not-layout-geometry",
        )
        self.assertNotIn("aligned_reference", record["sources"])
        self.assertFalse((self.template / "pet-transform.md").exists())
        self.assertFalse((self.template / "pet-transform.analysis.json").exists())

    def test_product_profile_rejects_pet_size_override(self) -> None:
        profile_path = write_product_profile(
            self.root / "product.json",
            create_product_profile(
                profile_id="profile-pet-size",
                print_size=ImageSize(9375, 12375),
            ),
        )
        args = self.parser.parse_args(
            [
                "--sample-design", str(self.sample),
                "--art-prompt", str(self.art_prompt),
                "--pet-prompt", str(self.pet_prompt),
                "--pet-image", str(self.pet),
                "--pet-name", "SAUSAGE",
                "--font", str(self.font),
                "--template-dir", str(self.template),
                "--api-key-file", str(self.key),
                "--product-profile", str(profile_path),
                "--pet-size", "1024x1024",
            ]
        )
        with self.assertRaisesRegex(PipelineError, "cannot override"):
            run_pipeline(args, client=CombinedClient(), layout_runner=self.save_layout)

    def test_profile_pipeline_runs_through_print_and_bundle_publication(self) -> None:
        supporting = make_image(self.root / "supporting-reference.png")
        profile_path = write_product_profile(
            self.root / "product.json",
            create_product_profile(
                profile_id="test-blanket",
                print_size=ImageSize(1600, 2400),
                dpi=300,
            ),
        )
        print_dir = self.root / "print"
        bundles = self.root / "bundles"
        args = self.parser.parse_args(
            [
                "--sample-design", str(self.sample),
                "--sample-design", str(supporting),
                "--art-prompt", str(self.art_prompt),
                "--pet-prompt", str(self.pet_prompt),
                "--pet-image", str(self.pet),
                "--pet-name", "SAUSAGE",
                "--font", str(self.font),
                "--template-dir", str(self.template),
                "--run-dir", str(self.run),
                "--api-key-file", str(self.key),
                "--product-profile", str(profile_path),
                "--quality", "low",
                "--print-dir", str(print_dir),
                "--bundle-output-dir", str(bundles),
                "--design-id", "life-is-good",
            ]
        )
        client = CombinedClient()

        outputs = run_pipeline(
            args,
            client=client,
            layout_runner=self.save_layout,
        )

        self.assertEqual(client.images.call_count, 2)
        for key in (
            "print_art",
            "print_transformed_pet",
            "print_layout",
            "template_print_manifest",
            "print_pet_manifest",
            "final_print",
            "final_print_debug",
            "bundle",
        ):
            self.assertTrue(outputs[key].exists(), key)
        self.assertEqual(
            outputs["template_print_manifest"].name,
            "template-print-manifest.json",
        )
        self.assertEqual(outputs["print_pet_manifest"].name, "pet-print-manifest.json")
        self.assertFalse((print_dir / "print-manifest.json").exists())
        with Image.open(outputs["final_print"]) as final:
            self.assertEqual(final.size, (1600, 2400))
            self.assertAlmostEqual(final.info["dpi"][0], 300, delta=1)
        self.assertEqual(validate_bundle(outputs["bundle"]).canvas_width, 672)
        self.assertEqual(
            {path.name for path in outputs["bundle"].iterdir()},
            {
                "art.png",
                "layout.json",
                "layout-print.json",
                "print",
                "reference-design.png",
                "reference-designs",
                "art-template.md",
                "pet-transform.md",
                "qa",
                "fonts",
            },
        )
        record = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(record["publication"]["status"], "published")
        self.assertEqual(
            record["publication"]["template_id"], "life-is-good--test-blanket"
        )
        self.assertEqual(record["publication"]["design_id"], "life-is-good")
        self.assertEqual(
            record["publication"]["product_profile_id"], "test-blanket"
        )
        catalog = load_catalog(bundles)
        self.assertEqual(
            catalog["templates"][0]["template_id"],
            "life-is-good--test-blanket",
        )
        self.assertEqual(
            catalog["templates"][0]["reference_designs"],
            [
                "reference-design.png",
                "reference-designs/reference-design-0002.png",
            ],
        )
        self.assertEqual(
            record["publication"]["catalog"],
            str((bundles / "catalog.json").resolve()),
        )
        self.assertEqual(outputs["catalog"], (bundles / "catalog.json").resolve())
        self.assertEqual(
            record["publication"]["bundle"], str(outputs["bundle"])
        )
        self.assertIsNotNone(record["artifact_sha256"]["print_art"])
        self.assertIsNotNone(record["artifact_sha256"]["final_print"])
        self.assertEqual(
            (outputs["bundle"] / "art-template.md").read_bytes(),
            self.art_prompt.read_bytes(),
        )
        self.assertEqual(
            (outputs["bundle"] / "pet-transform.md").read_bytes(),
            self.pet_prompt.read_bytes(),
        )

        rerun_client = CombinedClient()
        args.rerun_step = ["pet"]
        rerun_outputs = run_pipeline(
            args,
            client=rerun_client,
            layout_runner=lambda *args, **kwargs: self.fail(
                "pet-only rerun must not open the layout editor"
            ),
        )
        self.assertEqual(rerun_client.images.call_count, 1)
        self.assertTrue(rerun_outputs["final_print"].is_file())
        self.assertEqual(validate_bundle(rerun_outputs["bundle"]).canvas_width, 672)

    def test_bundle_publication_requires_product_profile_before_paid_calls(self) -> None:
        client = CombinedClient()
        args = self.args(
            "--design-id", "life-is-good",
            "--bundle-output-dir", str(self.root / "bundles"),
        )

        with self.assertRaisesRegex(PipelineError, "requires --product-profile"):
            run_pipeline(args, client=client, layout_runner=self.save_layout)

        self.assertEqual(client.images.call_count, 0)


if __name__ == "__main__":
    unittest.main()
