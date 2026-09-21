from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import shutil
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from copy import deepcopy
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote

from PIL import Image, UnidentifiedImageError

from .cli import _atomic_write_bytes
from .config import ConfigError, Layout, load_layout, parse_layout, write_layout
from .font_catalog import FontCandidate, FontCatalogError, discover_font_catalog
from .font_license import resolve_ofl_license
from .font_match import (
    rank_fonts,
    recommend_font_size,
    recommend_min_font_size_for_capacity,
)
from .font_reference import (
    FontReference,
    FontReferenceError,
    font_reference_from_editor,
    load_font_reference,
)
from .layout_reference import (
    LayoutReference,
    LayoutReferenceError,
    layout_reference_from_editor,
    load_layout_reference,
    map_reference_box,
)
from .personalization import (
    DEFAULT_MAX_NAME_CODE_POINTS,
    PersonalizationError,
    pet_name_policy,
    validate_pet_name,
)
from .renderer import RenderError, render_composition
from .remote_fonts import (
    RemoteFontError,
    import_google_ofl_family,
    normalize_google_font_family,
    search_google_ofl,
)


MAX_REQUEST_BYTES = 1024 * 1024
MAX_PREVIEW_PET_BYTES = 25 * 1024 * 1024
MAX_PREVIEW_PETS = 12
MAX_FONT_QUERY_LENGTH = 80
FONT_RECOMMENDATION_LIMIT = 15
STATIC_FILES = {
    "layout.js": "application/javascript; charset=utf-8",
    "layout.css": "text/css; charset=utf-8",
}
HEARTBEAT_TIMEOUT_SECONDS = 15.0


