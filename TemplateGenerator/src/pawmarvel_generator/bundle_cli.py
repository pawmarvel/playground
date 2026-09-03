# CLI purpose:
# Publish preview/print art and layout, design prompts, reference, and font
# assets as a clean bundle that follows the production consumer contract.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .bundle import BundleError, publish_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-bundle",
        description=(
            "Publish a production-compatible template bundle with preview and "
            "print art/layout pairs and design-specific prompt artifacts."
        ),
    )
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--design-id",
        "--template-id",
        dest="design_id",
        required=True,
        help="design identity; --template-id is a deprecated alias",
    )
    parser.add_argument(
        "--product-profile",
        type=Path,
        required=True,
        help="product variant used to derive the catalog template identity",
    )
    parser.add_argument(
        "--exemplar",
        type=Path,
        required=True,
        help="approved transparent transformed pet copied to qa/transformed-pet.png",
    )
    parser.add_argument(
        "--reference-design",
        type=Path,
        action="append",
        required=True,
        help=(
            "finished design reference used by pet transformation; repeat in "
            "priority order to include supporting references"
        ),
    )
    parser.add_argument(
        "--art-prompt",
        type=Path,
        required=True,
        help=(
            "design-specific prompt named art-template-gpt.md or "
            "art-template-gemini.md; its qualified name is preserved"
        ),
    )
    parser.add_argument(
        "--pet-prompt",
        type=Path,
        required=True,
        help=(
            "design-specific prompt named pet-transform-gpt.md or "
            "pet-transform-gemini.md; category must match --runtime-model"
        ),
    )
    parser.add_argument(
        "--print-art",
        "--art",
        dest="print_art",
        type=Path,
        required=True,
        help="high-resolution print art; --art is a deprecated alias",
    )
    parser.add_argument(
        "--print-layout",
        "--layout",
        dest="print_layout",
        type=Path,
        required=True,
        help="layout derived for the print art; --layout is a deprecated alias",
    )
    parser.add_argument("--font-license", type=Path, help="OFL.txt override")
    parser.add_argument(
        "--runtime-model",
        choices=("gpt-image-2", "gemini"),
        default="gpt-image-2",
    )
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output = publish_bundle(
            template_dir=args.template_dir,
            output_dir=args.output_dir,
            design_id=args.design_id,
            product_profile=args.product_profile,
            exemplar=args.exemplar,
            reference_design=args.reference_design,
            art_prompt=args.art_prompt,
            pet_prompt=args.pet_prompt,
            print_art=args.print_art,
            print_layout_path=args.print_layout,
            font_license=args.font_license,
            runtime_model=(
                "gpt-image-2" if args.runtime_model == "gpt-image-2" else None
            ),
            force=args.force,
        )
    except BundleError as exc:
        parser.error(str(exc))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
