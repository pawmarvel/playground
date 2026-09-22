# CLI purpose:
# Generate or edit personalized design assets with OpenAI or Gemini from a
# text prompt alone, a finished-design reference, a pet image, or both. It can
# also create a deterministic all-zero transparent canvas without an API call;
# output size may be explicit or derived from a reusable product profile.

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError

from .cli_errors import add_debug_argument, report_unexpected
from .generation_contract import gemini_aspect_ratio, gemini_image_size
from .image_size import ImageSizeError, parse_image_size, validate_generation_size
from .personalization import PersonalizationError, pet_name_policy, validate_pet_name
from .product_profile import ProductProfileError, load_product_profile


DEFAULT_OUTPUT_DIR = Path("output")
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
OUTPUT_FORMATS = ("png", "jpg", "jpeg", "webp")
OPENAI_API_KEY_PATTERN = re.compile(rb"sk-[A-Za-z0-9_-]{20,}")
MAX_API_KEY_FILE_BYTES = 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 10.0
GEMINI_INTERACTIONS_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/interactions"
)
PROVIDERS = ("auto", "openai", "gemini")
DEFAULT_MODELS = {
    "openai": "gpt-image-2",
    "gemini": "gemini-3.1-flash-image",
}
PROMPT_CATEGORY_PATTERN = re.compile(
    r"-(gpt|gemini)(?:-[a-z0-9]+)*\.md$"
)
PET_NAME_PLACEHOLDER = "{{PET_NAME}}"


class UserInputError(ValueError):
    """An actionable command-line input error."""


class _GeminiRestInteractions:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def create(self, **payload: Any) -> SimpleNamespace:
        request = Request(
            GEMINI_INTERACTIONS_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=300) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raw_detail = exc.read().decode("utf-8", errors="replace").strip()
            request_id = None
            if exc.headers is not None:
                request_id = exc.headers.get("x-request-id") or exc.headers.get(
                    "x-goog-request-id"
                )
            exc.close()
            detail = raw_detail
            error_code = None
            try:
                body = json.loads(raw_detail)
                error = body.get("error", {})
                if isinstance(error, dict):
                    detail = str(error.get("message", ""))
                    error_code = error.get("status") or error.get("code")
                elif error:
                    detail = str(error)
            except (json.JSONDecodeError, AttributeError):
                pass
            detail = " ".join(detail.split())[:1000]
            suffix = f": {detail}" if detail else ""
            context = f"; provider=gemini; model={payload.get('model', '<unknown>')}"
            if error_code:
                context += f"; error_code={error_code}"
            if request_id:
                context += f"; request_id={request_id}"
            if exc.code in {401, 403}:
                correction = "verify GEMINI_API_KEY and project/model permissions"
            elif exc.code == 429:
                correction = "wait and retry, or verify quota and rate limits"
            elif 400 <= exc.code < 500:
                correction = "correct the model, prompt, image, or request parameters"
            else:
                correction = "retry; if it persists, check Gemini service status"
            raise RuntimeError(
                f"Gemini API returned HTTP {exc.code}{suffix}{context}. "
                f"Correction: {correction}."
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                "Gemini API request failed; provider=gemini; "
                f"model={payload.get('model', '<unknown>')}; error={exc.reason}. "
                "Correction: verify network connectivity and retry."
            ) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Gemini API returned malformed JSON; provider=gemini; "
                f"model={payload.get('model', '<unknown>')}; line={exc.lineno}; "
                f"column={exc.colno}; error={exc.msg}. Correction: retry; if it "
                "persists, record the model and report a provider response issue."
            ) from exc

        for step in reversed(result.get("steps", [])):
            for content in reversed(step.get("content", [])):
                if content.get("type") == "image" and content.get("data"):
                    return SimpleNamespace(
                        output_image=SimpleNamespace(data=content["data"])
                    )
        return SimpleNamespace(output_image=None)


class _GeminiRestClient:
    def __init__(self, api_key: str) -> None:
        self.interactions = _GeminiRestInteractions(api_key)


