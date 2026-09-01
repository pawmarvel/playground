from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image, UnidentifiedImageError

from .cli import _atomic_write_bytes
from .config import Layout, Rect, load_layout
from .font_license import FontLicenseError, resolve_ofl_license
from .renderer import ALPHA_THRESHOLD


TARGET_SIZE_PATTERN = re.compile(r"^\s*(\d+)\s*[xX*×]\s*(\d+)\s*$")
PRINT_ART_NAME = "art-print.png"
PRINT_PET_NAME = "transformed-pet-print.png"
PRINT_NAME_NAME = "name-print.png"
PRINT_LAYOUT_NAME = "layout-print.json"
PRINT_MANIFEST_NAME = "print-manifest.json"
PRINT_PROFILE_NAME = "product-profile.json"
BRIA_ENDPOINT = "https://engine.prod.bria-api.com/v2/image/edit/increase_resolution"
BRIA_MAX_DIMENSION = 8192
PROGRESS_INTERVAL_SECONDS = 10.0


class PrintUpscaleError(ValueError):
    """A print-upscale input or output violates the geometry contract."""


BriaProvider = Callable[[bytes, int, bool], bytes]


@dataclass(frozen=True)
class PrintOutputs:
    art: Path
    pet: Path
    layout: Path
    manifest: Path
    name: Path | None = None
    product_profile: Path | None = None

    def paths(self) -> tuple[Path, ...]:
        values = [self.art, self.pet]
        if self.name is not None:
            values.append(self.name)
        if self.product_profile is not None:
            values.append(self.product_profile)
        values.extend((self.layout, self.manifest))
        return tuple(values)


class _ProgressReporter:
    def __init__(self, label: str) -> None:
        self.label = label
        self.started_at = 0.0
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_ProgressReporter":
        self.started_at = time.monotonic()
        print(f"Submitting {self.label} to Bria...", file=sys.stderr, flush=True)
        self._thread = threading.Thread(target=self._report, daemon=True)
        self._thread.start()
        return self

    def _report(self) -> None:
        while not self._finished.wait(PROGRESS_INTERVAL_SECONDS):
            elapsed = int(time.monotonic() - self.started_at)
            print(
                f"Bria is still upscaling {self.label} ({elapsed}s elapsed)...",
                file=sys.stderr,
                flush=True,
            )

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._finished.set()
        if self._thread is not None:
            self._thread.join()
        elapsed = int(time.monotonic() - self.started_at)
        status = "received" if exc_type is None else "stopped"
        print(
            f"Bria {self.label} request {status} after {elapsed}s.",
            file=sys.stderr,
            flush=True,
        )


def parse_target_size(value: str) -> tuple[int, int]:
    match = TARGET_SIZE_PATTERN.fullmatch(value)
    if match is None:
        raise PrintUpscaleError(
            "--target-size must use WIDTHxHEIGHT, WIDTH*HEIGHT, or WIDTH×HEIGHT"
        )
    width, height = (int(part) for part in match.groups())
    if width <= 0 or height <= 0:
        raise PrintUpscaleError("target width and height must be positive")
    return width, height


def _open_image(path: Path, label: str) -> Image.Image:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise PrintUpscaleError(f"{label} does not exist: {path}")
    try:
        with Image.open(path) as image:
            image.load()
            return image.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise PrintUpscaleError(f"{label} is not a readable image: {path}") from exc


def _validate_cutout(image: Image.Image, label: str) -> None:
    alpha = image.getchannel("A")
    low, high = alpha.getextrema()
    if high <= ALPHA_THRESHOLD:
        raise PrintUpscaleError(f"{label} is fully transparent")
    if low > ALPHA_THRESHOLD:
        raise PrintUpscaleError(f"{label} must have a transparent background")


def _visible_bounds(image: Image.Image) -> tuple[int, int, int, int]:
    alpha = image.getchannel("A")
    visible = alpha.point(lambda value: 255 if value > ALPHA_THRESHOLD else 0)
    bounds = visible.getbbox()
    if bounds is None:
        raise PrintUpscaleError("image is fully transparent")
    return bounds


