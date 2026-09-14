from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import ImageFont

from helpers import copy_font
from pawmarvel_generator.font_catalog import (
    FontCatalogError,
    default_local_font_catalog,
    discover_font_catalog,
)


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

    def test_repository_catalog_matches_manifest_and_has_distinct_candidates(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        catalog = repository / "assets" / "fonts"
        manifest = json.loads((catalog / "catalog.json").read_text(encoding="utf-8"))
        candidates = discover_font_catalog(
            catalog / "anton" / "Anton-Regular.ttf",
            catalog_roots=(catalog,),
        )

        expected_count = manifest["selection"]["face_count"]
        self.assertGreaterEqual(expected_count, 40)
        self.assertEqual(len(candidates), expected_count)
        self.assertEqual(len({candidate.sha256 for candidate in candidates}), expected_count)
        self.assertTrue(
            {
                "Anton",
                "Amatic SC Bold",
                "Bebas Neue",
                "Great Vibes",
                "Rye",
                "Bungee",
            }.issubset({candidate.label for candidate in candidates})
        )
        manifest_families = {entry["family"] for entry in manifest["fonts"]}
        priority_families = {
            family
            for families in manifest["selection"]["priority_groups"].values()
            for family in families
        }
        self.assertEqual(
            {key: len(value) for key, value in manifest["selection"]["priority_groups"].items()},
            {
                "condensed_display": 13,
                "general_sans": 16,
                "rounded_playful": 16,
                "handwritten_casual": 9,
                "retro_vintage": 15,
                "classic_premium_serif": 9,
                "western_americana_outdoors": 6,
            },
        )
        self.assertEqual(len(priority_families), 84)
        self.assertTrue(priority_families.issubset(manifest_families))
        self.assertTrue(
            {
                "Bebas Neue",
                "Alumni Sans Pinstripe",
                "Inter",
                "M PLUS Rounded 1c",
                "Cherry Bomb One",
                "Patrick Hand SC",
                "Fraunces",
                "Cormorant Garamond",
                "Pirata One",
            }.issubset(manifest_families)
        )
        excluded = manifest["selection"]["excluded_requested_families"]
        self.assertEqual(
            set(excluded),
            {
                "Alumni Sans Condensed",
                "Schoolbell",
                "Coming Soon",
                "Homemade Apple",
                "Rock Salt",
                "Just Another Hand",
                "Smokum",
                "Special Elite",
            },
        )
        self.assertTrue(set(excluded).isdisjoint(manifest_families))
        self.assertIn("M PLUS Rounded 1c", {candidate.label for candidate in candidates})

        self.assertEqual(len(manifest["fonts"]), expected_count)
        for entry in manifest["fonts"]:
            font = catalog / entry["font"]
            license_path = catalog / entry["license"]
            self.assertEqual(
                hashlib.sha256(font.read_bytes()).hexdigest(),
                entry["font_sha256"],
            )
            self.assertEqual(
                hashlib.sha256(license_path.read_bytes()).hexdigest(),
                entry["license_sha256"],
            )
            source_path = font.parent / "source.json"
            metadata_path = font.parent / "METADATA.pb"
            self.assertEqual(source_path.exists(), metadata_path.exists())
            if source_path.is_file():
                source = json.loads(source_path.read_text(encoding="utf-8"))
                self.assertEqual(source["source"], "google-fonts-ofl")
                self.assertEqual(source["font_filename"], font.name)
                self.assertEqual(source["font_sha256"], entry["font_sha256"])
                self.assertEqual(
                    source["license_sha256"], entry["license_sha256"]
                )
                self.assertEqual(
                    source["metadata_sha256"],
                    hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
                )
            rendered = ImageFont.truetype(str(font), 32)
            required = (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "abcdefghijklmnopqrstuvwxyz"
                "0123456789-.'"
            )
            for character in required:
                self.assertIsNotNone(rendered.getmask(character).getbbox())

    def test_default_catalog_resolves_to_repository_assets(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        self.assertEqual(
            default_local_font_catalog(),
            repository / "assets" / "fonts",
        )