class _ProgressReporter:
    def __init__(
        self,
        provider: str,
        interval: float | None = None,
        operation: str = "edit",
    ) -> None:
        self.provider = provider
        self.interval = PROGRESS_INTERVAL_SECONDS if interval is None else interval
        self.operation = operation
        self.started_at = 0.0
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_ProgressReporter":
        self.started_at = time.monotonic()
        service = "OpenAI" if self.provider == "openai" else "Gemini"
        print(
            f"Submitting image {self.operation} request to {service}...",
            file=sys.stderr,
            flush=True,
        )
        self._thread = threading.Thread(
            target=self._report,
            name="pawmarvel-generation-progress",
            daemon=True,
        )
        self._thread.start()
        return self

    def _report(self) -> None:
        while not self._finished.wait(self.interval):
            elapsed = int(time.monotonic() - self.started_at)
            print(
                f"Generation is still in progress ({elapsed}s elapsed)...",
                file=sys.stderr,
                flush=True,
            )

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._finished.set()
        if self._thread is not None:
            self._thread.join()
        elapsed = int(time.monotonic() - self.started_at)
        status = "API response received" if exc_type is None else "API request stopped"
        print(f"{status} after {elapsed}s.", file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-generate",
        description=(
            "Generate or edit artwork with OpenAI or Gemini, or create a "
            "deterministic all-zero transparent PNG canvas locally."
        ),
    )
    add_debug_argument(parser)
    parser.add_argument(
        "--reference-design",
        type=Path,
        action="append",
        help="optional finished-design reference; repeat in priority order",
    )
    parser.add_argument(
        "--pet-image",
        type=Path,
        help="optional user pet reference image",
    )
    parser.add_argument(
        "--pet-name",
        help=(
            "personalized pet name used to replace the exact {{PET_NAME}} "
            "placeholder in a transformed-pet prompt"
        ),
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help="UTF-8 Markdown/text prompt (required unless --empty-canvas)",
    )
    parser.add_argument(
        "--empty-canvas",
        action="store_true",
        help=(
            "create a deterministic all-zero transparent RGBA PNG locally; "
            "requires an explicit --size or a product-profile layer and makes "
            "no image API call"
        ),
    )
    parser.add_argument(
        "--api-key-file",
        type=Path,
        help=(
            "file containing the selected provider API key; OpenAI accepts "
            "plain text or RTF and Gemini accepts plain text; defaults to "
            "OPENAI_API_KEY or GEMINI_API_KEY"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="destination directory (default: ./output)",
    )
    parser.add_argument(
        "--output-name",
        help=(
            "output filename (default: first supplied image stem, or prompt-file "
            "stem in prompt-only mode, or empty-canvas.png in empty-canvas mode)"
        ),
    )
    parser.add_argument(
        "--output-format",
        choices=OUTPUT_FORMATS,
        default="png",
        help="generated image format; jpg is an alias for jpeg (default: png)",
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default="auto",
        help=(
            "image API provider; auto infers Gemini from a gemini-* model and "
            "otherwise uses OpenAI (default: auto)"
        ),
    )
    parser.add_argument(
        "--model",
        help=(
            "provider model ID (default: gpt-image-2 for OpenAI or "
            "gemini-3.1-flash-image for Gemini)"
        ),
    )
    parser.add_argument(
        "--size",
        default="auto",
        help=(
            "output size accepted by the selected model, or WIDTHxHEIGHT for "
            "--empty-canvas (default: auto)"
        ),
    )
    parser.add_argument(
        "--product-profile",
        type=Path,
        help="derive the generation size from a reusable product profile",
    )
    parser.add_argument(
        "--profile-layer",
        choices=("art", "transformed-pet"),
        help="profile preview layer whose dimensions should be used",
    )
    parser.add_argument(
        "--quality", choices=("low", "medium", "high", "auto"), default="high"
    )
    parser.add_argument(
        "--background",
        choices=("prompt", "transparent", "opaque", "auto"),
        default="prompt",
        help=(
            "output background; 'prompt' detects a transparent/opaque instruction "
            "in the prompt (default: prompt)"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "validate inputs and show resolved settings without calling an API "
            "or writing an output file"
        ),
    )
    return parser


def _validate_regular_file(path: Path, label: str) -> Path:
    path = path.expanduser().resolve()
    if not path.exists():
        raise UserInputError(f"{label} does not exist: {path}")
    if not path.is_file():
        raise UserInputError(f"{label} is not a regular file: {path}")
    return path


def _validate_image(path: Path, label: str) -> Path:
    path = _validate_regular_file(path, label)
    if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_IMAGE_SUFFIXES))
        raise UserInputError(f"{label} must be one of {allowed}: {path}")
    if path.stat().st_size == 0:
        raise UserInputError(f"{label} is empty: {path}")
    return path


