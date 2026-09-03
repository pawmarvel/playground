from __future__ import annotations

import unittest
from pathlib import Path

from PIL import Image


class RepositoryExampleTests(unittest.TestCase):
    def test_repository_fixtures_are_complete_and_decodable(self) -> None:
        project = Path(__file__).resolve().parents[1]
        examples = project / "examples"
        for fixture in ("life-is-good", "charlie-well-trained"):
            root = examples / fixture
            self.assertEqual(
                {path.name for path in root.iterdir()},
                {
                    "reference-design.png",
                    "art-template-gpt.md",
                    "art-template-gemini.md",
                    "pet-transform-gpt.md",
                    "pet-transform-gemini.md",
                },
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

        for name in ("sausage-dog-puppy.png", "white-fluffy-dog.png"):
            with self.subTest(pet=name), Image.open(examples / "pet-inputs" / name) as image:
                image.load()
                self.assertGreater(image.width, 0)
                self.assertGreater(image.height, 0)

        self.assertFalse(project.joinpath("ai-prompts").exists())


if __name__ == "__main__":
    unittest.main()
