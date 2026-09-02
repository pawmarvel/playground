# CLI purpose:
# Orchestrate profile-driven template creation, reference-guided sample-pet
# transformation, layout confirmation, preview/print rendering, and optional
# publication of the complete reusable two-resolution template bundle.

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .bundle import (
    CATALOG_FILENAME,
    BundleError,
    TEMPLATE_ID_PATTERN,
    catalog_template_id,
    publish_bundle,
)
from .cli import (
    UserInputError,
    _atomic_write_bytes,
    _resolve_api_key,
    _validate_image,
    _validate_regular_file,
    generate,
)
from .config import ConfigError, load_layout
from .font_catalog import (
    FontCatalogError,
    default_local_font_catalog,
    discover_font_catalog,
)
from .expanded_font_catalog import ExpandedFontCatalogError, materialize_expanded_fonts
from .font_license import FontLicenseError, resolve_ofl_license
from .image_size import ImageSizeError, validate_generation_size
from .layout_server import EditorConfig, serve_layout_editor
from .product_profile import (
    ProductProfile,
    ProductProfileError,
    load_product_profile,
    validate_print_output,
)
from .print_upscale import (
    PrintOutputs,
    PrintUpscaleError,
    _read_token as _resolve_bria_token,
    prepare_print_pet,
    prepare_print_template,
)
from .renderer import RenderError, render_to_files


class PipelineError(ValueError):
    """A one-step pipeline input or stage is invalid."""


LayoutRunner = Callable[..., None]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-pipeline",
        description=(
            "Create one template and tracked personalization preview, with an "
            "optional profile-driven print and bundle publication run."
        ),
    )
    parser.add_argument(
        "--sample-design",
        type=Path,
        action="append",
        required=True,
        help=(
            "finished design reference; repeat in priority order to add supporting "
            "references (the first remains the primary layout reference)"
        ),
    )
    parser.add_argument(
        "--art-prompt", type=Path, required=True, help="prompt used to create art.png"
    )
    parser.add_argument(
        "--pet-prompt",
        type=Path,
        required=True,
        help="design-specific prompt used with the user pet and finished reference",
    )
    parser.add_argument("--pet-image", type=Path, required=True)
    parser.add_argument("--pet-name", required=True)
    parser.add_argument(
        "--font", type=Path,
        help="explicit OFL font override; omit to auto-match from --font-catalog",
    )
    parser.add_argument(
        "--font-license",
        type=Path,
        help="OFL.txt for --font (defaults to a sibling OFL.txt)",
    )
    parser.add_argument(
        "--font-catalog",
        type=Path,
        action="append",
        default=[],
        help=(
            "directory recursively containing eligible TTF/OFL font families; "
            "repeat to combine catalogs; omit with --font unset to use the "
            "curated local catalog"
        ),
    )
    parser.add_argument(
        "--font-catalog-mode", choices=("local", "expanded"), default="local",
        help="expanded adds pinned remote OFL candidates through a validated cache",
    )
    parser.add_argument(
        "--font-index", type=Path,
        default=Path("assets/fonts/expanded-catalog.json"),
        help="versioned expanded OFL catalog index",
    )
    parser.add_argument("--font-cache", type=Path, default=Path(".pawmarvel-font-cache"))
    parser.add_argument("--font-shortlist-limit", type=int, default=24)
    parser.add_argument("--font-offline", action="store_true", help="use expanded cache only; never download")
    parser.add_argument(
        "--runtime-model",
        choices=("gpt-image-2", "gemini"),
        default="gpt-image-2",
        help="model route encoded in layout.json (default: gpt-image-2)",
    )
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument(
        "--run-dir",
        type=Path,
        help="tracked preview directory (default: TEMPLATE_DIR/runs/PET-NAME)",
    )
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--image-model", default="gpt-image-2")
    resolution = parser.add_mutually_exclusive_group(required=True)
    resolution.add_argument(
        "--product-profile",
        type=Path,
        help="product profile that defines print and preview layer dimensions",
    )
    resolution.add_argument(
        "--art-resolution",
        "--art-size",
        dest="art_size",
        metavar="WIDTHxHEIGHT",
        help=(
            "required canonical art.png pixel dimensions as WIDTHxHEIGHT; "
            "never inferred from the sample screenshot"
        ),
    )
    parser.add_argument(
        "--pet-size",
        help="override transformed-pet generation size (profile default when omitted)",
    )
    parser.add_argument(
        "--quality", choices=("low", "medium", "high", "auto"), default="high"
    )
    parser.add_argument(
        "--layout-mode",
        choices=("interactive", "existing"),
        default="interactive",
        help="open the editor or reuse TEMPLATE_DIR/layout.json",
    )
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument(
        "--design-id",
        "--template-id",
        dest="design_id",
        help=(
            "design identity used with the product profile to derive the catalog "
            "template ID; --template-id is a deprecated alias"
        ),
    )
    parser.add_argument(
        "--bundle-output-dir",
        type=Path,
        help="publish a clean two-resolution bundle under OUTPUT_DIR/TEMPLATE_ID",
    )
    parser.add_argument(
        "--print-dir",
        type=Path,
        help="print staging directory (default: RUN_DIR/print)",
    )
    parser.add_argument(
        "--upscale-backend",
        choices=("deterministic", "bria"),
        default="deterministic",
        help="print-layer upscale backend (default: deterministic)",
    )
    parser.add_argument(
        "--bria-api-key-file",
        type=Path,
        help="UTF-8 Bria token file used when --upscale-backend bria",
    )
    parser.add_argument(
        "--rerun-step",
        action="append",
        choices=("art", "pet", "layout"),
        default=[],
        help=(
            "rerun only the selected authoring step, then rebuild preview, "
            "provenance, and requested print/bundle outputs; repeat to combine steps"
        ),
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inputs and print the resolved plan without writing or calling APIs",
    )
    return parser


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return result or "preview"


