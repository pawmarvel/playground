from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageOps

from .core import (
    DesignIdentity,
    ProductProfile,
    StaticPrintError,
    design_authoring_path,
    design_identity,
    design_source_path,
    image_info,
    load_profile,
    product_profile_sha256,
    read_json,
    require_png,
    resolve_design_identity,
    sha256_file,
    utc_now,
    validate_id,
    validate_version,
    write_json,
)
from .image_quality import print_readiness_report_for_path, print_readiness_warning
from .upscale import BriaProvider, upscale_for_print

PREVIEW_MAX_EDGE = 1080
OPAQUE_FULL_BLEED = "opaque-full-bleed"
PRESERVE_TRANSPARENCY = "preserve-transparency"


def _discovery_png_info(path: Path) -> tuple[int, int, str]:
    """Read PNG metadata without decoding every pixel during gallery refresh."""

    resolved = path.expanduser().resolve()
    try:
        with Image.open(resolved) as image:
            if image.format != "PNG":
                raise StaticPrintError(
                    f"source design must contain PNG data: {resolved}; "
                    f"actual_format={image.format!r}"
                )
            return image.width, image.height, image.mode
    except StaticPrintError:
        raise
    except (OSError, ValueError) as exc:
        raise StaticPrintError(
            f"source design is not a readable PNG: {resolved}; error={exc}"
        ) from exc


