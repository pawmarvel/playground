from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .cli import UserInputError, generate
from .config import ConfigError, load_layout
from .renderer import RenderError, render_to_files, validate_name_image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-poc-run",
        description="Transform one pet and render the low-resolution MVP preview.",
    )
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument("--pet-image", type=Path, required=True)
    parser.add_argument("--pet-name", required=True)
    parser.add_argument(
        "--name-image",
        type=Path,
        help="optional pre-generated transparent name PNG; defaults to font rendering",
    )
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument(
        "--quality", choices=("low", "medium", "high", "auto"), default="high"
    )
    parser.add_argument("--force", action="store_true")
    return parser


def run_poc(args: argparse.Namespace, client: Any | None = None) -> tuple[Path, Path, Path]:
    template_dir = args.template_dir.expanduser().resolve()
    pet_image = args.pet_image.expanduser().resolve()
    name_image_arg = getattr(args, "name_image", None)
    name_image = (
        name_image_arg.expanduser().resolve() if name_image_arg is not None else None
    )
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else template_dir / "qa"
    )
    prompt_file = template_dir / "pet-transform.md"
    transformed = output_dir / "transformed-pet.png"
    final = output_dir / "final-preview.png"
    debug = output_dir / "final-preview-debug.png"

    load_layout(template_dir)
    if not pet_image.is_file():
        raise UserInputError(f"pet image does not exist: {pet_image}")
    if not prompt_file.is_file():
        raise UserInputError(f"pet transformation prompt does not exist: {prompt_file}")
    if not args.pet_name.strip():
        raise UserInputError("pet name must not be empty")
    if name_image is not None:
        if not name_image.is_file():
            raise UserInputError(f"name image does not exist: {name_image}")
        try:
            validate_name_image(name_image)
        except RenderError as exc:
            raise UserInputError(str(exc)) from exc
    targets = (transformed, final, debug)
    existing = [path for path in targets if path.exists()]
    if existing and not args.force:
        raise UserInputError(
            f"output already exists: {existing[0]} (pass --force to replace all POC outputs)"
        )

    summary = {
        "template_dir": str(template_dir),
        "pet_image": str(pet_image),
        "pet_name": args.pet_name,
        "name_image": str(name_image) if name_image else None,
        "name_rendering": "image" if name_image else "font",
        "prompt_file": str(prompt_file),
        "transformed_pet": str(transformed),
        "final_preview": str(final),
        "debug_preview": str(debug),
        "model": args.model,
        "size": args.size,
        "quality": args.quality,
    }
    print("POC run inputs and outputs:", file=sys.stderr)
    print(json.dumps(summary, indent=2), file=sys.stderr, flush=True)

    generation_args = argparse.Namespace(
        sample_design=None,
        pet_image=pet_image,
        prompt_file=prompt_file,
        api_key_file=args.api_key_file,
        output_dir=output_dir,
        output_name=transformed.name,
        output_format="png",
        model=args.model,
        size=args.size,
        quality=args.quality,
        background="transparent",
        force=args.force,
        dry_run=False,
    )
    generated = generate(generation_args, client=client)
    render_to_files(
        template_dir=template_dir,
        pet_image=generated,
        pet_name=args.pet_name,
        name_image=name_image,
        output=final,
        debug_output=debug,
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
        request_id = getattr(exc, "request_id", None)
        detail = f" (request ID: {request_id})" if request_id else ""
        print(f"POC run failed: {exc}{detail}", file=sys.stderr)
        return 1
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
