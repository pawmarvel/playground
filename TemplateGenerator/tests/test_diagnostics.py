from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from pawmarvel_generator.artifact_io import read_json
from pawmarvel_generator.cli_errors import report_unexpected
from pawmarvel_generator.bundle import BundleError
from pawmarvel_generator.s3_publisher import (
    _is_conditional_put_conflict,
    _verify_s3_object,
)


class DiagnosticTests(unittest.TestCase):
    @staticmethod
    def _s3_item() -> dict[str, object]:
        return {
            "key": "mvp/example.png",
            "sha256": "0" * 64,
            "bytes": 1,
            "media_type": "image/png",
        }

    def test_json_error_includes_absolute_path_location_and_correction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "broken.json"
            path.write_text('{\n  "value":\n}', encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                read_json(
                    path,
                    label="test artifact",
                    error_type=ValueError,
                    require_object=True,
                    correction="regenerate it.",
                )
        message = str(raised.exception)
        self.assertIn(str(path.resolve()), message)
        self.assertIn("line=3", message)
        self.assertIn("column=1", message)
        self.assertIn("Correction: regenerate it.", message)

    def test_missing_json_error_includes_absolute_path(self) -> None:
        missing = Path("relative/missing.json")
        with self.assertRaises(ValueError) as raised:
            read_json(
                missing,
                label="test artifact",
                error_type=ValueError,
            )
        self.assertIn(str(missing.resolve()), str(raised.exception))
        self.assertIn("does not exist", str(raised.exception))

    def test_unexpected_error_is_concise_by_default_and_debuggable(self) -> None:
        error = RuntimeError("provider failed")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = report_unexpected("test-cli", error, debug=False)
        self.assertEqual(result, 1)
        self.assertIn("RuntimeError: provider failed", stderr.getvalue())
        self.assertIn("re-run with --debug", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

        stderr = io.StringIO()
        try:
            raise RuntimeError("provider failed")
        except RuntimeError as caught:
            with redirect_stderr(stderr):
                report_unexpected("test-cli", caught, debug=True)
        self.assertIn("Traceback", stderr.getvalue())

    def test_s3_expired_sso_error_includes_context_and_login_command(self) -> None:
        result = subprocess.CompletedProcess(
            args=["aws"],
            returncode=255,
            stdout="",
            stderr="Error when retrieving token from sso: Token has expired",
        )
        with patch(
            "pawmarvel_generator.s3_publisher._aws_cli", return_value=result
        ), self.assertRaises(BundleError) as raised:
            _verify_s3_object(
                bucket="test-bucket",
                item=self._s3_item(),
                aws_profile="alphapaw-dev",
                region="us-east-1",
            )
        message = str(raised.exception)
        self.assertIn("s3://test-bucket/mvp/example.png", message)
        self.assertIn("profile=alphapaw-dev", message)
        self.assertIn("region=us-east-1", message)
        self.assertIn('aws sso login --profile "alphapaw-dev"', message)

    def test_s3_malformed_cli_json_includes_object_and_location(self) -> None:
        result = subprocess.CompletedProcess(
            args=["aws"], returncode=0, stdout="{broken", stderr=""
        )
        with patch(
            "pawmarvel_generator.s3_publisher._aws_cli", return_value=result
        ), self.assertRaises(BundleError) as raised:
            _verify_s3_object(
                bucket="test-bucket",
                item=self._s3_item(),
                aws_profile=None,
                region=None,
            )
        message = str(raised.exception)
        self.assertIn("s3://test-bucket/mvp/example.png", message)
        self.assertIn("line=1", message)
        self.assertIn("column=2", message)

    def test_s3_conflict_detection_requires_an_explicit_error_token(self) -> None:
        self.assertTrue(
            _is_conditional_put_conflict(
                "An error occurred (PreconditionFailed) when calling PutObject"
            )
        )
        self.assertTrue(_is_conditional_put_conflict("HTTP status: 412"))
        self.assertFalse(
            _is_conditional_put_conflict(
                "AccessDenied request-id=abc412def bytes=4096"
            )
        )


if __name__ == "__main__":
    unittest.main()
