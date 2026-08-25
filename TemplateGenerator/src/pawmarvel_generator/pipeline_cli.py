from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from PIL import Image

from .cli import (
    UserInputError,
    _atomic_write_bytes,
    _resolve_api_key,
    _validate_image,
    _validate_regular_file,
    generate,
)
from .config import ConfigError, load_layout
from .layout_server import EditorConfig, serve_layout_editor
from .name_prompt_cli import NamePromptError, configure, create_prompt
from .pet_prompt_cli import PetPromptError, derive_prompt
from .renderer import RenderError, render_to_files


class PipelineError(ValueError):
    """A one-step pipeline input or stage is invalid."""


LayoutRunner = Callable[..., None]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-pipeline",
        description=(
            "Create one low-resolution template asset set and run one tracked "
            "personalization preview."
        ),
    )
    parser.add_argument("--sample-design", type=Path, required=True)
    parser.add_argument(
        "--art-prompt", type=Path, required=True, help="prompt used to create art.png"
    )
    parser.add_argument(
        "--pet-prompt-baseline",
        type=Path,
        help="optional approved example for pet prompt authoring",
    )
    parser.add_argument("--pet-image", type=Path, required=True)
    parser.add_argument("--pet-name", required=True)
    parser.add_argument(
        "--name-method",
        choices=("font", "ai"),
        default="font",
        help="render the name with the layout font or GPT Image 2 (default: font)",
    )
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument(
        "--run-dir",
        type=Path,
        help="tracked preview directory (default: TEMPLATE_DIR/runs/PET-NAME)",
    )
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--image-model", default="gpt-image-2")
    parser.add_argument("--prompt-model", default="gpt-5.6")
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        default="high",
    )
    parser.add_argument(
        "--critic-pass", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument(
        "--art-size",
        help="art output WIDTHxHEIGHT; defaults to a sample-aspect size",
    )
    parser.add_argument("--pet-size", default="1024x1024")
    parser.add_argument(
        "--quality", choices=("low", "medium", "high", "auto"), default="high"
    )
    parser.add_argument(
        "--layout-mode",
        choices=("interactive", "existing"),
        default="interactive",
        help="open the editor or reuse TEMPLATE_DIR/layout.json",
    )
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inputs and print the resolved plan without writing or calling APIs",
    )
    return parser


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return result or "preview"


def _derived_art_size(sample: Path) -> str:
    with Image.open(sample) as image:
        width, height = image.size
    ratio = max(width, height) / min(width, height)
    if ratio > 3:
        raise PipelineError(
            "sample design aspect ratio exceeds 3:1; provide a cropped design image"
        )
    scale = 1024 / min(width, height)
    target_width = max(16, round(width * scale / 16) * 16)
    target_height = max(16, round(height * scale / 16) * 16)
    return f"{target_width}x{target_height}"


def _check_size(value: str, label: str) -> str:
    if not re.fullmatch(r"[1-9]\d*x[1-9]\d*", value):
        raise PipelineError(f"{label} must use WIDTHxHEIGHT")
    return value


