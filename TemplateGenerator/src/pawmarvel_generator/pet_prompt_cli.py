from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError

from .cli import (
    PROGRESS_INTERVAL_SECONDS,
    _atomic_write_bytes,
    _resolve_api_key,
    _validate_image,
)


class PetPromptError(ValueError):
    """A pet-prompt authoring input or model response is invalid."""


DIRECT_INSTRUCTIONS = """You are an expert prompt engineer for GPT Image 2.

Write the final runtime prompt that transforms one customer pet photo into an isolated replacement pet graphic for a personalized product template. The runtime image-generation call receives exactly one image: the customer's pet identity photo. It will not receive the sample design, art template, or this analysis conversation.

Study the supplied SAMPLE DESIGN and identify only the replaceable example pet. If an ART TEMPLATE is supplied, use it only to distinguish fixed artwork from the pet; it may differ because it was independently recreated. If an APPROVED BASELINE PROMPT is supplied, use its concise structure and successful prompting patterns while deriving all template-specific visual directions from the current sample.

The final prompt must preserve the customer's recognizable pet identity while reproducing the example pet's observable rendering medium, restricted palette, stroke construction, shading, texture, detail level, posture, expression, viewpoint, crop, silhouette, and composition. Resolve contradictions instead of listing them. Prefer concise, high-signal image-generation language over exhaustive visual analysis. State allowed colors and prohibited natural colors explicitly when the sample uses a restricted palette. Distinguish grouped or simplified marks from fine engraving or photorealistic detail.

Require one isolated pet only on a genuine transparent background. Exclude fixed template artwork, names, slogans, scenery, product mockups, rectangular backdrops, checkerboards, shadows that create a background plane, and extra objects. Keep the complete intended silhouette unclipped with transparent margin.

Return only the complete self-contained runtime prompt. Do not return analysis, commentary, JSON, a code fence, or instructions that refer to unavailable images. Keep it between 180 and 450 words.
"""


DIRECT_TASK = """Create the final reusable GPT Image 2 pet-transformation prompt now. The generated prompt must assume that its only uploaded image is the new customer's pet identity photo."""


CRITIC_INSTRUCTIONS = """You are the final quality editor for a GPT Image 2 pet-transformation prompt.

Study the supplied SAMPLE DESIGN, optional ART TEMPLATE, optional approved baseline, and DRAFT RUNTIME PROMPT. Rewrite the draft only where necessary so it reliably instructs GPT Image 2 to preserve the new pet's identity while matching the example pet's style, pose, expression, crop, and silhouette.

Remove repetition, vague prose, unavailable-image references, and conflicting instructions. Correct palette leakage, excessive fine-detail or engraving language, incorrect anatomy or crop, weak transparency requirements, and accidental inclusion of fixed design artwork. Use concise, high-signal image-generation language. The runtime receives exactly one image: the customer's pet photo.

Return only the complete revised runtime prompt. Do not return a critique, analysis, JSON, or a code fence. Keep it between 180 and 450 words.
"""


ANALYSIS_INSTRUCTIONS = """You are defining a reusable pet-image transformation style for a personalized product template.

You receive exactly two labeled images:
1. SAMPLE DESIGN: the complete example design containing an example pet plus fixed artwork and possibly personalized text.
2. ART TEMPLATE: the reusable fixed artwork with the example pet removed. It may differ slightly because it was independently recreated.

Semantically compare the images. Analyze only the example pet that must be replaced. Do not treat names, slogans, rainbows, paw prints, borders, or other fixed artwork as part of the pet. Describe the target pet's visual treatment, pose, posture, expression, view, crop, silhouette, composition, and visible anatomy precisely enough that an image-generation model can apply them to a different pet from one identity-reference photo.

Separate identity from treatment. The future result must retain the new input pet's breed/species, facial structure, muzzle, eyes, ears, fur, markings, colors when compatible with the target medium, and other recognizable traits. Pose, expression, crop, rendering medium, palette, and texture come from the example pet.

Be concrete and visually observable. Do not mention these source images, file names, comparison, or unavailable references in any field; the final transformation prompt must be self-contained. Do not guess hidden anatomy or identify a real artist. Return only the requested structured analysis.
"""


ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["style", "pose", "identity_priorities", "example_exclusions"],
    "properties": {
        "style": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "medium",
                "palette",
                "linework",
                "shading",
                "texture",
                "detail_emphasis",
            ],
            "properties": {
                "medium": {"type": "string"},
                "palette": {"type": "string"},
                "linework": {"type": "string"},
                "shading": {"type": "string"},
                "texture": {"type": "string"},
                "detail_emphasis": {"type": "string"},
            },
        },
        "pose": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "viewpoint",
                "head_and_body_orientation",
                "visible_anatomy",
                "crop_boundaries",
                "expression",
                "eyes_mouth_and_ears",
                "composition_and_silhouette",
            ],
            "properties": {
                "viewpoint": {"type": "string"},
                "head_and_body_orientation": {"type": "string"},
                "visible_anatomy": {"type": "string"},
                "crop_boundaries": {"type": "string"},
                "expression": {"type": "string"},
                "eyes_mouth_and_ears": {"type": "string"},
                "composition_and_silhouette": {"type": "string"},
            },
        },
        "identity_priorities": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 5,
            "maxItems": 12,
        },
        "example_exclusions": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 12,
        },
    },
}


