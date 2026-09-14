"""Shared command-line diagnostics for unexpected internal failures."""

from __future__ import annotations

import argparse
import sys
import traceback


def add_debug_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--debug",
        action="store_true",
        help="print a traceback when an unexpected internal error occurs",
    )


def report_unexpected(program: str, exc: BaseException, *, debug: bool) -> int:
    request_id = getattr(exc, "request_id", None)
    request_detail = f"; request_id={request_id}" if request_id else ""
    print(
        f"{program} failed unexpectedly: {type(exc).__name__}: {exc}{request_detail}",
        file=sys.stderr,
    )
    if debug:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    else:
        print(
            "Correction: verify the paths and command inputs above. If the failure "
            "persists, re-run with --debug and include that output in the bug report.",
            file=sys.stderr,
        )
    return 1
