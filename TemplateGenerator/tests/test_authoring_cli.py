from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pawmarvel_generator.authoring_cli import DEFAULT_PROJECT_ROOT, build_parser, main


class AuthoringCliTests(unittest.TestCase):
    def test_layout_attempt_defaults_to_pet_and_supports_explicit_no_name(self) -> None:
        default = build_parser().parse_args(
            [
                "run-attempt",
                "--experiment", "layout-v01",
                "--attempt-id", "attempt-0001",
            ]
        )
        self.assertIsNone(default.pet_name)
        self.assertFalse(default.no_pet_name)

        no_name = build_parser().parse_args(
            [
                "run-attempt",
                "--experiment", "layout-v01",
                "--attempt-id", "attempt-0001",
                "--no-pet-name",
            ]
        )
        self.assertIsNone(no_name.pet_name)
        self.assertTrue(no_name.no_pet_name)

    def test_empty_optional_array_argument_has_actionable_error(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(
                [
                    "create-experiment",
                    "--kind", "layout",
                    "--experiment-id", "layout-v01",
                    "--design-id", "cooper",
                    "--product-profile", "profile.json",
                    "",
                    "--authoring-root", "work/authoring",
                ]
            )

        self.assertEqual(raised.exception.code, 2)
        message = stderr.getvalue()
        self.assertIn("empty or whitespace-only command-line argument", message)
        self.assertIn("10=''", message)
        self.assertIn("PAWMARVEL_LAYOUT_REFERENCE_ARGS=()", message)

    def test_compare_prints_recorded_coverage_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evaluation = Path(temporary) / "evaluation.json"
            evaluation.write_text(
                json.dumps(
                    {
                        "warnings": [
                            "incomplete fixture coverage; missing_fixture_ids=['dog-2']"
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch(
                    "pawmarvel_generator.authoring_cli.compare",
                    return_value=evaluation,
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = main(
                    [
                        "compare",
                        "--kind", "pet",
                        "--review-id", "pet-release",
                        "--experiment", "pet-gpt-v01",
                        "--evaluation-protocol", "protocol.json",
                        "--authoring-product", "authoring/design/product",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), str(evaluation))
            self.assertIn("WARNING: incomplete fixture coverage", stderr.getvalue())
            self.assertIn("dog-2", stderr.getvalue())

    def test_init_config_defaults_to_editable_project_root(self) -> None:
        args = build_parser().parse_args(
            [
                "init-config",
                "--design-id",
                "life-is-good",
                "--product-profile-id",
                "blanket-king-9375x12375",
            ]
        )
        self.assertEqual(args.project_root, DEFAULT_PROJECT_ROOT)

    def test_init_shared_config_defaults_to_editable_project_root(self) -> None:
        args = build_parser().parse_args(["init-shared-config"])
        self.assertEqual(args.project_root, DEFAULT_PROJECT_ROOT)

    def test_create_art_experiment_accepts_empty_canvas_mode(self) -> None:
        args = build_parser().parse_args(
            [
                "create-experiment",
                "--kind", "art",
                "--experiment-id", "art-empty-v01",
                "--design-id", "life-is-good",
                "--product-profile", "product-profile.json",
                "--empty-canvas",
                "--authoring-root", "authoring",
            ]
        )

        self.assertTrue(args.empty_canvas)
        self.assertIsNone(args.prompt_file)
        self.assertIsNone(args.provider)
        self.assertIsNone(args.model)

    def test_prepare_benchmark_accepts_count_and_repeatable_fixture_filters(self) -> None:
        args = build_parser().parse_args(
            [
                "prepare-benchmark",
                "--fixture-set", "fixtures.json",
                "--fixture-count", "6",
                "--fixture-filter", "size_class=large",
                "--fixture-filter", "size_class=giant",
                "--output", "selection.json",
            ]
        )
        self.assertEqual(args.fixture_count, 6)
        self.assertEqual(
            args.fixture_filter,
            ["size_class=large", "size_class=giant"],
        )
        self.assertEqual(args.output, Path("selection.json"))

    def test_benchmark_requires_reviewed_fixture_selection(self) -> None:
        args = build_parser().parse_args(
            [
                "benchmark",
                "--experiment", "experiment",
                "--fixture-set", "fixtures.json",
                "--fixture-selection", "selection.json",
                "--evaluation-protocol", "protocol.json",
            ]
        )
        self.assertEqual(args.fixture_selection, Path("selection.json"))
        self.assertIsNone(args.pet_name)

    def test_run_attempt_pet_name_is_optional_without_an_implicit_value(self) -> None:
        args = build_parser().parse_args(
            [
                "run-attempt",
                "--experiment", "experiment",
                "--attempt-id", "attempt-0001",
                "--pet-image", "pet.png",
            ]
        )

        self.assertIsNone(args.pet_name)

    def test_pet_experiment_accepts_an_optional_default_pet_name(self) -> None:
        args = build_parser().parse_args(
            [
                "create-experiment",
                "--kind", "pet",
                "--experiment-id", "pet-gpt-v01",
                "--design-id", "cooper",
                "--product-profile", "profile.json",
                "--pet-name", "COOPER",
                "--authoring-root", "work/authoring",
            ]
        )

        self.assertEqual(args.pet_name, "COOPER")

    def test_prepare_print_pet_name_is_optional(self) -> None:
        args = build_parser().parse_args(
            [
                "prepare-print",
                "--candidate-id", "print-finalist-0001",
                "--authoring-product", "work/authoring/design/product",
                "--art-attempt", "art-attempt",
                "--pet-attempt", "pet-attempt",
                "--layout-attempt", "layout-attempt",
            ]
        )

        self.assertIsNone(args.pet_name)

    def test_help_does_not_require_resolvable_current_user(self) -> None:
        output = io.StringIO()
        with (
            patch("getpass.getuser", side_effect=KeyError("no user")),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as raised,
        ):
            main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("pawmarvel-author", output.getvalue())

    def test_init_config_writes_private_sourceable_design_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = (
                root
                / "work"
                / "configs"
                / "life-is-good--blanket-king-9375x12375--v01.env"
            )
            self.assertEqual(
                main(
                    [
                        "init-config",
                        "--project-root",
                        str(root),
                        "--design-id",
                        "life-is-good",
                        "--product-profile-id",
                        "blanket-king-9375x12375",
                    ]
                ),
                0,
            )
            contents = output.read_text(encoding="utf-8")
            self.assertNotIn("OPENAI_API_KEY", contents)
            self.assertNotIn("GEMINI_API_KEY", contents)
            self.assertNotIn("BRIA_API_TOKEN", contents)
            self.assertNotIn("AWS_PROFILE", contents)
            self.assertNotIn("AWS_REGION", contents)
            self.assertNotIn("PAWMARVEL_S3_BUCKET", contents)
            self.assertNotIn("PAWMARVEL_S3_PREFIX", contents)
            self.assertIn(
                "work/design-inputs/$PAWMARVEL_DESIGN_ID", contents
            )
            self.assertIn("PAWMARVEL_SMOKE_FIXTURE_SET", contents)
            self.assertIn("mvp-pets-smoke-v1/fixture-set.json", contents)
            self.assertIn("PAWMARVEL_RELEASE_FIXTURE_SET", contents)
            self.assertIn("mvp-pets-v1/fixture-set.json", contents)
            self.assertIn("PAWMARVEL_BENCHMARK_SELECTION_ROOT", contents)
            design_input = root / "work" / "design-inputs" / "life-is-good"
            self.assertTrue(design_input.is_dir())
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            loaded = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    f". {shlex.quote(str(output))}; "
                    "printf '<%s>|<%s>' \"$PAWMARVEL_FONT_REFERENCE\" "
                    "\"$PAWMARVEL_LAYOUT_REFERENCE\"",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(loaded.stdout, "<>|<>")
            (design_input / "font-reference.json").write_text("{}", encoding="utf-8")
            (design_input / "layout-reference.json").write_text("{}", encoding="utf-8")
            loaded_with_references = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    f". {shlex.quote(str(output))}; "
                    "printf '%s|%s' \"$PAWMARVEL_FONT_REFERENCE\" "
                    "\"$PAWMARVEL_LAYOUT_REFERENCE\"",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(loaded_with_references.returncode, 0)
            self.assertEqual(
                loaded_with_references.stdout,
                f"{design_input}/font-reference.json|{design_input}/layout-reference.json",
            )

    def test_init_shared_config_writes_private_sourceable_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "work" / "configs" / "pawmarvel-shared.env"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(
                    main(["init-shared-config", "--project-root", str(root)]),
                    0,
                )

            contents = output.read_text(encoding="utf-8")
            self.assertEqual(stdout.getvalue().strip(), str(output))
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertIn("export OPENAI_API_KEY=''", contents)
            self.assertIn("export GEMINI_API_KEY=''", contents)
            self.assertIn("export BRIA_API_TOKEN=''", contents)
            self.assertIn("export AWS_PROFILE='default'", contents)
            self.assertIn("export AWS_REGION='us-west-2'", contents)
            self.assertIn("export PAWMARVEL_S3_BUCKET=''", contents)
            self.assertIn("export PAWMARVEL_S3_PREFIX='mvp'", contents)

    def test_shared_config_rejects_surrounding_s3_prefix_slash_when_loaded(
        self,
    ) -> None:
        for prefix in ("/mvp", "mvp/"):
            with (
                self.subTest(prefix=prefix),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve()
                output = root / "work" / "configs" / "pawmarvel-shared.env"
                main(["init-shared-config", "--project-root", str(root)])
                contents = output.read_text(encoding="utf-8").replace(
                    "export PAWMARVEL_S3_PREFIX='mvp'",
                    f"export PAWMARVEL_S3_PREFIX='{prefix}'",
                )
                output.write_text(contents, encoding="utf-8")
                loaded = subprocess.run(
                    ["/bin/sh", "-c", f". {shlex.quote(str(output))}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(loaded.returncode, 2)
                self.assertIn("leading or trailing slash", loaded.stderr)

    def test_init_config_refuses_to_replace_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = (
                root
                / "work"
                / "configs"
                / "life-is-good--blanket-king-9375x12375--v01.env"
            )
            arguments = [
                "init-config",
                "--project-root",
                str(root),
                "--design-id",
                "life-is-good",
                "--product-profile-id",
                "blanket-king-9375x12375",
            ]
            main(arguments)
            output.write_text("secret\n", encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                main(arguments)

            self.assertEqual(output.read_text(encoding="utf-8"), "secret\n")
            self.assertIn(str(output), stderr.getvalue())

    def test_init_config_derives_versioned_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "init-config",
                            "--project-root",
                            str(root),
                            "--design-id",
                            "life-is-good",
                            "--product-profile-id",
                            "blanket-king-9375x12375",
                            "--version-number",
                            "2",
                        ]
                    ),
                    0,
                )
            expected = (
                root
                / "work"
                / "configs"
                / "life-is-good--blanket-king-9375x12375--v02.env"
            )
            self.assertEqual(output.getvalue().strip(), str(expected))
            self.assertTrue(expected.is_file())
            self.assertIn(
                "export PAWMARVEL_ART_TEMPLATE_MODE='generated'",
                expected.read_text(encoding="utf-8"),
            )

    def test_init_config_rejects_invalid_version_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                main(
                    [
                        "init-config",
                        "--project-root",
                        str(root),
                        "--design-id",
                        "life-is-good",
                        "--product-profile-id",
                        "blanket-king-9375x12375",
                        "--version-number",
                        "0",
                    ]
                )
            self.assertIn("version number must be between", stderr.getvalue())

    def test_empty_shell_path_is_rejected_with_actionable_error(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(
                [
                    "create-experiment",
                    "--kind",
                    "layout",
                    "--experiment-id",
                    "layout-v02",
                    "--design-id",
                    "life-is-good",
                    "--product-profile",
                    "profile.json",
                    "--font-reference",
                    "",
                    "--authoring-root",
                    "work/authoring",
                ]
            )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn(
            "path must not be empty; check the corresponding shell variable",
            stderr.getvalue(),
        )

    def test_domain_error_is_reported_without_error_handler_name_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary).resolve() / "missing-profile.json"
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "create-experiment",
                        "--kind",
                        "layout",
                        "--experiment-id",
                        "layout-v02",
                        "--design-id",
                        "life-is-good",
                        "--product-profile",
                        str(missing),
                        "--authoring-root",
                        str(Path(temporary) / "authoring"),
                    ]
                )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn(str(missing), stderr.getvalue())
        self.assertNotIn("NameError", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