EXPECTED_STYLE_KEYS = {
    "medium",
    "palette",
    "linework",
    "shading",
    "texture",
    "detail_emphasis",
}
EXPECTED_POSE_KEYS = {
    "viewpoint",
    "head_and_body_orientation",
    "visible_anatomy",
    "crop_boundaries",
    "expression",
    "eyes_mouth_and_ears",
    "composition_and_silhouette",
}


class _ResponseProgress:
    def __init__(self, label: str) -> None:
        self.label = label
        self.started = 0.0
        self.finished = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self) -> "_ResponseProgress":
        self.started = time.monotonic()
        print(
            f"Submitting {self.label} request to OpenAI...",
            file=sys.stderr,
            flush=True,
        )
        self.thread = threading.Thread(target=self._report, daemon=True)
        self.thread.start()
        return self

    def _report(self) -> None:
        while not self.finished.wait(PROGRESS_INTERVAL_SECONDS):
            elapsed = int(time.monotonic() - self.started)
            print(
                f"{self.label.capitalize()} is still in progress ({elapsed}s elapsed)...",
                file=sys.stderr,
                flush=True,
            )

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.finished.set()
        if self.thread is not None:
            self.thread.join()
        elapsed = int(time.monotonic() - self.started)
        status = (
            f"{self.label.capitalize()} response received"
            if exc_type is None
            else f"{self.label.capitalize()} stopped"
        )
        print(f"{status} after {elapsed}s.", file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-pet-prompt",
        description=(
            "Create a self-contained, one-pet-input GPT Image 2 transformation "
            "prompt from a sample design."
        ),
    )
    parser.add_argument("--sample-design", type=Path, required=True)
    parser.add_argument(
        "--art",
        type=Path,
        help=(
            "optional approved background-only art; helps distinguish fixed "
            "artwork from the replaceable pet"
        ),
    )
    parser.add_argument(
        "--api-key-file",
        type=Path,
        help="plain-text or RTF OpenAI API key file; defaults to OPENAI_API_KEY",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--analysis-output",
        type=Path,
        help="provenance/analysis JSON (default: <output stem>.analysis.json)",
    )
    parser.add_argument("--model", default="gpt-5.6")
    parser.add_argument(
        "--strategy",
        choices=("direct", "structured"),
        default="direct",
        help="direct model-authored prompt (default) or legacy structured compiler",
    )
    parser.add_argument(
        "--critic-pass",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "run a second visual prompt-revision call; defaults on for direct "
            "and off for structured"
        ),
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        default="high",
    )
    parser.add_argument(
        "--baseline-prompt",
        type=Path,
        help="optional approved prompt used as a concise authoring example",
    )
    parser.add_argument(
        "--image-detail",
        choices=("low", "high", "original", "auto"),
        default="original",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and show request settings without calling the API",
    )
    return parser


