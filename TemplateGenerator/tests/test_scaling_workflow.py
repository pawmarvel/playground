from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from pawmarvel_generator.artifact_io import atomic_json, sha256
from pawmarvel_generator.scaling_workflow import (
    ScalingWorkflowError,
    initialize_workflow,
    plan_workflow,
    record_workflow_review,
    run_workflow,
    workflow_status,
)


class ScalingWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parents[1]
        cls.profile = cls.repository / "profiles/t-shirt-3423x4533.json"
        cls.art_prompt = cls.repository / "examples/life-is-good/art-template-gpt.md"
        cls.pet_prompt = cls.repository / "examples/life-is-good/pet-transform-gpt.md"
        cls.reference = cls.repository / "examples/life-is-good/reference-design.png"
        cls.smoke = (
            cls.repository
            / "examples/authoring/fixture-sets/mvp-pets-smoke-v1/fixture-set.json"
        )
        cls.release = (
            cls.repository
            / "examples/authoring/fixture-sets/mvp-pets-v1/fixture-set.json"
        )
        cls.protocol = (
            cls.repository
            / "examples/authoring/evaluation-protocols/mvp-image-v1.json"
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _initialize(self, **overrides: object) -> Path:
        arguments: dict[str, object] = {
            "project_root": self.project,
            "workflow_id": "life-shirt-v01",
            "scenario": "new-design",
            "design_id": "life-is-good",
            "product_profile": self.profile,
            "art_prompt": self.art_prompt,
            "pet_prompt": self.pet_prompt,
            "references": [self.reference],
            "source_bundle": None,
            "variant_delta": None,
            "name_mode": "layout-text",
            "pet_name": None,
            "art_provider": "openai",
            "art_model": "gpt-image-2.5-sunburst",
            "art_quality": "high",
            "pet_provider": "openai",
            "pet_model": "gpt-image-2.5-sunburst",
            "pet_quality": "low",
            "smoke_fixture_set": self.smoke,
            "release_fixture_set": self.release,
            "evaluation_protocol": self.protocol,
            "smoke_fixture_count": 2,
            "release_fixture_count": 4,
            "skip_smoke": False,
            "scratch_approved": True,
            "created_by": "test-operator",
            "empty_canvas": False,
            "max_paid_calls": 24,
            "force": False,
        }
        arguments.update(overrides)
        return initialize_workflow(**arguments)  # type: ignore[arg-type]

    def _schema(self, name: str) -> dict[str, object]:
        return json.loads(
            (self.repository / "schemas" / name).read_text(encoding="utf-8")
        )

    def test_initialization_snapshots_inputs_and_writes_stable_plan(self) -> None:
        spec_path = self._initialize()
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        Draft202012Validator(
            self._schema("authoring-workflow-v1.schema.json")
        ).validate(spec)
        self.assertFalse(Path(spec["authoring_root"]).is_absolute())
        self.assertEqual(spec["execution"]["max_paid_calls"], 24)
        self.assertTrue(
            (spec_path.parent / spec["inputs"]["references"][0]).is_file()
        )

        plan_path = plan_workflow(
            spec_path=spec_path, checkpoint="smoke-review"
        )
        self.assertEqual(
            plan_workflow(spec_path=spec_path, checkpoint="smoke-review"),
            plan_path,
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        Draft202012Validator(
            self._schema("authoring-plan-v1.schema.json")
        ).validate(plan)
        self.assertEqual(plan["estimated_paid_calls"], 3)
        self.assertEqual(plan["quality_gate_evidence"], {})

        with self.assertRaisesRegex(
            ScalingWorkflowError, "cannot replace a workflow with planned"
        ):
            self._initialize(force=True)

    def test_default_release_selection_adds_three_non_smoke_fixtures(self) -> None:
        spec_path = self._initialize(
            smoke_fixture_count=None,
            release_fixture_count=None,
        )
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        fixture_sets = spec["inputs"]["fixture_sets"]
        smoke = json.loads(
            (spec_path.parent / fixture_sets["smoke"]["selection"]).read_text(
                encoding="utf-8"
            )
        )
        release = json.loads(
            (spec_path.parent / fixture_sets["release"]["selection"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(smoke["selected_fixture_ids"]), 3)
        self.assertEqual(len(release["selected_fixture_ids"]), 3)
        self.assertFalse(
            set(smoke["selected_fixture_ids"])
            & set(release["selected_fixture_ids"])
        )
        self.assertEqual(release["cumulative_fixture_count"], 6)
        self.assertEqual(release["warnings"], [])

    def test_skip_smoke_uses_six_release_fixtures_without_prior_coverage(self) -> None:
        spec_path = self._initialize(
            smoke_fixture_count=None,
            release_fixture_count=None,
            skip_smoke=True,
        )
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        release_entry = spec["inputs"]["fixture_sets"]["release"]
        release = json.loads(
            (spec_path.parent / release_entry["selection"]).read_text(encoding="utf-8")
        )
        self.assertFalse(spec["execution"]["smoke_enabled"])
        self.assertEqual(len(release["selected_fixture_ids"]), 6)
        self.assertIsNone(release["prior_coverage"])
        with self.assertRaisesRegex(ScalingWorkflowError, "smoke-review is disabled"):
            plan_workflow(spec_path=spec_path, checkpoint="smoke-review")
        art_plan = json.loads(
            plan_workflow(spec_path=spec_path, checkpoint="art-review").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(art_plan["estimated_paid_calls"], 1)

        product = (
            spec_path.parent
            / spec["authoring_root"]
            / spec["target"]["design_id"]
            / spec["target"]["product_profile_id"]
        ).resolve()
        atomic_json(
            product
            / "reviews"
            / "art"
            / spec["ids"]["art_review"]
            / "evaluation.json",
            {
                "review_id": spec["ids"]["art_review"],
                "kind": "art",
                "design_id": spec["target"]["design_id"],
                "product_profile_id": spec["target"]["product_profile_id"],
                "evaluation_protocol_sha256": sha256(
                    spec_path.parent / spec["inputs"]["evaluation_protocol"]
                ),
                "fixture_set_sha256": None,
                "fixture_selection": None,
                "attempt_id_prefix": None,
                "candidates": [
                    {"experiment_id": spec["ids"]["art_experiment"]}
                ],
                "hard_gates": {"status": "passed"},
                "warnings": [],
            },
        )
        record_workflow_review(
            spec_path=spec_path,
            gate="candidates",
            reviewed_by="application-owner",
            notes="Art accepted; smoke intentionally skipped",
        )
        release_plan = json.loads(
            plan_workflow(spec_path=spec_path, checkpoint="release-review").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(release_plan["estimated_paid_calls"], 7)
        self.assertNotIn(
            "benchmark-pet-smoke",
            {task["task_id"] for task in release_plan["tasks"]},
        )
        self.assertIn(
            "benchmark-pet-release",
            {task["task_id"] for task in release_plan["tasks"]},
        )

    def test_release_plan_requires_hash_bound_candidate_review(self) -> None:
        spec_path = self._initialize()
        with self.assertRaisesRegex(
            ScalingWorkflowError, "requires a recorded candidates approval"
        ):
            plan_workflow(spec_path=spec_path, checkpoint="release-review")

        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        product = (
            spec_path.parent
            / spec["authoring_root"]
            / spec["target"]["design_id"]
            / spec["target"]["product_profile_id"]
        ).resolve()
        for kind, review_id in (
            ("art", spec["ids"]["art_review"]),
            ("pet", spec["ids"]["smoke_review"]),
        ):
            fixture_entry = spec["inputs"]["fixture_sets"]["smoke"]
            atomic_json(
                product / "reviews" / kind / review_id / "evaluation.json",
                {
                    "review_id": review_id,
                    "kind": kind,
                    "design_id": spec["target"]["design_id"],
                    "product_profile_id": spec["target"]["product_profile_id"],
                    "evaluation_protocol_sha256": sha256(
                        spec_path.parent / spec["inputs"]["evaluation_protocol"]
                    ),
                    "fixture_set_sha256": (
                        sha256(spec_path.parent / fixture_entry["manifest"])
                        if kind == "pet"
                        else None
                    ),
                    "fixture_selection": (
                        {
                            "config_sha256": sha256(
                                spec_path.parent / fixture_entry["selection"]
                            )
                        }
                        if kind == "pet"
                        else None
                    ),
                    "attempt_id_prefix": "smoke-" if kind == "pet" else None,
                    "candidates": [
                        {
                            "experiment_id": (
                                spec["ids"]["pet_experiment"]
                                if kind == "pet"
                                else spec["ids"]["art_experiment"]
                            )
                        }
                    ],
                    "hard_gates": {"status": "passed"},
                    "warnings": [],
                },
            )
        approval = record_workflow_review(
            spec_path=spec_path,
            gate="candidates",
            reviewed_by="application-owner",
            notes="Art and smoke outputs accepted",
        )
        plan_path = plan_workflow(
            spec_path=spec_path, checkpoint="release-review"
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(
            plan["quality_gate_evidence"]["candidates"]["sha256"],
            sha256(approval),
        )
        self.assertEqual(plan["estimated_paid_calls"], 7)

    def test_plan_is_invalidated_when_snapshotted_prompt_changes(self) -> None:
        spec_path = self._initialize()
        plan_path = plan_workflow(spec_path=spec_path, checkpoint="art-review")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        prompt = spec_path.parent / spec["inputs"]["art_prompt"]
        prompt.write_text(prompt.read_text(encoding="utf-8") + "\nchanged\n")
        with self.assertRaisesRegex(
            ScalingWorkflowError, "workflow inputs changed after planning"
        ):
            run_workflow(plan_path=plan_path)

    def test_scratch_review_binds_the_current_input_inventory(self) -> None:
        spec_path = self._initialize(
            workflow_id="scratch-review-v01", scratch_approved=False
        )
        with self.assertRaisesRegex(
            ScalingWorkflowError, "scratch quality gate is not approved"
        ):
            plan_workflow(spec_path=spec_path, checkpoint="art-review")
        record_workflow_review(
            spec_path=spec_path,
            gate="scratch",
            reviewed_by="application-owner",
            notes="Representative scratch outputs accepted",
        )
        plan_workflow(spec_path=spec_path, checkpoint="art-review")

        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        prompt = spec_path.parent / spec["inputs"]["pet_prompt"]
        prompt.write_text(prompt.read_text(encoding="utf-8") + "\nchanged\n")
        with self.assertRaisesRegex(
            ScalingWorkflowError, "inputs changed after scratch approval"
        ):
            plan_workflow(spec_path=spec_path, checkpoint="art-review")

    def test_empty_canvas_art_checkpoint_runs_offline_and_resumes(self) -> None:
        spec_path = self._initialize(
            workflow_id="empty-shirt-v01",
            design_id="empty-shirt",
            art_prompt=None,
            references=[],
            empty_canvas=True,
            name_mode="none",
        )
        plan_path = plan_workflow(spec_path=spec_path, checkpoint="art-review")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["estimated_paid_calls"], 0)
        run_path = run_workflow(plan_path=plan_path)
        self.assertEqual(
            json.loads(run_path.read_text(encoding="utf-8"))["status"],
            "succeeded",
        )
        run_workflow(plan_path=plan_path)
        status = workflow_status(spec_path=spec_path)
        self.assertTrue(status["exists"]["art_review"])

    def test_name_mode_and_prompt_token_must_match(self) -> None:
        with self.assertRaisesRegex(
            ScalingWorkflowError,
            r"embedded-in-pet workflow requires \{\{PET_NAME\}\}",
        ):
            self._initialize(
                name_mode="embedded-in-pet",
                pet_name="SAUSAGE",
            )

    def test_category_variant_derives_reviewable_prompt_copies(self) -> None:
        source = self.project / "source-bundle"
        source.mkdir()
        (source / "bundle.json").write_text("{}\n", encoding="utf-8")
        (source / "product-profile.json").write_bytes(self.profile.read_bytes())
        (source / "art-template-gpt.md").write_bytes(self.art_prompt.read_bytes())
        (source / "pet-transform-gpt.md").write_bytes(self.pet_prompt.read_bytes())
        (source / "reference-design.png").write_bytes(self.reference.read_bytes())
        delta = self.project / "variant-delta.json"
        atomic_json(
            delta,
            {
                "preserve": ["illustration medium"],
                "change": ["headline to PORCH SUPERVISOR"],
                "forbid": ["fixed pet identity"],
            },
        )
        manifest = {
            "template_id": "life-is-good--blanket-king-9375x12375",
            "bundle_revision": 1,
            "design_id": "life-is-good",
            "product_profile_id": "blanket-king-9375x12375",
            "prompts": {
                "art_template": "art-template-gpt.md",
                "pet_transform": "pet-transform-gpt.md",
            },
        }
        with patch(
            "pawmarvel_generator.scaling_workflow.validate_production_bundle",
            return_value=manifest,
        ):
            spec_path = self._initialize(
                workflow_id="porch-shirt-v01",
                scenario="category-variant",
                design_id="porch-supervisor",
                art_prompt=None,
                pet_prompt=None,
                references=[self.reference],
                source_bundle=source,
                variant_delta=delta,
            )
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        derived = (
            spec_path.parent / spec["inputs"]["art_prompt"]
        ).read_text(encoding="utf-8")
        self.assertIn("Authoring variant requirements", derived)
        self.assertIn("headline to PORCH SUPERVISOR", derived)

    def test_profile_expansion_inherits_production_pet_runtime_and_name_mode(self) -> None:
        source = self.project / "embedded-source-bundle"
        source.mkdir()
        (source / "bundle.json").write_text("{}\n", encoding="utf-8")
        (source / "product-profile.json").write_bytes(self.profile.read_bytes())
        (source / "art-template-gpt.md").write_bytes(self.art_prompt.read_bytes())
        embedded_prompt = source / "pet-transform-gpt.md"
        embedded_prompt.write_text(
            "Transform the exact customer pet and render the artistic name "
            "{{PET_NAME}} inside the transparent pet cutout.\n",
            encoding="utf-8",
        )
        (source / "reference-design.png").write_bytes(self.reference.read_bytes())
        manifest = {
            "template_id": "life-is-good--blanket-king-9375x12375",
            "bundle_revision": 4,
            "design_id": "life-is-good",
            "product_profile_id": "blanket-king-9375x12375",
            "prompts": {
                "art_template": "art-template-gpt.md",
                "pet_transform": "pet-transform-gpt.md",
            },
            "runtime": {
                "provider": "openai",
                "model": "gpt-image-2",
                "request_parameters": {"quality": "medium"},
            },
            "renderer": {"name_mode": "embedded-in-pet"},
            "provenance": {"qa_fixture": {"pet_name": "CHARLIE"}},
        }
        with patch(
            "pawmarvel_generator.scaling_workflow.validate_production_bundle",
            return_value=manifest,
        ):
            spec_path = self._initialize(
                workflow_id="life-profile-v01",
                scenario="profile-expansion",
                art_prompt=None,
                pet_prompt=None,
                references=[],
                source_bundle=source,
                name_mode=None,
                pet_name=None,
                pet_provider=None,
                pet_model=None,
                pet_quality=None,
            )
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        self.assertEqual(spec["personalization"]["name_mode"], "embedded-in-pet")
        self.assertEqual(
            spec["personalization"]["representative_pet_name"], "CHARLIE"
        )
        self.assertEqual(
            spec["generation"]["pet"],
            {"provider": "openai", "model": "gpt-image-2", "quality": "medium"},
        )


if __name__ == "__main__":
    unittest.main()
