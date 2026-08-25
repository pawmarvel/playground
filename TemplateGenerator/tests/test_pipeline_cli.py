from __future__ import annotations

import argparse
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from helpers import copy_font, layout_data, make_image
from pawmarvel_generator.cli import _atomic_write_bytes
from pawmarvel_generator.pipeline_cli import PipelineError, build_parser, run_pipeline


def runtime_prompt(marker: str) -> str:
    return f"""Use the single uploaded pet photo as the identity reference for this {marker} result. Preserve the recognizable breed, facial proportions, muzzle length, nose shape, eye spacing, ear construction, coat length, primary markings, and distinctive asymmetry. Reconstruct a centered, eye-level, front-facing head-and-upper-chest portrait. Keep both ears complete, use a friendly alert expression with a relaxed open mouth, and preserve a clean compact silhouette with comfortable clearance around all fur edges.

Render the pet as a coarse vintage apparel screen print using two flat ink separations: warm cream and deep navy. Translate markings into flat value shapes and negative space. Do not use natural brown, tan, orange, photographic colors, gradients, or smooth airbrushed transitions. Construct fur from grouped hand-drawn strokes, simplified high-contrast shadow masses, sparse hatching, and moderately weathered ink loss. Emphasize eyes, nose, muzzle, ears, and defining markings. Avoid photorealism, engraving density, watercolor, glossy 3D rendering, anime, or generic mascot styling.

Output exactly one isolated pet on a fully transparent background with a genuine alpha channel. Do not include words, names, slogans, fixed decorative artwork, scenery, borders, frames, garments, product mockups, checkerboards, colored backdrops, rectangular planes, accessories, or additional animals. Keep the intended portrait unclipped and ready for local compositing."""


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


class CombinedResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls)
        return SimpleNamespace(
            id=f"resp_pipeline_{index}", output_text=runtime_prompt(str(index))
        )


class CombinedClient:
    def __init__(self) -> None:
        self.images = CombinedImages()
        self.responses = CombinedResponses()


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
        self.font = copy_font(self.root)
        self.key = self.root / "OPENAI_API_KEY.rtf"
        self.key.write_text("sk-test_1234567890abcdefghij", encoding="utf-8")
        self.template = self.root / "template"
        self.run = self.root / "run"
        self.parser = build_parser()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self, name_method: str = "font", *extra: str) -> argparse.Namespace:
        return self.parser.parse_args(
            [
                "--sample-design",
                str(self.sample),
                "--art-prompt",
                str(self.art_prompt),
                "--pet-image",
                str(self.pet),
                "--pet-name",
                "SAUSAGE",
                "--name-method",
                name_method,
                "--font",
                str(self.font),
                "--template-dir",
                str(self.template),
                "--run-dir",
                str(self.run),
                "--api-key-file",
                str(self.key),
                "--art-size",
                "200x300",
                "--pet-size",
                "64x64",
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
        self.assertEqual(len(client.responses.calls), 2)
        for key in (
            "art",
            "pet_prompt",
            "layout",
            "transformed_pet",
            "preview",
            "preview_debug",
            "layout_snapshot",
            "manifest",
        ):
            self.assertTrue(outputs[key].is_file(), key)
        record = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(record["name_generation"]["method"], "font")
        self.assertEqual(record["pipeline"]["pet_name"], "SAUSAGE")
        self.assertNotIn("sk-test", outputs["manifest"].read_text(encoding="utf-8"))

    def test_ai_name_pipeline_generates_name_asset(self) -> None:
        client = CombinedClient()

        outputs = run_pipeline(
            self.args("ai"), client=client, layout_runner=self.save_layout
        )

        self.assertEqual(client.images.call_count, 3)
        self.assertTrue(outputs["generated_name"].is_file())
        record = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(record["name_generation"]["method"], "ai")
        self.assertTrue(Path(record["name_generation"]["prompt"]).is_file())
        self.assertTrue((self.template / "name-generation.json").is_file())

    def test_preflight_stops_before_paid_calls(self) -> None:
        self.run.mkdir()
        (self.run / "preview.png").write_bytes(b"existing")
        client = CombinedClient()

        with self.assertRaisesRegex(PipelineError, "--force"):
            run_pipeline(self.args(), client=client, layout_runner=self.save_layout)

        self.assertEqual(client.images.call_count, 0)
        self.assertEqual(len(client.responses.calls), 0)

    def test_dry_run_only_prints_plan(self) -> None:
        outputs = run_pipeline(
            self.args("font", "--dry-run"),
            client=CombinedClient(),
            layout_runner=self.save_layout,
        )
        self.assertEqual(outputs["template_dir"], self.template.resolve())
        self.assertFalse(self.template.exists())


if __name__ == "__main__":
    unittest.main()
