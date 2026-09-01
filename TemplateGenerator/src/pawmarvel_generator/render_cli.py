# CLI purpose:
# Deterministically compose art, transformed pet, and pet-name layers for
# preview or validate and render a profile-bound print bundle.

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import ConfigError
from .cli import _atomic_write_bytes
from .product_profile import (
    ProductProfileError,
    load_product_profile,
    validate_print_output,
)
from .print_upscale import PrintUpscaleError, verify_print_bundle
from .renderer import RenderError, render_to_files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-render",
        description="Render one personalized preview from an explicit layout.",
    )
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument(
        "--layout",
        type=Path,
        help="layout JSON (default: TEMPLATE_DIR/layout.json)",
    )
    parser.add_argument("--pet", type=Path, required=True, help="transformed pet PNG")
    parser.add_argument("--pet-name", required=True)
    parser.add_argument(
        "--name-image",
        type=Path,
        help="future-extension name PNG experiment; MVP defaults to font rendering",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--debug-output", type=Path)
    parser.add_argument(
        "--product-profile",
        type=Path,
        help="validate the result as a profile-sized print review image",
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        help="print-review manifest (default: <output stem>.manifest.json)",
    )
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    summary = {
        "template_dir": str(args.template_dir.expanduser().resolve()),
        "layout": str(args.layout.expanduser().resolve()) if args.layout else None,
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
        "product_profile": (
            str(args.product_profile.expanduser().resolve())
            if args.product_profile
            else None
        ),
    }
    print("Render inputs:", file=sys.stderr)
    print(json.dumps(summary, indent=2), file=sys.stderr)
    try:
        profile = None
        manifest_output = None
        print_bundle_manifest = None
        if args.manifest_output is not None and args.product_profile is None:
            raise ProductProfileError("--manifest-output requires --product-profile")
        if args.product_profile is not None:
            profile = load_product_profile(args.product_profile)
            layout_path = (
                args.layout.expanduser().resolve()
                if args.layout is not None
                else args.template_dir.expanduser().resolve() / "layout.json"
            )
            print_bundle_manifest = verify_print_bundle(
                template_dir=args.template_dir,
                layout_path=layout_path,
                transformed_pet=args.pet,
                name_image=args.name_image,
                product_profile=args.product_profile,
            )
            manifest_output = (
                args.manifest_output.expanduser().resolve()
                if args.manifest_output is not None
                else args.output.expanduser().resolve().with_name(
                    f"{args.output.stem}.manifest.json"
                )
            )
            if manifest_output.exists() and not args.force:
                raise RenderError(
                    f"manifest output already exists: {manifest_output} (pass --force to replace it)"
                )
        output, debug_output = render_to_files(
            template_dir=args.template_dir,
            pet_image=args.pet,
            pet_name=args.pet_name,
            name_image=args.name_image,
            output=args.output,
            debug_output=args.debug_output,
            layout_path=args.layout,
            png_dpi=(
                (float(profile.print_spec["dpi"]), float(profile.print_spec["dpi"]))
                if profile is not None and profile.print_spec.get("dpi") is not None
                else None
            ),
            force=args.force,
        )
        if profile is not None:
            validate_print_output(profile, output)
            assert manifest_output is not None
            assert print_bundle_manifest is not None
            record = {
                "schema_version": 1,
                "status": "ready-for-final-print-review",
                "product_profile": str(args.product_profile.expanduser().resolve()),
                "print_bundle_manifest": str(print_bundle_manifest),
                "print_bundle_manifest_sha256": _sha256(print_bundle_manifest),
                "layout": str(args.layout.expanduser().resolve()) if args.layout else None,
                "inputs_sha256": {
                    "art": _sha256(args.template_dir.expanduser().resolve() / "art-print.png"),
                    "transformed_pet": _sha256(args.pet.expanduser().resolve()),
                    "name_image": (
                        _sha256(args.name_image.expanduser().resolve())
                        if args.name_image
                        else None
                    ),
                    "layout": _sha256(
                        args.layout.expanduser().resolve()
                        if args.layout
                        else args.template_dir.expanduser().resolve() / "layout.json"
                    ),
                    "product_profile": _sha256(args.product_profile.expanduser().resolve()),
                },
                "output": str(output),
                "output_sha256": _sha256(output),
                "dimensions": profile.print_size.to_dict(),
                "print_spec": dict(profile.print_spec),
            }
            _atomic_write_bytes(
                manifest_output, (json.dumps(record, indent=2) + "\n").encode("utf-8")
            )
            print(manifest_output)
    except (
        ConfigError,
        PrintUpscaleError,
        ProductProfileError,
        RenderError,
    ) as exc:
        parser.error(str(exc))
    print(output)
    if debug_output:
        print(debug_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
