# CLI purpose:
# Generate or edit personalized design assets with GPT Image using a sample
# design, a pet image, or both; optionally derive layer size from a reusable
# product profile, then validate and save the returned image.

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
from typing import Any, Sequence

from PIL import Image, UnidentifiedImageError

from .image_size import ImageSizeError, validate_generation_size
from .product_profile import ProductProfileError, load_product_profile


DEFAULT_OUTPUT_DIR = Path("output")
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
OUTPUT_FORMATS = ("png", "jpg", "jpeg", "webp")
API_KEY_PATTERN = re.compile(rb"sk-[A-Za-z0-9_-]{20,}")
MAX_API_KEY_FILE_BYTES = 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 10.0


class UserInputError(ValueError):
    """An actionable command-line input error."""


class _ProgressReporter:
    def __init__(self, interval: float | None = None) -> None:
        self.interval = PROGRESS_INTERVAL_SECONDS if interval is None else interval
        self.started_at = 0.0
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_ProgressReporter":
        self.started_at = time.monotonic()
        print("Submitting image edit request to OpenAI...", file=sys.stderr, flush=True)
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
            "Generate or edit artwork with GPT Image 2 using a sample design, "
            "a pet image, or both."
        ),
    )
    parser.add_argument(
        "--sample-design",
        type=Path,
        action="append",
        help="optional sample design/style reference; repeat for ordered references",
    )
    parser.add_argument(
        "--pet-image",
        type=Path,
        help="optional user pet reference image",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        required=True,
        help="UTF-8 Markdown/text prompt",
    )
    parser.add_argument(
        "--api-key-file",
        type=Path,
        help=(
            "plain-text or RTF file containing exactly one OpenAI API key; "
            "defaults to OPENAI_API_KEY"
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
        help="output filename (default: first supplied image stem and selected suffix)",
    )
    parser.add_argument(
        "--output-format",
        choices=OUTPUT_FORMATS,
        default="png",
        help="generated image format; jpg is an alias for jpeg (default: png)",
    )
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument(
        "--size",
        default="auto",
        help="output size accepted by the selected model (default: auto)",
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
        help="validate inputs and show request settings without calling the API",
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


def _read_prompt(path: Path) -> tuple[Path, str]:
    path = _validate_regular_file(path, "prompt file")
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise UserInputError(f"prompt file must be UTF-8 text: {path}") from exc
    if not prompt:
        raise UserInputError(f"prompt file is empty: {path}")
    return path, prompt


def _read_api_key(path: Path) -> tuple[Path, str]:
    path = _validate_regular_file(path, "API key file")
    size = path.stat().st_size
    if size == 0:
        raise UserInputError(f"API key file is empty: {path}")
    if size > MAX_API_KEY_FILE_BYTES:
        raise UserInputError(f"API key file is unexpectedly large: {path}")

    contents = path.read_bytes()
    matches = {match.decode("ascii") for match in API_KEY_PATTERN.findall(contents)}
    if not matches:
        raise UserInputError(
            f"API key file does not contain a valid sk-... key: {path}"
        )
    if len(matches) != 1:
        raise UserInputError(
            f"API key file must contain exactly one distinct API key: {path}"
        )
    return path, matches.pop()


def _resolve_api_key(path: Path | None) -> tuple[Path | None, str]:
    if path is not None:
        return _read_api_key(path)
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise UserInputError(
            "provide --api-key-file or set the OPENAI_API_KEY environment variable"
        )
    if not API_KEY_PATTERN.fullmatch(key.encode("ascii", errors="ignore")):
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
    primary_input: Path,
    output_format: str,
) -> Path:
    filename = output_name or f"{primary_input.stem}.{output_format}"
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
            "- REFERENCE DESIGN: the second supplied image; use its pet depiction "
            "for style, pose, expression, crop, and treatment, never identity."
        )
        if len(samples) > 1:
            roles.append(
                "- ADDITIONAL REFERENCE DESIGNS: all remaining images, in "
                "supplied order; use them only as supporting treatment evidence."
            )
    elif samples:
        roles.append(
            "- SAMPLE DESIGNS: all supplied images, in supplied order; use them for "
            "style, pose, expression, and treatment."
        )
    elif pet is not None:
        roles.append(
            "- USER PET: the supplied image; use it only for the customer's identity."
        )
    mapping = "\n".join(roles)
    return (
        "INPUT IMAGE MAPPING:\n"
        f"{mapping}\n\n"
        "Use the supplied images according to the production prompt below.\n\n"
        f"{user_prompt}"
    )


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
) -> dict[str, Any]:
    input_fidelity = (
        "model default (high fidelity)"
        if model == "gpt-image-2" or model.startswith("gpt-image-2-")
        else "high"
    )
    return {
        "sample_designs": [str(sample) for sample in samples],
        "pet_image": str(pet) if pet else None,
        "prompt_file": str(prompt_file),
        "api_key_source": str(api_key_file) if api_key_file else "OPENAI_API_KEY",
        "output": str(output),
        "product_profile": str(product_profile) if product_profile else None,
        "product_profile_id": product_profile_id,
        "profile_layer": profile_layer,
        "model": model,
        "size": size,
        "quality": quality,
        "background": background,
        "input_fidelity": input_fidelity,
        "output_format": output_format,
    }


