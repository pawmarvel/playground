# CLI purpose:
# Upscale one customer transformed-pet cutout against an already approved
# preview/print layout pair without regenerating reusable template art.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import ConfigError
from .print_upscale import PrintUpscaleError, prepare_print_pet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-upscale-pet",
        description="Upscale only customer cutouts for an approved print layout.",
    )
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument("--layout", type=Path)
    parser.add_argument("--print-layout", type=Path, required=True)
    parser.add_argument("--transformed-pet", type=Path, required=True)
    parser.add_argument("--name-image", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--backend", choices=("deterministic", "bria"), default="deterministic"
    )
    parser.add_argument("--bria-api-key-file", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    summary = {
        "template_dir": str(args.template_dir.expanduser().resolve()),
        "layout": str(args.layout.expanduser().resolve()) if args.layout else None,
        "print_layout": str(args.print_layout.expanduser().resolve()),
        "transformed_pet": str(args.transformed_pet.expanduser().resolve()),
        "name_image": str(args.name_image.expanduser().resolve()) if args.name_image else None,
        "output_dir": str(args.output_dir.expanduser().resolve()),
        "backend": args.backend,
    }
    print("Pet upscale inputs:", file=sys.stderr)
    print(json.dumps(summary, indent=2), file=sys.stderr, flush=True)
    try:
        outputs = prepare_print_pet(
            template_dir=args.template_dir,
            layout_path=args.layout,
            print_layout_path=args.print_layout,
            transformed_pet=args.transformed_pet,
            name_image=args.name_image,
            output_dir=args.output_dir,
            backend=args.backend,
            bria_token_file=args.bria_api_key_file,
            force=args.force,
        )
    except (ConfigError, PrintUpscaleError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Pet upscale failed: {exc}", file=sys.stderr)
        return 1
    for path in outputs.paths():
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
