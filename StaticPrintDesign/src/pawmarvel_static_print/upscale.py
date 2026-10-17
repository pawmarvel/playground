"""High-quality print upscaling for static product candidates."""

from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from PIL import Image, UnidentifiedImageError

from .core import StaticPrintError

BRIA_ENDPOINT = "https://engine.prod.bria-api.com/v2/image/edit/increase_resolution"
BRIA_MAX_DIMENSION = 8192
BriaProvider = Callable[[bytes, int, bool], bytes]


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _decode_image(contents: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(contents)) as image:
            image.load()
            return image.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise StaticPrintError("Bria returned an unreadable image") from exc


def _resize_rgba(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize premultiplied RGBA so transparent edges do not acquire dark halos."""

    if image.size == size:
        return image.copy()
    return (
        image.convert("RGBa")
        .resize(size, Image.Resampling.LANCZOS)
        .convert("RGBA")
    )


def _error_detail(exc: urllib.error.HTTPError) -> str:
    raw = exc.read(2048).decode("utf-8", errors="replace")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return " ".join(raw.split())[:1000] or "<empty response>"
    if isinstance(body, Mapping):
        value = body.get("message") or body.get("error") or body.get("detail")
        if value is not None:
            return " ".join(str(value).split())[:1000]
    return " ".join(raw.split())[:1000] or "<empty response>"


def bria_request(
    contents: bytes,
    increase: int,
    preserve_alpha: bool,
    *,
    token: str | None = None,
    max_attempts: int = 3,
) -> bytes:
    """Call Bria with bounded retries for rate limits and transient failures."""

    resolved_token = (token or os.environ.get("BRIA_API_TOKEN", "")).strip()
    if not resolved_token or any(character.isspace() for character in resolved_token):
        raise StaticPrintError(
            "Bria print upscaling requires BRIA_API_TOKEN in the shared environment"
        )
    payload = json.dumps(
        {
            "image": base64.b64encode(contents).decode("ascii"),
            "desired_increase": increase,
            "preserve_alpha": preserve_alpha,
            "sync": True,
        }
    ).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            BRIA_ENDPOINT,
            data=payload,
            headers={"Content-Type": "application/json", "api_token": resolved_token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            image_url = response_data["result"]["image_url"]
            if not isinstance(image_url, str) or not image_url.startswith(
                ("https://", "http://")
            ):
                raise KeyError("image_url")
            with urllib.request.urlopen(image_url, timeout=300) as response:
                result = response.read()
            if not result:
                raise StaticPrintError("Bria returned an empty image")
            return result
        except urllib.error.HTTPError as exc:
            last_error = exc
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt == max_attempts:
                raise StaticPrintError(
                    f"Bria API returned HTTP {exc.code}: {_error_detail(exc)}; "
                    f"endpoint={BRIA_ENDPOINT}; attempt={attempt}/{max_attempts}"
                ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == max_attempts:
                raise StaticPrintError(
                    f"Bria API request failed after {attempt} attempt(s); "
                    f"endpoint={BRIA_ENDPOINT}; error={exc}"
                ) from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StaticPrintError(
                f"Bria API returned an invalid response; endpoint={BRIA_ENDPOINT}; "
                f"error={type(exc).__name__}: {exc}"
            ) from exc
        time.sleep(min(2 ** (attempt - 1), 8))
    raise StaticPrintError(f"Bria API request failed: {last_error}")


def upscale_for_print(
    source: Image.Image,
    *,
    required_scale: float,
    preserve_alpha: bool,
    provider: BriaProvider | None = None,
) -> tuple[Image.Image, dict[str, object]]:
    """Upscale a source before exact product rendering and return provenance."""

    if required_scale <= 1:
        return source.copy(), {
            "backend": "none",
            "native_increase": 1,
            "preserve_alpha": preserve_alpha,
            "source_size": {"width": source.width, "height": source.height},
            "output_size": {"width": source.width, "height": source.height},
        }
    increase = 2 if required_scale <= 2 else 4
    if source.width * increase > BRIA_MAX_DIMENSION or source.height * increase > BRIA_MAX_DIMENSION:
        raise StaticPrintError(
            "Bria native upscale would exceed its 8192x8192 limit; "
            f"source={source.width}x{source.height}; increase={increase}"
        )
    request = provider or (lambda data, factor, preserve: bria_request(data, factor, preserve))
    generated = _decode_image(
        request(_png_bytes(source), increase, preserve_alpha)
    )
    if generated.width < source.width or generated.height < source.height:
        raise StaticPrintError(
            "Bria output is smaller than its source; "
            f"source={source.width}x{source.height}; "
            f"output={generated.width}x{generated.height}"
        )
    expected_size = (source.width * increase, source.height * increase)
    generated = _resize_rgba(generated, expected_size)
    if preserve_alpha:
        # The source owns the silhouette. Reusing its enlarged alpha prevents an
        # enhancement provider from adding haze, holes, or dirty fringe pixels.
        alpha = source.getchannel("A").resize(
            expected_size, Image.Resampling.LANCZOS
        )
        generated.putalpha(alpha)
    else:
        generated = generated.convert("RGB").convert("RGBA")
    return generated, {
        "backend": "bria",
        "native_increase": increase,
        "preserve_alpha": preserve_alpha,
        "source_size": {"width": source.width, "height": source.height},
        "output_size": {"width": generated.width, "height": generated.height},
    }
