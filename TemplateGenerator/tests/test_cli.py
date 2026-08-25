from __future__ import annotations

import argparse
import io
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from helpers import FakeClient, make_image
from pawmarvel_generator.cli import UserInputError, _read_api_key, build_parser, generate


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sample = make_image(self.root / "template.png")
        self.pet = make_image(self.root / "pet.png")
        self.prompt = self.root / "prompt.md"
        self.api_key_file = self.root / "OPENAI_API_KEY.rtf"
        self.prompt.write_text(
            "BACKGROUND = TRANSPARENT\nCreate the requested asset.", encoding="utf-8"
        )
        self.api_key_file.write_text(
            r"{\rtf1\ansi OpenAI key: sk-test_1234567890abcdefghij}",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self, *extra: str) -> argparse.Namespace:
        return build_parser().parse_args(
            [
                "--sample-design",
                str(self.sample),
                "--pet-image",
                str(self.pet),
                "--prompt-file",
                str(self.prompt),
                "--api-key-file",
                str(self.api_key_file),
                *extra,
            ]
        )

    def test_generates_with_ordered_references_and_neutral_roles(self) -> None:
        client = FakeClient()
        output_dir = self.root / "output"
        output = generate(self.args("--output-dir", str(output_dir)), client=client)
        self.assertEqual(output, output_dir.resolve() / "template.png")
        request = client.images.kwargs
        self.assertEqual(
            [Path(file.name).name for file in request["image"]],
            ["template.png", "pet.png"],
        )
        self.assertIn("Input image 1 is the SAMPLE DESIGN reference", request["prompt"])
        self.assertIn("Input image 2 is the USER PET reference", request["prompt"])
        self.assertNotIn("Replace only", request["prompt"])
        self.assertNotIn("input_fidelity", request)

    def test_sample_only_generation_is_supported(self) -> None:
        client = FakeClient()
        args = build_parser().parse_args(
            [
                "--sample-design",
                str(self.sample),
                "--prompt-file",
                str(self.prompt),
                "--api-key-file",
                str(self.api_key_file),
                "--output-dir",
                str(self.root / "output"),
            ]
        )
        generate(args, client=client)
        self.assertEqual(len(client.images.kwargs["image"]), 1)
        self.assertNotIn("USER PET", client.images.kwargs["prompt"])

    def test_pet_only_generation_is_supported(self) -> None:
        client = FakeClient()
        args = build_parser().parse_args(
            [
                "--pet-image",
                str(self.pet),
                "--prompt-file",
                str(self.prompt),
                "--api-key-file",
                str(self.api_key_file),
                "--output-dir",
                str(self.root / "output"),
            ]
        )
        output = generate(args, client=client)
        self.assertEqual(output.name, "pet.png")
        self.assertIn("Input image 1 is the USER PET reference", client.images.kwargs["prompt"])

    def test_rejects_neither_image(self) -> None:
        args = build_parser().parse_args(
            [
                "--prompt-file",
                str(self.prompt),
                "--api-key-file",
                str(self.api_key_file),
            ]
        )
        with self.assertRaisesRegex(UserInputError, "at least one"):
            generate(args, client=FakeClient())

    def test_refuses_to_overwrite_without_force(self) -> None:
        output_dir = self.root / "output"
        output_dir.mkdir()
        (output_dir / "template.png").write_bytes(b"old")
        with self.assertRaisesRegex(UserInputError, "--force"):
            generate(self.args("--output-dir", str(output_dir)), client=FakeClient())

    def test_force_replaces_output(self) -> None:
        output_dir = self.root / "output"
        output_dir.mkdir()
        output = output_dir / "template.png"
        output.write_bytes(b"old")
        generate(
            self.args("--output-dir", str(output_dir), "--force"),
            client=FakeClient(),
        )
        self.assertGreater(output.stat().st_size, 10)

    def test_output_name_must_be_safe(self) -> None:
        with self.assertRaisesRegex(UserInputError, "plain filename"):
            generate(self.args("--output-name", "../bad.png"), client=FakeClient())

    def test_jpg_alias_requests_and_validates_jpeg(self) -> None:
        client = FakeClient()
        output = generate(
            self.args(
                "--output-dir",
                str(self.root / "output"),
                "--output-format",
                "jpg",
                "--background",
                "opaque",
            ),
            client=client,
        )
        self.assertEqual(output.suffix, ".jpg")
        self.assertEqual(client.images.kwargs["output_format"], "jpeg")

    def test_jpeg_rejects_transparency(self) -> None:
        with self.assertRaisesRegex(UserInputError, "does not support transparent"):
            generate(self.args("--output-format", "jpeg"), client=FakeClient())

    def test_extracts_key_from_rtf(self) -> None:
        path, key = _read_api_key(self.api_key_file)
        self.assertEqual(path, self.api_key_file.resolve())
        self.assertEqual(key, "sk-test_1234567890abcdefghij")

    def test_environment_key_is_supported(self) -> None:
        args = build_parser().parse_args(
            [
                "--pet-image",
                str(self.pet),
                "--prompt-file",
                str(self.prompt),
                "--output-dir",
                str(self.root / "output"),
            ]
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test_1234567890abcdefghij"}):
            generate(args, client=FakeClient())

    def test_older_model_requests_high_input_fidelity(self) -> None:
        client = FakeClient()
        generate(
            self.args(
                "--model",
                "gpt-image-1.5",
                "--output-dir",
                str(self.root / "output"),
            ),
            client=client,
        )
        self.assertEqual(client.images.kwargs["input_fidelity"], "high")

    def test_logs_parameters_without_prompt_or_key(self) -> None:
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            generate(
                self.args("--output-dir", str(self.root / "output")),
                client=FakeClient(),
            )
        logged = diagnostics.getvalue()
        self.assertIn("API parameters (prompt omitted)", logged)
        self.assertNotIn("Create the requested asset", logged)
        self.assertNotIn("sk-test_1234567890abcdefghij", logged)

    def test_reports_periodic_progress(self) -> None:
        client = FakeClient()
        original = client.images.edit

        def slow_edit(**kwargs):
            time.sleep(0.04)
            return original(**kwargs)

        client.images.edit = slow_edit
        diagnostics = io.StringIO()
        with patch(
            "pawmarvel_generator.cli.PROGRESS_INTERVAL_SECONDS", 0.01
        ), redirect_stderr(diagnostics):
            generate(
                self.args("--output-dir", str(self.root / "output")),
                client=client,
            )
        self.assertIn("Generation is still in progress", diagnostics.getvalue())


if __name__ == "__main__":
    unittest.main()
