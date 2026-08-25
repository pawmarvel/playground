from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .config import ConfigError
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
        "--name-image",
        type=Path,
        help="optional generated transparent name PNG; defaults to font preview",
    )
    parser.add_argument("--font", type=Path, required=True)
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
                output=args.output,
                name_image=args.name_image,
                force=args.force,
            ),
            port=args.port,
            open_browser=not args.no_open,
        )
    except ConfigError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
