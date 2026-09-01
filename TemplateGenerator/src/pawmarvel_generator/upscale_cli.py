# CLI purpose:
# Convert explicit preview artifacts into independently upscaled print layers and
# a mechanically derived layout-print.json at the product-profile canvas size.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import ConfigError, load_layout
from .print_upscale import PrintUpscaleError, parse_target_size, prepare_print_assets
from .product_profile import ProductProfileError, load_product_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-upscale",
        description=(
            "Prepare independently upscaled print layers and a mechanically scaled layout."
        ),
    )
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument(
        "--layout",
        type=Path,
        help="preview layout JSON (default: TEMPLATE_DIR/layout.json)",
    )
    parser.add_argument("--transformed-pet", type=Path, required=True)
    parser.add_argument(
        "--target-size",
        help="exact print canvas; defaults to the product profile",
    )
    parser.add_argument(
        "--product-profile",
        type=Path,
        help=(
            "product profile supplying the print canvas; must match "
            "--target-size when both are supplied"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--backend",
        choices=("deterministic", "bria"),
        default="deterministic",
        help="upscale backend (default: deterministic Lanczos)",
    )
    parser.add_argument(
        "--bria-api-key-file",
        type=Path,
        help="UTF-8 file containing one Bria token; defaults to BRIA_API_TOKEN",
    )
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        template_dir = args.template_dir.expanduser().resolve()
        layout = (
            args.layout.expanduser().resolve()
            if args.layout is not None
            else template_dir / "layout.json"
        )
        transformed_pet = args.transformed_pet.expanduser().resolve()
        profile_path = (
            args.product_profile.expanduser().resolve() if args.product_profile else None
        )
        product_profile = (
            load_product_profile(profile_path) if profile_path is not None else None
        )
        if args.target_size is not None:
            target_size = parse_target_size(args.target_size)
        elif product_profile is not None:
            target_size = (
                product_profile.print_size.width,
                product_profile.print_size.height,
            )
        else:
            raise PrintUpscaleError("provide --target-size or --product-profile")
        if product_profile is not None and target_size != (
            product_profile.print_size.width,
            product_profile.print_size.height,
        ):
            raise PrintUpscaleError(
                "--target-size must match the product profile print canvas"
            )
        summary = {
            "template_dir": str(template_dir),
            "layout": str(layout),
            "transformed_pet": str(transformed_pet),
            "product_profile": str(profile_path) if profile_path else None,
            "target_size": {"width": target_size[0], "height": target_size[1]},
            "output_dir": str(args.output_dir.expanduser().resolve()),
            "backend": args.backend,
            "bria_token_source": (
                str(args.bria_api_key_file.expanduser().resolve())
                if args.bria_api_key_file
                else ("BRIA_API_TOKEN" if args.backend == "bria" else None)
            ),
        }
        print("Print upscale inputs:", file=sys.stderr)
        print(json.dumps(summary, indent=2), file=sys.stderr, flush=True)
        preview_layout = load_layout(template_dir, layout_path=layout)
        if (
            target_size[0] * preview_layout.canvas_height
            != target_size[1] * preview_layout.canvas_width
        ):
            guidance = ""
            if product_profile is not None:
                guidance = (
                    f" Product profile {product_profile.profile_id!r} requires preview "
                    f"art {product_profile.preview_art_size.width}x"
                    f"{product_profile.preview_art_size.height}. Copying a product profile "
                    "does not resize art.png or recalculate layout.json. Regenerate art.png "
                    "at the profile preview size, then recreate layout.json."
                )
            raise PrintUpscaleError(
                f"preview art is {preview_layout.canvas_width}x"
                f"{preview_layout.canvas_height}, but the print target is "
                f"{target_size[0]}x{target_size[1]}; their aspect ratios differ."
                f"{guidance} Cropping or stretching is not allowed."
            )
        outputs = prepare_print_assets(
            template_dir=template_dir,
            layout_path=layout,
            transformed_pet=transformed_pet,
            target_size=target_size,
            output_dir=args.output_dir,
            backend=args.backend,
            bria_token_file=args.bria_api_key_file,
            product_profile=profile_path,
            force=args.force,
        )
    except (
        ConfigError,
        PrintUpscaleError,
        ProductProfileError,
    ) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Print upscale failed: {exc}", file=sys.stderr)
        return 1

    for path in outputs.paths():
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
