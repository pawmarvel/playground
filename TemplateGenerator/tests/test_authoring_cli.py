from __future__ import annotations

import io
import os
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pawmarvel_generator.authoring_cli import build_parser, main


class AuthoringCliTests(unittest.TestCase):
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
            output = root / "work" / "configs" / "life-is-good.env"
            self.assertEqual(
                main(
                    [
                        "init-config",
                        "--output",
                        str(output),
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
            self.assertIn("export OPENAI_API_KEY=''", contents)
            self.assertIn("export GEMINI_API_KEY=''", contents)
            self.assertIn("export BRIA_API_TOKEN=''", contents)
            self.assertIn("export PAWMARVEL_S3_BUCKET=''", contents)
            self.assertIn(
                "work/design-inputs/$PAWMARVEL_DESIGN_ID", contents
            )
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

    def test_generated_config_rejects_surrounding_s3_prefix_slash_when_loaded(
        self,
    ) -> None:
        for prefix in ("/mvp", "mvp/"):
            with (
                self.subTest(prefix=prefix),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve()
                output = root / "work" / "configs" / "design.env"
                main(
                    [
                        "init-config",
                        "--output",
                        str(output),
                        "--project-root",
                        str(root),
                        "--design-id",
                        "life-is-good",
                        "--product-profile-id",
                        "blanket-king-9375x12375",
                    ]
                )
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

    def test_init_config_refuses_to_replace_existing_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "work" / "configs" / "design.env"
            arguments = [
                "init-config",
                "--output",
                str(output),
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

    def test_init_config_rejects_repository_tracked_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "examples" / "design.env"
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                main(
                    [
                        "init-config",
                        "--output",
                        str(output),
                        "--project-root",
                        str(root),
                        "--design-id",
                        "life-is-good",
                        "--product-profile-id",
                        "blanket-king-9375x12375",
                    ]
                )

            self.assertFalse(output.exists())
            self.assertIn(str(root / "work"), stderr.getvalue())

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
