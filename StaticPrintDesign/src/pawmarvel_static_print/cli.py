# CLI purpose:
# Initialize, serve, generate, validate, and publish the independent static
# print-design workflow without depending on the personalized template tooling.

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from .authoring import generate_design, initialize_workspace
from .bundles import parse_selection, prepare_release
from .core import (
    StaticPrintError,
    bundle_id_for,
    design_identity,
    design_source_path,
    discover_profiles,
    sha256_file,
    validate_id,
)
from .lifecycle import import_review_collection
from .publisher import (
    publish_release,
    rebuild_discovery_catalog,
    update_product_lifecycle,
    validate_prefix,
)
from .retirement import retire_obsolete_artifacts
from .reviews import list_operator_decisions
from .server import ServerConfig, create_server

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _path(value: str) -> Path:
    if not value.strip():
        raise argparse.ArgumentTypeError("path must not be empty")
    return Path(value).expanduser()


def _load_environment(path: Path) -> tuple[str, ...]:
    """Load a simple private KEY=value file without evaluating shell code."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return ()
    loaded: list[str] = []
    try:
        lines = resolved.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise StaticPrintError(
            f"shared environment file is not readable: {resolved}; error={exc}"
        ) from exc
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise StaticPrintError(
                f"shared environment entry must use KEY=value: "
                f"file={resolved}; line={line_number}"
            )
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not ENVIRONMENT_KEY.fullmatch(key):
            raise StaticPrintError(
                f"shared environment key is invalid: file={resolved}; "
                f"line={line_number}; key={key!r}"
            )
        try:
            values = shlex.split(raw_value, comments=True, posix=True)
        except ValueError as exc:
            raise StaticPrintError(
                f"shared environment value has invalid quoting: "
                f"file={resolved}; line={line_number}; error={exc}"
            ) from exc
        if len(values) > 1:
            raise StaticPrintError(
                f"shared environment values containing spaces must be quoted: "
                f"file={resolved}; line={line_number}; key={key!r}"
            )
        os.environ[key] = values[0] if values else ""
        loaded.append(key)
    return tuple(loaded)


def _environment_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false")
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise StaticPrintError(
        f"{name} must be true or false; actual={raw!r}"
    )


def _require_approved_subject(work: Path, design_id: str) -> None:
    identity = design_identity(design_id)
    if identity.subject_id is None:
        raise StaticPrintError(
            f"product operations require an approved subject design; actual seed={design_id!r}"
        )
    source = design_source_path(work / "static-graduated-designs", design_id)
    if not source.is_file():
        raise StaticPrintError(
            f"graduated subject preview does not exist: {source.resolve()}"
        )
    digest = sha256_file(source)
    current = next(
        (
            item
            for item in list_operator_decisions(work / "static-reviews.sqlite3").get(
                design_id, []
            )
            if item["source_sha256"] == digest
        ),
        None,
    )
    if current is None or current["decision"] != "approved":
        raise StaticPrintError(
            f"subject preview has no current approved operator decision: "
            f"design_id={design_id!r}; source={source.resolve()}"
        )


def _publication_settings(
    args: argparse.Namespace,
    work: Path,
    parser: argparse.ArgumentParser,
) -> tuple[str, str, str | None, str | None]:
    environment = work / "static-print.env"
    loaded = _load_environment(environment)
    if loaded:
        print(f"Loaded shared environment: {environment} ({len(loaded)} settings)")
    bucket = args.bucket or os.environ.get("STATIC_PRINT_S3_BUCKET")
    prefix = args.prefix or os.environ.get(
        "STATIC_PRINT_S3_PREFIX", "templates/static_design"
    )
    aws_profile = args.aws_profile or os.environ.get("AWS_PROFILE")
    region = (
        args.region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
    )
    if not bucket:
        parser.error("--bucket or STATIC_PRINT_S3_BUCKET is required")
    return bucket, validate_prefix(prefix), aws_profile, region


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pawmarvel-static-print")
    parser.add_argument("--debug", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init-workspace")
    init.add_argument("--work-root", type=_path, default=PROJECT_ROOT / "work")

    generate = subparsers.add_parser("generate")
    generate.add_argument("--design-id", required=True)
    generate.add_argument("--version-id", required=True)
    generate.add_argument("--product-profile", type=_path, required=True)
    generate.add_argument("--fit", choices=("cover",), default="cover")
    generate.add_argument(
        "--upscale-backend",
        choices=("deterministic", "bria"),
        help="print enhancement backend; defaults to STATIC_PRINT_UPSCALE_BACKEND",
    )
    generate.add_argument(
        "--normalize-print-alpha",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="normalize near-transparent fringe and near-opaque pixels",
    )
    generate.add_argument("--work-root", type=_path, default=PROJECT_ROOT / "work")

    publish = subparsers.add_parser("publish")
    publish.add_argument("--release-id", required=True)
    publish.add_argument("--selection", action="append", required=True)
    publish.add_argument(
        "--profiles-root", type=_path, default=PROJECT_ROOT / "profiles"
    )
    publish.add_argument("--work-root", type=_path, default=PROJECT_ROOT / "work")
    publish.add_argument("--bucket")
    publish.add_argument("--prefix")
    publish.add_argument("--aws-profile")
    publish.add_argument("--region")
    publish.add_argument("--execute", action="store_true")

    lifecycle = subparsers.add_parser("set-product-status")
    lifecycle.add_argument(
        "--selection",
        action="append",
        required=True,
        help="design-id:product-profile-id; repeatable",
    )
    lifecycle.add_argument("--status", choices=("active", "retired"), required=True)
    lifecycle.add_argument("--reason", default="")
    lifecycle.add_argument("--work-root", type=_path, default=PROJECT_ROOT / "work")
    lifecycle.add_argument("--bucket")
    lifecycle.add_argument("--prefix")
    lifecycle.add_argument("--aws-profile")
    lifecycle.add_argument("--region")

    rebuild_catalog = subparsers.add_parser("rebuild-catalog")
    rebuild_catalog.add_argument(
        "--work-root", type=_path, default=PROJECT_ROOT / "work"
    )
    rebuild_catalog.add_argument("--bucket")
    rebuild_catalog.add_argument("--prefix")
    rebuild_catalog.add_argument("--aws-profile")
    rebuild_catalog.add_argument("--region")

    retire = subparsers.add_parser(
        "retire-obsolete-artifacts",
        help="audit contract-invalid local/S3 artifacts; dry-run by default",
    )
    retire.add_argument(
        "--retirement-id",
        help="save or resume one reviewed plan; required with --execute",
    )
    retire.add_argument(
        "--reason",
        default="",
        help="auditable reason; required when --retirement-id is set",
    )
    retire.add_argument("--work-root", type=_path, default=PROJECT_ROOT / "work")
    retire.add_argument("--bucket")
    retire.add_argument("--prefix")
    retire.add_argument("--aws-profile")
    retire.add_argument("--region")
    retire.add_argument(
        "--execute",
        action="store_true",
        help="execute the previously saved plan with this ID and reason",
    )

    serve = subparsers.add_parser("serve")
    serve.add_argument("--work-root", type=_path, default=PROJECT_ROOT / "work")
    serve.add_argument(
        "--review-collection",
        type=_path,
        action="append",
        default=[],
        help="import direct child PNG files from this local folder before serving; repeatable",
    )
    serve.add_argument("--profiles-root", type=_path, default=PROJECT_ROOT / "profiles")
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8876)
    serve.add_argument("--open", action="store_true")
    serve.add_argument(
        "--reviewer-access-code",
    )
    serve.add_argument(
        "--operator-access-code",
    )
    serve.add_argument("--s3-bucket")
    serve.add_argument("--s3-prefix")
    serve.add_argument("--aws-profile")
    serve.add_argument("--region")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init-workspace":
            print(json.dumps(initialize_workspace(args.work_root.resolve()), indent=2))
            return 0
        if args.command == "generate":
            work = args.work_root.resolve()
            _load_environment(work / "static-print.env")
            _require_approved_subject(work, args.design_id)
            upscale_backend = args.upscale_backend or os.environ.get(
                "STATIC_PRINT_UPSCALE_BACKEND", "deterministic"
            )
            normalize_setting = (
                args.normalize_print_alpha
                if args.normalize_print_alpha is not None
                else _environment_bool("STATIC_PRINT_NORMALIZE_ALPHA", False)
            )
            result = generate_design(
                inputs_root=work / "static-graduated-designs",
                authoring_root=work / "static-authoring",
                design_id=args.design_id,
                version_id=args.version_id,
                profiles=[args.product_profile.resolve()],
                fit=args.fit,
                upscale_backend=upscale_backend,
                normalize_print_alpha=normalize_setting,
            )
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "publish":
            work = args.work_root.resolve()
            bucket, prefix, aws_profile, region = _publication_settings(
                args, work, parser
            )
            profiles = discover_profiles(args.profiles_root.resolve())
            selections = [parse_selection(value, profiles) for value in args.selection]
            for selection in selections:
                _require_approved_subject(work, selection.design_id)
            plan = prepare_release(
                release_id=args.release_id,
                selections=selections,
                inputs_root=work / "static-graduated-designs",
                authoring_root=work / "static-authoring",
                exchange_root=work / "static-exchange",
            )
            result = publish_release(
                plan,
                exchange_root=work / "static-exchange",
                bucket=bucket,
                prefix=prefix,
                aws_profile=aws_profile,
                region=region,
                execute=args.execute,
            )
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "set-product-status":
            work = args.work_root.resolve()
            bucket, prefix, aws_profile, region = _publication_settings(
                args, work, parser
            )
            bundle_ids: list[str] = []
            for selection in args.selection:
                parts = selection.split(":")
                if len(parts) != 2:
                    parser.error(
                        "--selection must be design-id:product-profile-id; "
                        f"actual={selection!r}"
                    )
                design_id = design_identity(parts[0]).design_id
                profile_id = validate_id(parts[1], "product profile ID")
                bundle_ids.append(bundle_id_for(design_id, profile_id))
            result = update_product_lifecycle(
                bucket=bucket,
                prefix=prefix,
                profile=aws_profile,
                region=region,
                exchange_root=work / "static-exchange",
                bundle_ids=bundle_ids,
                status=args.status,
                reason=args.reason,
            )
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "rebuild-catalog":
            work = args.work_root.resolve()
            bucket, prefix, aws_profile, region = _publication_settings(
                args, work, parser
            )
            result = rebuild_discovery_catalog(
                bucket=bucket,
                prefix=prefix,
                profile=aws_profile,
                region=region,
                exchange_root=work / "static-exchange",
            )
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "retire-obsolete-artifacts":
            work = args.work_root.resolve()
            bucket, prefix, aws_profile, region = _publication_settings(
                args, work, parser
            )
            result = retire_obsolete_artifacts(
                work_root=work,
                bucket=bucket,
                prefix=prefix,
                profile=aws_profile,
                region=region,
                retirement_id=args.retirement_id,
                reason=args.reason,
                execute=args.execute,
            )
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "serve":
            work_root = args.work_root.resolve()
            environment = work_root / "static-print.env"
            loaded = _load_environment(environment)
            reviewer_access_code = args.reviewer_access_code or os.environ.get(
                "STATIC_PRINT_REVIEWER_ACCESS_CODE"
            )
            operator_access_code = args.operator_access_code or os.environ.get(
                "STATIC_PRINT_OPERATOR_ACCESS_CODE"
            )
            s3_bucket = args.s3_bucket or os.environ.get("STATIC_PRINT_S3_BUCKET")
            s3_prefix = args.s3_prefix or os.environ.get(
                "STATIC_PRINT_S3_PREFIX", "templates/static_design"
            )
            aws_profile = args.aws_profile or os.environ.get("AWS_PROFILE")
            region = (
                args.region
                or os.environ.get("AWS_REGION")
                or os.environ.get("AWS_DEFAULT_REGION")
            )
            validate_prefix(s3_prefix)
            loopback = args.bind in {"127.0.0.1", "localhost", "::1"}
            if not loopback and (not reviewer_access_code or not operator_access_code):
                raise StaticPrintError(
                    "non-loopback serving requires both reviewer and operator access codes"
                )
            if (
                reviewer_access_code
                and operator_access_code
                and reviewer_access_code == operator_access_code
            ):
                raise StaticPrintError(
                    "reviewer and operator access codes must be different"
                )
            initialize_workspace(work_root)
            for collection in args.review_collection:
                import_result = import_review_collection(work_root, collection)
                print(
                    f"Imported review collection {import_result['collection_root']}: "
                    f"{len(import_result['imported'])} new, "
                    f"{len(import_result['updated'])} updated, "
                    f"{len(import_result['unchanged'])} unchanged, "
                    f"{len(import_result['skipped'])} skipped, "
                    f"{import_result['semantic_id_calls']} semantic naming calls"
                )
            config = ServerConfig(
                project_root=PROJECT_ROOT,
                work_root=work_root,
                profiles_root=args.profiles_root.resolve(),
                reviewer_access_code=reviewer_access_code,
                operator_access_code=operator_access_code,
                s3_bucket=s3_bucket,
                s3_prefix=s3_prefix,
                aws_profile=aws_profile,
                aws_region=region,
            )
            server = create_server(args.bind, args.port, config)
            reviewer = f"http://{args.bind}:{server.server_port}/"
            operator = f"http://{args.bind}:{server.server_port}/operator"
            active_seed_count = sum(
                1
                for path in config.inputs_root.iterdir()
                if path.is_dir() and (path / "source-design.png").is_file()
            )
            print(f"Workspace: {config.work_root}")
            if loaded:
                print(
                    f"Loaded shared environment: {environment} ({len(loaded)} settings)"
                )
            print(
                f"Active seed input: {config.inputs_root} ({active_seed_count} seed(s))"
            )
            print(f"Reviewer GUI: {reviewer}")
            print(f"Operator GUI: {operator}")
            if args.open:
                webbrowser.open(operator)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
    except StaticPrintError as exc:
        parser.error(str(exc))
    except Exception as exc:
        if args.debug:
            raise
        parser.error(
            f"unexpected {type(exc).__name__}: {exc}; rerun with --debug for a traceback"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
