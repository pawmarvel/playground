# CLI purpose:
# Launch the local visual layout editor used to position a transformed pet and
# pet name over reusable template art and save the resulting layout.json.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .cli_errors import add_debug_argument, report_unexpected
from .font_catalog import default_local_font_catalog
from .layout_server import EditorConfig, serve_layout_editor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-layout-config",
        description="Open the local low-resolution template layout editor.",
    )
    add_debug_argument(parser)
    parser.add_argument("--art", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--pet", type=Path, required=True)
    parser.add_argument(
        "--pet-name",
        default="PET",
        help="initial QA preview name; it can be changed in the editor (default: PET)",
    )
    parser.add_argument(
        "--reference-text",
        help=(
            "initial text visibly printed in the reference name region; this is "
            "independent from --pet-name and remains editable"
        ),
    )
    parser.add_argument(
        "--font-reference",
        type=Path,
        help=(
            "existing font-reference-v1 JSON used to initialize the exact "
            "reference-image text region and visible text"
        ),
    )
    parser.add_argument(
        "--layout-reference",
        type=Path,
        help=(
            "existing layout-reference-v1 JSON used to initialize pet and "
            "name boxes from normalized screenshot coordinates"
        ),
    )
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
            "directory recursively containing eligible TTF/OFL font families; "
            "repeat to combine catalogs; omit with --font unset to use the "
            "curated local catalog"
        ),
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
        font_catalogs = tuple(args.font_catalog)
        if not font_catalogs and args.font is None:
            font_catalogs = (default_local_font_catalog(),)
        serve_layout_editor(
            EditorConfig(
                art=args.art,
                reference=args.reference,
                pet=args.pet,
                pet_name=args.pet_name,
                font=args.font,
                font_license=args.font_license,
                font_catalogs=font_catalogs,
                font_reference=args.font_reference,
                layout_reference=args.layout_reference,
                reference_text=args.reference_text,
                output=args.output,
                force=args.force,
            ),
            port=args.port,
            open_browser=not args.no_open,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return report_unexpected("pawmarvel-layout-config", exc, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
