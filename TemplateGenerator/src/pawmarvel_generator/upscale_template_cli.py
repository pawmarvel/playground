# CLI purpose:
# Prepare reusable high-resolution art, layout, font, license, and product-profile
# artifacts once for a template; no customer pet input is accepted.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import ConfigError, load_layout
from .print_upscale import PrintUpscaleError, parse_target_size, prepare_print_template
from .product_profile import ProductProfileError, load_product_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-upscale-template",
        description="Upscale reusable template art and derive layout-print.json once.",
    )
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument("--layout", type=Path)
    parser.add_argument("--target-size")
    parser.add_argument("--product-profile", type=Path)
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
    try:
        template_dir = args.template_dir.expanduser().resolve()
        layout_path = (
            args.layout.expanduser().resolve()
            if args.layout
            else template_dir / "layout.json"
        )
        profile_path = (
            args.product_profile.expanduser().resolve()
            if args.product_profile
            else None
        )
        profile = load_product_profile(profile_path) if profile_path else None
        if args.target_size:
            target_size = parse_target_size(args.target_size)
        elif profile:
            target_size = (profile.print_size.width, profile.print_size.height)
        else:
            raise PrintUpscaleError("provide --target-size or --product-profile")
        if profile and target_size != (profile.print_size.width, profile.print_size.height):
            raise PrintUpscaleError(
                "--target-size must match the product profile print canvas"
            )
        preview = load_layout(template_dir, layout_path=layout_path)
        if target_size[0] * preview.canvas_height != target_size[1] * preview.canvas_width:
            raise PrintUpscaleError(
                f"preview art is {preview.canvas_width}x{preview.canvas_height}, but "
                f"the print target is {target_size[0]}x{target_size[1]}; their aspect "
                "ratios differ. Regenerate preview art for the product profile."
            )
        summary = {
            "template_dir": str(template_dir),
            "layout": str(layout_path),
            "product_profile": str(profile_path) if profile_path else None,
            "target_size": {"width": target_size[0], "height": target_size[1]},
            "output_dir": str(args.output_dir.expanduser().resolve()),
            "backend": args.backend,
        }
        print("Template upscale inputs:", file=sys.stderr)
        print(json.dumps(summary, indent=2), file=sys.stderr, flush=True)
        outputs = prepare_print_template(
            template_dir=template_dir,
            layout_path=layout_path,
            target_size=target_size,
            output_dir=args.output_dir,
            backend=args.backend,
            bria_token_file=args.bria_api_key_file,
            product_profile=profile_path,
            force=args.force,
        )
    except (ConfigError, PrintUpscaleError, ProductProfileError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Template upscale failed: {exc}", file=sys.stderr)
        return 1
    for path in outputs.paths():
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
