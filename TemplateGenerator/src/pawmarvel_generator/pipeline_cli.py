# CLI purpose:
# Orchestrate profile-driven template creation, reference-guided sample-pet
# transformation, layout confirmation, and optional preview/print rendering in
# a replaceable scratch workspace. Production publication is selection-only.

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .artifact_io import read_json
from .cli_errors import add_debug_argument, report_unexpected
from .cli import (
    PET_NAME_PLACEHOLDER,
    UserInputError,
    _atomic_write_bytes,
    _read_prompt,
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
from .font_license import FontLicenseError, resolve_ofl_license
from .font_reference import FontReferenceError, load_font_reference
from .layout_reference import LayoutReferenceError, load_layout_reference
from .image_size import ImageSizeError, validate_generation_size
from .layout_server import EditorConfig, serve_layout_editor
from .product_profile import (
    ProductProfile,
    ProductProfileError,
    load_product_profile,
    validate_print_output,
)
from .print_upscale import (
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
            "Create one replaceable template/personalization debug run, with "
            "optional profile-driven print preparation."
        ),
    )
    add_debug_argument(parser)
    parser.add_argument(
        "--reference-design",
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
    parser.add_argument(
        "--pet-name",
        help=(
            "optional personalized name for layout rendering; it is also passed "
            "to pet generation only when the pet prompt contains {{PET_NAME}}"
        ),
    )
    parser.add_argument(
        "--reference-text",
        help="initial exact pet-name text visible in the primary reference design",
    )
    parser.add_argument(
        "--font-reference",
        type=Path,
        help="existing font-reference-v1 JSON used to initialize font matching",
    )
    parser.add_argument(
        "--layout-reference",
        type=Path,
        help="layout-reference-v1 JSON used to initialize pet and name geometry",
    )
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
        "--print-dir",
        type=Path,
        help="enable print preparation and write its scratch artifacts here",
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
            "provenance, and requested print outputs; repeat to combine steps"
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
    reference_design: Sequence[Path] | Path | None,
    pet_image: Path | None,
    pet_name: str | None,
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
        reference_design=reference_design,
        pet_image=pet_image,
        pet_name=pet_name,
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
    value = read_json(
        path,
        label="existing pipeline manifest required for a selective rerun",
        error_type=PipelineError,
        require_object=True,
        correction="run the full pipeline first or fix run.json.",
    )
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
    stored = previous["sources"].get("reference_designs")
    if isinstance(stored, list):
        expected = [
            item.get("sha256") if isinstance(item, dict) else None for item in stored
        ]
    else:
        primary = previous["sources"].get("reference_design")
        expected = [primary.get("sha256") if isinstance(primary, dict) else None]
    actual = [_sha256(path) for path in current]
    if expected != actual:
        raise PipelineError(
            "selective rerun source changed since run.json: reference_designs; "
            "start a full pipeline run in a new or cleared working directory"
        )


def run_pipeline(
    args: argparse.Namespace,
    *,
    client: Any | None = None,
    layout_runner: LayoutRunner = serve_layout_editor,
) -> dict[str, Path]:
    sample_values = _ordered_paths(args.reference_design)
    samples = [
        _validate_image(sample, f"reference design {index}")
        for index, sample in enumerate(sample_values, 1)
    ]
    if not samples:
        raise PipelineError("at least one --reference-design is required")
    sample = samples[0]
    font_reference = (
        args.font_reference.expanduser().resolve()
        if args.font_reference is not None
        else None
    )
    if font_reference is not None:
        try:
            load_font_reference(font_reference, sample)
        except FontReferenceError as exc:
            raise PipelineError(str(exc)) from exc
    layout_reference = (
        args.layout_reference.expanduser().resolve()
        if args.layout_reference is not None
        else None
    )
    if layout_reference is not None:
        try:
            loaded_layout_reference = load_layout_reference(
                layout_reference, sample
            )
        except LayoutReferenceError as exc:
            raise PipelineError(str(exc)) from exc
        if font_reference is not None:
            loaded_font_reference = load_font_reference(font_reference, sample)
            if (
                loaded_layout_reference.name_region.to_dict()
                != loaded_font_reference.region.to_dict()
            ):
                raise PipelineError(
                    "layout reference name_region must match font reference region"
                )
    art_prompt_source = _validate_regular_file(args.art_prompt, "art prompt")
    pet_prompt_source = _validate_regular_file(args.pet_prompt, "pet prompt")
    _, pet_prompt_text = _read_prompt(pet_prompt_source)
    pet_source = _validate_image(args.pet_image, "pet image")
    explicit_font = args.font is not None
    font = _validate_regular_file(args.font, "font") if explicit_font else None
    try:
        font_license = resolve_ofl_license(font, args.font_license) if font else None
    except FontLicenseError as exc:
        raise PipelineError(str(exc)) from exc
    pet_name = (args.pet_name or "").strip() or None
    pet_prompt_uses_name = PET_NAME_PLACEHOLDER in pet_prompt_text
    pet_generation_name = pet_name if pet_prompt_uses_name else None
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
    if run_pet and pet_prompt_uses_name and pet_name is None:
        raise PipelineError(
            f"pet prompt contains {PET_NAME_PLACEHOLDER}; provide --pet-name"
        )
    replace_outputs = args.force or selective_rerun
    font_catalogs = tuple(
        path.expanduser().resolve() for path in getattr(args, "font_catalog", [])
    )
    if not font_catalogs and not explicit_font:
        try:
            font_catalogs = (default_local_font_catalog(),)
        except FontCatalogError as exc:
            raise PipelineError(str(exc)) from exc
    if run_layout and args.layout_mode == "interactive":
        try:
            font_candidates = discover_font_catalog(
                font,
                font_license,
                catalog_roots=font_catalogs,
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
        else template_dir
        / "runs"
        / _slug(f"{pet_source.stem}-{pet_name or 'no-name'}")
    )
    print_requested = args.print_dir is not None
    if print_requested and profile is None:
        raise PipelineError("print preparation requires --product-profile")
    if not print_requested and args.upscale_backend != "deterministic":
        raise PipelineError("--upscale-backend requires --print-dir")
    if args.bria_api_key_file is not None and args.upscale_backend != "bria":
        raise PipelineError("--bria-api-key-file requires --upscale-backend bria")

    print_dir = (
        args.print_dir.expanduser().resolve()
        if args.print_dir is not None
        else run_dir / "print"
    )
    if print_requested:
        if print_dir.exists() and not print_dir.is_dir():
            raise PipelineError(f"--print-dir is not a directory: {print_dir}")
        if print_dir == template_dir:
            raise PipelineError("--print-dir must not be the template directory")
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
    if print_requested:
        planned.extend([final_print, final_print_debug])
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
            ("image_model", args.image_model),
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
                    f"staged source reference {index} differs from --reference-design; "
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
            if existing_layout.has_name:
                assert existing_layout.font_path is not None
                resolve_ofl_license(existing_layout.font_path)
        except (ConfigError, FontLicenseError) as exc:
            raise PipelineError(str(exc)) from exc

    if full_run:
        existing = [path for path in planned if path.exists()]
        if print_requested and print_dir.is_dir() and any(print_dir.iterdir()):
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
        "reference_design": str(sample),
        "reference_designs": [str(path) for path in samples],
        "product_profile": str(staged_profile) if profile is not None else None,
        "product_profile_id": profile.profile_id if profile is not None else None,
        "print_size": profile.print_size.api_value() if profile is not None else None,
        "art_prompt": str(art_prompt_source),
        "pet_prompt": str(pet_prompt_source),
        "pet_image": str(pet_source),
        "pet_name": pet_name,
        "pet_prompt_uses_pet_name": pet_prompt_uses_name,
        "font": str(font) if explicit_font else None,
        "font_license": str(font_license) if explicit_font else None,
        "font_selection": (
            "explicit" if explicit_font else "confirmed_reference_visual_match_v3"
        ),
        "font_catalogs": [str(path) for path in font_catalogs],
        "font_reference": str(font_reference) if font_reference else None,
        "layout_reference": str(layout_reference) if layout_reference else None,
        "reference_text": args.reference_text,
        "template_dir": str(template_dir),
        "run_dir": str(run_dir),
        "art_size": art_size,
        "pet_size": pet_size,
        "layout_mode": args.layout_mode,
        "image_model": args.image_model,
        "quality": args.quality,
        "print_dir": str(print_dir) if print_requested else None,
        "upscale_backend": args.upscale_backend if print_requested else None,
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
        if print_requested:
            outputs["print_dir"] = print_dir
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
    if print_requested:
        stage_labels.extend(
            [
                "Upscale reusable template art and derive print layout",
                "Upscale the representative transformed-pet layer",
                "Render profile-sized print candidate",
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
                reference_design=source_references,
                pet_image=None,
                pet_name=None,
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
                reference_design=source_references,
                pet_image=staged_pet,
                pet_name=pet_generation_name,
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
                font_reference=font_reference,
                layout_reference=layout_reference,
                reference_text=args.reference_text,
                output=layout_path,
                force=replace_outputs,
            ),
            port=args.port,
            open_browser=not args.no_open,
        )
    active_layout = load_layout(template_dir, layout_path)
    if active_layout.has_name and pet_name is None:
        raise PipelineError(
            "--pet-name is required because the selected layout contains a "
            "separate name layer; provide a name or disable the name layer in "
            "the layout editor"
        )
    active_font_license = None
    if active_layout.has_name:
        assert active_layout.font_path is not None
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

    template_print_outputs = None
    pet_print_outputs = None
    if print_requested:
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
        announce("Render profile-sized print candidate")
        render_to_files(
            template_dir=print_dir,
            layout_path=template_print_outputs.layout,
            pet_image=pet_print_outputs.pet,
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
        "font": str(active_layout.font_path) if active_layout.font_path else None,
        "font_license": str(active_font_license) if active_font_license else None,
        "print_art": str(template_print_outputs.art) if template_print_outputs else None,
        "print_transformed_pet": str(pet_print_outputs.pet) if pet_print_outputs else None,
        "print_layout": str(template_print_outputs.layout) if template_print_outputs else None,
        "template_print_manifest": (
            str(template_print_outputs.manifest) if template_print_outputs else None
        ),
        "print_pet_manifest": str(pet_print_outputs.manifest) if pet_print_outputs else None,
        "final_print": str(final_print) if template_print_outputs else None,
        "final_print_debug": str(final_print_debug) if template_print_outputs else None,
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
            "reference_design": {"path": str(sample), "sha256": _sha256(sample)},
            "reference_designs": [
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
        "artifacts": artifacts,
        "artifact_sha256": artifact_sha256,
    }
    _atomic_write_bytes(manifest, _json_bytes(record))
    completion = f"\nPipeline complete. Preview: {preview}"
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
    if template_print_outputs is not None and pet_print_outputs is not None:
        outputs.update(
            {
                "print_dir": print_dir,
                "print_art": template_print_outputs.art,
                "print_transformed_pet": pet_print_outputs.pet,
                "print_layout": template_print_outputs.layout,
                "template_print_manifest": template_print_outputs.manifest,
                "print_pet_manifest": pet_print_outputs.manifest,
                "final_print": final_print,
                "final_print_debug": final_print_debug,
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
        ConfigError,
        RenderError,
    ) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        return report_unexpected("pawmarvel-pipeline", exc, debug=args.debug)
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