def initialize_workspace(root: Path) -> dict[str, str]:
    paths = {
        "inputs": root / "static-design-inputs",
        "graduated": root / "static-graduated-designs",
        "abandoned": root / "static-abandoned-designs",
        "authoring": root / "static-authoring",
        "exchange_releases": root / "static-exchange/releases",
        "exchange_publications": root / "static-exchange/publications",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return {key: str(path.resolve()) for key, path in paths.items()}


def annotate_design_collections(
    designs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add a stable UI collection without changing design or bundle identity.

    Intake metadata is authoritative. Manually added seeds fall back to the
    largest sibling group sharing a prefix of at least two ID segments; the
    longest prefix wins when candidate groups have equal membership.
    """

    explicit_by_seed: dict[str, str] = {}
    seed_ids: set[str] = set()
    for design in designs:
        seed_id = str(design["seed_design_id"])
        seed_ids.add(seed_id)
        intake = design.get("intake")
        collection_id = (
            intake.get("collection_id") if isinstance(intake, dict) else None
        )
        if isinstance(collection_id, str) and collection_id:
            explicit_by_seed[seed_id] = collection_id

    manual_seed_ids = seed_ids - set(explicit_by_seed)
    prefix_members: dict[str, set[str]] = defaultdict(set)
    for seed_id in manual_seed_ids:
        segments = seed_id.split("-")
        for length in range(2, len(segments)):
            prefix_members["-".join(segments[:length])].add(seed_id)

    inferred_by_seed: dict[str, str] = {}
    for seed_id in manual_seed_ids:
        candidates = [
            (prefix, members)
            for prefix, members in prefix_members.items()
            if seed_id in members and len(members) >= 2
        ]
        if candidates:
            inferred_by_seed[seed_id] = max(
                candidates,
                key=lambda item: (len(item[1]), len(item[0].split("-"))),
            )[0]

    for design in designs:
        seed_id = str(design["seed_design_id"])
        if seed_id in explicit_by_seed:
            design["collection_id"] = explicit_by_seed[seed_id]
            design["collection_source"] = "intake"
        elif seed_id in inferred_by_seed:
            design["collection_id"] = inferred_by_seed[seed_id]
            design["collection_source"] = "shared-prefix"
        else:
            design["collection_id"] = "@manual"
            design["collection_source"] = "manual"
    return designs


def discover_designs(inputs_root: Path, authoring_root: Path) -> list[dict[str, Any]]:
    designs: list[dict[str, Any]] = []
    if not inputs_root.is_dir():
        return designs

    def add(identity: DesignIdentity) -> None:
        design_id = identity.design_id
        source = inputs_root / identity.relative_directory / "source-design.png"
        intake_path = inputs_root / identity.seed_design_id / "intake.json"
        if not source.is_file():
            return
        existing = next(
            (item for item in designs if item["design_id"] == design_id), None
        )
        if existing is not None:
            raise StaticPrintError(
                f"ambiguous design ID {design_id!r}; both source paths exist: "
                f"{existing['source']} and {source.resolve()}"
            )
        width, height, mode = _discovery_png_info(source)
        source_digest = sha256_file(source)
        intake = read_json(intake_path) if intake_path.is_file() else None
        source_transparency: dict[str, Any] | None = None
        subject_review_attention: dict[str, Any] | None = None
        subject_print_readiness: dict[str, Any] | None = None
        if identity.subject_id is None:
            if (
                isinstance(intake, dict)
                and intake.get("source_sha256") == source_digest
                and isinstance(intake.get("source_transparency"), dict)
            ):
                source_transparency = intake["source_transparency"]
        else:
            provenance_path = (
                inputs_root / identity.relative_directory / "subject-generation.json"
            )
            if provenance_path.is_file():
                provenance = read_json(provenance_path)
                if provenance.get("output_sha256") == source_digest:
                    if isinstance(provenance.get("output_transparency"), dict):
                        source_transparency = provenance["output_transparency"]
                    if isinstance(provenance.get("print_readiness"), dict):
                        subject_print_readiness = provenance["print_readiness"]
                    semantic = provenance.get("semantic_validation")
                    attempts = (
                        semantic.get("attempts")
                        if isinstance(semantic, dict)
                        else None
                    )
                    latest_validation = (
                        attempts[-1]
                        if isinstance(attempts, list)
                        and attempts
                        and isinstance(attempts[-1], dict)
                        else None
                    )
                    recorded_attention = provenance.get("review_attention")
                    attention_source = (
                        recorded_attention
                        if isinstance(recorded_attention, dict)
                        and recorded_attention.get("required") is True
                        else latest_validation
                        if latest_validation
                        and latest_validation.get("operator_review_recommended") is True
                        else None
                    )
                    if attention_source:
                        subject_review_attention = {
                            "kind": "subject-behavior",
                            "behavior_context": str(
                                attention_source.get(
                                    "behavior_context", "borderline"
                                )
                            ),
                            "reason": str(
                                attention_source.get(
                                    "reason", "Animal behavior needs operator review."
                                )
                            ),
                            "source_sha256": source_digest,
                        }
        versions: list[dict[str, Any]] = []
        profile_root = authoring_root / identity.relative_directory
        if profile_root.is_dir():
            for product_dir in sorted(profile_root.iterdir()):
                if not product_dir.is_dir():
                    continue
                try:
                    product_profile_id = validate_id(
                        product_dir.name, "product profile ID"
                    )
                except StaticPrintError:
                    continue
                for version_dir in sorted(product_dir.iterdir(), reverse=True):
                    if not version_dir.is_dir():
                        continue
                    try:
                        validate_version(version_dir.name)
                    except StaticPrintError:
                        continue
                    preview = version_dir / f"{design_id}-preview.png"
                    generation_path = version_dir / "generation.json"
                    generation = (
                        read_json(generation_path)
                        if generation_path.is_file()
                        else None
                    )
                    prints = sorted(
                        path.name
                        for path in version_dir.glob(f"{design_id}--*.png")
                        if path.is_file()
                    )
                    profile_previews = sorted(
                        path.name
                        for path in (version_dir / "profile-previews").glob("*.png")
                        if path.is_file()
                    )
                    authoring_relative = str(
                        (
                            identity.relative_directory
                            / product_profile_id
                            / version_dir.name
                        ).as_posix()
                    )
                    versions.append(
                        {
                            "version_id": version_dir.name,
                            "product_profile_id": product_profile_id,
                            "authoring_relative": authoring_relative,
                            "preview": preview.name if preview.is_file() else None,
                            "prints": prints,
                            "profile_previews": profile_previews,
                            "generation": generation,
                        }
                    )
        designs.append(
            {
                "design_id": design_id,
                "seed_design_id": identity.seed_design_id,
                "subject_id": identity.subject_id,
                "source": str(source.resolve()),
                "source_width": width,
                "source_height": height,
                "source_mode": mode,
                "source_sha256": source_digest,
                "source_transparency": source_transparency,
                "subject_review_attention": subject_review_attention,
                "subject_print_readiness": subject_print_readiness,
                "intake": intake,
                "versions": versions,
            }
        )

    for seed_directory in sorted(inputs_root.iterdir()):
        if not seed_directory.is_dir():
            continue
        try:
            seed_design_id = validate_id(seed_directory.name, "seed design ID")
            seed_identity = DesignIdentity(seed_design_id)
        except StaticPrintError:
            continue
        add(seed_identity)
        for subject_directory in sorted(seed_directory.iterdir()):
            if not subject_directory.is_dir():
                continue
            try:
                subject_id = validate_id(subject_directory.name, "subject ID")
                subject_identity = DesignIdentity(seed_design_id, subject_id)
            except StaticPrintError:
                continue
            add(subject_identity)
    return sorted(designs, key=lambda item: item["design_id"])


def save_png(image: Image.Image, path: Path, *, dpi: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        options: dict[str, Any] = {"format": "PNG", "optimize": True}
        if dpi is not None:
            options["dpi"] = (dpi, dpi)
        image.save(temporary_path, **options)
        with temporary_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    finally:
        temporary_path.unlink(missing_ok=True)


def _background(size: tuple[int, int], value: str) -> Image.Image:
    if value == "transparent":
        return Image.new("RGBA", size, (0, 0, 0, 0))
    rgb = ImageColor.getrgb(value)
    return Image.new("RGBA", size, (*rgb, 255))


def _is_fully_opaque(image: Image.Image) -> bool:
    if "A" not in image.getbands():
        return True
    return image.getchannel("A").getextrema() == (255, 255)


def _image_transparency_report(image: Image.Image) -> dict[str, Any]:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    minimum, maximum = alpha.getextrema()
    histogram = alpha.histogram()
    total = rgba.width * rgba.height
    non_opaque = total - histogram[255]
    pixels = alpha.load()
    border_values = [pixels[x, 0] for x in range(rgba.width)]
    if rgba.height > 1:
        border_values.extend(pixels[x, rgba.height - 1] for x in range(rgba.width))
    if rgba.width > 1 and rgba.height > 2:
        border_values.extend(pixels[0, y] for y in range(1, rgba.height - 1))
        border_values.extend(
            pixels[rgba.width - 1, y] for y in range(1, rgba.height - 1)
        )
    transparent_border = sum(value < 255 for value in border_values)
    border_percent = (
        (transparent_border / len(border_values)) * 100 if border_values else 0.0
    )
    corners = {
        pixels[0, 0],
        pixels[rgba.width - 1, 0],
        pixels[0, rgba.height - 1],
        pixels[rgba.width - 1, rgba.height - 1],
    }
    has_transparent_background = non_opaque > 0 and (
        border_percent >= 5 or any(value < 255 for value in corners)
    )
    return {
        "has_alpha_channel": "A" in image.getbands(),
        "has_transparent_pixels": non_opaque > 0,
        "has_transparent_background": has_transparent_background,
        "transparent_pixel_percent": round((non_opaque / total) * 100, 3),
        "transparent_border_percent": round(border_percent, 3),
        "alpha_min": int(minimum),
        "alpha_max": int(maximum),
        "result": (
            "transparent-background"
            if has_transparent_background
            else "partial-alpha"
            if non_opaque > 0
            else "opaque"
        ),
    }


def _canvas_policy(image: Image.Image) -> str:
    return OPAQUE_FULL_BLEED if _is_fully_opaque(image) else PRESERVE_TRANSPARENCY


def _design_canvas_policy(
    inputs_root: Path, design_id: str, source: Image.Image
) -> str:
    identity = resolve_design_identity(inputs_root, design_id)
    intake_path = inputs_root / identity.seed_design_id / "intake.json"
    if intake_path.is_file():
        recorded = read_json(intake_path).get("canvas_policy")
        if recorded in {OPAQUE_FULL_BLEED, PRESERVE_TRANSPARENCY}:
            return str(recorded)
    return _canvas_policy(source)


def design_canvas_policy(inputs_root: Path, design_id: str) -> str:
    """Resolve the seed-level canvas contract for a seed or subject design."""

    resolved_design_id = resolve_design_identity(inputs_root, design_id).design_id
    source_path = design_source_path(inputs_root, resolved_design_id).resolve()
    require_png(source_path, "source design")
    with Image.open(source_path) as opened:
        opened.load()
        return _design_canvas_policy(
            inputs_root, resolved_design_id, opened.convert("RGBA")
        )


def _require_opaque_full_bleed(
    image: Image.Image, *, design_id: str, artifact: Path
) -> None:
    if not _is_fully_opaque(image):
        raise StaticPrintError(
            "opaque full-bleed source produced transparent output; "
            f"design_id={design_id!r}; artifact={artifact.resolve()}"
        )


def render_target(
    source: Image.Image,
    *,
    size: tuple[int, int],
    fit: str,
    background: str,
    composition: str = "cover",
) -> Image.Image:
    rgba = source.convert("RGBA")
    if composition == "repeat-horizontal":
        canvas = _background(size, background)
        cell_edges = (0, size[0] // 2, size[0])
        for left, right in zip(cell_edges, cell_edges[1:]):
            cell_size = (right - left, size[1])
            scale = min(cell_size[0] / rgba.width, cell_size[1] / rgba.height)
            copy_size = (
                max(1, round(rgba.width * scale)),
                max(1, round(rgba.height * scale)),
            )
            repeated = (
                rgba.convert("RGBa")
                .resize(copy_size, Image.Resampling.LANCZOS)
                .convert("RGBA")
            )
            canvas.alpha_composite(
                repeated,
                (
                    left + (cell_size[0] - repeated.width) // 2,
                    (cell_size[1] - repeated.height) // 2,
                ),
            )
        return canvas
    if composition != "cover":
        raise StaticPrintError(
            "composition must be cover or repeat-horizontal; "
            f"actual={composition!r}"
        )
    if fit == "cover":
        return ImageOps.fit(
            rgba,
            size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    if fit != "contain":
        raise StaticPrintError(f"fit must be contain or cover; actual={fit!r}")
    resized = ImageOps.contain(rgba, size, method=Image.Resampling.LANCZOS)
    canvas = _background(size, background)
    canvas.alpha_composite(
        resized,
        ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2),
    )
    return canvas


def render_scale(
    source_size: tuple[int, int],
    target_size: tuple[int, int],
    composition: str,
) -> float:
    source_width, source_height = source_size
    target_width, target_height = target_size
    if composition == "repeat-horizontal":
        widest_cell = target_width - target_width // 2
        return min(widest_cell / source_width, target_height / source_height)
    return max(target_width / source_width, target_height / source_height)


def _preview(source: Image.Image) -> Image.Image:
    preview = source.convert("RGBA")
    if max(preview.size) > PREVIEW_MAX_EDGE:
        preview.thumbnail(
            (PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE), Image.Resampling.LANCZOS
        )
    return preview


def _normalize_print_alpha(source: Image.Image) -> Image.Image:
    """Remove near-invisible fringe and normalize visually solid model output."""

    rgba = source.convert("RGBA")
    alpha = rgba.getchannel("A").point(
        lambda value: 0 if 0 < value < 32 else 255 if value >= 250 else value
    )
    rgba.putalpha(alpha)
    return rgba


def generate_design(
    *,
    inputs_root: Path,
    authoring_root: Path,
    design_id: str,
    version_id: str,
    profiles: Iterable[Path | ProductProfile],
    fit: str = "cover",
    operation_id: str | None = None,
    upscale_backend: str = "deterministic",
    normalize_print_alpha: bool = False,
    bria_provider: BriaProvider | None = None,
) -> dict[str, Any]:
    design_id = design_identity(design_id).design_id
    version_id = validate_version(version_id)
    if fit not in {"contain", "cover"}:
        raise StaticPrintError(f"fit must be contain or cover; actual={fit!r}")
    if upscale_backend not in {"deterministic", "bria"}:
        raise StaticPrintError(
            "upscale_backend must be deterministic or bria; "
            f"actual={upscale_backend!r}"
        )
    source_path = design_source_path(inputs_root, design_id).resolve()
    require_png(source_path, "source design")
    loaded_profiles = [
        item if isinstance(item, ProductProfile) else load_profile(item)
        for item in profiles
    ]
    if len(loaded_profiles) != 1:
        raise StaticPrintError(
            "product generation requires exactly one product profile per call; "
            f"actual={[profile.profile_id for profile in loaded_profiles]}"
        )
    profile = loaded_profiles[0]
    profile_digest = product_profile_sha256(profile)
    profile_root = (
        design_authoring_path(authoring_root, design_id, inputs_root=inputs_root)
        / profile.profile_id
    )
    profile_root.mkdir(parents=True, exist_ok=True)
    final_dir = profile_root / version_id
    source_digest = sha256_file(source_path)
    generation_path = final_dir / "generation.json"
    existing_generation = (
        read_json(generation_path) if generation_path.is_file() else None
    )
    if existing_generation is not None:
        if (
            existing_generation.get("schema") != "pawmarvel-static-generation-v3"
            or existing_generation.get("design_id") != design_id
            or existing_generation.get("product_profile_id") != profile.profile_id
            or existing_generation.get("version_id") != version_id
            or existing_generation.get("product_profile_sha256") != profile_digest
        ):
            raise StaticPrintError(
                f"existing generation record is invalid: {generation_path.resolve()}"
            )
        if existing_generation.get("source_sha256") != source_digest:
            raise StaticPrintError(
                f"version {version_id!r} was generated from a different subject preview; "
                f"use a new version ID; record={generation_path.resolve()}"
            )
        if existing_generation.get("fit") != fit:
            raise StaticPrintError(
                f"version {version_id!r} already uses fit={existing_generation.get('fit')!r}; "
                f"use a new version ID for fit={fit!r}"
            )
        if existing_generation.get("composition", "cover") != profile.composition:
            raise StaticPrintError(
                f"version {version_id!r} already uses a different composition; "
                f"expected={profile.composition!r}; "
                f"actual={existing_generation.get('composition', 'cover')!r}; "
                "use a new version ID"
            )
        render_pipeline = existing_generation.get("render_pipeline") or {}
        expected_pipeline = {
            "upscale_backend": upscale_backend,
            "normalize_print_alpha": normalize_print_alpha,
        }
        if any(
            render_pipeline.get(key) != value
            for key, value in expected_pipeline.items()
        ):
            raise StaticPrintError(
                f"version {version_id!r} already uses a different render pipeline; "
                f"expected={expected_pipeline}; actual={render_pipeline}; use a new version ID"
            )
        outputs: list[dict[str, Any]] = []
        existing_print_readiness = existing_generation.get("print_readiness", {})
        if not isinstance(existing_print_readiness, dict):
            existing_print_readiness = {}
        existing_print_reports = existing_print_readiness.get("prints", {})
        if not isinstance(existing_print_reports, dict):
            existing_print_reports = {}
        multiple = len(profile.targets) > 1
        for target in profile.targets:
            filename = (
                f"{design_id}--{profile.profile_id}--{target.target_id}.png"
                if multiple
                else f"{design_id}--{profile.profile_id}.png"
            )
            path = final_dir / filename
            width, height, mode = image_info(path)
            if (width, height) != (target.width, target.height):
                raise StaticPrintError(
                    "existing product artifact has incorrect dimensions: "
                    f"path={path.resolve()}; expected={target.width}x{target.height}; "
                    f"actual={width}x{height}"
                )
            outputs.append(
                {
                    "profile_id": profile.profile_id,
                    "target_id": target.target_id,
                    "path": str(path.resolve()),
                    "filename": filename,
                    "width": width,
                    "height": height,
                    "mode": mode,
                    "transparency": existing_generation.get("transparency", {})
                    .get("prints", {})
                    .get(target.target_id, {}),
                    "print_readiness": existing_print_reports.get(target.target_id),
                }
            )
        preview_path = final_dir / f"{design_id}-preview.png"
        preview_width, preview_height, preview_mode = image_info(preview_path)
        profile_preview_path = (
            final_dir / "profile-previews" / f"{profile.profile_id}.png"
        )
        profile_preview_width, profile_preview_height, _ = image_info(
            profile_preview_path
        )
        return {
            "design_id": design_id,
            "product_profile_id": profile.profile_id,
            "version_id": version_id,
            "fit": fit,
            "composition": profile.composition,
            "canvas_policy": existing_generation.get("canvas_policy"),
            "preview": {
                "path": str(preview_path.resolve()),
                "width": preview_width,
                "height": preview_height,
                "mode": preview_mode,
                "print_readiness": existing_print_readiness.get(
                    "operator_preview"
                ),
            },
            "outputs": outputs,
            "profile_previews": [
                {
                    "profile_id": profile.profile_id,
                    "path": str(profile_preview_path.resolve()),
                    "width": profile_preview_width,
                    "height": profile_preview_height,
                    "transparency": existing_generation.get("transparency", {}).get(
                        "profile_preview", {}
                    ),
                    "print_readiness": existing_print_readiness.get(
                        "profile_preview"
                    ),
                }
            ],
            "print_readiness": existing_print_readiness,
            "warnings": list(existing_generation.get("warnings", [])),
            "reused": True,
        }
    if final_dir.exists():
        raise StaticPrintError(
            "product candidate directory exists without a valid generation record; "
            f"restart the server to quarantine it or remove it after inspection: {final_dir.resolve()}"
        )
    token = (
        validate_id(operation_id, "operation ID")
        if operation_id
        else secrets.token_hex(8)
    )
    output_dir = profile_root / f".{version_id}.partial-{token}"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    generation_path = output_dir / "generation.json"
    preview_path = output_dir / f"{design_id}-preview.png"
    warnings: list[str] = []
    outputs: list[dict[str, Any]] = []
    profile_previews: list[dict[str, Any]] = []
    with Image.open(source_path) as opened:
        opened.load()
        source = opened.convert("RGBA")
        canvas_policy = _design_canvas_policy(inputs_root, design_id, source)
        if canvas_policy == OPAQUE_FULL_BLEED:
            source = source.convert("RGB").convert("RGBA")
        elif normalize_print_alpha:
            source = _normalize_print_alpha(source)
        required_scale = max(
            render_scale(
                source.size,
                (target.width, target.height),
                profile.composition,
            )
            for target in profile.targets
        )
        if upscale_backend == "bria":
            render_source, upscale_provenance = upscale_for_print(
                source,
                required_scale=required_scale,
                preserve_alpha=canvas_policy == PRESERVE_TRANSPARENCY,
                provider=bria_provider,
            )
        else:
            render_source = source
            upscale_provenance = {
                "backend": "deterministic",
                "native_increase": 1,
                "preserve_alpha": canvas_policy == PRESERVE_TRANSPARENCY,
                "source_size": {"width": source.width, "height": source.height},
                "output_size": {"width": source.width, "height": source.height},
            }
        operator_preview = _preview(source)
        save_png(operator_preview, preview_path)
        operator_preview_transparency = _image_transparency_report(operator_preview)
        operator_preview_readiness = print_readiness_report_for_path(
            preview_path,
            expected_size=operator_preview.size,
            canvas_policy=canvas_policy,
            product_profile_id=profile.profile_id,
        )
        operator_preview_warning = print_readiness_warning(
            f"{design_id}/operator-preview", operator_preview_readiness
        )
        if operator_preview_warning:
            warnings.append(operator_preview_warning)
        multiple = len(profile.targets) > 1
        first_target_image: Image.Image | None = None
        for target in profile.targets:
            scale = render_scale(
                render_source.size,
                (target.width, target.height),
                profile.composition,
            )
            filename = (
                f"{design_id}--{profile.profile_id}--{target.target_id}.png"
                if multiple
                else f"{design_id}--{profile.profile_id}.png"
            )
            path = output_dir / filename
            rendered = render_target(
                render_source,
                size=(target.width, target.height),
                fit=fit,
                background=target.background,
                composition=profile.composition,
            )
            if canvas_policy == PRESERVE_TRANSPARENCY and normalize_print_alpha:
                rendered = _normalize_print_alpha(rendered)
            if canvas_policy == OPAQUE_FULL_BLEED:
                _require_opaque_full_bleed(rendered, design_id=design_id, artifact=path)
            save_png(rendered, path, dpi=target.dpi)
            if first_target_image is None:
                first_target_image = rendered.copy()
            width, height, mode = image_info(path)
            print_readiness = print_readiness_report_for_path(
                path,
                expected_size=(target.width, target.height),
                canvas_policy=canvas_policy,
                upscale_ratio=scale,
                product_profile_id=profile.profile_id,
            )
            warning = print_readiness_warning(
                f"{design_id}/{profile.profile_id}/{target.target_id}",
                print_readiness,
            )
            if warning:
                warnings.append(warning)
            outputs.append(
                {
                    "profile_id": profile.profile_id,
                    "target_id": target.target_id,
                    "path": str(path.resolve()),
                    "filename": filename,
                    "width": width,
                    "height": height,
                    "mode": mode,
                    "transparency": _image_transparency_report(rendered),
                    "print_readiness": print_readiness,
                }
            )
        if first_target_image is None:  # ProductProfile validation prevents this.
            raise StaticPrintError(
                f"product profile has no print targets: {profile.profile_id!r}"
            )
        profile_preview_path = (
            output_dir / "profile-previews" / f"{profile.profile_id}.png"
        )
        profile_preview = render_target(
            first_target_image,
            size=(profile.preview_width, profile.preview_height),
            fit="cover",
            background=profile.targets[0].background,
        )
        if canvas_policy == OPAQUE_FULL_BLEED:
            _require_opaque_full_bleed(
                profile_preview,
                design_id=design_id,
                artifact=profile_preview_path,
            )
        save_png(profile_preview, profile_preview_path)
        profile_preview_readiness = print_readiness_report_for_path(
            profile_preview_path,
            expected_size=(profile.preview_width, profile.preview_height),
            canvas_policy=canvas_policy,
            upscale_ratio=max(
                profile.preview_width / first_target_image.width,
                profile.preview_height / first_target_image.height,
            ),
            product_profile_id=profile.profile_id,
        )
        profile_preview_warning = print_readiness_warning(
            f"{design_id}/{profile.profile_id}/profile-preview",
            profile_preview_readiness,
        )
        if profile_preview_warning:
            warnings.append(profile_preview_warning)
        profile_previews.append(
            {
                "profile_id": profile.profile_id,
                "path": str(profile_preview_path.resolve()),
                "width": profile.preview_width,
                "height": profile.preview_height,
                "transparency": _image_transparency_report(profile_preview),
                "print_readiness": profile_preview_readiness,
            }
        )
        generation_transparency = {
            "operator_preview": operator_preview_transparency,
            "profile_preview": profile_previews[0]["transparency"],
            "prints": {item["target_id"]: item["transparency"] for item in outputs},
        }
        generation_print_readiness = {
            "operator_preview": operator_preview_readiness,
            "profile_preview": profile_preview_readiness,
            "prints": {
                item["target_id"]: item["print_readiness"] for item in outputs
            },
        }
    write_json(
        generation_path,
        {
            "schema": "pawmarvel-static-generation-v3",
            "created_at": utc_now(),
            "design_id": design_id,
            "product_profile_id": profile.profile_id,
            "product_profile_sha256": profile_digest,
            "version_id": version_id,
            "source_sha256": source_digest,
            "fit": fit,
            "composition": profile.composition,
            "canvas_policy": canvas_policy,
            "render_pipeline": {
                "upscale_backend": upscale_backend,
                "normalize_print_alpha": normalize_print_alpha,
                "upscale": upscale_provenance,
            },
            "transparency": generation_transparency,
            "print_readiness": generation_print_readiness,
            "warnings": sorted(set(warnings)),
        },
    )
    os.replace(output_dir, final_dir)
    preview_path = final_dir / f"{design_id}-preview.png"
    for item in outputs:
        item["path"] = str((final_dir / item["filename"]).resolve())
    for item in profile_previews:
        item["path"] = str(
            (final_dir / "profile-previews" / f"{profile.profile_id}.png").resolve()
        )
    preview_width, preview_height, preview_mode = image_info(preview_path)
    return {
        "design_id": design_id,
        "product_profile_id": profile.profile_id,
        "version_id": version_id,
        "fit": fit,
        "composition": profile.composition,
        "canvas_policy": canvas_policy,
        "preview": {
            "path": str(preview_path.resolve()),
            "width": preview_width,
            "height": preview_height,
            "mode": preview_mode,
            "print_readiness": operator_preview_readiness,
        },
        "outputs": outputs,
        "profile_previews": profile_previews,
        "print_readiness": generation_print_readiness,
        "warnings": sorted(set(warnings)),
    }
