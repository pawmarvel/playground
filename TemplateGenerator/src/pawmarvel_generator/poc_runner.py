# CLI purpose:
# Run the small personalization test path by transforming or reusing one pet
# layer and composing it with a template into final and debug preview images.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .cli import (
    PET_NAME_PLACEHOLDER,
    UserInputError,
    _read_prompt,
    _resolve_provider_model,
    generate,
)
from .cli_errors import add_debug_argument, report_unexpected
from .config import ConfigError, load_layout
from .renderer import RenderError, render_to_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-poc-run",
        description=(
            "Transform one pet and render a preview, or reuse an approved transformed pet."
        ),
    )
    add_debug_argument(parser)
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument(
        "--layout",
        type=Path,
        help="layout JSON (default: TEMPLATE_DIR/layout.json)",
    )
    pet_source = parser.add_mutually_exclusive_group(required=True)
    pet_source.add_argument("--pet-image", type=Path)
    pet_source.add_argument(
        "--transformed-pet",
        type=Path,
        help="reuse an approved transformed pet and skip the paid image API call",
    )
    parser.add_argument(
        "--pet-name",
        help=(
            "pet name for the separate layout text layer; it is also passed to "
            "image generation only when the prompt contains {{PET_NAME}}"
        ),
    )
    parser.add_argument(
        "--api-key-file",
        type=Path,
        help="selected image provider API key file",
    )
    parser.add_argument(
        "--reference-design",
        type=Path,
        action="append",
        help=(
            "optional finished design reference used with --pet-image; repeat in "
            "priority order to add supporting references"
        ),
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help="design-specific pet prompt required when --pet-image is used",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--provider",
        choices=("auto", "openai", "gemini"),
        default="auto",
        help="pet transformation provider (default: infer from --model)",
    )
    parser.add_argument(
        "--model",
        help="pet transformation model; provider default when omitted",
    )
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument(
        "--quality", choices=("low", "medium", "high", "auto"), default="high"
    )
    parser.add_argument("--force", action="store_true")
    return parser


def run_poc(args: argparse.Namespace, client: Any | None = None) -> tuple[Path, Path, Path]:
    template_dir = args.template_dir.expanduser().resolve()
    pet_image_arg = getattr(args, "pet_image", None)
    transformed_pet_arg = getattr(args, "transformed_pet", None)
    pet_image = pet_image_arg.expanduser().resolve() if pet_image_arg else None
    supplied_transformed = (
        transformed_pet_arg.expanduser().resolve() if transformed_pet_arg else None
    )
    layout_arg = getattr(args, "layout", None)
    layout_path = layout_arg.expanduser().resolve() if layout_arg else None
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else template_dir / "qa"
    )
    reference_arg = getattr(args, "reference_design", None)
    reference_values = (
        []
        if reference_arg is None
        else list(reference_arg)
        if isinstance(reference_arg, (list, tuple))
        else [reference_arg]
    )
    reference_designs = [path.expanduser().resolve() for path in reference_values]
    prompt_arg = getattr(args, "prompt_file", None)
    prompt_file = prompt_arg.expanduser().resolve() if prompt_arg is not None else None
    transformed = supplied_transformed or output_dir / "transformed-pet.png"
    final = output_dir / "final-preview.png"
    debug = output_dir / "final-preview-debug.png"
    provider: str | None = None
    model: str | None = None
    pet_prompt_uses_name = False
    if supplied_transformed is None:
        provider, model = _resolve_provider_model(
            getattr(args, "provider", "auto"), getattr(args, "model", None)
        )

    layout = load_layout(template_dir, layout_path=layout_path)
    if supplied_transformed is not None:
        if not supplied_transformed.is_file():
            raise UserInputError(
                f"transformed pet does not exist: {supplied_transformed}"
            )
    else:
        if pet_image is None or not pet_image.is_file():
            raise UserInputError(f"pet image does not exist: {pet_image}")
        for index, reference_design in enumerate(reference_designs, 1):
            if not reference_design.is_file():
                raise UserInputError(
                    "finished reference design "
                    f"{index} does not exist: {reference_design}"
                )
        if prompt_file is None or not prompt_file.is_file():
            raise UserInputError(
                f"pet transformation prompt does not exist: {prompt_file}"
            )
        _, pet_prompt = _read_prompt(prompt_file)
        pet_prompt_uses_name = PET_NAME_PLACEHOLDER in pet_prompt
        if pet_prompt_uses_name and not (args.pet_name or "").strip():
            raise UserInputError(
                f"pet transformation prompt contains {PET_NAME_PLACEHOLDER}; "
                "provide --pet-name"
            )
    if layout.has_name and not (args.pet_name or "").strip():
        raise UserInputError(
            "--pet-name is required because layout.json contains a name layer"
        )
    targets = (
        (final, debug)
        if supplied_transformed is not None
        else (transformed, final, debug)
    )
    existing = [path for path in targets if path.exists()]
    if existing and not args.force:
        raise UserInputError(
            f"output already exists: {existing[0]} (pass --force to replace all POC outputs)"
        )

    summary = {
        "template_dir": str(template_dir),
        "layout": str(layout_path) if layout_path else None,
        "pet_image": str(pet_image) if pet_image else None,
        "supplied_transformed_pet": (
            str(supplied_transformed) if supplied_transformed else None
        ),
        "pet_source_mode": "reuse" if supplied_transformed else "generate",
        "pet_name": args.pet_name,
        "pet_prompt_uses_pet_name": pet_prompt_uses_name,
        "prompt_file": str(prompt_file) if supplied_transformed is None else None,
        "reference_designs": (
            [str(path) for path in reference_designs]
            if supplied_transformed is None
            else None
        ),
        "transformed_pet": str(transformed),
        "final_preview": str(final),
        "debug_preview": str(debug),
        "provider": provider,
        "model": model,
        "size": args.size,
        "quality": args.quality,
        "runtime_model": model,
    }
    print("POC run inputs and outputs:", file=sys.stderr)
    print(json.dumps(summary, indent=2), file=sys.stderr, flush=True)

    if supplied_transformed is None:
        generation_args = argparse.Namespace(
            reference_design=reference_designs,
            pet_image=pet_image,
            pet_name=args.pet_name if pet_prompt_uses_name else None,
            prompt_file=prompt_file,
            api_key_file=args.api_key_file,
            provider=provider,
            output_dir=output_dir,
            output_name=transformed.name,
            output_format="png",
            model=model,
            size=args.size,
            quality=args.quality,
            background="transparent",
            force=args.force,
            dry_run=False,
        )
        generated = generate(generation_args, client=client)
    else:
        generated = supplied_transformed
    render_to_files(
        template_dir=template_dir,
        pet_image=generated,
        pet_name=args.pet_name,
        output=final,
        debug_output=debug,
        layout_path=layout_path,
        force=args.force,
    )
    return transformed, final, debug


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        outputs = run_poc(args)
    except (UserInputError, ConfigError, RenderError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        return report_unexpected("pawmarvel-poc-run", exc, debug=args.debug)
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
