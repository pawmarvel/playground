from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from helpers import (
    FakeClient,
    copy_font,
    layout_data,
    make_image,
    make_transparent_mark,
)
from pawmarvel_generator.cli import UserInputError
from pawmarvel_generator.poc_runner import run_poc


class PocRunnerTests(unittest.TestCase):
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
        self.prompt = self.root / "pet-transform.md"
        self.prompt.write_text(
            "BACKGROUND = TRANSPARENT\nCreate an isolated pet portrait.",
            encoding="utf-8",
        )
        self.pet = make_image(self.root / "pet.png")
        self.reference = make_image(self.root / "reference.png")
        self.key = self.root / "OPENAI_API_KEY.rtf"
        self.key.write_text("sk-test_1234567890abcdefghij", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self, *, force: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            template_dir=self.template,
            pet_image=self.pet,
            pet_name="BUDDY",
            api_key_file=self.key,
            output_dir=None,
            model="gpt-image-2",
            size="816x816",
            quality="low",
            reference_design=self.reference,
            prompt_file=self.prompt,
            force=force,
        )

    def test_runs_generation_and_rendering(self) -> None:
        client = FakeClient()
        transformed, final, debug = run_poc(self.args(), client=client)
        self.assertEqual(client.images.call_count, 1)
        self.assertTrue(transformed.is_file())
        self.assertTrue(final.is_file())
        self.assertTrue(debug.is_file())
        self.assertEqual(
            [Path(file.name).name for file in client.images.kwargs["image"]],
            ["reference.png", "pet.png"],
        )

    def test_preflight_prevents_paid_call_when_output_exists(self) -> None:
        qa = self.template / "qa"
        qa.mkdir()
        (qa / "final-preview.png").write_bytes(b"existing")
        client = FakeClient()
        with self.assertRaisesRegex(UserInputError, "--force"):
            run_poc(self.args(), client=client)
        self.assertEqual(client.images.call_count, 0)

    def test_reuses_supplied_transformed_pet_without_paid_call(self) -> None:
        transformed = make_transparent_mark(self.root / "approved-transformed.png")
        args = self.args()
        args.pet_image = None
        args.transformed_pet = transformed
        args.layout = self.template / "layout.json"
        client = FakeClient()
        returned, final, debug = run_poc(args, client=client)
        self.assertEqual(returned, transformed.resolve())
        self.assertEqual(client.images.call_count, 0)
        self.assertTrue(final.is_file())
        self.assertTrue(debug.is_file())

    def test_missing_finished_reference_fails_before_paid_call(self) -> None:
        args = self.args()
        args.reference_design = self.root / "missing-reference.png"
        client = FakeClient()
        with self.assertRaisesRegex(UserInputError, "finished reference design"):
            run_poc(args, client=client)
        self.assertEqual(client.images.call_count, 0)


if __name__ == "__main__":
    unittest.main()
