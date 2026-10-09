from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pawmarvel_generator.gallery_cli import build_parser
from pawmarvel_generator.gallery_server import (
    GalleryConfig,
    GalleryError,
    GalleryStore,
    create_gallery_server,
)


class GalleryServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp.name)
        self.root = self.temp_root / "Test Design Pool"
        self.root.mkdir()
        self.authoring_root = self.root / "authoring"
        concepts = []
        for design_id, collection in (
            ("christmas-one", "Christmas"),
            ("christmas-two", "Christmas"),
            ("other-one", "Other"),
        ):
            folder = self.root / design_id
            folder.mkdir()
            Image.new("RGB", (8, 10), "white").save(folder / "reference-design.png")
            concepts.append(
                {
                    "design_id": design_id,
                    "folder": design_id,
                    "collection": collection,
                    "concept": "Watch",
                    "headline": "PATROL",
                    "bottom_line": "Under Surveillance",
                    "bottom_line_option": 1,
                    "priority": 1,
                    "row": 2,
                }
            )
        self.index = self.root / "concept-index.json"
        self.index.write_text(json.dumps({"concepts": concepts}), encoding="utf-8")
        product = self.authoring_root / "christmas-one" / "test-profile"
        art = product / "reviews/art/art-baseline"
        pet = product / "reviews/pet/pet-gpt-release"
        (art / "artifacts").mkdir(parents=True)
        (pet / "artifacts").mkdir(parents=True)
        Image.new("RGB", (24, 12), "red").save(art / "artifacts/art-comparison.png")
        Image.new("RGB", (24, 12), "blue").save(pet / "artifacts/pet-comparison.png")
        (art / "evaluation.json").write_text(
            json.dumps(
                {
                    "kind": "art",
                    "review_id": "art-baseline",
                    "created_at": "2026-10-05T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        (pet / "evaluation.json").write_text(
            json.dumps(
                {
                    "kind": "pet",
                    "review_id": "pet-gpt-release",
                    "fixture_tier": "release",
                    "created_at": "2026-10-05T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        incomplete_art = (
            self.authoring_root
            / "christmas-two/test-profile/reviews/art/art-baseline"
        )
        (incomplete_art / "artifacts").mkdir(parents=True)
        Image.new("RGB", (24, 12), "red").save(
            incomplete_art / "artifacts/art-comparison.png"
        )
        (incomplete_art / "evaluation.json").write_text(
            json.dumps(
                {
                    "kind": "art",
                    "review_id": "art-baseline",
                    "created_at": "2026-10-05T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        self.database = self.root / "votes.sqlite3"
        self.server = create_gallery_server(
            GalleryConfig(
                root=self.root,
                index=self.index,
                database=self.database,
                authoring_root=self.authoring_root,
                collections=("Christmas",),
            )
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def get_json(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path) as response:
            return json.loads(response.read())

    def post_vote(
        self, choice: str, comment: str = "", design_id: str = "christmas-one"
    ) -> dict:
        request = urllib.request.Request(
            self.base + "/api/vote",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "reviewer_id": "Reviewer@Example.com",
                    "reviewer_name": "Reviewer",
                    "design_id": design_id,
                    "choice": choice,
                    "comment": comment,
                }
            ).encode("utf-8"),
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def post_votes(self, votes: list[dict[str, str]]) -> dict:
        request = urllib.request.Request(
            self.base + "/api/votes",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "reviewer_id": "Reviewer@Example.com",
                    "reviewer_name": "Reviewer",
                    "votes": votes,
                }
            ).encode("utf-8"),
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def post_operator_action(self, action: str, design_id: str) -> dict:
        request = urllib.request.Request(
            self.base + "/api/operator/action",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "action": action,
                    "design_id": design_id,
                    "operator_id": "application-owner",
                    "reason": f"Test {action}",
                }
            ).encode("utf-8"),
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def post_operator_actions(self, action: str, design_ids: list[str]) -> dict:
        request = urllib.request.Request(
            self.base + "/api/operator/actions",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "action": action,
                    "design_ids": design_ids,
                    "operator_id": "application-owner",
                    "reason": f"Test batch {action}",
                }
            ).encode("utf-8"),
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def test_gallery_filters_collection_and_serves_reference_image(self) -> None:
        payload = self.get_json("/api/gallery")
        self.assertEqual(
            [item["design_id"] for item in payload["concepts"]],
            ["christmas-one", "christmas-two"],
        )
        with urllib.request.urlopen(
            self.base + "/assets/christmas-one/reference-design.png"
        ) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertGreater(len(response.read()), 10)
        self.assertEqual(
            {row["design_id"] for row in self.server.store.registry()},
            {"christmas-one", "christmas-two", "other-one"},
        )

    def test_absent_folder_is_retired_but_partial_folder_is_an_error(self) -> None:
        folder = self.root / "other-one"
        (folder / "reference-design.png").unlink()
        folder.rmdir()
        restarted = create_gallery_server(
            GalleryConfig(
                root=self.root,
                index=self.index,
                database=self.root / "restart.sqlite3",
                authoring_root=self.authoring_root,
            )
        )
        try:
            self.assertNotIn(
                "other-one", {item["design_id"] for item in restarted.concepts}
            )
            self.assertEqual(restarted.missing_design_ids, ("other-one",))
        finally:
            restarted.server_close()

        folder.mkdir()
        with self.assertRaisesRegex(GalleryError, "folder exists.*image is missing"):
            create_gallery_server(
                GalleryConfig(
                    root=self.root,
                    index=self.index,
                    database=self.root / "broken.sqlite3",
                    authoring_root=self.authoring_root,
                )
            )

    def test_release_pool_and_s3_settings_are_preserved_by_server_factory(self) -> None:
        release_root = self.temp_root / "Release Pool"
        release_root.mkdir(exist_ok=True)
        (self.root / "other-one").replace(release_root / "other-one")
        restarted = create_gallery_server(
            GalleryConfig(
                root=self.root,
                index=self.index,
                database=self.temp_root / "release.sqlite3",
                authoring_root=self.authoring_root,
                release_root=release_root,
                s3_bucket="test-bucket",
                s3_prefix="templates/mvp",
                aws_profile="test-profile",
                aws_region="us-east-1",
            )
        )
        try:
            self.assertEqual(
                restarted.designs["other-one"]["lifecycle_state"], "released"
            )
            self.assertEqual(restarted.gallery_config.s3_bucket, "test-bucket")
            self.assertEqual(restarted.gallery_config.s3_prefix, "templates/mvp")
            self.assertEqual(restarted.gallery_config.aws_profile, "test-profile")
            self.assertEqual(restarted.gallery_config.aws_region, "us-east-1")
        finally:
            restarted.server_close()

    def test_reviewer_page_uses_one_batch_save_action(self) -> None:
        with urllib.request.urlopen(self.base + "/") as response:
            html = response.read().decode("utf-8")
        with urllib.request.urlopen(self.base + "/static/gallery.js") as response:
            javascript = response.read().decode("utf-8")

        self.assertIn('id="save-all"', html)
        self.assertNotIn('class="save-vote"', html)
        self.assertIn('id="collection-filter"', html)
        self.assertIn('id="title-filter"', html)
        self.assertIn('id="bottom-line-filter"', html)
        self.assertIn('id="vote-filter"', html)
        self.assertIn('id="selection-filter"', html)
        self.assertIn('request("/api/votes"', javascript)
        self.assertIn("function applyFilters()", javascript)
        self.assertIn("current?.choice===vote", javascript)
        self.assertIn("beforeunload", javascript)

    def test_operator_page_exposes_batch_lifecycle_actions(self) -> None:
        with urllib.request.urlopen(self.base + "/operator") as response:
            html = response.read().decode("utf-8")
        with urllib.request.urlopen(self.base + "/static/operator.js") as response:
            javascript = response.read().decode("utf-8")

        self.assertIn('id="select-all"', html)
        self.assertIn('class="batch-select"', html)
        self.assertIn('id="collection-filter"', html)
        self.assertIn('id="title-filter"', html)
        self.assertIn('id="bottom-line-filter"', html)
        self.assertIn('id="vote-filter"', html)
        self.assertIn('id="selection-filter"', html)
        self.assertIn('id="workflow-step-filter"', html)
        self.assertIn('id="restore-selected"', html)
        self.assertIn('data-tab="released"', html)
        self.assertIn('request("/api/operator/actions"', javascript)
        self.assertIn('request("/api/operator/workflow"', javascript)
        self.assertIn('request("/api/operator/workflows"', javascript)
        self.assertIn('Run release on current experiment', javascript)
        self.assertIn('Approve composition and prepare print finalist', javascript)
        self.assertIn('Approve print and build local release', javascript)
        self.assertIn('action:"prepare-print"', javascript)
        self.assertIn('Publish verified release to S3', javascript)
        self.assertIn('action:"publish-s3"', javascript)
        self.assertIn('action:"reopen-stage"', javascript)
        self.assertIn('Redo / improve', javascript)
        self.assertIn('Open supersession record', javascript)
        self.assertIn('move the design to the Release Pool', javascript)
        self.assertIn('Complete release-pool move', javascript)
        self.assertIn('workflow_contract', javascript)
        self.assertIn('Gallery server restart required', javascript)
        self.assertIn('function failedJobOutcomeExists', javascript)
        self.assertIn('&&!failedJobOutcomeExists(job)', javascript)
        self.assertIn('Select graduated designs at the same step', html)
        self.assertIn('Operation guide → GUI coverage and offline handoffs', html)
        self.assertIn('Offline prerequisite', javascript)
        self.assertIn('§5 Develop generated or empty-canvas art.png', javascript)
        self.assertIn('view decision evidence', javascript)
        self.assertIn('function jobStatusBanner', javascript)
        self.assertIn('Background work', javascript)
        self.assertIn('workflow-busy', javascript)
        self.assertIn('function reviewEvidence', javascript)
        self.assertIn('Open raw evaluation JSON', javascript)
        self.assertIn('Warnings requiring attention', javascript)
        self.assertIn('Selected candidate has no evaluation warnings.', javascript)
        self.assertIn('View release evidence', javascript)
        self.assertIn('Pet release composition', javascript)
        self.assertIn('released-summary', javascript)
        self.assertIn("function filteredDesigns()", javascript)
        self.assertIn("for(const design of filteredDesigns())", javascript)
        self.assertIn('runBatchAction("restore")', javascript)

    def test_operator_batch_workflow_endpoint_queues_all_actions(self) -> None:
        captured: dict[str, object] = {}

        class Jobs:
            @staticmethod
            def submit_many(actions, *, operator_id):
                captured["actions"] = actions
                captured["operator_id"] = operator_id
                return [{"job_id": f"job-{index}"} for index, _ in enumerate(actions)]

        self.server.operator_jobs = Jobs()
        actions = [
            {
                "action": "record-decision",
                "design_id": "christmas-one",
                "product_profile_id": "test-profile",
            },
            {
                "action": "record-decision",
                "design_id": "christmas-two",
                "product_profile_id": "test-profile",
            },
        ]
        request = urllib.request.Request(
            self.base + "/api/operator/workflows",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {"operator_id": "application-owner", "actions": actions}
            ).encode("utf-8"),
        )
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read())
        self.assertEqual(len(payload["jobs"]), 2)
        self.assertEqual(captured["actions"], actions)
        self.assertEqual(captured["operator_id"], "application-owner")

    def test_batch_save_atomically_upserts_changed_votes(self) -> None:
        result = self.post_votes(
            [
                {
                    "design_id": "christmas-one",
                    "choice": "graduate",
                    "comment": "Strong concept",
                },
                {
                    "design_id": "christmas-two",
                    "choice": "consider",
                    "comment": "Improve the type",
                },
            ]
        )
        self.assertEqual(result["saved_count"], 2)
        own = self.get_json("/api/gallery?reviewer=reviewer%40example.com")
        self.assertEqual(own["current_votes"]["christmas-one"]["choice"], "graduate")
        self.assertEqual(own["current_votes"]["christmas-two"]["choice"], "consider")

    def test_invalid_batch_saves_nothing(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/votes",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "reviewer_id": "Reviewer@Example.com",
                    "reviewer_name": "Reviewer",
                    "votes": [
                        {
                            "design_id": "christmas-one",
                            "choice": "graduate",
                            "comment": "Would otherwise save",
                        },
                        {
                            "design_id": "missing",
                            "choice": "pass",
                            "comment": "Invalid design",
                        },
                    ],
                }
            ).encode("utf-8"),
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        try:
            self.assertEqual(caught.exception.code, 400)
            self.assertIn("vote 2 has unknown design_id", caught.exception.read().decode("utf-8"))
        finally:
            caught.exception.close()
        own = self.get_json("/api/gallery?reviewer=reviewer%40example.com")
        self.assertEqual(own["current_votes"], {})

    def test_generated_review_context_requires_both_complete_reviews(self) -> None:
        payload = self.get_json("/api/gallery")
        concepts = {item["design_id"]: item for item in payload["concepts"]}
        contexts = concepts["christmas-one"]["review_contexts"]
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["product_profile_id"], "test-profile")
        self.assertEqual(contexts[0]["art_review_id"], "art-baseline")
        self.assertEqual(contexts[0]["pet_review_id"], "pet-gpt-release")
        self.assertEqual(concepts["christmas-two"]["review_contexts"], [])
        saved = self.post_vote(
            "consider", "Vote remains available without context", "christmas-two"
        )
        self.assertEqual(saved["vote"]["design_id"], "christmas-two")
        for key in (
            "art_comparison_url",
            "pet_comparison_url",
            "art_evaluation_url",
            "pet_evaluation_url",
        ):
            with urllib.request.urlopen(self.base + contexts[0][key]) as response:
                self.assertEqual(response.status, 200)

    def test_generated_review_context_uses_first_complete_product_profile(self) -> None:
        product = self.authoring_root / "christmas-one" / "aaa-profile"
        art = product / "reviews/art/art-baseline"
        pet = product / "reviews/pet/pet-gpt-release"
        (art / "artifacts").mkdir(parents=True)
        (pet / "artifacts").mkdir(parents=True)
        Image.new("RGB", (24, 12), "red").save(
            art / "artifacts/art-comparison.png"
        )
        Image.new("RGB", (24, 12), "blue").save(
            pet / "artifacts/pet-comparison.png"
        )
        (art / "evaluation.json").write_text(
            json.dumps({"kind": "art", "review_id": "art-baseline"}),
            encoding="utf-8",
        )
        (pet / "evaluation.json").write_text(
            json.dumps(
                {
                    "kind": "pet",
                    "review_id": "pet-gpt-release",
                    "fixture_tier": "release",
                }
            ),
            encoding="utf-8",
        )
        restarted = create_gallery_server(
            GalleryConfig(
                root=self.root,
                index=self.index,
                database=self.root / "first-profile.sqlite3",
                authoring_root=self.authoring_root,
            )
        )
        try:
            concept = next(
                item
                for item in restarted.concepts
                if item["design_id"] == "christmas-one"
            )
            self.assertEqual(len(concept["review_contexts"]), 1)
            self.assertEqual(
                concept["review_contexts"][0]["product_profile_id"], "aaa-profile"
            )
        finally:
            restarted.server_close()

    def test_vote_is_upserted_per_normalized_reviewer_and_design(self) -> None:
        self.post_vote("consider", "Needs stronger copy")
        self.post_vote("graduate", "Ready")
        own = self.get_json("/api/gallery?reviewer=reviewer%40example.com")
        self.assertEqual(own["current_votes"]["christmas-one"]["choice"], "graduate")
        results = self.get_json("/api/results.json")
        self.assertEqual(results["vote_count"], 1)
        self.assertEqual(results["totals"]["christmas-one"]["graduate"], 1)
        self.assertEqual(results["votes"][0]["comment"], "Ready")

    def test_unknown_design_and_choice_return_specific_errors(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/vote",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "reviewer_id": "r@example.com",
                    "reviewer_name": "R",
                    "design_id": "missing",
                    "choice": "yes",
                }
            ).encode("utf-8"),
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        try:
            self.assertEqual(caught.exception.code, 400)
            self.assertIn("unknown design_id", caught.exception.read().decode("utf-8"))
        finally:
            caught.exception.close()

    def test_csv_export_contains_vote(self) -> None:
        self.post_vote("pass", "Not this one")
        with urllib.request.urlopen(self.base + "/api/results.csv") as response:
            body = response.read().decode("utf-8")
        self.assertIn("design_id,choice,comment", body)
        self.assertIn("christmas-one,pass,Not this one", body)

    def test_retained_results_are_opt_in_after_design_leaves_pool(self) -> None:
        self.post_vote("graduate", "Advance")
        index = json.loads(self.index.read_text(encoding="utf-8"))
        index["concepts"] = [
            item
            for item in index["concepts"]
            if item["design_id"] != "christmas-one"
        ]
        self.index.write_text(json.dumps(index), encoding="utf-8")
        restarted = create_gallery_server(
            GalleryConfig(
                root=self.root,
                index=self.index,
                database=self.database,
                authoring_root=self.authoring_root,
                collections=("Christmas",),
            )
        )
        thread = threading.Thread(target=restarted.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{restarted.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/results.json") as response:
                active = json.loads(response.read())
            with urllib.request.urlopen(
                base + "/api/results.json?include_retired=1"
            ) as response:
                retained = json.loads(response.read())
            self.assertEqual(active["vote_count"], 0)
            self.assertEqual(retained["vote_count"], 1)
            self.assertEqual(retained["retired_design_ids"], ["christmas-one"])
        finally:
            restarted.shutdown()
            restarted.server_close()
            thread.join(timeout=2)

    def test_operator_can_abandon_restore_and_reset_review_round(self) -> None:
        self.post_vote("consider", "Improve the typography")
        abandoned = self.post_operator_action("abandon", "christmas-one")
        self.assertEqual(abandoned["event"]["to_state"], "abandoned")
        self.assertFalse((self.root / "christmas-one").exists())
        self.assertTrue(
            (self.root.parent / "Abandoned Design Pool/christmas-one").is_dir()
        )
        active_gallery = self.get_json("/api/gallery")
        self.assertNotIn(
            "christmas-one",
            {item["design_id"] for item in active_gallery["concepts"]},
        )

        restored = self.post_operator_action("restore", "christmas-one")
        self.assertEqual(restored["event"]["to_state"], "active")
        self.assertTrue((self.root / "christmas-one").is_dir())
        own = self.get_json("/api/gallery?reviewer=reviewer%40example.com")
        self.assertIn("christmas-one", own["current_votes"])

        reset = self.post_operator_action("new-round", "christmas-one")
        design = next(
            item
            for item in reset["dashboard"]["active"]
            if item["design_id"] == "christmas-one"
        )
        self.assertEqual(design["review_round"], 2)
        self.assertEqual(design["vote_count"], 0)
        own = self.get_json("/api/gallery?reviewer=reviewer%40example.com")
        self.assertNotIn("christmas-one", own["current_votes"])
        with closing(self.server.store.connect()) as connection:
            archived = connection.execute(
                "SELECT COUNT(*) FROM archived_votes WHERE design_id='christmas-one'"
            ).fetchone()[0]
        self.assertEqual(archived, 1)

    def test_operator_can_graduate_multiple_designs_as_one_batch(self) -> None:
        result = self.post_operator_actions(
            "graduate", ["christmas-one", "christmas-two"]
        )

        self.assertEqual(result["processed_count"], 2)
        self.assertEqual(
            {event["design_id"] for event in result["events"]},
            {"christmas-one", "christmas-two"},
        )
        graduation_root = self.root.parent / "Graduation Pool"
        self.assertTrue((graduation_root / "christmas-one").is_dir())
        self.assertTrue((graduation_root / "christmas-two").is_dir())
        registry = {row["design_id"]: row for row in self.server.store.registry()}
        self.assertEqual(registry["christmas-one"]["lifecycle_state"], "graduated")
        self.assertEqual(registry["christmas-two"]["lifecycle_state"], "graduated")

    def test_operator_can_restore_multiple_inactive_designs_as_one_batch(self) -> None:
        self.post_operator_action("abandon", "christmas-one")
        self.post_operator_action("graduate", "christmas-two")

        result = self.post_operator_actions(
            "restore", ["christmas-one", "christmas-two"]
        )

        self.assertEqual(result["processed_count"], 2)
        self.assertEqual(
            {event["from_state"] for event in result["events"]},
            {"abandoned", "graduated"},
        )
        self.assertEqual(
            {event["to_state"] for event in result["events"]}, {"active"}
        )
        self.assertTrue((self.root / "christmas-one").is_dir())
        self.assertTrue((self.root / "christmas-two").is_dir())
        registry = {row["design_id"]: row for row in self.server.store.registry()}
        self.assertEqual(registry["christmas-one"]["lifecycle_state"], "active")
        self.assertEqual(registry["christmas-two"]["lifecycle_state"], "active")

    def test_batch_preflight_failure_moves_no_designs(self) -> None:
        blocked = self.root.parent / "Abandoned Design Pool/christmas-two"
        blocked.mkdir(parents=True)
        request = urllib.request.Request(
            self.base + "/api/operator/actions",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "action": "abandon",
                    "design_ids": ["christmas-one", "christmas-two"],
                    "operator_id": "application-owner",
                    "reason": "Test preflight",
                }
            ).encode("utf-8"),
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        try:
            self.assertEqual(caught.exception.code, 400)
            self.assertIn(
                "destination already exists", caught.exception.read().decode()
            )
        finally:
            caught.exception.close()
        self.assertTrue((self.root / "christmas-one").is_dir())
        self.assertTrue((self.root / "christmas-two").is_dir())
        registry = {row["design_id"]: row for row in self.server.store.registry()}
        self.assertEqual(registry["christmas-one"]["lifecycle_state"], "active")
        self.assertEqual(registry["christmas-two"]["lifecycle_state"], "active")

    def test_batch_persistence_failure_rolls_back_every_design(self) -> None:
        original_write = self.server.store._write_event
        calls = 0

        def fail_second_event(event: dict) -> Path:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk unavailable")
            return original_write(event)

        with patch.object(
            self.server.store, "_write_event", side_effect=fail_second_event
        ):
            request = urllib.request.Request(
                self.base + "/api/operator/actions",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps(
                    {
                        "action": "graduate",
                        "design_ids": ["christmas-one", "christmas-two"],
                        "operator_id": "application-owner",
                        "reason": "Test persistence rollback",
                    }
                ).encode("utf-8"),
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            try:
                self.assertEqual(caught.exception.code, 500)
                self.assertIn(
                    "operator action failed", caught.exception.read().decode()
                )
            finally:
                caught.exception.close()
        self.assertTrue((self.root / "christmas-one").is_dir())
        self.assertTrue((self.root / "christmas-two").is_dir())
        graduation_root = self.root.parent / "Graduation Pool"
        self.assertFalse((graduation_root / "christmas-one").exists())
        self.assertFalse((graduation_root / "christmas-two").exists())
        registry = {row["design_id"]: row for row in self.server.store.registry()}
        self.assertEqual(registry["christmas-one"]["lifecycle_state"], "active")
        self.assertEqual(registry["christmas-two"]["lifecycle_state"], "active")
        self.assertEqual(
            list((self.server.store.decisions_dir / "christmas-one").glob("*.json")),
            [],
        )

    def test_batch_restore_persistence_failure_rolls_back_every_design(self) -> None:
        self.post_operator_action("abandon", "christmas-one")
        self.post_operator_action("graduate", "christmas-two")
        original_write = self.server.store._write_event
        calls = 0

        def fail_second_event(event: dict) -> Path:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk unavailable")
            return original_write(event)

        with patch.object(
            self.server.store, "_write_event", side_effect=fail_second_event
        ):
            request = urllib.request.Request(
                self.base + "/api/operator/actions",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps(
                    {
                        "action": "restore",
                        "design_ids": ["christmas-one", "christmas-two"],
                        "operator_id": "application-owner",
                        "reason": "Test restore rollback",
                    }
                ).encode("utf-8"),
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            try:
                self.assertEqual(caught.exception.code, 500)
                self.assertIn(
                    "operator action failed", caught.exception.read().decode()
                )
            finally:
                caught.exception.close()

        abandoned_root = self.root.parent / "Abandoned Design Pool"
        graduation_root = self.root.parent / "Graduation Pool"
        self.assertTrue((abandoned_root / "christmas-one").is_dir())
        self.assertTrue((graduation_root / "christmas-two").is_dir())
        self.assertFalse((self.root / "christmas-one").exists())
        self.assertFalse((self.root / "christmas-two").exists())
        registry = {row["design_id"]: row for row in self.server.store.registry()}
        self.assertEqual(registry["christmas-one"]["lifecycle_state"], "abandoned")
        self.assertEqual(registry["christmas-two"]["lifecycle_state"], "graduated")

    def test_transition_rolls_back_when_event_file_cannot_be_written(self) -> None:
        source = self.root / "christmas-one"
        target = self.root.parent / "Abandoned Design Pool/christmas-one"
        with patch.object(
            self.server.store, "_write_event", side_effect=OSError("disk unavailable")
        ):
            request = urllib.request.Request(
                self.base + "/api/operator/action",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps(
                    {
                        "action": "abandon",
                        "design_id": "christmas-one",
                        "operator_id": "application-owner",
                        "reason": "Test persistence failure",
                    }
                ).encode("utf-8"),
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            try:
                self.assertEqual(caught.exception.code, 500)
                self.assertIn(
                    "operator action failed", caught.exception.read().decode("utf-8")
                )
            finally:
                caught.exception.close()
        self.assertTrue(source.is_dir())
        self.assertFalse(target.exists())
        registry = {
            row["design_id"]: row for row in self.server.store.registry()
        }
        self.assertEqual(registry["christmas-one"]["lifecycle_state"], "active")

    def test_inactive_design_rejects_late_vote(self) -> None:
        self.post_operator_action("graduate", "christmas-one")
        with self.assertRaisesRegex(GalleryError, "design is not active"):
            self.server.store.save_vote(
                reviewer_id="late@example.com",
                reviewer_name="Late Reviewer",
                design_id="christmas-one",
                choice="graduate",
                comment="Too late",
            )

    def test_access_code_protects_gallery_and_api(self) -> None:
        protected = create_gallery_server(
            GalleryConfig(
                root=self.root,
                index=self.index,
                database=self.root / "protected.sqlite3",
                authoring_root=self.authoring_root,
                reviewer_access_code="team-code",
                operator_access_code="owner-code",
            )
        )
        thread = threading.Thread(target=protected.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{protected.server_port}"
        try:
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
            with opener.open(base + "/") as response:
                self.assertEqual(response.geturl(), base + "/login")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base + "/api/gallery")
            try:
                self.assertEqual(caught.exception.code, 401)
            finally:
                caught.exception.close()
            token = hashlib.sha256(b"reviewer:team-code").hexdigest()
            request = urllib.request.Request(
                base + "/api/gallery",
                headers={"Cookie": f"pawmarvel_gallery_reviewer={token}"},
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 200)
            reviewer_results = urllib.request.Request(
                base + "/api/results.json",
                headers={"Cookie": f"pawmarvel_gallery_reviewer={token}"},
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(reviewer_results)
            try:
                self.assertEqual(caught.exception.code, 403)
            finally:
                caught.exception.close()
            operator_token = hashlib.sha256(b"operator:owner-code").hexdigest()
            operator_request = urllib.request.Request(
                base + "/api/operator/designs",
                headers={
                    "Cookie": f"pawmarvel_gallery_operator={operator_token}"
                },
            )
            with urllib.request.urlopen(operator_request) as response:
                self.assertEqual(response.status, 200)
        finally:
            protected.shutdown()
            protected.server_close()
            thread.join(timeout=2)

    def test_cli_accepts_repeatable_collection(self) -> None:
        args = build_parser().parse_args(
            [
                "--collection", "Christmas", "--collection", "Other",
                "--authoring-root", str(self.authoring_root), "--port", "0",
            ]
        )
        self.assertEqual(args.collection, ["Christmas", "Other"])
        self.assertEqual(args.port, 0)
        self.assertEqual(args.authoring_root, self.authoring_root)
        self.assertIn("work/gallery-reviews", str(args.database))


class GalleryLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = GalleryStore(
            self.root / "gallery-votes.sqlite3", self.root / "decisions"
        )
        self.designs = {
            "design-one": {"design_id": "design-one", "collection": "Christmas"},
            "design-two": {"design_id": "design-two", "collection": "Other"},
        }
        self.started = datetime(2026, 10, 5, tzinfo=timezone.utc)
        self.store.reconcile(self.designs, retention_days=30, now=self.started)
        self.store.save_vote(
            reviewer_id="reviewer@example.com",
            reviewer_name="Reviewer",
            design_id="design-one",
            choice="graduate",
            comment="Advance it",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_removed_design_is_retained_restored_then_purged(self) -> None:
        removed = self.store.reconcile(
            {"design-two": self.designs["design-two"]},
            retention_days=30,
            now=self.started + timedelta(days=1),
        )
        self.assertEqual(removed.removed, ("design-one",))
        self.assertEqual(len(self.store.votes_for("reviewer@example.com")), 1)

        restored = self.store.reconcile(
            self.designs,
            retention_days=30,
            now=self.started + timedelta(days=2),
        )
        self.assertEqual(restored.restored, ("design-one",))
        self.assertEqual(len(self.store.votes_for("reviewer@example.com")), 1)

        self.store.reconcile(
            {"design-two": self.designs["design-two"]},
            retention_days=30,
            now=self.started + timedelta(days=3),
        )
        purged = self.store.reconcile(
            {"design-two": self.designs["design-two"]},
            retention_days=30,
            now=self.started + timedelta(days=34),
        )
        self.assertEqual(purged.purged, ("design-one",))
        self.assertEqual(self.store.votes_for("reviewer@example.com"), {})
        registry = {
            row["design_id"]: row for row in self.store.registry()
        }
        self.assertIn("design-one", registry)
        self.assertIsNotNone(registry["design-one"]["raw_purged_at"])
        decision = self.root / "decisions/_reconciliation/design-one.json"
        retained = json.loads(decision.read_text(encoding="utf-8"))
        self.assertEqual(retained["outcome"], "unclassified")
        self.assertEqual(retained["final_vote_count"], 1)
        self.assertNotIn("reviewer", decision.read_text(encoding="utf-8").casefold())
        self.assertNotIn("decided_by", retained)
        self.assertNotIn("notes", retained)

    def test_removal_without_disposition_is_reported(self) -> None:
        result = self.store.reconcile(
            {"design-two": self.designs["design-two"]},
            retention_days=30,
            now=self.started + timedelta(days=1),
        )
        self.assertEqual(result.removed_without_disposition, ("design-one",))

if __name__ == "__main__":
    unittest.main()
