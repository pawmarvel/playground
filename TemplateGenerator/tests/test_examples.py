from __future__ import annotations

import json
import unittest
from pathlib import Path

from PIL import Image
from jsonschema import Draft202012Validator, FormatChecker

from pawmarvel_generator.font_reference import load_font_reference
from pawmarvel_generator.fixture_set import load_fixture_set
from pawmarvel_generator.layout_reference import (
    load_layout_reference,
    map_reference_box,
)


class RepositoryExampleTests(unittest.TestCase):
    def test_repository_schemas_are_valid_draft_2020_12(self) -> None:
        project = Path(__file__).resolve().parents[1]
        schemas = sorted((project / "schemas").glob("*.json"))
        self.assertTrue(schemas)
        for schema in schemas:
            with self.subTest(schema=schema.name):
                value = json.loads(schema.read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(value)

    def test_checked_in_json_inputs_match_their_schemas(self) -> None:
        project = Path(__file__).resolve().parents[1]
        checks = [
            (
                "evaluation-protocol-v1.schema.json",
                "examples/authoring/evaluation-protocols/mvp-image-v1.json",
            ),
            (
                "fixture-set-v2.schema.json",
                "examples/authoring/fixture-sets/mvp-pets-v1/fixture-set.json",
            ),
            (
                "fixture-set-v2.schema.json",
                "examples/authoring/fixture-sets/mvp-pets-smoke-v1/fixture-set.json",
            ),
            (
                "font-reference-v1.schema.json",
                "examples/life-is-good/font-reference.json",
            ),
            (
                "layout-reference-v1.schema.json",
                "examples/life-is-good/layout-reference.json",
            ),
            (
                "product-profile-v1.schema.json",
                "profiles/blanket-king-9375x12375.json",
            ),
            (
                "product-profile-v1.schema.json",
                "profiles/blanket-twin-full-7875x9375.json",
            ),
        ]
        for schema_name, instance_name in checks:
            with self.subTest(instance=instance_name):
                schema = json.loads(
                    (project / "schemas" / schema_name).read_text(encoding="utf-8")
                )
                instance = json.loads(
                    (project / instance_name).read_text(encoding="utf-8")
                )
                Draft202012Validator(
                    schema,
                    format_checker=FormatChecker(),
                ).validate(instance)

    def test_repository_fixtures_are_complete_and_decodable(self) -> None:
        project = Path(__file__).resolve().parents[1]
        examples = project / "examples"
        design_fixtures = ("life-is-good", "charlie-well-trained")
        self.assertEqual(
            {
                path.name
                for path in examples.iterdir()
                if path.is_dir()
                and path.name not in {
                    "authoring",
                    "pet-inputs",
                }
            },
            set(design_fixtures),
        )
        for fixture in design_fixtures:
            root = examples / fixture
            self.assertTrue(
                {
                    "reference-design.png",
                    "art-template-gpt.md",
                    "art-template-gemini.md",
                    "pet-transform-gpt.md",
                    "pet-transform-gemini.md",
                }.issubset({path.name for path in root.iterdir()}),
            )
            with Image.open(root / "reference-design.png") as image:
                image.load()
                self.assertGreater(image.width, 0)
                self.assertGreater(image.height, 0)
            for prompt in (
                "art-template-gpt.md",
                "art-template-gemini.md",
                "pet-transform-gpt.md",
                "pet-transform-gemini.md",
            ):
                self.assertGreater(
                    len((root / prompt).read_text(encoding="utf-8").strip()),
                    100,
                )

        pet_names = {
            "australian-shepherd.png", "beagle.jpg", "bernese-mountain.png",
            "doodle.png", "french-bulldog.jpg", "german-shepherd.jpg",
            "golden-retriever.png", "great-dane.jpg", "greyhound.jpg",
            "sausage-dog-puppy.png", "white-fluffy-dog.png",
        }
        self.assertEqual(
            {
                path.name
                for path in (examples / "pet-inputs").iterdir()
                if path.suffix.lower() in {".png", ".jpg", ".jpeg"}
            },
            pet_names,
        )
        self.assertTrue((examples / "pet-inputs" / "ONLINE_FIXTURE_ATTRIBUTIONS.md").is_file())
        for name in sorted(pet_names):
            with self.subTest(pet=name), Image.open(examples / "pet-inputs" / name) as image:
                image.load()
                self.assertGreater(image.width, 0)
                self.assertGreater(image.height, 0)

        smoke = load_fixture_set(
            examples / "authoring/fixture-sets/mvp-pets-smoke-v1/fixture-set.json"
        )
        release = load_fixture_set(
            examples / "authoring/fixture-sets/mvp-pets-v1/fixture-set.json"
        )
        self.assertEqual((smoke.tier, len(smoke.fixtures)), ("smoke", 3))
        self.assertEqual((release.tier, len(release.fixtures)), ("release", 11))
        self.assertEqual(smoke.attempts_per_fixture, 1)
        self.assertEqual(release.attempts_per_fixture, 1)

        life_root = examples / "life-is-good"
        font_reference = load_font_reference(
            life_root / "font-reference.json",
            life_root / "reference-design.png",
        )
        self.assertEqual(font_reference.text, "CHARLIE")
        layout_reference = load_layout_reference(
            life_root / "layout-reference.json",
            life_root / "reference-design.png",
        )
        self.assertEqual(
            layout_reference.name_region.to_dict(),
            font_reference.region.to_dict(),
        )
        self.assertLess(layout_reference.pet_region.y, 50)
        self.assertEqual(
            map_reference_box(
                layout_reference.pet_region,
                reference_size=(242, 265),
                canvas_size=(800, 1056),
            ).to_dict(),
            {"x": 162, "y": 143, "width": 479, "height": 590},
        )
        self.assertEqual(
            map_reference_box(
                layout_reference.name_region,
                reference_size=(242, 265),
                canvas_size=(800, 1056),
            ).to_dict(),
            {"x": 40, "y": 713, "width": 720, "height": 215},
        )

if __name__ == "__main__":
    unittest.main()