class _LayoutHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        remote_font_temp: tempfile.TemporaryDirectory[str],
    ) -> None:
        self._remote_font_temp = remote_font_temp
        super().__init__(server_address, handler)

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self._remote_font_temp.cleanup()


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
    font: Path | None
    output: Path
    pet_name: str = "PET"
    font_license: Path | None = None
    font_catalogs: tuple[Path, ...] = ()
    font_reference: Path | None = None
    layout_reference: Path | None = None
    reference_text: str | None = None
    force: bool = False
    auto_font: bool = False

    @property
    def template_dir(self) -> Path:
        return self.output.parent

    @property
    def calibration_output(self) -> Path:
        return self.template_dir / "qa" / "calibration-preview.png"

    @property
    def calibration_fixture_output(self) -> Path:
        return self.template_dir / "qa" / "calibration-fixture.json"

    @property
    def font_reference_output(self) -> Path:
        return self.template_dir / "qa" / "font-reference.json"

    @property
    def layout_reference_output(self) -> Path:
        return self.template_dir / "qa" / "layout-reference.json"


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
        pet_name=config.pet_name.strip() or "PET",
        font=font,
        output=config.output.expanduser().resolve(),
        font_license=font_license,
        font_catalogs=tuple(
            path.expanduser().resolve() for path in config.font_catalogs
        ),
        font_reference=(
            config.font_reference.expanduser().resolve()
            if config.font_reference
            else None
        ),
        layout_reference=(
            config.layout_reference.expanduser().resolve()
            if config.layout_reference
            else None
        ),
        reference_text=(config.reference_text or "").strip() or None,
        force=config.force,
        auto_font=auto_font,
    )
    for path, label in (
        (resolved.art, "art"),
        (resolved.reference, "reference"),
        (resolved.pet, "pet"),
    ):
        if not path.is_file():
            raise ConfigError(f"{label} does not exist: {path}")
    if resolved.output.name != "layout.json":
        raise ConfigError("--output must end with layout.json")
    if resolved.reference_text is not None and len(resolved.reference_text) > 64:
        raise ConfigError("reference text must not exceed 64 characters")
    if resolved.font_reference is not None:
        try:
            load_font_reference(resolved.font_reference, resolved.reference)
        except FontReferenceError as exc:
            raise ConfigError(str(exc)) from exc
    if resolved.layout_reference is not None:
        try:
            layout_reference = load_layout_reference(
                resolved.layout_reference, resolved.reference
            )
        except LayoutReferenceError as exc:
            raise ConfigError(str(exc)) from exc
        if resolved.font_reference is not None:
            font_reference = load_font_reference(
                resolved.font_reference, resolved.reference
            )
            if layout_reference.name_region.to_dict() != font_reference.region.to_dict():
                raise ConfigError(
                    "layout reference name_region must match font reference region"
                )
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
    try:
        candidates = discover_font_catalog(
            resolved.font,
            resolved.font_license,
            catalog_roots=resolved.font_catalogs,
            additional_fonts=additional_fonts,
        )
    except FontCatalogError as exc:
        raise ConfigError(str(exc)) from exc
    if resolved.font is None:
        resolved = EditorConfig(**{**resolved.__dict__, "font": candidates[0].font, "font_license": candidates[0].license})
    return resolved, candidates


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _default_layout(
    config: EditorConfig,
    font_reference: FontReference | None,
    layout_reference: LayoutReference | None,
) -> dict[str, Any]:
    with Image.open(config.art) as art:
        width, height = art.size
    with Image.open(config.reference) as reference:
        reference_size = reference.size
    if layout_reference is not None:
        pet_box = map_reference_box(
            layout_reference.pet_region,
            reference_size=reference_size,
            canvas_size=(width, height),
        ).to_dict()
        name_box = map_reference_box(
            layout_reference.name_region,
            reference_size=reference_size,
            canvas_size=(width, height),
        ).to_dict()
    else:
        pet_box = {
            "x": round(width * 0.2),
            "y": round(height * 0.18),
            "width": round(width * 0.6),
            "height": round(height * 0.56),
        }
        if font_reference is not None:
            name_box = map_reference_box(
                font_reference.region,
                reference_size=reference_size,
                canvas_size=(width, height),
            ).to_dict()
        else:
            name_box = {
                "x": round(width * 0.1),
                "y": round(height * 0.76),
                "width": round(width * 0.8),
                "height": round(height * 0.14),
            }
    name_height = name_box["height"]
    name_padding = min(4, max(0, (name_height - 1) // 2))
    assert config.font is not None
    minimum_font_size = recommend_min_font_size_for_capacity(
        config.font,
        box_width=name_box["width"],
        box_height=name_height,
        padding=name_padding,
        character_count=DEFAULT_MAX_NAME_CODE_POINTS,
    )
    result = {
        "schema_version": 2,
        "art": _relative(config.art, config.template_dir),
        "pet": {
            "box": pet_box,
        },
        "name": {
            "box": name_box,
            "font": f"fonts/{config.font.name}",
            "font_size_px": name_height,
            "min_font_size_px": min(name_height, minimum_font_size),
            "fit": "shrink_only",
            "padding_px": name_padding,
            "color": "#F7E7C6FF",
            "horizontal_align": "center",
        },
    }
    return result


def _initial_layout(
    config: EditorConfig,
    font_reference: FontReference | None,
    layout_reference: LayoutReference | None,
) -> dict[str, Any]:
    if config.output.is_file():
        try:
            return load_layout(config.template_dir, config.output).to_dict()
        except ConfigError as exc:
            if config.force:
                return _default_layout(config, font_reference, layout_reference)
            raise ConfigError(
                f"existing layout cannot be reopened: {config.output}; {exc}. "
                "Fix the file or pass --force to start a new layout."
            ) from exc
    return _default_layout(config, font_reference, layout_reference)


def _initial_font_reference(config: EditorConfig) -> FontReference | None:
    source = config.font_reference
    if source is None and config.font_reference_output.is_file():
        source = config.font_reference_output
    if source is None:
        return None
    try:
        return load_font_reference(source, config.reference)
    except FontReferenceError as exc:
        raise ConfigError(str(exc)) from exc


def _initial_layout_reference(config: EditorConfig) -> LayoutReference | None:
    source = config.layout_reference
    if source is None and config.layout_reference_output.is_file():
        source = config.layout_reference_output
    if source is None:
        return None
    try:
        return load_layout_reference(source, config.reference)
    except LayoutReferenceError as exc:
        raise ConfigError(str(exc)) from exc


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _request_revision(payload: Mapping[str, Any]) -> int:
    value = payload.get("revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError("request revision must be a non-negative integer")
    return value


def _request_pet_name(payload: Mapping[str, Any]) -> str:
    try:
        return validate_pet_name(payload.get("pet_name"), pet_name_policy(64))
    except PersonalizationError as exc:
        raise ConfigError(str(exc)) from exc


def _request_font_reference(
    payload: Mapping[str, Any], reference: Path
) -> FontReference:
    value = payload.get("font_reference")
    if not isinstance(value, Mapping):
        raise ConfigError("request must contain a font_reference object")
    try:
        return font_reference_from_editor(
            reference=reference,
            region=value.get("region"),
            text=value.get("text"),
        )
    except FontReferenceError as exc:
        raise ConfigError(str(exc)) from exc


def _request_layout_reference(
    payload: Mapping[str, Any], reference: Path
) -> LayoutReference:
    value = payload.get("layout_reference")
    if not isinstance(value, Mapping):
        raise ConfigError("request must contain a layout_reference object")
    try:
        return layout_reference_from_editor(
            reference=reference,
            pet_region=value.get("pet_region"),
            name_region=value.get("name_region"),
        )
    except LayoutReferenceError as exc:
        raise ConfigError(str(exc)) from exc


def _font_reference_fingerprint(font_reference: FontReference) -> str:
    canonical = json.dumps(
        font_reference.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _ranking_response(
    font_reference: FontReference,
    matches: tuple[Any, ...],
) -> dict[str, Any]:
    top = matches[0]
    return {
        "schema_version": 1,
        "method": "confirmed-reference-silhouette-v3",
        "font_reference": font_reference.to_dict(),
        "recommendation": {
            "font_id": top.candidate.candidate_id,
            "font": top.candidate.relative_name,
            "similarity_score": top.score,
            "confidence_score": top.confidence,
            "confidence_level": top.confidence_level,
            "auto_select": top.confidence_level == "high",
        },
        "ranked_options": [
            {
                "rank": rank,
                "font_id": match.candidate.candidate_id,
                "label": match.candidate.label,
                "font": match.candidate.relative_name,
                "similarity_score": match.score,
                "confidence_score": match.confidence,
                "confidence_level": match.confidence_level,
            }
            for rank, match in enumerate(matches, 1)
        ],
    }


def _preview_fingerprint(
    layout: Layout, selected_font: FontCandidate, pet_name: str,
    pet_sha256: str,
) -> str:
    canonical = json.dumps(
        {
            "layout": layout.to_dict(),
            "font_id": selected_font.candidate_id,
            "pet_name": pet_name,
            "pet_sha256": pet_sha256,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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
    draft.pop("model", None)
    draft["schema_version"] = 2
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
    remote_font_root: Path,
) -> type[BaseHTTPRequestHandler]:
    with Image.open(config.art) as art_image:
        canvas = {"width": art_image.width, "height": art_image.height}
    with Image.open(config.reference) as reference_image:
        reference_canvas = {
            "width": reference_image.width,
            "height": reference_image.height,
        }
    initial_font_reference = _initial_font_reference(config)
    initial_layout_reference = _initial_layout_reference(config)
    initial_layout = _initial_layout(
        config, initial_font_reference, initial_layout_reference
    )
    selected_candidate = _candidate_for_layout(candidates, initial_layout)
    initial_ranking: dict[str, Any] | None = None
    if initial_font_reference is not None:
        try:
            matches = rank_fonts(
                config.reference, initial_font_reference, candidates
            )
            initial_ranking = _ranking_response(initial_font_reference, matches)
        except ValueError:
            # Keep the editor available so the operator can correct an invalid
            # or visually ambiguous reference region in the browser.
            initial_ranking = None
    if (
        config.auto_font
        and not config.output.is_file()
        and initial_ranking
        and initial_ranking["recommendation"]["confidence_score"] > 0
    ):
        selected_candidate = next(
            candidate
            for candidate in candidates
            if candidate.candidate_id
            == initial_ranking["recommendation"]["font_id"]
        )
        name = initial_layout["name"]
        box = name["box"]
        try:
            scale = recommend_font_size(
                config.reference,
                initial_font_reference,
                selected_candidate.font,
                box_width=box["width"],
                box_height=box["height"],
                padding=name["padding_px"],
            )
        except ValueError:
            # A blank/ambiguous reference region must not prevent the operator
            # from opening the editor and redrawing it.
            initial_ranking = None
        else:
            name["font_size_px"] = scale.font_size_px
            name["min_font_size_px"] = min(
                scale.font_size_px,
                recommend_min_font_size_for_capacity(
                    selected_candidate.font,
                    box_width=box["width"],
                    box_height=box["height"],
                    padding=name["padding_px"],
                    character_count=DEFAULT_MAX_NAME_CODE_POINTS,
                ),
            )
    initial_layout["name"]["font"] = selected_candidate.relative_name
    initial_font_confirmed = (
        not config.auto_font
        or config.output.is_file()
        or bool(
            initial_ranking
            and initial_ranking["recommendation"]["auto_select"]
        )
    )
    bootstrap = {
        "layout": initial_layout,
        "canvas": canvas,
        "petName": config.pet_name,
        "referenceDataUrl": _data_url(config.reference),
        "referenceCanvas": reference_canvas,
        "fontReference": (
            initial_font_reference.to_dict() if initial_font_reference else None
        ),
        "layoutReference": (
            initial_layout_reference.to_dict()
            if initial_layout_reference
            else None
        ),
        "referenceText": (
            initial_font_reference.text
            if initial_font_reference
            else config.reference_text or ""
        ),
        "autoFont": config.auto_font,
        "fontSelectionConfirmed": initial_font_confirmed,
        "fontRanking": initial_ranking,
        "selectedFontId": selected_candidate.candidate_id,
        "fontCandidates": [
            {
                "id": candidate.candidate_id,
                "label": candidate.label,
                "relativeName": candidate.relative_name,
                "sha256": candidate.sha256,
            }
            for candidate in candidates
        ],
    }
    candidates_by_id = {
        candidate.candidate_id: candidate for candidate in candidates
    }
    remote_font_sources: dict[str, dict[str, Any]] = {}
    font_candidates_lock = threading.Lock()

    for candidate in candidates:
        source_path = candidate.font.parent / "source.json"
        metadata_path = candidate.font.parent / "METADATA.pb"
        if not source_path.is_file() or not metadata_path.is_file():
            continue
        try:
            source = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(source, dict)
            and source.get("source") == "google-fonts-ofl"
            and source.get("font_filename") == candidate.font.name
            and source.get("font_sha256") == candidate.sha256
            and source.get("license_sha256")
            == hashlib.sha256(candidate.license.read_bytes()).hexdigest()
            and source.get("metadata_sha256")
            == hashlib.sha256(metadata_path.read_bytes()).hexdigest()
        ):
            remote_font_sources[candidate.candidate_id] = {
                **source,
                "metadata_path": str(metadata_path),
            }

    def candidate_snapshot() -> tuple[FontCandidate, ...]:
        with font_candidates_lock:
            return tuple(candidates_by_id.values())
    previewed_revisions: dict[int, str] = {}
    preview_lock = threading.Lock()
    pinned_pet_sha256 = hashlib.sha256(config.pet.read_bytes()).hexdigest()
    uploaded_preview_pets: dict[str, tuple[bytes, dict[str, Any]]] = {}
    tested_preview_pets: dict[str, dict[str, Any]] = {}
    preview_pet_lock = threading.Lock()
    font_rankings: dict[str, dict[str, Any]] = {}
    if initial_font_reference is not None and initial_ranking is not None:
        font_rankings[
            _font_reference_fingerprint(initial_font_reference)
        ] = initial_ranking
    ranking_lock = threading.Lock()

    def selected_preview_pet(
        payload: Mapping[str, Any],
    ) -> tuple[Path | BytesIO, dict[str, Any]]:
        pet_id = payload.get("preview_pet_id", "pinned")
        if pet_id == "pinned":
            return config.pet, {
                "id": "pinned",
                "label": config.pet.name,
                "sha256": pinned_pet_sha256,
                "source": "layout-experiment",
            }
        with preview_pet_lock:
            selected = (
                uploaded_preview_pets.get(pet_id)
                if isinstance(pet_id, str)
                else None
            )
        if selected is None:
            raise ConfigError(
                "selected preview pet is unavailable; choose the pinned pet or upload it again"
            )
        content, descriptor = selected
        return BytesIO(content), descriptor

    class Handler(BaseHTTPRequestHandler):
        server_version = "PawMarvelLayout/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            print(f"layout editor: {format % args}", file=sys.stderr)

        def _send(
            self,
            status: int,
            content_type: str,
            body: bytes,
            *,
            headers: Mapping[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json_error(
            self, status: int, message: str, *, code: str | None = None
        ) -> None:
            payload = {"error": message}
            if code is not None:
                payload["code"] = code
            self._send(
                status,
                "application/json; charset=utf-8",
                json.dumps(payload).encode("utf-8"),
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
                with font_candidates_lock:
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
            if self.path == "/preview-pet":
                try:
                    lifecycle.touch()
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError as exc:
                        raise ConfigError("invalid preview-pet Content-Length") from exc
                    if length <= 0 or length > MAX_PREVIEW_PET_BYTES:
                        raise ConfigError(
                            f"preview pet must be between 1 byte and "
                            f"{MAX_PREVIEW_PET_BYTES} bytes"
                        )
                    raw = self.rfile.read(length)
                    with Image.open(BytesIO(raw)) as source:
                        source.load()
                        rgba = source.convert("RGBA")
                    alpha_min, alpha_max = rgba.getchannel("A").getextrema()
                    if alpha_max == 0:
                        raise ConfigError("preview pet is fully transparent")
                    if alpha_min == 255:
                        raise ConfigError(
                            "preview pet must contain transparency; upload a transformed pet-only image"
                        )
                    normalized = BytesIO()
                    rgba.save(normalized, format="PNG")
                    content = normalized.getvalue()
                    digest = hashlib.sha256(content).hexdigest()
                    pet_id = f"upload-{digest}"
                    uploaded_label = unquote(
                        self.headers.get("X-PawMarvel-Pet-Name", "")
                    ).strip()
                    if (
                        not uploaded_label
                        or len(uploaded_label) > 255
                        or any(ord(character) < 32 for character in uploaded_label)
                    ):
                        uploaded_label = f"Uploaded pet {len(uploaded_preview_pets) + 1}"
                    with preview_pet_lock:
                        descriptor = {
                            "id": pet_id,
                            "label": uploaded_label,
                            "sha256": digest,
                            "source": "browser-upload",
                        }
                        uploaded_preview_pets[pet_id] = (content, descriptor)
                        while len(uploaded_preview_pets) > MAX_PREVIEW_PETS:
                            uploaded_preview_pets.pop(next(iter(uploaded_preview_pets)))
                    self._send(
                        HTTPStatus.OK,
                        "application/json; charset=utf-8",
                        json.dumps(descriptor).encode("utf-8"),
                    )
                except (ConfigError, UnidentifiedImageError, OSError) as exc:
                    self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if self.path == "/search-fonts":
                try:
                    lifecycle.touch()
                    payload = self._read_payload()
                    query = payload.get("query")
                    if not isinstance(query, str) or not query.strip():
                        raise ConfigError("font search query must not be empty")
                    query = query.strip()
                    if len(query) > MAX_FONT_QUERY_LENGTH:
                        raise ConfigError(
                            f"font search query must not exceed {MAX_FONT_QUERY_LENGTH} characters"
                        )
                    normalized = "".join(
                        character for character in query.lower() if character.isalnum()
                    )
                    local = []
                    exact_local = False
                    for candidate in candidate_snapshot():
                        searchable = {
                            "".join(
                                character
                                for character in value.lower()
                                if character.isalnum()
                            )
                            for value in (
                                candidate.label,
                                candidate.font.stem,
                                candidate.font.parent.name,
                            )
                        }
                        if any(normalized in value for value in searchable):
                            local.append(
                                {
                                    "source": "local",
                                    "font_id": candidate.candidate_id,
                                    "label": candidate.label,
                                }
                            )
                        exact_local = exact_local or normalized in searchable
                    local = local[:12]
                    remote = (
                        []
                        if exact_local
                        else [
                            family.to_dict() for family in search_google_ofl(query)
                        ]
                    )
                    self._send(
                        HTTPStatus.OK,
                        "application/json; charset=utf-8",
                        json.dumps(
                            {"query": query, "local": local, "remote": remote}
                        ).encode("utf-8"),
                    )
                except ConfigError as exc:
                    self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
                except RemoteFontError as exc:
                    self._json_error(
                        HTTPStatus.BAD_GATEWAY,
                        "remote OFL font search failed for "
                        f"{locals().get('query')!r}: {exc}",
                        code="remote_font_search_failed",
                    )
                except Exception as exc:
                    traceback.print_exception(type(exc), exc, exc.__traceback__)
                    self._json_error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        f"font search failed unexpectedly for {locals().get('query')!r}; "
                        "see the layout CLI terminal for details",
                        code="font_search_internal_error",
                    )
                return
            if self.path == "/import-font":
                try:
                    lifecycle.touch()
                    payload = self._read_payload()
                    family_id = payload.get("family_id")
                    if not isinstance(family_id, str):
                        raise ConfigError("font import requires family_id")
                    family_id = normalize_google_font_family(family_id)
                    family_dir = remote_font_root / family_id
                    if family_dir.exists():
                        raise ConfigError(
                            "font family is already imported in this editor session"
                        )
                    imported = import_google_ofl_family(family_id, remote_font_root)
                    added = []
                    with font_candidates_lock:
                        for candidate in imported.candidates:
                            existing = candidates_by_id.get(candidate.candidate_id)
                            if existing is None:
                                candidates_by_id[candidate.candidate_id] = candidate
                                remote_font_sources[candidate.candidate_id] = {
                                    "schema_version": 1,
                                    "source": "google-fonts-ofl",
                                    "family_id": imported.family.family_id,
                                    "family": imported.family.label,
                                    "source_url": imported.source_url,
                                    "font_filename": candidate.font.name,
                                    "font_sha256": candidate.sha256,
                                    "license_sha256": hashlib.sha256(
                                        candidate.license.read_bytes()
                                    ).hexdigest(),
                                    "metadata_sha256": hashlib.sha256(
                                        imported.metadata.read_bytes()
                                    ).hexdigest(),
                                    "metadata_path": str(imported.metadata),
                                }
                                existing = candidate
                            added.append(
                                {
                                    "id": existing.candidate_id,
                                    "label": existing.label,
                                    "relativeName": existing.relative_name,
                                    "sha256": existing.sha256,
                                    "source": "google-fonts-ofl",
                                }
                            )
                    self._send(
                        HTTPStatus.OK,
                        "application/json; charset=utf-8",
                        json.dumps(
                            {
                                "family_id": imported.family.family_id,
                                "family": imported.family.label,
                                "candidates": added,
                            }
                        ).encode("utf-8"),
                    )
                except RemoteFontError as exc:
                    if 'family_dir' in locals() and family_dir.exists():
                        shutil.rmtree(family_dir, ignore_errors=True)
                    self._json_error(
                        HTTPStatus.BAD_GATEWAY,
                        f"remote OFL font import failed: {exc}",
                        code="remote_font_import_failed",
                    )
                except (ConfigError, FontCatalogError, OSError) as exc:
                    if 'family_dir' in locals() and family_dir.exists():
                        shutil.rmtree(family_dir, ignore_errors=True)
                    self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if self.path not in {
                "/preview",
                "/save",
                "/rank-fonts",
                "/calibrate-font-size",
            }:
                self._json_error(HTTPStatus.NOT_FOUND, "not found")
                return
            try:
                lifecycle.touch()
                payload = self._read_payload()
                if self.path == "/rank-fonts":
                    font_reference = _request_font_reference(
                        payload, config.reference
                    )
                    matches = rank_fonts(
                        config.reference, font_reference, candidate_snapshot()
                    )
                    ranking = _ranking_response(font_reference, matches)
                    with ranking_lock:
                        font_rankings[
                            _font_reference_fingerprint(font_reference)
                        ] = ranking
                        while len(font_rankings) > 20:
                            font_rankings.pop(next(iter(font_rankings)))
                    self._send(
                        HTTPStatus.OK,
                        "application/json; charset=utf-8",
                        json.dumps(ranking).encode("utf-8"),
                    )
                    return
                if self.path == "/calibrate-font-size":
                    font_reference = _request_font_reference(
                        payload, config.reference
                    )
                    layout, selected_font = _draft_layout(
                        config, candidate_snapshot(), payload
                    )
                    recommendation = recommend_font_size(
                        config.reference,
                        font_reference,
                        selected_font.font,
                        box_width=layout.name_box.width,
                        box_height=layout.name_box.height,
                        padding=layout.name_padding_px,
                    )
                    recommended_minimum = min(
                        recommendation.font_size_px,
                        recommend_min_font_size_for_capacity(
                            selected_font.font,
                            box_width=layout.name_box.width,
                            box_height=layout.name_box.height,
                            padding=layout.name_padding_px,
                            character_count=DEFAULT_MAX_NAME_CODE_POINTS,
                        ),
                    )
                    self._send(
                        HTTPStatus.OK,
                        "application/json; charset=utf-8",
                        json.dumps(
                            {
                                **recommendation.to_dict(),
                                "min_font_size_px": recommended_minimum,
                                "font_id": selected_font.candidate_id,
                            }
                        ).encode("utf-8"),
                    )
                    return
                revision = _request_revision(payload)
                pet_name = _request_pet_name(payload)
                layout, selected_font = _draft_layout(
                    config, candidate_snapshot(), payload
                )
                preview_pet, preview_pet_descriptor = selected_preview_pet(payload)
                fingerprint = _preview_fingerprint(
                    layout,
                    selected_font,
                    pet_name,
                    str(preview_pet_descriptor["sha256"]),
                )
                if self.path == "/preview":
                    preview = render_composition(
                        layout,
                        preview_pet,
                        pet_name,
                    )
                    with preview_pet_lock:
                        tested_preview_pets[
                            str(preview_pet_descriptor["sha256"])
                        ] = preview_pet_descriptor
                    with preview_lock:
                        previewed_revisions[revision] = fingerprint
                        while len(previewed_revisions) > 20:
                            previewed_revisions.pop(next(iter(previewed_revisions)))
                    self._send(
                        HTTPStatus.OK,
                        "image/png",
                        _png_bytes(preview.image),
                        headers={
                            "X-PawMarvel-Preview-Revision": str(revision),
                            "X-PawMarvel-Applied-Font-Size": str(
                                preview.text.applied_font_size_px
                            ),
                            "X-PawMarvel-Text-Fit": preview.text.fit,
                        },
                    )
                    return

                with preview_lock:
                    preview_matches = previewed_revisions.get(revision) == fingerprint
                if not preview_matches:
                    self._json_error(
                        HTTPStatus.CONFLICT,
                        "current layout, pet name, and transformed pet must be previewed before saving",
                        code="preview_required",
                    )
                    return

                requested_font_reference = payload.get("font_reference")
                font_reference = None
                ranking = None
                if requested_font_reference is not None:
                    font_reference = _request_font_reference(
                        payload, config.reference
                    )
                    with ranking_lock:
                        ranking = font_rankings.get(
                            _font_reference_fingerprint(font_reference)
                        )
                requested_layout_reference = payload.get("layout_reference")
                layout_reference = None
                if requested_layout_reference is not None:
                    layout_reference = _request_layout_reference(
                        payload, config.reference
                    )
                    if (
                        font_reference is not None
                        and layout_reference.name_region.to_dict()
                        != font_reference.region.to_dict()
                    ):
                        raise ConfigError(
                            "layout reference name_region must match font reference region"
                        )
                if config.auto_font:
                    if payload.get("font_selection_confirmed") is not True:
                        raise ConfigError(
                            "select a ranked font before saving the layout"
                        )
                    if font_reference is None or ranking is None:
                        self._json_error(
                            HTTPStatus.CONFLICT,
                            "rank the confirmed reference text region before saving",
                            code="font_ranking_required",
                        )
                        return

                overwrite = config.force or payload.get("overwrite") is True
                if (
                    config.output.exists()
                    or config.calibration_output.exists()
                    or config.calibration_fixture_output.exists()
                    or (
                        font_reference is not None
                        and config.font_reference_output.exists()
                    )
                    or (
                        layout_reference is not None
                        and config.layout_reference_output.exists()
                    )
                ) and not overwrite:
                    self._json_error(
                        HTTPStatus.CONFLICT,
                        "layout or calibration output exists; confirm overwrite",
                        code="overwrite_required",
                    )
                    return
                calibration = render_composition(
                    layout,
                    preview_pet,
                    pet_name,
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
                with font_candidates_lock:
                    remote_source = remote_font_sources.get(
                        selected_font.candidate_id
                    )
                source_output = config.template_dir / "fonts" / "source.json"
                metadata_output = config.template_dir / "fonts" / "METADATA.pb"
                if remote_source is not None:
                    metadata_path = Path(str(remote_source["metadata_path"]))
                    public_source = {
                        key: value
                        for key, value in remote_source.items()
                        if key != "metadata_path"
                    }
                    _atomic_write_bytes(
                        source_output,
                        (json.dumps(public_source, indent=2) + "\n").encode("utf-8"),
                    )
                    _atomic_write_bytes(metadata_output, metadata_path.read_bytes())
                else:
                    source_output.unlink(missing_ok=True)
                    metadata_output.unlink(missing_ok=True)
                saved_layout = parse_layout(
                    layout.to_dict(),
                    config.template_dir,
                    art_override=config.art,
                    font_override=bundled_font,
                )
                write_layout(config.output, saved_layout)
                _atomic_write_bytes(
                    config.calibration_output, _png_bytes(calibration.image)
                )
                with preview_pet_lock:
                    tested_pet_descriptors = list(tested_preview_pets.values())
                fixture = {
                    "schema_version": 1,
                    "pet_name": pet_name,
                    "revision": revision,
                    "applied_font_size_px": calibration.text.applied_font_size_px,
                    "text_fit": calibration.text.fit,
                    "layout_sha256": hashlib.sha256(
                        config.output.read_bytes()
                    ).hexdigest(),
                    "transformed_pet_sha256": preview_pet_descriptor["sha256"],
                    "transformed_pet": preview_pet_descriptor,
                    "tested_transformed_pets": tested_pet_descriptors,
                }
                _atomic_write_bytes(
                    config.calibration_fixture_output,
                    (json.dumps(fixture, indent=2) + "\n").encode("utf-8"),
                )
                recommendation_output = None
                if layout_reference is not None:
                    _atomic_write_bytes(
                        config.layout_reference_output,
                        (
                            json.dumps(layout_reference.to_dict(), indent=2) + "\n"
                        ).encode("utf-8"),
                    )
                if font_reference is not None and ranking is not None:
                    _atomic_write_bytes(
                        config.font_reference_output,
                        (
                            json.dumps(font_reference.to_dict(), indent=2) + "\n"
                        ).encode("utf-8"),
                    )
                    recommendation_output = (
                        config.template_dir / "qa" / "font-recommendation.json"
                    )
                    try:
                        scale = recommend_font_size(
                            config.reference,
                            font_reference,
                            selected_font.font,
                            box_width=layout.name_box.width,
                            box_height=layout.name_box.height,
                            padding=layout.name_padding_px,
                        )
                        reference_scale: dict[str, Any] = {
                            "status": "available",
                            **scale.to_dict(),
                            "selected_font_size_px": layout.font_size_px,
                            "selected_matches_recommendation": (
                                layout.font_size_px == scale.font_size_px
                            ),
                        }
                    except (OSError, ValueError) as exc:
                        reference_scale = {
                            "status": "unavailable",
                            "reason": str(exc),
                            "selected_font_size_px": layout.font_size_px,
                        }
                    recommendation = {
                        **ranking,
                        "selected_font": selected_font.relative_name,
                        "selected_font_id": selected_font.candidate_id,
                        "selection_confirmed": True,
                        "reference_scale": reference_scale,
                        "ranked_options": ranking["ranked_options"][
                            :FONT_RECOMMENDATION_LIMIT
                        ],
                    }
                    _atomic_write_bytes(
                        recommendation_output,
                        (json.dumps(recommendation, indent=2) + "\n").encode(
                            "utf-8"
                        ),
                    )
                lifecycle.saved.set()
                response = {
                    "layout": str(config.output),
                    "calibration": str(config.calibration_output),
                    "calibration_fixture": str(config.calibration_fixture_output),
                    "revision": revision,
                    "layout_sha256": fixture["layout_sha256"],
                    "pet_name": pet_name,
                    "text_metrics": calibration.text.to_dict(),
                    "font": str(bundled_font),
                    "font_license": str(bundled_license),
                    "font_id": selected_font.candidate_id,
                    "font_label": selected_font.label,
                    "font_reference": (
                        str(config.font_reference_output)
                        if font_reference is not None and ranking is not None
                        else None
                    ),
                    "layout_reference": (
                        str(config.layout_reference_output)
                        if layout_reference is not None
                        else None
                    ),
                    "font_recommendation": (
                        str(recommendation_output)
                        if recommendation_output is not None
                        else None
                    ),
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
    remote_font_temp = tempfile.TemporaryDirectory(
        prefix="pawmarvel-layout-fonts-"
    )
    try:
        handler = _make_handler(
            config,
            candidates,
            lifecycle,
            Path(remote_font_temp.name),
        )
        server = _LayoutHTTPServer(
            (host, port), handler, remote_font_temp
        )
    except Exception:
        remote_font_temp.cleanup()
        raise
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
