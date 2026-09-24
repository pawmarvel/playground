from __future__ import annotations

import argparse
import io
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from PIL import Image

from helpers import FakeClient, FakeGeminiClient, make_image
from pawmarvel_generator.cli import (
    UserInputError,
    _GeminiRestInteractions,
    _read_api_key,
    build_parser,
    generate,
)
from pawmarvel_generator.image_size import ImageSize
from pawmarvel_generator.product_profile import (
    create_product_profile,
    write_product_profile,
)


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
                "--reference-design",
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

    def test_empty_optional_array_argument_has_actionable_error(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(
                [
                    "--pet-image", str(self.pet),
                    "",
                    "--prompt-file", str(self.prompt),
                ]
            )

        self.assertEqual(raised.exception.code, 2)
        message = stderr.getvalue()
        self.assertIn("empty or whitespace-only command-line argument", message)
        self.assertIn("3=''", message)
        self.assertIn("PAWMARVEL_REFERENCE_ARGS=()", message)

    def test_transforms_pet_with_one_reference_design_and_prompt(self) -> None:
        client = FakeClient()
        output_dir = self.root / "output"
        output = generate(self.args("--output-dir", str(output_dir)), client=client)
        self.assertEqual(output, output_dir.resolve() / "template.png")
        request = client.images.kwargs
        self.assertEqual(
            [Path(file.name).name for file in request["image"]],
            ["pet.png", "template.png"],
        )
        self.assertIn("USER PET", request["prompt"])
        self.assertIn("PRIMARY REFERENCE DESIGN", request["prompt"])
        self.assertIn(
            "never pet identity or facial expression", request["prompt"]
        )
        self.assertIn("Create the requested asset", request["prompt"])
        self.assertNotIn("ADDITIONAL REFERENCE DESIGNS", request["prompt"])
        self.assertNotIn("Input image 1", request["prompt"])
        self.assertNotIn("Replace only", request["prompt"])
        self.assertNotIn("input_fidelity", request)
        self.assertNotIn("service_tier", request)

    def test_substitutes_pet_name_placeholder_before_api_call(self) -> None:
        self.prompt.write_text(
            "BACKGROUND = TRANSPARENT\nRender {{PET_NAME}} as artistic lettering.",
            encoding="utf-8",
        )
        client = FakeClient()

        generate(
            self.args(
                "--pet-name",
                "  Cooper  ",
                "--output-dir",
                str(self.root / "output"),
            ),
            client=client,
        )

        self.assertIn("Render Cooper as artistic lettering", client.images.kwargs["prompt"])
        self.assertNotIn("{{PET_NAME}}", client.images.kwargs["prompt"])

    def test_pet_name_placeholder_requires_pet_name(self) -> None:
        self.prompt.write_text(
            "Render {{PET_NAME}} as artistic lettering.", encoding="utf-8"
        )
        client = FakeClient()

        with self.assertRaisesRegex(UserInputError, "provide --pet-name"):
            generate(self.args(), client=client)

        self.assertEqual(client.images.call_count, 0)

    def test_no_pet_name_prepends_instruction_and_runs_with_placeholder(self) -> None:
        self.prompt.write_text(
            "Render {{PET_NAME}} as artistic lettering.", encoding="utf-8"
        )
        client = FakeClient()

        generate(
            self.args(
                "--no-pet-name",
                "--output-dir",
                str(self.root / "output"),
            ),
            client=client,
        )

        prompt = client.images.kwargs["prompt"]
        self.assertTrue(
            prompt.startswith("No pet name, ignore {{PET_NAME}} placeholder\n\n")
        )
        self.assertIn("Render {{PET_NAME}} as artistic lettering.", prompt)
        self.assertEqual(client.images.call_count, 1)

    def test_pet_name_and_no_pet_name_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit):
            self.args("--pet-name", "Cooper", "--no-pet-name")

    def test_unused_pet_name_warns_and_does_not_block_api_call(self) -> None:
        self.prompt.write_text(
            "Transform only the pet cutout.", encoding="utf-8"
        )
        client = FakeClient()
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            generate(
                self.args(
                    "--pet-name",
                    "Cooper",
                    "--output-dir",
                    str(self.root / "output"),
                ),
                client=client,
            )

        self.assertEqual(client.images.call_count, 1)
        self.assertIn("Transform only the pet cutout.", client.images.kwargs["prompt"])
        self.assertNotIn("Cooper", client.images.kwargs["prompt"])
        self.assertIn("prompt contains no {{PET_NAME}} placeholder", stderr.getvalue())

    def test_pet_name_placeholder_requires_pet_image(self) -> None:
        self.prompt.write_text(
            "Render {{PET_NAME}} as artistic lettering.", encoding="utf-8"
        )
        args = build_parser().parse_args(
            [
                "--reference-design",
                str(self.sample),
                "--pet-name",
                "Cooper",
                "--prompt-file",
                str(self.prompt),
                "--api-key-file",
                str(self.api_key_file),
            ]
        )

        with self.assertRaisesRegex(UserInputError, "transformed-pet generation"):
            generate(args, client=FakeClient())

    def test_reference_only_generation_is_supported(self) -> None:
        client = FakeClient()
        args = build_parser().parse_args(
            [
                "--reference-design",
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
        self.assertIn("PRIMARY REFERENCE DESIGN", client.images.kwargs["prompt"])

    def test_reference_only_multiple_references_preserve_primary_role(self) -> None:
        second = make_image(self.root / "second.png")
        client = FakeClient()
        args = build_parser().parse_args(
            [
                "--reference-design",
                str(self.sample),
                "--reference-design",
                str(second),
                "--prompt-file",
                str(self.prompt),
                "--api-key-file",
                str(self.api_key_file),
                "--output-dir",
                str(self.root / "output"),
            ]
        )
        generate(args, client=client)
        api_prompt = client.images.kwargs["prompt"]
        self.assertIn("PRIMARY REFERENCE DESIGN", api_prompt)
        self.assertIn("ADDITIONAL REFERENCE DESIGNS", api_prompt)
        self.assertIn("never to override the primary geometry", api_prompt)

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
        self.assertIn("USER PET", client.images.kwargs["prompt"])

    def test_accepts_ordered_repeated_references_after_pet(self) -> None:
        second = make_image(self.root / "second.png")
        client = FakeClient()
        args = self.args(
            "--reference-design",
            str(second),
            "--output-dir",
            str(self.root / "output"),
        )
        generate(args, client=client)
        self.assertEqual(
            [Path(file.name).name for file in client.images.kwargs["image"]],
            ["pet.png", "template.png", "second.png"],
        )
        self.assertIn(
            "ADDITIONAL REFERENCE DESIGNS", client.images.kwargs["prompt"]
        )

    def test_openai_prompt_only_uses_image_generation_endpoint(self) -> None:
        args = build_parser().parse_args(
            [
                "--prompt-file",
                str(self.prompt),
                "--api-key-file",
                str(self.api_key_file),
                "--output-dir",
                str(self.root / "output"),
            ]
        )
        client = FakeClient()

        output = generate(args, client=client)

        self.assertEqual(output, (self.root / "output" / "prompt.png").resolve())
        self.assertEqual(client.images.generate_call_count, 1)
        self.assertEqual(client.images.edit_call_count, 0)
        self.assertEqual(
            client.images.kwargs["prompt"],
            "BACKGROUND = TRANSPARENT\nCreate the requested asset.",
        )
        self.assertNotIn("image", client.images.kwargs)

    def test_empty_canvas_creates_all_zero_rgba_png_without_api(self) -> None:
        output_dir = self.root / "empty-output"
        args = build_parser().parse_args(
            [
                "--empty-canvas",
                "--size",
                "321x123",
                "--output-dir",
                str(output_dir),
                "--output-name",
                "art.png",
            ]
        )
        client = FakeClient()

        output = generate(args, client=client)

        self.assertEqual(output, output_dir.resolve() / "art.png")
        self.assertEqual(client.images.call_count, 0)
        with Image.open(output) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.size, (321, 123))
            self.assertEqual(image.getextrema(), ((0, 0),) * 4)

    def test_empty_canvas_uses_product_profile_art_size(self) -> None:
        profile = write_product_profile(
            self.root / "product-profile.json",
            create_product_profile(
                profile_id="blanket-king-9375x12375",
                print_size=ImageSize(9375, 12375),
            ),
        )
        args = build_parser().parse_args(
            [
                "--empty-canvas",
                "--product-profile",
                str(profile),
                "--profile-layer",
                "art",
                "--output-dir",
                str(self.root / "empty-output"),
            ]
        )

        output = generate(args, client=FakeClient())

        with Image.open(output) as image:
            self.assertEqual(image.size, (800, 1056))
            self.assertEqual(image.getextrema(), ((0, 0),) * 4)

    def test_empty_canvas_rejects_generation_inputs_and_implicit_size(self) -> None:
        with self.assertRaisesRegex(UserInputError, "requires --size"):
            generate(
                build_parser().parse_args(["--empty-canvas"]),
                client=FakeClient(),
            )
        with self.assertRaisesRegex(UserInputError, "cannot be combined"):
            generate(
                build_parser().parse_args(
                    [
                        "--empty-canvas",
                        "--size",
                        "100x100",
                        "--prompt-file",
                        str(self.prompt),
                    ]
                ),
                client=FakeClient(),
            )

    def test_gemini_prompt_only_uses_text_input(self) -> None:
        gemini_key = self.root / "GEMINI_API_KEY.txt"
        gemini_key.write_text("test-gemini-key", encoding="utf-8")
        args = build_parser().parse_args(
            [
                "--provider",
                "gemini",
                "--prompt-file",
                str(self.prompt),
                "--api-key-file",
                str(gemini_key),
                "--output-dir",
                str(self.root / "gemini-output"),
            ]
        )
        client = FakeGeminiClient()

        generate(args, client=client)

        request = client.interactions.kwargs
        self.assertEqual(len(request["input"]), 1)
        self.assertEqual(request["input"][0]["type"], "text")
        self.assertIn("Create the requested asset", request["input"][0]["text"])
        self.assertIn("only the artwork requested", request["input"][0]["text"])
        self.assertNotIn("isolated subject image", request["input"][0]["text"])

    def test_rejects_invalid_gpt_image_2_dimensions_before_api_call(self) -> None:
        client = FakeClient()
        with self.assertRaisesRegex(UserInputError, "multiples of 16"):
            generate(self.args("--size", "1000x1320"), client=client)
        self.assertEqual(client.images.call_count, 0)

    def test_product_profile_derives_art_generation_size(self) -> None:
        profile = write_product_profile(
            self.root / "product-profile.json",
            create_product_profile(
                profile_id="blanket-king-9375x12375",
                print_size=ImageSize(9375, 12375),
            ),
        )
        client = FakeClient()
        args = self.args(
            "--output-dir", str(self.root / "output"),
            "--product-profile", str(profile),
            "--profile-layer", "art",
        )
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            generate(args, client=client)
        self.assertEqual(client.images.kwargs["size"], "800x1056")
        self.assertIn(
            "authoritative product-profile art canvas is 800x1056",
            diagnostics.getvalue(),
        )
        self.assertIn("review the result", diagnostics.getvalue())

    def test_matching_art_reference_aspect_ratio_does_not_warn(self) -> None:
        self.sample = make_image(self.sample, size=(800, 1056))
        profile = write_product_profile(
            self.root / "product-profile.json",
            create_product_profile(
                profile_id="blanket-king-9375x12375",
                print_size=ImageSize(9375, 12375),
            ),
        )
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            generate(
                self.args(
                    "--output-dir", str(self.root / "output"),
                    "--product-profile", str(profile),
                    "--profile-layer", "art",
                ),
                client=FakeClient(),
            )
        self.assertNotIn("aspect ratio", diagnostics.getvalue())

    def test_product_profile_derives_transformed_pet_generation_size(self) -> None:
        profile = write_product_profile(
            self.root / "product-profile.json",
            create_product_profile(
                profile_id="blanket-king-9375x12375",
                print_size=ImageSize(9375, 12375),
            ),
        )
        client = FakeClient()
        args = self.args(
            "--output-dir", str(self.root / "output"),
            "--product-profile", str(profile),
            "--profile-layer", "transformed-pet",
        )
        generate(args, client=client)
        self.assertEqual(client.images.kwargs["size"], "816x816")

    def test_product_profile_rejects_explicit_size_override(self) -> None:
        profile = write_product_profile(
            self.root / "product-profile.json",
            create_product_profile(
                profile_id="blanket-king-9375x12375",
                print_size=ImageSize(9375, 12375),
            ),
        )
        client = FakeClient()
        args = self.args(
            "--size", "1024x1120",
            "--product-profile", str(profile),
            "--profile-layer", "art",
        )
        with self.assertRaisesRegex(UserInputError, "profile owns"):
            generate(args, client=client)
        self.assertEqual(client.images.call_count, 0)

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

    def test_gemini_provider_uses_ordered_images_and_exact_local_canvas(self) -> None:
        client = FakeGeminiClient()
        gemini_key = self.root / "GEMINI_API_KEY.txt"
        gemini_key.write_text("test-gemini-key", encoding="utf-8")
        args = self.args(
            "--provider",
            "gemini",
            "--api-key-file",
            str(gemini_key),
            "--size",
            "816x816",
            "--output-dir",
            str(self.root / "gemini-output"),
        )
        output = generate(args, client=client)
        request = client.interactions.kwargs
        self.assertEqual(request["model"], "gemini-3.1-flash-image")
        self.assertEqual(
            [part["mime_type"] for part in request["input"][1:]],
            ["image/png", "image/png"],
        )
        self.assertIn("USER PET", request["input"][0]["text"])
        self.assertIn("not reproduce any reference background", request["input"][0]["text"])
        self.assertIn("pure-white (#FFFFFF) isolation matte", request["input"][0]["text"])
        self.assertNotIn("mime_type", request["response_format"])
        self.assertEqual(request["response_format"]["aspect_ratio"], "1:1")
        self.assertEqual(request["response_format"]["image_size"], "1K")
        with Image.open(output) as image:
            self.assertEqual(image.size, (816, 816))
            self.assertIn("A", image.getbands())

    def test_gemini_model_infers_provider_and_environment_key(self) -> None:
        args = build_parser().parse_args(
            [
                "--pet-image",
                str(self.pet),
                "--prompt-file",
                str(self.prompt),
                "--model",
                "gemini-3.1-flash-lite-image",
                "--output-dir",
                str(self.root / "gemini-output"),
            ]
        )
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key"}):
            generate(args, client=FakeGeminiClient())

    def test_rejects_provider_model_mismatch(self) -> None:
        with self.assertRaisesRegex(UserInputError, "requires a gemini"):
            generate(
                self.args("--provider", "gemini", "--model", "gpt-image-2"),
                client=FakeGeminiClient(),
            )

    def test_gemini_http_error_includes_json_diagnostic(self) -> None:
        error = HTTPError(
            "https://example.invalid",
            400,
            "Bad Request",
            {},
            io.BytesIO(
                b'{"error":{"message":"Unsupported response MIME type"}}'
            ),
        )
        with patch(
            "pawmarvel_generator.cli.urlopen", side_effect=error
        ), self.assertRaisesRegex(
            RuntimeError, "400: Unsupported response MIME type"
        ):
            _GeminiRestInteractions("secret").create(
                model="gemini-3.1-flash-image",
                input="test",
            )

    def test_rejects_prompt_category_that_disagrees_with_provider(self) -> None:
        for filename in (
            "pet-transform-gemini.md",
            "pet-transform-gemini-artistic-name-v01.md",
            "pet-transform-artistic-name-gemini-v01.md",
        ):
            gemini_prompt = self.root / filename
            gemini_prompt.write_text("Create the requested pet.", encoding="utf-8")
            args = self.args("--prompt-file", str(gemini_prompt))
            with self.subTest(filename=filename), self.assertRaisesRegex(
                UserInputError, "does not match openai"
            ):
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