def _warn_art_reference_aspect_mismatch(
    samples: Sequence[Path], requested_size: str
) -> None:
    """Warn when screenshot geometry differs from the authoritative art canvas."""
    if not re.fullmatch(r"\d+x\d+", requested_size):
        return
    target_width, target_height = (
        int(value) for value in requested_size.split("x", 1)
    )
    for index, sample in enumerate(samples, start=1):
        try:
            with Image.open(sample) as image:
                source_width, source_height = image.size
        except (OSError, UnidentifiedImageError):
            # This diagnostic must not introduce a new image-validation contract.
            continue
        if source_width * target_height == target_width * source_height:
            continue
        print(
            "Warning: reference design "
            f"{index} has aspect ratio {source_width}x{source_height}, but the "
            f"authoritative product-profile art canvas is {requested_size}. "
            "The output will use the product-profile dimensions and map the "
            "reference composition by normalized coordinates; review the result "
            "for unintended crop, stretching, or reflow.",
            file=sys.stderr,
            flush=True,
        )


def _read_prompt(path: Path) -> tuple[Path, str]:
    path = _validate_regular_file(path, "prompt file")
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise UserInputError(f"prompt file must be UTF-8 text: {path}") from exc
    if not prompt:
        raise UserInputError(f"prompt file is empty: {path}")
    return path, prompt


def _substitute_pet_name(
    prompt: str, *, pet_name: str | None, pet: Path | None
) -> tuple[str, str | None, bool]:
    """Resolve the one supported runtime prompt variable before an API call."""
    if PET_NAME_PLACEHOLDER not in prompt:
        if pet_name is not None:
            print(
                "WARNING: --pet-name was provided, but the prompt contains no "
                f"{PET_NAME_PLACEHOLDER} placeholder; the name will be ignored "
                "and the API call will continue.",
                file=sys.stderr,
                flush=True,
            )
        return prompt, None, False
    if pet is None:
        raise UserInputError(
            f"{PET_NAME_PLACEHOLDER} is only supported for transformed-pet "
            "generation with --pet-image"
        )
    if pet_name is None:
        raise UserInputError(
            f"prompt contains {PET_NAME_PLACEHOLDER}; provide --pet-name"
        )
    try:
        normalized = validate_pet_name(pet_name, pet_name_policy(64))
    except PersonalizationError as exc:
        raise UserInputError(f"invalid --pet-name: {exc}") from exc
    return prompt.replace(PET_NAME_PLACEHOLDER, normalized), normalized, True


def _validate_prompt_category(path: Path, provider: str) -> None:
    match = PROMPT_CATEGORY_PATTERN.search(path.name)
    if match is None:
        return
    expected = "gemini" if provider == "gemini" else "gpt"
    if match.group(1) != expected:
        raise UserInputError(
            f"prompt category {match.group(1)} does not match {provider} provider: "
            f"{path.name}"
        )


def _resolve_provider_model(provider: str, model: str | None) -> tuple[str, str]:
    if provider not in PROVIDERS:
        raise UserInputError(f"unsupported image provider: {provider}")
    inferred = "gemini" if model and model.startswith("gemini-") else "openai"
    resolved_provider = inferred if provider == "auto" else provider
    resolved_model = model or DEFAULT_MODELS[resolved_provider]
    if resolved_provider == "gemini" and not resolved_model.startswith("gemini-"):
        raise UserInputError("--provider gemini requires a gemini-* model")
    if resolved_provider == "openai" and resolved_model.startswith("gemini-"):
        raise UserInputError("a gemini-* model requires --provider gemini or auto")
    return resolved_provider, resolved_model


def _read_api_key(path: Path, provider: str = "openai") -> tuple[Path, str]:
    path = _validate_regular_file(path, "API key file")
    size = path.stat().st_size
    if size == 0:
        raise UserInputError(f"API key file is empty: {path}")
    if size > MAX_API_KEY_FILE_BYTES:
        raise UserInputError(f"API key file is unexpectedly large: {path}")

    contents = path.read_bytes()
    if provider == "openai":
        matches = {
            match.decode("ascii")
            for match in OPENAI_API_KEY_PATTERN.findall(contents)
        }
        if not matches:
            raise UserInputError(
                f"API key file does not contain a valid sk-... key: {path}"
            )
        if len(matches) != 1:
            raise UserInputError(
                f"API key file must contain exactly one distinct API key: {path}"
            )
        return path, matches.pop()

    try:
        key = contents.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise UserInputError(f"Gemini API key file must be UTF-8 plain text: {path}") from exc
    if not key or any(character.isspace() for character in key):
        raise UserInputError(
            f"Gemini API key file must contain only one non-empty key: {path}"
        )
    return path, key


