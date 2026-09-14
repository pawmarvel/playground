# CLI purpose:
# Create and inspect reusable product print/preview profiles.

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .image_size import ImageSizeError, parse_image_size
from .cli_errors import add_debug_argument, report_unexpected
from .product_profile import (
    ProductProfileError,
    create_product_profile,
    load_product_profile,
    write_product_profile,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-product-profile",
        description="Create or inspect reusable product print/preview profiles.",
    )
    add_debug_argument(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser(
        "create", help="derive API-valid 1K preview layer dimensions from a print canvas"
    )
    create.add_argument("--profile-id", required=True)
    create.add_argument("--print-size", required=True, help="vendor canvas WIDTHxHEIGHT")
    create.add_argument("--preview-long-edge", type=int, default=1024)
    create.add_argument("--dpi", type=int)
    create.add_argument("--color-space", default="sRGB")
    create.add_argument("--background", choices=("transparent", "opaque"), default="transparent")
    create.add_argument("--output-format", choices=("png",), default="png")
    create.add_argument("--bleed-px", type=int, default=0)
    create.add_argument("--safe-margin-px", type=int, default=0)
    create.add_argument("--max-file-bytes", type=int)
    create.add_argument("--vendor-requirements-confirmed", action="store_true")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--force", action="store_true")

    show = subparsers.add_parser("show", help="validate and print one product profile")
    show.add_argument("--profile", type=Path, required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            profile = load_product_profile(args.profile)
            print(json.dumps(profile.to_dict(), indent=2))
            return 0
        profile = create_product_profile(
            profile_id=args.profile_id,
            print_size=parse_image_size(args.print_size, "--print-size"),
            preview_target_long_edge=args.preview_long_edge,
            dpi=args.dpi,
            color_space=args.color_space,
            background=args.background,
            output_format=args.output_format,
            bleed_px=args.bleed_px,
            safe_margin_px=args.safe_margin_px,
            max_file_bytes=args.max_file_bytes,
            vendor_requirements_confirmed=args.vendor_requirements_confirmed,
        )
        output = write_product_profile(args.output, profile, force=args.force)
        print(output)
        print(json.dumps(profile.to_dict(), indent=2))
        return 0
    except (ImageSizeError, ProductProfileError, OSError) as exc:
        parser.error(str(exc))
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return report_unexpected("pawmarvel-product-profile", exc, debug=args.debug)


if __name__ == "__main__":
    raise SystemExit(main())
