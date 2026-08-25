from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from helpers import make_image
from pawmarvel_generator.pet_prompt_cli import (
    PetPromptError,
    build_parser,
    derive_prompt,
)


def analysis_data() -> dict:
    return {
        "style": {
            "medium": "distressed single-ink screen-print illustration",
            "palette": "warm cream ink with transparent negative space",
            "linework": "hand-drawn grouped fur strokes and firm facial contours",
            "shading": "sparse hatching and simplified high-contrast shadow shapes",
            "texture": "weathered vintage ink loss without a background texture",
            "detail_emphasis": "eyes, nose, muzzle, ears, and recognizable fur pattern",
        },
        "pose": {
            "viewpoint": "front-facing at eye level",
            "head_and_body_orientation": "head centered and upright over the chest",
            "visible_anatomy": "head, ears, neck, and upper chest",
            "crop_boundaries": "crop below the upper chest; retain both complete ears",
            "expression": "friendly alert expression with a relaxed open mouth",
            "eyes_mouth_and_ears": "eyes toward viewer, mouth open, natural ears visible",
            "composition_and_silhouette": "centered near-symmetrical portrait",
        },
        "identity_priorities": [
            "preserve facial proportions",
            "preserve muzzle length",
            "preserve eye shape and spacing",
            "preserve ear shape",
            "preserve coat markings",
        ],
        "example_exclusions": [
            "exclude the example pet's markings",
            "exclude all lettering and fixed artwork",
        ],
    }


def runtime_prompt(marker: str = "draft") -> str:
    return f"""# GPT Image 2 Pet Transformation — {marker}

Use the single uploaded pet photo as the identity reference. Generate one isolated pet portrait and preserve the recognizable identity, breed characteristics, facial proportions, muzzle length, nose shape, eye shape and spacing, natural ear construction, fur length, major markings, and distinctive asymmetry. Reconstruct only the target posture and expression: a centered, eye-level, front-facing head-and-upper-chest portrait with the complete ears visible, a friendly relaxed open mouth, and an unclipped compact silhouette.

Render the animal as a coarse vintage apparel screen print. Use exactly two flat ink separations: warm cream and deep navy. Translate coat markings through flat value shapes and negative space. Do not use natural brown, tan, orange, photographic color, gradients, or smooth airbrushed transitions. Construct the fur from grouped hand-drawn strokes, simplified high-contrast shadow masses, sparse hatching, and moderately weathered ink loss. Emphasize the eyes, nose, muzzle, ears, and defining markings. Avoid photorealism, fine engraving, scratchboard density, watercolor, painterly rendering, cartoon mascot styling, anime, and 3D rendering.

Output only the pet on a fully transparent background with a genuine alpha channel. Include comfortable transparent margin around every ear and fur edge. Do not add text, names, slogans, fixed decorative artwork, scenery, borders, frames, garments, product mockups, cast shadows, glow, checkerboards, colored backdrops, rectangular background planes, accessories, or extra animals. Return one clean reusable pet asset ready for local compositing."""


class FakeResponses:
    def __init__(self, outputs: list[dict | str] | dict | str | None = None) -> None:
        if outputs is None:
            outputs = [runtime_prompt("draft"), runtime_prompt("critic")]
        if not isinstance(outputs, list):
            outputs = [outputs]
        self.outputs = [
            json.dumps(output) if isinstance(output, dict) else output
            for output in outputs
        ]
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        output = self.outputs[min(index, len(self.outputs) - 1)]
        return SimpleNamespace(id=f"resp_test_{index + 1}", output_text=output)


class FakeClient:
    def __init__(self, outputs: list[dict | str] | dict | str | None = None) -> None:
        self.responses = FakeResponses(outputs)


class PetPromptCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sample = make_image(self.root / "sample.png", size=(200, 300))
        self.art = make_image(self.root / "art.png", size=(400, 600))
        self.key = self.root / "OPENAI_API_KEY.rtf"
        self.key.write_text(
            r"{\rtf1\ansi OpenAI key: sk-test_1234567890abcdefghij}",
            encoding="utf-8",
        )
        self.output = self.root / "pet-transform.md"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self, *extra: str, include_art: bool = True):
        values = [
            "--sample-design",
            str(self.sample),
            "--api-key-file",
            str(self.key),
            "--output",
            str(self.output),
        ]
        if include_art:
            values.extend(["--art", str(self.art)])
        values.extend(extra)
        return build_parser().parse_args(values)

    def test_direct_strategy_generates_and_critiques_prompt_by_default(self) -> None:
        client = FakeClient()

        result = derive_prompt(self.args(), client=client)

        self.assertIsNotNone(result)
        prompt_path, provenance_path = result
        prompt = prompt_path.read_text(encoding="utf-8")
        self.assertIn("critic", prompt)
        self.assertIn("fully transparent background", prompt)
        record = json.loads(provenance_path.read_text(encoding="utf-8"))
        self.assertEqual(record["schema_version"], 2)
        self.assertEqual(record["generator"]["strategy"], "direct")
        self.assertTrue(record["generator"]["critic_pass"])
        self.assertEqual(
            record["generator"]["response_ids"], ["resp_test_1", "resp_test_2"]
        )
        self.assertTrue(record["prompt"]["critic_changed"])
        self.assertNotIn("analysis", record)

        self.assertEqual(len(client.responses.calls), 2)
        first, second = client.responses.calls
        self.assertEqual(first["model"], "gpt-5.6")
        self.assertEqual(first["reasoning"], {"effort": "high"})
        self.assertFalse(first["store"])
        self.assertNotIn("text", first)
        first_images = [
            item
            for item in first["input"][0]["content"]
            if item["type"] == "input_image"
        ]
        self.assertEqual(len(first_images), 2)
        self.assertTrue(
            all(image["detail"] == "original" for image in first_images)
        )
        second_text = "\n".join(
            item["text"]
            for item in second["input"][0]["content"]
            if item["type"] == "input_text"
        )
        self.assertIn("DRAFT RUNTIME PROMPT", second_text)

    def test_direct_strategy_allows_sample_only_and_no_critic(self) -> None:
        client = FakeClient(runtime_prompt())

        result = derive_prompt(
            self.args("--no-critic-pass", include_art=False), client=client
        )

        self.assertIsNotNone(result)
        self.assertEqual(len(client.responses.calls), 1)
        content = client.responses.calls[0]["input"][0]["content"]
        images = [item for item in content if item["type"] == "input_image"]
        self.assertEqual(len(images), 1)
        record = json.loads(result[1].read_text(encoding="utf-8"))
        self.assertIsNone(record["sources"]["art"])
        self.assertFalse(record["generator"]["critic_pass"])

    def test_direct_strategy_includes_optional_baseline(self) -> None:
        baseline = self.root / "approved.md"
        baseline.write_text("Approved concise structure.", encoding="utf-8")
        client = FakeClient(runtime_prompt())

        result = derive_prompt(
            self.args(
                "--no-critic-pass",
                "--baseline-prompt",
                str(baseline),
                include_art=False,
            ),
            client=client,
        )

        content = client.responses.calls[0]["input"][0]["content"]
        request_text = "\n".join(
            item["text"] for item in content if item["type"] == "input_text"
        )
        self.assertIn("APPROVED BASELINE PROMPT", request_text)
        record = json.loads(result[1].read_text(encoding="utf-8"))
        self.assertEqual(
            record["sources"]["baseline_prompt"]["path"], str(baseline.resolve())
        )

    def test_structured_strategy_remains_available(self) -> None:
        client = FakeClient(analysis_data())

        result = derive_prompt(self.args("--strategy", "structured"), client=client)

        self.assertIsNotNone(result)
        prompt = result[0].read_text(encoding="utf-8")
        self.assertIn("preserve muzzle length", prompt)
        record = json.loads(result[1].read_text(encoding="utf-8"))
        self.assertEqual(record["generator"]["strategy"], "structured")
        self.assertIn("analysis", record)
        request = client.responses.calls[0]
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertTrue(request["text"]["format"]["strict"])

    def test_structured_strategy_requires_art(self) -> None:
        client = FakeClient(analysis_data())

        with self.assertRaisesRegex(PetPromptError, "requires --art"):
            derive_prompt(
                self.args("--strategy", "structured", include_art=False),
                client=client,
            )

        self.assertEqual(len(client.responses.calls), 0)

    def test_rejects_invalid_direct_prompt_without_writing_outputs(self) -> None:
        client = FakeClient("short transparent pet identity")

        with self.assertRaisesRegex(PetPromptError, "unusually short"):
            derive_prompt(self.args("--no-critic-pass"), client=client)

        self.assertFalse(self.output.exists())
        self.assertFalse((self.root / "pet-transform.analysis.json").exists())

    def test_refuses_existing_output_before_api_call(self) -> None:
        self.output.write_text("existing", encoding="utf-8")
        client = FakeClient()

        with self.assertRaisesRegex(PetPromptError, "--force"):
            derive_prompt(self.args(), client=client)

        self.assertEqual(len(client.responses.calls), 0)

    def test_rejects_mismatched_aspect_ratio(self) -> None:
        make_image(self.art, size=(400, 400))
        client = FakeClient()

        with self.assertRaisesRegex(PetPromptError, "aspect ratios"):
            derive_prompt(self.args(), client=client)

        self.assertEqual(len(client.responses.calls), 0)

    def test_dry_run_does_not_call_api_or_write_outputs(self) -> None:
        client = FakeClient()

        result = derive_prompt(self.args("--dry-run"), client=client)

        self.assertIsNone(result)
        self.assertEqual(len(client.responses.calls), 0)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
