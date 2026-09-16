from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import make_image
from pawmarvel_generator.artifact_io import sha256
from pawmarvel_generator.fixture_set import (
    FixtureSetError,
    load_fixture_selection,
    load_fixture_set,
    select_fixtures,
    write_fixture_selection,
)


class FixtureSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.images = [
            make_image(self.root / f"dog-{index}.png", color=(index * 20, 50, 90, 255))
            for index in range(1, 4)
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _manifest(self) -> Path:
        path = self.root / "fixture-set.json"
        fixtures = []
        for index, image in enumerate(self.images, 1):
            fixtures.append(
                {
                    "id": f"dog-{index}",
                    "pet_image": image.name,
                    "sha256": sha256(image),
                    "breed": {"id": f"breed-{index}", "label": f"Breed {index}", "mixed": False},
                    "size_class": ("toy", "medium", "large")[index - 1],
                    "morphology": ["compact" if index == 1 else "long-legs"],
                    "coat": {"length": "short", "texture": "smooth", "tone": "medium"},
                    "capture": {"framing": "full-body", "view": "front", "subject_coverage": "isolated", "background_complexity": "simple"},
                    "risk_tags": ["edge-detail"],
                    "rights": {"source_kind": "test", "license": "test-only", "reviewed": False, "intended_use": "test"},
                }
            )
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "fixture_set_id": "test-smoke-v1",
                    "tier": "smoke",
                    "attempts_per_fixture": 1,
                    "fixtures": fixtures,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_loads_smoke_inventory_and_reports_coverage_dimensions(self) -> None:
        fixture_set = load_fixture_set(self._manifest())
        self.assertEqual(fixture_set.tier, "smoke")
        self.assertEqual(len(fixture_set.fixtures), 3)
        self.assertEqual(fixture_set.summary()["size_classes"], ["large", "medium", "toy"])

    def test_rejects_changed_image_bytes_with_actionable_path(self) -> None:
        manifest = self._manifest()
        make_image(self.images[0], color=(255, 0, 0, 255))
        with self.assertRaisesRegex(FixtureSetError, r"SHA-256 mismatch.*dog-1.*resolved_path="):
            load_fixture_set(manifest)

    def test_enforces_tier_size_and_one_attempt(self) -> None:
        manifest = self._manifest()
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["attempts_per_fixture"] = 2
        manifest.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(FixtureSetError, "attempts_per_fixture must be 1"):
            load_fixture_set(manifest)

    def test_filters_use_or_within_field_and_and_across_fields(self) -> None:
        fixture_set = load_fixture_set(self._manifest())
        selected = select_fixtures(
            fixture_set,
            fixture_count=2,
            filters=("size_class=toy", "size_class=medium"),
        )
        self.assertEqual([fixture.id for fixture in selected], ["dog-1", "dog-2"])

        with self.assertRaisesRegex(FixtureSetError, "smoke run must select 2-3"):
            select_fixtures(
                fixture_set,
                filters=("size_class=toy", "morphology=compact"),
            )

    def test_rejects_unknown_filter_and_oversized_request(self) -> None:
        fixture_set = load_fixture_set(self._manifest())
        with self.assertRaisesRegex(FixtureSetError, "unsupported fixture filter"):
            select_fixtures(fixture_set, filters=("color=dark",))
        with self.assertRaisesRegex(FixtureSetError, "smaller than the requested"):
            select_fixtures(fixture_set, fixture_count=4)

    def test_release_inventory_and_run_require_at_least_six_dogs(self) -> None:
        manifest = self._manifest()
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["tier"] = "release"
        manifest.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(FixtureSetError, "release fixture set must contain 6-15"):
            load_fixture_set(manifest)

    def test_writes_reviewable_selection_and_honors_edited_id_order(self) -> None:
        manifest = self._manifest()
        selection = write_fixture_selection(
            manifest,
            output=self.root / "selection.json",
            fixture_count=2,
            filters=("id=dog-1", "id=dog-2", "id=dog-3"),
        )
        value = json.loads(selection.read_text(encoding="utf-8"))
        self.assertEqual(value["selected_fixture_ids"], ["dog-1", "dog-2"])
        value["selected_fixture_ids"] = ["dog-3", "dog-1"]
        selection.write_text(json.dumps(value), encoding="utf-8")

        selected = load_fixture_selection(load_fixture_set(manifest), selection)
        self.assertEqual([fixture.id for fixture in selected], ["dog-3", "dog-1"])

    def test_selection_pins_fixture_set_and_requires_force_to_replace(self) -> None:
        manifest = self._manifest()
        selection = write_fixture_selection(
            manifest,
            output=self.root / "selection.json",
        )
        with self.assertRaisesRegex(FixtureSetError, "already exists"):
            write_fixture_selection(manifest, output=selection)

        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["fixtures"][0]["rights"]["intended_use"] = "changed"
        manifest.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(FixtureSetError, "different fixture-set bytes"):
            load_fixture_selection(load_fixture_set(manifest), selection)


if __name__ == "__main__":
    unittest.main()
