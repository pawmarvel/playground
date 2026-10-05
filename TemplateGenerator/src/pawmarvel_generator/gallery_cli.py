# CLI purpose:
# Share a private reference-design gallery, collect one updatable vote and
# comment per reviewer/design, and export the review results from local SQLite.

from __future__ import annotations

import argparse
import hmac
import os
import sys
import webbrowser
from pathlib import Path
from typing import Sequence

from .cli_errors import HelpfulArgumentParser, add_debug_argument, report_unexpected
from .gallery_server import (
    GalleryConfig,
    GalleryError,
    create_gallery_server,
)


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW_ROOT = DEFAULT_PROJECT_ROOT / "work/gallery-reviews/patrol-franchise"


def _path(value: str) -> Path:
    if not value.strip():
        raise argparse.ArgumentTypeError("path must not be empty")
    return Path(value)


def build_parser() -> argparse.ArgumentParser:
    parser = HelpfulArgumentParser(
        prog="pawmarvel-gallery",
        description="Serve a private voting gallery for reference-design review.",
    )
    add_debug_argument(parser)
    parser.add_argument(
        "--gallery-root",
        type=_path,
        default=DEFAULT_PROJECT_ROOT / "work/design-inputs/Test Design Pool",
        help="folder containing concept-index.json and per-design folders",
    )
    parser.add_argument(
        "--index",
        type=_path,
        help="concept index (default: <gallery-root>/concept-index.json)",
    )
    parser.add_argument(
        "--abandoned-root",
        type=_path,
        help="abandoned pool (default: sibling Abandoned Design Pool)",
    )
    parser.add_argument(
        "--graduation-root",
        type=_path,
        help="graduation pool (default: sibling Graduation Pool)",
    )
    parser.add_argument(
        "--database",
        type=_path,
        default=DEFAULT_REVIEW_ROOT / "gallery-votes.sqlite3",
        help=(
            "SQLite vote database (default: "
            "<project>/work/gallery-reviews/patrol-franchise/gallery-votes.sqlite3)"
        ),
    )
    parser.add_argument(
        "--decisions-dir",
        type=_path,
        help="durable non-PII decisions (default: <database-parent>/decisions)",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=30,
        help="days to retain votes after a design leaves the pool (default: 30)",
    )
    parser.add_argument(
        "--authoring-root",
        type=_path,
        default=DEFAULT_PROJECT_ROOT / "work/authoring",
        help=(
            "authoring root used to discover complete art and release-pet reviews "
            "(default: <project>/work/authoring)"
        ),
    )
    parser.add_argument(
        "--collection",
        action="append",
        default=[],
        help="include one exact collection name; repeat to include more (default: all)",
    )
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--reviewer-access-code",
        help="reviewer code (prefer PAWMARVEL_GALLERY_REVIEWER_ACCESS_CODE)",
    )
    parser.add_argument(
        "--operator-access-code",
        help="operator code (prefer PAWMARVEL_GALLERY_OPERATOR_ACCESS_CODE)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="open the local gallery in the default browser",
    )
    parser.add_argument(
        "--show-results",
        action="store_true",
        help="print result endpoint URLs on startup",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = args.gallery_root.resolve()
        index = (args.index or root / "concept-index.json").resolve()
        database = args.database.resolve()
        decisions_dir = (
            args.decisions_dir.resolve()
            if args.decisions_dir is not None
            else database.parent / "decisions"
        )
        if args.retention_days < 1:
            parser.error(
                f"--retention-days must be at least 1; actual={args.retention_days}"
            )
        reviewer_code = (
            args.reviewer_access_code
            or os.environ.get("PAWMARVEL_GALLERY_REVIEWER_ACCESS_CODE")
        )
        operator_code = args.operator_access_code or os.environ.get(
            "PAWMARVEL_GALLERY_OPERATOR_ACCESS_CODE"
        )
        if args.bind not in {"127.0.0.1", "localhost", "::1"} and (
            not reviewer_code or not operator_code
        ):
            parser.error(
                "separate reviewer and operator access codes are required when "
                "binding to a shareable interface; set "
                "PAWMARVEL_GALLERY_REVIEWER_ACCESS_CODE and "
                f"PAWMARVEL_GALLERY_OPERATOR_ACCESS_CODE; bind={args.bind!r}"
            )
        if reviewer_code and operator_code and hmac.compare_digest(
            reviewer_code, operator_code
        ):
            parser.error("reviewer and operator access codes must be different")
        if not 0 <= args.port <= 65535:
            parser.error(f"--port must be between 0 and 65535; actual={args.port}")
        server = create_gallery_server(
            GalleryConfig(
                root=root,
                index=index,
                database=database,
                authoring_root=args.authoring_root.resolve(),
                abandoned_root=(
                    args.abandoned_root.resolve()
                    if args.abandoned_root is not None
                    else None
                ),
                graduation_root=(
                    args.graduation_root.resolve()
                    if args.graduation_root is not None
                    else None
                ),
                collections=tuple(args.collection),
                reviewer_access_code=reviewer_code,
                operator_access_code=operator_code,
                retention_days=args.retention_days,
                decisions_dir=decisions_dir,
            ),
            bind=args.bind,
            port=args.port,
        )
        host = "127.0.0.1" if args.bind in {"0.0.0.0", "::"} else args.bind
        url = f"http://{host}:{server.server_port}/"
        operator_url = f"http://{host}:{server.server_port}/operator"
        print(f"Gallery: {url}")
        print(f"Operator console: {operator_url}")
        print(f"Designs: {len(server.concepts)}")
        print(
            "Designs with generated review context: "
            f"{sum(bool(item['review_contexts']) for item in server.concepts)}"
        )
        print(f"Votes: {database}")
        result = server.reconciliation
        print(
            "Pool reconciliation: "
            f"active={result.active}, added={len(result.added)}, "
            f"removed={len(result.removed)}, restored={len(result.restored)}, "
            f"purged={len(result.purged)}"
        )
        for label, values in (
            ("New designs", result.added),
            ("Removed designs", result.removed),
            ("Restored designs", result.restored),
            ("Purged designs", result.purged),
        ):
            if values:
                print(f"{label}: {', '.join(values)}")
        if result.removed_without_disposition:
            print(
                "Warning: removed designs have no graduated/abandoned disposition: "
                + ", ".join(result.removed_without_disposition),
                file=sys.stderr,
            )
        if server.missing_design_ids:
            print(
                "Warning: concept index entries have no design folder and are not active: "
                + ", ".join(server.missing_design_ids),
                file=sys.stderr,
            )
        if args.bind in {"0.0.0.0", "::"}:
            print(
                "Share the host's LAN address with teammates, or expose this port "
                "through an approved HTTPS tunnel. Do not expose raw HTTP to the internet."
            )
        if args.show_results:
            print(f"Operator JSON results: {url}api/results.json")
            print(f"Operator CSV results: {url}api/results.csv")
            print(
                "Retained removed-design results: "
                f"{url}api/results.json?include_retired=1"
            )
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nGallery stopped.")
        finally:
            server.server_close()
        return 0
    except GalleryError as exc:
        parser.error(str(exc))
    except Exception as exc:
        return report_unexpected("pawmarvel-gallery", exc, debug=args.debug)
    return 2


if __name__ == "__main__":
    sys.exit(main())
