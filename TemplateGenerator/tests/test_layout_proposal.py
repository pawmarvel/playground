from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from helpers import copy_font, make_transparent_mark
from pawmarvel_generator.config import load_layout
from pawmarvel_generator.layout_proposal import LayoutProposalError, propose_layout


class LayoutProposalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.product = self.root / "authoring" / "design" / "profile"
        self.pet_experiment = self.product / "experiments" / "pet" / "pet-gpt-v01"
        self.layout_experiment = self.product / "experiments" / "layout" / "layout-v01"
        self.inputs = self.layout_experiment / "inputs"
        self.inputs.mkdir(parents=True)
        self._art(self.inputs / "art.png")
        make_transparent_mark(
            self.inputs / "transformed-pet.png",
            size=(160, 180),
            color=(200, 100, 50, 255),
        )
        for fixture, size in (("small", (120, 180)), ("wide", (210, 150))):
            attempt = self.pet_experiment / "attempts" / f"release-{fixture}-0001"
            make_transparent_mark(
                attempt / "outputs" / "transformed-pet.png",
                size=size,
                color=(80, 120, 180, 255),
            )
            self._write_json(
                attempt / "run.json",
                {
                    "schema_version": 1,
                    "attempt_id": attempt.name,
                    "experiment_id": "pet-gpt-v01",
                    "kind": "pet",
                    "status": "succeeded",
                },
            )
        scratch_attempt = self.pet_experiment / "attempts" / "attempt-0001"
        make_transparent_mark(
            scratch_attempt / "outputs" / "transformed-pet.png",
            size=(145, 205),
            color=(100, 160, 90, 255),
        )
        self._write_json(
            scratch_attempt / "run.json",
            {
                "schema_version": 1,
                "attempt_id": scratch_attempt.name,
                "experiment_id": "pet-gpt-v01",
                "kind": "pet",
                "status": "succeeded",
            },
        )
        self._write_json(
            self.pet_experiment / "experiment.json",
            {
                "schema_version": 1,
                "experiment_id": "pet-gpt-v01",
                "kind": "pet",
                "design_id": "design",
                "product_profile_id": "profile",
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    @staticmethod
    def _art(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (300, 420), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((15, 15, 285, 75), fill=(0, 0, 0, 255))
        draw.rectangle((15, 350, 285, 405), fill=(0, 0, 0, 255))
        image.save(path, format="PNG")

    def _layout_record(
        self,
        *,
        embedded_name: str | None = None,
        fonts: bool = False,
        reference: bool = False,
    ) -> None:
        pinned = self.pet_experiment / "attempts" / "release-small-0001"
        record = json.loads((pinned / "run.json").read_text(encoding="utf-8"))
        if embedded_name is not None:
            record["prompt_variables"] = {"pet_name": embedded_name}
            self._write_json(pinned / "run.json", record)
        inputs: dict = {
            "art": {"path": "inputs/art.png"},
            "representative_pet": {"path": "inputs/transformed-pet.png"},
            "pet_attempt": {
                "path": "experiments/pet/pet-gpt-v01/attempts/release-small-0001"
            },
            "font_catalogs": [],
        }
        if fonts:
            family = self.inputs / "font-catalog-01" / "family-001"
            family.mkdir(parents=True)
            copy_font(family)
            inputs["font_catalogs"] = [{"path": "inputs/font-catalog-01"}]
        if reference:
            reference_path = self.inputs / "reference-design.png"
            image = Image.new("RGB", (300, 420), (250, 250, 250))
            draw = ImageDraw.Draw(image)
            draw.rectangle((15, 15, 285, 75), fill=(0, 0, 0))
            draw.rectangle((15, 350, 285, 405), fill=(0, 0, 0))
            draw.rounded_rectangle(
                (38, 105, 262, 335), radius=30, fill=(180, 70, 45)
            )
            image.save(reference_path, format="PNG")
            inputs["reference"] = {
                "path": "inputs/reference-design.png",
                "selection": "inferred-shared-upstream",
                "used_by": ["art", "pet"],
            }
        self._write_json(
            self.layout_experiment / "experiment.json",
            {
                "schema_version": 1,
                "experiment_id": "layout-v01",
                "kind": "layout",
                "design_id": "design",
                "product_profile_id": "profile",
                "status": "draft",
                "inputs": inputs,
            },
        )

    def test_embedded_name_proposal_ranks_importable_candidates(self) -> None:
        self._layout_record(embedded_name="MILO")
        result = propose_layout(
            experiment=self.layout_experiment,
            proposal_id="proposal-v01",
            name_mode="embedded-in-pet",
            max_candidates=20,
            finalists=3,
        )
        proposal = json.loads((result / "proposal.json").read_text(encoding="utf-8"))
        self.assertEqual(proposal["name_mode"], "embedded-in-pet")
        self.assertEqual(len(proposal["pet_evidence"]["successful_attempts"]), 3)
        self.assertEqual(
            proposal["pet_evidence"]["attempt_filter"], {"mode": "all-successful"}
        )
        self.assertEqual(len(proposal["candidates"]), 3)
        self.assertEqual(
            proposal["search"]["method"], "bounded-alpha-clearance-grid-v3"
        )
        self.assertEqual(
            proposal["search"]["finalist_strategy"],
            "best-fit-nearby-refinements",
        )
        self.assertFalse(proposal["seed"]["authoritative"])
        self.assertTrue(proposal["warnings"])
        self.assertTrue((result / "ranked-layout-proposals.png").is_file())
        scores = [candidate["metrics"]["score"] for candidate in proposal["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        layouts = [
            load_layout((result / candidate["layout"]).parent)
            for candidate in proposal["candidates"]
        ]
        best = layouts[0].pet_box
        for layout in layouts[1:]:
            self.assertGreaterEqual(layout.pet_box.width / best.width, 0.88)
            self.assertLessEqual(layout.pet_box.width / best.width, 1.12)
        for candidate in proposal["candidates"]:
            layout_path = result / candidate["layout"]
            self.assertFalse(load_layout(layout_path.parent).has_name)
            self.assertTrue((result / candidate["matrix"]).is_file())

    def test_finished_reference_estimates_prominence_without_prior_layout(self) -> None:
        self._layout_record(embedded_name="MILO", reference=True)
        result = propose_layout(
            experiment=self.layout_experiment,
            proposal_id="proposal-reference-v01",
            name_mode="embedded-in-pet",
            max_candidates=20,
            finalists=3,
        )
        proposal = json.loads((result / "proposal.json").read_text(encoding="utf-8"))
        self.assertEqual(proposal["seed"]["source"], "reference-estimate")
        self.assertTrue(proposal["seed"]["reference_estimate_used"])
        self.assertGreaterEqual(
            proposal["reference_prominence"]["confidence"], 0.65
        )
        self.assertEqual(
            proposal["reference_evidence"]["used_by"], ["art", "pet"]
        )
        self.assertEqual(
            proposal["reference_evidence"]["selection"],
            "inferred-shared-upstream",
        )
        best = proposal["candidates"][0]["metrics"]
        self.assertGreater(best["representative_pet_canvas_width_ratio"], 0.65)
        self.assertLess(best["representative_prominence_distance"], 0.2)

    def test_layout_text_proposal_tests_names_and_copies_ofl_font(self) -> None:
        self._layout_record(fonts=True)
        result = propose_layout(
            experiment=self.layout_experiment,
            proposal_id="proposal-text-v01",
            name_mode="layout-text",
            pet_names=("BO", "MARSHMALLOW"),
            max_candidates=12,
            finalists=2,
        )
        proposal = json.loads((result / "proposal.json").read_text(encoding="utf-8"))
        self.assertEqual(proposal["search"]["pet_name_probes"], ["BO", "MARSHMALLOW"])
        for candidate in proposal["candidates"]:
            layout_path = result / candidate["layout"]
            layout = load_layout(layout_path.parent)
            self.assertTrue(layout.has_name)
            self.assertTrue((layout_path.parent / "fonts" / "OFL.txt").is_file())
            metrics = json.loads((layout_path.parent / "metrics.json").read_text())
            self.assertEqual(len(metrics["fixture_name_matrix"]), 6)

    def test_rejects_existing_proposal_and_invalid_budget(self) -> None:
        self._layout_record(embedded_name="MILO")
        propose_layout(
            experiment=self.layout_experiment,
            proposal_id="proposal-v01",
            name_mode="embedded-in-pet",
            finalists=1,
        )
        with self.assertRaisesRegex(LayoutProposalError, "already exists"):
            propose_layout(
                experiment=self.layout_experiment,
                proposal_id="proposal-v01",
                name_mode="embedded-in-pet",
                finalists=1,
            )
        with self.assertRaisesRegex(LayoutProposalError, "max candidates"):
            propose_layout(
                experiment=self.layout_experiment,
                proposal_id="proposal-v02",
                name_mode="embedded-in-pet",
                max_candidates=61,
            )

    def test_requires_literal_prefix_with_matching_attempts(self) -> None:
        self._layout_record(embedded_name="MILO")
        with self.assertRaisesRegex(LayoutProposalError, "without whitespace"):
            propose_layout(
                experiment=self.layout_experiment,
                proposal_id="proposal-invalid-prefix",
                name_mode="embedded-in-pet",
                attempt_prefix="release-*",
            )
        with self.assertRaisesRegex(LayoutProposalError, "no pet attempts match"):
            propose_layout(
                experiment=self.layout_experiment,
                proposal_id="proposal-no-attempts",
                name_mode="embedded-in-pet",
                attempt_prefix="smoke-",
            )


if __name__ == "__main__":
    unittest.main()
