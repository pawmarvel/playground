from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from pawmarvel_generator.operator_workflow import (
    OperatorJobManager,
    OperatorWorkflowConfig,
    OperatorWorkflowError,
    OperatorWorkflowRunner,
    discover_operator_workflows,
    discover_releases,
)
from pawmarvel_generator.gallery_server import _operator_payload


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class OperatorWorkflowDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.authoring = self.root / "authoring"
        self.exchange = self.root / "exchange"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_release_requires_matching_publication_receipt(self) -> None:
        release = self.exchange / "releases/2026-10-07.001/catalog.json"
        write_json(
            release,
            {
                "release_id": "2026-10-07.001",
                "templates": [
                    {
                        "design_id": "test-design",
                        "product_profile_id": "test-profile",
                        "template_id": "test-design--test-profile",
                        "bundle_revision": 1,
                        "manifest_path": "bundles/test-design--test-profile/v000001/bundle.json",
                    }
                ],
            },
        )
        releases = discover_releases(self.exchange, self.authoring)
        self.assertFalse(releases[("test-design", "test-profile")][0]["published"])
        write_json(
            self.authoring
            / "test-design/test-profile/graduations/v1/publications/receipt.json",
            {
                "release_id": "2026-10-07.001",
                "template_id": "test-design--test-profile",
                "bundle_revision": 1,
            },
        )
        releases = discover_releases(self.exchange, self.authoring)
        self.assertTrue(releases[("test-design", "test-profile")][0]["published"])

    def test_discovers_prompt_reviews_and_layout_proposals(self) -> None:
        product = self.authoring / "test-design/test-profile"
        write_json(product / "product.json", {"design_id": "test-design"})
        experiment = product / "experiments/art/art-gpt-v01"
        (experiment / "inputs").mkdir(parents=True)
        (experiment / "inputs/art-template-gpt.md").write_text("prompt", encoding="utf-8")
        write_json(
            experiment / "experiment.json",
            {
                "created_at": "2026-10-07T00:00:00Z",
                "status": "draft",
                "generation": {"model": "gpt-image-2.5-sunburst"},
                "inputs": {"prompt": {"path": "inputs/art-template-gpt.md"}},
            },
        )
        review = product / "reviews/art/art-review"
        (review / "artifacts").mkdir(parents=True)
        Image.new("RGB", (8, 8), "red").save(review / "artifacts/art-comparison.png")
        write_json(
            review / "evaluation.json",
            {
                "kind": "art",
                "review_id": "art-review",
                "review_mode": "source-output",
                "measurements": {"attempts": 1, "median_seconds": 12.5},
                "warnings": ["manual review required"],
                "candidates": [],
            },
        )
        broken_review = product / "reviews/art/broken-review"
        broken_review.mkdir(parents=True)
        (broken_review / "evaluation.json").write_text("{", encoding="utf-8")
        proposal = product / "experiments/layout/layout-v01/proposals/proposal-v01"
        proposal.mkdir(parents=True)
        Image.new("RGB", (8, 8), "blue").save(proposal / "ranked-layout-proposals.png")
        candidate = proposal / "candidates/rank-01"
        candidate.mkdir(parents=True)
        Image.new("RGB", (8, 8), "green").save(candidate / "preview.png")
        write_json(candidate / "layout.json", {"schema_version": 2})
        write_json(
            proposal / "proposal.json",
            {
                "proposal_id": "proposal-v01",
                "duration_seconds": 3.25,
                "name_mode": "layout-text",
                "warnings": ["advisory geometry"],
                "candidates": [
                    {
                        "rank": 1,
                        "candidate_id": "rank-01",
                        "layout": "candidates/rank-01/layout.json",
                        "preview": "candidates/rank-01/preview.png",
                    }
                ],
            },
        )
        config = OperatorWorkflowConfig(
            project_root=self.root,
            authoring_root=self.authoring,
            exchange_root=self.exchange,
            jobs_root=self.root / "jobs",
        )
        workflows, assets = discover_operator_workflows(config, {"test-design"})
        discovered = workflows["test-design"][0]
        self.assertEqual(discovered["experiments"]["art"][0]["prompt_text"], "prompt")
        self.assertEqual(discovered["reviews"]["art"][0]["review_id"], "art-review")
        self.assertEqual(
            discovered["reviews"]["art"][0]["measurements"]["median_seconds"],
            12.5,
        )
        self.assertEqual(
            discovered["reviews"]["art"][0]["warnings"],
            ["manual review required"],
        )
        broken = next(
            item
            for item in discovered["reviews"]["art"]
            if item["review_id"] == "broken-review"
        )
        self.assertEqual(broken["hard_gates"]["status"], "failed")
        self.assertIn(str((broken_review / "evaluation.json").resolve()), broken["warnings"][0])
        self.assertEqual(discovered["layout_proposals"][0]["candidates"][0]["candidate_id"], "rank-01")
        self.assertEqual(discovered["layout_proposals"][0]["duration_seconds"], 3.25)
        self.assertEqual(
            discovered["layout_proposals"][0]["warnings"],
            ["advisory geometry"],
        )
        self.assertTrue(all(path.is_file() for path in assets.values()))

    def test_published_design_is_removed_from_graduated_operator_list(self) -> None:
        base = {
            "lifecycle_state": "graduated",
            "vote_totals": {"graduate": 0, "consider": 0, "pass": 0},
            "net_score": 0,
            "vote_count": 0,
        }

        class Store:
            @staticmethod
            def operator_designs(_):
                return [
                    {**base, "design_id": "pending"},
                    {**base, "design_id": "published"},
                ]

        server = SimpleNamespace(
            store=Store(),
            designs={},
            operator_workflows={
                "pending": [{"released": False}],
                "published": [{"released": True}],
            },
            operator_jobs=None,
            missing_design_ids=(),
        )
        payload = _operator_payload(server)
        self.assertEqual([item["design_id"] for item in payload["graduated"]], ["pending"])
        self.assertEqual([item["design_id"] for item in payload["released"]], ["published"])
        self.assertEqual(payload["workflow_contract"]["version"], 1)
        self.assertEqual(payload["workflow_contract"]["actions"], [])
        self.assertFalse(
            payload["workflow_contract"]["publication"]["configured"]
        )

    def test_operator_rejects_smoke_only_pet_decision(self) -> None:
        product = self.authoring / "test-design/test-profile"
        write_json(product / "product.json", {"design_id": "test-design"})
        write_json(
            product / "reviews/pet/pet-smoke/evaluation.json",
            {"kind": "pet", "fixture_tier": "smoke"},
        )
        runner = OperatorWorkflowRunner(
            OperatorWorkflowConfig(
                project_root=self.root,
                authoring_root=self.authoring,
                exchange_root=self.exchange,
                jobs_root=self.root / "jobs",
            )
        )
        with self.assertRaisesRegex(
            OperatorWorkflowError,
            "require release-tier fixture evidence",
        ):
            runner.execute(
                {
                    "action": "record-decision",
                    "design_id": "test-design",
                    "product_profile_id": "test-profile",
                    "kind": "pet",
                    "review_id": "pet-smoke",
                    "selected_experiment": "pet-gpt-v01",
                },
                operator_id="owner",
                progress=lambda _: None,
            )

    def test_reopen_pet_preserves_history_and_retires_downstream_chain(self) -> None:
        product = self.authoring / "test-design/test-profile"
        write_json(product / "product.json", {"design_id": "test-design"})
        for kind in ("art", "pet", "layout", "assembly"):
            review = product / "reviews" / kind / f"{kind}-review"
            write_json(
                review / "evaluation.json",
                {
                    "kind": kind,
                    "review_id": f"{kind}-review",
                    "design_id": "test-design",
                    "product_profile_id": "test-profile",
                    "hard_gates": {"status": "passed"},
                    "candidates": [],
                },
            )
            write_json(
                review / "decision.json",
                {
                    "kind": kind,
                    "selected": {"experiment_id": f"{kind}-v01"},
                    "decision": {
                        "selected_by": "owner",
                        "selected_at": "2026-10-08T00:00:00Z",
                    },
                },
            )
        composition = product / "reviews/pet/composition-v01"
        write_json(
            composition / "evaluation.json",
            {
                "kind": "pet",
                "review_id": "composition-v01",
                "hard_gates": {"status": "passed"},
                "candidates": [],
            },
        )
        (composition / "artifacts").mkdir(parents=True)
        Image.new("RGB", (8, 8), "purple").save(
            composition / "artifacts/pet-composition-comparison.png"
        )
        layout = product / "experiments/layout/layout-v01"
        write_json(layout / "experiment.json", {"kind": "layout"})
        proposal = layout / "proposals/proposal-v01"
        candidate = proposal / "candidates/rank-01"
        candidate.mkdir(parents=True)
        Image.new("RGB", (8, 8), "green").save(candidate / "preview.png")
        write_json(candidate / "layout.json", {"schema_version": 2})
        write_json(
            proposal / "proposal.json",
            {
                "candidates": [
                    {
                        "rank": 1,
                        "candidate_id": "rank-01",
                        "layout": "candidates/rank-01/layout.json",
                        "preview": "candidates/rank-01/preview.png",
                    }
                ]
            },
        )
        print_candidate = product / "print-candidates/print-ui-0001"
        write_json(
            print_candidate / "print-candidate.json",
            {
                "print_candidate_id": "print-ui-0001",
                "created_at": "2026-10-08T00:00:00Z",
                "status": "succeeded",
            },
        )
        (print_candidate / "outputs").mkdir(exist_ok=True)
        Image.new("RGB", (8, 8), "orange").save(
            print_candidate / "outputs/final-print.png"
        )
        Image.new("RGB", (8, 8), "white").save(
            print_candidate / "outputs/final-print-debug.png"
        )
        release = self.exchange / "releases/2026-10-08.001/catalog.json"
        write_json(
            release,
            {
                "release_id": "2026-10-08.001",
                "templates": [
                    {
                        "design_id": "test-design",
                        "product_profile_id": "test-profile",
                        "template_id": "test-design--test-profile",
                        "bundle_revision": 1,
                        "manifest_path": (
                            "bundles/test-design--test-profile/v000001/bundle.json"
                        ),
                    }
                ],
            },
        )
        runner = OperatorWorkflowRunner(
            OperatorWorkflowConfig(
                project_root=self.root,
                authoring_root=self.authoring,
                exchange_root=self.exchange,
                jobs_root=self.root / "jobs",
            )
        )
        messages: list[str] = []

        result = runner.execute(
            {
                "action": "reopen-stage",
                "design_id": "test-design",
                "product_profile_id": "test-profile",
                "stage": "pet",
                "reason": "Pet cutout needs cleaner paws",
            },
            operator_id="owner",
            progress=messages.append,
        )

        self.assertFalse(
            (product / "reviews/art/art-review/decision-retired.json").exists()
        )
        for path in (
            product / "reviews/pet/pet-review/decision-retired.json",
            product / "reviews/layout/layout-review/decision-retired.json",
            product / "reviews/assembly/assembly-review/decision-retired.json",
            product / "reviews/pet/composition-v01/review-retired.json",
        ):
            self.assertTrue(path.is_file(), path)
        self.assertTrue(Path(result["event"]).is_file())
        self.assertIn("layout-v01", result["invalidated_layout_experiments"])
        self.assertIn("print-ui-0001", result["invalidated_print_candidates"])
        self.assertIn("Reopened pet", messages[-1])

        releases = discover_releases(self.exchange, self.authoring)
        self.assertFalse(releases[("test-design", "test-profile")][0]["active"])
        workflows, _ = discover_operator_workflows(
            runner.config,
            {"test-design"},
        )
        discovered = workflows["test-design"][0]
        self.assertFalse(discovered["reviews"]["pet"][0]["active"])
        self.assertFalse(discovered["layout_proposals"][0]["active"])
        self.assertFalse(discovered["print_candidates"][0]["active"])
        self.assertIsNotNone(discovered["print_candidates"][0]["final_print_url"])
        self.assertFalse(discovered["released"])

    def test_reopen_requires_an_active_decision_before_writing_markers(self) -> None:
        product = self.authoring / "test-design/test-profile"
        write_json(product / "product.json", {"design_id": "test-design"})
        runner = OperatorWorkflowRunner(
            OperatorWorkflowConfig(
                project_root=self.root,
                authoring_root=self.authoring,
                exchange_root=self.exchange,
                jobs_root=self.root / "jobs",
            )
        )
        with self.assertRaisesRegex(OperatorWorkflowError, "no active layout decision"):
            runner.execute(
                {
                    "action": "reopen-stage",
                    "design_id": "test-design",
                    "product_profile_id": "test-profile",
                    "stage": "layout",
                    "reason": "Try another proposal",
                },
                operator_id="owner",
                progress=lambda _: None,
            )
        self.assertFalse((product / "operator-iterations").exists())

    def test_reopen_layout_keeps_upstream_decisions_and_ranked_proposals(self) -> None:
        product = self.authoring / "test-design/test-profile"
        write_json(product / "product.json", {"design_id": "test-design"})
        for kind in ("art", "pet", "layout"):
            review = product / "reviews" / kind / f"{kind}-review"
            write_json(review / "evaluation.json", {"kind": kind})
            write_json(review / "decision.json", {"kind": kind})
        proposal = product / "experiments/layout/layout-v01/proposals/proposal-v01"
        candidate = proposal / "candidates/rank-01"
        candidate.mkdir(parents=True)
        Image.new("RGB", (8, 8), "green").save(candidate / "preview.png")
        write_json(candidate / "layout.json", {"schema_version": 2})
        write_json(
            proposal / "proposal.json",
            {
                "candidates": [
                    {
                        "rank": 1,
                        "candidate_id": "rank-01",
                        "layout": "candidates/rank-01/layout.json",
                        "preview": "candidates/rank-01/preview.png",
                    }
                ]
            },
        )
        runner = OperatorWorkflowRunner(
            OperatorWorkflowConfig(
                project_root=self.root,
                authoring_root=self.authoring,
                exchange_root=self.exchange,
                jobs_root=self.root / "jobs",
            )
        )

        runner.execute(
            {
                "action": "reopen-stage",
                "design_id": "test-design",
                "product_profile_id": "test-profile",
                "stage": "layout",
                "reason": "Try the second-ranked layout",
            },
            operator_id="owner",
            progress=lambda _: None,
        )

        self.assertFalse(
            (product / "reviews/art/art-review/decision-retired.json").exists()
        )
        self.assertFalse(
            (product / "reviews/pet/pet-review/decision-retired.json").exists()
        )
        self.assertTrue(
            (product / "reviews/layout/layout-review/decision-retired.json").is_file()
        )
        workflows, _ = discover_operator_workflows(runner.config, {"test-design"})
        self.assertTrue(workflows["test-design"][0]["layout_proposals"][0]["active"])


