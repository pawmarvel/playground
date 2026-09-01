# CLI purpose:
# Launch the local visual layout editor used to position a transformed pet and
# pet name over reusable template art and save the resulting layout.json.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .config import ConfigError
from .font_license import FontLicenseError
from .layout_server import EditorConfig, serve_layout_editor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-layout-config",
        description="Open the local low-resolution template layout editor.",
    )
    parser.add_argument("--art", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--pet", type=Path, required=True)
    parser.add_argument("--pet-name", required=True)
    parser.add_argument(
        "--font", type=Path,
        help="explicit OFL font override; omit to auto-match from --font-catalog",
    )
    parser.add_argument(
        "--font-license",
        type=Path,
        help="OFL.txt for --font (defaults to a sibling OFL.txt)",
    )
    parser.add_argument(
        "--font-catalog",
        type=Path,
        action="append",
        default=[],
        help=(
            "directory recursively containing approved TTF/OFL font families; "
            "repeat to combine catalogs"
        ),
    )
    parser.add_argument(
        "--font-catalog-mode", choices=("local", "expanded"), default="local",
        help="expanded adds pinned remote OFL candidates through a validated cache",
    )
    parser.add_argument(
        "--font-index", type=Path,
        default=Path("assets/fonts/expanded-catalog.json"),
        help="versioned expanded OFL catalog index",
    )
    parser.add_argument("--font-cache", type=Path, default=Path(".pawmarvel-font-cache"))
    parser.add_argument("--font-shortlist-limit", type=int, default=24)
    parser.add_argument("--font-offline", action="store_true", help="use expanded cache only; never download")
    parser.add_argument(
        "--runtime-model",
        choices=("gpt-image-2", "gemini"),
        default="gpt-image-2",
        help="production pet-styling route; gemini is encoded by omitting layout.model",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    try:
        serve_layout_editor(
            EditorConfig(
                art=args.art,
                reference=args.reference,
                pet=args.pet,
                pet_name=args.pet_name,
                font=args.font,
                font_license=args.font_license,
                font_catalogs=tuple(args.font_catalog),
                font_catalog_mode=args.font_catalog_mode,
                font_index=args.font_index,
                font_cache=args.font_cache,
                font_shortlist_limit=args.font_shortlist_limit,
                font_offline=args.font_offline,
                runtime_model=(
                    "gpt-image-2" if args.runtime_model == "gpt-image-2" else None
                ),
                output=args.output,
                force=args.force,
            ),
            port=args.port,
            open_browser=not args.no_open,
        )
    except (ConfigError, FontLicenseError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
