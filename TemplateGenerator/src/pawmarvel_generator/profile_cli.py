# CLI purpose:
# Create and inspect reusable product print/preview profiles or normalize a
# reference screenshot to the exact profile-defined preview-art dimensions.

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .image_size import ImageSizeError, parse_image_size
from .product_profile import (
    ProductProfileError,
    create_product_profile,
    load_product_profile,
    normalize_reference,
    write_product_profile,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-product-profile",
        description="Create or inspect reusable product print/preview profiles.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser(
        "create", help="derive API-valid 1K preview layer dimensions from a print canvas"
    )
    create.add_argument("--profile-id", required=True)
    create.add_argument("--print-size", required=True, help="vendor canvas WIDTHxHEIGHT")
    create.add_argument("--preview-long-edge", type=int, default=1024)
    create.add_argument(
        "--reference-fit",
        choices=("contain", "cover"),
        default="contain",
        help="fit the full screenshot with padding (default: contain); cover center-crops",
    )
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

    normalize = subparsers.add_parser(
        "normalize-reference",
        help="crop/pad a screenshot to the profile's exact preview canvas",
    )
    normalize.add_argument("--profile", type=Path, required=True)
    normalize.add_argument("--input", type=Path, required=True)
    normalize.add_argument("--output", type=Path, required=True)
    normalize.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            profile = load_product_profile(args.profile)
            print(json.dumps(profile.to_dict(), indent=2))
            return 0
        if args.command == "normalize-reference":
            profile = load_product_profile(args.profile)
            output = normalize_reference(
                args.input,
                args.output,
                profile.preview_art_size,
                fit=profile.reference_fit,
                force=args.force,
            )
            print(output)
            return 0
        profile = create_product_profile(
            profile_id=args.profile_id,
            print_size=parse_image_size(args.print_size, "--print-size"),
            preview_target_long_edge=args.preview_long_edge,
            reference_fit=args.reference_fit,
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


if __name__ == "__main__":
    raise SystemExit(main())
