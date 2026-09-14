from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import make_image
from pawmarvel_generator.artifact_io import sha256
from pawmarvel_generator.config import Rect
from pawmarvel_generator.layout_reference import (
    LayoutReferenceError,
    layout_reference_from_editor,
    load_layout_reference,
    map_reference_box,
)


class LayoutReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.reference = make_image(self.root / "reference.png", size=(200, 300))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_round_trips_hash_bound_regions(self) -> None:
        value = layout_reference_from_editor(
            reference=self.reference,
            pet_region={"x": 20, "y": 30, "width": 100, "height": 120},
            name_region={"x": 10, "y": 205, "width": 180, "height": 60},
        )
        path = self.root / "layout-reference.json"
        path.write_text(json.dumps(value.to_dict()), encoding="utf-8")

        loaded = load_layout_reference(path, self.reference)

        self.assertEqual(loaded.reference_image_sha256, sha256(self.reference))
        self.assertEqual(loaded.pet_region.width, 100)
        self.assertEqual(loaded.name_region.height, 60)

    def test_rejects_region_outside_reference(self) -> None:
        with self.assertRaisesRegex(LayoutReferenceError, "fully inside"):
            layout_reference_from_editor(
                reference=self.reference,
                pet_region={"x": 190, "y": 10, "width": 20, "height": 20},
                name_region={"x": 10, "y": 205, "width": 180, "height": 60},
            )

    def test_rejects_directory_with_resolved_path(self) -> None:
        with self.assertRaisesRegex(
            LayoutReferenceError,
            rf"layout reference is not a regular file: {self.root.resolve()}",
        ):
            load_layout_reference(self.root, self.reference)

    def test_maps_normalized_edges_without_overflow_or_rounding_drift(self) -> None:
        mapped = map_reference_box(
            Rect(x=49, y=36, width=145, height=148),
            reference_size=(242, 265),
            canvas_size=(800, 1056),
        )
        self.assertEqual(
            mapped,
            Rect(x=162, y=143, width=479, height=590),
        )
        full = map_reference_box(
            Rect(x=0, y=0, width=242, height=265),
            reference_size=(242, 265),
            canvas_size=(800, 1056),
        )
        self.assertEqual(full, Rect(x=0, y=0, width=800, height=1056))


if __name__ == "__main__":
    unittest.main()