class _BlockingRunner:
    def __init__(self) -> None:
        self.release = False

    def execute(self, payload, *, operator_id, progress):
        progress("running")
        while not self.release:
            time.sleep(0.005)
        return {"ok": True}


class OperatorJobManagerTests(unittest.TestCase):
    def test_unsupported_action_is_rejected_before_job_is_queued(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runner = OperatorWorkflowRunner(
                OperatorWorkflowConfig(
                    project_root=Path(folder),
                    authoring_root=Path(folder) / "authoring",
                    exchange_root=Path(folder) / "exchange",
                    jobs_root=Path(folder) / "jobs",
                )
            )
            manager = OperatorJobManager(runner, Path(folder) / "jobs")
            with self.assertRaisesRegex(
                OperatorWorkflowError,
                "unsupported workflow action.*restart the gallery server",
            ):
                manager.submit(
                    {
                        "action": "future-ui-only-action",
                        "design_id": "test-design",
                        "product_profile_id": "test-profile",
                    },
                    operator_id="owner",
                )
            self.assertEqual(manager.list(), [])

    def test_generate_composition_is_in_runtime_contract(self) -> None:
        self.assertIn(
            "generate-composition",
            OperatorWorkflowRunner.supported_actions,
        )

    def test_prepare_print_is_in_runtime_contract(self) -> None:
        self.assertIn("prepare-print", OperatorWorkflowRunner.supported_actions)

    def test_publish_s3_records_success_then_moves_design_to_release_pool(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            authoring = root / "authoring"
            exchange = root / "exchange"
            graduation = root / "Graduation Pool"
            released = root / "Release Pool"
            product = authoring / "test-design/test-profile"
            write_json(product / "product.json", {"design_id": "test-design"})
            source = graduation / "test-design"
            source.mkdir(parents=True)
            (source / "reference-design.png").write_bytes(b"reference")
            catalog = exchange / "releases/2026-10-08.001/catalog.json"
            write_json(catalog, {"release_id": "2026-10-08.001"})
            local = {
                ("test-design", "test-profile"): [
                    {
                        "release_id": "2026-10-08.001",
                        "bundle_revision": 1,
                        "catalog": str(catalog),
                        "published": False,
                    }
                ]
            }
            published = {
                ("test-design", "test-profile"): [
                    {**local[("test-design", "test-profile")][0], "published": True}
                ]
            }
            runner = OperatorWorkflowRunner(
                OperatorWorkflowConfig(
                    project_root=root,
                    authoring_root=authoring,
                    exchange_root=exchange,
                    jobs_root=root / "jobs",
                    graduation_root=graduation,
                    release_root=released,
                    s3_bucket="test-bucket",
                    s3_prefix="templates/mvp",
                    aws_profile="test-profile",
                    aws_region="us-east-1",
                )
            )
            messages: list[str] = []
            with (
                patch(
                    "pawmarvel_generator.operator_workflow.discover_releases",
                    side_effect=[local, published],
                ),
                patch(
                    "pawmarvel_generator.operator_workflow.publish_s3",
                    return_value=(
                        "s3://test-bucket/templates/mvp/releases/"
                        "2026-10-08.001/catalog.json"
                    ),
                ) as publisher,
            ):
                result = runner.execute(
                    {
                        "action": "publish-s3",
                        "design_id": "test-design",
                        "product_profile_id": "test-profile",
                    },
                    operator_id="owner",
                    progress=messages.append,
                )
            self.assertFalse(source.exists())
            self.assertTrue((released / "test-design/reference-design.png").is_file())
            self.assertEqual(result["release_id"], "2026-10-08.001")
            self.assertEqual(
                result["release_pool"], str((released / "test-design").resolve())
            )
            self.assertEqual(publisher.call_args.kwargs["execute"], True)
            self.assertEqual(publisher.call_args.kwargs["bucket"], "test-bucket")
            self.assertIn("Moved published design", messages[-1])

            (released / "test-design").replace(source)
            recovery_messages: list[str] = []
            with (
                patch(
                    "pawmarvel_generator.operator_workflow.discover_releases",
                    return_value=published,
                ),
                patch(
                    "pawmarvel_generator.operator_workflow.publish_s3"
                ) as recovery_publisher,
            ):
                recovered = runner.execute(
                    {
                        "action": "publish-s3",
                        "design_id": "test-design",
                        "product_profile_id": "test-profile",
                    },
                    operator_id="owner",
                    progress=recovery_messages.append,
                )
            recovery_publisher.assert_not_called()
            self.assertEqual(
                recovered["release_pool"], str((released / "test-design").resolve())
            )
            self.assertIn("receipt already exists", recovery_messages[0])

    def test_restart_marks_interrupted_receipt_failed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / "interrupted.json"
            write_json(
                receipt,
                {
                    "job_id": "interrupted",
                    "status": "running",
                    "action": "rerun-experiment",
                    "design_id": "test-design",
                    "product_profile_id": "test-profile",
                    "operator_id": "owner",
                    "message": "Submitting image request",
                    "created_at": "2026-10-08T00:00:00Z",
                    "updated_at": "2026-10-08T00:00:01Z",
                },
            )
            manager = OperatorJobManager(_BlockingRunner(), Path(folder))
            recovered = manager.list()[0]
            self.assertEqual(recovered["status"], "failed")
            self.assertEqual(recovered["error"]["type"], "OperatorProcessRestarted")
            self.assertIn("gallery process stopped", recovered["message"])
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8"))["status"],
                "failed",
            )

    def test_only_one_job_per_design_product_runs_at_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runner = _BlockingRunner()
            manager = OperatorJobManager(runner, Path(folder))
            payload = {
                "action": "rerun-experiment",
                "design_id": "test-design",
                "product_profile_id": "test-profile",
            }
            first = manager.submit(payload, operator_id="owner")
            with self.assertRaises(OperatorWorkflowError):
                manager.submit(payload, operator_id="owner")
            runner.release = True
            for _ in range(200):
                if manager.list()[0]["status"] == "succeeded":
                    break
                time.sleep(0.005)
            self.assertEqual(manager.list()[0]["status"], "succeeded")
            self.assertTrue((Path(folder) / f"{first['job_id']}.json").is_file())

    def test_batch_submission_preflights_duplicate_targets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            manager = OperatorJobManager(_BlockingRunner(), Path(folder))
            action = {
                "action": "record-decision",
                "design_id": "test-design",
                "product_profile_id": "test-profile",
            }
            with self.assertRaisesRegex(
                OperatorWorkflowError,
                "duplicate design/product target",
            ):
                manager.submit_many([action, dict(action)], operator_id="owner")
            self.assertEqual(manager.list(), [])

    def test_batch_submission_queues_each_distinct_target(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runner = _BlockingRunner()
            manager = OperatorJobManager(runner, Path(folder))
            actions = [
                {
                    "action": "record-decision",
                    "design_id": f"test-design-{index}",
                    "product_profile_id": "test-profile",
                }
                for index in (1, 2)
            ]
            jobs = manager.submit_many(actions, operator_id="owner")
            self.assertEqual(len(jobs), 2)
            self.assertEqual(len(manager.list()), 2)
            runner.release = True
            for _ in range(200):
                if all(job["status"] == "succeeded" for job in manager.list()):
                    break
                time.sleep(0.005)
            self.assertTrue(
                all(job["status"] == "succeeded" for job in manager.list())
            )


if __name__ == "__main__":
    unittest.main()
