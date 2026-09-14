from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import make_image
from pawmarvel_generator.artifact_io import sha256
from pawmarvel_generator.font_reference import (
    FontReferenceError,
    font_reference_from_editor,
    load_font_reference,
    parse_font_reference,
)


class FontReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.reference = make_image(self.root / "reference.png", size=(200, 300))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_round_trips_hash_bound_reference_region(self) -> None:
        value = font_reference_from_editor(
            reference=self.reference,
            region={"x": 10, "y": 20, "width": 120, "height": 40},
            text="  CHARLIE  ",
        )
        path = self.root / "font-reference.json"
        path.write_text(json.dumps(value.to_dict()), encoding="utf-8")

        loaded = load_font_reference(path, self.reference)

        self.assertEqual(loaded.text, "CHARLIE")
        self.assertEqual(loaded.reference_image_sha256, sha256(self.reference))
        self.assertEqual(loaded.region.width, 120)

    def test_rejects_region_outside_reference(self) -> None:
        with self.assertRaisesRegex(FontReferenceError, "fully inside"):
            font_reference_from_editor(
                reference=self.reference,
                region={"x": 190, "y": 20, "width": 20, "height": 40},
                text="CHARLIE",
            )

    def test_rejects_reference_hash_mismatch(self) -> None:
        value = font_reference_from_editor(
            reference=self.reference,
            region={"x": 10, "y": 20, "width": 120, "height": 40},
            text="CHARLIE",
        ).to_dict()
        value["reference_image_sha256"] = "0" * 64
        path = self.root / "font-reference.json"
        path.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaisesRegex(FontReferenceError, "does not match"):
            load_font_reference(path, self.reference)

    def test_rejects_directory_with_resolved_path(self) -> None:
        with self.assertRaisesRegex(
            FontReferenceError,
            rf"font reference is not a regular file: {self.root.resolve()}",
        ):
            load_font_reference(self.root, self.reference)

    def test_rejects_non_hex_digest(self) -> None:
        with self.assertRaisesRegex(FontReferenceError, "SHA-256"):
            parse_font_reference(
                {
                    "schema_version": 1,
                    "reference_image_sha256": "z" * 64,
                    "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                    "text": "PET",
                },
                self.reference,
            )


if __name__ == "__main__":
    unittest.main()
