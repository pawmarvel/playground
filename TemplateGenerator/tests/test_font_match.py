from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pawmarvel_generator.font_catalog import discover_font_catalog
from pawmarvel_generator.font_match import rank_fonts


class FontMatchTests(unittest.TestCase):
    def test_ranks_the_rendered_reference_font_first(self) -> None:
        catalog = Path(__file__).resolve().parents[1] / "assets" / "fonts"
        candidates = discover_font_catalog(None, catalog_roots=(catalog,))
        expected = next(value for value in candidates if value.font.name == "AmaticSC-Bold.ttf")
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            image = Image.new("RGB", (400, 200), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.truetype(str(expected.font), 74)
            draw.text((70, 65), "CHARLIE", font=font, fill="black")
            image.save(reference)
            matches = rank_fonts(
                reference,
                # Deliberately offset from the screenshot lettering. Authoring
                # geometry is approximate until the operator confirms it.
                {"x": 50, "y": 5, "width": 300, "height": 70},
                image.size,
                "CHARLIE",
                candidates,
            )
        self.assertEqual(matches[0].candidate.font.name, "AmaticSC-Bold.ttf")
        self.assertGreater(matches[0].confidence, 0)
        self.assertEqual(matches[0].confidence, matches[0].score)
        self.assertEqual(len(matches), 40)
        self.assertEqual(
            [match.score for match in matches],
            sorted((match.score for match in matches), reverse=True),
        )

    def test_reports_zero_confidence_when_reference_has_no_visible_ink(self) -> None:
        catalog = Path(__file__).resolve().parents[1] / "assets" / "fonts"
        candidates = discover_font_catalog(None, catalog_roots=(catalog,))
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            Image.new("RGB", (400, 200), "white").save(reference)
            matches = rank_fonts(
                reference,
                {"x": 50, "y": 50, "width": 300, "height": 70},
                (400, 200),
                "CHARLIE",
                candidates,
            )

        self.assertEqual(len(matches), 40)
        self.assertTrue(all(match.confidence == 0 for match in matches))


if __name__ == "__main__":
    unittest.main()
