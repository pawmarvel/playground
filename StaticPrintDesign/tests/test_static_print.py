from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from PIL import Image, ImageChops

from pawmarvel_static_print.authoring import (
    discover_designs,
    generate_design,
    initialize_workspace,
)
from pawmarvel_static_print.bundles import (
    BundleSelection,
    prepare_release,
    validate_bundle,
    validate_release,
)
from pawmarvel_static_print.catalog import (
    build_catalog,
    set_product_status,
    validate_catalog,
)
from pawmarvel_static_print.cli import _load_environment, build_parser
from pawmarvel_static_print.core import (
    StaticPrintError,
    bundle_id_for,
    design_identity,
    discover_profiles,
    load_profile,
    read_json,
)
from pawmarvel_static_print.lifecycle import (
    Subject,
    generate_subject_variants,
    import_review_collection,
    load_subject_catalog,
    move_seed,
    move_seeds,
    transparency_report,
)
from pawmarvel_static_print.operations import (
    append_operation_event,
    finish_operation,
    mark_running_operations_interrupted,
    operation_snapshot,
    operation_snapshots,
    start_operation,
    update_operation_item,
)
from pawmarvel_static_print.publisher import (
    _load_latest_catalog_history,
    _publish_catalog_version,
    _store_local_catalog,
    load_s3_release_entries,
    publication_plan,
    publish_release,
)
from pawmarvel_static_print.recovery import reconcile_workspace
from pawmarvel_static_print.retirement import (
    RETIREMENT_SCHEMA,
    plan_obsolete_local_artifacts,
    plan_obsolete_s3_artifacts,
    retire_obsolete_artifacts,
)
from pawmarvel_static_print.reviews import (
    list_feedback,
    list_operator_decisions,
    save_feedback,
    save_feedbacks,
    save_operator_decision,
    save_operator_decisions,
)
from pawmarvel_static_print.server import (
    _generation_plans,
    _next_release_id,
    _product_coverage_records,
    _published_entries,
    _resolve_generation_versions,
)


class StaticPrintWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        initialize_workspace(self.root)
        self.inputs = self.root / "static-design-inputs"
        self.authoring = self.root / "static-authoring"
        self.exchange = self.root / "static-exchange"
        self.profile_path = self.root / "multi-profile.json"
        self.profile_path.write_text(
            json.dumps(
                {
                    "schema": "pawmarvel-static-product-profile-v1",
                    "product_profile_id": "test-product",
                    "preview": {"width": 64, "height": 48},
                    "print_targets": [
                        {
                            "target_id": "square",
                            "width": 80,
                            "height": 80,
                            "dpi": 300,
                            "color_space": "sRGB",
                            "background": "transparent",
                        },
                        {
                            "target_id": "wide",
                            "width": 120,
                            "height": 60,
                            "dpi": 300,
                            "color_space": "sRGB",
                            "background": "#FFFFFF",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        source_dir = self.inputs / "sample-design"
        source_dir.mkdir()
        Image.new("RGBA", (100, 50), (220, 20, 50, 255)).save(
            source_dir / "source-design.png"
        )

    def assert_schema(self, filename: str, value: dict[str, object]) -> None:
        project = Path(__file__).resolve().parents[1]
        schema = read_json(project / "schemas" / filename)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _subject_design(self) -> str:
        design_id = "sample-design--subject-cat"
        destination = self.inputs / "sample-design/cat/source-design.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.inputs / "sample-design/source-design.png", destination)
        return design_id

    def test_shared_environment_is_reloaded_without_evaluating_shell(self) -> None:
        environment = self.root / "static-print.env"
        environment.write_text(
            "# private settings\n"
            "export OPENAI_API_KEY='replacement-key'\n"
            "AWS_PROFILE=alphapaw-dev # comment\n"
            "EMPTY_VALUE=\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "stale-key"}, clear=False):
            loaded = _load_environment(environment)
            self.assertEqual(loaded, ("OPENAI_API_KEY", "AWS_PROFILE", "EMPTY_VALUE"))
            self.assertEqual(os.environ["OPENAI_API_KEY"], "replacement-key")
            self.assertEqual(os.environ["AWS_PROFILE"], "alphapaw-dev")
            self.assertEqual(os.environ["EMPTY_VALUE"], "")

    def test_cli_product_generation_defaults_to_fill_canvas(self) -> None:
        args = build_parser().parse_args(
            [
                "generate",
                "--design-id",
                "sample-design--subject-cat",
                "--version-id",
                "v000001",
                "--product-profile",
                str(self.profile_path),
            ]
        )
        self.assertEqual(args.fit, "cover")

    def test_generation_plans_keep_version_scoped_to_design_and_profile(self) -> None:
        plans = _generation_plans(
            [
                {
                    "design_id": "sample-design--subject-cat",
                    "product_profile_id": "coffee-mug",
                    "version_id": "v000003",
                },
                {
                    "design_id": "sample-design--subject-cat",
                    "product_profile_id": "blanket",
                    "version_id": "v000001",
                },
                {
                    "design_id": "other-design--subject-dog-corgi",
                    "product_profile_id": "coffee-mug",
                    "version_id": "v000001",
                },
            ],
            {"coffee-mug", "blanket"},
        )
        self.assertEqual(
            [plan["version_id"] for plan in plans],
            ["v000003", "v000001", "v000001"],
        )

    def test_generation_plans_reject_duplicate_tuple(self) -> None:
        plan = {
            "design_id": "sample-design--subject-cat",
            "product_profile_id": "coffee-mug",
            "version_id": "v000001",
        }
        with self.assertRaisesRegex(StaticPrintError, "duplicate generation plan"):
            _generation_plans([plan, plan], {"coffee-mug"})

    def test_product_coverage_uses_current_publication_receipts(self) -> None:
        design = {
            "versions": [
                {
                    "version_id": "v000004",
                    "profile_outputs": [{"product_profile_id": "mens-tee-house-style"}],
                },
                {
                    "version_id": "v000001",
                    "profile_outputs": [
                        {"product_profile_id": "coffee-mug-11oz"},
                        {"product_profile_id": "pillow"},
                    ],
                },
            ],
            "bundle_candidates": [
                {
                    "product_profile_id": "mens-tee-house-style",
                    "version_id": "v000004",
                },
                {
                    "product_profile_id": "coffee-mug-11oz",
                    "version_id": "v000001",
                },
            ],
            "publication": {
                "current_entries": [
                    {
                        "product_profile_id": "mens-tee-house-style",
                        "version_id": "v000004",
                        "release_id": "2026-10-15.002",
                    }
                ]
            },
        }
        records = {
            item["product_profile_id"]: item
            for item in _product_coverage_records(
                design,
                [
                    "mens-tee-house-style",
                    "coffee-mug-11oz",
                    "pillow",
                    "sticker",
                ],
            )
        }
        self.assertEqual(records["mens-tee-house-style"]["state"], "published")
        self.assertEqual(
            records["mens-tee-house-style"]["latest_published_version"],
            "v000004",
        )
        self.assertEqual(records["coffee-mug-11oz"]["state"], "ready")
        self.assertEqual(records["pillow"]["state"], "local-attention")
        self.assertEqual(records["sticker"]["state"], "missing")
        unknown = _product_coverage_records(
            design, ["mens-tee-house-style"], publication_known=False
        )
        self.assertEqual(unknown[0]["state"], "release-status-unavailable")

    def test_operation_progress_tracks_individual_batch_items(self) -> None:
        database = self.root / "operations.sqlite3"
        operation_id = "product-test-operation"
        start_operation(
            database,
            operation_id=operation_id,
            kind="product-generation",
            label="Product generation",
            request={"plans": [{"design_id": "subject-a"}]},
            items=[
                ("subject-a / mug / v000001", "subject-a / mug / v000001"),
                ("subject-b / mug / v000001", "subject-b / mug / v000001"),
            ],
        )
        update_operation_item(
            database,
            operation_id=operation_id,
            item_key="subject-a / mug / v000001",
            status="running",
        )
        update_operation_item(
            database,
            operation_id=operation_id,
            item_key="subject-a / mug / v000001",
            status="succeeded",
        )
        update_operation_item(
            database,
            operation_id=operation_id,
            item_key="subject-b / mug / v000001",
            status="skipped",
        )
        append_operation_event(
            database,
            operation_id=operation_id,
            item_key="subject-b / mug / v000001",
            severity="warning",
            message="print output requires visual inspection",
        )
        finish_operation(database, operation_id=operation_id)
        operation = operation_snapshot(database, operation_id)
        self.assertEqual(operation["status"], "succeeded")
        self.assertEqual(operation["completed_count"], 2)
        self.assertEqual(
            [item["status"] for item in operation["items"]],
            ["succeeded", "skipped"],
        )
        self.assertEqual(operation["status_counts"]["succeeded"], 1)
        self.assertEqual(operation["warning_count"], 1)
        self.assertEqual(
            operation["recent_output"][-1]["message"],
            "print output requires visual inspection",
        )

    def test_operation_feed_is_bounded_but_exact_history_remains_loadable(self) -> None:
        database = self.root / "bounded-operations.sqlite3"
        for number in range(3):
            operation_id = f"batch-{number}"
            start_operation(
                database,
                operation_id=operation_id,
                kind="product-generation",
                label=operation_id,
                request={"number": number},
                items=[("item", "item")],
            )
            finish_operation(database, operation_id=operation_id)
        recent = operation_snapshots(database, limit=2)
        self.assertEqual(
            {item["operation_id"] for item in recent}, {"batch-1", "batch-2"}
        )
        self.assertEqual(
            operation_snapshot(database, "batch-0")["operation_id"], "batch-0"
        )

    def test_interrupted_operation_can_resume_only_unfinished_items(self) -> None:
        database = self.root / "operations.sqlite3"
        items = [("one", "one"), ("two", "two")]
        request = {"plans": [{"design_id": "example"}]}
        start_operation(
            database,
            operation_id="resume-test",
            kind="product-generation",
            label="Product generation",
            request=request,
            items=items,
        )
        update_operation_item(
            database,
            operation_id="resume-test",
            item_key="one",
            status="succeeded",
        )
        update_operation_item(
            database,
            operation_id="resume-test",
            item_key="two",
            status="running",
        )
        self.assertEqual(mark_running_operations_interrupted(database), ["resume-test"])
        start_operation(
            database,
            operation_id="resume-test",
            kind="product-generation",
            label="Product generation",
            request=request,
            items=items,
            resume=True,
        )
        operation = operation_snapshot(database, "resume-test")
        self.assertEqual(operation["status"], "running")
        self.assertEqual(
            [item["status"] for item in operation["items"]],
            ["succeeded", "pending"],
        )

    def test_startup_recovery_removes_staging_restores_backup_and_quarantines(
        self,
    ) -> None:
        graduated = self.root / "static-graduated-designs"
        seed_root = graduated / "seed"
        partial = seed_root / ".cat.partial-product-test"
        partial.mkdir(parents=True)
        (partial / "incomplete").write_text("partial", encoding="utf-8")
        backup = seed_root / ".dog.backup-product-test"
        backup.mkdir(parents=True)
        Image.new("RGBA", (4, 4), (1, 2, 3, 255)).save(backup / "source-design.png")
        incomplete = self.authoring / "seed" / "cat" / "test-product" / "v000001"
        incomplete.mkdir(parents=True)
        (incomplete / "generation.json").write_text("{}", encoding="utf-8")
        profile = load_profile(self.profile_path)
        report = reconcile_workspace(
            work_root=self.root,
            authoring_root=self.authoring,
            graduated_root=graduated,
            profiles={profile.profile_id: profile},
        )
        self.assertFalse(partial.exists())
        self.assertTrue((seed_root / "dog" / "source-design.png").is_file())
        self.assertFalse(incomplete.exists())
        self.assertEqual(len(report["quarantined"]), 1)
        self.assertTrue(Path(report["quarantined"][0]["destination"]).is_dir())

    def test_s3_catalog_index_is_publication_source_of_truth(self) -> None:
        key = "templates/static_design/catalog.json"
        head = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "ETag": '"catalog-etag"',
                    "Metadata": {"sha256": "a" * 64},
                }
            ),
            stderr="",
        )
        catalog = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "schema": "pawmarvel-static-catalog-v1",
                    "updated_at": "2026-10-15T12:00:00Z",
                    "products": [
                        {
                            "bundle_id": "sample-design--subject-cat--mens-tee-house-style",
                            "design_id": "sample-design--subject-cat",
                            "product_profile_id": "mens-tee-house-style",
                            "status": "active",
                            "latest_version_id": "v000004",
                            "versions": [
                                {
                                    "version_id": "v000004",
                                    "release_id": "2026-10-15.002",
                                    "published_at": "2026-10-15T11:00:00Z",
                                    "manifest_path": "bundles/sample-design--subject-cat--mens-tee-house-style/v000004/bundle.json",
                                    "manifest_sha256": "b" * 64,
                                }
                            ],
                        }
                    ],
                }
            ),
            stderr="",
        )
        cache: dict[str, dict[str, object]] = {}
        with patch(
            "pawmarvel_static_print.publisher._run",
            side_effect=[head, catalog],
        ):
            published, inventory = load_s3_release_entries(
                bucket="example-bucket",
                prefix="templates/static_design",
                profile="example-profile",
                region="us-east-1",
                catalog_cache=cache,
            )
        self.assertEqual(
            published["sample-design--subject-cat"][0],
            {
                "release_id": "2026-10-15.002",
                "product_profile_id": "mens-tee-house-style",
                "version_id": "v000004",
                "lifecycle_status": "active",
                "latest_version_id": "v000004",
            },
        )
        self.assertEqual(inventory["catalog_count"], 1)
        self.assertEqual(inventory["bundle_count"], 1)
        self.assertIn(key, cache)

        with patch("pawmarvel_static_print.publisher._run", return_value=head) as run:
            cached, _ = load_s3_release_entries(
                bucket="example-bucket",
                prefix="templates/static_design",
                profile="example-profile",
                region="us-east-1",
                catalog_cache=cache,
            )
        self.assertEqual(cached, published)
        self.assertEqual(run.call_count, 1)

    def test_missing_s3_catalog_recovers_from_release_catalogs(self) -> None:
        release_id = "2026-10-15.002"
        release_key = f"templates/static_design/releases/{release_id}/catalog.json"
        missing_catalog = subprocess.CompletedProcess(
            args=[], returncode=255, stdout="", stderr="404 Not Found"
        )
        release_listing = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "IsTruncated": False,
                    "Contents": [{"Key": release_key, "ETag": '"release-etag"'}],
                }
            ),
            stderr="",
        )
        release_catalog = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "schema": "pawmarvel-static-release-catalog-v1",
                    "release_id": release_id,
                    "created_at": "2026-10-15T11:00:00Z",
                    "bundles": [
                        {
                            "bundle_id": (
                                "sample-design--subject-cat--mens-tee-house-style"
                            ),
                            "design_id": "sample-design--subject-cat",
                            "product_profile_id": "mens-tee-house-style",
                            "version_id": "v000004",
                            "manifest_path": (
                                "bundles/sample-design--subject-cat--"
                                "mens-tee-house-style/v000004/bundle.json"
                            ),
                            "manifest_sha256": "b" * 64,
                        }
                    ],
                }
            ),
            stderr="",
        )
        with patch(
            "pawmarvel_static_print.publisher._run",
            side_effect=[missing_catalog, release_listing, release_catalog],
        ):
            published, inventory = load_s3_release_entries(
                bucket="example-bucket",
                prefix="templates/static_design",
                profile="example-profile",
                region="us-east-1",
            )
        self.assertEqual(
            published["sample-design--subject-cat"][0]["version_id"],
            "v000004",
        )
        self.assertEqual(inventory["source"], "s3-release-catalogs-recovery")
        self.assertTrue(inventory["catalog_rebuild_required"])

    def test_s3_catalog_can_resolve_source_from_immutable_manifest(self) -> None:
        source_sha256 = "c" * 64
        manifest = {
            "schema": "pawmarvel-static-bundle-v1",
            "design_id": "sample-design--subject-cat",
            "product_profile_id": "mens-tee-house-style",
            "version_id": "v000004",
            "source_sha256": source_sha256,
        }
        manifest_bytes = json.dumps(manifest).encode("utf-8")
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        head = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "ETag": '"catalog-etag"',
                    "Metadata": {"sha256": "a" * 64},
                }
            ),
            stderr="",
        )
        catalog = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "schema": "pawmarvel-static-catalog-v1",
                    "updated_at": "2026-10-15T12:00:00Z",
                    "products": [
                        {
                            "bundle_id": (
                                "sample-design--subject-cat--mens-tee-house-style"
                            ),
                            "design_id": "sample-design--subject-cat",
                            "product_profile_id": "mens-tee-house-style",
                            "status": "active",
                            "latest_version_id": "v000004",
                            "versions": [
                                {
                                    "version_id": "v000004",
                                    "release_id": "2026-10-15.002",
                                    "published_at": "2026-10-15T11:00:00Z",
                                    "manifest_path": (
                                        "bundles/sample-design--subject-cat--"
                                        "mens-tee-house-style/v000004/bundle.json"
                                    ),
                                    "manifest_sha256": manifest_sha256,
                                }
                            ],
                        }
                    ],
                }
            ),
            stderr="",
        )
        manifest_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=manifest_bytes, stderr=b""
        )
        cache: dict[str, dict[str, object]] = {}
        with (
            patch(
                "pawmarvel_static_print.publisher._run",
                side_effect=[head, catalog],
            ),
            patch(
                "pawmarvel_static_print.publisher._run_bytes",
                return_value=manifest_result,
            ) as run_bytes,
        ):
            published, _ = load_s3_release_entries(
                bucket="example-bucket",
                prefix="templates/static_design",
                profile="example-profile",
                region="us-east-1",
                catalog_cache=cache,
                include_source_sha256=True,
            )
        self.assertEqual(
            published["sample-design--subject-cat"][0]["source_sha256"],
            source_sha256,
        )
        self.assertEqual(run_bytes.call_count, 1)

        with (
            patch("pawmarvel_static_print.publisher._run", return_value=head),
            patch("pawmarvel_static_print.publisher._run_bytes") as run_bytes,
        ):
            cached, _ = load_s3_release_entries(
                bucket="example-bucket",
                prefix="templates/static_design",
                profile="example-profile",
                region="us-east-1",
                catalog_cache=cache,
                include_source_sha256=True,
            )
        self.assertEqual(cached, published)
        run_bytes.assert_not_called()

    def test_discovery_catalog_resolves_latest_and_preserves_retirement(self) -> None:
        releases = [
            {
                "schema": "pawmarvel-static-release-catalog-v1",
                "release_id": "2026-10-14.001",
                "created_at": "2026-10-14T10:00:00Z",
                "bundles": [
                    {
                        "bundle_id": "sample-design--subject-cat--test-product",
                        "design_id": "sample-design--subject-cat",
                        "product_profile_id": "test-product",
                        "version_id": "v000001",
                        "manifest_path": "bundles/sample-design--subject-cat--test-product/v000001/bundle.json",
                        "manifest_sha256": "a" * 64,
                    }
                ],
            },
            {
                "schema": "pawmarvel-static-release-catalog-v1",
                "release_id": "2026-10-15.001",
                "created_at": "2026-10-15T10:00:00Z",
                "bundles": [
                    {
                        "bundle_id": "sample-design--subject-cat--test-product",
                        "design_id": "sample-design--subject-cat",
                        "product_profile_id": "test-product",
                        "version_id": "v000002",
                        "manifest_path": "bundles/sample-design--subject-cat--test-product/v000002/bundle.json",
                        "manifest_sha256": "b" * 64,
                    }
                ],
            },
        ]
        catalog = build_catalog(releases, updated_at="2026-10-15T11:00:00Z")
        product = catalog["products"][0]
        self.assertEqual(product["latest_version_id"], "v000002")
        retired = set_product_status(
            catalog,
            bundle_ids=[product["bundle_id"]],
            status="retired",
            reason="vendor target paused",
            changed_at="2026-10-15T12:00:00Z",
        )
        rebuilt = build_catalog(
            releases,
            previous=retired,
            updated_at="2026-10-15T13:00:00Z",
        )
        self.assertEqual(rebuilt["products"][0]["status"], "retired")
        self.assertEqual(
            rebuilt["products"][0]["status_reason"], "vendor target paused"
        )
        validate_catalog(rebuilt)

    def test_discovery_catalog_allows_unchanged_version_in_later_release(self) -> None:
        entry = {
            "bundle_id": "sample-design--subject-cat--test-product",
            "design_id": "sample-design--subject-cat",
            "product_profile_id": "test-product",
            "version_id": "v000001",
            "manifest_path": "bundles/sample-design--subject-cat--test-product/v000001/bundle.json",
            "manifest_sha256": "a" * 64,
        }
        catalog = build_catalog(
            [
                {
                    "schema": "pawmarvel-static-release-catalog-v1",
                    "release_id": "2026-10-14.001",
                    "created_at": "2026-10-14T10:00:00Z",
                    "bundles": [entry],
                },
                {
                    "schema": "pawmarvel-static-release-catalog-v1",
                    "release_id": "2026-10-15.001",
                    "created_at": "2026-10-15T10:00:00Z",
                    "bundles": [entry],
                },
            ],
            updated_at="2026-10-15T11:00:00Z",
        )
        version = catalog["products"][0]["versions"][0]
        self.assertEqual(version["release_id"], "2026-10-14.001")
        self.assertEqual(len(catalog["products"][0]["versions"]), 1)

    def test_catalog_history_records_only_a_committed_stable_catalog(self) -> None:
        catalog = build_catalog(
            [
                {
                    "schema": "pawmarvel-static-release-catalog-v1",
                    "release_id": "2026-10-15.001",
                    "created_at": "2026-10-15T10:00:00Z",
                    "bundles": [
                        {
                            "bundle_id": "sample-design--subject-cat--test-product",
                            "design_id": "sample-design--subject-cat",
                            "product_profile_id": "test-product",
                            "version_id": "v000001",
                            "manifest_path": "bundles/sample-design--subject-cat--test-product/v000001/bundle.json",
                            "manifest_sha256": "a" * 64,
                        }
                    ],
                }
            ],
            updated_at="2026-10-15T11:00:00Z",
        )
        path = self.root / "catalog.json"
        path.write_text(json.dumps(catalog), encoding="utf-8")
        calls: list[str] = []

        with (
            patch(
                "pawmarvel_static_print.publisher._upload_catalog",
                side_effect=lambda **_: calls.append("stable") or "updated",
            ),
            patch(
                "pawmarvel_static_print.publisher._upload",
                side_effect=lambda **_: calls.append("history") or "uploaded",
            ),
        ):
            result = _publish_catalog_version(
                path=path,
                bucket="example-bucket",
                prefix="templates/static_design",
                profile="example-profile",
                region="us-east-1",
                expected={"etag": '"old"', "sha256": "b" * 64},
            )
        self.assertEqual(calls, ["stable", "history"])
        self.assertEqual(result["stable_status"], "updated")

        calls.clear()
        with (
            patch(
                "pawmarvel_static_print.publisher._upload_catalog",
                side_effect=StaticPrintError("conditional conflict"),
            ),
            patch(
                "pawmarvel_static_print.publisher._upload",
                side_effect=lambda **_: calls.append("history") or "uploaded",
            ),
            self.assertRaisesRegex(StaticPrintError, "conditional conflict"),
        ):
            _publish_catalog_version(
                path=path,
                bucket="example-bucket",
                prefix="templates/static_design",
                profile="example-profile",
                region="us-east-1",
                expected={"etag": '"old"', "sha256": "b" * 64},
            )
        self.assertEqual(calls, [])

    def test_catalog_history_recovery_uses_latest_object_across_pages(self) -> None:
        older = build_catalog([], updated_at="2026-10-15T10:00:00Z")
        latest = build_catalog([], updated_at="2026-10-15T11:00:00Z")
        pages = [
            {
                "IsTruncated": True,
                "NextContinuationToken": "next-page",
                "Contents": [
                    {
                        "Key": "templates/static_design/catalog-history/older.json",
                        "ETag": '"older"',
                        "LastModified": "2026-10-15T10:00:00Z",
                    }
                ],
            },
            {
                "IsTruncated": False,
                "Contents": [
                    {
                        "Key": "templates/static_design/catalog-history/latest.json",
                        "ETag": '"latest"',
                        "LastModified": "2026-10-15T11:00:00Z",
                    }
                ],
            },
        ]
        with (
            patch("pawmarvel_static_print.publisher._aws_json", side_effect=pages),
            patch(
                "pawmarvel_static_print.publisher._download_s3_json",
                return_value=latest,
            ) as download,
        ):
            recovered = _load_latest_catalog_history(
                bucket="example-bucket",
                prefix="templates/static_design",
                profile="example-profile",
                region="us-east-1",
            )
        self.assertEqual(recovered, latest)
        self.assertEqual(
            download.call_args.kwargs["key"],
            "templates/static_design/catalog-history/latest.json",
        )
        self.assertNotEqual(older, latest)

    def test_local_catalog_mirror_is_validated_and_refreshed(self) -> None:
        source = self.root / "staged-catalog.json"
        catalog = build_catalog([], updated_at="2026-10-15T11:00:00Z")
        source.write_text(json.dumps(catalog), encoding="utf-8")
        path = Path(_store_local_catalog(self.exchange, source))
        self.assertEqual(path, (self.exchange / "catalog.json").resolve())
        self.assertEqual(read_json(path), catalog)

    def test_auto_version_advances_per_design_profile(self) -> None:
        local = self.authoring / "sample-design/coffee-mug/v000003"
        local.mkdir(parents=True)
        plans = _generation_plans(
            [
                {
                    "design_id": "sample-design",
                    "product_profile_id": "coffee-mug",
                },
                {
                    "design_id": "sample-design",
                    "product_profile_id": "blanket",
                },
            ],
            {"coffee-mug", "blanket"},
        )
        resolved = _resolve_generation_versions(
            plans,
            authoring_root=self.authoring,
            inputs_root=self.inputs,
            published={
                "sample-design": [
                    {
                        "release_id": "2026-10-14.001",
                        "product_profile_id": "coffee-mug",
                        "version_id": "v000005",
                    }
                ]
            },
        )
        self.assertEqual(
            [plan["version_id"] for plan in resolved],
            ["v000006", "v000001"],
        )

    def test_auto_release_id_advances_past_drafts_and_receipts(self) -> None:
        releases = self.exchange / "releases"
        publications = self.exchange / "publications"
        (releases / "2026-10-14.001").mkdir(parents=True)
        publications.mkdir(parents=True, exist_ok=True)
        (publications / "2026-10-14.003.json").write_text("{}", encoding="utf-8")
        with patch(
            "pawmarvel_static_print.server.utc_now",
            return_value="2026-10-14T12:00:00Z",
        ):
            self.assertEqual(_next_release_id(self.exchange), "2026-10-14.004")

    def test_product_versions_use_independent_profile_directories(self) -> None:
        second_profile = self.root / "second-profile.json"
        profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
        profile["product_profile_id"] = "second-product"
        second_profile.write_text(json.dumps(profile), encoding="utf-8")
        for profile_path in (self.profile_path, second_profile):
            generate_design(
                inputs_root=self.inputs,
                authoring_root=self.authoring,
                design_id="sample-design",
                version_id="v000001",
                profiles=[profile_path],
            )
        first = self.authoring / "sample-design/test-product/v000001/generation.json"
        second = self.authoring / "sample-design/second-product/v000001/generation.json"
        self.assertEqual(read_json(first)["product_profile_id"], "test-product")
        self.assertEqual(read_json(second)["product_profile_id"], "second-product")

    def test_generate_bundle_and_release_contract(self) -> None:
        profile = load_profile(self.profile_path)
        design_id = self._subject_design()
        generated = generate_design(
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            design_id=design_id,
            version_id="v000001",
            profiles=[profile],
            fit="cover",
            operation_id="product-test",
        )
        self.assertFalse(
            list((self.authoring / "sample-design/test-product").glob(".*.partial-*"))
        )
        reused = generate_design(
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            design_id=design_id,
            version_id="v000001",
            profiles=[profile],
            fit="cover",
            operation_id="product-test",
        )
        self.assertTrue(reused["reused"])
        self.assertEqual(
            [(item["width"], item["height"]) for item in generated["outputs"]],
            [(80, 80), (120, 60)],
        )
        self.assertEqual(
            (
                generated["profile_previews"][0]["width"],
                generated["profile_previews"][0]["height"],
            ),
            (64, 48),
        )
        profile_preview = (
            self.authoring
            / "sample-design/cat/test-product/v000001/profile-previews/test-product.png"
        )
        self.assertTrue(profile_preview.is_file())
        with Image.open(profile_preview) as preview:
            self.assertEqual(preview.size, (64, 48))
            self.assertEqual(preview.convert("RGBA").getextrema()[3], (255, 255))
        discovered = discover_designs(self.inputs, self.authoring)
        discovered_subject = next(
            item for item in discovered if item["design_id"] == design_id
        )
        self.assertEqual(
            discovered_subject["versions"][0]["profile_previews"],
            ["test-product.png"],
        )
        preparation_events: list[tuple[str, dict[str, object]]] = []
        plan = prepare_release(
            release_id="2026-10-20.001",
            selections=[BundleSelection(design_id, "v000001", profile)],
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            exchange_root=self.exchange,
            progress=lambda event, details: preparation_events.append((event, details)),
        )
        self.assertEqual(
            [event for event, _ in preparation_events],
            ["bundle-start", "bundle-complete", "release-validated"],
        )
        catalog = validate_release(plan)
        self.assert_schema("static-release-catalog-v1.schema.json", catalog)
        self.assertEqual(len(catalog["bundles"]), 1)
        manifest = validate_bundle(plan.bundle_manifests[0].parent)
        self.assert_schema("static-bundle-v1.schema.json", manifest)
        self.assertEqual(manifest["bundle_type"], "static-design")
        self.assertEqual(
            manifest["preview"],
            {"path": "preview.png", "width": 64, "height": 48},
        )
        with (
            Image.open(profile_preview).convert("RGBA") as checkpoint_preview,
            Image.open(plan.bundle_manifests[0].parent / "preview.png").convert(
                "RGBA"
            ) as bundle_preview,
        ):
            self.assertIsNone(
                ImageChops.difference(checkpoint_preview, bundle_preview).getbbox()
            )
        self.assertEqual(
            [target["target_id"] for target in manifest["print_targets"]],
            ["square", "wide"],
        )
        objects = publication_plan(
            plan, bucket="static-print-test-bucket", prefix="templates/static_design"
        )
        self.assertTrue(objects[-1]["key"].endswith("/catalog.json"))
        self.assertTrue(all("source-design.png" not in item["key"] for item in objects))
        publication_events: list[tuple[str, dict[str, object]]] = []
        dry_run = publish_release(
            plan,
            exchange_root=self.exchange,
            bucket="static-print-test-bucket",
            prefix="templates/static_design",
            aws_profile=None,
            region=None,
            execute=False,
            progress=lambda event, details: publication_events.append((event, details)),
        )
        self.assertEqual(
            [event for event, _ in publication_events],
            ["validation-complete"],
        )
        self.assertFalse(plan.staging_root.exists())
        self.assertTrue(all("source" not in item for item in dry_run["objects"]))
        retried = prepare_release(
            release_id="2026-10-20.001",
            selections=[BundleSelection(design_id, "v000001", profile)],
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            exchange_root=self.exchange,
        )
        self.assertEqual(
            plan.exchange_catalog_path.read_bytes(),
            retried.exchange_catalog_path.read_bytes(),
        )

    def test_existing_release_id_rejects_changed_bundle_bytes(self) -> None:
        profile = load_profile(self.profile_path)
        design_id = self._subject_design()
        generate_design(
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            design_id=design_id,
            version_id="v000001",
            profiles=[profile],
        )
        selection = BundleSelection(design_id, "v000001", profile)
        prepare_release(
            release_id="2026-10-20.001",
            selections=[selection],
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            exchange_root=self.exchange,
        )
        changed = (
            self.authoring / "sample-design/cat/test-product/v000001/"
            "sample-design--subject-cat--test-product--square.png"
        )
        Image.new("RGBA", (80, 80), (0, 0, 0, 255)).save(changed)
        with self.assertRaisesRegex(StaticPrintError, "use a new release ID"):
            prepare_release(
                release_id="2026-10-20.001",
                selections=[selection],
                inputs_root=self.inputs,
                authoring_root=self.authoring,
                exchange_root=self.exchange,
            )

    def test_release_rejects_generation_without_canvas_policy(self) -> None:
        profile = load_profile(self.profile_path)
        design_id = self._subject_design()
        generate_design(
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            design_id=design_id,
            version_id="v000001",
            profiles=[profile],
        )
        generation_path = (
            self.authoring / "sample-design/cat/test-product/v000001/generation.json"
        )
        generation = read_json(generation_path)
        generation.pop("canvas_policy")
        generation_path.write_text(json.dumps(generation), encoding="utf-8")
        with self.assertRaisesRegex(
            StaticPrintError, "does not match the current subject preview/profile"
        ):
            prepare_release(
                release_id="2026-10-23.001",
                selections=[BundleSelection(design_id, "v000001", profile)],
                inputs_root=self.inputs,
                authoring_root=self.authoring,
                exchange_root=self.exchange,
            )

    def test_release_rejects_transparent_output_for_opaque_canvas(self) -> None:
        profile = load_profile(self.profile_path)
        design_id = self._subject_design()
        generate_design(
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            design_id=design_id,
            version_id="v000001",
            profiles=[profile],
        )
        corrupted = (
            self.authoring / "sample-design/cat/test-product/v000001/"
            "sample-design--subject-cat--test-product--square.png"
        )
        Image.new("RGBA", (80, 80), (20, 30, 40, 0)).save(corrupted)
        with self.assertRaisesRegex(
            StaticPrintError, "violates opaque full-bleed canvas policy"
        ):
            prepare_release(
                release_id="2026-10-23.002",
                selections=[BundleSelection(design_id, "v000001", profile)],
                inputs_root=self.inputs,
                authoring_root=self.authoring,
                exchange_root=self.exchange,
            )

    def test_local_publication_receipts_remain_available_for_audit(self) -> None:
        release_id = "2026-10-20.001"
        release_root = self.exchange / "releases" / release_id
        release_root.mkdir(parents=True)
        (release_root / "catalog.json").write_text(
            json.dumps(
                {
                    "schema": "pawmarvel-static-release-catalog-v1",
                    "release_id": release_id,
                    "created_at": "2026-10-20T00:00:00Z",
                    "bundles": [
                        {
                            "bundle_id": "sample-design--test-product",
                            "design_id": "sample-design",
                            "product_profile_id": "test-product",
                            "version_id": "v000001",
                            "manifest_path": "unused",
                            "manifest_sha256": "a" * 64,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(_published_entries(self.exchange), {})
        publications = self.exchange / "publications"
        publications.mkdir(exist_ok=True)
        (publications / f"{release_id}.json").write_text(
            json.dumps({"release_id": release_id}), encoding="utf-8"
        )
        self.assertEqual(
            _published_entries(self.exchange)["sample-design"],
            [
                {
                    "release_id": release_id,
                    "product_profile_id": "test-product",
                    "version_id": "v000001",
                }
            ],
        )

    def test_feedback_is_upserted_per_reviewer_and_design(self) -> None:
        database = self.root / "static-reviews.sqlite3"
        save_feedback(
            database,
            design_id="sample-design",
            source_sha256="a" * 64,
            reviewer="reviewer@example.com",
            verdict="changes",
            comment="Adjust the crop",
        )
        save_feedback(
            database,
            design_id="sample-design",
            source_sha256="a" * 64,
            reviewer="reviewer@example.com",
            verdict="approve",
            comment="Looks good now",
        )
        feedback = list_feedback(database)["sample-design"]
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0]["verdict"], "approve")

    def test_reviewer_feedback_batch_is_recorded_together(self) -> None:
        database = self.root / "batch-reviews.sqlite3"
        records = save_feedbacks(
            database,
            reviewer="reviewer@example.com",
            feedback=[
                {
                    "design_id": "first-design",
                    "source_sha256": "a" * 64,
                    "verdict": "approve",
                    "comment": "ready",
                },
                {
                    "design_id": "second-design",
                    "source_sha256": "b" * 64,
                    "verdict": "changes",
                    "comment": "adjust crop",
                },
            ],
        )
        self.assertEqual(len(records), 2)
        stored = list_feedback(database)
        self.assertEqual(set(stored), {"first-design", "second-design"})

    def test_batch_subject_decisions_are_recorded_together(self) -> None:
        database = self.root / "static-reviews.sqlite3"
        results = save_operator_decisions(
            database,
            designs=[
                ("sample-design--subject-dog-corgi", "a" * 64),
                ("sample-design--subject-cat", "b" * 64),
            ],
            decision="approved",
            note="Batch approved",
        )
        self.assertEqual(len(results), 2)
        decisions = list_operator_decisions(database)
        self.assertEqual(
            decisions["sample-design--subject-dog-corgi"][0]["decision"],
            "approved",
        )
        self.assertEqual(
            decisions["sample-design--subject-cat"][0]["note"],
            "Batch approved",
        )

    def test_seed_lifecycle_subject_generation_and_approval(self) -> None:
        graduated = self.root / "static-graduated-designs"
        destination = move_seed(
            self.root,
            seed_design_id="sample-design",
            source="active",
            target="graduated",
        )
        self.assertEqual(destination, (graduated / "sample-design").resolve())
        subject = Subject(
            subject_id="dog-dachshund",
            display_name="Dachshund",
            subject_kind="dog-breed",
            hero_strategy="breed-specific",
            collection_path="/collections/dachshund-gifts",
        )

        def fake_generator(source, selected, prompt, size):
            self.assertEqual(source, destination / "source-design.png")
            self.assertEqual(selected, subject)
            self.assertIn("Dachshund", prompt)
            self.assertIn("keep the red scarf", prompt)
            self.assertIn("opaque, full-bleed design", prompt)
            return Image.new("RGBA", size, (10, 20, 30, 0))

        generated = generate_subject_variants(
            graduated_root=graduated,
            seed_design_id="sample-design",
            subjects=[subject],
            generator=fake_generator,
            instruction="keep the red scarf",
        )
        design_id = "sample-design--subject-dog-dachshund"
        self.assertEqual(generated[0]["design_id"], design_id)
        output = graduated / "sample-design/dog-dachshund/source-design.png"
        self.assertTrue(output.is_file())
        self.assertFalse(transparency_report(output)["has_transparent_pixels"])
        provenance = read_json(output.parent / "subject-generation.json")
        self.assertEqual(provenance["operator_instruction"], "keep the red scarf")
        self.assertEqual(provenance["canvas_policy"], "opaque-full-bleed")
        self.assertEqual(provenance["output_transparency"]["result"], "opaque")
        discovered = discover_designs(graduated, self.authoring)
        discovered_subject = next(
            item for item in discovered if item["design_id"] == design_id
        )
        self.assertEqual(discovered_subject["source_transparency"]["result"], "opaque")
        decision = save_operator_decision(
            self.root / "static-reviews.sqlite3",
            design_id=design_id,
            source_sha256=generated[0]["source_sha256"],
            decision="approved",
        )
        self.assertEqual(decision["decision"], "approved")
        self.assertEqual(
            list_operator_decisions(self.root / "static-reviews.sqlite3")[design_id][0][
                "decision"
            ],
            "approved",
        )

    def test_flat_review_collection_import_preserves_original(self) -> None:
        collection = self.root / "Holiday Design Collection"
        collection.mkdir()
        original = collection / "Merry Pawlidays!.PNG"
        Image.new("RGBA", (80, 90), (20, 100, 180, 0)).save(original)

        imported = import_review_collection(self.root, collection)
        self.assertEqual(imported["collection_id"], "holiday-design-collection")
        self.assertEqual(
            [item["design_id"] for item in imported["imported"]],
            ["merry-pawlidays"],
        )
        active = self.inputs / "merry-pawlidays"
        self.assertTrue((active / "source-design.png").is_file())
        intake = read_json(active / "intake.json")
        self.assertEqual(intake["schema"], "pawmarvel-static-review-intake-v1")
        self.assertEqual(intake["source_file"], str(original.resolve()))
        self.assertEqual(intake["canvas_policy"], "preserve-transparency")
        self.assertEqual(
            intake["source_transparency"]["result"], "transparent-background"
        )
        discovered = discover_designs(self.inputs, self.authoring)
        imported_design = next(
            item for item in discovered if item["design_id"] == "merry-pawlidays"
        )
        self.assertEqual(
            imported_design["intake"]["collection_id"],
            "holiday-design-collection",
        )
        self.assertEqual(
            imported_design["source_transparency"]["result"],
            "transparent-background",
        )

        move_seed(
            self.root,
            seed_design_id="merry-pawlidays",
            source="active",
            target="graduated",
        )
        self.assertTrue(original.is_file())
        retried = import_review_collection(self.root, collection)
        self.assertEqual(retried["imported"], [])
        self.assertEqual(
            retried["skipped"][0]["reason"], "already exists in graduated pool"
        )

    def test_collection_reimport_enforces_recorded_opaque_canvas_policy(self) -> None:
        collection = self.root / "Holiday Selfie"
        collection.mkdir()
        original = collection / "Full Bleed.png"
        Image.new("RGB", (40, 40), (20, 30, 40)).save(original)
        import_review_collection(self.root, collection)
        intake = read_json(self.inputs / "full-bleed/intake.json")
        self.assertEqual(intake["canvas_policy"], "opaque-full-bleed")

        Image.new("RGBA", (40, 40), (20, 30, 40, 0)).save(original)
        with self.assertRaisesRegex(
            StaticPrintError, "violates its recorded opaque full-bleed canvas policy"
        ):
            import_review_collection(self.root, collection)

    def test_batch_seed_move_preflights_and_moves_complete_selection(self) -> None:
        second = self.inputs / "second-design"
        second.mkdir()
        Image.new("RGBA", (40, 40), (40, 50, 60, 255)).save(
            second / "source-design.png"
        )
        destinations = move_seeds(
            self.root,
            seed_design_ids=["sample-design", "second-design"],
            source="active",
            target="graduated",
        )
        self.assertEqual(len(destinations), 2)
        self.assertFalse((self.inputs / "sample-design").exists())
        self.assertFalse(second.exists())
        self.assertTrue(
            (
                self.root / "static-graduated-designs/sample-design/source-design.png"
            ).is_file()
        )
        self.assertTrue(
            (
                self.root / "static-graduated-designs/second-design/source-design.png"
            ).is_file()
        )

    def test_transparency_report_distinguishes_opaque_and_transparent(self) -> None:
        opaque = self.root / "opaque.png"
        transparent = self.root / "transparent.png"
        Image.new("RGB", (4, 4), (1, 2, 3)).save(opaque)
        Image.new("RGBA", (4, 4), (1, 2, 3, 0)).save(transparent)
        self.assertEqual(transparency_report(opaque)["result"], "opaque")
        report = transparency_report(transparent)
        self.assertEqual(report["result"], "transparent-background")
        self.assertTrue(report["has_transparent_background"])
        self.assertEqual(report["transparent_pixel_percent"], 100.0)

    def test_transparent_seed_preserves_subject_transparency_policy(self) -> None:
        graduated = self.root / "static-graduated-designs"
        destination = move_seed(
            self.root,
            seed_design_id="sample-design",
            source="active",
            target="graduated",
        )
        Image.new("RGBA", (100, 50), (10, 20, 30, 0)).save(
            destination / "source-design.png"
        )
        subject = Subject(
            subject_id="cat",
            display_name="Cat",
            subject_kind="pet-kind",
            hero_strategy="mixed-coat",
            collection_path="/collections/cat-gifts",
        )

        def fake_generator(source, selected, prompt, size):
            self.assertIn("intentional alpha transparency", prompt)
            return Image.new("RGBA", size, (40, 50, 60, 0))

        generate_subject_variants(
            graduated_root=graduated,
            seed_design_id="sample-design",
            subjects=[subject],
            generator=fake_generator,
        )
        output = destination / "cat/source-design.png"
        self.assertTrue(transparency_report(output)["has_transparent_pixels"])
        provenance = read_json(output.parent / "subject-generation.json")
        self.assertEqual(provenance["canvas_policy"], "preserve-transparency")

    def test_subject_variant_uses_nested_private_folders(self) -> None:
        nested = self.inputs / "holiday-patrol/dog-dachshund"
        nested.mkdir(parents=True)
        Image.new("RGBA", (100, 50), (10, 90, 180, 255)).save(
            nested / "source-design.png"
        )
        design_id = "holiday-patrol--subject-dog-dachshund"
        designs = discover_designs(self.inputs, self.authoring)
        variant = next(item for item in designs if item["design_id"] == design_id)
        self.assertEqual(variant["seed_design_id"], "holiday-patrol")
        self.assertEqual(variant["subject_id"], "dog-dachshund")
        result = generate_design(
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            design_id=design_id,
            version_id="v000001",
            profiles=[self.profile_path],
        )
        expected_root = (
            self.authoring / "holiday-patrol/dog-dachshund/test-product/v000001"
        ).resolve()
        self.assertTrue((expected_root / f"{design_id}-preview.png").is_file())
        self.assertTrue(
            all(
                Path(item["path"]).is_relative_to(expected_root)
                for item in result["outputs"]
            )
        )
        profile = load_profile(self.profile_path)
        plan = prepare_release(
            release_id="2026-10-21.001",
            selections=[BundleSelection(design_id, "v000001", profile)],
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            exchange_root=self.exchange,
        )
        manifest = validate_bundle(plan.bundle_manifests[0].parent)
        self.assertEqual(manifest["design_id"], design_id)

    def test_subject_product_inherits_opaque_seed_canvas_policy(self) -> None:
        seed = self.inputs / "holiday-selfie"
        seed.mkdir()
        Image.new("RGB", (100, 100), (20, 30, 40)).save(seed / "source-design.png")
        (seed / "intake.json").write_text(
            json.dumps(
                {
                    "schema": "pawmarvel-static-review-intake-v1",
                    "collection_id": "holiday-selfie",
                    "canvas_policy": "opaque-full-bleed",
                }
            ),
            encoding="utf-8",
        )
        subject = seed / "cat"
        subject.mkdir()
        Image.new("RGBA", (100, 100), (90, 100, 110, 120)).save(
            subject / "source-design.png"
        )
        design_id = "holiday-selfie--subject-cat"
        result = generate_design(
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            design_id=design_id,
            version_id="v000001",
            profiles=[self.profile_path],
        )
        self.assertEqual(result["canvas_policy"], "opaque-full-bleed")
        generation = read_json(
            self.authoring / "holiday-selfie/cat/test-product/v000001/generation.json"
        )
        self.assertEqual(generation["canvas_policy"], "opaque-full-bleed")
        self.assertFalse(
            generation["transparency"]["operator_preview"]["has_transparent_pixels"]
        )
        self.assertFalse(
            generation["transparency"]["profile_preview"]["has_transparent_pixels"]
        )
        self.assertTrue(generation["transparency"]["prints"])
        self.assertTrue(
            all(
                not report["has_transparent_pixels"]
                for report in generation["transparency"]["prints"].values()
            )
        )
        for item in result["outputs"] + result["profile_previews"]:
            self.assertFalse(
                transparency_report(Path(item["path"]))["has_transparent_pixels"]
            )

    def test_changed_subject_preview_invalidates_generated_version(self) -> None:
        nested = self.inputs / "holiday-patrol/dog-corgi"
        nested.mkdir(parents=True)
        source = nested / "source-design.png"
        Image.new("RGBA", (100, 50), (10, 90, 180, 255)).save(source)
        design_id = "holiday-patrol--subject-dog-corgi"
        generate_design(
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            design_id=design_id,
            version_id="v000001",
            profiles=[self.profile_path],
        )
        Image.new("RGBA", (100, 50), (200, 40, 20, 255)).save(source)
        with self.assertRaisesRegex(
            StaticPrintError, "does not match the current subject preview"
        ):
            prepare_release(
                release_id="2026-10-22.001",
                selections=[
                    BundleSelection(
                        design_id,
                        "v000001",
                        load_profile(self.profile_path),
                    )
                ],
                inputs_root=self.inputs,
                authoring_root=self.authoring,
                exchange_root=self.exchange,
            )

    def test_changed_product_profile_invalidates_generated_version(self) -> None:
        design_id = self._subject_design()
        generate_design(
            inputs_root=self.inputs,
            authoring_root=self.authoring,
            design_id=design_id,
            version_id="v000001",
            profiles=[self.profile_path],
            fit="cover",
        )
        changed_path = self.root / "changed-profile.json"
        profile_value = read_json(self.profile_path)
        profile_value["preview"]["width"] = 65
        changed_path.write_text(json.dumps(profile_value), encoding="utf-8")
        changed_profile = load_profile(changed_path)
        with self.assertRaisesRegex(
            StaticPrintError,
            "does not match the current subject preview/profile",
        ):
            prepare_release(
                release_id="2026-10-24.001",
                selections=[BundleSelection(design_id, "v000001", changed_profile)],
                inputs_root=self.inputs,
                authoring_root=self.authoring,
                exchange_root=self.exchange,
            )

    def test_missing_source_reports_absolute_path(self) -> None:
        with self.assertRaisesRegex(StaticPrintError, str(self.inputs.resolve())):
            generate_design(
                inputs_root=self.inputs,
                authoring_root=self.authoring,
                design_id="missing-design",
                version_id="v000001",
                profiles=[self.profile_path],
            )

    def test_checked_in_product_profile_dimensions(self) -> None:
        profiles_root = Path(__file__).resolve().parents[1] / "profiles"
        profiles = discover_profiles(profiles_root)
        actual = {
            profile_id: (
                (profile.preview_width, profile.preview_height),
                [(target.width, target.height) for target in profile.targets],
            )
            for profile_id, profile in profiles.items()
        }
        expected = {
            "coffee-mug-11oz": ((837, 1020), [(837, 1020)]),
            "mousepad": ((1080, 900), [(2700, 2250)]),
            "poster-modern-portrait": (
                (1080, 1350),
                [(2400, 3000), (3600, 3600), (4800, 6000), (7200, 10800)],
            ),
            "wrapped-canvas": ((1080, 1350), [(2400, 3000), (9000, 12000)]),
            "pillow": ((1080, 1080), [(2850, 2850)]),
            "fleece-sherpa-blankets": (
                (1080, 1296),
                [(7875, 9375), (9375, 12375)],
            ),
            "beach-towel": ((1080, 2188), [(5551, 11250)]),
            "ornament": (
                (1080, 1080),
                [(992, 992), (1098, 1063), (780, 1087), (1004, 933)],
            ),
            "sticker": (
                (1080, 1080),
                [(600, 600), (900, 900), (1200, 1200), (1500, 1500), (1800, 1800)],
            ),
            "tumbler-20oz": ((1080, 811), [(2795, 2100)]),
            "unisex-tee-crewneck-hoodie-tanks": ((1080, 1430), [(3423, 4533)]),
            "womens-tee-bella-6400": ((1080, 1431), [(2812, 3725)]),
            "mens-tee-house-style": ((1080, 1234), [(4200, 4800)]),
        }
        self.assertEqual(actual, expected)

    def test_checked_in_subject_expansion_catalog(self) -> None:
        project = Path(__file__).resolve().parents[1]
        catalog_path = project / "catalog/static-design-subjects.json"
        catalog = read_json(catalog_path)
        self.assert_schema("static-subject-catalog-v1.schema.json", catalog)
        _, loaded_subjects = load_subject_catalog(catalog_path)
        self.assertEqual(catalog["schema"], "pawmarvel-static-subject-catalog-v1")
        self.assertEqual(
            catalog["design_id_pattern"],
            "<seed-design-id>--subject-<subject-id>",
        )
        self.assertEqual(catalog["workspace_pattern"], "<seed-design-id>/<subject-id>")
        self.assertEqual(
            catalog["subject_id_contract"],
            {
                "authority": "explicit-subject-id",
                "format": "lowercase-kebab-case",
                "immutable": True,
            },
        )
        self.assertEqual(
            catalog["expansion_policy"],
            {
                "seed_approval_required": True,
                "allow_partial_subject_coverage": True,
                "preserve_seed_canvas_dimensions": True,
                "product_generation_requires_subject_approval": True,
            },
        )
        subjects = catalog["subjects"]
        self.assertEqual(
            [subject.subject_id for subject in loaded_subjects],
            [item["subject_id"] for item in subjects],
        )
        self.assertEqual([item["priority"] for item in subjects], list(range(1, 12)))
        self.assertEqual(
            [item["subject_id"] for item in subjects],
            [
                "dog-dachshund",
                "cat",
                "dog-mixed-breed",
                "dog-golden-retriever",
                "dog-french-bulldog",
                "dog-labrador",
                "dog-corgi",
                "dog-goldendoodle",
                "dog-pug",
                "cat-black",
                "dog-german-shepherd",
            ],
        )
        for subject in subjects:
            self.assertEqual(
                subject["collection_path"],
                f"/collections/{subject['collection_handle']}",
            )

    def test_checked_in_profiles_match_public_schema(self) -> None:
        project = Path(__file__).resolve().parents[1]
        for profile_path in sorted((project / "profiles").glob("*.json")):
            with self.subTest(profile=profile_path.name):
                self.assert_schema(
                    "static-product-profile-v1.schema.json",
                    read_json(profile_path),
                )

    def test_discovery_catalog_matches_public_schema(self) -> None:
        release = {
            "schema": "pawmarvel-static-release-catalog-v1",
            "release_id": "2026-10-15.001",
            "created_at": "2026-10-15T10:00:00Z",
            "bundles": [
                {
                    "bundle_id": "sample-design--subject-cat--test-product",
                    "design_id": "sample-design--subject-cat",
                    "product_profile_id": "test-product",
                    "version_id": "v000001",
                    "manifest_path": (
                        "bundles/sample-design--subject-cat--test-product/"
                        "v000001/bundle.json"
                    ),
                    "manifest_sha256": "a" * 64,
                }
            ],
        }
        catalog = build_catalog([release], updated_at="2026-10-15T11:00:00Z")
        self.assert_schema("static-catalog-v1.schema.json", catalog)

    def test_subject_and_bundle_identity_use_unambiguous_double_hyphens(self) -> None:
        design_id = "holiday-patrol--subject-dog-dachshund"
        identity = design_identity(design_id)
        self.assertEqual(identity.seed_design_id, "holiday-patrol")
        self.assertEqual(identity.subject_id, "dog-dachshund")
        self.assertEqual(identity.design_id, design_id)
        self.assertEqual(
            bundle_id_for(design_id, "coffee-mug-11oz"),
            "holiday-patrol--subject-dog-dachshund--coffee-mug-11oz",
        )
        with self.assertRaisesRegex(StaticPrintError, "subject design ID"):
            bundle_id_for("holiday-patrol-subject-dog-dachshund", "coffee-mug-11oz")

    def test_obsolete_retirement_finds_only_contract_invalid_artifacts(self) -> None:
        legacy = self.authoring / "legacy-design/v000001"
        legacy.mkdir(parents=True)
        (legacy / "generation.json").write_text(
            json.dumps({"schema": "pawmarvel-static-generation-v2"}),
            encoding="utf-8",
        )
        local = plan_obsolete_local_artifacts(self.root)
        self.assertEqual(
            {Path(item["path"]) for item in local},
            {legacy.resolve()},
        )

        catalog = build_catalog([], updated_at="2026-10-15T11:00:00Z")
        objects = [
            {
                "key": "prefix/",
                "relative_path": "",
                "etag": '"root"',
                "bytes": 0,
                "last_modified": "then",
            },
            {
                "key": "prefix/catalog.json",
                "relative_path": "catalog.json",
                "etag": '"catalog"',
                "bytes": 10,
                "last_modified": "now",
            },
            {
                "key": "prefix/bundles/seed-subject-cat--test-product/v000001/preview.png",
                "relative_path": (
                    "bundles/seed-subject-cat--test-product/v000001/preview.png"
                ),
                "etag": '"legacy"',
                "bytes": 20,
                "last_modified": "then",
            },
            {
                "key": "prefix/catalog-history/legacy.json",
                "relative_path": "catalog-history/legacy.json",
                "etag": '"history"',
                "bytes": 30,
                "last_modified": "then",
            },
        ]

        def download(*, key: str, **_: object) -> dict[str, object]:
            if key.endswith("catalog.json"):
                return catalog
            return {"schema": "old-catalog"}

        with (
            patch(
                "pawmarvel_static_print.retirement._list_s3_objects",
                return_value=objects,
            ),
            patch(
                "pawmarvel_static_print.retirement._download_s3_json",
                side_effect=download,
            ),
        ):
            remote = plan_obsolete_s3_artifacts(
                bucket="bucket", prefix="prefix", profile=None, region=None
            )
        self.assertEqual(
            [item["key"] for item in remote],
            [
                "prefix/",
                "prefix/bundles/seed-subject-cat--test-product/v000001/preview.png",
                "prefix/catalog-history/legacy.json",
            ],
        )

    def test_failed_obsolete_retirement_can_resume(self) -> None:
        obsolete = self.authoring / "legacy-design/v000001"
        obsolete.mkdir(parents=True)
        (obsolete / "generation.json").write_text(
            json.dumps({"schema": "pawmarvel-static-generation-v2"}),
            encoding="utf-8",
        )
        local = plan_obsolete_local_artifacts(self.root)
        remote = [
            {
                "key": "prefix/catalog-history/legacy.json",
                "relative_path": "catalog-history/legacy.json",
                "etag": '"legacy"',
                "bytes": 10,
                "last_modified": "then",
                "reason": "old schema",
            }
        ]
        arguments = {
            "work_root": self.root,
            "bucket": "bucket",
            "prefix": "prefix",
            "profile": None,
            "region": None,
            "retirement_id": "legacy-cleanup",
            "reason": "remove pre-contract files",
            "execute": False,
        }
        with (
            patch(
                "pawmarvel_static_print.retirement.plan_obsolete_local_artifacts",
                return_value=local,
            ),
            patch(
                "pawmarvel_static_print.retirement.plan_obsolete_s3_artifacts",
                return_value=remote,
            ),
        ):
            planned = retire_obsolete_artifacts(**arguments)
        self.assertEqual(planned["status"], "planned")

        arguments["execute"] = True
        with (
            patch(
                "pawmarvel_static_print.retirement._assert_pending_s3_targets_not_catalog_referenced"
            ),
            patch(
                "pawmarvel_static_print.retirement._preflight_s3_object",
                return_value=True,
            ),
            patch(
                "pawmarvel_static_print.retirement._delete_s3_object",
                side_effect=StaticPrintError("temporary delete failure"),
            ),
            self.assertRaisesRegex(StaticPrintError, "temporary delete failure"),
        ):
            retire_obsolete_artifacts(**arguments)

        receipt_path = self.exchange / "retirements/legacy-cleanup.json"
        self.assertEqual(read_json(receipt_path)["status"], "failed")
        self.assertFalse(obsolete.exists())
        arguments["execute"] = False
        self.assertEqual(retire_obsolete_artifacts(**arguments)["status"], "failed")
        arguments["execute"] = True

        with (
            patch(
                "pawmarvel_static_print.retirement._assert_pending_s3_targets_not_catalog_referenced"
            ),
            patch(
                "pawmarvel_static_print.retirement._preflight_s3_object",
                return_value=True,
            ),
            patch("pawmarvel_static_print.retirement._delete_s3_object") as delete,
        ):
            result = retire_obsolete_artifacts(**arguments)
        self.assertEqual(result["schema"], RETIREMENT_SCHEMA)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["retired_local_artifacts"]), 1)
        self.assertEqual(len(result["retired_s3_objects"]), 1)
        delete.assert_called_once()

    def test_retirement_execution_requires_a_saved_reviewed_plan(self) -> None:
        with self.assertRaisesRegex(StaticPrintError, "reviewed saved plan"):
            retire_obsolete_artifacts(
                work_root=self.root,
                bucket="bucket",
                prefix="prefix",
                profile=None,
                region=None,
                retirement_id="legacy-cleanup",
                reason="remove pre-contract files",
                execute=True,
            )

    def test_retirement_stops_when_local_artifact_changes_after_planning(self) -> None:
        obsolete = self.authoring / "legacy-design/v000001"
        obsolete.mkdir(parents=True)
        record = obsolete / "generation.json"
        record.write_text(
            json.dumps({"schema": "pawmarvel-static-generation-v2"}),
            encoding="utf-8",
        )
        arguments = {
            "work_root": self.root,
            "bucket": "bucket",
            "prefix": "prefix",
            "profile": None,
            "region": None,
            "retirement_id": "legacy-cleanup",
            "reason": "remove pre-contract files",
            "execute": False,
        }
        with patch(
            "pawmarvel_static_print.retirement.plan_obsolete_s3_artifacts",
            return_value=[],
        ):
            retire_obsolete_artifacts(**arguments)
        record.write_text(
            json.dumps({"schema": "changed-after-review"}), encoding="utf-8"
        )
        arguments["execute"] = True
        with self.assertRaisesRegex(StaticPrintError, "changed after review"):
            retire_obsolete_artifacts(**arguments)
        self.assertTrue(obsolete.is_dir())

    def test_retirement_protects_stable_catalog_references(self) -> None:
        release = {
            "schema": "pawmarvel-static-release-catalog-v1",
            "release_id": "2026-10-15.001",
            "created_at": "2026-10-15T10:00:00Z",
            "bundles": [
                {
                    "bundle_id": "sample-design--subject-cat--test-product",
                    "design_id": "sample-design--subject-cat",
                    "product_profile_id": "test-product",
                    "version_id": "v000001",
                    "manifest_path": (
                        "bundles/sample-design--subject-cat--test-product/"
                        "v000001/bundle.json"
                    ),
                    "manifest_sha256": "a" * 64,
                }
            ],
        }
        catalog = build_catalog([release], updated_at="2026-10-15T11:00:00Z")
        objects = [
            {
                "key": "prefix/catalog.json",
                "relative_path": "catalog.json",
                "etag": '"catalog"',
                "bytes": 10,
                "last_modified": "now",
            },
            {
                "key": (
                    "prefix/bundles/sample-design--subject-cat--test-product/"
                    "v000001/unexpected.txt"
                ),
                "relative_path": (
                    "bundles/sample-design--subject-cat--test-product/"
                    "v000001/unexpected.txt"
                ),
                "etag": '"asset"',
                "bytes": 10,
                "last_modified": "now",
            },
        ]
        with (
            patch(
                "pawmarvel_static_print.retirement._list_s3_objects",
                return_value=objects,
            ),
            patch(
                "pawmarvel_static_print.retirement._download_s3_json",
                return_value=catalog,
            ),
            self.assertRaisesRegex(StaticPrintError, "stable FE catalog references"),
        ):
            plan_obsolete_s3_artifacts(
                bucket="bucket", prefix="prefix", profile=None, region=None
            )

    def test_retirement_rechecks_catalog_references_before_execution(self) -> None:
        target_key = (
            "prefix/bundles/sample-design--subject-cat--test-product/"
            "v000001/preview.png"
        )
        remote = [
            {
                "key": target_key,
                "relative_path": target_key.removeprefix("prefix/"),
                "etag": '"asset"',
                "bytes": 10,
                "last_modified": "now",
                "reason": "bundle was incomplete when reviewed",
            }
        ]
        arguments = {
            "work_root": self.root,
            "bucket": "bucket",
            "prefix": "prefix",
            "profile": None,
            "region": None,
            "retirement_id": "legacy-cleanup",
            "reason": "remove pre-contract files",
            "execute": False,
        }
        with (
            patch(
                "pawmarvel_static_print.retirement.plan_obsolete_local_artifacts",
                return_value=[],
            ),
            patch(
                "pawmarvel_static_print.retirement.plan_obsolete_s3_artifacts",
                return_value=remote,
            ),
        ):
            retire_obsolete_artifacts(**arguments)

        release = {
            "schema": "pawmarvel-static-release-catalog-v1",
            "release_id": "2026-10-15.001",
            "created_at": "2026-10-15T10:00:00Z",
            "bundles": [
                {
                    "bundle_id": "sample-design--subject-cat--test-product",
                    "design_id": "sample-design--subject-cat",
                    "product_profile_id": "test-product",
                    "version_id": "v000001",
                    "manifest_path": (
                        "bundles/sample-design--subject-cat--test-product/"
                        "v000001/bundle.json"
                    ),
                    "manifest_sha256": "a" * 64,
                }
            ],
        }
        catalog = build_catalog([release], updated_at="2026-10-15T11:00:00Z")
        catalog_object = {
            "key": "prefix/catalog.json",
            "relative_path": "catalog.json",
            "etag": '"catalog"',
            "bytes": 10,
            "last_modified": "now",
        }
        arguments["execute"] = True
        with (
            patch(
                "pawmarvel_static_print.retirement._list_s3_objects",
                return_value=[catalog_object],
            ),
            patch(
                "pawmarvel_static_print.retirement._download_s3_json",
                return_value=catalog,
            ),
            self.assertRaisesRegex(StaticPrintError, "became unsafe after review"),
        ):
            retire_obsolete_artifacts(**arguments)
        receipt = read_json(self.exchange / "retirements/legacy-cleanup.json")
        self.assertEqual(receipt["status"], "failed")


if __name__ == "__main__":
    unittest.main()
