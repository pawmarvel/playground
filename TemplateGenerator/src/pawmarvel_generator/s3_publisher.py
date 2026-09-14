"""Dry-run-first immutable Amazon S3 publication for reviewed releases."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from .artifact_io import sha256
from .bundle import BundleError
from .production_bundle import validate_production_bundle
from .release_catalog import validate_release


def _safe_cli_detail(value: str, *, limit: int = 1000) -> str:
    return " ".join(value.split())[:limit] or "<empty>"


def _aws_context(
    *, aws_profile: str | None, region: str | None, bucket: str, key: str
) -> str:
    return (
        f"object=s3://{bucket}/{key}; profile={aws_profile or '<default>'}; "
        f"region={region or '<default>'}"
    )


def _aws_correction(stderr: str, aws_profile: str | None) -> str:
    if re.search(r"token has expired|sso.*expired|refresh failed", stderr, re.I):
        profile = aws_profile or "<your-profile>"
        return f' Run: aws sso login --profile "{profile}" and retry.'
    return " Verify AWS credentials, profile, region, bucket permissions, and retry."


def _is_conditional_put_conflict(stderr: str) -> bool:
    """Recognize only explicit AWS error names or HTTP status tokens."""
    return re.search(
        r"\b(?:PreconditionFailed|ConditionalRequestConflict)\b"
        r"|\b(?:HTTP(?: status)?|status code)\s*[:=]?\s*(?:409|412)\b",
        stderr,
        re.IGNORECASE,
    ) is not None


def _validate_prefix(prefix: str) -> None:
    if "\\" in prefix:
        raise BundleError("S3 prefix must use forward slashes")
    if prefix != prefix.strip("/"):
        raise BundleError("S3 prefix must not have a leading or trailing slash")
    if prefix and any(
        part in {"", ".", ".."} for part in prefix.split("/")
    ):
        raise BundleError("S3 prefix must not contain empty, dot, or parent segments")


def _publication_key(prefix: str, relative: str) -> str:
    _validate_prefix(prefix)
    prefix_path = PurePosixPath(prefix) if prefix else None
    relative_path = PurePosixPath(relative)
    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_path.parts
    ):
        raise BundleError(f"invalid publication path: {relative}")
    return (
        f"{prefix_path.as_posix()}/{relative_path.as_posix()}"
        if prefix_path is not None
        else relative_path.as_posix()
    )


def build_s3_publication_plan(
    *, release_catalog: Path, exchange_root: Path, prefix: str = ""
) -> list[dict[str, Any]]:
    """Return a validated, manifest-last S3 upload plan for one release."""

    _validate_prefix(prefix)
    root = exchange_root.expanduser().resolve()
    catalog_path = release_catalog.expanduser().resolve()
    catalog = validate_release(catalog_path, exchange_root=root)
    expected_catalog = root / "releases" / catalog["release_id"] / "catalog.json"
    if catalog_path != expected_catalog:
        raise BundleError(
            f"release catalog must use the canonical exchange path: {expected_catalog}"
        )

    plan: list[dict[str, Any]] = []
    for entry in catalog["templates"]:
        manifest_relative = PurePosixPath(entry["manifest_path"])
        manifest_path = root.joinpath(*manifest_relative.parts)
        manifest = validate_production_bundle(manifest_path.parent)
        for asset in sorted(manifest["assets"], key=lambda item: item["path"]):
            asset_relative = manifest_relative.parent / PurePosixPath(asset["path"])
            plan.append(
                {
                    "source": manifest_path.parent.joinpath(
                        *PurePosixPath(asset["path"]).parts
                    ),
                    "key": _publication_key(prefix, asset_relative.as_posix()),
                    "media_type": asset["media_type"],
                    "sha256": asset["sha256"],
                    "bytes": asset["bytes"],
                }
            )
        plan.append(
            {
                "source": manifest_path,
                "key": _publication_key(prefix, manifest_relative.as_posix()),
                "media_type": "application/json",
                "sha256": entry["manifest_sha256"],
                "bytes": manifest_path.stat().st_size,
            }
        )

    catalog_relative = (
        PurePosixPath("releases") / catalog["release_id"] / "catalog.json"
    )
    plan.append(
        {
            "source": catalog_path,
            "key": _publication_key(prefix, catalog_relative.as_posix()),
            "media_type": "application/json",
            "sha256": sha256(catalog_path),
            "bytes": catalog_path.stat().st_size,
        }
    )
    keys = [item["key"] for item in plan]
    if len(keys) != len(set(keys)):
        raise BundleError("S3 publication plan contains duplicate object keys")
    return plan


def _aws_cli(
    arguments: list[str], *, aws_profile: str | None, region: str | None
) -> subprocess.CompletedProcess[str]:
    command = ["aws", "--no-cli-pager"]
    if aws_profile:
        command.extend(["--profile", aws_profile])
    if region:
        command.extend(["--region", region])
    command.extend(arguments)
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise BundleError(
            "AWS CLI v2 is required for S3 publication; install it and run aws configure"
        ) from exc


def _verify_s3_object(
    *,
    bucket: str,
    item: dict[str, Any],
    aws_profile: str | None,
    region: str | None,
) -> None:
    result = _aws_cli(
        [
            "s3api",
            "head-object",
            "--bucket",
            bucket,
            "--key",
            item["key"],
            "--checksum-mode",
            "ENABLED",
            "--output",
            "json",
        ],
        aws_profile=aws_profile,
        region=region,
    )
    if result.returncode != 0:
        raise BundleError(
            "S3 head-object failed; "
            f"{_aws_context(aws_profile=aws_profile, region=region, bucket=bucket, key=item['key'])}; "
            f"exit_code={result.returncode}; stderr={_safe_cli_detail(result.stderr)}."
            f"{_aws_correction(result.stderr, aws_profile)}"
        )
    try:
        remote = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BundleError(
            "AWS CLI returned malformed head-object JSON; "
            f"{_aws_context(aws_profile=aws_profile, region=region, bucket=bucket, key=item['key'])}; "
            f"line={exc.lineno}; column={exc.colno}; error={exc.msg}; "
            f"stdout={_safe_cli_detail(result.stdout)}. Correction: rerun the AWS "
            "CLI command directly and verify that AWS CLI v2 emits JSON."
        ) from exc
    expected_checksum = base64.b64encode(bytes.fromhex(item["sha256"])).decode("ascii")
    if (
        remote.get("ContentLength") != item["bytes"]
        or remote.get("ChecksumSHA256") != expected_checksum
        or remote.get("ContentType") != item["media_type"]
    ):
        raise BundleError(
            "existing S3 object differs from local artifact: "
            f"s3://{bucket}/{item['key']}"
        )


def publish_s3(
    *,
    release_catalog: Path,
    exchange_root: Path,
    bucket: str,
    prefix: str,
    aws_profile: str | None,
    region: str | None,
    authoring_root: Path | None,
    execute: bool,
) -> str:
    if not bucket or bucket.startswith("s3://") or "/" in bucket:
        raise BundleError("--bucket must be an S3 bucket name, without s3:// or a path")
    plan = build_s3_publication_plan(
        release_catalog=release_catalog,
        exchange_root=exchange_root,
        prefix=prefix,
    )
    destination = f"s3://{bucket}/{plan[-1]['key']}"
    print(
        f"S3 publication plan: {len(plan)} immutable objects; "
        f"catalog will be uploaded last to {destination}"
    )
    if not execute:
        for item in plan:
            print(f"DRY RUN {item['source']} -> s3://{bucket}/{item['key']}")
        print("Dry run only. Re-run with --execute after local review.")
        return destination
    if authoring_root is None:
        raise BundleError(
            "--authoring-root is required with --execute so verified publication "
            "receipts can be recorded automatically"
        )

    for index, item in enumerate(plan, start=1):
        checksum = base64.b64encode(bytes.fromhex(item["sha256"])).decode("ascii")
        print(f"Uploading {index}/{len(plan)}: s3://{bucket}/{item['key']}")
        result = _aws_cli(
            [
                "s3api",
                "put-object",
                "--bucket",
                bucket,
                "--key",
                item["key"],
                "--body",
                str(item["source"]),
                "--content-type",
                item["media_type"],
                "--checksum-algorithm",
                "SHA256",
                "--checksum-sha256",
                checksum,
                "--if-none-match",
                "*",
                "--output",
                "json",
            ],
            aws_profile=aws_profile,
            region=region,
        )
        if result.returncode != 0 and not _is_conditional_put_conflict(
            result.stderr
        ):
            raise BundleError(
                "S3 put-object failed; "
                f"{_aws_context(aws_profile=aws_profile, region=region, bucket=bucket, key=item['key'])}; "
                f"source={item['source']}; exit_code={result.returncode}; "
                f"stderr={_safe_cli_detail(result.stderr)}."
                f"{_aws_correction(result.stderr, aws_profile)}"
            )
        _verify_s3_object(
            bucket=bucket,
            item=item,
            aws_profile=aws_profile,
            region=region,
        )
    if sha256(release_catalog.expanduser().resolve()) != plan[-1]["sha256"]:
        raise BundleError(
            "release catalog changed during publication; remote objects were verified "
            "but receipts were not recorded. Restore the published catalog bytes and retry."
        )
    _record_publication_receipts(
        release_catalog=release_catalog,
        exchange_root=exchange_root,
        authoring_root=authoring_root,
        transfer_location=destination,
    )
    return destination


def _record_publication_receipts(
    *,
    release_catalog: Path,
    exchange_root: Path,
    authoring_root: Path,
    transfer_location: str,
) -> list[Path]:
    """Bind every verified release entry to its retained local graduation."""
    from .authoring import record_publication

    catalog_path = release_catalog.expanduser().resolve()
    root = exchange_root.expanduser().resolve()
    authoring = authoring_root.expanduser().resolve()
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(
            f"verified release catalog cannot be reread for publication receipts: "
            f"{catalog_path}: {exc}"
        ) from exc
    receipts = []
    for entry in catalog["templates"]:
        manifest_path = root.joinpath(
            *PurePosixPath(entry["manifest_path"]).parts
        ).resolve()
        manifest = validate_production_bundle(manifest_path.parent)
        selection_id = manifest.get("provenance", {}).get("selection_id")
        if not isinstance(selection_id, str) or not selection_id:
            raise BundleError(
                f"published bundle has no selection identity: {manifest_path}"
            )
        selection = (
            authoring
            / manifest["design_id"]
            / manifest["product_profile_id"]
            / "graduations"
            / selection_id
            / "selection.json"
        )
        receipt = record_publication(
            selection=selection,
            bundle_manifest=manifest_path,
            release_catalog=catalog_path,
            transfer_location=transfer_location,
            authoring_root=authoring,
        )
        receipts.append(receipt)
        print(f"Recorded publication receipt: {receipt}")
    return receipts