def _decode_image(path: Path, label: str) -> tuple[Path, tuple[int, int], str]:
    path = _validate_image(path, label)
    try:
        with Image.open(path) as image:
            image.load()
            image_format = (image.format or "").lower()
            if image_format not in {"png", "jpeg", "webp"}:
                raise PetPromptError(f"{label} must decode as PNG, JPEG, or WebP")
            return path, image.size, image_format
    except UnidentifiedImageError as exc:
        raise PetPromptError(f"{label} is not a supported image: {path}") from exc


def _data_url(path: Path, image_format: str) -> str:
    mime = "image/jpeg" if image_format == "jpeg" else f"image/{image_format}"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _output_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    output = args.output.expanduser().resolve()
    analysis_output = (
        args.analysis_output.expanduser().resolve()
        if args.analysis_output is not None
        else output.with_name(f"{output.stem}.analysis.json")
    )
    if output.suffix.lower() != ".md":
        raise PetPromptError("--output must use the .md suffix")
    if analysis_output.suffix.lower() != ".json":
        raise PetPromptError("--analysis-output must use the .json suffix")
    if output == analysis_output:
        raise PetPromptError("prompt and analysis output paths must differ")
    existing = [path for path in (output, analysis_output) if path.exists()]
    if existing and not args.force:
        raise PetPromptError(
            "output already exists; pass --force to replace it: "
            + ", ".join(str(path) for path in existing)
        )
    return output, analysis_output


def _validate_aspect_ratios(
    sample_size: tuple[int, int], art_size: tuple[int, int]
) -> None:
    sample_ratio = sample_size[0] / sample_size[1]
    art_ratio = art_size[0] / art_size[1]
    difference = abs(sample_ratio - art_ratio) / art_ratio
    if difference > 0.02:
        raise PetPromptError(
            "sample design and art aspect ratios differ by more than 2%; "
            "provide matching flat-design crops"
        )


def _require_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PetPromptError(f"analysis.{label} must be an object")
    return value


def _validate_text_object(
    value: Any, expected_keys: set[str], label: str
) -> dict[str, str]:
    data = _require_object(value, label)
    if set(data) != expected_keys:
        raise PetPromptError(f"analysis.{label} has missing or unsupported fields")
    result: dict[str, str] = {}
    for key in sorted(expected_keys):
        field = data[key]
        if not isinstance(field, str) or not field.strip():
            raise PetPromptError(f"analysis.{label}.{key} must be nonempty text")
        result[key] = " ".join(field.split())
    return result


