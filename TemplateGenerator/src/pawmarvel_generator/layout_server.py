from __future__ import annotations

import base64
import json
import mimetypes
import sys
import threading
import time
import webbrowser
from copy import deepcopy
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageFont, UnidentifiedImageError

from .cli import _atomic_write_bytes
from .config import ConfigError, Layout, load_layout, parse_layout, write_layout
from .font_catalog import FontCandidate, FontCatalogError, discover_font_catalog
from .expanded_font_catalog import ExpandedFontCatalogError, materialize_expanded_fonts
from .font_license import resolve_ofl_license
from .font_match import rank_fonts
from .renderer import RenderError, render_with_layout


MAX_REQUEST_BYTES = 1024 * 1024
STATIC_FILES = {
    "layout.js": "application/javascript; charset=utf-8",
    "layout.css": "text/css; charset=utf-8",
}
HEARTBEAT_TIMEOUT_SECONDS = 15.0


class _EditorLifecycle:
    def __init__(self) -> None:
        self.opened = threading.Event()
        self.saved = threading.Event()
        self.close_requested = threading.Event()
        self._lock = threading.Lock()
        self._last_seen = 0.0

    def touch(self) -> None:
        with self._lock:
            self._last_seen = time.monotonic()
        self.opened.set()

    def seconds_since_seen(self) -> float:
        with self._lock:
            last_seen = self._last_seen
        return time.monotonic() - last_seen


@dataclass(frozen=True)
class EditorConfig:
    art: Path
    reference: Path
    pet: Path
    pet_name: str
    font: Path | None
    output: Path
    font_license: Path | None = None
    font_catalogs: tuple[Path, ...] = ()
    runtime_model: str | None = "gpt-image-2"
    force: bool = False
    auto_font: bool = False
    font_catalog_mode: str = "local"
    font_index: Path | None = None
    font_cache: Path | None = None
    font_shortlist_limit: int = 24
    font_offline: bool = False

    @property
    def template_dir(self) -> Path:
        return self.output.parent

    @property
    def calibration_output(self) -> Path:
        return self.template_dir / "qa" / "calibration-preview.png"

def _validate_editor_config(
    config: EditorConfig,
) -> tuple[EditorConfig, tuple[FontCandidate, ...]]:
    auto_font = config.font is None
    font = config.font.expanduser().resolve() if config.font else None
    font_license = resolve_ofl_license(font, config.font_license) if font else None
    resolved = EditorConfig(
        art=config.art.expanduser().resolve(),
        reference=config.reference.expanduser().resolve(),
        pet=config.pet.expanduser().resolve(),
        pet_name=config.pet_name.strip(),
        font=font,
        output=config.output.expanduser().resolve(),
        font_license=font_license,
        font_catalogs=tuple(
            path.expanduser().resolve() for path in config.font_catalogs
        ),
        runtime_model=config.runtime_model,
        force=config.force,
        auto_font=auto_font,
        font_catalog_mode=config.font_catalog_mode,
        font_index=config.font_index.expanduser().resolve() if config.font_index else None,
        font_cache=config.font_cache.expanduser().resolve() if config.font_cache else None,
        font_shortlist_limit=config.font_shortlist_limit,
        font_offline=config.font_offline,
    )
    for path, label in (
        (resolved.art, "art"),
        (resolved.reference, "reference"),
        (resolved.pet, "pet"),
    ):
        if not path.is_file():
            raise ConfigError(f"{label} does not exist: {path}")
    if not resolved.pet_name:
        raise ConfigError("pet name must not be empty")
    if resolved.output.name != "layout.json":
        raise ConfigError("--output must end with layout.json")
    try:
        resolved.art.relative_to(resolved.template_dir)
    except ValueError as exc:
        raise ConfigError("art must be inside the layout output directory") from exc
    try:
        with Image.open(resolved.reference) as image:
            image.load()
        with Image.open(resolved.pet) as image:
            image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ConfigError(f"editor input cannot be decoded: {exc}") from exc
    additional_fonts: list[Path] = []
    if resolved.output.is_file():
        try:
            additional_fonts.append(
                load_layout(resolved.template_dir, resolved.output).font_path
            )
        except ConfigError:
            pass
    expanded_fonts: tuple[Path, ...] = ()
    if resolved.font_catalog_mode not in {"local", "expanded"}:
        raise ConfigError("font catalog mode must be local or expanded")
    if resolved.font_catalog_mode == "expanded":
        if resolved.font_index is None or resolved.font_cache is None:
            raise ConfigError("expanded font mode requires --font-index and --font-cache")
        try:
            expanded_fonts = materialize_expanded_fonts(
                resolved.font_index,
                resolved.font_cache,
                limit=resolved.font_shortlist_limit,
                offline=resolved.font_offline,
            )
        except ExpandedFontCatalogError as exc:
            if not resolved.font_catalogs and resolved.font is None:
                raise ConfigError(str(exc)) from exc
            print(f"Font catalog warning: {exc}; using local OFL catalog.", file=sys.stderr)
    try:
        candidates = discover_font_catalog(
            resolved.font,
            resolved.font_license,
            catalog_roots=resolved.font_catalogs,
            additional_fonts=(*additional_fonts, *expanded_fonts),
        )
    except FontCatalogError as exc:
        raise ConfigError(str(exc)) from exc
    if resolved.font is None:
        resolved = EditorConfig(**{**resolved.__dict__, "font": candidates[0].font, "font_license": candidates[0].license})
    return resolved, candidates


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _default_layout(config: EditorConfig) -> dict[str, Any]:
    with Image.open(config.art) as art:
        width, height = art.size
    result = {
        "schema_version": 1,
        "art": _relative(config.art, config.template_dir),
        "pet": {
            "box": {
                "x": round(width * 0.2),
                "y": round(height * 0.18),
                "width": round(width * 0.6),
                "height": round(height * 0.56),
            },
            "rotation_degrees": 0,
        },
        "name": {
            "box": {
                "x": round(width * 0.1),
                "y": round(height * 0.76),
                "width": round(width * 0.8),
                "height": round(height * 0.14),
            },
            "font": f"fonts/{config.font.name}",
            "font_size_px": max(12, round(height * 0.08)),
            "min_font_size_px": max(8, round(height * 0.03)),
            "color": "#F7E7C6FF",
            "horizontal_align": "center",
            "vertical_align": "middle",
        },
    }
    if config.runtime_model is not None:
        result["model"] = config.runtime_model
    return result


