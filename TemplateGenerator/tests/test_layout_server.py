from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from helpers import copy_font, layout_data, make_image
from pawmarvel_generator.font_catalog import discover_font_catalog
from pawmarvel_generator.layout_server import EditorConfig, create_server


class LayoutServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.art = make_image(
            self.root / "art.png", size=(200, 300), color=(20, 30, 40, 255)
        )
        self.reference = make_image(
            self.root / "reference.png", size=(200, 300), color=(30, 40, 50, 255)
        )
        self.pet = make_image(
            self.root / "pet.png", size=(80, 80), color=(200, 100, 50, 255)
        )
        external = self.root / "external"
        external.mkdir()
        self.font = copy_font(external)
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
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(request)

    def test_serves_packaged_editor_assets(self) -> None:
        with urllib.request.urlopen(self.base + "/") as response:
            html = response.read()
            self.assertIn(b"PawMarvel Layout Configurator", html)
            self.assertIn(b'"fontCandidates"', html)
            self.assertIn(b"Amatic SC Bold", html)
            self.assertIn(b"Save &amp; continue", html)
        with urllib.request.urlopen(self.base + "/assets/layout.js") as response:
            script = response.read()
            self.assertIn(b"requestPreview", script)
            self.assertIn(b"Top five font recommendations", script)
            self.assertIn(b"candidate.confidence", script)
            self.assertIn(b"/heartbeat", script)
            self.assertIn(b"pagehide", script)

        with self.post("/heartbeat", {}) as response:
            self.assertEqual(response.status, 204)

    def test_preview_and_save_use_shared_renderer(self) -> None:
        payload = {"layout": layout_data()}
        with self.post("/preview", payload) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertTrue(response.read().startswith(b"\x89PNG"))

        with self.post("/save", payload) as response:
            saved = json.loads(response.read())
        self.assertEqual(Path(saved["layout"]), self.output.resolve())
        self.assertTrue(self.output.is_file())
        self.assertTrue((self.root / "fonts" / "TestFont.ttf").is_file())
        self.assertTrue((self.root / "fonts" / "OFL.txt").is_file())
        self.assertEqual(json.loads(self.output.read_text())["model"], "gpt-image-2")
        self.assertTrue((self.root / "qa" / "calibration-preview.png").is_file())

    def test_selected_catalog_font_is_previewed_and_saved(self) -> None:
        selected = next(
            candidate
            for candidate in self.font_candidates
            if candidate.font.name == "AmaticSC-Bold.ttf"
        )
        payload = {
            "layout": layout_data(),
            "font_id": selected.candidate_id,
        }

        with urllib.request.urlopen(
            f"{self.base}/fonts/{selected.candidate_id}"
        ) as response:
            self.assertEqual(response.headers.get_content_type(), "font/ttf")
            self.assertEqual(response.read(), selected.font.read_bytes())
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
            recommendation["method"], "normalized_reference_visual_match_v2"
        )
        self.assertEqual(len(recommendation["ranked_options"]), 5)
        self.assertTrue(
            all(
                0 <= option["confidence"] <= 0.99
                for option in recommendation["ranked_options"]
            )
        )

    def test_auto_font_defaults_to_best_of_five_scored_recommendations(self) -> None:
        auto_dir = self.root / "auto"
        auto_dir.mkdir()
        auto_art = make_image(
            auto_dir / "art.png", size=(200, 300), color=(20, 30, 40, 255)
        )
        auto_server = create_server(
            EditorConfig(
                art=auto_art,
                reference=self.reference,
                pet=self.pet,
                pet_name="BUDDY",
                font=None,
                font_catalogs=(self.font_catalog,),
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
            self.assertEqual(len(candidates), 5)
            self.assertEqual(
                bootstrap["selectedFontId"],
                bootstrap["fontRecommendation"]["fontId"],
            )
            self.assertTrue(candidates[0]["recommended"])
            self.assertEqual(candidates[0]["rank"], 1)
            self.assertTrue(all("confidence" in value for value in candidates))
        finally:
            auto_server.shutdown()
            auto_server.server_close()
            thread.join(timeout=2)

    def test_close_endpoint_returns_control_to_server_caller(self) -> None:
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