def _scaled_edges(
    bounds: tuple[int, int, int, int], scale_x: float, scale_y: float
) -> tuple[int, int, int, int]:
    left, top, right, bottom = bounds
    return (
        round(left * scale_x),
        round(top * scale_y),
        round(right * scale_x),
        round(bottom * scale_y),
    )


def _resize_rgba(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    if image.size == size:
        return image.copy()
    # Pillow's RGBa mode premultiplies color by alpha and avoids dark fringes
    # when partially transparent edge pixels are filtered.
    return image.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")


def _normalize_cutout(
    source: Image.Image, candidate: Image.Image, size: tuple[int, int]
) -> Image.Image:
    candidate = _resize_rgba(candidate, size)
    scale_x = size[0] / source.width
    scale_y = size[1] / source.height
    source_bounds = _visible_bounds(source)
    target_bounds = _scaled_edges(source_bounds, scale_x, scale_y)
    left, top, right, bottom = target_bounds
    target_width = max(1, right - left)
    target_height = max(1, bottom - top)

    candidate_crop = candidate.crop(target_bounds)
    if candidate_crop.size != (target_width, target_height):
        candidate_crop = _resize_rgba(candidate_crop, (target_width, target_height))
    alpha_crop = source.getchannel("A").crop(source_bounds).resize(
        (target_width, target_height), Image.Resampling.LANCZOS
    )
    candidate_crop.putalpha(alpha_crop)

    normalized = Image.new("RGBA", size, (0, 0, 0, 0))
    normalized.alpha_composite(candidate_crop, (left, top))
    return normalized


def _png_bytes(image: Image.Image, *, icc_profile: bytes | None = None) -> bytes:
    output = BytesIO()
    options = {"format": "PNG"}
    if icc_profile:
        options["icc_profile"] = icc_profile
    image.save(output, **options)
    return output.getvalue()


def _decode_image(contents: bytes, label: str) -> Image.Image:
    try:
        with Image.open(BytesIO(contents)) as image:
            image.load()
            return image.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise PrintUpscaleError(f"{label} did not return a readable image") from exc


def _read_token(path: Path | None) -> str:
    if path is None:
        token = os.environ.get("BRIA_API_TOKEN", "").strip()
        source = "BRIA_API_TOKEN"
    else:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise PrintUpscaleError(f"Bria API token file does not exist: {resolved}")
        if resolved.stat().st_size > 1024 * 1024:
            raise PrintUpscaleError(f"Bria API token file is unexpectedly large: {resolved}")
        try:
            token = resolved.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError as exc:
            raise PrintUpscaleError("Bria API token file must be UTF-8 text") from exc
        source = str(resolved)
    if not token or any(character.isspace() for character in token):
        raise PrintUpscaleError(f"{source} must contain exactly one nonempty token")
    return token


def _bria_request(contents: bytes, increase: int, preserve_alpha: bool, token: str) -> bytes:
    payload = json.dumps(
        {
            "image": base64.b64encode(contents).decode("ascii"),
            "desired_increase": increase,
            "preserve_alpha": preserve_alpha,
            "sync": True,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        BRIA_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json", "api_token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        image_url = response_data["result"]["image_url"]
        if not isinstance(image_url, str) or not image_url.startswith(("https://", "http://")):
            raise KeyError("image_url")
        with urllib.request.urlopen(image_url, timeout=300) as response:
            result = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"Bria returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Bria request failed: {exc}") from exc
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Bria returned an invalid response") from exc
    if not result:
        raise RuntimeError("Bria returned an empty image")
    return result


def _scale_rect(rect: Rect, scale_x: float, scale_y: float) -> Rect:
    left, top, right, bottom = _scaled_edges(
        (rect.x, rect.y, rect.right, rect.bottom), scale_x, scale_y
    )
    return Rect(x=left, y=top, width=max(1, right - left), height=max(1, bottom - top))


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_print_manifest(path: Path) -> Mapping[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise PrintUpscaleError(f"print manifest does not exist: {resolved}")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise PrintUpscaleError("print manifest must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise PrintUpscaleError(f"print manifest contains invalid JSON: {exc}") from exc
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise PrintUpscaleError("print manifest must be a schema-v1 object")
    return value


def verify_print_bundle(
    *,
    template_dir: Path,
    layout_path: Path,
    transformed_pet: Path,
    name_image: Path | None,
    product_profile: Path,
) -> Path:
    """Verify that every final-render input belongs to one prepared upscale run."""
    root = template_dir.expanduser().resolve()
    manifest_path = root / PRINT_MANIFEST_NAME
    manifest = _load_print_manifest(manifest_path)
    print_data = manifest.get("print")
    hashes = manifest.get("output_sha256")
    if not isinstance(print_data, Mapping) or not isinstance(hashes, Mapping):
        raise PrintUpscaleError("print manifest lacks print paths or output checksums")

    expected_paths: dict[str, Path | None] = {
        "art": root / str(print_data.get("art")),
        "transformed_pet": root / str(print_data.get("transformed_pet")),
        "name_image": (
            root / str(print_data.get("name_image"))
            if print_data.get("name_image") is not None
            else None
        ),
        "layout": root / str(print_data.get("layout")),
        "font": root / str(print_data.get("font")),
        "font_license": root / str(print_data.get("font_license")),
        "product_profile": root / PRINT_PROFILE_NAME,
    }
    supplied_paths: dict[str, Path | None] = {
        "art": root / PRINT_ART_NAME,
        "transformed_pet": transformed_pet.expanduser().resolve(),
        "name_image": name_image.expanduser().resolve() if name_image else None,
        "layout": layout_path.expanduser().resolve(),
        "font": expected_paths["font"],
        "font_license": expected_paths["font_license"],
        "product_profile": product_profile.expanduser().resolve(),
    }
    for label, expected_path in expected_paths.items():
        supplied_path = supplied_paths[label]
        if expected_path is None and supplied_path is None:
            continue
        if expected_path is None or supplied_path is None:
            raise PrintUpscaleError(
                f"final render {label} does not match the prepared print bundle"
            )
        expected_path = expected_path.resolve()
        if supplied_path != expected_path:
            raise PrintUpscaleError(
                f"final render {label} is not the prepared print-bundle artifact"
            )
        if not supplied_path.is_file():
            raise PrintUpscaleError(f"print-bundle artifact does not exist: {supplied_path}")
        expected_hash = hashes.get(label)
        if not isinstance(expected_hash, str) or _file_sha256(supplied_path) != expected_hash:
            raise PrintUpscaleError(
                f"print-bundle artifact changed after upscale: {label} ({supplied_path})"
            )
    return manifest_path


def prepare_print_assets(
    *,
    template_dir: Path,
    transformed_pet: Path,
    target_size: tuple[int, int],
    output_dir: Path,
    layout_path: Path | None = None,
    name_image: Path | None = None,
    backend: str = "deterministic",
    bria_token_file: Path | None = None,
    product_profile: Path | None = None,
    force: bool = False,
    bria_provider: BriaProvider | None = None,
) -> PrintOutputs:
    template_dir = template_dir.expanduser().resolve()
    transformed_pet = transformed_pet.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    layout_path = layout_path.expanduser().resolve() if layout_path else None
    name_image = name_image.expanduser().resolve() if name_image else None
    product_profile = (
        product_profile.expanduser().resolve() if product_profile else None
    )
    layout = load_layout(template_dir, layout_path=layout_path)
    try:
        font_license = resolve_ofl_license(layout.font_path)
    except FontLicenseError as exc:
        raise PrintUpscaleError(str(exc)) from exc

    target_width, target_height = target_size
    if target_width <= layout.canvas_width or target_height <= layout.canvas_height:
        raise PrintUpscaleError(
            "target dimensions must be larger than the preview art in both axes"
        )
    if target_width * layout.canvas_height != target_height * layout.canvas_width:
        raise PrintUpscaleError(
            "target aspect ratio must exactly match the preview art; "
            "cropping or stretching is not allowed"
        )
    scale_x = target_width / layout.canvas_width
    scale_y = target_height / layout.canvas_height
    if not math.isclose(scale_x, scale_y, rel_tol=0, abs_tol=1e-12):
        raise PrintUpscaleError("print scaling must be uniform")
    scale = scale_x

    art_source = _open_image(layout.art_path, "art")
    pet_source = _open_image(transformed_pet, "transformed pet")
    _validate_cutout(pet_source, "transformed pet")
    name_source = _open_image(name_image, "name image") if name_image else None
    if name_source is not None:
        _validate_cutout(name_source, "name image")

    optional_name_output = output_dir / PRINT_NAME_NAME
    outputs = PrintOutputs(
        art=output_dir / PRINT_ART_NAME,
        pet=output_dir / PRINT_PET_NAME,
        name=optional_name_output if name_source is not None else None,
        layout=output_dir / PRINT_LAYOUT_NAME,
        manifest=output_dir / PRINT_MANIFEST_NAME,
        product_profile=(output_dir / PRINT_PROFILE_NAME if product_profile else None),
    )
    font_output = output_dir / layout.font_relative
    font_license_output = font_output.parent / "OFL.txt"
    targets = list(outputs.paths()) + [font_output, font_license_output]
    if outputs.name is None:
        targets.append(optional_name_output)
    if len(set(targets)) != len(targets):
        raise PrintUpscaleError("print output paths collide with the configured font path")
    existing = [path for path in targets if path.exists()]
    if existing and not force:
        raise PrintUpscaleError(
            f"output already exists: {existing[0]} (pass --force to replace print outputs)"
        )

    layer_sizes = {
        "art": target_size,
        "pet": (
            max(1, round(pet_source.width * scale)),
            max(1, round(pet_source.height * scale)),
        ),
    }
    if name_source is not None:
        layer_sizes["name"] = (
            max(1, round(name_source.width * scale)),
            max(1, round(name_source.height * scale)),
        )

    if backend not in {"deterministic", "bria"}:
        raise PrintUpscaleError("backend must be deterministic or bria")
    bria_increase: int | None = None
    provider = bria_provider
    if backend == "bria":
        bria_increase = 2 if scale <= 2 else 4
        bria_sources = [art_source, pet_source]
        if name_source is not None:
            bria_sources.append(name_source)
        oversized = next(
            (
                source
                for source in bria_sources
                if source.width * bria_increase > BRIA_MAX_DIMENSION
                or source.height * bria_increase > BRIA_MAX_DIMENSION
            ),
            None,
        )
        if oversized is not None:
            raise PrintUpscaleError(
                "a Bria native upscale would exceed 8192x8192 before exact-size normalization"
            )
        if provider is None:
            token = _read_token(bria_token_file)
            provider = lambda contents, increase, preserve: _bria_request(
                contents, increase, preserve, token
            )

    sources = [("art", art_source, False)]
    sources.append(("pet", pet_source, True))
    if name_source is not None:
        sources.append(("name", name_source, True))

    rendered: dict[str, bytes] = {}
    for label, source, cutout in sources:
        size = layer_sizes[label]
        if backend == "bria":
            assert provider is not None and bria_increase is not None
            source_bytes = _png_bytes(source, icc_profile=source.info.get("icc_profile"))
            with _ProgressReporter(label):
                candidate = _decode_image(
                    provider(source_bytes, bria_increase, "A" in source.getbands()),
                    f"Bria {label}",
                )
        else:
            candidate = source

        if cutout:
            result = _normalize_cutout(source, candidate, size)
        else:
            result = _resize_rgba(candidate, size)
            if source.getchannel("A").getextrema()[0] < 255:
                result.putalpha(source.getchannel("A").resize(size, Image.Resampling.LANCZOS))
        rendered[label] = _png_bytes(result, icc_profile=source.info.get("icc_profile"))

    print_layout = Layout(
        template_dir=output_dir,
        art_relative=PRINT_ART_NAME,
        art_path=outputs.art,
        canvas_width=target_width,
        canvas_height=target_height,
        pet_box=_scale_rect(layout.pet_box, scale_x, scale_y),
        pet_rotation_degrees=layout.pet_rotation_degrees,
        font_relative=layout.font_relative,
        font_path=font_output,
        name_box=_scale_rect(layout.name_box, scale_x, scale_y),
        font_size_px=max(1, round(layout.font_size_px * scale)),
        min_font_size_px=max(1, round(layout.min_font_size_px * scale)),
        color=layout.color,
        horizontal_align=layout.horizontal_align,
        vertical_align=layout.vertical_align,
        runtime_model=layout.runtime_model,
    )
    layout_bytes = (json.dumps(print_layout.to_dict(), indent=2) + "\n").encode("utf-8")
    manifest = {
        "schema_version": 1,
        "backend": backend,
        "bria_desired_increase": bria_increase,
        "detail_upscale": (
            {
                "provider": "bria",
                "native_increase": bria_increase,
                "final_exact_scale": scale,
                "normalization": "Lanczos after native detail upscale",
            }
            if backend == "bria"
            else {
                "provider": "deterministic",
                "native_increase": None,
                "final_exact_scale": scale,
                "normalization": "premultiplied-alpha Lanczos",
            }
        ),
        "product_profile": str(product_profile) if product_profile else None,
        "source": {
            "template_dir": str(template_dir),
            "layout": str(layout_path or template_dir / "layout.json"),
            "art": str(layout.art_path),
            "transformed_pet": str(transformed_pet),
            "name_image": str(name_image) if name_image else None,
            "canvas": {"width": layout.canvas_width, "height": layout.canvas_height},
        },
        "print": {
            "canvas": {"width": target_width, "height": target_height},
            "scale": scale,
            "art": PRINT_ART_NAME,
            "transformed_pet": PRINT_PET_NAME,
            "name_image": PRINT_NAME_NAME if name_source is not None else None,
            "layout": PRINT_LAYOUT_NAME,
            "font": layout.font_relative,
            "font_license": "fonts/OFL.txt",
            "layer_dimensions": {
                key: {"width": value[0], "height": value[1]}
                for key, value in layer_sizes.items()
            },
        },
        "alpha_policy": "source alpha scaled deterministically; visible cutout bounds preserved",
        "source_sha256": {
            "art": _file_sha256(layout.art_path),
            "transformed_pet": _file_sha256(transformed_pet),
            "name_image": _file_sha256(name_image) if name_image else None,
            "layout": _file_sha256(layout_path or template_dir / "layout.json"),
            "font": _file_sha256(layout.font_path),
            "font_license": _file_sha256(font_license),
            "product_profile": _file_sha256(product_profile) if product_profile else None,
        },
        "output_sha256": {
            "art": _sha256(rendered["art"]),
            "transformed_pet": _sha256(rendered["pet"]),
            "name_image": _sha256(rendered["name"]) if "name" in rendered else None,
            "layout": _sha256(layout_bytes),
            "font": _file_sha256(layout.font_path),
            "font_license": _file_sha256(font_license),
            "product_profile": _file_sha256(product_profile) if product_profile else None,
        },
    }
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")

    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(outputs.art, rendered["art"])
    _atomic_write_bytes(outputs.pet, rendered["pet"])
    if outputs.name is not None:
        _atomic_write_bytes(outputs.name, rendered["name"])
    elif force:
        optional_name_output.unlink(missing_ok=True)
    _atomic_write_bytes(outputs.layout, layout_bytes)
    _atomic_write_bytes(outputs.manifest, manifest_bytes)
    if outputs.product_profile is not None and product_profile is not None:
        _atomic_write_bytes(outputs.product_profile, product_profile.read_bytes())
    font_output.parent.mkdir(parents=True, exist_ok=True)
    temporary_font = font_output.with_name(f".{font_output.name}.tmp")
    try:
        shutil.copyfile(layout.font_path, temporary_font)
        os.replace(temporary_font, font_output)
    finally:
        temporary_font.unlink(missing_ok=True)
    _atomic_write_bytes(font_license_output, font_license.read_bytes())

    # Validate the completed bundle with the same parser used by the renderer.
    load_layout(output_dir, layout_path=outputs.layout)
    return outputs
