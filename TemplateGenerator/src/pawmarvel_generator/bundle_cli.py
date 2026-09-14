# CLI purpose:
# Build a complete immutable production bundle revision from an application
# owner's reviewed offline component selection.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .bundle import BundleError
from .cli_errors import add_debug_argument, report_unexpected
from .personalization import DEFAULT_MAX_NAME_CODE_POINTS
from .production_bundle import build_from_selection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-bundle",
        description="Build an immutable, selection-driven production bundle revision.",
    )
    add_debug_argument(parser)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument(
        "--qa-input-pet",
        type=Path,
        required=True,
        help=(
            "operator-reviewed, non-customer fixture used by the selected "
            "representative pet attempt; bundled so FE can replay the runtime contract"
        ),
    )
    parser.add_argument(
        "--bundle-revision",
        default="next",
        help="positive integer or 'next' (default: next)",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--pet-name-max-length",
        type=int,
        default=DEFAULT_MAX_NAME_CODE_POINTS,
        help="maximum normalized Unicode code points accepted by this bundle (default: 12)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.bundle_revision != "next":
        try:
            if int(args.bundle_revision) < 1:
                raise ValueError
        except ValueError:
            parser.error("--bundle-revision must be a positive integer or 'next'")
    try:
        output = build_from_selection(
            selection_path=args.selection,
            output_dir=args.output_dir,
            bundle_revision=args.bundle_revision,
            pet_name_max_length=args.pet_name_max_length,
            qa_input_pet=args.qa_input_pet,
        )
    except (BundleError, ValueError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return report_unexpected("pawmarvel-bundle", exc, debug=args.debug)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
