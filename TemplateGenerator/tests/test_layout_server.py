from __future__ import annotations

import json
import io
import hashlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from helpers import copy_font, layout_data, make_image
from pawmarvel_generator.font_catalog import FontCandidate, discover_font_catalog
from pawmarvel_generator.font_match import (
    recommend_font_size,
    recommend_min_font_size_for_capacity,
)
from pawmarvel_generator.font_reference import font_reference_from_editor
from pawmarvel_generator.layout_cli import build_parser
from pawmarvel_generator.layout_reference import layout_reference_from_editor
from pawmarvel_generator.layout_server import ConfigError, EditorConfig, create_server
from pawmarvel_generator.remote_fonts import (
    ImportedFontFamily,
    RemoteFontError,
    RemoteFontFamily,
)
from pawmarvel_generator.renderer import render_preview


class LayoutServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.art = make_image(
            self.root / "art.png", size=(200, 300), color=(20, 30, 40, 255)
        )
        self.pet = make_image(
            self.root / "pet.png", size=(80, 80), color=(200, 100, 50, 255)
        )
        external = self.root / "external"
        external.mkdir()
        self.font = copy_font(external)
        self.reference = self.root / "reference.png"
        reference = Image.new("RGB", (200, 300), "white")
        ImageDraw.Draw(reference).text(
            (20, 215),
            "BUDDY",
            font=ImageFont.truetype(str(self.font), 42),
            fill="black",
        )
        reference.save(self.reference)
        self.font_catalog = Path(__file__).resolve().parents[1] / "assets" / "fonts"
        self.font_candidates = discover_font_catalog(
            self.font, catalog_roots=(self.font_catalog,)
        )
        self.output = self.root / "layout.json"
        self.server = create_server(
            EditorConfig(
                art=self.art,
                reference=self.reference,
                pet=self.pet,
                pet_name="BUDDY",
                font=self.font,
                font_catalogs=(self.font_catalog,),
                output=self.output,
            )
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def post(self, path: str, payload: dict) -> urllib.request.addinfourl:
        if path in {"/preview", "/save"}:
            payload = {"revision": 1, "pet_name": "BUDDY", **payload}
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(request)

    def upload_preview_pet(self, image: Image.Image) -> dict:
        content = io.BytesIO()
        image.save(content, format="PNG")
        request = urllib.request.Request(
            self.base + "/preview-pet",
            data=content.getvalue(),
            headers={"Content-Type": "image/png"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    @staticmethod
    def font_reference() -> dict:
        return {
            "region": {"x": 10, "y": 205, "width": 180, "height": 60},
            "text": "BUDDY",
        }

    @staticmethod
    def layout_reference() -> dict:
        return {
            "pet_region": {"x": 20, "y": 30, "width": 100, "height": 120},
            "name_region": {"x": 10, "y": 205, "width": 180, "height": 60},
        }

    def test_layout_cli_pet_name_is_an_optional_initial_value(self) -> None:
        args = build_parser().parse_args(
            [
                "--art",
                str(self.art),
                "--reference",
                str(self.reference),
                "--pet",
                str(self.pet),
                "--font",
                str(self.font),
                "--output",
                str(self.output),
            ]
        )
        self.assertEqual(args.pet_name, "PET")

        args = build_parser().parse_args(
            [
                "--art", str(self.art),
                "--reference", str(self.reference),
                "--pet", str(self.pet),
                "--font", str(self.font),
                "--output", str(self.output),
                "--no-pet-name",
            ]
        )
        self.assertIsNone(args.pet_name)

    def test_new_editor_without_pet_name_starts_with_name_layer_disabled(self) -> None:
        no_name_root = self.root / "no-name"
        art = make_image(
            no_name_root / "art.png", size=(200, 300), color=(20, 30, 40, 255)
        )
        output = no_name_root / "layout.json"
        server = create_server(
            EditorConfig(
                art=art,
                reference=self.reference,
                pet=self.pet,
                font=self.font,
                output=output,
                name_enabled=False,
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/"
            ) as response:
                html = response.read()
            self.assertIn(b'"nameEnabled": false', html)
            self.assertIn(b'"petName": "PET"', html)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_serves_packaged_editor_assets(self) -> None:
        with urllib.request.urlopen(self.base + "/") as response:
            html = response.read()
            self.assertIn(b"PawMarvel Layout Configurator", html)
            self.assertIn(b'"fontCandidates"', html)
            self.assertIn(b"Amatic SC Bold", html)
            self.assertIn(b"Save &amp; continue", html)
            self.assertIn(b"preview-pet-name", html)
            self.assertIn(b"preview-pet-upload", html)
        with urllib.request.urlopen(self.base + "/assets/layout.js") as response:
            script = response.read()
            self.assertIn(b"requestPreview", script)
            self.assertIn(b"Top 15 font recommendations", script)
            self.assertIn(b"recommendation.confidence_level", script)
            self.assertIn(b"AbortController", script)
            self.assertIn(b"renderedRevision", script)
            self.assertIn(b"pet_name", script)
            self.assertIn(b"/rank-fonts", script)
            self.assertIn(b"/calibrate-font-size", script)
            self.assertIn(b"/preview-pet", script)
            self.assertIn(b"/search-fonts", script)
            self.assertIn(b"/import-font", script)
            self.assertIn(b"scaleNameTypography", script)
            self.assertIn(b"applyReferenceGeometry", script)
            self.assertIn(b"layout_reference", script)
            self.assertIn(b"font_reference", script)
            self.assertIn(b"/heartbeat", script)
            self.assertIn(b"pagehide", script)

        with self.post("/heartbeat", {}) as response:
            self.assertEqual(response.status, 204)

    def test_saves_layout_without_separate_name_layer_or_font_assets(self) -> None:
        layout = layout_data()
        del layout["name"]
        payload = {"layout": layout, "font_id": self.font_candidates[0].candidate_id}

        with self.post("/preview", payload), self.post("/save", payload):
            pass

        saved = json.loads(self.output.read_text(encoding="utf-8"))
        fixture = json.loads(
            (self.root / "qa" / "calibration-fixture.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("name", saved)
        self.assertEqual(fixture["name_mode"], "embedded-in-pet")
        self.assertIsNone(fixture["pet_name"])
        self.assertFalse((self.root / "fonts").exists())

    def test_remote_ofl_font_can_be_explored_and_saved(self) -> None:
        family = RemoteFontFamily("remotetest", "Remote Test")

        def imported(_family_id: str, destination: Path) -> ImportedFontFamily:
            family_root = destination / "remotetest"
            family_root.mkdir()
            font = copy_font(family_root, "Remote-Regular.ttf")
            metadata = family_root / "METADATA.pb"
            metadata.write_text(
                'name: "Remote Test"\nlicense: "OFL"\n', encoding="utf-8"
            )
            digest = hashlib.sha256(font.read_bytes()).hexdigest()
            candidates = (
                FontCandidate(
                    candidate_id="font-remote-test",
                    label="Remote Test",
                    font=font,
                    license=family_root / "OFL.txt",
                    sha256=digest,
                ),
            )
            return ImportedFontFamily(
                family=family,
                candidates=candidates,
                license=family_root / "OFL.txt",
                metadata=metadata,
                source_url="https://github.com/google/fonts/tree/main/ofl/remotetest",
            )

        with (
            patch(
                "pawmarvel_generator.layout_server.search_google_ofl",
                return_value=(family,),
            ),
            patch(
                "pawmarvel_generator.layout_server.import_google_ofl_family",
                side_effect=imported,
            ),
        ):
            with self.post("/search-fonts", {"query": "Remote Test"}) as response:
                search = json.loads(response.read())
            self.assertEqual(search["remote"][0]["family_id"], "remotetest")

            with self.post("/import-font", {"family_id": "remotetest"}) as response:
                result = json.loads(response.read())
            selected = result["candidates"][0]

            with urllib.request.urlopen(
                f"{self.base}/fonts/{selected['id']}"
            ) as response:
                self.assertEqual(response.headers.get_content_type(), "font/ttf")

            payload = {"layout": layout_data(), "font_id": selected["id"]}
            with self.post("/preview", payload), self.post("/save", payload):
                pass

        saved = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(saved["name"]["font"], "fonts/Remote-Regular.ttf")
        self.assertTrue((self.root / "fonts" / "Remote-Regular.ttf").is_file())
        self.assertTrue((self.root / "fonts" / "OFL.txt").is_file())
        self.assertTrue((self.root / "fonts" / "METADATA.pb").is_file())
        source = json.loads(
            (self.root / "fonts" / "source.json").read_text(encoding="utf-8")
        )
        self.assertEqual(source["source"], "google-fonts-ofl")
        self.assertEqual(source["family_id"], "remotetest")

    def test_exact_local_font_avoids_remote_lookup(self) -> None:
        with patch(
            "pawmarvel_generator.layout_server.search_google_ofl"
        ) as remote_search:
            with self.post("/search-fonts", {"query": "TestFont"}) as response:
                result = json.loads(response.read())
        self.assertTrue(result["local"])
        self.assertEqual(result["remote"], [])
        remote_search.assert_not_called()

    def test_remote_font_search_failure_returns_structured_gateway_error(self) -> None:
        with patch(
            "pawmarvel_generator.layout_server.search_google_ofl",
            side_effect=RemoteFontError("GitHub connection reset after retry"),
        ):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.post("/search-fonts", {"query": "Bodoni"})

        self.assertEqual(raised.exception.code, 502)
        payload = json.loads(raised.exception.read())
        raised.exception.close()
        self.assertEqual(payload["code"], "remote_font_search_failed")
        self.assertIn("Bodoni", payload["error"])
        self.assertIn("connection reset", payload["error"])

    def test_preview_and_save_use_shared_renderer(self) -> None:
        payload = {"layout": layout_data()}
        with self.post("/preview", payload) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.headers["X-PawMarvel-Preview-Revision"], "1")
            self.assertEqual(response.headers["X-PawMarvel-Text-Fit"], "nominal")
            preview = response.read()
            self.assertTrue(preview.startswith(b"\x89PNG"))

        with self.post("/save", payload) as response:
            saved = json.loads(response.read())
        self.assertEqual(Path(saved["layout"]), self.output.resolve())
        self.assertTrue(self.output.is_file())
        self.assertTrue((self.root / "fonts" / "TestFont.ttf").is_file())
        self.assertTrue((self.root / "fonts" / "OFL.txt").is_file())
        saved_layout = json.loads(self.output.read_text())
        self.assertEqual(saved_layout["schema_version"], 2)
        self.assertNotIn("model", saved_layout)
        calibration = self.root / "qa" / "calibration-preview.png"
        self.assertEqual(calibration.read_bytes(), preview)
        fixture = json.loads(
            (self.root / "qa" / "calibration-fixture.json").read_text()
        )
        self.assertEqual(fixture["pet_name"], "BUDDY")
        self.assertEqual(fixture["revision"], 1)
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(
            preview,
            render_preview(self.root, self.pet, "BUDDY"),
        )

    def test_reopens_existing_layout_and_force_replaces_legacy_layout(self) -> None:
        payload = {"layout": layout_data()}
        with self.post("/preview", payload), self.post("/save", payload):
            pass
        reopened = create_server(
            EditorConfig(
                art=self.art,
                reference=self.reference,
                pet=self.pet,
                font=self.font,
                output=self.output,
            )
        )
        reopened.server_close()

        legacy = self.output
        legacy.write_text(
            json.dumps({**layout_data(), "schema_version": 1}), encoding="utf-8"
        )
        with self.assertRaisesRegex(ConfigError, "pass --force"):
            create_server(
                EditorConfig(
                    art=self.art,
                    reference=self.reference,
                    pet=self.pet,
                    font=self.font,
                    output=legacy,
                )
            )
        forced = create_server(
            EditorConfig(
                art=self.art,
                reference=self.reference,
                pet=self.pet,
                font=self.font,
                output=legacy,
                force=True,
            )
        )
        forced.server_close()

    def test_blank_font_reference_does_not_block_editor_startup(self) -> None:
        blank_root = self.root / "blank-layout"
        blank_art = make_image(
            blank_root / "art.png", size=(200, 300), color=(20, 30, 40, 255)
        )
        reference_path = self.root / "blank-font-reference.json"
        reference_path.write_text(
            json.dumps(
                font_reference_from_editor(
                    reference=self.reference,
                    region={"x": 0, "y": 0, "width": 30, "height": 30},
                    text="BUDDY",
                ).to_dict()
            ),
            encoding="utf-8",
        )
        server = create_server(
            EditorConfig(
                art=blank_art,
                reference=self.reference,
                pet=self.pet,
                font=None,
                font_catalogs=(self.font_catalog,),
                font_reference=reference_path,
                output=blank_root / "layout.json",
                auto_font=True,
            )
        )
        server.server_close()

    def test_switches_between_temporary_and_pinned_preview_pets(self) -> None:
        alternate = Image.new("RGBA", (90, 70), (0, 0, 0, 0))
        ImageDraw.Draw(alternate).ellipse((10, 5, 80, 68), fill=(30, 180, 90, 255))
        uploaded = self.upload_preview_pet(alternate)

        with self.post(
            "/preview",
            {
                "layout": layout_data(),
                "revision": 2,
                "preview_pet_id": uploaded["id"],
            },
        ) as response:
            alternate_preview = response.read()
        with self.post(
            "/preview",
            {
                "layout": layout_data(),
                "revision": 3,
                "preview_pet_id": "pinned",
            },
        ) as response:
            pinned_preview = response.read()
        self.assertNotEqual(alternate_preview, pinned_preview)

        with self.post(
            "/save",
            {
                "layout": layout_data(),
                "revision": 3,
                "preview_pet_id": "pinned",
            },
        ):
            pass
        fixture = json.loads(
            (self.root / "qa" / "calibration-fixture.json").read_text()
        )
        self.assertEqual(fixture["transformed_pet"]["source"], "layout-experiment")
        self.assertEqual(
            {item["sha256"] for item in fixture["tested_transformed_pets"]},
            {uploaded["sha256"], fixture["transformed_pet_sha256"]},
        )

    def test_save_rejects_a_state_that_was_not_previewed(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.post("/save", {"layout": layout_data(), "revision": 9})
        self.assertEqual(raised.exception.code, 409)
        raised.exception.close()

    def test_save_rejects_changed_name_for_a_previewed_revision(self) -> None:
        with self.post(
            "/preview",
            {"layout": layout_data(), "revision": 9, "pet_name": "BUDDY"},
        ) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.post(
                "/save",
                {
                    "layout": layout_data(),
                    "revision": 9,
                    "pet_name": "MARSHMALLOW",
                },
            )
        self.assertEqual(raised.exception.code, 409)
        raised.exception.close()

    def test_editable_preview_name_drives_saved_calibration_fixture(self) -> None:
        payload = {
            "layout": layout_data(),
            "revision": 2,
            "pet_name": "MARSHMALLOW",
        }
        with self.post("/preview", payload) as response:
            self.assertEqual(response.headers["X-PawMarvel-Preview-Revision"], "2")
            self.assertEqual(response.headers["X-PawMarvel-Text-Fit"], "shrunk")
        with self.post("/save", payload) as response:
            saved = json.loads(response.read())

        fixture = json.loads(
            (self.root / "qa" / "calibration-fixture.json").read_text()
        )
        self.assertEqual(saved["pet_name"], "MARSHMALLOW")
        self.assertEqual(fixture["pet_name"], "MARSHMALLOW")
        self.assertLess(fixture["applied_font_size_px"], 42)

    def test_selected_catalog_font_is_previewed_and_saved(self) -> None:
        selected = next(
            candidate
            for candidate in self.font_candidates
            if candidate.font.name == "AmaticSC-Bold.ttf"
        )
        payload = {
            "layout": layout_data(),
            "font_id": selected.candidate_id,
            "font_reference": self.font_reference(),
            "layout_reference": self.layout_reference(),
            "font_selection_confirmed": True,
        }

        with urllib.request.urlopen(
            f"{self.base}/fonts/{selected.candidate_id}"
        ) as response:
            self.assertEqual(response.headers.get_content_type(), "font/ttf")
            self.assertEqual(response.read(), selected.font.read_bytes())
        with self.post(
            "/rank-fonts", {"font_reference": self.font_reference()}
        ) as response:
            ranking = json.loads(response.read())
        self.assertEqual(
            ranking["method"], "confirmed-reference-silhouette-v3"
        )
        with self.post(
            "/calibrate-font-size",
            {
                "layout": layout_data(),
                "font_id": selected.candidate_id,
                "font_reference": self.font_reference(),
            },
        ) as response:
            scale = json.loads(response.read())
        self.assertEqual(scale["font_id"], selected.candidate_id)
        self.assertGreater(scale["font_size_px"], 0)
        self.assertGreater(scale["reference_horizontal_fill"], 0)
        self.assertEqual(
            scale["min_font_size_px"],
            min(
                scale["font_size_px"],
                recommend_min_font_size_for_capacity(
                    selected.font,
                    box_width=160,
                    box_height=50,
                    padding=4,
                    character_count=12,
                ),
            ),
        )
        with self.post("/preview", payload) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
        with self.post("/save", payload) as response:
            saved = json.loads(response.read())

        data = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(data["name"]["font"], "fonts/AmaticSC-Bold.ttf")
        self.assertEqual(saved["font_label"], "Amatic SC Bold")
        self.assertEqual(saved["font_id"], selected.candidate_id)
        self.assertTrue((self.root / "fonts" / "AmaticSC-Bold.ttf").is_file())
        self.assertEqual(
            (self.root / "fonts" / "OFL.txt").read_bytes(),
            selected.license.read_bytes(),
        )
        recommendation = json.loads(
            (self.root / "qa" / "font-recommendation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            recommendation["method"], "confirmed-reference-silhouette-v3"
        )
        self.assertEqual(len(recommendation["ranked_options"]), 15)
        self.assertTrue(
            all(
                0 <= option["confidence_score"] <= 1
                for option in recommendation["ranked_options"]
            )
        )
        self.assertEqual(recommendation["reference_scale"]["status"], "available")
        self.assertGreater(
            recommendation["reference_scale"]["font_size_px"], 0
        )
        font_reference = json.loads(
            (self.root / "qa" / "font-reference.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(font_reference["text"], "BUDDY")
        layout_reference = json.loads(
            (self.root / "qa" / "layout-reference.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(layout_reference["pet_region"]["y"], 30)

    def test_auto_font_defaults_to_best_of_fifteen_scored_recommendations(self) -> None:
        auto_dir = self.root / "auto"
        auto_dir.mkdir()
        auto_art = make_image(
            auto_dir / "art.png", size=(200, 300), color=(20, 30, 40, 255)
        )
        layout_reference_path = auto_dir / "input-layout-reference.json"
        layout_reference_path.write_text(
            json.dumps(
                layout_reference_from_editor(
                    reference=self.reference,
                    pet_region=self.layout_reference()["pet_region"],
                    name_region=self.layout_reference()["name_region"],
                ).to_dict()
            ),
            encoding="utf-8",
        )
        font_reference_path = auto_dir / "input-font-reference.json"
        font_reference_path.write_text(
            json.dumps(
                font_reference_from_editor(
                    reference=self.reference,
                    region=self.font_reference()["region"],
                    text=self.font_reference()["text"],
                ).to_dict()
            ),
            encoding="utf-8",
        )
        auto_server = create_server(
            EditorConfig(
                art=auto_art,
                reference=self.reference,
                pet=self.pet,
                pet_name="BUDDY",
                font=None,
                font_catalogs=(self.font_catalog,),
                font_reference=font_reference_path,
                layout_reference=layout_reference_path,
                output=auto_dir / "layout.json",
            )
        )
        thread = threading.Thread(target=auto_server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{auto_server.server_port}"
            with urllib.request.urlopen(base + "/") as response:
                html = response.read().decode("utf-8")
            prefix = "window.PAWMARVEL_BOOTSTRAP = "
            encoded = html.split(prefix, 1)[1].split(";</script>", 1)[0]
            bootstrap = json.loads(encoded)
            candidates = bootstrap["fontCandidates"]
            self.assertEqual(len(candidates), len(self.font_candidates))
            self.assertTrue(bootstrap["autoFont"])
            ranking = bootstrap["fontRanking"]
            self.assertIsNotNone(ranking)
            self.assertEqual(
                bootstrap["fontSelectionConfirmed"],
                ranking["recommendation"]["auto_select"],
            )
            self.assertEqual(
                bootstrap["selectedFontId"],
                ranking["recommendation"]["font_id"],
            )
            self.assertEqual(
                bootstrap["layout"]["name"]["font"],
                ranking["recommendation"]["font"],
            )
            selected = next(
                candidate
                for candidate in discover_font_catalog(
                    None, None, catalog_roots=(self.font_catalog,)
                )
                if candidate.candidate_id == bootstrap["selectedFontId"]
            )
            expected_scale = recommend_font_size(
                self.reference,
                font_reference_from_editor(
                    reference=self.reference,
                    region=self.font_reference()["region"],
                    text=self.font_reference()["text"],
                ),
                selected.font,
                box_width=bootstrap["layout"]["name"]["box"]["width"],
                box_height=bootstrap["layout"]["name"]["box"]["height"],
                padding=bootstrap["layout"]["name"]["padding_px"],
            )
            self.assertEqual(
                bootstrap["layout"]["name"]["font_size_px"],
                expected_scale.font_size_px,
            )
            self.assertEqual(
                bootstrap["layout"]["pet"]["box"],
                self.layout_reference()["pet_region"],
            )
            self.assertEqual(
                bootstrap["layout"]["name"]["box"],
                self.layout_reference()["name_region"],
            )
            request = urllib.request.Request(
                base + "/rank-fonts",
                data=json.dumps(
                    {"font_reference": self.font_reference()}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                ranking = json.loads(response.read())
            self.assertEqual(
                len(ranking["ranked_options"]), len(self.font_candidates)
            )
            self.assertIn(
                ranking["recommendation"]["confidence_level"],
                {"low", "medium", "high"},
            )
            payload = {
                "revision": 7,
                "pet_name": "BUDDY",
                "layout": bootstrap["layout"],
                "font_id": ranking["recommendation"]["font_id"],
                "font_reference": self.font_reference(),
            }
            preview_request = urllib.request.Request(
                base + "/preview",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(preview_request) as response:
                self.assertEqual(response.status, 200)
            save_request = urllib.request.Request(
                base + "/save",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(save_request)
            self.assertEqual(raised.exception.code, 400)
            raised.exception.close()
            payload["font_selection_confirmed"] = True
            save_request = urllib.request.Request(
                base + "/save",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(save_request) as response:
                self.assertEqual(response.status, 200)
            self.assertTrue((auto_dir / "qa" / "font-reference.json").is_file())
        finally:
            auto_server.shutdown()
            auto_server.server_close()
            thread.join(timeout=2)

    def test_close_endpoint_returns_control_to_server_caller(self) -> None:
        with self.post("/preview", {"layout": layout_data()}) as response:
            self.assertEqual(response.status, 200)
        with self.post("/save", {"layout": layout_data()}) as response:
            self.assertEqual(response.status, 200)
        with self.post("/close", {}) as response:
            self.assertEqual(response.status, 200)

        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        lifecycle = getattr(self.server, "pawmarvel_lifecycle")
        self.assertTrue(lifecycle.saved.is_set())
        self.assertTrue(lifecycle.close_requested.is_set())


if __name__ == "__main__":
    unittest.main()