def _resolve_api_key(
    path: Path | None, provider: str = "openai"
) -> tuple[Path | None, str]:
    if path is not None:
        return _read_api_key(path, provider=provider)
    environment_names = (
        ("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if provider == "gemini"
        else ("OPENAI_API_KEY",)
    )
    source = next(
        (name for name in environment_names if os.environ.get(name, "").strip()),
        environment_names[0],
    )
    key = os.environ.get(source, "").strip()
    if not key:
        raise UserInputError(
            "provide --api-key-file or set " + " or ".join(environment_names)
        )
    if provider == "openai" and not OPENAI_API_KEY_PATTERN.fullmatch(
        key.encode("ascii", errors="ignore")
    ):
        raise UserInputError("OPENAI_API_KEY does not contain a valid sk-... key")
    return None, key


def _background_from_prompt(prompt: str, requested: str) -> str:
    if requested != "prompt":
        return requested
    normalized = " ".join(prompt.upper().split())
    if "BACKGROUND = TRANSPARENT" in normalized or "TRANSPARENT BACKGROUND" in normalized:
        return "transparent"
    if "BACKGROUND = OPAQUE" in normalized or "OPAQUE BACKGROUND" in normalized:
        return "opaque"
    return "auto"


def _api_output_format(output_format: str) -> str:
    return "jpeg" if output_format == "jpg" else output_format


def _output_path(
    output_dir: Path,
    output_name: str | None,
    default_stem: str,
    output_format: str,
) -> Path:
    filename = output_name or f"{default_stem}.{output_format}"
    name = Path(filename)
    expected_suffixes = (
        {".jpg", ".jpeg"}
        if _api_output_format(output_format) == "jpeg"
        else {f".{output_format}"}
    )
    if name.name != filename or name.suffix.lower() not in expected_suffixes:
        expected = " or ".join(sorted(expected_suffixes))
        raise UserInputError(
            "--output-name must be a plain filename ending in "
            f"{expected} for --output-format {output_format}"
        )
    return output_dir.expanduser().resolve() / filename


def _api_prompt(user_prompt: str, samples: list[Path], pet: Path | None) -> str:
    roles: list[str] = []
    if pet is not None and samples:
        roles.append(
            "- USER PET: the first supplied image; use it only for the customer's identity."
        )
        roles.append(
            "- PRIMARY REFERENCE DESIGN: the second supplied image; use it for "
            "crop, composition, palette, and artistic treatment, never pet "
            "identity or facial expression."
        )
        if len(samples) > 1:
            roles.append(
                "- ADDITIONAL REFERENCE DESIGNS: all remaining images, in "
                "supplied order; use them only to clarify style and detail, "
                "never to override primary geometry, palette, or content."
            )
    elif samples:
        roles.append(
            "- PRIMARY REFERENCE DESIGN: the first supplied image; it is "
            "authoritative for geometry, crop, composition, palette, fixed "
            "content, and treatment."
        )
        if len(samples) > 1:
            roles.append(
                "- ADDITIONAL REFERENCE DESIGNS: all remaining images, in "
                "supplied order; use them only to clarify style and detail, "
                "never to override the primary geometry, palette, or content."
            )
    elif pet is not None:
        roles.append(
            "- USER PET: the supplied image; use it only for the customer's identity."
        )
    if not roles:
        return user_prompt
    mapping = "\n".join(roles)
    return (
        "INPUT IMAGE MAPPING:\n"
        f"{mapping}\n\n"
        "Use the supplied images according to the production prompt below.\n\n"
        f"{user_prompt}"
    )


def _gemini_prompt(
    prompt: str,
    background: str,
    *,
    prompt_only: bool = False,
) -> str:
    if background != "transparent":
        return prompt
    if prompt_only:
        return (
            f"{prompt}\n\n"
            "GEMINI OUTPUT REQUIREMENT: Return only the artwork requested by the "
            "prompt on a genuine transparent background. Do not add a product, "
            "mockup, room scene, backdrop, border, checkerboard, or unrequested "
            "decoration. If the response transport cannot encode alpha, use a "
            "perfectly uniform pure-white (#FFFFFF) matte with no shadow, texture, "
            "gradient, or other background content so downstream background "
            "removal can isolate the requested artwork."
        )
    return (
        f"{prompt}\n\n"
        "GEMINI OUTPUT REQUIREMENT: Return one isolated subject image only. Do "
        "not reproduce any reference background, template, text, decoration, "
        "product, or mockup. Prefer genuine transparent alpha. If the response "
        "transport cannot encode alpha, use a perfectly uniform pure-white "
        "(#FFFFFF) isolation matte with no shadow, texture, gradient, or other "
        "content so a downstream background-removal step can isolate the subject."
    )


def _image_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return "image/jpeg" if suffix in {".jpg", ".jpeg"} else f"image/{suffix[1:]}"


def _request_summary(
    *,
    samples: list[Path],
    pet: Path | None,
    prompt_file: Path,
    api_key_file: Path | None,
    output: Path,
    model: str,
    size: str,
    quality: str,
    background: str,
    output_format: str,
    product_profile: Path | None,
    product_profile_id: str | None,
    profile_layer: str | None,
    pet_name: str | None = None,
    pet_name_substituted: bool = False,
    provider: str = "openai",
) -> dict[str, Any]:
    operation = "edit" if samples or pet is not None else "generation"
    input_fidelity = (
        "model default (high fidelity)"
        if model == "gpt-image-2" or model.startswith("gpt-image-2-")
        else "high"
        if provider == "openai"
        else "provider managed"
    )
    return {
        "reference_designs": [str(sample) for sample in samples],
        "pet_image": str(pet) if pet else None,
        "pet_name": pet_name,
        "pet_name_substituted": pet_name_substituted,
        "prompt_file": str(prompt_file),
        "api_key_source": (
            str(api_key_file)
            if api_key_file
            else "GEMINI_API_KEY or GOOGLE_API_KEY"
            if provider == "gemini"
            else "OPENAI_API_KEY"
        ),
        "output": str(output),
        "product_profile": str(product_profile) if product_profile else None,
        "product_profile_id": product_profile_id,
        "profile_layer": profile_layer,
        "operation": operation,
        "provider": provider,
        "model": model,
        "size": size,
        "quality": quality,
        "background": background,
        "input_fidelity": input_fidelity,
        "output_format": output_format,
    }


def _print_request_details(summary: dict[str, Any]) -> None:
    inputs = {
        "reference_designs": summary["reference_designs"],
        "pet_image": summary["pet_image"],
        "pet_name": summary["pet_name"],
        "pet_name_substituted": summary["pet_name_substituted"],
        "prompt_file": summary["prompt_file"],
        "api_key_source": summary["api_key_source"],
        "output": summary["output"],
        "product_profile": summary["product_profile"],
        "product_profile_id": summary["product_profile_id"],
        "profile_layer": summary["profile_layer"],
        "operation": summary["operation"],
    }
    images = (
        [summary["pet_image"]] if summary["pet_image"] is not None else []
    ) + list(summary["reference_designs"])
    if summary["provider"] == "gemini":
        api_parameters = {
            "provider": "gemini",
            "model": summary["model"],
            "input_images": images,
            "response_format": {
                "type": "image",
                "aspect_ratio": gemini_aspect_ratio(summary["size"]),
                "image_size": gemini_image_size(
                    summary["size"], summary["model"]
                ),
            },
            "postprocess_size": summary["size"],
            "postprocess_format": summary["output_format"],
            "background": f"prompt-driven ({summary['background']})",
        }
    else:
        api_parameters = {
            "provider": "openai",
            "endpoint": (
                "images.edit"
                if summary["operation"] == "edit"
                else "images.generate"
            ),
            "model": summary["model"],
            "quality": summary["quality"],
            "size": summary["size"],
            "background": summary["background"],
            "output_format": summary["output_format"],
            "n": 1,
        }
        if summary["operation"] == "edit":
            api_parameters["image"] = images
        if summary["operation"] == "edit" and summary["input_fidelity"] == "high":
            api_parameters["input_fidelity"] = "high"
    print("Input parameters:", file=sys.stderr)
    print(json.dumps(inputs, indent=2), file=sys.stderr)
    print("API parameters (prompt omitted):", file=sys.stderr)
    print(json.dumps(api_parameters, indent=2), file=sys.stderr, flush=True)


def _decode_and_validate_image(
    encoded: str,
    *,
    output_format: str,
    background: str,
    size: str,
    provider: str = "openai",
) -> bytes:
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"{provider.title()} returned invalid base64 image data") from exc
    if not image_bytes:
        raise RuntimeError(f"{provider.title()} returned an empty image")

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            if provider == "gemini":
                if background == "transparent" and "A" not in image.getbands():
                    print(
                        "Warning: Gemini did not return an alpha channel; the API "
                        "does not provide a native transparency control.",
                        file=sys.stderr,
                        flush=True,
                    )
                normalized = image.convert("RGBA")
                if size != "auto" and re.fullmatch(r"\d+x\d+", size):
                    width, height = (int(value) for value in size.split("x", 1))
                    fitted = ImageOps.contain(
                        normalized,
                        (width, height),
                        method=Image.Resampling.LANCZOS,
                    )
                    fill = (
                        (0, 0, 0, 0)
                        if background == "transparent"
                        else (255, 255, 255, 255)
                    )
                    canvas = Image.new("RGBA", (width, height), fill)
                    canvas.alpha_composite(
                        fitted,
                        ((width - fitted.width) // 2, (height - fitted.height) // 2),
                    )
                    normalized = canvas
                expected_format = _api_output_format(output_format)
                if expected_format == "jpeg":
                    normalized = normalized.convert("RGB")
                buffer = BytesIO()
                normalized.save(buffer, format=expected_format.upper())
                image_bytes = buffer.getvalue()
            else:
                actual_format = (image.format or "").lower()
                expected_format = _api_output_format(output_format)
                if actual_format != expected_format:
                    raise RuntimeError(
                        f"OpenAI returned {actual_format or 'unknown'} instead of {expected_format}"
                    )
            if size != "auto" and re.fullmatch(r"\d+x\d+", size):
                with Image.open(BytesIO(image_bytes)) as validated:
                    width, height = (int(value) for value in size.split("x", 1))
                    actual_size = validated.size
                if actual_size != (width, height):
                    raise RuntimeError(
                        f"{provider.title()} returned {actual_size[0]}x{actual_size[1]}; "
                        f"expected {size}"
                    )
            if background == "transparent":
                with Image.open(BytesIO(image_bytes)) as validated:
                    if "A" not in validated.getbands():
                        raise RuntimeError(
                            f"{provider.title()} returned an image without an alpha channel"
                        )
    except UnidentifiedImageError as exc:
        raise RuntimeError(
            f"{provider.title()} returned data that is not a supported image"
        ) from exc
    return image_bytes


def _atomic_write_bytes(output: Path, contents: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output.stem}-",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as temp:
            temp.write(contents)
            temp.flush()
            os.fsync(temp.fileno())
            temp_name = temp.name
        os.replace(temp_name, output)
        temp_name = None
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def _generate_empty_canvas(args: argparse.Namespace) -> Path:
    incompatible = {
        "--prompt-file": getattr(args, "prompt_file", None),
        "--reference-design": getattr(args, "reference_design", None),
        "--pet-image": getattr(args, "pet_image", None),
        "--pet-name": getattr(args, "pet_name", None),
        "--api-key-file": getattr(args, "api_key_file", None),
    }
    supplied = [option for option, value in incompatible.items() if value]
    if supplied:
        raise UserInputError(
            "--empty-canvas cannot be combined with " + ", ".join(supplied)
        )
    if args.output_format != "png":
        raise UserInputError("--empty-canvas requires --output-format png")

    product_profile_arg = getattr(args, "product_profile", None)
    profile_layer = getattr(args, "profile_layer", None)
    if (product_profile_arg is None) != (profile_layer is None):
        raise UserInputError(
            "--product-profile and --profile-layer must be supplied together"
        )
    product_profile_path: Path | None = None
    product_profile_id: str | None = None
    requested_size = args.size
    if product_profile_arg is not None:
        if args.size != "auto":
            raise UserInputError(
                "--size cannot be combined with --product-profile; the profile owns "
                "the selected layer dimensions"
            )
        try:
            product_profile = load_product_profile(product_profile_arg)
        except ProductProfileError as exc:
            raise UserInputError(str(exc)) from exc
        product_profile_path = product_profile.path
        product_profile_id = product_profile.profile_id
        requested_size = (
            product_profile.preview_art_size.api_value()
            if profile_layer == "art"
            else product_profile.preview_pet_size.api_value()
        )
    if requested_size == "auto":
        raise UserInputError(
            "--empty-canvas requires --size WIDTHxHEIGHT or "
            "--product-profile with --profile-layer"
        )
    try:
        canvas_size = parse_image_size(requested_size, "--size")
    except ImageSizeError as exc:
        raise UserInputError(str(exc)) from exc

    output = _output_path(
        args.output_dir,
        args.output_name,
        "empty-canvas",
        "png",
    )
    summary = {
        "operation": "empty-canvas",
        "provider": None,
        "api_key_source": None,
        "size": canvas_size.api_value(),
        "output_format": "png",
        "pixel_value": [0, 0, 0, 0],
        "product_profile": (
            str(product_profile_path) if product_profile_path else None
        ),
        "product_profile_id": product_profile_id,
        "profile_layer": profile_layer,
        "output": str(output),
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2))
        return output
    if output.exists() and not args.force:
        raise UserInputError(
            f"output already exists: {output} (pass --force to replace it)"
        )

    print("Empty canvas parameters:", file=sys.stderr)
    print(json.dumps(summary, indent=2), file=sys.stderr, flush=True)
    canvas = Image.new(
        "RGBA",
        (canvas_size.width, canvas_size.height),
        (0, 0, 0, 0),
    )
    encoded = BytesIO()
    canvas.save(encoded, format="PNG")
    image_bytes = encoded.getvalue()
    with Image.open(BytesIO(image_bytes)) as validation:
        validation.load()
        extrema = validation.convert("RGBA").getextrema()
        if validation.size != (canvas_size.width, canvas_size.height) or any(
            channel != (0, 0) for channel in extrema
        ):
            raise RuntimeError(
                "deterministic empty canvas validation failed; expected every "
                "pixel to be RGBA (0, 0, 0, 0)"
            )
    _atomic_write_bytes(output, image_bytes)
    print(f"Saved deterministic empty canvas: {output}", file=sys.stderr, flush=True)
    return output