def _check_size(value: str, label: str) -> str:
    if not re.fullmatch(r"[1-9]\d*x[1-9]\d*", value):
        raise PipelineError(f"{label} must use WIDTHxHEIGHT")
    return value


def _copy_file(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    _atomic_write_bytes(destination, source.read_bytes())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _generation_args(
    *,
    sample_design: Sequence[Path] | Path | None,
    pet_image: Path | None,
    prompt_file: Path,
    api_key_file: Path | None,
    output_dir: Path,
    output_name: str,
    model: str,
    size: str,
    quality: str,
    background: str,
    force: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        sample_design=sample_design,
        pet_image=pet_image,
        prompt_file=prompt_file,
        api_key_file=api_key_file,
        output_dir=output_dir,
        output_name=output_name,
        output_format="png",
        model=model,
        size=size,
        quality=quality,
        background=background,
        force=force,
        dry_run=False,
    )


def _ordered_paths(value: Sequence[Path] | Path | None) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, Path):
        return [value]
    return list(value)


def _staged_reference_path(
    template_dir: Path, source: Path, index: int
) -> Path:
    if index == 1:
        return template_dir / f"source-reference-design{source.suffix.lower()}"
    return (
        template_dir
        / "source-reference-designs"
        / f"reference-design-{index:04d}{source.suffix.lower()}"
    )


def _stage(label: str, number: int, total: int) -> None:
    print(f"\n[{number}/{total}] {label}", file=sys.stderr, flush=True)


