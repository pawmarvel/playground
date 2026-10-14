# CLI purpose:
# Prepare API-free multi-output print review candidates and, after explicit
# operator approval, build a backward-compatible immutable bundle revision.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .bundle import BundleError
from .cli_errors import add_debug_argument, report_unexpected
from .print_targets import (
    DEFAULT_ORNAMENT_TARGETS,
    PrintTargetsError,
    build_multi_output_revision,
    prepare_print_target_candidate,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pawmarvel-print-targets")
    add_debug_argument(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare",
        help="render a local target-by-fixture review candidate without API calls",
    )
    prepare.add_argument("--source-bundle", type=Path, required=True)
    prepare.add_argument("--authoring-product", type=Path, required=True)
    prepare.add_argument("--candidate-id", required=True)
    prepare.add_argument(
        "--target",
        action="append",
        dest="targets",
        help=(
            "WIDTHxHEIGHT; repeat for every target "
            f"(default: {', '.join(DEFAULT_ORNAMENT_TARGETS)})"
        ),
    )
    prepare.add_argument("--coverage", type=float, default=0.95)

    approve = commands.add_parser(
        "approve-build",
        help="record approval and build the next immutable multi-output revision",
    )
    approve.add_argument("--candidate", type=Path, required=True)
    approve.add_argument("--output-dir", type=Path, required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--notes", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            output = prepare_print_target_candidate(
                source_bundle=args.source_bundle,
                authoring_product=args.authoring_product,
                candidate_id=args.candidate_id,
                targets=args.targets or DEFAULT_ORNAMENT_TARGETS,
                coverage=args.coverage,
            )
        else:
            output = build_multi_output_revision(
                candidate=args.candidate,
                output_dir=args.output_dir,
                approved_by=args.approved_by,
                approval_notes=args.notes,
            )
    except (PrintTargetsError, BundleError, ValueError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return report_unexpected("pawmarvel-print-targets", exc, debug=args.debug)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
