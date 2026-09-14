from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from helpers import copy_font, layout_data, make_image, make_transparent_mark
from pawmarvel_generator.bundle import BundleError, catalog_template_id, media_type
from pawmarvel_generator.bundle_cli import main as bundle_cli_main
from pawmarvel_generator.catalog_cli import main as catalog_cli_main
from pawmarvel_generator.release_catalog import build_release, validate_release
from pawmarvel_generator.s3_publisher import build_s3_publication_plan, publish_s3
from pawmarvel_generator.image_size import ImageSize
from pawmarvel_generator.production_bundle import validate_production_bundle
from pawmarvel_generator.product_profile import create_product_profile, write_product_profile
from pawmarvel_generator.renderer import render_to_files


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _asset(path: Path, root: Path) -> dict[str, object]:
    value: dict[str, object] = {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "media_type": media_type(path),
    }
    if path.suffix.lower() == ".png":
        with Image.open(path) as image:
            value.update(width=image.width, height=image.height)
    return value


def _validate_schema(instance: object, schema_name: str) -> None:
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    ).validate(instance)


class BundleContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.exchange = Path(self.temp.name) / "exchange"
        self.bundle = (
            self.exchange
            / "bundles"
            / "life-is-good--test-blanket"
            / "v000001"
        )
        self._write_bundle()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_bundle(self) -> None:
        root = self.bundle
        (root / "print").mkdir(parents=True)
        (root / "qa").mkdir()
        (root / "fonts").mkdir()
        profile = create_product_profile(
            profile_id="test-blanket",
            print_size=ImageSize(1344, 2016),
        )
        write_product_profile(root / "product-profile.json", profile)
        make_transparent_mark(root / "art.png", size=(672, 1008))
        make_transparent_mark(root / "print" / "art.png", size=(1344, 2016))
        make_image(root / "qa" / "input-pet.png", size=(300, 300))
        make_transparent_mark(root / "qa" / "transformed-pet.png", size=(816, 816))
        make_image(root / "reference-design.png", size=(300, 300))
        copy_font(root / "fonts")
        (root / "art-template-gpt.md").write_text("Generate reusable art.\n", encoding="utf-8")
        (root / "pet-transform-gpt.md").write_text("Transform the user pet.\n", encoding="utf-8")

        preview = layout_data()
        (root / "layout.json").write_text(json.dumps(preview), encoding="utf-8")
        print_layout = layout_data()
        print_layout["art"] = "print/art.png"
        for layer in ("pet", "name"):
            for key in ("x", "y", "width", "height"):
                print_layout[layer]["box"][key] *= 2
        for key in ("font_size_px", "min_font_size_px", "padding_px"):
            print_layout["name"][key] *= 2
        (root / "layout-print.json").write_text(json.dumps(print_layout), encoding="utf-8")
        render_to_files(
            template_dir=root,
            pet_image=root / "qa" / "transformed-pet.png",
            pet_name="PET",
            output=root / "qa" / "golden-preview.png",
            debug_output=root / "qa" / "golden-preview-debug.png",
        )
        manifest: dict[str, object] = {
            "schema_version": 1,
            "template_id": "life-is-good--test-blanket",
            "design_id": "life-is-good",
            "product_profile_id": "test-blanket",
            "bundle_revision": 1,
            "created_at": "2026-09-07T20:00:00Z",
            "runtime": {
                "provider": "openai",
                "model": "gpt-image-2",
                "transport": "images.edits",
                "prompt": "pet-transform-gpt.md",
                "reference_assets": ["reference-design.png"],
                "input_image_order": ["user_pet", "reference_1"],
                "request_parameters": {
                    "quality": "high",
                    "size": "816x816",
                    "background": "transparent",
                    "output_format": "png",
                    "n": 1,
                },
                "output": {
                    "format": "png",
                    "background": "transparent",
                    "width": 816,
                    "height": 816,
                },
                "normalization": {
                    "policy": "transparent-rgba-contain",
                    "version": 1,
                    "alpha_failure": "reject",
                },
            },
            "renderer": {
                "layout_schema_version": 2,
                "pet_fit": "contain-visible-alpha",
                "pet_anchor": "bottom-center",
                "name_fit": "nominal-size-shrink-only-visible-ink-contain",
                "version": 2,
            },
            "personalization": {
                "pet_name": {
                    "normalization": "NFC",
                    "whitespace": "trim-and-collapse",
                    "length_unit": "unicode-code-points",
                    "min_length": 1,
                    "max_length": 12,
                    "allowed_characters": (
                        "unicode-letters-marks-numbers-space-apostrophes-"
                        "ascii-hyphen"
                    ),
                }
            },
            "provenance": {
                "selection_id": "selection-v01",
                "selection_sha256": "a" * 64,
                "art_attempt_id": "attempt-0001",
                "pet_experiment_id": "pet-gpt-v01",
                "representative_pet_attempt_id": "benchmark-pet-0001",
                "representative_pet_sha256": _sha256(
                    root / "qa" / "transformed-pet.png"
                ),
                "qa_fixture": {
                    "input_pet": "qa/input-pet.png",
                    "pet_name": "PET",
                },
                "layout_attempt_id": "attempt-0001",
                "component_evaluations": {
                    kind: {
                        "evaluation_id": f"{kind}-eval-v01",
                        "evaluation_sha256": "a" * 64,
                        "status": "passed",
                    }
                    for kind in ("art", "pet", "layout")
                },
                "compatibility_evaluation": {
                    "evaluation_id": "assembly-eval-v01",
                    "evaluation_sha256": "b" * 64,
                    "status": "passed",
                },
                "print_candidate": {
                    "print_candidate_id": "print-finalist-0001",
                    "print_candidate_sha256": "c" * 64,
                    "status": "passed",
                },
                "selected": {
                    "art": {
                        "experiment_id": "art-gpt-v01",
                        "attempt_id": "attempt-0001",
                        "artifact_sha256": _sha256(root / "art.png"),
                    },
                    "pet_runtime": {
                        "experiment_id": "pet-gpt-v01",
                        "experiment_sha256": "d" * 64,
                    },
                    "layout_font": {
                        "experiment_id": "layout-v01",
                        "attempt_id": "attempt-0001",
                        "layout_sha256": _sha256(root / "layout.json"),
                        "font_sha256": _sha256(root / "fonts" / "TestFont.ttf"),
                    },
                },
                "print_derivation": {
                    "mode": "selected-print-candidate",
                    "print_candidate_id": "print-finalist-0001",
                    "print_candidate_sha256": "c" * 64,
                    "backends": {
                        "template": "deterministic",
                        "pet": "deterministic",
                    },
                    "template_source": {"mode": "generated"},
                    "art_sha256": _sha256(root / "print" / "art.png"),
                    "layout_sha256": _sha256(root / "layout-print.json"),
                },
                "generator": {"version": "0.1.0"},
            },
            "prompts": {
                "art_template": "art-template-gpt.md",
                "pet_transform": "pet-transform-gpt.md",
            },
            "assets": [],
        }
        manifest["assets"] = [
            _asset(path, root)
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != "bundle.json"
        ]
        (root / "bundle.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def _refresh_assets(self) -> None:
        manifest_path = self.bundle / "bundle.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"] = [
            _asset(path, self.bundle)
            for path in sorted(self.bundle.rglob("*"))
            if path.is_file() and path.name != "bundle.json"
        ]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    def test_validates_complete_closed_contract(self) -> None:
        manifest = validate_production_bundle(self.bundle)
        _validate_schema(manifest, "bundle-v1.schema.json")
        _validate_schema(
            json.loads((self.bundle / "layout.json").read_text(encoding="utf-8")),
            "layout-v2.schema.json",
        )
        _validate_schema(
            json.loads(
                (self.bundle / "layout-print.json").read_text(encoding="utf-8")
            ),
            "layout-v2.schema.json",
        )
        _validate_schema(
            json.loads(
                (self.bundle / "product-profile.json").read_text(encoding="utf-8")
            ),
            "product-profile-v1.schema.json",
        )
        self.assertEqual(manifest["template_id"], "life-is-good--test-blanket")
        self.assertEqual(manifest["runtime"]["input_image_order"][0], "user_pet")

    def test_validates_optional_remote_font_provenance(self) -> None:
        metadata = self.bundle / "fonts" / "METADATA.pb"
        metadata.write_text(
            'name: "Test Font"\nlicense: "OFL"\n', encoding="utf-8"
        )
        font = self.bundle / "fonts" / "TestFont.ttf"
        license_path = self.bundle / "fonts" / "OFL.txt"
        source = {
            "schema_version": 1,
            "source": "google-fonts-ofl",
            "family_id": "testfont",
            "family": "Test Font",
            "source_url": "https://github.com/google/fonts/tree/main/ofl/testfont",
            "font_filename": font.name,
            "font_sha256": _sha256(font),
            "license_sha256": _sha256(license_path),
            "metadata_sha256": _sha256(metadata),
        }
        (self.bundle / "fonts" / "source.json").write_text(
            json.dumps(source, indent=2) + "\n", encoding="utf-8"
        )
        self._refresh_assets()
        manifest = validate_production_bundle(self.bundle)
        _validate_schema(manifest, "bundle-v1.schema.json")
        self.assertIn(
            "fonts/source.json", {asset["path"] for asset in manifest["assets"]}
        )

        source["font_filename"] = "Other.ttf"
        (self.bundle / "fonts" / "source.json").write_text(
            json.dumps(source, indent=2) + "\n", encoding="utf-8"
        )
        self._refresh_assets()
        with self.assertRaisesRegex(BundleError, "selected Google Fonts OFL asset"):
            validate_production_bundle(self.bundle)

    def test_catalog_cli_validates_bundle(self) -> None:
        self.assertEqual(
            catalog_cli_main(["validate", "--bundle", str(self.bundle)]), 0
        )

    def test_bundle_cli_rejects_invalid_revision_before_build(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            bundle_cli_main(
                [
                    "--selection",
                    str(self.exchange / "missing-selection.json"),
                    "--qa-input-pet",
                    str(self.bundle / "qa" / "input-pet.png"),
                    "--bundle-revision",
                    "zero",
                    "--output-dir",
                    str(self.exchange / "other-bundles"),
                ]
            )
        self.assertEqual(raised.exception.code, 2)

    def test_rejects_changed_asset(self) -> None:
        (self.bundle / "art.png").write_bytes(b"changed")
        with self.assertRaisesRegex(BundleError, "metadata mismatch"):
            validate_production_bundle(self.bundle)

    def test_rejects_non_openai_mvp_runtime(self) -> None:
        manifest_path = self.bundle / "bundle.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runtime"].update(
            provider="gemini",
            model="gemini-3.1-flash-image",
            transport="interactions",
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(BundleError, "provider and transport"):
            validate_production_bundle(self.bundle)
        with self.assertRaisesRegex(ValidationError, "openai"):
            _validate_schema(manifest, "bundle-v1.schema.json")

    def test_rejects_uninventoried_asset(self) -> None:
        (self.bundle / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(BundleError, "inventory every file"):
            validate_production_bundle(self.bundle)

    def test_rejects_runtime_reference_order_drift(self) -> None:
        manifest_path = self.bundle / "bundle.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runtime"]["input_image_order"] = ["reference_1", "user_pet"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(BundleError, "user_pet first"):
            validate_production_bundle(self.bundle)

    def test_rejects_more_than_four_runtime_references(self) -> None:
        manifest_path = self.bundle / "bundle.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runtime"]["reference_assets"] = [
            "reference-design.png",
            "reference-designs/reference-design-0002.png",
            "reference-designs/reference-design-0003.png",
            "reference-designs/reference-design-0004.png",
            "reference-designs/reference-design-0005.png",
        ]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(BundleError, "one to four"):
            validate_production_bundle(self.bundle)

    def test_rejects_print_layout_geometry_drift(self) -> None:
        path = self.bundle / "layout-print.json"
        layout = json.loads(path.read_text(encoding="utf-8"))
        layout["pet"]["box"]["x"] += 1
        path.write_text(json.dumps(layout), encoding="utf-8")
        self._refresh_assets()
        with self.assertRaisesRegex(BundleError, "pet.box"):
            validate_production_bundle(self.bundle)

    def test_rejects_print_layout_typography_scale_drift(self) -> None:
        path = self.bundle / "layout-print.json"
        layout = json.loads(path.read_text(encoding="utf-8"))
        layout["name"]["font_size_px"] += 1
        path.write_text(json.dumps(layout), encoding="utf-8")
        self._refresh_assets()
        with self.assertRaisesRegex(BundleError, "name.font_size_px"):
            validate_production_bundle(self.bundle)

    def test_rejects_noncanonical_font_path(self) -> None:
        for name in ("layout.json", "layout-print.json"):
            path = self.bundle / name
            layout = json.loads(path.read_text(encoding="utf-8"))
            layout["name"]["font"] = "selected.ttf"
            path.write_text(json.dumps(layout), encoding="utf-8")
        (self.bundle / "selected.ttf").write_bytes(
            (self.bundle / "fonts" / "TestFont.ttf").read_bytes()
        )
        (self.bundle / "OFL.txt").write_bytes(
            (self.bundle / "fonts" / "OFL.txt").read_bytes()
        )
        (self.bundle / "fonts" / "TestFont.ttf").unlink()
        (self.bundle / "fonts" / "OFL.txt").unlink()
        (self.bundle / "fonts").rmdir()
        self._refresh_assets()
        with self.assertRaisesRegex(BundleError, "fonts/<filename>"):
            validate_production_bundle(self.bundle)

    def test_release_binds_exact_bundle_manifest(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        catalog = validate_release(catalog_path, exchange_root=self.exchange)
        _validate_schema(catalog, "release-catalog-v2.schema.json")
        self.assertEqual(
            catalog["templates"][0]["manifest_sha256"],
            _sha256(self.bundle / "bundle.json"),
        )
        self.assertEqual(
            catalog["templates"][0]["manifest_path"],
            "bundles/life-is-good--test-blanket/v000001/bundle.json",
        )

    def test_s3_publication_plan_uploads_assets_then_manifests_then_catalog(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        plan = build_s3_publication_plan(
            release_catalog=catalog_path,
            exchange_root=self.exchange,
            prefix="pawmarvel/mvp",
        )
        keys = [item["key"] for item in plan]
        bundle_manifest = (
            "pawmarvel/mvp/bundles/life-is-good--test-blanket/"
            "v000001/bundle.json"
        )
        self.assertEqual(
            keys[-1], "pawmarvel/mvp/releases/2026-09-07.001/catalog.json"
        )
        self.assertIn(bundle_manifest, keys)
        self.assertGreater(keys.index(bundle_manifest), keys.index(
            "pawmarvel/mvp/bundles/life-is-good--test-blanket/v000001/art.png"
        ))

    def test_s3_publication_is_dry_run_unless_execute_is_explicit(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        result = publish_s3(
            release_catalog=catalog_path,
            exchange_root=self.exchange,
            bucket="pawmarvel-template-catalog",
            prefix="mvp",
            aws_profile=None,
            region=None,
            authoring_root=None,
            execute=False,
        )
        self.assertEqual(
            result,
            "s3://pawmarvel-template-catalog/mvp/releases/"
            "2026-09-07.001/catalog.json",
        )

    def test_s3_publication_executes_conditional_puts_and_verifies_each_object(
        self,
    ) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        plan = build_s3_publication_plan(
            release_catalog=catalog_path,
            exchange_root=self.exchange,
            prefix="mvp",
        )
        by_key = {item["key"]: item for item in plan}

        def fake_aws(arguments, *, aws_profile, region):
            self.assertEqual(aws_profile, "publisher")
            self.assertEqual(region, "us-west-2")
            key = arguments[arguments.index("--key") + 1]
            if "put-object" in arguments:
                self.assertIn("--if-none-match", arguments)
                self.assertEqual(arguments[arguments.index("--if-none-match") + 1], "*")
                if key == plan[0]["key"]:
                    return subprocess.CompletedProcess(
                        arguments, 1, "", "PreconditionFailed (412)"
                    )
                return subprocess.CompletedProcess(arguments, 0, "{}", "")
            item = by_key[key]
            checksum = base64.b64encode(bytes.fromhex(item["sha256"])).decode("ascii")
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps(
                    {
                        "ContentLength": item["bytes"],
                        "ChecksumSHA256": checksum,
                        "ContentType": item["media_type"],
                    }
                ),
                "",
            )

        with (
            patch(
                "pawmarvel_generator.s3_publisher._aws_cli", side_effect=fake_aws
            ) as aws_cli,
            patch(
                "pawmarvel_generator.s3_publisher._record_publication_receipts"
            ) as record_receipts,
        ):
            result = publish_s3(
                release_catalog=catalog_path,
                exchange_root=self.exchange,
                bucket="pawmarvel-template-catalog",
                prefix="mvp",
                aws_profile="publisher",
                region="us-west-2",
                authoring_root=self.exchange / "authoring",
                execute=True,
            )
        self.assertEqual(aws_cli.call_count, len(plan) * 2)
        put_calls = [
            call.args[0]
            for call in aws_cli.call_args_list
            if "put-object" in call.args[0]
        ]
        self.assertEqual(
            put_calls[-1][put_calls[-1].index("--key") + 1],
            "mvp/releases/2026-09-07.001/catalog.json",
        )
        self.assertEqual(
            result,
            "s3://pawmarvel-template-catalog/mvp/releases/"
            "2026-09-07.001/catalog.json",
        )
        record_receipts.assert_called_once_with(
            release_catalog=catalog_path,
            exchange_root=self.exchange,
            authoring_root=self.exchange / "authoring",
            transfer_location=result,
        )

    def test_s3_execute_requires_authoring_root_before_upload(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        with self.assertRaisesRegex(BundleError, "authoring-root"):
            publish_s3(
                release_catalog=catalog_path,
                exchange_root=self.exchange,
                bucket="pawmarvel-template-catalog",
                prefix="mvp",
                aws_profile=None,
                region=None,
                authoring_root=None,
                execute=True,
            )

    def test_s3_publication_stops_on_put_failure(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        failed = subprocess.CompletedProcess(
            ["aws"], 1, "", "AccessDenied request-id=abc412def bytes=4096"
        )
        with patch(
            "pawmarvel_generator.s3_publisher._aws_cli", return_value=failed
        ) as aws_cli, self.assertRaisesRegex(BundleError, "put-object failed"):
            publish_s3(
                release_catalog=catalog_path,
                exchange_root=self.exchange,
                bucket="pawmarvel-template-catalog",
                prefix="mvp",
                aws_profile=None,
                region=None,
                authoring_root=self.exchange / "authoring",
                execute=True,
            )
        self.assertEqual(aws_cli.call_count, 1)

    def test_s3_publication_stops_on_verify_mismatch_without_receipt(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )

        def fake_aws(arguments, *, aws_profile, region):
            if "put-object" in arguments:
                return subprocess.CompletedProcess(arguments, 0, "{}", "")
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps(
                    {
                        "ContentLength": 0,
                        "ChecksumSHA256": "wrong",
                        "ContentType": "application/octet-stream",
                    }
                ),
                "",
            )

        with (
            patch(
                "pawmarvel_generator.s3_publisher._aws_cli", side_effect=fake_aws
            ),
            patch(
                "pawmarvel_generator.s3_publisher._record_publication_receipts"
            ) as receipts,
            self.assertRaisesRegex(BundleError, "differs from local artifact"),
        ):
            publish_s3(
                release_catalog=catalog_path,
                exchange_root=self.exchange,
                bucket="pawmarvel-template-catalog",
                prefix="mvp",
                aws_profile=None,
                region=None,
                authoring_root=self.exchange / "authoring",
                execute=True,
            )
        receipts.assert_not_called()

    def test_s3_publication_rejects_unsafe_prefix(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        with self.assertRaisesRegex(BundleError, "prefix"):
            build_s3_publication_plan(
                release_catalog=catalog_path,
                exchange_root=self.exchange,
                prefix="mvp/../production",
            )

    def test_s3_publication_rejects_surrounding_prefix_slash(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        for prefix in ("/mvp", "mvp/"):
            with self.subTest(prefix=prefix), self.assertRaisesRegex(
                BundleError, "leading or trailing slash"
            ):
                build_s3_publication_plan(
                    release_catalog=catalog_path,
                    exchange_root=self.exchange,
                    prefix=prefix,
                )

    def test_rejects_invalid_bundle_timestamp(self) -> None:
        manifest_path = self.bundle / "bundle.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["created_at"] = "not-a-time"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(BundleError, "UTC timestamp"):
            validate_production_bundle(self.bundle)

    def test_rejects_invalid_release_id_before_writing(self) -> None:
        with self.assertRaisesRegex(BundleError, "YYYY-MM-DD.NNN"):
            build_release(
                release_id="latest",
                bundles=[self.bundle],
                exchange_root=self.exchange,
                asset_base_url=None,
            )

    def test_release_catalog_creation_is_exclusive(self) -> None:
        build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        with self.assertRaisesRegex(BundleError, "already exists"):
            build_release(
                release_id="2026-09-07.001",
                bundles=[self.bundle],
                exchange_root=self.exchange,
                asset_base_url=None,
            )

    def test_missing_release_catalog_reports_absolute_path_and_build_hint(self) -> None:
        missing = self.exchange / "releases" / "2026-09-13.001" / "catalog.json"
        with self.assertRaises(BundleError) as raised:
            validate_release(missing, exchange_root=self.exchange)

        message = str(raised.exception)
        self.assertIn(f"release catalog does not exist: {missing.resolve()}", message)
        self.assertIn("pawmarvel-catalog build-release", message)
        self.assertIn("PAWMARVEL_RELEASE_ID", message)

    def test_malformed_release_catalog_reports_location(self) -> None:
        malformed = self.exchange / "releases" / "2026-09-13.001" / "catalog.json"
        malformed.parent.mkdir(parents=True)
        malformed.write_text('{"schema_version": 2,\n', encoding="utf-8")
        with self.assertRaises(BundleError) as raised:
            validate_release(malformed, exchange_root=self.exchange)

        message = str(raised.exception)
        self.assertIn(f"invalid JSON: {malformed.resolve()}", message)
        self.assertIn("line=2", message)
        self.assertIn("column=1", message)

    def test_rejects_non_https_release_asset_base(self) -> None:
        with self.assertRaisesRegex(BundleError, "HTTPS URL"):
            build_release(
                release_id="2026-09-07.001",
                bundles=[self.bundle],
                exchange_root=self.exchange,
                asset_base_url="http://assets.example.test/bundles",
            )

    def test_release_asset_url_uses_exchange_root(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url="https://assets.example.test/pawmarvel-exchange",
        )
        catalog = validate_release(catalog_path, exchange_root=self.exchange)
        self.assertEqual(
            catalog["templates"][0]["manifest_url"],
            "https://assets.example.test/pawmarvel-exchange/"
            "bundles/life-is-good--test-blanket/v000001/bundle.json",
        )

    def test_release_validation_checks_referenced_bundle_assets(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        (self.bundle / "art.png").write_bytes(b"changed")
        with self.assertRaisesRegex(BundleError, "metadata mismatch"):
            validate_release(catalog_path, exchange_root=self.exchange)

    def test_release_rejects_bundle_outside_canonical_exchange_path(self) -> None:
        misplaced = (
            self.exchange
            / "misplaced"
            / self.bundle.parent.name
            / self.bundle.name
        )
        misplaced.parent.parent.mkdir()
        self.bundle.parent.rename(misplaced.parent)
        with self.assertRaisesRegex(BundleError, "canonical exchange path"):
            build_release(
                release_id="2026-09-07.001",
                bundles=[misplaced],
                exchange_root=self.exchange,
                asset_base_url=None,
            )

    def test_rejects_https_asset_base_without_host(self) -> None:
        with self.assertRaisesRegex(BundleError, "without credentials"):
            build_release(
                release_id="2026-09-07.001",
                bundles=[self.bundle],
                exchange_root=self.exchange,
                asset_base_url="https:///bundles",
            )

    def test_rejects_invalid_product_profile_identity(self) -> None:
        with self.assertRaisesRegex(BundleError, "product profile id"):
            catalog_template_id("life-is-good", "bad--profile")

    def test_release_validation_rejects_malformed_identity_cleanly(self) -> None:
        catalog_path = build_release(
            release_id="2026-09-07.001",
            bundles=[self.bundle],
            exchange_root=self.exchange,
            asset_base_url=None,
        )
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["templates"][0]["bundle_revision"] = []
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaisesRegex(BundleError, "invalid identity"):
            validate_release(catalog_path)


if __name__ == "__main__":
    unittest.main()