def _initial_layout(config: EditorConfig) -> dict[str, Any]:
    if config.output.is_file():
        try:
            data = load_layout(config.template_dir, config.output).to_dict()
        except ConfigError:
            data = json.loads(config.output.read_text(encoding="utf-8"))
            data = parse_layout(
                data,
                config.template_dir,
                art_override=config.art,
                font_override=config.font,
            ).to_dict()
        if config.runtime_model is None:
            data.pop("model", None)
        else:
            data["model"] = config.runtime_model
        return data
    return _default_layout(config)


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _candidate_for_layout(
    candidates: tuple[FontCandidate, ...], raw_layout: Mapping[str, Any]
) -> FontCandidate:
    name = raw_layout.get("name")
    configured = name.get("font") if isinstance(name, Mapping) else None
    configured_name = Path(configured).name if isinstance(configured, str) else None
    if configured_name is not None:
        for candidate in candidates:
            if candidate.font.name == configured_name:
                return candidate
    return candidates[0]


def _draft_layout(
    config: EditorConfig,
    candidates: tuple[FontCandidate, ...],
    payload: Mapping[str, Any],
) -> tuple[Layout, FontCandidate]:
    raw = payload.get("layout")
    if not isinstance(raw, Mapping):
        raise ConfigError("request must contain a layout object")
    draft = deepcopy(dict(raw))
    draft["art"] = _relative(config.art, config.template_dir)
    if config.runtime_model is None:
        draft.pop("model", None)
    else:
        draft["model"] = config.runtime_model
    name = draft.get("name")
    if not isinstance(name, dict):
        raise ConfigError("layout.name must be an object")
    requested_font = payload.get("font_id")
    if requested_font is None:
        candidate = _candidate_for_layout(candidates, draft)
    else:
        candidate = next(
            (
                value
                for value in candidates
                if value.candidate_id == requested_font
            ),
            None,
        )
        if candidate is None:
            raise ConfigError("selected font is not in the eligible OFL catalog")
    name["font"] = candidate.relative_name
    return (
        parse_layout(
            draft,
            config.template_dir,
            art_override=config.art,
            font_override=candidate.font,
        ),
        candidate,
    )


def _read_static(name: str) -> bytes:
    return (
        resources.files("pawmarvel_generator")
        .joinpath("static", name)
        .read_bytes()
    )