def _load_previous_run(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PipelineError(
            f"selective rerun requires an existing pipeline manifest: {path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError(
            f"cannot read existing pipeline manifest {path}: {exc}"
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("sources"), dict):
        raise PipelineError(f"existing pipeline manifest has invalid sources: {path}")
    return value


def _require_matching_source(
    previous: dict[str, Any], label: str, current: Path, *, selected_step: str | None
) -> None:
    source = previous["sources"].get(label)
    expected = source.get("sha256") if isinstance(source, dict) else None
    if not isinstance(expected, str) or expected != _sha256(current):
        hint = (
            f"include --rerun-step {selected_step}"
            if selected_step is not None
            else "start a full pipeline run in a new or cleared working directory"
        )
        raise PipelineError(
            f"selective rerun source changed since run.json: {label}; {hint}"
        )


def _require_matching_references(
    previous: dict[str, Any], current: Sequence[Path]
) -> None:
    stored = previous["sources"].get("sample_designs")
    if isinstance(stored, list):
        expected = [
            item.get("sha256") if isinstance(item, dict) else None for item in stored
        ]
    else:
        primary = previous["sources"].get("sample_design")
        expected = [primary.get("sha256") if isinstance(primary, dict) else None]
    actual = [_sha256(path) for path in current]
    if expected != actual:
        raise PipelineError(
            "selective rerun source changed since run.json: sample_designs; "
            "start a full pipeline run in a new or cleared working directory"
        )


def run_pipeline(
    args: argparse.Namespace,
    *,
    client: Any | None = None,
    layout_runner: LayoutRunner = serve_layout_editor,
) -> dict[str, Path]:
    sample_values = _ordered_paths(args.sample_design)
    samples = [
        _validate_image(sample, f"sample design {index}")
        for index, sample in enumerate(sample_values, 1)
    ]
    if not samples:
        raise PipelineError("at least one --sample-design is required")
    sample = samples[0]
    art_prompt_source = _validate_regular_file(args.art_prompt, "art prompt")
    pet_prompt_source = _validate_regular_file(args.pet_prompt, "pet prompt")
    pet_source = _validate_image(args.pet_image, "pet image")
    explicit_font = args.font is not None
    font = _validate_regular_file(args.font, "font") if explicit_font else None
    try:
        font_license = resolve_ofl_license(font, args.font_license) if font else None
    except FontLicenseError as exc:
        raise PipelineError(str(exc)) from exc
    pet_name = args.pet_name.strip()
    if not pet_name:
        raise PipelineError("--pet-name must not be empty")
    rerun_steps = tuple(dict.fromkeys(getattr(args, "rerun_step", [])))
    selective_rerun = bool(rerun_steps)
    if selective_rerun and args.force:
        raise PipelineError("--rerun-step cannot be combined with --force")
    if "layout" in rerun_steps and args.layout_mode != "interactive":
        raise PipelineError("--rerun-step layout requires --layout-mode interactive")
    full_run = not selective_rerun
    run_art = full_run or "art" in rerun_steps
    run_pet = full_run or "pet" in rerun_steps
    run_layout = full_run or "layout" in rerun_steps
    replace_outputs = args.force or selective_rerun
    font_catalogs = tuple(
        path.expanduser().resolve() for path in getattr(args, "font_catalog", [])
    )
    if not font_catalogs and not explicit_font:
        try:
            font_catalogs = (default_local_font_catalog(),)
        except FontCatalogError as exc:
            raise PipelineError(str(exc)) from exc
    font_index = args.font_index.expanduser().resolve() if args.font_index else None
    font_cache = args.font_cache.expanduser().resolve()
    expanded_fonts: tuple[Path, ...] = ()
    if args.font_catalog_mode == "expanded" and run_layout and args.layout_mode == "interactive":
        if font_index is None:
            raise PipelineError("--font-index is required in expanded font catalog mode")
        try:
            expanded_fonts = materialize_expanded_fonts(
                font_index,
                font_cache,
                limit=args.font_shortlist_limit,
                offline=args.font_offline,
            )
        except ExpandedFontCatalogError as exc:
            if not font_catalogs and font is None:
                raise PipelineError(str(exc)) from exc
            print(f"Font catalog warning: {exc}; using local OFL catalog.", file=sys.stderr)
    if run_layout and args.layout_mode == "interactive":
        try:
            font_candidates = discover_font_catalog(
                font,
                font_license,
                catalog_roots=font_catalogs,
                additional_fonts=expanded_fonts,
            )
        except FontCatalogError as exc:
            raise PipelineError(str(exc)) from exc
        if font is None:
            font = font_candidates[0].font
            font_license = font_candidates[0].license
    elif font is None:
        raise PipelineError("--font is required when the interactive layout editor is not run")
    if not 0 <= args.port <= 65535:
        raise PipelineError("--port must be between 0 and 65535")
    profile: ProductProfile | None = None
    if args.product_profile is not None:
        profile = load_product_profile(args.product_profile)
        art_size = profile.preview_art_size.api_value()
        pet_size = profile.preview_pet_size.api_value()
        if args.pet_size is not None and _check_size(
            args.pet_size, "--pet-size"
        ) != pet_size:
            raise PipelineError(
                "--pet-size cannot override product-profile preview.transformed_pet"
            )
    else:
        art_size = _check_size(args.art_size, "--art-resolution")
        pet_size = _check_size(args.pet_size or "1024x1024", "--pet-size")
    try:
        art_size = validate_generation_size(
            art_size,
            model=args.image_model,
            label=("product profile preview.art" if profile else "--art-resolution"),
            allow_auto=False,
        )
        pet_size = validate_generation_size(
            pet_size,
            model=args.image_model,
            label="--pet-size",
            allow_auto=False,
        )
    except ImageSizeError as exc:
        raise PipelineError(str(exc)) from exc
    template_dir = args.template_dir.expanduser().resolve()
    run_dir = (
        args.run_dir.expanduser().resolve()
        if args.run_dir is not None
        else template_dir / "runs" / _slug(f"{pet_source.stem}-{pet_name}")
    )
    bundle_requested = args.design_id is not None or args.bundle_output_dir is not None
    if (args.design_id is None) != (args.bundle_output_dir is None):
        raise PipelineError(
            "--design-id and --bundle-output-dir must be supplied together"
        )
    if bundle_requested and profile is None:
        raise PipelineError("bundle publication requires --product-profile")
    if args.design_id is not None and not TEMPLATE_ID_PATTERN.fullmatch(
        args.design_id
    ):
        raise PipelineError(
            "--design-id must contain 3-64 lowercase letters, numbers, or internal hyphens"
        )
    if not bundle_requested and args.print_dir is not None:
        raise PipelineError("--print-dir requires bundle publication")
    if not bundle_requested and args.upscale_backend != "deterministic":
        raise PipelineError("--upscale-backend requires bundle publication")
    if args.bria_api_key_file is not None and args.upscale_backend != "bria":
        raise PipelineError("--bria-api-key-file requires --upscale-backend bria")

    print_dir = (
        args.print_dir.expanduser().resolve()
        if args.print_dir is not None
        else run_dir / "print"
    )
    bundle_output_dir = (
        args.bundle_output_dir.expanduser().resolve()
        if args.bundle_output_dir is not None
        else None
    )
    catalog_id = (
        catalog_template_id(args.design_id, profile.profile_id)
        if args.design_id is not None and profile is not None
        else None
    )
    bundle_path = (
        bundle_output_dir / catalog_id
        if bundle_output_dir and catalog_id
        else None
    )
    catalog_path = bundle_output_dir / CATALOG_FILENAME if bundle_output_dir else None
    if bundle_requested:
        if print_dir.exists() and not print_dir.is_dir():
            raise PipelineError(f"--print-dir is not a directory: {print_dir}")
        assert bundle_output_dir is not None and bundle_path is not None
        if bundle_output_dir.exists() and not bundle_output_dir.is_dir():
            raise PipelineError(
                f"--bundle-output-dir is not a directory: {bundle_output_dir}"
            )
        if print_dir == template_dir:
            raise PipelineError("--print-dir must not be the template directory")
        protected_roots = (template_dir, run_dir, print_dir)
        if any(
            protected == bundle_path or protected.is_relative_to(bundle_path)
            for protected in protected_roots
        ):
            raise PipelineError(
                "published bundle path must not equal or contain a working directory"
            )
        if args.upscale_backend == "bria" and not args.dry_run:
            _resolve_bria_token(args.bria_api_key_file)

    source_references = [
        _staged_reference_path(template_dir, source, index)
        for index, source in enumerate(samples, 1)
    ]
    source_reference = source_references[0]
    staged_profile = template_dir / "product-profile.json"
    art = template_dir / "art.png"
    layout_path = template_dir / "layout.json"
    bundled_font = template_dir / "fonts" / font.name
    bundled_font_license = template_dir / "fonts" / "OFL.txt"
    staged_pet = run_dir / f"input-pet{pet_source.suffix.lower()}"
    transformed_pet = run_dir / "transformed-pet.png"
    preview = run_dir / "preview.png"
    preview_debug = run_dir / "preview-debug.png"
    layout_snapshot = run_dir / "layout.snapshot.json"
    manifest = run_dir / "run.json"
    final_print = print_dir / "final-print.png"
    final_print_debug = print_dir / "final-print-debug.png"

    planned = [
        *source_references,
        art,
        staged_pet,
        transformed_pet,
        preview,
        preview_debug,
        layout_snapshot,
        manifest,
    ]
    if profile is not None:
        planned.append(staged_profile)
    if bundle_requested:
        planned.extend([final_print, final_print_debug])
        if bundle_path is not None:
            planned.append(bundle_path)
    if args.layout_mode == "interactive":
        planned.extend(
            [
                layout_path,
                bundled_font,
                bundled_font_license,
                template_dir / "qa" / "calibration-preview.png",
            ]
        )

    previous_run: dict[str, Any] | None = None
    if selective_rerun:
        previous_run = _load_previous_run(manifest)
        _require_matching_references(previous_run, samples)
        if profile is not None:
            assert profile.path is not None
            _require_matching_source(
                previous_run,
                "product_profile",
                profile.path,
                selected_step=None,
            )
        if not run_art:
            _require_matching_source(
                previous_run, "art_prompt", art_prompt_source, selected_step="art"
            )
        if not run_pet:
            _require_matching_source(
                previous_run, "pet_prompt", pet_prompt_source, selected_step="pet"
            )
            _require_matching_source(
                previous_run, "pet_image", pet_source, selected_step="pet"
            )
        previous_plan = previous_run.get("pipeline")
        if not isinstance(previous_plan, dict):
            raise PipelineError(
                f"existing pipeline manifest has invalid pipeline plan: {manifest}"
            )
        for key, current in (
            ("art_size", art_size),
            ("pet_size", pet_size),
            (
                "product_profile_id",
                profile.profile_id if profile is not None else None,
            ),
            (
                "print_size",
                profile.print_size.api_value() if profile is not None else None,
            ),
            ("runtime_model", args.runtime_model),
        ):
            if previous_plan.get(key) != current:
                raise PipelineError(
                    f"selective rerun cannot change {key}; start a full pipeline run"
                )
        for index, (source, staged) in enumerate(
            zip(samples, source_references, strict=True), 1
        ):
            _validate_image(staged, f"staged source reference {index}")
            if _sha256(staged) != _sha256(source):
                raise PipelineError(
                    f"staged source reference {index} differs from --sample-design; "
                    "start a full pipeline run"
                )
        if profile is not None:
            assert profile.path is not None
            _validate_regular_file(staged_profile, "staged product profile")
            if _sha256(staged_profile) != _sha256(profile.path):
                raise PipelineError(
                    "staged product profile differs from --product-profile; "
                    "start a full pipeline run"
                )
        if not run_art:
            _validate_image(art, "existing art template")
        if not run_pet:
            _validate_image(transformed_pet, "existing transformed pet")

    reuse_layout = not run_layout or args.layout_mode == "existing"
    if reuse_layout and not layout_path.is_file():
        mode = "selective rerun" if selective_rerun else "--layout-mode existing"
        raise PipelineError(f"{mode} requires an existing layout: {layout_path}")
    if reuse_layout:
        try:
            existing_layout = load_layout(template_dir, layout_path)
            resolve_ofl_license(existing_layout.font_path)
        except (ConfigError, FontLicenseError) as exc:
            raise PipelineError(str(exc)) from exc
        expected_runtime_model = (
            "gpt-image-2" if args.runtime_model == "gpt-image-2" else None
        )
        if existing_layout.runtime_model != expected_runtime_model:
            raise PipelineError(
                "existing layout runtime model does not match --runtime-model"
            )

    if full_run:
        existing = [path for path in planned if path.exists()]
        if bundle_requested and print_dir.is_dir() and any(print_dir.iterdir()):
            existing.insert(0, print_dir)
        if existing and not args.force:
            raise PipelineError(
                f"planned output already exists: {existing[0]} "
                "(pass --force to replace pipeline outputs)"
            )

    needs_image_client = run_art or run_pet
    plan = {
        "run_mode": "selective-rerun" if selective_rerun else "full",
        "rerun_steps": list(rerun_steps),
        "sample_design": str(sample),
        "sample_designs": [str(path) for path in samples],
        "product_profile": str(staged_profile) if profile is not None else None,
        "product_profile_id": profile.profile_id if profile is not None else None,
        "print_size": profile.print_size.api_value() if profile is not None else None,
        "art_prompt": str(art_prompt_source),
        "pet_prompt": str(pet_prompt_source),
        "pet_image": str(pet_source),
        "pet_name": pet_name,
        "font": str(font) if explicit_font else None,
        "font_license": str(font_license) if explicit_font else None,
        "font_selection": "explicit" if explicit_font else "reference_visual_match_v1",
        "font_catalogs": [str(path) for path in font_catalogs],
        "font_catalog_mode": args.font_catalog_mode,
        "font_index": str(font_index) if font_index else None,
        "font_cache": str(font_cache) if args.font_catalog_mode == "expanded" else None,
        "font_shortlist_limit": args.font_shortlist_limit,
        "font_offline": args.font_offline,
        "runtime_model": args.runtime_model,
        "template_dir": str(template_dir),
        "run_dir": str(run_dir),
        "art_size": art_size,
        "pet_size": pet_size,
        "layout_mode": args.layout_mode,
        "image_model": args.image_model,
        "quality": args.quality,
        "print_dir": str(print_dir) if bundle_requested else None,
        "upscale_backend": args.upscale_backend if bundle_requested else None,
        "bundle_output_dir": str(bundle_output_dir) if bundle_output_dir else None,
        "design_id": args.design_id,
        "template_id": catalog_id,
        "bundle": str(bundle_path) if bundle_path else None,
        "catalog": str(catalog_path) if catalog_path else None,
        "api_key_source": (
            (
                str(args.api_key_file.expanduser().resolve())
                if args.api_key_file is not None
                else "OPENAI_API_KEY"
            )
            if needs_image_client
            else None
        ),
    }
    print("Resolved one-step pipeline:", file=sys.stderr)
    print(json.dumps(plan, indent=2), file=sys.stderr, flush=True)
    if args.dry_run:
        outputs = {"template_dir": template_dir, "run_dir": run_dir}
        if bundle_path is not None:
            outputs.update(
                {
                    "print_dir": print_dir,
                    "bundle": bundle_path,
                    "catalog": catalog_path,
                }
            )
        return outputs
    if needs_image_client and client is None:
        _, api_key = _resolve_api_key(args.api_key_file)
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

    stage_labels: list[str] = []
    if full_run:
        stage_labels.append("Stage immutable source copies")
    if run_art:
        stage_labels.append("Generate background art template")
    if run_pet:
        stage_labels.append("Transform customer pet from finished design reference")
    if run_layout:
        stage_labels.append(
            "Confirm layout in local editor"
            if args.layout_mode == "interactive"
            else "Reuse existing layout"
        )
    stage_labels.append("Render final preview and debug overlay")
    if bundle_requested:
        stage_labels.extend(
            [
                "Upscale preview layers and derive print layout",
                "Render profile-sized print candidate",
                "Publish clean two-resolution bundle",
            ]
        )
    stage_labels.append("Write tracked run metadata")
    total = len(stage_labels)
    stage_number = 0

    def announce(label: str) -> None:
        nonlocal stage_number
        stage_number += 1
        _stage(label, stage_number, total)

    if full_run:
        announce("Stage immutable source copies")
        for source, staged in zip(samples, source_references, strict=True):
            _copy_file(source, staged)
        if profile is not None:
            assert profile.path is not None
            _copy_file(profile.path, staged_profile)
        _copy_file(pet_source, staged_pet)

    if run_art:
        announce("Generate background art template")
        generate(
            _generation_args(
                sample_design=source_references,
                pet_image=None,
                prompt_file=art_prompt_source,
                api_key_file=args.api_key_file,
                output_dir=template_dir,
                output_name=art.name,
                model=args.image_model,
                size=art_size,
                quality=args.quality,
                background="transparent",
                force=replace_outputs,
            ),
            client=client,
        )

    if run_pet:
        announce("Transform customer pet from finished design reference")
        if selective_rerun:
            _copy_file(pet_source, staged_pet)
        generate(
            _generation_args(
                sample_design=source_references,
                pet_image=staged_pet,
                prompt_file=pet_prompt_source,
                api_key_file=args.api_key_file,
                output_dir=run_dir,
                output_name=transformed_pet.name,
                model=args.image_model,
                size=pet_size,
                quality=args.quality,
                background="transparent",
                force=replace_outputs,
            ),
            client=client,
        )

    if run_layout:
        announce(
            "Confirm layout in local editor"
            if args.layout_mode == "interactive"
            else "Reuse existing layout"
        )
    if run_layout and args.layout_mode == "interactive":
        layout_runner(
            EditorConfig(
                art=art,
                reference=source_reference,
                pet=transformed_pet,
                pet_name=pet_name,
                font=font if explicit_font else None,
                font_license=font_license if explicit_font else None,
                font_catalogs=font_catalogs,
                font_catalog_mode=args.font_catalog_mode,
                font_index=font_index,
                font_cache=font_cache,
                font_shortlist_limit=args.font_shortlist_limit,
                font_offline=args.font_offline,
                runtime_model=(
                    "gpt-image-2" if args.runtime_model == "gpt-image-2" else None
                ),
                output=layout_path,
                force=replace_outputs,
            ),
            port=args.port,
            open_browser=not args.no_open,
        )
    active_layout = load_layout(template_dir, layout_path)
    try:
        active_font_license = resolve_ofl_license(active_layout.font_path)
    except FontLicenseError as exc:
        raise PipelineError(str(exc)) from exc

    announce("Render final preview and debug overlay")
    render_to_files(
        template_dir=template_dir,
        pet_image=transformed_pet,
        pet_name=pet_name,
        output=preview,
        debug_output=preview_debug,
        force=replace_outputs,
    )

    print_outputs: PrintOutputs | None = None
    print_pet_manifest: Path | None = None
    published_bundle: Path | None = None
    if bundle_requested:
        assert profile is not None
        announce("Upscale reusable template art and derive print layout")
        template_print_outputs = prepare_print_template(
            template_dir=template_dir,
            layout_path=layout_path,
            target_size=(profile.print_size.width, profile.print_size.height),
            output_dir=print_dir,
            backend=args.upscale_backend,
            bria_token_file=args.bria_api_key_file,
            product_profile=staged_profile,
            force=replace_outputs,
        )
        announce("Upscale the representative transformed-pet layer")
        pet_print_outputs = prepare_print_pet(
            template_dir=template_dir,
            layout_path=layout_path,
            print_layout_path=template_print_outputs.layout,
            transformed_pet=transformed_pet,
            output_dir=print_dir,
            backend=args.upscale_backend,
            bria_token_file=args.bria_api_key_file,
            force=replace_outputs,
        )
        print_pet_manifest = pet_print_outputs.manifest
        print_outputs = PrintOutputs(
            art=template_print_outputs.art,
            pet=pet_print_outputs.pet,
            layout=template_print_outputs.layout,
            manifest=template_print_outputs.manifest,
            product_profile=template_print_outputs.product_profile,
        )

        announce("Render profile-sized print candidate")
        render_to_files(
            template_dir=print_dir,
            layout_path=print_outputs.layout,
            pet_image=print_outputs.pet,
            pet_name=pet_name,
            output=final_print,
            debug_output=final_print_debug,
            png_dpi=(
                (
                    float(profile.print_spec["dpi"]),
                    float(profile.print_spec["dpi"]),
                )
                if profile.print_spec.get("dpi") is not None
                else None
            ),
            force=replace_outputs,
        )
        validate_print_output(profile, final_print)

        announce("Publish clean two-resolution bundle")
        assert bundle_output_dir is not None and args.design_id is not None
        published_bundle = publish_bundle(
            template_dir=template_dir,
            output_dir=bundle_output_dir,
            design_id=args.design_id,
            product_profile=staged_profile,
            exemplar=transformed_pet,
            reference_design=source_references,
            art_prompt=art_prompt_source,
            pet_prompt=pet_prompt_source,
            print_art=print_outputs.art,
            print_layout_path=print_outputs.layout,
            runtime_model=(
                "gpt-image-2" if args.runtime_model == "gpt-image-2" else None
            ),
            force=replace_outputs,
        )

    announce("Write tracked run metadata")
    _copy_file(layout_path, layout_snapshot)
    artifacts: dict[str, str | None] = {
        "art": str(art),
        "layout": str(layout_path),
        "layout_snapshot": str(layout_snapshot),
        "transformed_pet": str(transformed_pet),
        "preview": str(preview),
        "preview_debug": str(preview_debug),
        "product_profile": str(staged_profile) if profile is not None else None,
        "font": str(active_layout.font_path),
        "font_license": str(active_font_license),
        "print_art": str(print_outputs.art) if print_outputs else None,
        "print_transformed_pet": str(print_outputs.pet) if print_outputs else None,
        "print_layout": str(print_outputs.layout) if print_outputs else None,
        "template_print_manifest": (
            str(print_outputs.manifest) if print_outputs else None
        ),
        "print_pet_manifest": str(print_pet_manifest) if print_pet_manifest else None,
        "final_print": str(final_print) if print_outputs else None,
        "final_print_debug": str(final_print_debug) if print_outputs else None,
    }
    artifact_sha256 = {
        label: _sha256(Path(path)) if path is not None else None
        for label, path in artifacts.items()
    }
    record = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": plan,
        "sources": {
            "sample_design": {"path": str(sample), "sha256": _sha256(sample)},
            "sample_designs": [
                {
                    "path": str(path),
                    "sha256": _sha256(path),
                    "role": "primary" if index == 1 else "supporting",
                }
                for index, path in enumerate(samples, 1)
            ],
            "art_prompt": {
                "path": str(art_prompt_source),
                "sha256": _sha256(art_prompt_source),
            },
            "pet_prompt": {
                "path": str(pet_prompt_source),
                "sha256": _sha256(pet_prompt_source),
            },
            "pet_image": {"path": str(pet_source), "sha256": _sha256(pet_source)},
            "product_profile": (
                {"path": str(profile.path), "sha256": _sha256(profile.path)}
                if profile is not None and profile.path is not None
                else None
            ),
            "staged_source_reference": {
                "path": str(source_reference),
                "sha256": _sha256(source_reference),
                "role": "visual-context-only-not-layout-geometry",
            },
            "staged_source_references": [
                {
                    "path": str(path),
                    "sha256": _sha256(path),
                    "role": "primary" if index == 1 else "supporting",
                }
                for index, path in enumerate(source_references, 1)
            ],
        },
        "publication": (
            {
                "bundle": str(published_bundle),
                "template_id": catalog_id,
                "design_id": args.design_id,
                "product_profile_id": profile.profile_id if profile else None,
                "catalog": str(catalog_path),
                "status": "published",
            }
            if published_bundle is not None
            else None
        ),
        "artifacts": artifacts,
        "artifact_sha256": artifact_sha256,
    }
    _atomic_write_bytes(manifest, _json_bytes(record))
    completion = f"\nPipeline complete. Preview: {preview}"
    if published_bundle is not None:
        completion += f"\nBundle: {published_bundle}"
    print(completion, file=sys.stderr, flush=True)
    outputs = {
        "template_dir": template_dir,
        "run_dir": run_dir,
        "source_reference": source_reference,
        "art": art,
        "layout": layout_path,
        "transformed_pet": transformed_pet,
        "preview": preview,
        "preview_debug": preview_debug,
        "layout_snapshot": layout_snapshot,
        "manifest": manifest,
    }
    if print_outputs is not None and published_bundle is not None:
        outputs.update(
            {
                "print_dir": print_dir,
                "print_art": print_outputs.art,
                "print_transformed_pet": print_outputs.pet,
                "print_layout": print_outputs.layout,
                "template_print_manifest": print_outputs.manifest,
                "print_pet_manifest": print_pet_manifest,
                "final_print": final_print,
                "final_print_debug": final_print_debug,
                "bundle": published_bundle,
                "catalog": catalog_path,
            }
        )
    return outputs


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        outputs = run_pipeline(args)
    except (
        PipelineError,
        UserInputError,
        ProductProfileError,
        PrintUpscaleError,
        BundleError,
        ConfigError,
        RenderError,
    ) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        request_id = getattr(exc, "request_id", None)
        detail = f" (request ID: {request_id})" if request_id else ""
        print(f"Pipeline failed: {exc}{detail}", file=sys.stderr)
        return 1
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
