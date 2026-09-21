from __future__ import annotations

import json
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from jsonschema import Draft202012Validator, FormatChecker

from helpers import copy_font, layout_data, make_image, make_transparent_mark
from pawmarvel_generator.artifact_io import sha256
from pawmarvel_generator.authoring import (
    AuthoringError, atomic_json, benchmark, cleanup, compare, create_experiment,
    graduate, prepare_print_candidate, record_decision, record_publication,
    run_attempt, set_status, trace_graduation,
)
from pawmarvel_generator.fixture_set import write_fixture_selection
from pawmarvel_generator.bundle import BundleError
from pawmarvel_generator.font_reference import font_reference_from_editor
from pawmarvel_generator.layout_reference import layout_reference_from_editor
from pawmarvel_generator.image_size import ImageSize
from pawmarvel_generator.production_bundle import build_from_selection, validate_production_bundle
from pawmarvel_generator.product_profile import create_product_profile, write_product_profile
from pawmarvel_generator.release_catalog import build_release
from pawmarvel_generator.s3_publisher import _record_publication_receipts


class AuthoringLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.authoring = self.root / "authoring"
        self.profile = write_product_profile(
            self.root / "product-profile.json",
            create_product_profile(profile_id="test-blanket", print_size=ImageSize(1344, 2016)),
        )
        self.reference = make_image(self.root / "reference.png")
        self.pet = make_image(self.root / "pet.png")
        self.art_prompt = self.root / "art-template-gpt-v02.md"
        self.art_prompt.write_text("art", encoding="utf-8")
        self.pet_prompt = self.root / "pet-transform-gpt.md"
        self.pet_prompt.write_text("pet", encoding="utf-8")
        self.protocol = self.root / "protocol.json"
        atomic_json(self.protocol, {"schema_version": 1, "evaluation_protocol_id": "mvp-v1"})

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _validate_schema(instance_path: Path, schema_name: str) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / schema_name
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        instance = json.loads(instance_path.read_text(encoding="utf-8"))
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).validate(instance)

    def _experiment(
        self,
        kind: str,
        name: str,
        prompt: Path,
        *,
        pet_name: str | None = None,
    ) -> Path:
        return create_experiment(kind=kind, experiment_id=name, design_id="life-is-good",
            product_profile=self.profile, authoring_root=self.authoring,
            references=[self.reference], prompt_file=prompt, provider="openai", model="gpt-image-2",
            quality="high",
            art_attempt=None, pet_attempt=None, font_catalogs=[],
            parent_experiment_id=None, base_bundle_revision=None, created_by="test",
            pet_name=pet_name)

    def _fake_attempt(
        self,
        experiment: Path,
        attempt_id: str,
        filename: str,
        size: tuple[int, int],
        pet_source: Path | None = None,
    ) -> Path:
        attempt = experiment / "attempts" / attempt_id
        output_color = (0, 120, 255, 255) if pet_source is not None else (0, 255, 0, 255)
        make_transparent_mark(
            attempt / "outputs" / filename,
            size=size,
            color=output_color,
        )
        experiment_record = json.loads((experiment / "experiment.json").read_text())
        record = {"schema_version": 1, "attempt_id": attempt_id,
            "experiment_id": experiment.name,
            "kind": experiment_record["kind"],
            "status": "succeeded", "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:00:01Z", "duration_seconds": 1.0,
            "resolved_generation": experiment_record.get("generation"), "outputs": []}
        if filename == "transformed-pet.png":
            pet_source = pet_source or self.pet
            pet_copy = attempt / "inputs" / "input-pet.png"
            pet_copy.parent.mkdir()
            pet_copy.write_bytes(pet_source.read_bytes())
            record["input_pet_sha256"] = sha256(pet_source)
            prompt_variables = (experiment_record.get("generation") or {}).get(
                "prompt_variables"
            )
            if prompt_variables:
                record["prompt_variables"] = prompt_variables
        atomic_json(attempt / "run.json", record)
        return attempt

    def test_layout_attempt_comparison_and_reviewed_selection(self) -> None:
        art_exp = self._experiment("art", "art-gpt-v01", self.art_prompt)
        losing_art_exp = self._experiment("art", "art-gpt-v02", self.art_prompt)
        pet_exp = self._experiment("pet", "pet-gpt-v01", self.pet_prompt)
        pet_exp_v02 = self._experiment("pet", "pet-gpt-v02", self.pet_prompt)
        art_attempt = self._fake_attempt(art_exp, "attempt-0001", "art.png", (672, 1008))
        self._fake_attempt(losing_art_exp, "attempt-0001", "art.png", (100, 100))
        pet_attempt = self._fake_attempt(pet_exp, "attempt-0001", "transformed-pet.png", (816, 816))
        second_pet_source = make_image(
            self.root / "second-pet.png", color=(80, 120, 200, 255)
        )
        pet_attempt_v02 = self._fake_attempt(
            pet_exp_v02,
            "attempt-0001",
            "transformed-pet.png",
            (816, 816),
            pet_source=second_pet_source,
        )

        layout_source = self.root / "layout-source"
        make_transparent_mark(layout_source / "art.png", size=(672, 1008))
        (layout_source / "fonts").mkdir()
        copy_font(layout_source / "fonts")
        (layout_source / "layout.json").write_text(json.dumps(layout_data()), encoding="utf-8")
        layout_exp = create_experiment(kind="layout", experiment_id="layout-v01", design_id="life-is-good",
            product_profile=self.profile, authoring_root=self.authoring, references=[], prompt_file=None,
            provider=None, model=None, quality="high", art_attempt=art_attempt, pet_attempt=pet_attempt,
            font_catalogs=[], parent_experiment_id=None, base_bundle_revision=None, created_by="test")
        layout_attempt = run_attempt(experiment=layout_exp, attempt_id="attempt-0001", pet_image=None,
                                     pet_name="SAUSAGE", layout_file=layout_source / "layout.json")
        layout_exp_v02 = create_experiment(
            kind="layout",
            experiment_id="layout-v02",
            design_id="life-is-good",
            product_profile=self.profile,
            authoring_root=self.authoring,
            references=[],
            prompt_file=None,
            provider=None,
            model=None,
            quality="high",
            art_attempt=art_attempt,
            pet_attempt=pet_attempt_v02,
            font_catalogs=[],
            parent_experiment_id="layout-v01",
            base_bundle_revision=None,
            created_by="test",
        )
        run_attempt(
            experiment=layout_exp_v02,
            attempt_id="attempt-0001",
            pet_image=None,
            pet_name="MARSHMALLOW",
            layout_file=layout_source / "layout.json",
        )
        saved_layout = json.loads((layout_attempt / "outputs" / "layout.json").read_text())
        self.assertEqual(saved_layout["schema_version"], 2)
        self.assertNotIn("model", saved_layout)
        fixture = json.loads(
            (layout_attempt / "outputs" / "qa" / "calibration-fixture.json").read_text()
        )
        run_record = json.loads((layout_attempt / "run.json").read_text())
        self.assertEqual(fixture["pet_name"], "SAUSAGE")
        self.assertEqual(
            run_record["layout_fixture"]["applied_font_size_px"],
            fixture["applied_font_size_px"],
        )
        self.assertEqual(run_record["layout_fixture"]["text_fit"], fixture["text_fit"])

        product = self.authoring / "life-is-good" / "test-blanket"
        self.assertEqual(
            json.loads((product / "product.json").read_text())["product_profile_id"],
            "test-blanket",
        )
        art_evaluation = compare(kind="art", review_id="art-eval", authoring_product=product,
            experiments=[art_exp.name, losing_art_exp.name], evaluation_protocol=self.protocol, fixture_set=None,
            art_attempt=None, pet_experiment=None, layout_attempt=None, base_bundle_revision=None)
        art_evaluation_record = json.loads(art_evaluation.read_text())
        self.assertEqual(
            art_evaluation,
            product.resolve() / "reviews" / "art" / "art-eval" / "evaluation.json",
        )
        self.assertEqual(art_evaluation_record["design_id"], "life-is-good")
        self.assertEqual(art_evaluation_record["product_profile_id"], "test-blanket")
        self.assertEqual(
            [artifact["kind"] for artifact in art_evaluation_record["review_artifacts"]],
            ["art-contact-sheet"],
        )
        contact_sheet = product / art_evaluation_record["review_artifacts"][0]["path"]
        self.assertTrue(contact_sheet.is_file())
        self.assertEqual(
            len(art_evaluation_record["review_artifacts"][0]["candidates"]),
            2,
        )
        with Image.open(contact_sheet) as comparison_image:
            self.assertEqual(comparison_image.size, (720, 430))
        pet_evaluation = compare(kind="pet", review_id="pet-eval", authoring_product=product,
            experiments=[pet_exp.name, pet_exp_v02.name], evaluation_protocol=self.protocol, fixture_set=None,
            art_attempt=None, pet_experiment=None, layout_attempt=None, base_bundle_revision=None)
        pet_evaluation_record = json.loads(pet_evaluation.read_text())
        self.assertEqual(
            pet_evaluation_record["review_artifacts"][0]["kind"],
            "pet-contact-sheet",
        )
        pet_contact_sheet = product / pet_evaluation_record["review_artifacts"][0]["path"]
        self.assertTrue(pet_contact_sheet.is_file())
        self.assertEqual(
            len(pet_evaluation_record["review_artifacts"][0]["candidates"]),
            2,
        )
        layout_evaluation = compare(kind="layout", review_id="layout-eval", authoring_product=product,
            experiments=[layout_exp.name, layout_exp_v02.name], evaluation_protocol=self.protocol, fixture_set=None,
            art_attempt=None, pet_experiment=None, layout_attempt=None, base_bundle_revision=None)
        layout_evaluation_record = json.loads(layout_evaluation.read_text())
        self.assertEqual(
            layout_evaluation_record["review_artifacts"][0]["kind"],
            "layout-contact-sheet",
        )
        self.assertEqual(
            [
                candidate["attempts"][0]["pet_name"]
                for candidate in layout_evaluation_record["candidates"]
            ],
            ["SAUSAGE", "MARSHMALLOW"],
        )
        self.assertNotEqual(
            layout_evaluation_record["candidates"][0]["attempts"][0][
                "representative_pet_sha256"
            ],
            layout_evaluation_record["candidates"][1]["attempts"][0][
                "representative_pet_sha256"
            ],
        )

        composed_pet_evaluation = compare(
            kind="pet",
            review_id="pet-composed-eval",
            authoring_product=product,
            experiments=[pet_exp.name, pet_exp_v02.name],
            evaluation_protocol=self.protocol,
            fixture_set=None,
            art_attempt=art_attempt,
            pet_experiment=None,
            layout_attempt=layout_attempt,
            base_bundle_revision=None,
        )
        composed_pet_record = json.loads(composed_pet_evaluation.read_text())
        self.assertEqual(composed_pet_record["review_mode"], "composed-preview")
        self.assertEqual(
            composed_pet_record["review_artifacts"][0]["kind"],
            "pet-composition-contact-sheet",
        )
        for candidate in composed_pet_record["review_artifacts"][0]["candidates"]:
            preview = product / candidate["review_path"]
            self.assertTrue(preview.is_file())
            self.assertEqual(candidate["review_sha256"], sha256(preview))
        evaluation = compare(kind="assembly", review_id="assembly-eval", authoring_product=product,
            experiments=[], evaluation_protocol=self.protocol, fixture_set=None, art_attempt=art_attempt,
            pet_experiment=pet_exp, layout_attempt=layout_attempt, base_bundle_revision=None)
        assembly_record = json.loads(evaluation.read_text())
        self.assertEqual(assembly_record["candidates"][0]["pet_name"], "SAUSAGE")
        self.assertEqual(
            (product / "reviews" / "assembly" / "assembly-eval" / "artifacts" / "preview.png").read_bytes(),
            (layout_attempt / "outputs" / "preview.png").read_bytes(),
        )
        art_decision = record_decision(
            review=art_evaluation.parent, selected_by="owner", notes="best fixed art",
            selected_experiment=art_exp.name, selected_attempt=art_attempt.name,
        )
        pet_decision = record_decision(
            review=pet_evaluation.parent, selected_by="owner", notes="best runtime",
            selected_experiment=pet_exp.name, selected_attempt=None,
        )
        layout_decision = record_decision(
            review=layout_evaluation.parent, selected_by="owner", notes="best composition",
            selected_experiment=layout_exp.name, selected_attempt=layout_attempt.name,
        )
        assembly_decision = record_decision(
            review=evaluation.parent, selected_by="owner", notes="compatible",
            selected_experiment=None, selected_attempt=None,
        )
        for decision in (
            art_decision,
            pet_decision,
            layout_decision,
            assembly_decision,
        ):
            self._validate_schema(
                decision,
                "evaluation-decision-v1.schema.json",
            )
        print_candidate = prepare_print_candidate(
            candidate_id="print-finalist-0001", authoring_product=product,
            art_attempt=art_attempt, pet_attempt=pet_attempt,
            layout_attempt=layout_attempt, pet_name="SAUSAGE",
            backend="deterministic",
        )
        self._validate_schema(
            print_candidate / "print-candidate.json",
            "print-candidate-v1.schema.json",
        )
        inferred_print_candidate = prepare_print_candidate(
            candidate_id="print-finalist-from-decisions",
            authoring_product=product,
            art_attempt=None,
            pet_attempt=None,
            layout_attempt=None,
            art_review=art_decision.parent,
            pet_review=pet_decision.parent,
            layout_review=layout_decision.parent,
            pet_name=None,
            backend="deterministic",
        )
        inferred_print_record = json.loads(
            (inferred_print_candidate / "print-candidate.json").read_text()
        )
        explicit_print_record = json.loads(
            (print_candidate / "print-candidate.json").read_text()
        )
        self.assertEqual(inferred_print_record["sources"], explicit_print_record["sources"])
        self.assertEqual(inferred_print_record["pet_name"], "SAUSAGE")
        reused_print_candidate = prepare_print_candidate(
            candidate_id="print-finalist-0002", authoring_product=product,
            art_attempt=art_attempt, pet_attempt=pet_attempt,
            layout_attempt=layout_attempt, pet_name="SAUSAGE",
            backend="deterministic", reuse_template_from=print_candidate,
        )
        reused_print_record = json.loads(
            (reused_print_candidate / "print-candidate.json").read_text()
        )
        self.assertEqual(
            reused_print_record["template_source"]["mode"],
            "reused-print-candidate",
        )
        self.assertEqual(
            (print_candidate / "outputs" / "art-print.png").read_bytes(),
            (reused_print_candidate / "outputs" / "art-print.png").read_bytes(),
        )
        with self.assertRaisesRegex(AuthoringError, "art review scope"):
            graduate(
                graduation_id="invalid-selection",
                print_candidate=print_candidate,
                art_review=pet_decision.parent,
                pet_review=pet_decision.parent,
                layout_review=layout_decision.parent,
                assembly_review=assembly_decision.parent,
                selected_by="owner",
                notes="invalid",
                authoring_root=self.authoring,
            )
        pet_metadata_path = pet_exp / "experiment.json"
        pet_metadata = json.loads(pet_metadata_path.read_text(encoding="utf-8"))
        gemini_metadata = dict(pet_metadata)
        gemini_metadata["generation"] = dict(pet_metadata["generation"])
        gemini_metadata["generation"].update(
            provider="gemini", model="gemini-3.1-flash-image"
        )
        atomic_json(pet_metadata_path, gemini_metadata)
        with self.assertRaisesRegex(
            AuthoringError, "MVP production graduation requires an OpenAI pet runtime"
        ):
            graduate(
                graduation_id="gemini-selection",
                art_review=art_decision.parent,
                pet_review=pet_decision.parent,
                layout_review=layout_decision.parent,
                assembly_review=assembly_decision.parent,
                print_candidate=print_candidate,
                selected_by="owner",
                notes="must be rejected",
                authoring_root=self.authoring,
            )
        atomic_json(pet_metadata_path, pet_metadata)
        selection = graduate(graduation_id="life-is-good-selection",
            art_review=art_decision.parent, pet_review=pet_decision.parent,
            layout_review=layout_decision.parent,
            assembly_review=assembly_decision.parent,
            print_candidate=print_candidate, selected_by="owner", notes="winner",
            authoring_root=self.authoring)
        self._validate_schema(selection, "selection-v1.schema.json")
        selected = json.loads(selection.read_text())
        self.assertEqual(
            selection,
            product.resolve() / "graduations" / "life-is-good-selection" / "selection.json",
        )
        self.assertEqual(selected["graduation_id"], "life-is-good-selection")
        self.assertTrue(
            all(not Path(value).is_absolute() for value in selected["sources"].values())
        )
        self.assertEqual(selected["compatibility_qa"]["status"], "passed")
        self.assertEqual(selected["selected"]["pet_runtime"]["experiment_id"], "pet-gpt-v01")
        with self.assertRaisesRegex(AuthoringError, "selected experiment status"):
            set_status(pet_exp, "discarded")

        bundle = build_from_selection(
            selection_path=selection,
            output_dir=self.root / "exchange" / "bundles",
            bundle_revision="next",
            pet_name_max_length=12,
            qa_input_pet=self.pet,
        )
        manifest = validate_production_bundle(bundle)
        self.assertEqual(manifest["bundle_revision"], 1)
        self.assertEqual(
            manifest["provenance"]["print_candidate"]["print_candidate_id"],
            "print-finalist-0001",
        )
        self.assertEqual(manifest["runtime"]["input_image_order"], ["user_pet", "reference_1"])
        self.assertEqual(manifest["runtime"]["request_parameters"]["quality"], "high")
        self.assertEqual(manifest["prompts"]["art_template"], "art-template-gpt.md")
        self.assertTrue((bundle / "art-template-gpt.md").is_file())
        self.assertFalse((bundle / "art-template-gpt-v02.md").exists())
        self.assertEqual(manifest["provenance"]["qa_fixture"]["input_pet"], "qa/input-pet.png")
        mismatched_pet = make_image(
            self.root / "different-pet.png", color=(10, 20, 30, 255)
        )
        with self.assertRaisesRegex(BundleError, "qa-input-pet"):
            build_from_selection(
                selection_path=selection,
                output_dir=self.root / "other-exchange" / "bundles",
                bundle_revision="next",
                pet_name_max_length=12,
                qa_input_pet=mismatched_pet,
            )
        second_selection = graduate(graduation_id="life-is-good-selection-2",
            art_review=art_decision.parent, pet_review=pet_decision.parent,
            layout_review=layout_decision.parent,
            assembly_review=assembly_decision.parent,
            print_candidate=reused_print_candidate, selected_by="owner", notes="winner",
            authoring_root=self.authoring)
        second_bundle = build_from_selection(selection_path=second_selection,
            output_dir=self.root / "exchange" / "bundles", bundle_revision="next",
            pet_name_max_length=12, qa_input_pet=self.pet)
        second_manifest = validate_production_bundle(second_bundle)
        self.assertEqual(second_manifest["bundle_revision"], 2)
        self.assertEqual(
            second_manifest["provenance"]["print_derivation"]["mode"],
            "selected-print-candidate",
        )
        self.assertEqual(
            second_manifest["provenance"]["print_derivation"]["template_source"]["mode"],
            "reused-print-candidate",
        )
        self.assertEqual(second_manifest["provenance"]["qa_fixture"]["pet_name"], "SAUSAGE")
        self.assertNotIn(
            "path",
            second_manifest["provenance"]["print_derivation"]["template_source"],
        )
        catalog = build_release(
            release_id="2026-09-07.001",
            bundles=[bundle],
            exchange_root=self.root / "exchange",
            asset_base_url=None,
        )
        receipts = _record_publication_receipts(
            release_catalog=catalog,
            exchange_root=self.root / "exchange",
            authoring_root=self.authoring,
            transfer_location="s3://example/release",
        )
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        self.assertTrue(receipt.is_file())
        self._validate_schema(receipt, "publication-receipt-v1.schema.json")
        self.assertEqual(
            record_publication(
                selection=selection,
                bundle_manifest=bundle / "bundle.json",
                release_catalog=catalog,
                transfer_location="s3://example/release",
                authoring_root=self.authoring,
            ),
            receipt,
        )
        second_catalog = build_release(
            release_id="2026-09-08.001",
            bundles=[bundle],
            exchange_root=self.root / "exchange",
            asset_base_url=None,
        )
        second_receipt = record_publication(
            selection=selection,
            bundle_manifest=bundle / "bundle.json",
            release_catalog=second_catalog,
            transfer_location="s3://example/second-release",
            authoring_root=self.authoring,
        )
        self.assertNotEqual(second_receipt, receipt)
        self.assertTrue(second_receipt.is_file())
        template_root = bundle.parent
        shutil.rmtree(template_root)
        rebuilt_after_cleanup = build_from_selection(
            selection_path=second_selection,
            output_dir=self.root / "exchange" / "bundles",
            bundle_revision="next",
            pet_name_max_length=12,
            qa_input_pet=self.pet,
        )
        self.assertEqual(
            validate_production_bundle(rebuilt_after_cleanup)["bundle_revision"], 2
        )
        trace = json.loads(trace_graduation(selection.parent))
        self.assertEqual(trace["status"], "valid")
        self.assertEqual(set(trace["reviews"]), {"art", "pet", "layout", "assembly"})

    def test_layout_attempt_can_embed_name_in_transformed_pet(self) -> None:
        art_exp = self._experiment("art", "art-gpt-v01", self.art_prompt)
        self.pet_prompt.write_text(
            "Render {{PET_NAME}} with the transformed pet.", encoding="utf-8"
        )
        pet_exp = self._experiment(
            "pet", "pet-gpt-v01", self.pet_prompt, pet_name="COOPER"
        )
        art_attempt = self._fake_attempt(
            art_exp, "attempt-0001", "art.png", (672, 1008)
        )
        pet_attempt = self._fake_attempt(
            pet_exp, "attempt-0001", "transformed-pet.png", (816, 816)
        )
        layout_source = self.root / "embedded-name-layout"
        make_transparent_mark(layout_source / "art.png", size=(672, 1008))
        layout = layout_data()
        del layout["name"]
        (layout_source / "layout.json").write_text(
            json.dumps(layout), encoding="utf-8"
        )
        layout_exp = create_experiment(
            kind="layout",
            experiment_id="layout-embedded-name-v01",
            design_id="life-is-good",
            product_profile=self.profile,
            authoring_root=self.authoring,
            references=[],
            prompt_file=None,
            provider=None,
            model=None,
            quality="high",
            art_attempt=art_attempt,
            pet_attempt=pet_attempt,
            font_catalogs=[],
            parent_experiment_id=None,
            base_bundle_revision=None,
            created_by="test",
        )

        attempt = run_attempt(
            experiment=layout_exp,
            attempt_id="attempt-0001",
            pet_image=None,
            no_pet_name=True,
            layout_file=layout_source / "layout.json",
        )

        saved = json.loads((attempt / "outputs" / "layout.json").read_text())
        fixture = json.loads(
            (attempt / "outputs" / "qa" / "calibration-fixture.json").read_text()
        )
        record = json.loads((attempt / "run.json").read_text())
        self.assertNotIn("name", saved)
        self.assertEqual(fixture["name_mode"], "embedded-in-pet")
        self.assertIsNone(fixture["pet_name"])
        self.assertEqual(record["layout_fixture"]["name_mode"], "embedded-in-pet")
        self.assertFalse((attempt / "outputs" / "fonts").exists())

        product = self.authoring / "life-is-good" / "test-blanket"
        pet_evaluation = compare(
            kind="pet",
            review_id="pet-embedded-eval",
            authoring_product=product,
            experiments=[pet_exp.name],
            evaluation_protocol=self.protocol,
            fixture_set=None,
            art_attempt=art_attempt,
            pet_experiment=None,
            layout_attempt=attempt,
            base_bundle_revision=None,
        )
        pet_evaluation_record = json.loads(pet_evaluation.read_text())
        self.assertEqual(
            pet_evaluation_record["review_artifacts"][0]["kind"],
            "pet-composition-contact-sheet",
        )

        print_candidate = prepare_print_candidate(
            candidate_id="print-embedded-name-v01",
            authoring_product=product,
            art_attempt=art_attempt,
            pet_attempt=pet_attempt,
            layout_attempt=attempt,
            pet_name=None,
            backend="deterministic",
        )
        print_record = json.loads(
            (print_candidate / "print-candidate.json").read_text()
        )
        self.assertEqual(print_record["pet_name"], "COOPER")
        self.assertFalse((print_candidate / "outputs" / "fonts").exists())
        print_layout = json.loads(
            (print_candidate / "outputs" / "layout-print.json").read_text()
        )
        self.assertNotIn("name", print_layout)
        with self.assertRaisesRegex(AuthoringError, "embedded pet-name source"):
            prepare_print_candidate(
                candidate_id="print-embedded-name-mismatch",
                authoring_product=product,
                art_attempt=art_attempt,
                pet_attempt=pet_attempt,
                layout_attempt=attempt,
                pet_name="MILO",
                backend="deterministic",
            )

        art_evaluation = compare(
            kind="art",
            review_id="art-embedded-eval",
            authoring_product=product,
            experiments=[art_exp.name],
            evaluation_protocol=self.protocol,
            fixture_set=None,
            art_attempt=None,
            pet_experiment=None,
            layout_attempt=None,
            base_bundle_revision=None,
        )
        layout_evaluation = compare(
            kind="layout",
            review_id="layout-embedded-eval",
            authoring_product=product,
            experiments=[layout_exp.name],
            evaluation_protocol=self.protocol,
            fixture_set=None,
            art_attempt=None,
            pet_experiment=None,
            layout_attempt=None,
            base_bundle_revision=None,
        )
        assembly_evaluation = compare(
            kind="assembly",
            review_id="assembly-embedded-eval",
            authoring_product=product,
            experiments=[],
            evaluation_protocol=self.protocol,
            fixture_set=None,
            art_attempt=art_attempt,
            pet_experiment=pet_exp,
            layout_attempt=attempt,
            base_bundle_revision=None,
        )
        art_decision = record_decision(
            review=art_evaluation.parent,
            selected_by="owner",
            notes="embedded art",
            selected_experiment=art_exp.name,
            selected_attempt=art_attempt.name,
        )
        pet_decision = record_decision(
            review=pet_evaluation.parent,
            selected_by="owner",
            notes="embedded pet runtime",
            selected_experiment=pet_exp.name,
            selected_attempt=None,
        )
        layout_decision = record_decision(
            review=layout_evaluation.parent,
            selected_by="owner",
            notes="embedded layout",
            selected_experiment=layout_exp.name,
            selected_attempt=attempt.name,
        )
        assembly_decision = record_decision(
            review=assembly_evaluation.parent,
            selected_by="owner",
            notes="embedded assembly",
            selected_experiment=None,
            selected_attempt=None,
        )
        selection = graduate(
            graduation_id="embedded-name-selection",
            print_candidate=print_candidate,
            art_review=art_decision.parent,
            pet_review=pet_decision.parent,
            layout_review=layout_decision.parent,
            assembly_review=assembly_decision.parent,
            selected_by="owner",
            notes="embedded winner",
            authoring_root=self.authoring,
        )
        selection_record = json.loads(selection.read_text())
        self.assertIsNone(selection_record["selected"]["layout"]["font_sha256"])
        self._validate_schema(selection, "selection-v1.schema.json")

        bundle = build_from_selection(
            selection_path=selection,
            output_dir=self.root / "embedded-exchange" / "bundles",
            bundle_revision="next",
            pet_name_max_length=12,
            qa_input_pet=self.pet,
        )
        bundle_record = validate_production_bundle(bundle)
        self.assertEqual(bundle_record["renderer"]["name_mode"], "embedded-in-pet")
        self.assertFalse((bundle / "fonts").exists())
        self.assertNotIn("name", json.loads((bundle / "layout.json").read_text()))

    def test_layout_attempt_defaults_to_pet_or_explicitly_disables_name(self) -> None:
        art_exp = self._experiment("art", "art-layout-mode", self.art_prompt)
        pet_exp = self._experiment("pet", "pet-layout-mode", self.pet_prompt)
        art_attempt = self._fake_attempt(
            art_exp, "attempt-0001", "art.png", (672, 1008)
        )
        pet_attempt = self._fake_attempt(
            pet_exp, "attempt-0001", "transformed-pet.png", (816, 816)
        )
        layout_exp = create_experiment(
            kind="layout",
            experiment_id="layout-mode-v01",
            design_id="life-is-good",
            product_profile=self.profile,
            authoring_root=self.authoring,
            references=[],
            prompt_file=None,
            provider=None,
            model=None,
            quality="high",
            art_attempt=art_attempt,
            pet_attempt=pet_attempt,
            font_catalogs=[],
            parent_experiment_id=None,
            base_bundle_revision=None,
            created_by="test",
        )

        fixture = {
            "pet_name": "PET",
            "name_mode": "layout-text",
            "layout_sha256": "a" * 64,
        }
        with patch(
            "pawmarvel_generator.authoring._run_layout", return_value=fixture
        ) as run_layout:
            default_attempt = run_attempt(
                experiment=layout_exp,
                attempt_id="default-name",
                pet_image=None,
            )
        self.assertEqual(run_layout.call_args.args[3], "PET")
        default_record = json.loads((default_attempt / "run.json").read_text())
        self.assertEqual(default_record["layout_fixture"]["pet_name"], "PET")

        fixture = {
            "pet_name": None,
            "name_mode": "embedded-in-pet",
            "layout_sha256": "b" * 64,
        }
        with patch(
            "pawmarvel_generator.authoring._run_layout", return_value=fixture
        ) as run_layout:
            no_name_attempt = run_attempt(
                experiment=layout_exp,
                attempt_id="no-name",
                pet_image=None,
                no_pet_name=True,
            )
        self.assertIsNone(run_layout.call_args.args[3])
        no_name_record = json.loads((no_name_attempt / "run.json").read_text())
        self.assertIsNone(no_name_record["layout_fixture"]["pet_name"])

    def test_attempt_ids_are_immutable(self) -> None:
        art_exp = self._experiment("art", "art-gpt-v01", self.art_prompt)
        self._fake_attempt(art_exp, "attempt-0001", "art.png", (672, 1008))
        with self.assertRaisesRegex(AuthoringError, "already exists"):
            run_attempt(experiment=art_exp, attempt_id="attempt-0001", pet_image=None, pet_name="PET")

    def test_bad_pet_input_does_not_leave_retry_blocking_partial(self) -> None:
        pet_exp = self._experiment("pet", "pet-gpt-v01", self.pet_prompt)
        missing = self.root / "missing-pet.png"
        with self.assertRaises(FileNotFoundError):
            run_attempt(
                experiment=pet_exp,
                attempt_id="attempt-0001",
                pet_image=missing,
                pet_name="PET",
            )
        self.assertFalse((pet_exp / "attempts" / "attempt-0001.partial").exists())

    def test_generation_failure_is_saved_as_immutable_attempt(self) -> None:
        art_exp = self._experiment("art", "art-gpt-v01", self.art_prompt)
        with patch(
            "pawmarvel_generator.authoring.generate",
            side_effect=RuntimeError("provider unavailable"),
        ), self.assertRaisesRegex(AuthoringError, "immutable record saved"):
            run_attempt(
                experiment=art_exp,
                attempt_id="attempt-0001",
                pet_image=None,
                pet_name="PET",
            )
        attempt = art_exp / "attempts" / "attempt-0001"
        self.assertTrue(attempt.is_dir())
        self.assertFalse(attempt.with_name("attempt-0001.partial").exists())
        record = json.loads((attempt / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")

    def test_real_art_attempt_runs_generation_adapter_and_records_output(self) -> None:
        art_exp = self._experiment("art", "art-gpt-v01", self.art_prompt)

        def fake_generate(args) -> None:
            make_transparent_mark(
                Path(args.output_dir) / args.output_name,
                size=(672, 1008),
            )

        with patch(
            "pawmarvel_generator.authoring.generate", side_effect=fake_generate
        ) as generated:
            attempt = run_attempt(
                experiment=art_exp,
                attempt_id="attempt-0001",
                pet_image=None,
                pet_name="PET",
            )
        generated.assert_called_once()
        record = json.loads((attempt / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["outputs"][0]["path"], "outputs/art.png")
        self.assertNotIn("experiment_path", record)

    def test_real_pet_attempt_runs_generation_adapter_and_records_input(self) -> None:
        self.pet_prompt.write_text(
            "Generate {{PET_NAME}} with the transformed pet.", encoding="utf-8"
        )
        pet_exp = self._experiment("pet", "pet-gpt-v01", self.pet_prompt)

        def fake_generate(args) -> None:
            make_transparent_mark(
                Path(args.output_dir) / args.output_name,
                size=(816, 816),
            )

        with patch(
            "pawmarvel_generator.authoring.generate", side_effect=fake_generate
        ) as generated:
            attempt = run_attempt(
                experiment=pet_exp,
                attempt_id="attempt-0001",
                pet_image=self.pet,
                pet_name="  PET  ",
            )
        self.assertEqual(generated.call_args.args[0].pet_name, "PET")
        record = json.loads((attempt / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["input_pet_sha256"], sha256(self.pet))
        self.assertEqual(record["prompt_variables"], {"pet_name": "PET"})
        self.assertTrue((attempt / "inputs" / "input-pet.png").is_file())

    def test_pet_attempt_omits_name_by_default(self) -> None:
        pet_exp = self._experiment("pet", "pet-no-name-v01", self.pet_prompt)

        def fake_generate(args) -> None:
            make_transparent_mark(
                Path(args.output_dir) / args.output_name,
                size=(816, 816),
            )

        with patch(
            "pawmarvel_generator.authoring.generate", side_effect=fake_generate
        ) as generated:
            attempt = run_attempt(
                experiment=pet_exp,
                attempt_id="attempt-0001",
                pet_image=self.pet,
            )

        self.assertIsNone(generated.call_args.args[0].pet_name)
        record = json.loads((attempt / "run.json").read_text(encoding="utf-8"))
        self.assertNotIn("prompt_variables", record)
        self.assertNotIn("prompt_variables", record["resolved_generation"])

    def test_pet_attempt_inherits_name_from_experiment(self) -> None:
        self.pet_prompt.write_text(
            "Generate {{PET_NAME}} with the transformed pet.", encoding="utf-8"
        )
        pet_exp = create_experiment(
            kind="pet",
            experiment_id="pet-artistic-name-v01",
            design_id="life-is-good",
            product_profile=self.profile,
            authoring_root=self.authoring,
            references=[self.reference],
            prompt_file=self.pet_prompt,
            pet_name="  Cooper  ",
            provider="openai",
            model="gpt-image-2",
            quality="low",
            art_attempt=None,
            pet_attempt=None,
            font_catalogs=[],
            parent_experiment_id=None,
            base_bundle_revision=None,
            created_by="test",
        )
        experiment_record = json.loads(
            (pet_exp / "experiment.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            experiment_record["generation"]["prompt_variables"],
            {"pet_name": "Cooper"},
        )
        self._validate_schema(pet_exp / "experiment.json", "experiment-v1.schema.json")

        def fake_generate(args) -> None:
            make_transparent_mark(
                Path(args.output_dir) / args.output_name,
                size=(816, 816),
            )

        with patch(
            "pawmarvel_generator.authoring.generate", side_effect=fake_generate
        ) as generated:
            attempt = run_attempt(
                experiment=pet_exp,
                attempt_id="attempt-0001",
                pet_image=self.pet,
            )

        self.assertEqual(generated.call_args.args[0].pet_name, "Cooper")
        attempt_record = json.loads(
            (attempt / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            attempt_record["prompt_variables"], {"pet_name": "Cooper"}
        )
        self.assertEqual(
            attempt_record["resolved_generation"]["prompt_variables"],
            {"pet_name": "Cooper"},
        )

        with patch(
            "pawmarvel_generator.authoring.generate", side_effect=fake_generate
        ) as generated:
            override = run_attempt(
                experiment=pet_exp,
                attempt_id="attempt-0002",
                pet_image=self.pet,
                pet_name="MILO",
            )

        self.assertEqual(generated.call_args.args[0].pet_name, "MILO")
        override_record = json.loads(
            (override / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            override_record["resolved_generation"]["prompt_variables"],
            {"pet_name": "MILO"},
        )

    def test_pet_experiment_rejects_more_than_four_references(self) -> None:
        with self.assertRaisesRegex(AuthoringError, "at most four"):
            create_experiment(
                kind="pet",
                experiment_id="pet-too-many-references",
                design_id="life-is-good",
                product_profile=self.profile,
                authoring_root=self.authoring,
                references=[self.reference] * 5,
                prompt_file=self.pet_prompt,
                provider="openai",
                model="gpt-image-2",
                quality="low",
                art_attempt=None,
                pet_attempt=None,
                font_catalogs=[],
                parent_experiment_id=None,
                base_bundle_revision=None,
                created_by="test",
            )

    def test_comparison_rejects_duplicate_experiment_ids(self) -> None:
        art_exp = self._experiment("art", "art-gpt-v01", self.art_prompt)
        self._fake_attempt(art_exp, "attempt-0001", "art.png", (672, 1008))
        product = self.authoring / "life-is-good" / "test-blanket"
        with self.assertRaisesRegex(AuthoringError, "must be unique"):
            compare(
                kind="art",
                review_id="duplicate-eval",
                authoring_product=product,
                experiments=[art_exp.name, art_exp.name],
                evaluation_protocol=self.protocol,
                fixture_set=None,
                art_attempt=None,
                pet_experiment=None,
                layout_attempt=None,
                base_bundle_revision=None,
            )

    def test_failed_contact_sheet_does_not_reserve_review_id(self) -> None:
        art_exp = self._experiment("art", "art-gpt-v01", self.art_prompt)
        self._fake_attempt(art_exp, "attempt-0001", "art.png", (672, 1008))
        self._fake_attempt(art_exp, "attempt-0002", "art.png", (672, 1008))
        product = self.authoring / "life-is-good" / "test-blanket"
        with patch.object(
            Image.Image, "save", side_effect=OSError("corrupt output")
        ), self.assertRaisesRegex(OSError, "corrupt output"):
            compare(
                kind="art",
                review_id="retryable-review",
                authoring_product=product,
                experiments=[art_exp.name],
                evaluation_protocol=self.protocol,
                fixture_set=None,
                art_attempt=None,
                pet_experiment=None,
                layout_attempt=None,
                base_bundle_revision=None,
            )
        self.assertFalse(
            (product / "reviews" / "art" / "retryable-review").exists()
        )

    def test_pet_comparison_filters_benchmark_attempts_and_checks_fixtures(self) -> None:
        first = self._experiment("pet", "pet-gpt-v01", self.pet_prompt)
        second = self._experiment("pet", "pet-gpt-v02", self.pet_prompt)
        self._fake_attempt(first, "smoke-0001", "transformed-pet.png", (816, 816))
        self._fake_attempt(first, "benchmark-pet-0001", "transformed-pet.png", (816, 816))
        self._fake_attempt(second, "benchmark-pet-0001", "transformed-pet.png", (816, 816))
        second_fixture_pet = make_image(
            self.root / "pet-two.png", color=(90, 100, 110, 255)
        )
        self._fake_attempt(
            first, "benchmark-pet-two-0001", "transformed-pet.png", (816, 816),
            pet_source=second_fixture_pet,
        )
        unselected_pet = make_image(
            self.root / "pet-unselected.png", color=(20, 30, 40, 255)
        )
        self._fake_attempt(
            first,
            "benchmark-unselected-0001",
            "transformed-pet.png",
            (816, 816),
            pet_source=unselected_pet,
        )
        fixture_set = self.root / "fixture-set.json"
        atomic_json(
            fixture_set,
            {
                "schema_version": 2,
                "fixture_set_id": "test-smoke-v1",
                "tier": "smoke",
                "attempts_per_fixture": 1,
                "fixtures": [
                    {
                        "id": fixture_id,
                        "pet_image": image.name,
                        "sha256": sha256(image),
                        "species": "dog",
                        "breed": {"id": fixture_id, "label": label, "mixed": False},
                        "size_class": size,
                        "morphology": morphology,
                        "coat": {"length": "short", "texture": "smooth", "tone": "medium"},
                        "capture": {"framing": "full-body", "view": "front", "subject_coverage": "isolated", "background_complexity": "simple"},
                        "risk_tags": risk_tags,
                        "rights": {"source_kind": "test", "license": "test-only", "reviewed": False, "intended_use": "test"},
                    }
                    for fixture_id, image, label, size, morphology, risk_tags in (
                        ("pet", self.pet, "Pet one", "small", ["compact"], ["light-edge-detail"]),
                        ("pet-two", second_fixture_pet, "Pet two", "large", ["long-legs"], ["thin-legs"]),
                    )
                ],
            },
        )
        product = self.authoring / "life-is-good" / "test-blanket"
        fixture_selection = write_fixture_selection(
            fixture_set,
            output=self.root / "pet-controlled-selection.json",
            fixture_count=2,
            filters=("id=pet", "id=pet-two"),
        )
        evaluation = compare(
            kind="pet",
            review_id="pet-controlled-eval",
            authoring_product=product,
            experiments=[first.name, second.name],
            evaluation_protocol=self.protocol,
            fixture_set=fixture_set,
            art_attempt=None,
            pet_experiment=None,
            layout_attempt=None,
            base_bundle_revision=None,
            attempt_prefix="benchmark-",
            fixture_selection=fixture_selection,
        )
        record = json.loads(evaluation.read_text())
        self.assertEqual(record["attempt_id_prefix"], "benchmark-")
        self.assertEqual(
            record["fixture_selection"]["selected_fixture_ids"],
            ["pet", "pet-two"],
        )
        self.assertEqual(
            record["fixture_selection"]["config_sha256"],
            sha256(fixture_selection),
        )
        complete, incomplete = record["candidates"]
        self.assertEqual(complete["measurements"]["attempts"], 2)
        self.assertEqual(complete["fixture_coverage"]["status"], "passed")
        self.assertEqual(complete["fixture_coverage"]["coverage_rate"], 1.0)
        self.assertEqual(complete["fixture_coverage"]["missing_fixture_ids"], [])
        self.assertEqual(
            {group["value"] for group in complete["fixture_coverage"]["groups"]["size_class"]},
            {"small", "large"},
        )
        self.assertEqual(
            complete["fixture_coverage"]["groups"]["species"][0]["value"],
            "dog",
        )
        self.assertEqual(incomplete["measurements"]["attempts"], 1)
        self.assertEqual(incomplete["fixture_coverage"]["status"], "warning")
        self.assertEqual(incomplete["fixture_coverage"]["coverage_rate"], 0.5)
        self.assertEqual(
            incomplete["fixture_coverage"]["missing_fixture_ids"], ["pet-two"]
        )
        self.assertTrue(incomplete["hard_gates_passed"])
        self.assertEqual(record["hard_gates"]["status"], "passed")
        self.assertEqual(len(record["warnings"]), 1)
        self.assertIn("pet-gpt-v02", record["warnings"][0])
        self.assertIn("pet-two", record["warnings"][0])

        with self.assertRaisesRegex(
            AuthoringError, "selected candidate contains warnings.*--notes"
        ):
            record_decision(
                review=evaluation.parent,
                selected_by="application-owner",
                notes="",
                selected_experiment=second.name,
                selected_attempt=None,
            )
        decision = record_decision(
            review=evaluation.parent,
            selected_by="application-owner",
            notes="Accepted reduced coverage for MVP release",
            selected_experiment=second.name,
            selected_attempt=None,
        )
        decision_record = json.loads(decision.read_text(encoding="utf-8"))
        self.assertEqual(
            decision_record["decision"]["accepted_warnings"], record["warnings"]
        )

        complete_evaluation = compare(
            kind="pet",
            review_id="pet-controlled-complete-winner",
            authoring_product=product,
            experiments=[first.name, second.name],
            evaluation_protocol=self.protocol,
            fixture_set=fixture_set,
            art_attempt=None,
            pet_experiment=None,
            layout_attempt=None,
            base_bundle_revision=None,
            attempt_prefix="benchmark-",
            fixture_selection=fixture_selection,
        )
        complete_decision = record_decision(
            review=complete_evaluation.parent,
            selected_by="application-owner",
            notes="",
            selected_experiment=first.name,
            selected_attempt=None,
        )
        self.assertEqual(
            json.loads(complete_decision.read_text(encoding="utf-8"))["decision"][
                "accepted_warnings"
            ],
            [],
        )

    def test_benchmark_runs_only_reviewed_fixture_selection(self) -> None:
        experiment = self._experiment("pet", "pet-benchmark-v01", self.pet_prompt)
        second_pet = make_image(self.root / "pet-two.png", color=(90, 100, 110, 255))
        third_pet = make_image(self.root / "pet-three.png", color=(20, 30, 40, 255))
        fixture_set = self.root / "fixture-set.json"
        atomic_json(
            fixture_set,
            {
                "schema_version": 2,
                "fixture_set_id": "test-smoke-v1",
                "tier": "smoke",
                "attempts_per_fixture": 1,
                "fixtures": [
                    {
                        "id": fixture_id,
                        "pet_image": image.name,
                        "sha256": sha256(image),
                        "species": "dog",
                        "breed": {"id": fixture_id, "label": fixture_id, "mixed": False},
                        "size_class": size,
                        "morphology": ["compact"],
                        "coat": {"length": "short", "texture": "smooth", "tone": "medium"},
                        "capture": {"framing": "full-body", "view": "front", "subject_coverage": "isolated", "background_complexity": "simple"},
                        "risk_tags": ["edge-detail"],
                        "rights": {"source_kind": "test", "license": "test-only", "reviewed": False, "intended_use": "test"},
                    }
                    for fixture_id, image, size in (
                        ("pet", self.pet, "small"),
                        ("pet-two", second_pet, "medium"),
                        ("pet-three", third_pet, "large"),
                    )
                ],
            },
        )
        selection = write_fixture_selection(
            fixture_set,
            output=self.root / "benchmark-selection.json",
            fixture_count=2,
            filters=("id=pet", "id=pet-three"),
        )
        expected = [
            experiment / "attempts" / "release-pet-0001",
            experiment / "attempts" / "release-pet-three-0001",
        ]
        with patch(
            "pawmarvel_generator.authoring.run_attempt",
            side_effect=expected,
        ) as mocked_run:
            result = benchmark(
                experiment=experiment,
                fixture_set=fixture_set,
                fixture_selection=selection,
                evaluation_protocol=self.protocol,
                attempts_per_fixture=1,
                attempt_id_prefix="release",
            )

        self.assertEqual(result, expected)
        self.assertEqual(
            [call.kwargs["attempt_id"] for call in mocked_run.call_args_list],
            ["release-pet-0001", "release-pet-three-0001"],
        )
        self.assertEqual(
            [call.kwargs["pet_image"] for call in mocked_run.call_args_list],
            [self.pet.resolve(), third_pet.resolve()],
        )

        warning_output = io.StringIO()
        with patch(
            "pawmarvel_generator.authoring.run_attempt",
            side_effect=[AuthoringError("provider failed"), expected[1]],
        ) as mocked_run, redirect_stderr(warning_output):
            partial_result = benchmark(
                experiment=experiment,
                fixture_set=fixture_set,
                fixture_selection=selection,
                evaluation_protocol=self.protocol,
                attempts_per_fixture=1,
                attempt_id_prefix="retry",
            )
        self.assertEqual(mocked_run.call_count, 2)
        self.assertEqual(partial_result, [expected[1]])
        self.assertIn("WARNING: benchmark completed with incomplete fixture coverage", warning_output.getvalue())
        self.assertIn("provider failed", warning_output.getvalue())

    def test_benchmark_rejects_non_pet_experiment_before_paid_calls(self) -> None:
        experiment = self._experiment("art", "art-benchmark-v01", self.art_prompt)
        with (
            patch("pawmarvel_generator.authoring.run_attempt") as mocked_run,
            self.assertRaisesRegex(
                AuthoringError,
                r"fixture benchmarks require a pet experiment.*actual_kind='art'",
            ),
        ):
            benchmark(
                experiment=experiment,
                fixture_set=self.root / "missing-fixture-set.json",
                fixture_selection=self.root / "missing-selection.json",
                evaluation_protocol=self.protocol,
                attempts_per_fixture=1,
                attempt_id_prefix="release",
            )
        mocked_run.assert_not_called()

    def test_same_design_uses_independent_product_workspaces(self) -> None:
        second_profile = write_product_profile(
            self.root / "second-product-profile.json",
            create_product_profile(
                profile_id="test-throw",
                print_size=ImageSize(1344, 1680),
            ),
        )
        first = self._experiment("art", "art-gpt-v01", self.art_prompt)
        second = create_experiment(
            kind="art", experiment_id="art-gpt-v01", design_id="life-is-good",
            product_profile=second_profile, authoring_root=self.authoring,
            references=[self.reference], prompt_file=self.art_prompt,
            provider="openai", model="gpt-image-2", art_attempt=None,
            quality="high",
            pet_attempt=None, font_catalogs=[], parent_experiment_id=None,
            base_bundle_revision=None, created_by="test",
        )
        self.assertEqual(first.parent.parent.parent.name, "test-blanket")
        self.assertEqual(second.parent.parent.parent.name, "test-throw")
        self.assertNotEqual(first, second)

    def test_layout_rejects_attempt_from_another_product(self) -> None:
        second_profile = write_product_profile(
            self.root / "second-product-profile.json",
            create_product_profile(
                profile_id="test-throw",
                print_size=ImageSize(1344, 1680),
            ),
        )
        art_exp = self._experiment("art", "art-gpt-v01", self.art_prompt)
        pet_exp = self._experiment("pet", "pet-gpt-v01", self.pet_prompt)
        art_attempt = self._fake_attempt(art_exp, "attempt-0001", "art.png", (672, 1008))
        pet_attempt = self._fake_attempt(pet_exp, "attempt-0001", "transformed-pet.png", (816, 816))

        with self.assertRaisesRegex(AuthoringError, "not life-is-good/test-throw"):
            create_experiment(
                kind="layout", experiment_id="layout-v01", design_id="life-is-good",
                product_profile=second_profile, authoring_root=self.authoring,
                references=[], prompt_file=None, provider=None, model=None,
                quality="high",
                art_attempt=art_attempt, pet_attempt=pet_attempt, font_catalogs=[],
                parent_experiment_id=None, base_bundle_revision=None,
                created_by="test",
            )

    def test_layout_experiment_snapshots_font_catalog_directory(self) -> None:
        art_exp = self._experiment("art", "art-gpt-v01", self.art_prompt)
        pet_exp = self._experiment("pet", "pet-gpt-v01", self.pet_prompt)
        art_attempt = self._fake_attempt(art_exp, "attempt-0001", "art.png", (672, 1008))
        pet_attempt = self._fake_attempt(pet_exp, "attempt-0001", "transformed-pet.png", (816, 816))
        catalog = self.root / "fonts"
        catalog.mkdir()
        catalog_font = copy_font(catalog)
        catalog_metadata = catalog / "METADATA.pb"
        catalog_metadata.write_text(
            'name: "Test Font"\nlicense: "OFL"\n', encoding="utf-8"
        )
        atomic_json(
            catalog / "source.json",
            {
                "schema_version": 1,
                "source": "google-fonts-ofl",
                "family_id": "testfont",
                "family": "Test Font",
                "source_url": "https://github.com/google/fonts/tree/main/ofl/testfont",
                "font_filename": catalog_font.name,
                "font_sha256": sha256(catalog_font),
                "license_sha256": sha256(catalog / "OFL.txt"),
                "metadata_sha256": sha256(catalog_metadata),
            },
        )
        font_reference = self.root / "font-reference.json"
        atomic_json(
            font_reference,
            font_reference_from_editor(
                reference=self.reference,
                region={"x": 10, "y": 10, "width": 40, "height": 20},
                text="CHARLIE",
            ).to_dict(),
        )
        layout_reference = self.root / "layout-reference.json"
        atomic_json(
            layout_reference,
            layout_reference_from_editor(
                reference=self.reference,
                pet_region={"x": 5, "y": 5, "width": 50, "height": 50},
                name_region={"x": 10, "y": 10, "width": 40, "height": 20},
            ).to_dict(),
        )

        layout_exp = create_experiment(
            kind="layout", experiment_id="layout-v01", design_id="life-is-good",
            product_profile=self.profile, authoring_root=self.authoring,
            references=[], prompt_file=None, provider=None, model=None,
            quality="high",
            art_attempt=art_attempt, pet_attempt=pet_attempt,
            font_catalogs=[catalog], parent_experiment_id=None,
            base_bundle_revision=None, created_by="test",
            font_reference=font_reference,
            layout_reference=layout_reference,
        )
        snapshot = layout_exp / "inputs" / "font-catalog-01"
        self.assertTrue((snapshot / "catalog.snapshot.json").is_file())
        self.assertEqual(len(list(snapshot.rglob("*.ttf"))), 1)
        self.assertEqual(len(list(snapshot.rglob("OFL.txt"))), 1)
        self.assertEqual(len(list(snapshot.rglob("METADATA.pb"))), 1)
        self.assertEqual(len(list(snapshot.rglob("source.json"))), 1)
        snapshotted_reference = layout_exp / "inputs" / "font-reference.json"
        self.assertTrue(snapshotted_reference.is_file())
        self.assertEqual(
            json.loads(snapshotted_reference.read_text())["text"], "CHARLIE"
        )
        self.assertTrue((layout_exp / "inputs" / "layout-reference.json").is_file())

    def test_cleanup_is_dry_run_by_default_and_rejects_unsafe_inputs(self) -> None:
        experiment = self._experiment("art", "art-gpt-v01", self.art_prompt)
        set_status(experiment, "discarded")
        old = 1_600_000_000
        os.utime(experiment, (old, old))
        candidates = cleanup(
            authoring_root=self.authoring,
            status="discarded",
            older_than_days=1,
            apply=False,
        )
        self.assertEqual(candidates, [experiment.resolve()])
        self.assertTrue(experiment.is_dir())
        cleanup(
            authoring_root=self.authoring,
            status="discarded",
            older_than_days=1,
            apply=True,
        )
        self.assertFalse(experiment.exists())
        with self.assertRaisesRegex(AuthoringError, "must not be negative"):
            cleanup(
                authoring_root=self.authoring,
                status="discarded",
                older_than_days=-1,
                apply=False,
            )
        with self.assertRaisesRegex(AuthoringError, "filesystem root"):
            cleanup(
                authoring_root=Path("/"),
                status="discarded",
                older_than_days=1,
                apply=False,
            )

    def test_cleanup_can_remove_old_unselected_review_packets(self) -> None:
        experiment = self._experiment("art", "art-gpt-v01", self.art_prompt)
        self._fake_attempt(experiment, "attempt-0001", "art.png", (672, 1008))
        product = self.authoring / "life-is-good" / "test-blanket"
        evaluation = compare(
            kind="art",
            review_id="abandoned-review",
            authoring_product=product,
            experiments=[experiment.name],
            evaluation_protocol=self.protocol,
            fixture_set=None,
            art_attempt=None,
            pet_experiment=None,
            layout_attempt=None,
            base_bundle_revision=None,
        )
        review = evaluation.parent
        old = 1_600_000_000
        os.utime(review, (old, old))
        candidates = cleanup(
            authoring_root=self.authoring,
            status="unselected",
            older_than_days=1,
            apply=False,
        )
        self.assertEqual(candidates, [review.resolve()])
        self.assertTrue(review.is_dir())


if __name__ == "__main__":
    unittest.main()