def _validate_text_list(
    value: Any, label: str, minimum: int, maximum: int
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise PetPromptError(
            f"analysis.{label} must contain {minimum} to {maximum} items"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PetPromptError(f"analysis.{label} items must be nonempty text")
        result.append(" ".join(item.split()))
    return result


def _validate_analysis(value: Any) -> dict[str, Any]:
    data = _require_object(value, "root")
    expected = {"style", "pose", "identity_priorities", "example_exclusions"}
    if set(data) != expected:
        raise PetPromptError("analysis has missing or unsupported top-level fields")
    return {
        "style": _validate_text_object(data["style"], EXPECTED_STYLE_KEYS, "style"),
        "pose": _validate_text_object(data["pose"], EXPECTED_POSE_KEYS, "pose"),
        "identity_priorities": _validate_text_list(
            data["identity_priorities"], "identity_priorities", 5, 12
        ),
        "example_exclusions": _validate_text_list(
            data["example_exclusions"], "example_exclusions", 1, 12
        ),
    }


def _bullet_lines(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)


def _render_prompt(analysis: Mapping[str, Any]) -> str:
    style = analysis["style"]
    pose = analysis["pose"]
    identity = analysis["identity_priorities"]
    exclusions = analysis["example_exclusions"]
    return f"""# GPT Image 2 Prompt — Isolated Personalized Pet Asset

## Input contract

The single uploaded image is the identity reference for the customer's pet. There is no design-example image available during this generation.

Generate only one isolated transformed pet portrait. Replace no background artwork and create no complete product design.

## Identity priority

The result must remain unmistakably the same individual pet shown in the uploaded image. Preserve all observable identity evidence before applying the target treatment:

{_bullet_lines(identity)}
- Preserve breed/species, facial geometry, muzzle and nose, eye shape and spacing, ears, fur type and length, major markings, and distinctive asymmetry visible in the input.
- Preserve the spatial distribution of coat colors and markings; when the target medium uses a restricted palette, translate those differences into tone, line density, or negative space instead of erasing them.
- Preserve skull, muzzle, eye, and ear proportions even while reconstructing the target expression and posture.
- Do not replace the pet with a generic animal of the same breed.
- Do not invent markings or anatomy that are not supported by the uploaded image.

## Required target posture, expression, and crop

- Viewpoint: {pose['viewpoint']}
- Head and body orientation: {pose['head_and_body_orientation']}
- Visible anatomy: {pose['visible_anatomy']}
- Crop boundaries: {pose['crop_boundaries']}
- Expression: {pose['expression']}
- Eyes, mouth, and ears: {pose['eyes_mouth_and_ears']}
- Composition and silhouette: {pose['composition_and_silhouette']}

Match these pose, expression, framing, and silhouette requirements closely while retaining the input pet's real anatomy and recognizable identity. If the source photo uses another pose, reconstruct the target pose without changing identity-defining features.

## Required rendering style

- Medium: {style['medium']}
- Palette: {style['palette']}
- Linework: {style['linework']}
- Shading: {style['shading']}
- Texture: {style['texture']}
- Detail emphasis: {style['detail_emphasis']}

Apply the style consistently to the pet itself. Do not copy unrelated design elements into the pet asset.

## Exclude example-specific and fixed-design content

{_bullet_lines(exclusions)}
- No names, slogans, lettering, rainbow, paw prints, borders, frames, scenery, garment, product mockup, or decorative background.
- No extra animal, duplicated anatomy, collar, tag, clothing, or accessory unless it is an identity-critical feature clearly present in the uploaded pet and compatible with the target portrait.

## Output contract

- Output one pet portrait only on a fully transparent background.
- Preserve a real alpha channel with transparent pixels around the complete visible pet.
- Do not output white, black, colored, textured, checkerboard, or rectangular backgrounds.
- Keep all intended ears, fur, outlines, texture, and visible anatomy inside the canvas with comfortable transparent margin.
- Use one centered composition suitable for alpha trimming and proportional placement into a local template.
- Do not include cast shadows or glow that create a background plane.

Before returning the image, verify: one pet only; recognizable input-pet identity; required target pose, expression, crop, and style; no fixed artwork or text; and a genuinely transparent background.
"""


def _read_baseline(path: Path | None) -> tuple[Path, str] | tuple[None, None]:
    if path is None:
        return None, None
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise PetPromptError(f"baseline prompt does not exist: {resolved}")
    text = resolved.read_text(encoding="utf-8").strip()
    if not text:
        raise PetPromptError(f"baseline prompt is empty: {resolved}")
    return resolved, text


def _critic_enabled(args: argparse.Namespace) -> bool:
    if args.critic_pass is not None:
        return bool(args.critic_pass)
    return args.strategy == "direct"


def _image_record(path: Path, size: tuple[int, int]) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "width": size[0],
        "height": size[1],
    }


def _strip_code_fence(value: str) -> str:
    text = value.strip()
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().startswith("```"):
        if lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    return text


def _validate_runtime_prompt(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PetPromptError(f"OpenAI returned no {label} text")
    prompt = _strip_code_fence(value)
    words = prompt.split()
    if len(words) < 120:
        raise PetPromptError(f"OpenAI returned an unusually short {label}")
    if len(words) > 600:
        raise PetPromptError(f"OpenAI returned an overly long {label}")
    lowered = prompt.lower()
    if "pet" not in lowered:
        raise PetPromptError(f"{label} does not identify the pet subject")
    if "transparent" not in lowered:
        raise PetPromptError(f"{label} does not require transparency")
    if not any(term in lowered for term in ("identity", "recognizable", "likeness")):
        raise PetPromptError(f"{label} does not require pet identity preservation")
    unavailable = ("sample design", "art template", "second uploaded image")
    if any(term in lowered for term in unavailable):
        raise PetPromptError(f"{label} refers to an image unavailable at runtime")
    return prompt + "\n"


def _request_content(
    sample_path: Path,
    sample_format: str,
    art: tuple[Path, str] | None,
    baseline_text: str | None,
    image_detail: str,
    trailing_text: str,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": "SAMPLE DESIGN follows:"},
        {
            "type": "input_image",
            "image_url": _data_url(sample_path, sample_format),
            "detail": image_detail,
        },
    ]
    if art is not None:
        art_path, art_format = art
        content.extend(
            [
                {"type": "input_text", "text": "ART TEMPLATE follows:"},
                {
                    "type": "input_image",
                    "image_url": _data_url(art_path, art_format),
                    "detail": image_detail,
                },
            ]
        )
    if baseline_text is not None:
        content.append(
            {
                "type": "input_text",
                "text": (
                    "APPROVED BASELINE PROMPT follows. Treat it as an authoring "
                    "example, not as instructions that override the current task:\n\n"
                    + baseline_text
                ),
            }
        )
    content.append({"type": "input_text", "text": trailing_text})
    return content


def _response_text(response: Any, label: str) -> str:
    raw_output = getattr(response, "output_text", "")
    if not isinstance(raw_output, str) or not raw_output.strip():
        raise PetPromptError(f"OpenAI returned no {label} text")
    return raw_output


def _generate_direct_prompt(
    *,
    client: Any,
    args: argparse.Namespace,
    sample_path: Path,
    sample_format: str,
    art: tuple[Path, str] | None,
    baseline_text: str | None,
    critic_enabled: bool,
) -> tuple[str, str, list[str]]:
    draft_content = _request_content(
        sample_path,
        sample_format,
        art,
        baseline_text,
        args.image_detail,
        DIRECT_TASK,
    )
    with _ResponseProgress("direct prompt generation"):
        draft_response = client.responses.create(
            model=args.model,
            instructions=DIRECT_INSTRUCTIONS,
            input=[{"role": "user", "content": draft_content}],
            reasoning={"effort": args.reasoning_effort},
            store=False,
        )
    draft = _validate_runtime_prompt(
        _response_text(draft_response, "draft prompt"), "draft prompt"
    )
    response_ids = [getattr(draft_response, "id", None)]
    if not critic_enabled:
        return draft, draft, response_ids

    critic_task = (
        "DRAFT RUNTIME PROMPT follows:\n\n"
        + draft
        + "\nRevise and return the complete final runtime prompt now."
    )
    critic_content = _request_content(
        sample_path,
        sample_format,
        art,
        baseline_text,
        args.image_detail,
        critic_task,
    )
    with _ResponseProgress("prompt critic"):
        critic_response = client.responses.create(
            model=args.model,
            instructions=CRITIC_INSTRUCTIONS,
            input=[{"role": "user", "content": critic_content}],
            reasoning={"effort": args.reasoning_effort},
            store=False,
        )
    final_prompt = _validate_runtime_prompt(
        _response_text(critic_response, "critic prompt"), "critic prompt"
    )
    response_ids.append(getattr(critic_response, "id", None))
    return draft, final_prompt, response_ids


def _generate_structured_prompt(
    *,
    client: Any,
    args: argparse.Namespace,
    sample_path: Path,
    sample_format: str,
    art_path: Path,
    art_format: str,
) -> tuple[str, dict[str, Any], list[str]]:
    request_input = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "SAMPLE DESIGN follows:"},
                {
                    "type": "input_image",
                    "image_url": _data_url(sample_path, sample_format),
                    "detail": args.image_detail,
                },
                {"type": "input_text", "text": "ART TEMPLATE follows:"},
                {
                    "type": "input_image",
                    "image_url": _data_url(art_path, art_format),
                    "detail": args.image_detail,
                },
            ],
        }
    ]
    with _ResponseProgress("structured template analysis"):
        response = client.responses.create(
            model=args.model,
            instructions=ANALYSIS_INSTRUCTIONS,
            input=request_input,
            reasoning={"effort": args.reasoning_effort},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "pet_transformation_analysis",
                    "strict": True,
                    "schema": ANALYSIS_SCHEMA,
                }
            },
            store=False,
        )
    raw_output = _response_text(response, "structured analysis")
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise PetPromptError("OpenAI returned invalid analysis JSON") from exc
    analysis = _validate_analysis(parsed)
    return _render_prompt(analysis), analysis, [getattr(response, "id", None)]