def _copy_file(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    _atomic_write_bytes(destination, source.read_bytes())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _generation_args(
    *,
    sample_design: Path | None,
    pet_image: Path | None,
    prompt_file: Path,
    api_key_file: Path | None,
    output_dir: Path,
    output_name: str,
    model: str,
    size: str,
    quality: str,
    background: str,
    force: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        sample_design=sample_design,
        pet_image=pet_image,
        prompt_file=prompt_file,
        api_key_file=api_key_file,
        output_dir=output_dir,
        output_name=output_name,
        output_format="png",
        model=model,
        size=size,
        quality=quality,
        background=background,
        force=force,
        dry_run=False,
    )


def _stage(label: str, number: int, total: int) -> None:
    print(f"\n[{number}/{total}] {label}", file=sys.stderr, flush=True)


def run_pipeline(
    args: argparse.Namespace,
    *,
    client: Any | None = None,
    layout_runner: LayoutRunner = serve_layout_editor,
) -> dict[str, Path]:
    sample = _validate_image(args.sample_design, "sample design")
    art_prompt_source = _validate_regular_file(args.art_prompt, "art prompt")
    pet_source = _validate_image(args.pet_image, "pet image")
    font = _validate_regular_file(args.font, "font")
    baseline_source = (
        _validate_regular_file(args.pet_prompt_baseline, "pet prompt baseline")
        if args.pet_prompt_baseline is not None
        else None
    )
    pet_name = args.pet_name.strip()
    if not pet_name:
        raise PipelineError("--pet-name must not be empty")
    if not 0 <= args.port <= 65535:
        raise PipelineError("--port must be between 0 and 65535")
    art_size = _check_size(args.art_size or _derived_art_size(sample), "--art-size")
    pet_size = _check_size(args.pet_size, "--pet-size")
    template_dir = args.template_dir.expanduser().resolve()
    run_dir = (
        args.run_dir.expanduser().resolve()
        if args.run_dir is not None
        else template_dir / "runs" / _slug(f"{pet_source.stem}-{pet_name}")
    )

    reference = template_dir / f"reference-design{sample.suffix.lower()}"
    art_prompt = template_dir / "art-template.md"
    baseline = template_dir / "pet-prompt-baseline.md"
    art = template_dir / "art.png"
    pet_prompt = template_dir / "pet-transform.md"
    pet_analysis = template_dir / "pet-transform.analysis.json"
    layout_path = template_dir / "layout.json"
    staged_pet = run_dir / f"input-pet{pet_source.suffix.lower()}"
    transformed_pet = run_dir / "transformed-pet.png"
    preview = run_dir / "preview.png"
    preview_debug = run_dir / "preview-debug.png"
    layout_snapshot = run_dir / "layout.snapshot.json"
    manifest = run_dir / "run.json"

    planned = [
        reference,
        art_prompt,
        art,
        pet_prompt,
        pet_analysis,
        staged_pet,
        transformed_pet,
        preview,
        preview_debug,
        layout_snapshot,
        manifest,
    ]
    if baseline_source is not None:
        planned.append(baseline)
    if args.name_method == "ai":
        planned.extend(
            [
                template_dir / "name-generation.json",
                template_dir / "name-prompt-template.md",
                template_dir / "name-style-reference.png",
                template_dir / "qa" / "name-slot-debug.png",
                run_dir / f"name-{_slug(pet_name)}.md",
                run_dir / f"name-{_slug(pet_name)}.request.json",
                run_dir / "generated-name.png",
            ]
        )
    if args.layout_mode == "interactive":
        planned.extend(
            [layout_path, template_dir / "qa" / "calibration-preview.png"]
        )
    elif not layout_path.is_file():
        raise PipelineError(
            f"--layout-mode existing requires an existing layout: {layout_path}"
        )

    existing = [path for path in planned if path.exists()]
    if existing and not args.force:
        raise PipelineError(
            f"planned output already exists: {existing[0]} (pass --force to replace pipeline outputs)"
        )

    plan = {
        "sample_design": str(sample),
        "art_prompt": str(art_prompt_source),
        "pet_prompt_baseline": str(baseline_source) if baseline_source else None,
        "pet_image": str(pet_source),
        "pet_name": pet_name,
        "name_method": args.name_method,
        "font": str(font),
        "template_dir": str(template_dir),
        "run_dir": str(run_dir),
        "art_size": art_size,
        "pet_size": pet_size,
        "layout_mode": args.layout_mode,
        "image_model": args.image_model,
        "prompt_model": args.prompt_model,
        "quality": args.quality,
        "api_key_source": (
            str(args.api_key_file.expanduser().resolve())
            if args.api_key_file is not None
            else "OPENAI_API_KEY"
        ),
    }
    print("Resolved one-step pipeline:", file=sys.stderr)
    print(json.dumps(plan, indent=2), file=sys.stderr, flush=True)
    _, api_key = _resolve_api_key(args.api_key_file)
    if args.dry_run:
        return {"template_dir": template_dir, "run_dir": run_dir}

    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

    total = 8 if args.name_method == "ai" else 6
    _stage("Stage immutable source copies", 1, total)
    _copy_file(sample, reference)
    _copy_file(art_prompt_source, art_prompt)
    _copy_file(pet_source, staged_pet)
    if baseline_source is not None:
        _copy_file(baseline_source, baseline)

    _stage("Generate background art template", 2, total)
    generate(
        _generation_args(
            sample_design=reference,
            pet_image=None,
            prompt_file=art_prompt,
            api_key_file=args.api_key_file,
            output_dir=template_dir,
            output_name=art.name,
            model=args.image_model,
            size=art_size,
            quality=args.quality,
            background="transparent",
            force=args.force,
        ),
        client=client,
    )

    _stage("Author reusable pet-transformation prompt", 3, total)
    derive_prompt(
        argparse.Namespace(
            sample_design=reference,
            art=art,
            api_key_file=args.api_key_file,
            output=pet_prompt,
            analysis_output=pet_analysis,
            model=args.prompt_model,
            strategy="direct",
            critic_pass=args.critic_pass,
            reasoning_effort=args.reasoning_effort,
            baseline_prompt=baseline if baseline_source is not None else None,
            image_detail="original",
            force=args.force,
            dry_run=False,
        ),
        client=client,
    )

    _stage("Transform customer pet", 4, total)
    generate(
        _generation_args(
            sample_design=None,
            pet_image=staged_pet,
            prompt_file=pet_prompt,
            api_key_file=args.api_key_file,
            output_dir=run_dir,
            output_name=transformed_pet.name,
            model=args.image_model,
            size=pet_size,
            quality=args.quality,
            background="transparent",
            force=args.force,
        ),
        client=client,
    )

    _stage("Confirm layout in local editor", 5, total)
    if args.layout_mode == "interactive":
        layout_runner(
            EditorConfig(
                art=art,
                reference=reference,
                pet=transformed_pet,
                pet_name=pet_name,
                font=font,
                output=layout_path,
                force=args.force,
            ),
            port=args.port,
            open_browser=not args.no_open,
        )
    load_layout(template_dir, layout_path)

    name_image: Path | None = None
    name_outputs: dict[str, Any] | None = None
    if args.name_method == "ai":
        _stage("Create layout-aware AI name prompt", 6, total)
        configure(
            argparse.Namespace(
                sample_design=reference,
                art=art,
                layout=layout_path,
                output_dir=template_dir,
                min_characters=2,
                max_characters_advisory=15,
                min_natural_width_ratio=0.20,
                min_font_scale_ratio=0.60,
                long_name_scale_threshold=0.80,
                crop_padding_ratio=0.0,
                force=args.force,
            )
        )
        name_prompt = run_dir / f"name-{_slug(pet_name)}.md"
        name_request = run_dir / f"name-{_slug(pet_name)}.request.json"
        name_outputs = create_prompt(
            argparse.Namespace(
                config=template_dir / "name-generation.json",
                pet_name=pet_name,
                output=name_prompt,
                request_output=name_request,
                force=args.force,
            )
        )
        params = name_outputs["api_parameters"]
        name_image = run_dir / "generated-name.png"
        generate(
            _generation_args(
                sample_design=name_outputs["style_reference"],
                pet_image=None,
                prompt_file=name_outputs["prompt"],
                api_key_file=args.api_key_file,
                output_dir=run_dir,
                output_name=name_image.name,
                model=str(params["model"]),
                size=str(params["size"]),
                quality=str(params["quality"]),
                background=str(params["background"]),
                force=args.force,
            ),
            client=client,
        )

    render_stage = 7 if args.name_method == "ai" else 6
    _stage("Render final preview and debug overlay", render_stage, total)
    render_to_files(
        template_dir=template_dir,
        pet_image=transformed_pet,
        pet_name=pet_name,
        name_image=name_image,
        output=preview,
        debug_output=preview_debug,
        force=args.force,
    )

    if args.name_method == "ai":
        _stage("Write tracked run metadata", 8, total)
    _copy_file(layout_path, layout_snapshot)
    artifacts: dict[str, str | None] = {
        "art": str(art),
        "pet_prompt": str(pet_prompt),
        "pet_prompt_analysis": str(pet_analysis),
        "layout": str(layout_path),
        "layout_snapshot": str(layout_snapshot),
        "transformed_pet": str(transformed_pet),
        "generated_name": str(name_image) if name_image else None,
        "preview": str(preview),
        "preview_debug": str(preview_debug),
    }
    record = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": plan,
        "sources": {
            "sample_design": {"path": str(sample), "sha256": _sha256(sample)},
            "art_prompt": {
                "path": str(art_prompt_source),
                "sha256": _sha256(art_prompt_source),
            },
            "pet_image": {"path": str(pet_source), "sha256": _sha256(pet_source)},
            "pet_prompt_baseline": (
                {"path": str(baseline_source), "sha256": _sha256(baseline_source)}
                if baseline_source is not None
                else None
            ),
        },
        "name_generation": (
            {
                "method": "ai",
                "prompt": str(name_outputs["prompt"]),
                "request": str(name_outputs["request"]),
                "validation": name_outputs["validation"],
            }
            if name_outputs is not None
            else {"method": "font"}
        ),
        "artifacts": artifacts,
    }
    _atomic_write_bytes(manifest, _json_bytes(record))
    print(f"\nPipeline complete. Preview: {preview}", file=sys.stderr, flush=True)
    return {
        "template_dir": template_dir,
        "run_dir": run_dir,
        "art": art,
        "pet_prompt": pet_prompt,
        "layout": layout_path,
        "transformed_pet": transformed_pet,
        "preview": preview,
        "preview_debug": preview_debug,
        "layout_snapshot": layout_snapshot,
        "manifest": manifest,
        **({"generated_name": name_image} if name_image is not None else {}),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        outputs = run_pipeline(args)
    except (
        PipelineError,
        UserInputError,
        PetPromptError,
        NamePromptError,
        ConfigError,
        RenderError,
    ) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        request_id = getattr(exc, "request_id", None)
        detail = f" (request ID: {request_id})" if request_id else ""
        print(f"Pipeline failed: {exc}{detail}", file=sys.stderr)
        return 1
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
