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
from .renderer import RenderError, render_with_layout, validate_name_image


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
    font: Path
    output: Path
    name_image: Path | None = None
    force: bool = False

    @property
    def template_dir(self) -> Path:
        return self.output.parent

    @property
    def calibration_output(self) -> Path:
        return self.template_dir / "qa" / "calibration-preview.png"

    @property
    def bundled_font(self) -> Path:
        return self.template_dir / "fonts" / self.font.name


def _validate_editor_config(config: EditorConfig) -> EditorConfig:
    resolved = EditorConfig(
        art=config.art.expanduser().resolve(),
        reference=config.reference.expanduser().resolve(),
        pet=config.pet.expanduser().resolve(),
        pet_name=config.pet_name.strip(),
        font=config.font.expanduser().resolve(),
        output=config.output.expanduser().resolve(),
        name_image=(
            config.name_image.expanduser().resolve() if config.name_image else None
        ),
        force=config.force,
    )
    for path, label in (
        (resolved.art, "art"),
        (resolved.reference, "reference"),
        (resolved.pet, "pet"),
        (resolved.font, "font"),
    ):
        if not path.is_file():
            raise ConfigError(f"{label} does not exist: {path}")
    if not resolved.pet_name:
        raise ConfigError("pet name must not be empty")
    if resolved.name_image is not None:
        if not resolved.name_image.is_file():
            raise ConfigError(f"name image does not exist: {resolved.name_image}")
        try:
            validate_name_image(resolved.name_image)
        except RenderError as exc:
            raise ConfigError(str(exc)) from exc
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
        ImageFont.truetype(str(resolved.font), size=12)
    except (UnidentifiedImageError, OSError) as exc:
        raise ConfigError(f"editor input cannot be decoded: {exc}") from exc
    return resolved


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _default_layout(config: EditorConfig) -> dict[str, Any]:
    with Image.open(config.art) as art:
        width, height = art.size
    return {
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


def _initial_layout(config: EditorConfig) -> dict[str, Any]:
    if config.output.is_file():
        try:
            return load_layout(config.template_dir, config.output).to_dict()
        except ConfigError:
            data = json.loads(config.output.read_text(encoding="utf-8"))
            return parse_layout(
                data,
                config.template_dir,
                art_override=config.art,
                font_override=config.font,
            ).to_dict()
    return _default_layout(config)


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _draft_layout(config: EditorConfig, payload: Mapping[str, Any]) -> Layout:
    raw = payload.get("layout")
    if not isinstance(raw, Mapping):
        raise ConfigError("request must contain a layout object")
    draft = deepcopy(dict(raw))
    draft["art"] = _relative(config.art, config.template_dir)
    name = draft.get("name")
    if not isinstance(name, dict):
        raise ConfigError("layout.name must be an object")
    name["font"] = f"fonts/{config.font.name}"
    return parse_layout(
        draft,
        config.template_dir,
        art_override=config.art,
        font_override=config.font,
    )


def _read_static(name: str) -> bytes:
    return (
        resources.files("pawmarvel_generator")
        .joinpath("static", name)
        .read_bytes()
    )


def _make_handler(
    config: EditorConfig, lifecycle: _EditorLifecycle
) -> type[BaseHTTPRequestHandler]:
    with Image.open(config.art) as art_image:
        canvas = {"width": art_image.width, "height": art_image.height}
    bootstrap = {
        "layout": _initial_layout(config),
        "canvas": canvas,
        "petName": config.pet_name,
        "nameMode": "image" if config.name_image else "font",
        "referenceDataUrl": _data_url(config.reference),
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
                layout = _draft_layout(config, payload)
                if self.path == "/preview":
                    preview = render_with_layout(
                        layout,
                        config.pet,
                        config.pet_name,
                        name_image=config.name_image,
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
                    name_image=config.name_image,
                    debug=True,
                )
                config.bundled_font.parent.mkdir(parents=True, exist_ok=True)
                if config.font != config.bundled_font:
                    _atomic_write_bytes(config.bundled_font, config.font.read_bytes())
                saved_layout = parse_layout(
                    layout.to_dict(),
                    config.template_dir,
                    art_override=config.art,
                    font_override=config.bundled_font,
                )
                write_layout(config.output, saved_layout)
                _atomic_write_bytes(config.calibration_output, _png_bytes(calibration))
                lifecycle.saved.set()
                response = {
                    "layout": str(config.output),
                    "calibration": str(config.calibration_output),
                    "font": str(config.bundled_font),
                    "name_mode": "image" if config.name_image else "font",
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
    config = _validate_editor_config(config)
    lifecycle = _EditorLifecycle()
    server = ThreadingHTTPServer((host, port), _make_handler(config, lifecycle))
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
