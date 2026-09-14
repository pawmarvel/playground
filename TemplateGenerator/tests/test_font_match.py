from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pawmarvel_generator.font_catalog import discover_font_catalog
from pawmarvel_generator.font_match import (
    rank_fonts,
    recommend_font_size,
    recommend_min_font_size_for_capacity,
)
from pawmarvel_generator.font_reference import (
    font_reference_from_editor,
    load_font_reference,
)


class FontMatchTests(unittest.TestCase):
    def test_minimum_size_defaults_to_twelve_character_capacity(self) -> None:
        catalog = Path(__file__).resolve().parents[1] / "assets" / "fonts"
        candidates = discover_font_catalog(None, catalog_roots=(catalog,))
        font_path = candidates[0].font

        size = recommend_min_font_size_for_capacity(
            font_path,
            box_width=300,
            box_height=80,
            padding=4,
        )

        font = ImageFont.truetype(str(font_path), size)
        bounds = font.getbbox("W" * 12)
        self.assertLessEqual(bounds[2] - bounds[0], 292)
        self.assertLessEqual(bounds[3] - bounds[1], 72)

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
                font_reference_from_editor(
                    reference=reference,
                    region={"x": 60, "y": 55, "width": 300, "height": 90},
                    text="CHARLIE",
                ),
                candidates,
            )
        self.assertEqual(matches[0].candidate.font.name, "AmaticSC-Bold.ttf")
        self.assertGreater(matches[0].confidence, 0)
        self.assertIn(matches[0].confidence_level, {"low", "medium", "high"})
        self.assertGreaterEqual(matches[0].confidence, 0)
        self.assertEqual(len(matches), len(candidates))
        self.assertEqual(
            [match.score for match in matches],
            sorted((match.score for match in matches), reverse=True),
        )
        self.assertEqual(
            [match.confidence for match in matches],
            sorted((match.confidence for match in matches), reverse=True),
        )

    def test_reports_zero_confidence_when_reference_has_no_visible_ink(self) -> None:
        catalog = Path(__file__).resolve().parents[1] / "assets" / "fonts"
        candidates = discover_font_catalog(None, catalog_roots=(catalog,))
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            Image.new("RGB", (400, 200), "white").save(reference)
            matches = rank_fonts(
                reference,
                font_reference_from_editor(
                    reference=reference,
                    region={"x": 50, "y": 50, "width": 300, "height": 70},
                    text="CHARLIE",
                ),
                candidates,
            )

        self.assertEqual(len(matches), len(candidates))
        self.assertTrue(all(match.confidence == 0 for match in matches))

    def test_life_is_good_confirmed_region_does_not_recommend_script(self) -> None:
        project = Path(__file__).resolve().parents[1]
        reference = project / "examples" / "life-is-good" / "reference-design.png"
        font_reference = load_font_reference(
            project / "examples" / "life-is-good" / "font-reference.json",
            reference,
        )
        candidates = discover_font_catalog(
            None, catalog_roots=(project / "assets" / "fonts",)
        )

        matches = rank_fonts(reference, font_reference, candidates)

        self.assertEqual(matches[0].candidate.font.name, "LeagueGothic-wdth.ttf")
        self.assertEqual(matches[0].confidence_level, "high")
        self.assertNotIn(
            "Pacifico-Regular.ttf",
            {match.candidate.font.name for match in matches[:15]},
        )
        scale = recommend_font_size(
            reference,
            font_reference,
            matches[0].candidate.font,
            box_width=726,
            box_height=215,
            padding=0,
        )
        self.assertGreaterEqual(scale.font_size_px, 270)
        self.assertLessEqual(scale.font_size_px, 285)
        self.assertGreater(scale.horizontal_fill, 0.8)


if __name__ == "__main__":
    unittest.main()
