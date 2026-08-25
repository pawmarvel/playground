from __future__ import annotations

import unittest
from pathlib import Path

from PIL import Image


class RepositoryExampleTests(unittest.TestCase):
    def test_repository_fixtures_are_complete_and_decodable(self) -> None:
        examples = Path(__file__).resolve().parents[1] / "examples"
        fixtures = {
            "life-is-good": (
                ("reference-design.png", "pet-input.png"),
                ("art-template.md", "pet-transform-baseline.md"),
            ),
            "charlie-well-trained": (
                ("reference-design.png", "pet-input.png"),
                ("art-template.md",),
            ),
        }

        for fixture, (images, prompts) in fixtures.items():
            root = examples / fixture
            for name in images:
                path = root / name
                with self.subTest(fixture=fixture, path=name), Image.open(path) as image:
                    image.load()
                    self.assertGreater(image.width, 0)
                    self.assertGreater(image.height, 0)
            for name in prompts:
                path = root / name
                with self.subTest(fixture=fixture, path=name):
                    self.assertGreater(
                        len(path.read_text(encoding="utf-8").strip()), 100
                    )


if __name__ == "__main__":
    unittest.main()