def generate(args: argparse.Namespace, client: Any | None = None) -> Path:
    if getattr(args, "empty_canvas", False):
        return _generate_empty_canvas(args)
    if getattr(args, "prompt_file", None) is None:
        raise UserInputError("--prompt-file is required unless --empty-canvas is used")
    provider, model = _resolve_provider_model(
        getattr(args, "provider", "auto"), getattr(args, "model", None)
    )
    raw_samples = getattr(args, "reference_design", None)
    sample_values = (
        []
        if raw_samples is None
        else list(raw_samples)
        if isinstance(raw_samples, (list, tuple))
        else [raw_samples]
    )
    samples = [
        _validate_image(sample, f"reference design {index}")
        for index, sample in enumerate(sample_values, start=1)
    ]
    pet = (
        _validate_image(args.pet_image, "pet image")
        if args.pet_image is not None
        else None
    )
    prompt_file, user_prompt = _read_prompt(args.prompt_file)
    _validate_prompt_category(prompt_file, provider)
    user_prompt, resolved_pet_name, pet_name_substituted = _substitute_pet_name(
        user_prompt,
        pet_name=getattr(args, "pet_name", None),
        pet=pet,
    )
    product_profile_arg = getattr(args, "product_profile", None)
    profile_layer = getattr(args, "profile_layer", None)
    if (product_profile_arg is None) != (profile_layer is None):
        raise UserInputError(
            "--product-profile and --profile-layer must be supplied together"
        )
    product_profile_path: Path | None = None
    product_profile_id: str | None = None
    requested_size = args.size
    if product_profile_arg is not None:
        if args.size != "auto":
            raise UserInputError(
                "--size cannot be combined with --product-profile; the profile owns "
                "the selected layer dimensions"
            )
        try:
            product_profile = load_product_profile(product_profile_arg)
        except ProductProfileError as exc:
            raise UserInputError(str(exc)) from exc
        product_profile_path = product_profile.path
        product_profile_id = product_profile.profile_id
        requested_size = (
            product_profile.preview_art_size.api_value()
            if profile_layer == "art"
            else product_profile.preview_pet_size.api_value()
        )
    try:
        request_size = validate_generation_size(
            requested_size,
            model=model,
            label=(
                f"product profile preview.{profile_layer}"
                if product_profile_path is not None
                else "--size"
            ),
        )
    except ImageSizeError as exc:
        raise UserInputError(str(exc)) from exc
    if profile_layer == "art":
        _warn_art_reference_aspect_mismatch(samples, request_size)
    api_key_file, api_key = _resolve_api_key(args.api_key_file, provider=provider)
    api_output_format = _api_output_format(args.output_format)
    primary_input = samples[0] if samples else pet
    output = _output_path(
        args.output_dir,
        args.output_name,
        primary_input.stem if primary_input is not None else prompt_file.stem,
        args.output_format,
    )
    background = _background_from_prompt(user_prompt, args.background)
    if api_output_format == "jpeg" and background == "transparent":
        raise UserInputError(
            "JPEG does not support transparent backgrounds; use --output-format png "
            "or webp, or select --background opaque/auto"
        )

    summary = _request_summary(
        samples=samples,
        pet=pet,
        prompt_file=prompt_file,
        api_key_file=api_key_file,
        output=output,
        model=model,
        size=request_size,
        quality=args.quality,
        background=background,
        output_format=api_output_format,
        product_profile=product_profile_path,
        product_profile_id=product_profile_id,
        profile_layer=profile_layer,
        pet_name=(
            resolved_pet_name
            if pet_name_substituted
            else getattr(args, "pet_name", None)
        ),
        pet_name_substituted=pet_name_substituted,
        provider=provider,
    )
    if args.dry_run:
        print(json.dumps(summary, indent=2))
        return output

    if output.exists() and not args.force:
        raise UserInputError(
            f"output already exists: {output} (pass --force to replace it)"
        )
    _print_request_details(summary)

    if client is None:
        if provider == "gemini":
            client = _GeminiRestClient(api_key)
        else:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)

    image_paths = ([pet] if pet is not None else []) + samples
    api_prompt = _api_prompt(user_prompt, samples=samples, pet=pet)
    if provider == "gemini":
        input_items: list[dict[str, str]] = [
            {
                "type": "text",
                "text": _gemini_prompt(
                    api_prompt,
                    background,
                    prompt_only=not image_paths,
                ),
            }
        ]
        input_items.extend(
            {
                "type": "image",
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                "mime_type": _image_mime_type(path),
            }
            for path in image_paths
        )
        # Interactions currently supports image/jpeg as its only explicit image
        # response MIME type. Omit the field and convert the result locally to
        # the caller's requested PNG/JPEG/WebP format.
        response_format: dict[str, str] = {"type": "image"}
        aspect_ratio = gemini_aspect_ratio(request_size)
        image_size = gemini_image_size(request_size, model)
        if aspect_ratio is not None:
            response_format["aspect_ratio"] = aspect_ratio
        if image_size is not None:
            response_format["image_size"] = image_size
        with _ProgressReporter(
            provider,
            operation="edit" if image_paths else "generation",
        ):
            result = client.interactions.create(
                model=model,
                input=input_items,
                response_format=response_format,
            )
        output_image = getattr(result, "output_image", None)
        encoded = getattr(output_image, "data", None)
        if not encoded:
            raise RuntimeError("Gemini returned no generated image")
    elif image_paths:
        with ExitStack() as stack:
            image_files = [stack.enter_context(path.open("rb")) for path in image_paths]
            request: dict[str, Any] = dict(
                model=model,
                image=image_files,
                prompt=api_prompt,
                quality=args.quality,
                size=request_size,
                background=background,
                output_format=api_output_format,
                n=1,
            )
            if not (model == "gpt-image-2" or model.startswith("gpt-image-2-")):
                request["input_fidelity"] = "high"
            with _ProgressReporter(provider):
                result = client.images.edit(**request)
    else:
        request = dict(
            model=model,
            prompt=api_prompt,
            quality=args.quality,
            size=request_size,
            background=background,
            output_format=api_output_format,
            n=1,
        )
        with _ProgressReporter(provider, operation="generation"):
            result = client.images.generate(**request)

    if provider == "openai":
        if not getattr(result, "data", None):
            raise RuntimeError("OpenAI returned no generated image")
        encoded = getattr(result.data[0], "b64_json", None)
        if not encoded:
            raise RuntimeError("OpenAI response did not contain base64 image data")
    print("Validating and saving generated image...", file=sys.stderr, flush=True)
    image_bytes = _decode_and_validate_image(
        encoded,
        output_format=args.output_format,
        background=background,
        size=request_size,
        provider=provider,
    )
    _atomic_write_bytes(output, image_bytes)
    print(f"Saved generated image: {output}", file=sys.stderr, flush=True)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output = generate(args)
    except UserInputError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        return report_unexpected("pawmarvel-generate", exc, debug=args.debug)

    if not args.dry_run:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
