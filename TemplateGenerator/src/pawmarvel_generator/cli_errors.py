"""Shared command-line diagnostics for unexpected internal failures."""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Sequence


class HelpfulArgumentParser(argparse.ArgumentParser):
    """Turn invisible shell arguments into an actionable parser error."""

    def parse_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        values = list(sys.argv[1:] if args is None else args)
        invisible = [
            (index + 1, repr(value))
            for index, value in enumerate(values)
            if not value.strip()
            and (index == 0 or not values[index - 1].startswith("--"))
        ]
        if invisible:
            rendered = ", ".join(
                f"{position}={value}" for position, value in invisible
            )
            self.error(
                "received empty or whitespace-only command-line argument(s): "
                f"{rendered}. This commonly happens in zsh when an unset optional "
                "array is expanded as \"${ARRAY[@]}\" or an array contains an "
                "empty element. Inspect it with `typeset -p ARRAY`; initialize "
                "optional arrays with `ARRAY=()` before populating them, or omit "
                "the expansion. PawMarvel examples use "
                "`PAWMARVEL_REFERENCE_ARGS=()` for generation references and "
                "`PAWMARVEL_LAYOUT_REFERENCE_ARGS=()` for layout references."
            )
        return super().parse_args(values, namespace)


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