def _make_handler(
    config: EditorConfig,
    candidates: tuple[FontCandidate, ...],
    lifecycle: _EditorLifecycle,
) -> type[BaseHTTPRequestHandler]:
    with Image.open(config.art) as art_image:
        canvas = {"width": art_image.width, "height": art_image.height}
    initial_layout = _initial_layout(config)
    matches = rank_fonts(
        config.reference,
        initial_layout["name"]["box"],
        (canvas["width"], canvas["height"]),
        config.pet_name,
        candidates,
    )
    # An explicit --font is an override. Without it, the best visual match is
    # preselected even when an older layout contains a different font.
    selected_candidate = matches[0].candidate if config.auto_font else _candidate_for_layout(candidates, initial_layout)
    initial_layout["name"]["font"] = selected_candidate.relative_name
    top_matches = matches[:5]
    visible_matches = list(top_matches)
    if selected_candidate not in {match.candidate for match in top_matches}:
        visible_matches.append(
            next(match for match in matches if match.candidate == selected_candidate)
        )
    bootstrap = {
        "layout": initial_layout,
        "canvas": canvas,
        "petName": config.pet_name,
        "referenceDataUrl": _data_url(config.reference),
        "selectedFontId": selected_candidate.candidate_id,
        "fontCandidates": [
            {
                "id": candidate.candidate_id,
                "label": candidate.label,
                "relativeName": candidate.relative_name,
                "sha256": candidate.sha256,
                "matchScore": next(
                    match.score for match in matches if match.candidate == candidate
                ),
                "confidence": next(
                    match.confidence
                    for match in matches
                    if match.candidate == candidate
                ),
                "recommended": candidate == matches[0].candidate,
                "rank": next(
                    index
                    for index, match in enumerate(matches, 1)
                    if match.candidate == candidate
                ),
            }
            for candidate in (match.candidate for match in visible_matches)
        ],
        "fontRecommendation": {
            "method": "normalized_reference_visual_match_v2",
            "confidence": matches[0].confidence,
            "fontId": matches[0].candidate.candidate_id,
            "rankedFontIds": [match.candidate.candidate_id for match in matches],
        },
    }
    candidates_by_id = {
        candidate.candidate_id: candidate for candidate in candidates
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "PawMarvelLayout/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            print(f"layout editor: {format % args}", file=sys.stderr)

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json_error(self, status: int, message: str) -> None:
            self._send(
                status,
                "application/json; charset=utf-8",
                json.dumps({"error": message}).encode("utf-8"),
            )

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                lifecycle.touch()
                html = _read_static("layout.html").decode("utf-8")
                encoded = json.dumps(bootstrap).replace("</", "<\\/")
                html = html.replace("__PAWMARVEL_BOOTSTRAP__", encoded)
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", html.encode("utf-8"))
                return
            prefix = "/assets/"
            if self.path.startswith(prefix):
                lifecycle.touch()
                name = self.path[len(prefix) :]
                content_type = STATIC_FILES.get(name)
                if content_type:
                    self._send(HTTPStatus.OK, content_type, _read_static(name))
                    return
            font_prefix = "/fonts/"
            if self.path.startswith(font_prefix):
                lifecycle.touch()
                candidate_id = self.path[len(font_prefix) :]
                candidate = candidates_by_id.get(candidate_id)
                if candidate is not None:
                    self._send(HTTPStatus.OK, "font/ttf", candidate.font.read_bytes())
                    return
            self._json_error(HTTPStatus.NOT_FOUND, "not found")

        def _read_payload(self) -> Mapping[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ConfigError("invalid Content-Length") from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ConfigError("request body size is invalid")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ConfigError("request body must be UTF-8 JSON") from exc
            if not isinstance(payload, Mapping):
                raise ConfigError("request body must be an object")
            return payload

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/heartbeat":
                lifecycle.touch()
                self._send(HTTPStatus.NO_CONTENT, "text/plain", b"")
                return
            if self.path == "/close":
                lifecycle.close_requested.set()
                self._send(
                    HTTPStatus.OK,
                    "application/json; charset=utf-8",
                    b'{"closed": true}',
                )
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if self.path not in {"/preview", "/save"}:
                self._json_error(HTTPStatus.NOT_FOUND, "not found")
                return
            try:
                lifecycle.touch()
                payload = self._read_payload()
                layout, selected_font = _draft_layout(config, candidates, payload)
                if self.path == "/preview":
                    preview = render_with_layout(
                        layout,
                        config.pet,
                        config.pet_name,
                    )
                    self._send(HTTPStatus.OK, "image/png", _png_bytes(preview))
                    return

                overwrite = config.force or payload.get("overwrite") is True
                if (config.output.exists() or config.calibration_output.exists()) and not overwrite:
                    self._json_error(
                        HTTPStatus.CONFLICT,
                        "layout or calibration output exists; confirm overwrite",
                    )
                    return
                calibration = render_with_layout(
                    layout,
                    config.pet,
                    config.pet_name,
                    debug=True,
                )
                bundled_font = config.template_dir / selected_font.relative_name
                bundled_license = config.template_dir / "fonts" / "OFL.txt"
                bundled_font.parent.mkdir(parents=True, exist_ok=True)
                if selected_font.font != bundled_font:
                    _atomic_write_bytes(bundled_font, selected_font.font.read_bytes())
                bundled_license.parent.mkdir(parents=True, exist_ok=True)
                if selected_font.license != bundled_license:
                    _atomic_write_bytes(
                        bundled_license, selected_font.license.read_bytes()
                    )
                saved_layout = parse_layout(
                    layout.to_dict(),
                    config.template_dir,
                    art_override=config.art,
                    font_override=bundled_font,
                )
                write_layout(config.output, saved_layout)
                _atomic_write_bytes(config.calibration_output, _png_bytes(calibration))
                recommendation_output = config.template_dir / "qa" / "font-recommendation.json"
                recommendation = {
                    "method": "normalized_reference_visual_match_v2",
                    "recommended_font": matches[0].candidate.relative_name,
                    "confidence": matches[0].confidence,
                    "selected_font": selected_font.relative_name,
                    "confirmed": True,
                    "ranked_options": [
                        {
                            "rank": rank,
                            "font_id": match.candidate.candidate_id,
                            "label": match.candidate.label,
                            "font": match.candidate.relative_name,
                            "score": match.score,
                            "confidence": match.confidence,
                        }
                        for rank, match in enumerate(matches[:5], 1)
                    ],
                }
                _atomic_write_bytes(
                    recommendation_output,
                    (json.dumps(recommendation, indent=2) + "\n").encode("utf-8"),
                )
                lifecycle.saved.set()
                response = {
                    "layout": str(config.output),
                    "calibration": str(config.calibration_output),
                    "font": str(bundled_font),
                    "font_license": str(bundled_license),
                    "font_id": selected_font.candidate_id,
                    "font_label": selected_font.label,
                    "font_recommendation": str(recommendation_output),
                }
                self._send(
                    HTTPStatus.OK,
                    "application/json; charset=utf-8",
                    json.dumps(response).encode("utf-8"),
                )
            except (ConfigError, RenderError, OSError, ValueError) as exc:
                self._json_error(HTTPStatus.BAD_REQUEST, str(exc))

    return Handler


def create_server(
    config: EditorConfig, *, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    config, candidates = _validate_editor_config(config)
    lifecycle = _EditorLifecycle()
    server = ThreadingHTTPServer(
        (host, port), _make_handler(config, candidates, lifecycle)
    )
    setattr(server, "pawmarvel_lifecycle", lifecycle)
    return server


def serve_layout_editor(
    config: EditorConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
) -> None:
    server = create_server(config, host=host, port=port)
    lifecycle = getattr(server, "pawmarvel_lifecycle")
    url = f"http://{host}:{server.server_port}/"
    print(f"Layout editor: {url}", file=sys.stderr, flush=True)
    print(
        "Save the layout, then close the browser window or select "
        "Save & continue. Press Ctrl-C to cancel.",
        file=sys.stderr,
        flush=True,
    )
    if open_browser:
        webbrowser.open(url)

    def monitor_browser() -> None:
        while not lifecycle.close_requested.wait(1.0):
            if (
                lifecycle.opened.is_set()
                and lifecycle.seconds_since_seen() > HEARTBEAT_TIMEOUT_SECONDS
            ):
                print(
                    "Layout browser closed; stopping the editor.",
                    file=sys.stderr,
                    flush=True,
                )
                server.shutdown()
                return

    monitor = threading.Thread(
        target=monitor_browser,
        name="pawmarvel-layout-browser-monitor",
        daemon=True,
    )
    monitor.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        raise ConfigError("layout editor cancelled before completion")
    finally:
        lifecycle.close_requested.set()
        server.server_close()
        monitor.join(timeout=2)
    if not lifecycle.saved.is_set():
        raise ConfigError("layout editor closed before the layout was saved")
