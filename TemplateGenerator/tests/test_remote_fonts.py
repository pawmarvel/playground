from __future__ import annotations

import tempfile
import unittest
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

    def test_fuzzy_search_is_limited_to_ofl_metadata_paths(self) -> None:
        tree = {
            "truncated": False,
            "tree": [
                {"path": "ofl/amaticsc/METADATA.pb"},
                {"path": "ofl/assistant/METADATA.pb"},
                {"path": "apache/roboto/METADATA.pb"},
            ],
        }
        with (
            patch(
                "pawmarvel_generator.remote_fonts._family_listing",
                side_effect=RemoteFontError("font family was not found in Google Fonts OFL"),
            ),
            patch("pawmarvel_generator.remote_fonts._request_json", return_value=tree),
            patch("pawmarvel_generator.remote_fonts._tree_family_ids", None),
        ):
            result = search_google_ofl("amaticc")
        self.assertEqual(result[0].family_id, "amaticsc")
        self.assertNotIn("roboto", {item.family_id for item in result})

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
