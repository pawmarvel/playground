from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import ConfigError
from .renderer import RenderError, render_to_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-render",
        description="Render one low-resolution personalized preview.",
    )
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument("--pet", type=Path, required=True, help="transformed pet PNG")
    parser.add_argument("--pet-name", required=True)
    parser.add_argument(
        "--name-image",
        type=Path,
        help="optional generated transparent name PNG; defaults to font rendering",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--debug-output", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    summary = {
        "template_dir": str(args.template_dir.expanduser().resolve()),
        "pet": str(args.pet.expanduser().resolve()),
        "pet_name": args.pet_name,
        "name_image": (
            str(args.name_image.expanduser().resolve()) if args.name_image else None
        ),
        "name_rendering": "image" if args.name_image else "font",
        "output": str(args.output.expanduser().resolve()),
        "debug_output": (
            str(args.debug_output.expanduser().resolve()) if args.debug_output else None
        ),
    }
    print("Render inputs:", file=sys.stderr)
    print(json.dumps(summary, indent=2), file=sys.stderr)
    try:
        output, debug_output = render_to_files(
            template_dir=args.template_dir,
            pet_image=args.pet,
            pet_name=args.pet_name,
            name_image=args.name_image,
            output=args.output,
            debug_output=args.debug_output,
            force=args.force,
        )
    except (ConfigError, RenderError) as exc:
        parser.error(str(exc))
    print(output)
    if debug_output:
        print(debug_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
