# CLI purpose:
# Expose immutable release construction/validation and explicit S3 publication
# while keeping catalog rules and storage transport in independent modules.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .cli_errors import add_debug_argument, report_unexpected
from .production_bundle import validate_production_bundle
from .release_catalog import build_release, validate_release
from .s3_publisher import publish_s3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-catalog",
        description="Build, validate, and explicitly publish immutable releases.",
    )
    add_debug_argument(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build-release")
    build.add_argument("--release-id", required=True)
    build.add_argument("--bundle", type=Path, action="append", required=True)
    build.add_argument(
        "--exchange-root",
        type=Path,
        required=True,
        help="root containing canonical bundles/ and releases/ directories",
    )
    build.add_argument("--asset-base-url")

    validate = commands.add_parser("validate")
    target = validate.add_mutually_exclusive_group(required=True)
    target.add_argument("--bundle", type=Path)
    target.add_argument("--release-catalog", type=Path)
    validate.add_argument(
        "--exchange-root",
        type=Path,
        help="verify a release catalog and every referenced local bundle",
    )

    publish = commands.add_parser(
        "publish-s3",
        help="validate and upload an immutable reviewed release to Amazon S3",
    )
    publish.add_argument("--release-catalog", type=Path, required=True)
    publish.add_argument("--exchange-root", type=Path, required=True)
    publish.add_argument("--bucket", required=True)
    publish.add_argument(
        "--prefix",
        default="",
        help=(
            "optional key prefix below the bucket, without s3:// or a leading/"
            "trailing slash"
        ),
    )
    publish.add_argument("--aws-profile")
    publish.add_argument("--region")
    publish.add_argument(
        "--authoring-root",
        type=Path,
        help="authoring root where verified publication receipts are recorded",
    )
    publish.add_argument(
        "--execute",
        action="store_true",
        help="perform uploads; omission prints a validated dry-run plan",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build-release":
            result = build_release(
                release_id=args.release_id,
                bundles=args.bundle,
                exchange_root=args.exchange_root,
                asset_base_url=args.asset_base_url,
            )
        elif args.command == "publish-s3":
            result = publish_s3(
                release_catalog=args.release_catalog,
                exchange_root=args.exchange_root,
                bucket=args.bucket,
                prefix=args.prefix,
                aws_profile=args.aws_profile,
                region=args.region,
                authoring_root=args.authoring_root,
                execute=args.execute,
            )
        elif args.bundle:
            validate_production_bundle(args.bundle)
            result = args.bundle.expanduser().resolve()
        else:
            if args.exchange_root is None:
                parser.error(
                    "--exchange-root is required with --release-catalog so referenced "
                    "bundle manifests and assets are verified"
                )
            validate_release(args.release_catalog, exchange_root=args.exchange_root)
            result = args.release_catalog.expanduser().resolve()
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return report_unexpected("pawmarvel-catalog", exc, debug=args.debug)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
