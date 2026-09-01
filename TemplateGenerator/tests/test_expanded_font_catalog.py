from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from helpers import copy_font
from pawmarvel_generator.expanded_font_catalog import (
    ExpandedFontCatalogError,
    load_expanded_index,
    materialize_expanded_fonts,
)


class ExpandedFontCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "source"
        source.mkdir()
        self.font_data = copy_font(source).read_bytes()
        self.license_data = (source / "OFL.txt").read_bytes()
        self.index = self.root / "index.json"
        self.index.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_revision": "pinned-test-revision",
                    "fonts": [
                        {
                            "family": "Test Family",
                            "style": "Regular",
                            "filename": "TestFont.ttf",
                            "priority": 1,
                            "font_url": "https://fonts.invalid/TestFont.ttf",
                            "license_url": "https://fonts.invalid/OFL.txt",
                            "font_sha256": hashlib.sha256(self.font_data).hexdigest(),
                            "license_sha256": hashlib.sha256(self.license_data).hexdigest(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def downloader(self, url: str) -> bytes:
        return self.license_data if url.endswith("OFL.txt") else self.font_data

    def test_downloads_validates_and_reuses_offline_cache(self) -> None:
        cache = self.root / "cache"
        downloaded = materialize_expanded_fonts(
            self.index, cache, downloader=self.downloader
        )
        reused = materialize_expanded_fonts(self.index, cache, offline=True)
        self.assertEqual(downloaded, reused)
        self.assertEqual(downloaded[0].read_bytes(), self.font_data)
        self.assertEqual(downloaded[0].parent.name.__len__(), 16)

    def test_rejects_checksum_mismatch(self) -> None:
        with self.assertRaisesRegex(ExpandedFontCatalogError, "checksum"):
            materialize_expanded_fonts(
                self.index,
                self.root / "cache",
                downloader=lambda url: b"wrong",
            )

    def test_checked_in_index_is_valid_and_pinned(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        revision, entries = load_expanded_index(
            repository / "assets" / "fonts" / "expanded-catalog.json"
        )
        self.assertEqual(len(revision), 40)
        self.assertGreaterEqual(len(entries), 15)
        self.assertTrue(all(revision in item.font_url for item in entries))


if __name__ == "__main__":
    unittest.main()
