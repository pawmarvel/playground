from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import copy_font
from pawmarvel_generator.font_catalog import FontCatalogError, discover_font_catalog


class FontCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_primary_is_first_and_duplicate_bytes_are_collapsed(self) -> None:
        primary_root = self.root / "primary"
        primary_root.mkdir()
        primary = copy_font(primary_root, "Primary.ttf")
        duplicate_root = self.root / "catalog" / "duplicate"
        duplicate_root.mkdir(parents=True)
        copy_font(duplicate_root, "Duplicate.ttf")

        candidates = discover_font_catalog(
            primary, catalog_roots=(self.root / "catalog",)
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].font, primary.resolve())
        self.assertEqual(candidates[0].relative_name, "fonts/Primary.ttf")

    def test_catalog_rejects_ttf_without_sibling_ofl(self) -> None:
        primary_root = self.root / "primary"
        primary_root.mkdir()
        primary = copy_font(primary_root)
        invalid_root = self.root / "catalog" / "invalid"
        invalid_root.mkdir(parents=True)
        (invalid_root / "Unlicensed.ttf").write_bytes(primary.read_bytes())

        with self.assertRaisesRegex(FontCatalogError, "OFL"):
            discover_font_catalog(
                primary, catalog_roots=(self.root / "catalog",)
            )

    def test_repository_catalog_has_distinct_approved_candidates(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        catalog = repository / "assets" / "fonts"
        candidates = discover_font_catalog(
            catalog / "anton" / "Anton-Regular.ttf",
            catalog_roots=(catalog,),
        )

        self.assertEqual(
            {candidate.label for candidate in candidates},
            {"Anton", "Amatic SC Bold", "Bebas Neue"},
        )