def _print_request_details(summary: dict[str, Any]) -> None:
    inputs = {
        "sample_designs": summary["sample_designs"],
        "pet_image": summary["pet_image"],
        "prompt_file": summary["prompt_file"],
        "api_key_source": summary["api_key_source"],
        "output": summary["output"],
        "product_profile": summary["product_profile"],
        "product_profile_id": summary["product_profile_id"],
        "profile_layer": summary["profile_layer"],
    }
    images = (
        [summary["pet_image"]] if summary["pet_image"] is not None else []
    ) + list(summary["sample_designs"])
    api_parameters = {
        "model": summary["model"],
        "image": images,
        "quality": summary["quality"],
        "size": summary["size"],
        "background": summary["background"],
        "output_format": summary["output_format"],
        "n": 1,
    }
    if summary["input_fidelity"] == "high":
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
) -> bytes:
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("OpenAI returned invalid base64 image data") from exc
    if not image_bytes:
        raise RuntimeError("OpenAI returned an empty image")

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            actual_format = (image.format or "").lower()
            expected_format = _api_output_format(output_format)
            if actual_format != expected_format:
                raise RuntimeError(
                    f"OpenAI returned {actual_format or 'unknown'} instead of {expected_format}"
                )
            if size != "auto" and re.fullmatch(r"\d+x\d+", size):
                width, height = (int(value) for value in size.split("x", 1))
                if image.size != (width, height):
                    raise RuntimeError(
                        f"OpenAI returned {image.width}x{image.height}; expected {size}"
                    )
            if background == "transparent" and "A" not in image.getbands():
                raise RuntimeError("OpenAI returned an image without an alpha channel")
    except UnidentifiedImageError as exc:
        raise RuntimeError("OpenAI returned data that is not a supported image") from exc
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


def generate(args: argparse.Namespace, client: Any | None = None) -> Path:
    raw_samples = getattr(args, "sample_design", None)
    sample_values = (
        []
        if raw_samples is None
        else list(raw_samples)
        if isinstance(raw_samples, (list, tuple))
        else [raw_samples]
    )
    samples = [
        _validate_image(sample, f"sample design {index}")
        for index, sample in enumerate(sample_values, start=1)
    ]
    pet = (
        _validate_image(args.pet_image, "pet image")
        if args.pet_image is not None
        else None
    )
    if not samples and pet is None:
        raise UserInputError("provide at least one of --sample-design or --pet-image")

    prompt_file, user_prompt = _read_prompt(args.prompt_file)
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
            model=args.model,
            label=(
                f"product profile preview.{profile_layer}"
                if product_profile_path is not None
                else "--size"
            ),
        )
    except ImageSizeError as exc:
        raise UserInputError(str(exc)) from exc
    api_key_file, api_key = _resolve_api_key(args.api_key_file)
    api_output_format = _api_output_format(args.output_format)
    primary_input = samples[0] if samples else pet
    assert primary_input is not None
    output = _output_path(
        args.output_dir,
        args.output_name,
        primary_input,
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
        model=args.model,
        size=request_size,
        quality=args.quality,
        background=background,
        output_format=api_output_format,
        product_profile=product_profile_path,
        product_profile_id=product_profile_id,
        profile_layer=profile_layer,
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
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

    with ExitStack() as stack:
        image_paths = ([pet] if pet is not None else []) + samples
        image_files = [stack.enter_context(path.open("rb")) for path in image_paths]
        request: dict[str, Any] = dict(
            model=args.model,
            image=image_files,
            prompt=_api_prompt(user_prompt, samples=samples, pet=pet),
            quality=args.quality,
            size=request_size,
            background=background,
            output_format=api_output_format,
            n=1,
        )
        if not (
            args.model == "gpt-image-2" or args.model.startswith("gpt-image-2-")
        ):
            request["input_fidelity"] = "high"
        with _ProgressReporter():
            result = client.images.edit(**request)

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
        request_id = getattr(exc, "request_id", None)
        detail = f" (request ID: {request_id})" if request_id else ""
        print(f"Generation failed: {exc}{detail}", file=sys.stderr)
        return 1

    if not args.dry_run:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
