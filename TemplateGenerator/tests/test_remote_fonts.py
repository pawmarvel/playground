from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from helpers import copy_font
from pawmarvel_generator.remote_fonts import (
    ImportedFontFamily,
    RemoteFontError,
    RemoteFontFamily,
    import_google_ofl_family,
    normalize_google_font_family,
    search_google_ofl,
)
from pawmarvel_generator import remote_fonts


class RemoteFontTests(unittest.TestCase):
    def test_normalizes_human_family_name_and_rejects_paths(self) -> None:
        self.assertEqual(normalize_google_font_family("Amatic SC"), "amaticsc")
        with self.assertRaisesRegex(RemoteFontError, "2-80"):
            normalize_google_font_family("../")

    def test_exact_ofl_search_does_not_fetch_the_large_tree(self) -> None:
        with patch(
            "pawmarvel_generator.remote_fonts._family_listing", return_value=[]
        ) as listing:
            result = search_google_ofl("Amatic SC")
        self.assertEqual(result, (RemoteFontFamily("amaticsc", "Amatic SC"),))
        listing.assert_called_once_with("amaticsc")

    def test_bodoni_and_didone_aliases_resolve_to_verified_ofl_families(self) -> None:
        with patch(
            "pawmarvel_generator.remote_fonts._family_listing", return_value=[]
        ) as listing:
            bodoni = search_google_ofl("Bodoni")
            didone = search_google_ofl("Didone-style serif")

        self.assertEqual(
            [family.family_id for family in bodoni],
            ["bodonimoda", "librebodoni", "bodonimodasc"],
        )
        self.assertEqual(
            [family.family_id for family in didone],
            ["bodonimoda", "librebodoni", "bodonimodasc"],
        )
        self.assertEqual(listing.call_count, 6)

    def test_unknown_family_does_not_download_the_global_google_fonts_tree(self) -> None:
        with patch(
            "pawmarvel_generator.remote_fonts._family_listing",
            side_effect=RemoteFontError("font family was not found in Google Fonts OFL"),
        ) as listing, patch(
            "pawmarvel_generator.remote_fonts._request_json"
        ) as request_json:
            result = search_google_ofl("not a known family")

        self.assertEqual(result, ())
        listing.assert_called_once_with("notaknownfamily")
        request_json.assert_not_called()

    def test_json_request_retries_protocol_failure_and_returns_data(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit: int) -> bytes:
                return json.dumps({"ok": True}).encode("utf-8")

        failure = urllib.error.URLError("connection reset")
        with patch(
            "pawmarvel_generator.remote_fonts.urllib.request.urlopen",
            side_effect=[failure, Response()],
        ) as urlopen, patch("pawmarvel_generator.remote_fonts.time.sleep") as sleep:
            result = remote_fonts._request_json("https://api.github.com/test")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_import_downloads_ttf_license_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            font = copy_font(source_root, "Remote-Regular.ttf")
            license_bytes = (source_root / "OFL.txt").read_bytes()
            metadata = b'name: "Remote Test"\nlicense: "OFL"\n'
            listing = [
                {"name": "OFL.txt", "download_url": "https://raw.githubusercontent.com/google/fonts/main/ofl/remotetest/OFL.txt"},
                {"name": "METADATA.pb", "download_url": "https://raw.githubusercontent.com/google/fonts/main/ofl/remotetest/METADATA.pb"},
                {"name": "Remote-Regular.ttf", "download_url": "https://raw.githubusercontent.com/google/fonts/main/ofl/remotetest/Remote-Regular.ttf"},
            ]

            def content(url: str) -> bytes:
                if url.endswith("OFL.txt"):
                    return license_bytes
                if url.endswith("METADATA.pb"):
                    return metadata
                return font.read_bytes()

            destination = root / "download"
            destination.mkdir()
            with (
                patch("pawmarvel_generator.remote_fonts._family_listing", return_value=listing),
                patch("pawmarvel_generator.remote_fonts._download", side_effect=content),
            ):
                imported = import_google_ofl_family("remote test", destination)

            self.assertIsInstance(imported, ImportedFontFamily)
            self.assertEqual(imported.family.label, "Remote Test")
            self.assertEqual(len(imported.candidates), 1)
            self.assertTrue(imported.candidates[0].font.is_file())
            self.assertTrue(imported.license.is_file())
            self.assertTrue(imported.metadata.is_file())

    def test_failed_import_removes_partial_family(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            listing = [
                {"name": "OFL.txt", "download_url": "https://raw.githubusercontent.com/google/fonts/main/ofl/remotetest/OFL.txt"},
                {"name": "METADATA.pb", "download_url": "https://raw.githubusercontent.com/google/fonts/main/ofl/remotetest/METADATA.pb"},
                {"name": "Remote-Regular.ttf", "download_url": "https://raw.githubusercontent.com/google/fonts/main/ofl/remotetest/Remote-Regular.ttf"},
            ]
            with (
                patch(
                    "pawmarvel_generator.remote_fonts._family_listing",
                    return_value=listing,
                ),
                patch(
                    "pawmarvel_generator.remote_fonts._download",
                    side_effect=RemoteFontError("network failed"),
                ),
                self.assertRaisesRegex(RemoteFontError, "network failed"),
            ):
                import_google_ofl_family("remote test", root)
            self.assertFalse((root / "remotetest").exists())
            self.assertFalse((root / ".remotetest.partial").exists())