def derive_prompt(
    args: argparse.Namespace, client: Any | None = None
) -> tuple[Path, Path] | None:
    sample_path, sample_size, sample_format = _decode_image(
        args.sample_design, "sample design"
    )
    art_path: Path | None = None
    art_size: tuple[int, int] | None = None
    art_format: str | None = None
    if args.art is not None:
        art_path, art_size, art_format = _decode_image(args.art, "art")
        _validate_aspect_ratios(sample_size, art_size)
    baseline_path, baseline_text = _read_baseline(args.baseline_prompt)
    critic_enabled = _critic_enabled(args)
    if args.strategy == "structured" and art_path is None:
        raise PetPromptError("--strategy structured requires --art")
    if args.strategy == "structured" and baseline_path is not None:
        raise PetPromptError("--baseline-prompt is supported only by --strategy direct")
    if args.strategy == "structured" and critic_enabled:
        raise PetPromptError("--critic-pass is supported only by --strategy direct")
    output, analysis_output = _output_paths(args)
    key_path, api_key = _resolve_api_key(args.api_key_file)

    summary = {
        "sample_design": str(sample_path),
        "sample_size": f"{sample_size[0]}x{sample_size[1]}",
        "art": str(art_path) if art_path else None,
        "art_size": f"{art_size[0]}x{art_size[1]}" if art_size else None,
        "baseline_prompt": str(baseline_path) if baseline_path else None,
        "api_key_source": str(key_path) if key_path else "OPENAI_API_KEY",
        "output": str(output),
        "analysis_output": str(analysis_output),
        "model": args.model,
        "strategy": args.strategy,
        "critic_pass": critic_enabled,
        "reasoning_effort": args.reasoning_effort,
        "image_detail": args.image_detail,
    }
    print("Input and API parameters (image data and instructions omitted):")
    print(json.dumps(summary, indent=2))
    if args.dry_run:
        return None

    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

    analysis: dict[str, Any] | None = None
    if args.strategy == "direct":
        art_input = (
            (art_path, art_format)
            if art_path is not None and art_format is not None
            else None
        )
        draft_prompt, prompt, response_ids = _generate_direct_prompt(
            client=client,
            args=args,
            sample_path=sample_path,
            sample_format=sample_format,
            art=art_input,
            baseline_text=baseline_text,
            critic_enabled=critic_enabled,
        )
    else:
        assert art_path is not None and art_format is not None
        prompt, analysis, response_ids = _generate_structured_prompt(
            client=client,
            args=args,
            sample_path=sample_path,
            sample_format=sample_format,
            art_path=art_path,
            art_format=art_format,
        )
        draft_prompt = prompt

    record = {
        "schema_version": 2,
        "generator": {
            "strategy": args.strategy,
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "image_detail": args.image_detail,
            "critic_pass": critic_enabled,
            "response_ids": response_ids,
        },
        "sources": {
            "sample_design": _image_record(sample_path, sample_size),
            "art": (
                _image_record(art_path, art_size)
                if art_path is not None and art_size is not None
                else None
            ),
            "baseline_prompt": (
                {"path": str(baseline_path), "sha256": _sha256(baseline_path)}
                if baseline_path is not None
                else None
            ),
        },
        "prompt": {
            "draft_sha256": hashlib.sha256(draft_prompt.encode("utf-8")).hexdigest(),
            "final_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "draft_word_count": len(draft_prompt.split()),
            "final_word_count": len(prompt.split()),
            "critic_changed": draft_prompt != prompt,
        },
    }
    if analysis is not None:
        record["analysis"] = analysis
    _atomic_write_bytes(output, prompt.encode("utf-8"))
    _atomic_write_bytes(
        analysis_output, (json.dumps(record, indent=2) + "\n").encode("utf-8")
    )
    print(f"Saved pet transformation prompt: {output}")
    print(f"Saved pet transformation provenance: {analysis_output}")
    return output, analysis_output


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        derive_prompt(args)
        return 0
    except (PetPromptError, OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        request_id = getattr(exc, "request_id", None)
        detail = f" (request ID: {request_id})" if request_id else ""
        print(f"Pet prompt generation failed: {exc}{detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
