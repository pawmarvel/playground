from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from PIL import Image

from . import __version__
from .artifact_io import atomic_json, mismatch, read_json, sha256, utc_now
from .bundle import catalog_template_id
from .cli import (
    PET_NAME_PLACEHOLDER,
    build_parser as build_generate_parser,
    generate,
)
from .comparison_artifacts import render_comparison_contact_sheet
from .config import load_layout, parse_layout
from .font_catalog import FontCatalogError, discover_font_catalog
from .font_license import resolve_ofl_license
from .font_reference import FontReferenceError, load_font_reference
from .fixture_set import (
    FixtureSetError,
    PetFixture,
    fixture_selection_warnings,
    load_fixture_selection,
    load_fixture_set,
)
from .layout_reference import LayoutReferenceError, load_layout_reference
from .layout_server import EditorConfig, serve_layout_editor
from .personalization import PersonalizationError, pet_name_policy, validate_pet_name
from .print_upscale import (
    TemplatePrintOutputs,
    prepare_print_pet,
    prepare_print_template,
)
from .product_profile import load_product_profile, validate_print_output
from .renderer import render_composition, render_to_files


class AuthoringError(ValueError):
    """An immutable authoring lifecycle operation is invalid."""


KINDS = {"art", "pet", "layout"}
GENERATION_QUALITIES = {"low", "medium", "high", "auto"}
ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _json(path: Path) -> dict[str, Any]:
    return read_json(
        path,
        label="authoring artifact",
        error_type=AuthoringError,
        require_object=True,
        correction="fix the file or regenerate the artifact that produced it.",
    )


def _id(value: str, label: str) -> str:
    if not 3 <= len(value) <= 96 or value[0] == "-" or value[-1] == "-" or any(c not in ID_CHARS for c in value):
        raise AuthoringError(f"{label} must be 3-96 lowercase letters, numbers, or internal hyphens")
    return value


def _product_relative(path: Path, product_root: Path) -> str:
    path = path.expanduser().resolve()
    product_root = product_root.expanduser().resolve()
    try:
        return path.relative_to(product_root).as_posix()
    except ValueError as exc:
        raise AuthoringError(
            f"artifact is outside its design/product authoring root: "
            f"artifact={path}; product_root={product_root}"
        ) from exc


def _product_path(product_root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise AuthoringError(f"{label} must contain a product-relative path")
    relative = Path(value)
    if relative.is_absolute():
        raise AuthoringError(f"{label} must be product-relative, not absolute: {value}")
    product_root = product_root.expanduser().resolve()
    resolved = (product_root / relative).resolve()
    if not resolved.is_relative_to(product_root):
        raise AuthoringError(f"{label} escapes its product root: {value}")
    return resolved


def _review_root(review: Path) -> tuple[Path, str, str]:
    review = review.expanduser().resolve()
    if review.parent.parent.name != "reviews":
        raise AuthoringError(
            "review must be reviews/<kind>/<review-id>: " + str(review)
        )
    kind = review.parent.name
    review_id = review.name
    if kind not in {*KINDS, "assembly"}:
        raise AuthoringError(f"unsupported review kind in path: {review}")
    _id(review_id, "review ID")
    return review.parent.parent.parent, kind, review_id


def _copy(source: Path, target: Path) -> dict[str, str]:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise AuthoringError(f"input does not exist: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return {"path": target.relative_to(target.parents[1]).as_posix(), "sha256": sha256(target)}


def _copy_font_catalog(source: Path, target: Path, experiment_stage: Path) -> dict[str, Any]:
    """Snapshot a renderable OFL catalog into one immutable layout experiment."""
    source = source.expanduser().resolve()
    try:
        candidates = discover_font_catalog(None, catalog_roots=(source,))
    except FontCatalogError as exc:
        raise AuthoringError(str(exc)) from exc
    target.mkdir(parents=True)
    files: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, 1):
        family = target / f"family-{index:03d}"
        family.mkdir()
        font = family / candidate.font.name
        license_file = family / "OFL.txt"
        shutil.copyfile(candidate.font, font)
        shutil.copyfile(candidate.license, license_file)
        copied = [font, license_file]
        source_metadata = candidate.font.parent / "source.json"
        family_metadata = candidate.font.parent / "METADATA.pb"
        if source_metadata.exists() != family_metadata.exists():
            raise AuthoringError(
                f"font provenance requires both source.json and METADATA.pb: "
                f"{candidate.font.parent}"
            )
        if source_metadata.is_file() and family_metadata.is_file():
            source_record = _json(source_metadata)
            expected_source = {
                "font_filename": candidate.font.name,
                "font_sha256": candidate.sha256,
                "license_sha256": sha256(candidate.license),
                "metadata_sha256": sha256(family_metadata),
            }
            actual_source = {
                key: source_record.get(key) for key in expected_source
            }
            if actual_source != expected_source:
                raise AuthoringError(
                    mismatch(
                        f"font provenance for {candidate.font}",
                        expected=expected_source,
                        actual=actual_source,
                    )
                )
            copied_source = family / "source.json"
            copied_metadata = family / "METADATA.pb"
            shutil.copyfile(source_metadata, copied_source)
            shutil.copyfile(family_metadata, copied_metadata)
            copied.extend((copied_source, copied_metadata))
        files.extend(
            {
                "path": path.relative_to(experiment_stage).as_posix(),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in copied
        )
    manifest = target / "catalog.snapshot.json"
    atomic_json(manifest, {"schema_version": 1, "fonts": files})
    return {
        "path": target.relative_to(experiment_stage).as_posix(),
        "manifest_sha256": sha256(manifest),
        "font_count": len(candidates),
    }


def _attempt_experiment(
    path: Path,
    record: dict[str, Any] | None = None,
    *,
    expected_kind: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    path = path.expanduser().resolve()
    if path.parent.name != "attempts":
        raise AuthoringError(
            f"attempt must be stored under <experiment>/attempts/: {path}"
        )
    experiment = path.parent.parent
    metadata = _json(experiment / "experiment.json")
    value = record if record is not None else _json(path / "run.json")
    actual = {
        "attempt_id": value.get("attempt_id"),
        "experiment_id": value.get("experiment_id"),
        "kind": value.get("kind"),
    }
    expected = {
        "attempt_id": path.name,
        "experiment_id": experiment.name,
        "kind": metadata.get("kind"),
    }
    if actual != expected:
        raise AuthoringError(
            mismatch(f"attempt identity ({path})", expected=expected, actual=actual)
        )
    if expected_kind is not None and actual["kind"] != expected_kind:
        raise AuthoringError(
            mismatch(
                f"attempt kind ({path})",
                expected=expected_kind,
                actual=actual["kind"],
            )
        )
    return experiment, metadata


def _attempt_record(
    path: Path, *, require_success: bool = True, expected_kind: str | None = None
) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if path.name.endswith(".partial"):
        raise AuthoringError(f"partial attempt is not selectable: {path}")
    value = _json(path / "run.json")
    _attempt_experiment(path, value, expected_kind=expected_kind)
    if require_success and value.get("status") != "succeeded":
        raise AuthoringError(f"attempt is not succeeded: {path}")
    return value


def _require_matching_layout_art(
    art_attempt: Path,
    layout_attempt: Path,
    *,
    label: str = "layout art",
) -> Path:
    """Verify that a layout was authored against the selected art bytes."""
    art = art_attempt / "outputs" / "art.png"
    layout_art = layout_attempt / "outputs" / "art.png"
    art_hash = sha256(art)
    layout_art_hash = sha256(layout_art)
    if layout_art_hash != art_hash:
        raise AuthoringError(
            mismatch(
                label,
                expected={"path": str(art), "sha256": art_hash},
                actual={"path": str(layout_art), "sha256": layout_art_hash},
            )
        )
    return art


def _image_gates(
    path: Path,
    expected_size: tuple[int, int],
    *,
    allow_fully_transparent: bool = False,
) -> dict[str, Any]:
    gates = {"readable_png": False, "expected_dimensions": False, "usable_alpha": False}
    try:
        with Image.open(path) as image:
            image.load()
            gates["readable_png"] = image.format == "PNG"
            gates["expected_dimensions"] = image.size == expected_size
            rgba = image.convert("RGBA")
            low, high = rgba.getchannel("A").getextrema()
            gates["usable_alpha"] = low < 255 and (
                allow_fully_transparent or high > 0
            )
    except OSError:
        pass
    gates["status"] = "passed" if all(gates.values()) else "failed"
    return gates


def create_experiment(
    *, kind: str, experiment_id: str, design_id: str, product_profile: Path,
    authoring_root: Path, references: list[Path], prompt_file: Path | None,
    provider: str | None, model: str | None, quality: str,
    art_attempt: Path | None, pet_attempt: Path | None, font_catalogs: list[Path],
    parent_experiment_id: str | None, base_bundle_revision: int | None,
    created_by: str, font_reference: Path | None = None,
    layout_reference: Path | None = None,
    pet_name: str | None = None,
) -> Path:
    if kind not in KINDS:
        raise AuthoringError(f"kind must be one of: {', '.join(sorted(KINDS))}")
    _id(experiment_id, "experiment ID")
    profile = load_product_profile(product_profile)
    catalog_template_id(design_id, profile.profile_id)
    root = authoring_root.expanduser().resolve() / design_id / profile.profile_id
    product_record = root / "product.json"
    expected_product = {
        "schema_version": 1,
        "design_id": design_id,
        "product_profile_id": profile.profile_id,
    }
    if product_record.is_file():
        actual_product = _json(product_record)
        if any(actual_product.get(key) != value for key, value in expected_product.items()):
            raise AuthoringError(
                mismatch(
                    f"authoring product identity ({product_record})",
                    expected=expected_product,
                    actual=actual_product,
                )
            )
    else:
        atomic_json(product_record, {**expected_product, "created_at": utc_now()})
    destination = root / "experiments" / kind / experiment_id
    partial = destination.with_name(destination.name + ".partial")
    if destination.exists() or partial.exists():
        raise AuthoringError(f"experiment already exists or is partial: {destination}")
    partial.mkdir(parents=True)
    try:
        inputs = partial / "inputs"
        inputs.mkdir()
        profile_info = _copy(product_profile, inputs / "product-profile.json")
        record_inputs: dict[str, Any] = {"product_profile": profile_info}
        generation: dict[str, Any] | None = None
        if pet_name is not None and kind != "pet":
            raise AuthoringError("--pet-name is only valid for a pet experiment")
        if pet_name is not None:
            pet_name = pet_name.strip()
            if not pet_name:
                raise AuthoringError("--pet-name must not be empty")
        if kind in {"art", "pet"}:
            if not prompt_file or not provider or not model:
                raise AuthoringError(
                    f"{kind} experiment requires prompt, provider, and model"
                )
            if kind == "pet" and len(references) > 4:
                raise AuthoringError(
                    "pet experiments accept at most four ordered reference designs "
                    "(one primary and up to three supporting references)"
                )
            if provider not in {"openai", "gemini"}:
                raise AuthoringError("provider must be openai or gemini")
            if provider == "openai" and not model.startswith("gpt-image-"):
                raise AuthoringError("OpenAI experiments require a gpt-image-* model")
            if provider == "gemini" and not model.startswith("gemini-"):
                raise AuthoringError("Gemini experiments require a gemini-* model")
            if quality not in GENERATION_QUALITIES:
                raise AuthoringError(
                    "quality must be low, medium, high, or auto"
                )
            prompt_info = _copy(prompt_file, inputs / prompt_file.name)
            reference_info = []
            for index, reference in enumerate(references, 1):
                suffix = reference.suffix.lower() or ".png"
                target = inputs / f"reference-design-{index:04d}{suffix}"
                copied = _copy(reference, target)
                copied["role"] = "finished_design"
                reference_info.append(copied)
            record_inputs.update(prompt=prompt_info, references=reference_info)
            input_mode = (
                "reference-guided"
                if reference_info
                else ("prompt-only" if kind == "art" else "pet-and-prompt")
            )
            generation = {
                "provider": provider, "model": model,
                "transport": (
                    "images.generations"
                    if provider == "openai" and kind == "art" and not reference_info
                    else "images.edits" if provider == "openai" else "interactions"
                ),
                "input_mode": input_mode,
                "parameters": {
                    "quality": quality,
                    "output_format": "png",
                    "background": "transparent",
                },
            }
            if kind == "pet" and pet_name is not None:
                if PET_NAME_PLACEHOLDER in prompt_file.read_text(encoding="utf-8"):
                    try:
                        pet_name = validate_pet_name(pet_name, pet_name_policy(64))
                    except PersonalizationError as exc:
                        raise AuthoringError(f"invalid --pet-name: {exc}") from exc
                generation["prompt_variables"] = {"pet_name": pet_name}
        else:
            if not art_attempt or not pet_attempt:
                raise AuthoringError("layout experiment requires --art-attempt and --pet-attempt")
            art_run = _attempt_record(art_attempt.expanduser().resolve())
            pet_run = _attempt_record(pet_attempt.expanduser().resolve())
            expected_identity = (design_id, profile.profile_id)
            for label, run, attempt in (
                ("art", art_run, art_attempt),
                ("pet", pet_run, pet_attempt),
            ):
                _, metadata = _attempt_experiment(
                    attempt, run, expected_kind=label
                )
                actual_identity = (
                    metadata.get("design_id"),
                    metadata.get("product_profile_id"),
                )
                if actual_identity != expected_identity:
                    raise AuthoringError(
                        f"{label} attempt belongs to {actual_identity[0]}/{actual_identity[1]}, "
                        f"not {design_id}/{profile.profile_id}"
                    )
            art_source = art_attempt.expanduser().resolve() / "outputs" / "art.png"
            pet_source = pet_attempt.expanduser().resolve() / "outputs" / "transformed-pet.png"
            record_inputs.update(
                art=_copy(art_source, inputs / "art.png"),
                representative_pet=_copy(pet_source, inputs / "transformed-pet.png"),
                art_attempt={
                    "path": _product_relative(art_attempt, root),
                    "sha256": sha256(art_attempt / "run.json"),
                },
                pet_attempt={
                    "path": _product_relative(pet_attempt, root),
                    "sha256": sha256(pet_attempt / "run.json"),
                },
            )
            art_experiment, _ = _attempt_experiment(
                art_attempt, art_run, expected_kind="art"
            )
            refs = sorted((art_experiment / "inputs").glob("reference-design-*"))
            if refs:
                record_inputs["reference"] = _copy(
                    refs[0], inputs / "reference-design.png"
                )
                record_inputs["reference_mode"] = "finished-design"
            else:
                record_inputs["reference_mode"] = "art-template"
                if font_reference is not None or layout_reference is not None:
                    raise AuthoringError(
                        "--font-reference and --layout-reference require a finished-design "
                        "reference in the selected art experiment"
                    )
            if font_reference is not None:
                try:
                    load_font_reference(font_reference, refs[0])
                except FontReferenceError as exc:
                    raise AuthoringError(str(exc)) from exc
                record_inputs["font_reference"] = _copy(
                    font_reference, inputs / "font-reference.json"
                )
            if layout_reference is not None:
                try:
                    loaded_layout_reference = load_layout_reference(
                        layout_reference, refs[0]
                    )
                except LayoutReferenceError as exc:
                    raise AuthoringError(str(exc)) from exc
                if font_reference is not None:
                    loaded_font_reference = load_font_reference(
                        font_reference, refs[0]
                    )
                    if (
                        loaded_layout_reference.name_region.to_dict()
                        != loaded_font_reference.region.to_dict()
                    ):
                        raise AuthoringError(
                            "layout reference name_region must match font reference region"
                        )
                record_inputs["layout_reference"] = _copy(
                    layout_reference, inputs / "layout-reference.json"
                )
            copied_catalogs = []
            for index, catalog in enumerate(font_catalogs, 1):
                copied_catalogs.append(
                    _copy_font_catalog(
                        catalog,
                        inputs / f"font-catalog-{index:02d}",
                        partial,
                    )
                )
            record_inputs["font_catalogs"] = copied_catalogs
        record = {
            "schema_version": 1, "experiment_id": experiment_id, "kind": kind,
            "design_id": design_id, "product_profile_id": profile.profile_id,
            "base_bundle_revision": base_bundle_revision,
            "parent_experiment_id": parent_experiment_id, "status": "draft",
            "inputs": record_inputs, "generation": generation,
            "created_by": created_by, "created_at": utc_now(),
            "generator": {"version": __version__},
        }
        atomic_json(partial / "experiment.json", record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, destination)
        return destination
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        raise


def _relative_input(experiment: Path, descriptor: dict[str, Any]) -> Path:
    return experiment / str(descriptor["path"])


def _run_generation(
    experiment: Path,
    meta: dict[str, Any],
    stage: Path,
    pet_image: Path | None,
    pet_name: str | None,
) -> None:
    kind = str(meta["kind"])
    inputs = meta["inputs"]
    generation = meta["generation"]
    output_name = "art.png" if kind == "art" else "transformed-pet.png"
    args = [
        "--provider", str(generation["provider"]), "--model", str(generation["model"]),
        "--prompt-file", str(_relative_input(experiment, inputs["prompt"])),
        "--output-dir", str(stage / "outputs"), "--output-name", output_name,
        "--product-profile", str(_relative_input(experiment, inputs["product_profile"])),
        "--profile-layer", kind if kind == "art" else "transformed-pet",
        "--background", "transparent", "--output-format", "png",
        "--quality", str(generation["parameters"]["quality"]),
    ]
    if kind == "pet":
        if pet_image is None:
            raise AuthoringError("pet attempt requires --pet-image")
        pet_copy = stage / "inputs" / "input-pet.png"
        pet_copy.parent.mkdir(parents=True)
        shutil.copyfile(pet_image.expanduser().resolve(), pet_copy)
        args.extend(["--pet-image", str(pet_copy)])
        if pet_name is not None:
            args.extend(["--pet-name", pet_name])
    for reference in inputs["references"]:
        args.extend(
            ["--reference-design", str(_relative_input(experiment, reference))]
        )
    parsed = build_generate_parser().parse_args(args)
    generate(parsed)


def _run_layout(
    experiment: Path,
    meta: dict[str, Any],
    stage: Path,
    pet_name: str | None,
    layout_file: Path | None,
    reference_text: str | None,
    name_mode_without_layer: str,
) -> dict[str, Any]:
    inputs = meta["inputs"]
    outputs = stage / "outputs"
    outputs.mkdir(parents=True)
    shutil.copyfile(_relative_input(experiment, inputs["art"]), outputs / "art.png")
    shutil.copyfile(_relative_input(experiment, inputs["representative_pet"]), outputs / "transformed-pet.png")
    reference = (
        _relative_input(experiment, inputs["reference"])
        if "reference" in inputs
        else outputs / "art.png"
    )
    font_reference = (
        _relative_input(experiment, inputs["font_reference"])
        if "font_reference" in inputs
        else None
    )
    layout_reference = (
        _relative_input(experiment, inputs["layout_reference"])
        if "layout_reference" in inputs
        else None
    )
    if layout_file:
        source_root = layout_file.expanduser().resolve().parent
        value = _json(layout_file.expanduser().resolve())
        source_layout = parse_layout(value, source_root)
        value["art"] = "art.png"
        if source_layout.has_name and pet_name is None:
            value.pop("name")
        elif source_layout.has_name:
            assert source_layout.font_path is not None
            fonts = outputs / "fonts"
            fonts.mkdir()
            shutil.copyfile(
                source_layout.font_path, fonts / source_layout.font_path.name
            )
            shutil.copyfile(
                resolve_ofl_license(source_layout.font_path), fonts / "OFL.txt"
            )
            source_metadata = source_layout.font_path.parent / "source.json"
            family_metadata = source_layout.font_path.parent / "METADATA.pb"
            if source_metadata.exists() != family_metadata.exists():
                raise AuthoringError(
                    "imported layout font requires both fonts/source.json and "
                    "fonts/METADATA.pb"
                )
            if source_metadata.is_file() and family_metadata.is_file():
                shutil.copyfile(source_metadata, fonts / "source.json")
                shutil.copyfile(family_metadata, fonts / "METADATA.pb")
            value["name"]["font"] = f"fonts/{source_layout.font_path.name}"
        parse_layout(value, outputs)
        atomic_json(outputs / "layout.json", value)
        if font_reference is not None:
            target = outputs / "qa" / "font-reference.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(font_reference, target)
        if layout_reference is not None:
            target = outputs / "qa" / "layout-reference.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(layout_reference, target)
    else:
        catalogs = tuple(_relative_input(experiment, item) for item in inputs.get("font_catalogs", []))
        serve_layout_editor(EditorConfig(
            art=outputs / "art.png", reference=reference,
            pet=outputs / "transformed-pet.png", pet_name=pet_name or "PET",
            name_enabled=pet_name is not None,
            name_mode_without_layer=name_mode_without_layer,
            font=None, output=outputs / "layout.json", font_catalogs=catalogs,
            font_reference=font_reference, layout_reference=layout_reference,
            reference_text=reference_text,
            auto_font=True,
        ))
        fixture = _json(outputs / "qa" / "calibration-fixture.json")
        saved_layout = load_layout(outputs)
        saved_pet_name = fixture.get("pet_name")
        pet_name = str(saved_pet_name or "").strip()
        if saved_layout.has_name and not pet_name:
            raise AuthoringError("layout editor did not save its QA pet name")
    render_to_files(template_dir=outputs, pet_image=outputs / "transformed-pet.png", pet_name=pet_name,
                    output=outputs / "preview.png", debug_output=outputs / "preview-debug.png")
    if layout_file:
        composition = render_composition(
            load_layout(outputs), outputs / "transformed-pet.png", pet_name
        )
        fixture = {
            "schema_version": 1,
            "pet_name": pet_name if composition.text is not None else None,
            "name_mode": (
                "layout-text" if composition.text else name_mode_without_layer
            ),
            "layout_sha256": sha256(outputs / "layout.json"),
            "transformed_pet_sha256": sha256(
                outputs / "transformed-pet.png"
            ),
        }
        if composition.text is not None:
            fixture["applied_font_size_px"] = composition.text.applied_font_size_px
            fixture["text_fit"] = composition.text.fit
        atomic_json(outputs / "qa" / "calibration-fixture.json", fixture)
    saved_font_reference = outputs / "qa" / "font-reference.json"
    if saved_font_reference.is_file():
        fixture["font_reference_sha256"] = sha256(saved_font_reference)
    saved_layout_reference = outputs / "qa" / "layout-reference.json"
    if saved_layout_reference.is_file():
        fixture["layout_reference_sha256"] = sha256(saved_layout_reference)
    recommendation = outputs / "qa" / "font-recommendation.json"
    if recommendation.is_file():
        recommendation_value = _json(recommendation)
        selected_id = recommendation_value.get("selected_font_id")
        selected = next(
            (
                option
                for option in recommendation_value.get("ranked_options", [])
                if isinstance(option, dict) and option.get("font_id") == selected_id
            ),
            None,
        )
        if selected is not None:
            fixture["font_match"] = {
                "similarity_score": selected.get("similarity_score"),
                "confidence_score": selected.get("confidence_score"),
                "confidence_level": selected.get("confidence_level"),
            }
    return fixture


def run_attempt(*, experiment: Path, attempt_id: str, pet_image: Path | None,
                pet_name: str | None = None, layout_file: Path | None = None,
                reference_text: str | None = None,
                no_pet_name: bool = False) -> Path:
    _id(attempt_id, "attempt ID")
    experiment = experiment.expanduser().resolve()
    meta = _json(experiment / "experiment.json")
    if no_pet_name and meta.get("kind") != "layout":
        raise AuthoringError("--no-pet-name is supported only for layout attempts")
    if meta.get("kind") == "pet" and pet_name is None:
        generation = meta.get("generation")
        prompt_variables = (
            generation.get("prompt_variables", {})
            if isinstance(generation, dict)
            else {}
        )
        inherited_pet_name = prompt_variables.get("pet_name")
        if isinstance(inherited_pet_name, str):
            pet_name = inherited_pet_name
    if meta.get("status") == "discarded":
        raise AuthoringError("cannot run a discarded experiment")
    resolved_generation = deepcopy(meta.get("generation"))
    if meta.get("kind") == "pet" and isinstance(resolved_generation, dict):
        if pet_name is None:
            resolved_generation.pop("prompt_variables", None)
        else:
            resolved_generation["prompt_variables"] = {"pet_name": pet_name}
    final = experiment / "attempts" / attempt_id
    partial = final.with_name(final.name + ".partial")
    if final.exists() or partial.exists():
        raise AuthoringError(f"attempt ID already exists or is partial: {attempt_id}")
    started = time.monotonic()
    record: dict[str, Any] = {
        "schema_version": 1, "attempt_id": attempt_id, "experiment_id": meta["experiment_id"],
        "kind": meta["kind"], "status": "running", "started_at": utc_now(),
        "experiment_sha256": sha256(experiment / "experiment.json"),
        "resolved_generation": resolved_generation,
        "input_hashes": {
            key: value.get("sha256")
            for key, value in meta.get("inputs", {}).items()
            if isinstance(value, dict) and isinstance(value.get("sha256"), str)
        },
        "ordered_reference_hashes": [
            value["sha256"] for value in meta.get("inputs", {}).get("references", [])
            if isinstance(value, dict) and isinstance(value.get("sha256"), str)
        ],
        "generator": {"version": __version__},
    }
    if pet_image is not None:
        record["input_pet_sha256"] = sha256(pet_image.expanduser().resolve())
    prompt_descriptor = meta.get("inputs", {}).get("prompt", {})
    prompt_path = (
        _relative_input(experiment, prompt_descriptor)
        if isinstance(prompt_descriptor, dict) and "path" in prompt_descriptor
        else None
    )
    if (
        meta["kind"] == "pet"
        and prompt_path is not None
        and PET_NAME_PLACEHOLDER in prompt_path.read_text(encoding="utf-8")
        and pet_name is not None
    ):
        try:
            pet_name = validate_pet_name(pet_name, pet_name_policy(64))
        except PersonalizationError as exc:
            raise AuthoringError(f"invalid --pet-name: {exc}") from exc
        record["prompt_variables"] = {"pet_name": pet_name}
    if meta["kind"] == "layout":
        layout_pet_name = (
            None if no_pet_name else (pet_name or "").strip() or "PET"
        )
        product_root = experiment.parents[2]
        pet_attempt_descriptor = meta.get("inputs", {}).get("pet_attempt", {})
        pet_attempt_path = (
            product_root / str(pet_attempt_descriptor.get("path", ""))
        ).resolve()
        if not pet_attempt_path.is_relative_to(product_root):
            raise AuthoringError(
                "layout representative pet attempt escapes its authoring product root"
            )
        representative_pet_record = _attempt_record(pet_attempt_path)
        representative_prompt_variables = representative_pet_record.get(
            "prompt_variables"
        )
        embedded_name = (
            representative_prompt_variables.get("pet_name")
            if isinstance(representative_prompt_variables, dict)
            else None
        )
        name_mode_without_layer = (
            "embedded-in-pet"
            if isinstance(embedded_name, str) and embedded_name.strip()
            else "none"
        )
        representative_pet = meta.get("inputs", {}).get("representative_pet", {})
        record["layout_fixture"] = {
            "pet_name": layout_pet_name,
            "representative_pet_sha256": representative_pet.get("sha256"),
        }
    partial.mkdir(parents=True)
    try:
        atomic_json(partial / "run.json", record)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    try:
        if meta["kind"] in {"art", "pet"}:
            _run_generation(experiment, meta, partial, pet_image, pet_name)
        else:
            saved_fixture = _run_layout(
                experiment,
                meta,
                partial,
                layout_pet_name,
                layout_file,
                reference_text,
                name_mode_without_layer,
            )
            record["layout_fixture"].update(
                pet_name=saved_fixture["pet_name"],
                name_mode=saved_fixture.get("name_mode", "layout-text"),
                layout_sha256=saved_fixture["layout_sha256"],
            )
            if "applied_font_size_px" in saved_fixture:
                record["layout_fixture"]["applied_font_size_px"] = saved_fixture[
                    "applied_font_size_px"
                ]
            if "text_fit" in saved_fixture:
                record["layout_fixture"]["text_fit"] = saved_fixture["text_fit"]
            if "transformed_pet" in saved_fixture:
                record["layout_fixture"]["calibration_pet"] = saved_fixture[
                    "transformed_pet"
                ]
            if "tested_transformed_pets" in saved_fixture:
                record["layout_fixture"]["tested_transformed_pets"] = (
                    saved_fixture["tested_transformed_pets"]
                )
            if "font_reference_sha256" in saved_fixture:
                record["layout_fixture"]["font_reference_sha256"] = (
                    saved_fixture["font_reference_sha256"]
                )
            if "font_match" in saved_fixture:
                record["layout_fixture"]["font_match"] = saved_fixture[
                    "font_match"
                ]
        record["status"] = "succeeded"
    except Exception as exc:
        record.update(status="failed", error={"category": type(exc).__name__, "message": str(exc)[:1000]})
    record.update(completed_at=utc_now(), duration_seconds=round(time.monotonic() - started, 3))
    record["outputs"] = [
        {"path": path.relative_to(partial).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size}
        for path in sorted((partial / "outputs").rglob("*")) if path.is_file()
    ] if (partial / "outputs").exists() else []
    atomic_json(partial / "run.json", record)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(partial, final)
    if record["status"] != "succeeded":
        raise AuthoringError(f"attempt failed; immutable record saved at {final}: {record['error']['message']}")
    return final


def benchmark(*, experiment: Path, fixture_set: Path, evaluation_protocol: Path,
              attempts_per_fixture: int | None, attempt_id_prefix: str,
              fixture_selection: Path, pet_name: str | None = None) -> list[Path]:
    experiment = experiment.expanduser().resolve()
    experiment_meta = _json(experiment / "experiment.json")
    if experiment_meta.get("kind") != "pet":
        raise AuthoringError(
            "fixture benchmarks require a pet experiment; "
            f"experiment={experiment}; actual_kind={experiment_meta.get('kind')!r}"
        )
    if experiment_meta.get("status") == "discarded":
        raise AuthoringError(f"cannot benchmark a discarded experiment: {experiment}")
    _id(attempt_id_prefix, "attempt ID prefix")
    try:
        fixture_inventory = load_fixture_set(fixture_set)
    except FixtureSetError as exc:
        raise AuthoringError(str(exc)) from exc
    repetitions = (
        fixture_inventory.attempts_per_fixture
        if attempts_per_fixture is None
        else attempts_per_fixture
    )
    if repetitions != fixture_inventory.attempts_per_fixture:
        raise AuthoringError(
            "attempt count does not match the immutable fixture-set protocol; "
            f"fixture_set={fixture_inventory.fixture_set_id}; "
            f"expected={fixture_inventory.attempts_per_fixture}; actual={repetitions}"
        )
    try:
        selected_fixtures = load_fixture_selection(
            fixture_inventory, fixture_selection
        )
    except FixtureSetError as exc:
        raise AuthoringError(str(exc)) from exc
    _json(evaluation_protocol.expanduser().resolve())
    results: list[Path] = []
    failures: list[tuple[str, str]] = []
    for fixture in selected_fixtures:
        for repetition in range(1, repetitions + 1):
            attempt_id = f"{attempt_id_prefix}-{fixture.id}-{repetition:04d}"
            try:
                results.append(
                    run_attempt(
                        experiment=experiment,
                        attempt_id=attempt_id,
                        pet_image=fixture.image,
                        pet_name=pet_name,
                    )
                )
            except AuthoringError as exc:
                failures.append((attempt_id, str(exc)))
    if failures:
        details = "; ".join(
            f"{attempt_id}: {message}" for attempt_id, message in failures
        )
        print(
            "WARNING: benchmark completed with incomplete fixture coverage; "
            f"experiment={experiment}; succeeded={len(results)}; "
            f"failed={len(failures)}; failures=[{details}]. "
            "The successful attempts remain usable for comparison; review and "
            "explicitly accept the coverage gap in the pet decision if graduating.",
            file=sys.stderr,
            flush=True,
        )
    return results


def _fixture_group_coverage(
    fixtures: tuple[PetFixture, ...], passing_hashes: set[str]
) -> dict[str, list[dict[str, Any]]]:
    dimensions = {
        "species": lambda fixture: (fixture.species,),
        "size_class": lambda fixture: (fixture.size_class,),
        "morphology": lambda fixture: fixture.morphology,
        "risk_tag": lambda fixture: fixture.risk_tags,
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for dimension, labels in dimensions.items():
        members: dict[str, list[PetFixture]] = {}
        for fixture in fixtures:
            for label in labels(fixture):
                members.setdefault(label, []).append(fixture)
        groups[dimension] = [
            {
                "value": label,
                "expected_fixtures": len(group),
                "covered_fixtures": sum(
                    fixture.image_sha256 in passing_hashes for fixture in group
                ),
                "status": (
                    "passed"
                    if all(fixture.image_sha256 in passing_hashes for fixture in group)
                    else "warning"
                ),
            }
            for label, group in sorted(members.items())
        ]
    return groups


def _attempt_measurements(runs: list[dict[str, Any]]) -> dict[str, Any]:
    succeeded = [run for run in runs if run.get("status") == "succeeded"]
    passed = [
        run for run in succeeded if run.get("hard_gates", {}).get("status") == "passed"
    ]
    durations = [
        float(run["duration_seconds"])
        for run in succeeded
        if isinstance(run.get("duration_seconds"), (int, float))
    ]
    result: dict[str, Any] = {
        "attempts": len(runs),
        "successful_calls": len(succeeded),
        "hard_gate_passes": len(passed),
        "success_rate": len(succeeded) / len(runs) if runs else None,
        "hard_gate_pass_rate": len(passed) / len(runs) if runs else None,
        "minimum_seconds": min(durations) if durations else None,
        "maximum_seconds": max(durations) if durations else None,
        "median_seconds": statistics.median(durations) if durations else None,
    }
    if len(durations) >= 20:
        result["p95_seconds"] = statistics.quantiles(
            durations, n=100, method="inclusive"
        )[94]
    return result


def compare(*, kind: str, review_id: str, authoring_product: Path,
            experiments: list[str], evaluation_protocol: Path, fixture_set: Path | None,
            art_attempt: Path | None, pet_experiment: Path | None,
            layout_attempt: Path | None, base_bundle_revision: int | None,
            attempt_prefix: str | None = None,
            fixture_selection: Path | None = None) -> Path:
    if kind not in {*KINDS, "assembly"}:
        raise AuthoringError("comparison kind must be art, pet, layout, or assembly")
    _id(review_id, "review ID")
    authoring_product = authoring_product.expanduser().resolve()
    protocol = _json(evaluation_protocol.expanduser().resolve())
    review = authoring_product / "reviews" / kind / review_id
    output = review / "evaluation.json"
    if review.exists():
        raise AuthoringError(f"review already exists: {review}")
    if attempt_prefix is not None and not attempt_prefix:
        raise AuthoringError("attempt prefix must not be empty")
    if kind != "pet" and fixture_selection is not None:
        raise AuthoringError(
            "fixture selection is supported only for pet comparisons"
        )
    if kind == "pet" and fixture_set and fixture_selection is None:
        raise AuthoringError(
            "pet comparisons with --fixture-set require --fixture-selection"
        )
    if kind == "pet" and fixture_selection is not None and not fixture_set:
        raise AuthoringError(
            "pet --fixture-selection requires --fixture-set"
        )
    expected_fixture_hashes: set[str] = set()
    fixture_inventory = None
    selected_fixtures: tuple[PetFixture, ...] = ()
    fixtures_by_hash: dict[str, PetFixture] = {}
    if fixture_set:
        try:
            fixture_inventory = load_fixture_set(fixture_set)
        except FixtureSetError as exc:
            raise AuthoringError(str(exc)) from exc
    if kind == "pet" and fixture_inventory:
        try:
            selected_fixtures = load_fixture_selection(
                fixture_inventory, fixture_selection
            )
        except FixtureSetError as exc:
            raise AuthoringError(str(exc)) from exc
        fixtures_by_hash = {
            fixture.image_sha256: fixture for fixture in selected_fixtures
        }
        expected_fixture_hashes = set(fixtures_by_hash)
    composition_template = None
    composition_pet_name = None
    if kind == "pet" and (art_attempt or layout_attempt):
        if not art_attempt or not layout_attempt:
            raise AuthoringError(
                "pet composition comparison requires both --art-attempt and --layout-attempt"
            )
        art_attempt = art_attempt.expanduser().resolve()
        layout_attempt = layout_attempt.expanduser().resolve()
        _attempt_record(art_attempt)
        layout_record = _attempt_record(layout_attempt)
        composition_pet_name = str(
            layout_record.get("layout_fixture", {}).get("pet_name", "")
        ).strip() or None
        composition_layout = load_layout(layout_attempt / "outputs")
        if composition_layout.has_name and not composition_pet_name:
            raise AuthoringError(
                "layout attempt does not record its fixture pet name; create a new layout attempt"
            )
        _require_matching_layout_art(art_attempt, layout_attempt)
        composition_template = layout_attempt / "outputs"
    candidates: list[dict[str, Any]] = []
    coverage_warnings: list[str] = []
    selection_warnings = list(
        fixture_selection_warnings(fixture_inventory, len(selected_fixtures))
        if fixture_inventory and selected_fixtures
        else ()
    )
    coverage_warnings.extend(selection_warnings)
    if kind in KINDS:
        if not experiments:
            raise AuthoringError(f"{kind} comparison requires at least one --experiment")
        if len(set(experiments)) != len(experiments):
            raise AuthoringError("comparison experiment IDs must be unique")
        for experiment_id in experiments:
            experiment = authoring_product / "experiments" / kind / experiment_id
            experiment_meta = _json(experiment / "experiment.json")
            profile = load_product_profile(_relative_input(experiment, experiment_meta["inputs"]["product_profile"]))
            expected_size = (
                (profile.preview_art_size.width, profile.preview_art_size.height)
                if kind == "art" else (profile.preview_pet_size.width, profile.preview_pet_size.height)
            )
            runs = []
            for attempt in sorted((experiment / "attempts").glob("*")):
                if attempt.name.endswith(".partial") or not attempt.is_dir():
                    continue
                if attempt_prefix and not attempt.name.startswith(attempt_prefix):
                    continue
                record = _attempt_record(attempt, require_success=False)
                if (
                    kind == "pet"
                    and expected_fixture_hashes
                    and record.get("input_pet_sha256") not in expected_fixture_hashes
                ):
                    continue
                gates: dict[str, Any]
                if kind in {"art", "pet"}:
                    filename = "art.png" if kind == "art" else "transformed-pet.png"
                    gates = _image_gates(
                        attempt / "outputs" / filename,
                        expected_size,
                        allow_fully_transparent=kind == "art",
                    )
                else:
                    try:
                        layout = load_layout(attempt / "outputs")
                        gates = {"layout_v2": layout.schema_version == 2,
                                 "preview_exists": (attempt / "outputs" / "preview.png").is_file()}
                        gates["status"] = "passed" if all(gates.values()) else "failed"
                        if layout.has_name:
                            assert layout.name_box is not None
                            review_label = (
                                f"{layout.font_relative} "
                                f"text={record.get('layout_fixture', {}).get('pet_name', '?')} "
                                f"font={layout.font_size_px}px "
                                f"applied={record.get('layout_fixture', {}).get('applied_font_size_px', '?')}px "
                                f"name-box={layout.name_box.width}x{layout.name_box.height}"
                            )
                        else:
                            review_label = "embedded artistic name; no layout text layer"
                    except (OSError, ValueError):
                        gates = {"layout_v2": False, "preview_exists": False, "status": "failed"}
                        review_label = "unreadable layout"
                input_pet_sha256 = record.get("input_pet_sha256")
                fixture_labels = (
                    fixtures_by_hash[input_pet_sha256].labels()
                    if input_pet_sha256 in fixtures_by_hash
                    else {}
                )
                runs.append({"attempt_id": attempt.name, "status": record.get("status"),
                             "duration_seconds": record.get("duration_seconds"),
                             "input_pet_sha256": input_pet_sha256,
                             "representative_pet_sha256": record.get(
                                 "layout_fixture", {}
                             ).get("representative_pet_sha256"),
                             "pet_name": record.get("layout_fixture", {}).get("pet_name"),
                             "review_label": review_label if kind == "layout" else None,
                             "hard_gates": gates,
                             **fixture_labels})
            succeeded = [run for run in runs if run["status"] == "succeeded" and run["hard_gates"]["status"] == "passed"]
            passing_fixture_hashes = {
                run["input_pet_sha256"]
                for run in runs
                if isinstance(run.get("input_pet_sha256"), str)
                and run.get("status") == "succeeded"
                and run.get("hard_gates", {}).get("status") == "passed"
            }
            fixture_coverage = None
            candidate_warnings = list(selection_warnings)
            if expected_fixture_hashes:
                missing_fixtures = [
                    fixture.id
                    for fixture in selected_fixtures
                    if fixture.image_sha256 not in passing_fixture_hashes
                ]
                fixture_coverage = {
                    "expected_fixtures": len(expected_fixture_hashes),
                    "covered_fixtures": len(expected_fixture_hashes & passing_fixture_hashes),
                    "coverage_rate": (
                        len(expected_fixture_hashes & passing_fixture_hashes)
                        / len(expected_fixture_hashes)
                    ),
                    "missing_fixture_ids": missing_fixtures,
                    "status": (
                        "passed"
                        if expected_fixture_hashes <= passing_fixture_hashes
                        else "warning"
                    ),
                    "groups": _fixture_group_coverage(
                        selected_fixtures, passing_fixture_hashes
                    ),
                }
                if missing_fixtures:
                    coverage_warning = (
                        "incomplete fixture coverage for pet experiment "
                        f"{experiment_id!r}: covered "
                        f"{fixture_coverage['covered_fixtures']}/"
                        f"{fixture_coverage['expected_fixtures']}; "
                        f"missing_fixture_ids={missing_fixtures}. This is review "
                        "evidence, not a machine graduation gate; the application "
                        "owner must explicitly accept the gap in the decision notes."
                    )
                    candidate_warnings.append(coverage_warning)
                    coverage_warnings.append(coverage_warning)
            candidates.append({"experiment_id": experiment_id,
                               "configuration": experiment_meta.get("generation"),
                               "attempts": runs,
                               "measurements": _attempt_measurements(runs),
                               "fixture_coverage": fixture_coverage,
                               "warnings": candidate_warnings,
                               "hard_gates_passed": bool(succeeded)})
    elif kind == "assembly":
        if not art_attempt or not pet_experiment or not layout_attempt:
            raise AuthoringError("assembly comparison currently requires art, pet, and layout inputs")
        _attempt_record(art_attempt.expanduser().resolve())
        layout_record = _attempt_record(layout_attempt.expanduser().resolve())
        assembly_pet_name = str(
            layout_record.get("layout_fixture", {}).get("pet_name", "")
        ).strip()
        assembly_layout = load_layout(layout_attempt / "outputs")
        if assembly_layout.has_name and not assembly_pet_name:
            raise AuthoringError(
                "layout attempt does not record its fixture pet name; create a new layout attempt"
            )
        succeeded_pets = [p for p in sorted((pet_experiment.expanduser().resolve() / "attempts").glob("*")) if p.is_dir() and not p.name.endswith(".partial") and _attempt_record(p, require_success=False).get("status") == "succeeded"]
        if not succeeded_pets:
            raise AuthoringError("pet experiment has no succeeded attempts")
        _require_matching_layout_art(
            art_attempt.expanduser().resolve(),
            layout_attempt.expanduser().resolve(),
        )
        artifacts = review / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=False)
        pet = succeeded_pets[0] / "outputs" / "transformed-pet.png"
        try:
            render_to_files(template_dir=layout_attempt / "outputs", pet_image=pet,
                            pet_name=assembly_pet_name,
                            output=artifacts / "preview.png", debug_output=artifacts / "preview-debug.png")
        except Exception:
            shutil.rmtree(review, ignore_errors=True)
            raise
        candidates.append({
                           "art_attempt": _product_relative(art_attempt, authoring_product),
                           "pet_experiment": _product_relative(pet_experiment, authoring_product),
                           "layout_attempt": _product_relative(layout_attempt, authoring_product),
                           "pet_name": assembly_pet_name,
                           "hard_gates_passed": True})
    else:
        raise AuthoringError("comparison kind must be art, pet, layout, or assembly")
    component_runs = []
    for candidate in candidates:
        for run in candidate.get("attempts", []):
            component_runs.append(run)
    measurements = _attempt_measurements(component_runs)
    review_artifacts = []
    if kind in KINDS:
        contact_sheet = render_comparison_contact_sheet(
            kind=kind,
            authoring_product=authoring_product,
            review_id=review_id,
            candidates=candidates,
            composition_template=composition_template,
            composition_pet_name=composition_pet_name,
        )
        if contact_sheet:
            review_artifacts.append(contact_sheet)
    record = {
        "schema_version": 1,
        "review_id": review_id,
        "kind": kind,
        "design_id": authoring_product.parent.name,
        "product_profile_id": authoring_product.name,
        "evaluation_protocol_id": protocol.get("evaluation_protocol_id"),
        "evaluation_protocol_sha256": sha256(evaluation_protocol.expanduser().resolve()),
        "fixture_set_sha256": sha256(fixture_set.expanduser().resolve()) if fixture_set else None,
        "fixture_set_id": fixture_inventory.fixture_set_id if fixture_inventory else None,
        "fixture_tier": fixture_inventory.tier if fixture_inventory else None,
        "fixture_inventory": (
            {
                key: value
                for key, value in fixture_inventory.summary().items()
                if key != "fixture_set"
            }
            if fixture_inventory
            else None
        ),
        "fixture_selection": (
            {
                "config_sha256": sha256(fixture_selection.expanduser().resolve()),
                "selected_count": len(selected_fixtures),
                "selected_fixture_ids": [
                    fixture.id for fixture in selected_fixtures
                ],
            }
            if selected_fixtures
            else None
        ),
        "attempt_id_prefix": attempt_prefix,
        "review_mode": "composed-preview" if composition_template else "source-output",
        "base_bundle_revision": base_bundle_revision, "created_at": utc_now(),
        "candidates": candidates, "measurements": measurements,
        "review_artifacts": review_artifacts,
        "warnings": coverage_warnings,
        "hard_gates": {"status": "passed" if candidates and any(c["hard_gates_passed"] for c in candidates) else "failed"},
        "human_review": {
            "status": "pending",
            "notes": (
                "Application owner records approval in the decision file and "
                "explicitly accepts any evaluation warnings."
            ),
        },
    }
    try:
        atomic_json(output, record)
    except Exception:
        # A review is immutable once visible. Do not leave a half-written packet
        # that blocks a retry with the same review ID.
        shutil.rmtree(review, ignore_errors=True)
        raise
    return output


def record_decision(
    *,
    review: Path,
    selected_by: str,
    notes: str,
    selected_experiment: str | None,
    selected_attempt: str | None,
) -> Path:
    """Record one immutable human winner decision against exact evaluation bytes."""
    authoring_product, path_kind, review_id = _review_root(review)
    review = review.expanduser().resolve()
    evaluation = review / "evaluation.json"
    value = _json(evaluation)
    kind = value.get("kind")
    if kind not in {*KINDS, "assembly"} or kind != path_kind:
        raise AuthoringError(f"unsupported evaluation document: {evaluation}")
    expected_identity = {
        "design_id": authoring_product.parent.name,
        "product_profile_id": authoring_product.name,
        "review_id": review_id,
    }
    actual_identity = {
        "design_id": value.get("design_id"),
        "product_profile_id": value.get("product_profile_id"),
        "review_id": value.get("review_id"),
    }
    if actual_identity != expected_identity:
        raise AuthoringError(
            mismatch(
                "review evaluation identity",
                expected=expected_identity,
                actual=actual_identity,
            )
        )
    if value.get("hard_gates", {}).get("status") != "passed":
        raise AuthoringError(
            f"cannot approve evaluation with non-passing hard gates: {evaluation}"
        )
    reviewer = selected_by.strip()
    if not reviewer:
        raise AuthoringError("--selected-by must not be empty")
    accepted_warnings: list[str] = []

    if kind in KINDS:
        if not selected_experiment:
            raise AuthoringError(
                f"{kind} decision requires --selected-experiment"
            )
        candidate = next(
            (
                item
                for item in value.get("candidates", [])
                if isinstance(item, dict)
                and item.get("experiment_id") == selected_experiment
            ),
            None,
        )
        if not isinstance(candidate, dict) or candidate.get("hard_gates_passed") is not True:
            raise AuthoringError(
                f"selected {kind} experiment is absent or failed hard gates: "
                f"{selected_experiment!r}; evaluation={evaluation}"
            )
        accepted_warnings = [
            str(warning) for warning in candidate.get("warnings", [])
        ]
        selected: dict[str, Any] = {"experiment_id": selected_experiment}
        if kind in {"art", "layout"}:
            if not selected_attempt:
                raise AuthoringError(
                    f"{kind} decision requires --selected-attempt"
                )
            attempt = next(
                (
                    item
                    for item in candidate.get("attempts", [])
                    if isinstance(item, dict)
                    and item.get("attempt_id") == selected_attempt
                ),
                None,
            )
            if (
                not isinstance(attempt, dict)
                or attempt.get("status") != "succeeded"
                or attempt.get("hard_gates", {}).get("status") != "passed"
            ):
                raise AuthoringError(
                    f"selected {kind} attempt is absent, failed, or did not pass hard gates: "
                    f"{selected_attempt!r}; evaluation={evaluation}"
                )
            selected["attempt_id"] = selected_attempt
        elif selected_attempt is not None:
            raise AuthoringError(
                "pet decisions select the reusable experiment runtime; "
                "do not pass --selected-attempt"
            )
    else:
        if selected_experiment is not None or selected_attempt is not None:
            raise AuthoringError(
                "assembly decisions infer the single evaluated combination; "
                "do not pass --selected-experiment or --selected-attempt"
            )
        candidates = [
            item
            for item in value.get("candidates", [])
            if isinstance(item, dict) and item.get("hard_gates_passed") is True
        ]
        if len(candidates) != 1:
            raise AuthoringError(
                "assembly decision requires exactly one passing evaluated combination; "
                f"actual={len(candidates)}; evaluation={evaluation}"
            )
        selected = {
            field: candidates[0].get(field)
            for field in ("art_attempt", "pet_experiment", "layout_attempt")
        }

    if accepted_warnings and not notes.strip():
        raise AuthoringError(
            "selected candidate contains warnings; --notes must document the "
            "application owner's acceptance before graduation: "
            f"evaluation={evaluation}; warnings={accepted_warnings}"
        )

    output = review / "decision.json"
    if output.exists():
        raise AuthoringError(f"decision already exists: {output}")
    atomic_json(
        output,
        {
            "schema_version": 1,
            "review_id": review_id,
            "kind": kind,
            "design_id": authoring_product.parent.name,
            "product_profile_id": authoring_product.name,
            "approved": True,
            "evaluation": {
                "path": "evaluation.json",
                "evaluation_sha256": sha256(evaluation),
            },
            "selected": selected,
            "decision": {
                "selected_by": reviewer,
                "selected_at": utc_now(),
                "notes": notes.strip(),
                "accepted_warnings": accepted_warnings,
            },
        },
    )
    return output


def prepare_print_candidate(
    *, candidate_id: str, authoring_product: Path, art_attempt: Path | None,
    pet_attempt: Path | None, layout_attempt: Path | None,
    art_review: Path | None = None, pet_review: Path | None = None,
    layout_review: Path | None = None, pet_name: str | None = None, backend: str,
    reuse_template_from: Path | None = None,
) -> Path:
    """Create one immutable profile-sized finalist from exact component attempts."""
    _id(candidate_id, "print candidate ID")
    authoring_product = authoring_product.expanduser().resolve()
    decision_inputs = (art_review, pet_review, layout_review)
    attempt_inputs = (art_attempt, pet_attempt, layout_attempt)
    if any(decision_inputs) and any(attempt_inputs):
        raise AuthoringError(
            "prepare-print accepts either the three stage reviews or the three "
            "explicit attempts, not both"
        )
    if any(decision_inputs):
        if not all(decision_inputs):
            raise AuthoringError(
                "review-driven print requires --art-review, --pet-review, "
                "and --layout-review"
            )
        decision_values = {
            kind: _json(path.expanduser().resolve() / "decision.json")
            for kind, path in zip(
                ("art", "pet", "layout"),
                decision_inputs,
            )
            if path is not None
        }
        for kind, path in zip(("art", "pet", "layout"), decision_inputs):
            assert path is not None
            selected = decision_values[kind].get("selected", {})
            expected_selected = {
                key: str(selected.get(key))
                for key in (
                    ("experiment_id", "attempt_id")
                    if kind in {"art", "layout"}
                    else ("experiment_id",)
                )
            }
            _decision_evidence(
                path,
                product_root=authoring_product,
                kind=kind,
                expected_selected=expected_selected,
            )
        art_selected = decision_values["art"]["selected"]
        pet_selected = decision_values["pet"]["selected"]
        layout_selected = decision_values["layout"]["selected"]
        art_attempt = (
            authoring_product / "experiments" / "art"
            / art_selected["experiment_id"] / "attempts" / art_selected["attempt_id"]
        )
        pet_experiment = (
            authoring_product / "experiments" / "pet" / pet_selected["experiment_id"]
        )
        layout_attempt = (
            authoring_product / "experiments" / "layout"
            / layout_selected["experiment_id"] / "attempts" / layout_selected["attempt_id"]
        )
        layout_run_for_source = _attempt_record(layout_attempt)
        layout_experiment_for_source, layout_meta_for_source = _attempt_experiment(
            layout_attempt, layout_run_for_source, expected_kind="layout"
        )
        layout_inputs_for_source = layout_meta_for_source.get("inputs", {})
        art_attempt_descriptor = layout_inputs_for_source.get("art_attempt")
        pet_attempt_descriptor = layout_meta_for_source.get("inputs", {}).get("pet_attempt")
        if not isinstance(art_attempt_descriptor, dict):
            raise AuthoringError(
                f"selected layout experiment has no pinned art attempt: "
                f"{layout_experiment_for_source}"
            )
        if not isinstance(pet_attempt_descriptor, dict):
            raise AuthoringError(
                f"selected layout experiment has no representative pet attempt: "
                f"{layout_experiment_for_source}"
            )
        pinned_art_path = art_attempt_descriptor.get("path")
        pinned_pet_path = pet_attempt_descriptor.get("path")
        if not isinstance(pinned_art_path, str) or not pinned_art_path.strip():
            raise AuthoringError(
                f"selected layout experiment has an invalid pinned art path: "
                f"{layout_experiment_for_source}"
            )
        if not isinstance(pinned_pet_path, str) or not pinned_pet_path.strip():
            raise AuthoringError(
                f"selected layout experiment has an invalid representative pet path: "
                f"{layout_experiment_for_source}"
            )
        pinned_art_attempt = _product_path(
            authoring_product, pinned_art_path, "layout pinned art attempt"
        )
        pet_attempt = _product_path(
            authoring_product, pinned_pet_path, "layout representative pet attempt"
        )
        if pinned_art_attempt != art_attempt:
            raise AuthoringError(
                mismatch(
                    "selected layout pinned art attempt",
                    expected=str(art_attempt),
                    actual=str(pinned_art_attempt),
                )
            )
        if pet_attempt.parent.parent != pet_experiment:
            raise AuthoringError(
                mismatch(
                    "selected layout representative pet runtime",
                    expected=str(pet_experiment),
                    actual=str(pet_attempt.parent.parent),
                )
            )
        for label, descriptor, attempt in (
            ("art", art_attempt_descriptor, art_attempt),
            ("representative pet", pet_attempt_descriptor, pet_attempt),
        ):
            expected_hash = descriptor.get("sha256")
            actual_hash = sha256(attempt / "run.json")
            if expected_hash != actual_hash:
                raise AuthoringError(
                    mismatch(
                        f"selected layout pinned {label} run",
                        expected={
                            "path": str(attempt / "run.json"),
                            "sha256": expected_hash,
                        },
                        actual={
                            "path": str(attempt / "run.json"),
                            "sha256": actual_hash,
                        },
                    )
                )
    elif not all(attempt_inputs):
        raise AuthoringError(
            "explicit print preparation requires --art-attempt, --pet-attempt, "
            "and --layout-attempt"
        )
    assert art_attempt is not None and pet_attempt is not None and layout_attempt is not None
    art_attempt, pet_attempt, layout_attempt = (
        path.expanduser().resolve()
        for path in (art_attempt, pet_attempt, layout_attempt)
    )
    records = {
        "art": _attempt_record(art_attempt),
        "pet": _attempt_record(pet_attempt),
        "layout": _attempt_record(layout_attempt),
    }
    metadata = {
        label: _attempt_experiment(
            attempt,
            records[label],
            expected_kind=label,
        )[1]
        for label, attempt in (
            ("art", art_attempt),
            ("pet", pet_attempt),
            ("layout", layout_attempt),
        )
    }
    identities = {
        (value.get("design_id"), value.get("product_profile_id"))
        for value in metadata.values()
    }
    expected_identity = (authoring_product.parent.name, authoring_product.name)
    if identities != {expected_identity}:
        raise AuthoringError(
            mismatch(
                "print candidate design/product identity",
                expected=expected_identity,
                actual=sorted(identities, key=repr),
            )
        )
    art = _require_matching_layout_art(art_attempt, layout_attempt)
    profile_descriptor = metadata["layout"].get("inputs", {}).get("product_profile")
    if not isinstance(profile_descriptor, dict):
        raise AuthoringError("layout experiment is missing its product profile snapshot")
    layout_experiment, _ = _attempt_experiment(
        layout_attempt, records["layout"], expected_kind="layout"
    )
    profile_path = _relative_input(layout_experiment, profile_descriptor)
    profile = load_product_profile(profile_path)
    preview_layout = load_layout(layout_attempt / "outputs")
    layout_fixture_name = str(
        records["layout"].get("layout_fixture", {}).get("pet_name") or ""
    ).strip() or None
    layout_name_mode = records["layout"].get("layout_fixture", {}).get(
        "name_mode"
    )
    pet_prompt_variables = records["pet"].get("prompt_variables")
    pet_prompt_name = (
        pet_prompt_variables.get("pet_name", "").strip() or None
        if isinstance(pet_prompt_variables, dict)
        and isinstance(pet_prompt_variables.get("pet_name"), str)
        else None
    )
    supplied_pet_name = (pet_name or "").strip() or None
    if preview_layout.has_name:
        if layout_name_mode != "layout-text":
            raise AuthoringError(
                "selected layout has a name layer but its fixture does not declare "
                "name_mode=layout-text"
            )
        pet_name = supplied_pet_name or layout_fixture_name
    elif layout_name_mode == "embedded-in-pet":
        if not pet_prompt_name:
            raise AuthoringError(
                "embedded-name layout requires the selected representative pet "
                "attempt to record an applied {{PET_NAME}} prompt variable"
            )
        if supplied_pet_name and pet_prompt_name and supplied_pet_name != pet_prompt_name:
            raise AuthoringError(
                mismatch(
                    "embedded pet-name source",
                    expected=pet_prompt_name,
                    actual=supplied_pet_name,
                )
            )
        pet_name = pet_prompt_name
    elif layout_name_mode == "none":
        if supplied_pet_name:
            raise AuthoringError(
                "selected layout declares name_mode=none, so --pet-name has no "
                "layout or prompt consumer"
            )
        if pet_prompt_name:
            raise AuthoringError(
                "selected layout declares name_mode=none but the representative pet "
                "attempt records an applied {{PET_NAME}} value"
            )
        pet_name = None
    else:
        raise AuthoringError(
            "fontless layout must declare name_mode=embedded-in-pet or none; "
            f"actual={layout_name_mode!r}"
        )
    if layout_name_mode != "none" and not pet_name:
        source = (
            "layout fixture"
            if preview_layout.has_name
            else "representative pet attempt"
        )
        raise AuthoringError(
            "print candidate cannot infer the QA pet name from the selected "
            f"{source}; provide --pet-name"
        )

    reused_record: dict[str, Any] | None = None
    reused_candidate: Path | None = None
    template_backend = backend
    if reuse_template_from is not None:
        reused_candidate = reuse_template_from.expanduser().resolve()
        reused_record = _json(reused_candidate / "print-candidate.json")
        if reused_record.get("status") != "succeeded":
            raise AuthoringError("reused template must come from a succeeded print candidate")
        if (
            reused_record.get("design_id"),
            reused_record.get("product_profile_id"),
        ) != expected_identity:
            raise AuthoringError(
                mismatch(
                    "reused template design/product identity",
                    expected=expected_identity,
                    actual=(
                        reused_record.get("design_id"),
                        reused_record.get("product_profile_id"),
                    ),
                )
            )
        reused_sources = reused_record.get("sources", {})
        if (
            reused_sources.get("art_attempt"),
            reused_sources.get("layout_attempt"),
        ) != (
            _product_relative(art_attempt, authoring_product),
            _product_relative(layout_attempt, authoring_product),
        ):
            raise AuthoringError(
                mismatch(
                    "reused template art/layout sources",
                    expected={
                        "art_attempt": _product_relative(art_attempt, authoring_product),
                        "layout_attempt": _product_relative(layout_attempt, authoring_product),
                    },
                    actual={
                        "art_attempt": reused_sources.get("art_attempt"),
                        "layout_attempt": reused_sources.get("layout_attempt"),
                    },
                )
            )
        template_backend = reused_record.get("backends", {}).get("template")
        if template_backend not in {"deterministic", "bria"}:
            raise AuthoringError(
                "reused print candidate has no supported template backend"
            )

    final = authoring_product / "print-candidates" / candidate_id
    partial = final.with_name(final.name + ".partial")
    if final.exists() or partial.exists():
        raise AuthoringError(f"print candidate ID already exists or is partial: {candidate_id}")
    record: dict[str, Any] = {
        "schema_version": 1,
        "print_candidate_id": candidate_id,
        "design_id": expected_identity[0],
        "product_profile_id": expected_identity[1],
        "status": "running",
        "created_at": utc_now(),
        "backends": {
            "template": template_backend,
            "pet": backend,
        },
        "pet_name": pet_name,
        "name_mode": layout_name_mode,
        "sources": {
            "art_attempt": _product_relative(art_attempt, authoring_product),
            "pet_attempt": _product_relative(pet_attempt, authoring_product),
            "pet_experiment": _product_relative(pet_attempt.parent.parent, authoring_product),
            "layout_attempt": _product_relative(layout_attempt, authoring_product),
        },
        "source_hashes": {
            "art_run": sha256(art_attempt / "run.json"),
            "pet_run": sha256(pet_attempt / "run.json"),
            "layout_run": sha256(layout_attempt / "run.json"),
            "art": sha256(art),
            "pet": sha256(pet_attempt / "outputs" / "transformed-pet.png"),
            "layout": sha256(layout_attempt / "outputs" / "layout.json"),
            "product_profile": sha256(profile_path),
        },
        "template_source": (
            {
                "mode": "reused-print-candidate",
                "print_candidate_id": reused_record["print_candidate_id"],
                "path": _product_relative(reused_candidate, authoring_product),
                "manifest_sha256": sha256(reused_candidate / "print-candidate.json"),
            }
            if reused_record is not None and reused_candidate is not None
            else {"mode": "generated"}
        ),
    }
    partial.mkdir(parents=True)
    try:
        atomic_json(partial / "print-candidate.json", record)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    try:
        outputs = partial / "outputs"
        if reused_record is not None and reused_candidate is not None:
            recorded_outputs = {
                item.get("path"): item
                for item in reused_record.get("outputs", [])
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
            required = [
                "outputs/art-print.png",
                "outputs/layout-print.json",
                "outputs/product-profile.json",
                "outputs/template-print-manifest.json",
            ]
            font_files = sorted((reused_candidate / "outputs" / "fonts").rglob("*"))
            required.extend(
                path.relative_to(reused_candidate).as_posix()
                for path in font_files
                if path.is_file()
            )
            for relative in required:
                source = reused_candidate / relative
                descriptor = recorded_outputs.get(relative)
                if (
                    not source.is_file()
                    or not isinstance(descriptor, dict)
                    or descriptor.get("sha256") != sha256(source)
                ):
                    raise AuthoringError(
                        f"reused template output does not resolve to its recorded hash: {relative}"
                    )
                target = outputs / Path(relative).relative_to("outputs")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            template_outputs = TemplatePrintOutputs(
                art=outputs / "art-print.png",
                layout=outputs / "layout-print.json",
                manifest=outputs / "template-print-manifest.json",
                product_profile=outputs / "product-profile.json",
            )
            load_layout(outputs, layout_path=template_outputs.layout)
        else:
            template_outputs = prepare_print_template(
                template_dir=layout_attempt / "outputs",
                layout_path=layout_attempt / "outputs" / "layout.json",
                target_size=(profile.print_size.width, profile.print_size.height),
                output_dir=outputs,
                backend=backend,
                product_profile=profile_path,
            )
        pet_outputs = prepare_print_pet(
            template_dir=layout_attempt / "outputs",
            layout_path=layout_attempt / "outputs" / "layout.json",
            print_layout_path=template_outputs.layout,
            transformed_pet=pet_attempt / "outputs" / "transformed-pet.png",
            output_dir=outputs,
            backend=backend,
        )
        final_print = outputs / "final-print.png"
        debug_print = outputs / "final-print-debug.png"
        render_to_files(
            template_dir=outputs,
            layout_path=template_outputs.layout,
            pet_image=pet_outputs.pet,
            pet_name=pet_name,
            output=final_print,
            debug_output=debug_print,
            png_dpi=(
                (float(profile.print_spec["dpi"]), float(profile.print_spec["dpi"]))
                if profile.print_spec.get("dpi") is not None
                else None
            ),
        )
        validate_print_output(profile, final_print)
        record["status"] = "succeeded"
        record["outputs"] = [
            {
                "path": path.relative_to(partial).as_posix(),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted(outputs.rglob("*"))
            if path.is_file()
        ]
        atomic_json(partial / "print-candidate.json", record)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, final)
        return final
    except Exception as exc:
        record.update(
            status="failed",
            error={"category": type(exc).__name__, "message": str(exc)[:1000]},
        )
        atomic_json(partial / "print-candidate.json", record)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, final)
        raise AuthoringError(
            f"print candidate failed; immutable record saved at {final}: {exc}"
        ) from exc


def _decision_evidence(
    review: Path,
    *,
    product_root: Path,
    kind: str,
    expected_selected: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Validate one stage decision and return its QA and audit descriptors."""
    review = review.expanduser().resolve()
    resolved_product, review_kind, review_id = _review_root(review)
    if resolved_product != product_root or review_kind != kind:
        raise AuthoringError(
            mismatch(
                f"{kind} review scope",
                expected={"product": str(product_root), "kind": kind},
                actual={"product": str(resolved_product), "kind": review_kind},
            )
        )
    path = review / "decision.json"
    decision = _json(path)
    if decision.get("review_id") != review_id:
        raise AuthoringError(
            mismatch(
                f"{kind} decision review identity",
                expected=review_id,
                actual=decision.get("review_id"),
            )
        )
    if decision.get("kind") != kind or decision.get("approved") is not True:
        raise AuthoringError(
            mismatch(
                f"{kind} decision kind/approval",
                expected={"kind": kind, "approved": True},
                actual={
                    "kind": decision.get("kind"),
                    "approved": decision.get("approved"),
                },
            )
        )
    expected_identity = {
        "design_id": product_root.parent.name,
        "product_profile_id": product_root.name,
    }
    actual_identity = {
        "design_id": decision.get("design_id"),
        "product_profile_id": decision.get("product_profile_id"),
    }
    if actual_identity != expected_identity:
        raise AuthoringError(
            mismatch(
                f"{kind} decision design/product identity",
                expected=expected_identity,
                actual=actual_identity,
            )
        )
    if decision.get("selected") != expected_selected:
        raise AuthoringError(
            mismatch(
                f"{kind} stage decision selection",
                expected=expected_selected,
                actual=decision.get("selected"),
            )
            + f"; decision={path}"
        )
    evaluation_descriptor = decision.get("evaluation", {})
    evaluation_path = review / "evaluation.json"
    if evaluation_descriptor.get("path") != "evaluation.json":
        raise AuthoringError(
            f"{kind} decision must reference its sibling evaluation.json: {path}"
        )
    actual_evaluation_hash = sha256(evaluation_path) if evaluation_path.is_file() else None
    if actual_evaluation_hash != evaluation_descriptor.get("evaluation_sha256"):
        raise AuthoringError(
            mismatch(
                f"{kind} decision evaluation hash ({evaluation_path})",
                expected=evaluation_descriptor.get("evaluation_sha256"),
                actual=actual_evaluation_hash,
            )
        )
    evaluation = _json(evaluation_path)
    if evaluation.get("kind") != kind or evaluation.get("hard_gates", {}).get("status") != "passed":
        raise AuthoringError(
            f"{kind} decision must reference a matching evaluation with passing hard gates: "
            f"{evaluation_path}"
        )
    if kind == "assembly":
        covered = any(
            isinstance(candidate, dict)
            and candidate.get("hard_gates_passed") is True
            and all(
                candidate.get(field) == expected_selected[field]
                for field in ("art_attempt", "pet_experiment", "layout_attempt")
            )
            for candidate in evaluation.get("candidates", [])
        )
    else:
        candidate = next(
            (
                item
                for item in evaluation.get("candidates", [])
                if isinstance(item, dict)
                and item.get("experiment_id") == expected_selected["experiment_id"]
            ),
            None,
        )
        covered = isinstance(candidate, dict) and candidate.get("hard_gates_passed") is True
        if covered and "attempt_id" in expected_selected:
            covered = any(
                isinstance(attempt, dict)
                and attempt.get("attempt_id") == expected_selected["attempt_id"]
                and attempt.get("status") == "succeeded"
                and attempt.get("hard_gates", {}).get("status") == "passed"
                for attempt in candidate.get("attempts", [])
            )
    if not covered:
        raise AuthoringError(
            f"{kind} decision selection is not covered by its passing evaluation: "
            f"decision={path}; evaluation={evaluation_path}"
        )
    review = decision.get("decision", {})
    if not isinstance(review.get("selected_by"), str) or not review["selected_by"].strip():
        raise AuthoringError(f"{kind} decision has no reviewer: {path}")
    qa = {
        "review_id": review_id,
        "evaluation_path": _product_relative(evaluation_path, product_root),
        "evaluation_sha256": str(actual_evaluation_hash),
        "status": "passed",
    }
    audit = {
        "review_id": review_id,
        "decision_path": _product_relative(path, product_root),
        "decision_sha256": sha256(path),
        "selected_by": review["selected_by"],
        "selected_at": str(review.get("selected_at", "")),
        "notes": str(review.get("notes", "")),
    }
    return qa, audit


def graduate(*, graduation_id: str, print_candidate: Path,
             art_review: Path, pet_review: Path, layout_review: Path,
             assembly_review: Path, selected_by: str, notes: str,
             authoring_root: Path) -> Path:
    _id(graduation_id, "graduation ID")
    print_candidate = print_candidate.expanduser().resolve()
    print_record = _json(print_candidate / "print-candidate.json")
    if print_record.get("status") != "succeeded":
        raise AuthoringError(
            mismatch(
                f"print candidate status ({print_candidate})",
                expected="succeeded",
                actual=print_record.get("status"),
            )
        )
    candidate_product_root = print_candidate.parent.parent
    print_sources = print_record.get("sources", {})
    try:
        art_attempt = _product_path(
            candidate_product_root, print_sources["art_attempt"], "print art attempt"
        )
        pet_experiment = _product_path(
            candidate_product_root, print_sources["pet_experiment"], "print pet experiment"
        )
        layout_attempt = _product_path(
            candidate_product_root, print_sources["layout_attempt"], "print layout attempt"
        )
    except (KeyError, TypeError) as exc:
        raise AuthoringError(
            f"print candidate has incomplete component sources: {print_candidate}"
        ) from exc
    art_run = _attempt_record(art_attempt, expected_kind="art")
    layout_run = _attempt_record(layout_attempt, expected_kind="layout")
    art_experiment, art_meta = _attempt_experiment(
        art_attempt, art_run, expected_kind="art"
    )
    layout_experiment, layout_meta = _attempt_experiment(
        layout_attempt, layout_run, expected_kind="layout"
    )
    pet_meta = _json(pet_experiment / "experiment.json")
    pet_generation = pet_meta.get("generation")
    if not isinstance(pet_generation, dict) or pet_generation.get("provider") != "openai":
        raise AuthoringError(
            "MVP production graduation requires an OpenAI pet runtime; "
            f"pet_experiment={pet_experiment}; "
            f"provider={pet_generation.get('provider') if isinstance(pet_generation, dict) else None!r}. "
            "Gemini remains available for offline experiments only."
        )
    if not any(_attempt_record(p, require_success=False).get("status") == "succeeded" for p in (pet_experiment / "attempts").glob("*") if p.is_dir() and not p.name.endswith(".partial")):
        raise AuthoringError("pet experiment has no succeeded attempt")
    identities = {
        (art_meta.get("design_id"), art_meta.get("product_profile_id")),
        (pet_meta.get("design_id"), pet_meta.get("product_profile_id")),
        (layout_meta.get("design_id"), layout_meta.get("product_profile_id")),
    }
    if len(identities) != 1:
        raise AuthoringError(
            "selected components do not share one design/product identity; "
            f"actual={sorted(identities, key=repr)!r}"
        )
    design_id, profile_id = next(iter(identities))
    root = authoring_root.expanduser().resolve() / str(design_id) / str(profile_id)
    expected = (
        _product_relative(art_attempt, root),
        _product_relative(pet_experiment, root),
        _product_relative(layout_attempt, root),
    )
    expected_decisions = {
        "art": {
            "experiment_id": art_experiment.name,
            "attempt_id": art_attempt.name,
        },
        "pet": {"experiment_id": pet_experiment.name},
        "layout": {
            "experiment_id": layout_experiment.name,
            "attempt_id": layout_attempt.name,
        },
        "assembly": {
            "art_attempt": expected[0],
            "pet_experiment": expected[1],
            "layout_attempt": expected[2],
        },
    }
    decision_paths = {
        "art": art_review,
        "pet": pet_review,
        "layout": layout_review,
        "assembly": assembly_review,
    }
    component_qa: dict[str, dict[str, str]] = {}
    review_decisions: dict[str, dict[str, str]] = {}
    for kind, decision_path in decision_paths.items():
        qa, audit = _decision_evidence(
            decision_path,
            product_root=root,
            kind=kind,
            expected_selected=expected_decisions[kind],
        )
        review_decisions[kind] = audit
        if kind != "assembly":
            component_qa[kind] = qa
        else:
            compatibility_qa = qa
    reviewer = selected_by.strip()
    if not reviewer:
        raise AuthoringError("--selected-by must not be empty")
    art_file = _require_matching_layout_art(
        art_attempt,
        layout_attempt,
        label="selected layout art",
    )
    layout_file = layout_attempt / "outputs" / "layout.json"
    layout = load_layout(layout_attempt / "outputs")
    graduation = root / "graduations" / graduation_id
    output = graduation / "selection.json"
    if graduation.exists():
        raise AuthoringError(f"graduation already exists: {graduation}")
    value = {
        "schema_version": 1,
        "selection_id": graduation_id,
        "graduation_id": graduation_id,
        "design_id": design_id,
        "product_profile_id": profile_id,
        "selected": {
            "art": {"experiment_id": art_experiment.name, "attempt_id": art_attempt.name, "artifact_sha256": sha256(art_file)},
            "pet_runtime": {"experiment_id": pet_experiment.name, "experiment_sha256": sha256(pet_experiment / "experiment.json")},
            "layout": {
                "experiment_id": layout_experiment.name,
                "attempt_id": layout_attempt.name,
                "layout_sha256": sha256(layout_file),
                "font_sha256": sha256(layout.font_path) if layout.font_path else None,
            },
        },
        "sources": {
            "art_attempt": _product_relative(art_attempt, root),
            "pet_experiment": _product_relative(pet_experiment, root),
            "layout_attempt": _product_relative(layout_attempt, root),
            "print_candidate": _product_relative(print_candidate, root),
        },
        "component_qa": component_qa,
        "compatibility_qa": compatibility_qa,
        "review_decisions": review_decisions,
        "print_qa": {"print_candidate_id": print_record["print_candidate_id"],
                     "print_candidate_sha256": sha256(print_candidate / "print-candidate.json"), "status": "passed"},
        "decision": {
            "selected_by": reviewer,
            "selected_at": utc_now(),
            "notes": notes.strip(),
        },
    }
    graduation.mkdir(parents=True)
    atomic_json(output, value)
    return output


def trace_graduation(graduation: Path) -> str:
    """Validate and render the complete offline lineage for one graduation."""
    graduation = graduation.expanduser().resolve()
    selection_path = graduation / "selection.json"
    if graduation.parent.name != "graduations":
        raise AuthoringError(
            "graduation must be a product-scoped graduations/<graduation-id> directory: "
            f"{graduation}"
        )
    product_root = graduation.parent.parent
    selection = _json(selection_path)
    if selection.get("graduation_id") != graduation.name:
        raise AuthoringError(
            mismatch(
                "graduation identity",
                expected=graduation.name,
                actual=selection.get("graduation_id"),
            )
        )
    identity = {
        "design_id": product_root.parent.name,
        "product_profile_id": product_root.name,
    }
    actual_identity = {key: selection.get(key) for key in identity}
    if actual_identity != identity:
        raise AuthoringError(
            mismatch("graduation product identity", expected=identity, actual=actual_identity)
        )

    selection_sources = selection.get("sources", {})
    required_sources = {
        "art_attempt", "pet_experiment", "layout_attempt", "print_candidate"
    }
    if not isinstance(selection_sources, dict) or set(selection_sources) != required_sources:
        raise AuthoringError(
            mismatch(
                "graduation selection sources",
                expected=sorted(required_sources),
                actual=sorted(selection_sources) if isinstance(selection_sources, dict) else selection_sources,
            )
        )
    sources: dict[str, Any] = {}
    resolved_sources: dict[str, Path] = {}
    for label, value in selection_sources.items():
        path = _product_path(product_root, value, f"selection {label}")
        if not path.exists():
            raise AuthoringError(f"selection {label} does not exist: {path}")
        sources[label] = {
            "path": str(value),
            "resolved_path": str(path),
        }
        resolved_sources[label] = path

    selected = selection.get("selected", {})
    source_hash_checks = (
        (
            "selected art",
            resolved_sources["art_attempt"] / "outputs" / "art.png",
            selected.get("art", {}).get("artifact_sha256"),
        ),
        (
            "selected pet runtime",
            resolved_sources["pet_experiment"] / "experiment.json",
            selected.get("pet_runtime", {}).get("experiment_sha256"),
        ),
        (
            "selected layout",
            resolved_sources["layout_attempt"] / "outputs" / "layout.json",
            selected.get("layout", {}).get("layout_sha256"),
        ),
        (
            "selected print candidate",
            resolved_sources["print_candidate"] / "print-candidate.json",
            selection.get("print_qa", {}).get("print_candidate_sha256"),
        ),
    )
    for label, path, expected_hash in source_hash_checks:
        actual_hash = sha256(path)
        if actual_hash != expected_hash:
            raise AuthoringError(
                mismatch(
                    f"{label} hash ({path})",
                    expected=expected_hash,
                    actual=actual_hash,
                )
            )

    decision_descriptors = selection.get("review_decisions", {})
    required_reviews = {"art", "pet", "layout", "assembly"}
    if (
        not isinstance(decision_descriptors, dict)
        or set(decision_descriptors) != required_reviews
    ):
        raise AuthoringError(
            mismatch(
                "graduation review decisions",
                expected=sorted(required_reviews),
                actual=(
                    sorted(decision_descriptors)
                    if isinstance(decision_descriptors, dict)
                    else decision_descriptors
                ),
            )
        )

    reviews: dict[str, Any] = {}
    for kind, descriptor in decision_descriptors.items():
        if not isinstance(descriptor, dict):
            raise AuthoringError(f"selection {kind} review descriptor is invalid")
        decision = _product_path(
            product_root, descriptor.get("decision_path"), f"{kind} decision"
        )
        evaluation = decision.parent / "evaluation.json"
        actual_decision_hash = sha256(decision)
        actual_evaluation_hash = sha256(evaluation)
        qa = (
            selection.get("component_qa", {}).get(kind)
            if kind != "assembly"
            else selection.get("compatibility_qa")
        )
        if not isinstance(qa, dict):
            raise AuthoringError(f"selection {kind} evaluation descriptor is invalid")
        if actual_decision_hash != descriptor.get("decision_sha256"):
            raise AuthoringError(
                mismatch(
                    f"{kind} decision hash ({decision})",
                    expected=descriptor.get("decision_sha256"),
                    actual=actual_decision_hash,
                )
            )
        if actual_evaluation_hash != qa.get("evaluation_sha256"):
            raise AuthoringError(
                mismatch(
                    f"{kind} evaluation hash ({evaluation})",
                    expected=qa.get("evaluation_sha256"),
                    actual=actual_evaluation_hash,
                )
            )
        reviews[kind] = {
            "review_id": descriptor.get("review_id"),
            "decision": _product_relative(decision, product_root),
            "decision_sha256": actual_decision_hash,
            "evaluation": _product_relative(evaluation, product_root),
            "evaluation_sha256": actual_evaluation_hash,
            "selected": _json(decision).get("selected"),
        }

    return json.dumps(
        {
            "status": "valid",
            "graduation_id": graduation.name,
            **identity,
            "selection": {
                "path": str(selection_path),
                "sha256": sha256(selection_path),
                "selected_by": selection.get("decision", {}).get("selected_by"),
                "selected_at": selection.get("decision", {}).get("selected_at"),
            },
            "sources": sources,
            "reviews": reviews,
            "publications": [
                {
                    "path": str(path),
                    "sha256": sha256(path),
                }
                for path in sorted((graduation / "publications").glob("*.json"))
            ],
        },
        indent=2,
    )


def set_status(experiment: Path, status: str) -> Path:
    if status not in {"draft", "evaluated", "discarded"}:
        raise AuthoringError("status must be draft, evaluated, or discarded; selected is established by selection")
    experiment = experiment.expanduser().resolve()
    try:
        product_root = experiment.parents[2]
    except IndexError as exc:
        raise AuthoringError("experiment path is not inside an authoring product") from exc
    for selection_path in (product_root / "graduations").glob("*/selection.json"):
        sources = _json(selection_path).get("sources", {})
        if not isinstance(sources, dict):
            continue
        for source in sources.values():
            try:
                selected_source = _product_path(
                    product_root, source, "selection source"
                )
            except (OSError, RuntimeError):
                continue
            if selected_source == experiment or selected_source.is_relative_to(experiment):
                raise AuthoringError(
                    "selected experiment status is immutable; create a new experiment"
                )
    path = experiment / "experiment.json"
    value = _json(path)
    current = value.get("status")
    if current == "discarded" and status != "discarded":
        raise AuthoringError("discarded experiment status is terminal")
    value["status"] = status
    value["status_updated_at"] = utc_now()
    atomic_json(path, value)
    return path


def record_publication(*, selection: Path, bundle_manifest: Path, release_catalog: Path,
                       transfer_location: str, authoring_root: Path) -> Path:
    selection = selection.expanduser().resolve()
    authoring_root = authoring_root.expanduser().resolve()
    if not selection.is_relative_to(authoring_root):
        raise AuthoringError(
            f"selection is outside --authoring-root: selection={selection}; "
            f"authoring_root={authoring_root}"
        )
    selected = _json(selection)
    manifest = _json(bundle_manifest.expanduser().resolve())
    catalog = _json(release_catalog.expanduser().resolve())
    selection_hash = sha256(selection)
    manifest_selection_hash = manifest.get("provenance", {}).get("selection_sha256")
    if manifest_selection_hash != selection_hash:
        raise AuthoringError(
            mismatch(
                f"bundle manifest selection hash ({bundle_manifest.expanduser().resolve()})",
                expected=selection_hash,
                actual=manifest_selection_hash,
            )
        )
    template_id = catalog_template_id(selected["design_id"], selected["product_profile_id"])
    revision = int(manifest["bundle_revision"])
    matches = [entry for entry in catalog.get("templates", []) if entry.get("template_id") == template_id and entry.get("bundle_revision") == revision]
    bundle_hash = sha256(bundle_manifest)
    if not matches:
        raise AuthoringError(
            f"release catalog {release_catalog.expanduser().resolve()} has no entry for "
            f"template_id={template_id!r}, bundle_revision={revision!r}"
        )
    if matches[0].get("manifest_sha256") != bundle_hash:
        raise AuthoringError(
            mismatch(
                f"release catalog bundle manifest hash ({bundle_manifest.expanduser().resolve()})",
                expected=bundle_hash,
                actual=matches[0].get("manifest_sha256"),
            )
        )
    if selection.name != "selection.json" or selection.parent.parent.name != "graduations":
        raise AuthoringError(
            "selection must be graduations/<graduation-id>/selection.json: "
            f"{selection}"
        )
    release_id = catalog.get("release_id")
    if not isinstance(release_id, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]{3}", release_id
    ) is None:
        raise AuthoringError(
            f"release catalog has an invalid release_id: {release_id!r}"
        )
    output = (
        selection.parent
        / "publications"
        / f"{release_id}--v{revision:06d}.json"
    )
    receipt = {
        "schema_version": 1,
        "selection_id": selected["selection_id"],
        "graduation_id": selected["graduation_id"],
        "selection_sha256": sha256(selection),
        "template_id": template_id,
        "bundle_revision": revision,
        "bundle_manifest_sha256": sha256(bundle_manifest),
        "release_id": release_id,
        "release_catalog_sha256": sha256(release_catalog),
        "transfer_location": transfer_location,
    }
    if output.exists():
        existing = _json(output)
        if not isinstance(existing.get("published_at"), str) or not existing[
            "published_at"
        ]:
            raise AuthoringError(
                f"existing publication receipt has no published_at timestamp: {output}"
            )
        existing_stable = {
            key: value for key, value in existing.items() if key != "published_at"
        }
        if existing_stable != receipt:
            raise AuthoringError(
                mismatch(
                    f"existing publication receipt ({output})",
                    expected=receipt,
                    actual=existing_stable,
                )
            )
        return output
    atomic_json(output, {**receipt, "published_at": utc_now()})
    return output


def cleanup(*, authoring_root: Path, status: str, older_than_days: int, apply: bool) -> list[Path]:
    root = authoring_root.expanduser().resolve()
    if older_than_days < 0:
        raise AuthoringError("older-than-days must not be negative")
    if root == Path(root.anchor):
        raise AuthoringError("authoring root must not be the filesystem root")
    if not root.is_dir():
        raise AuthoringError(f"authoring root is not a directory: {root}")
    protected: set[Path] = set()
    for selection in root.glob("*/*/graduations/*/selection.json"):
        product_root = selection.parents[2]
        selection_value = _json(selection)
        for value in selection_value.get("sources", {}).values():
            selected = _product_path(product_root, value, "selection source")
            protected.add(selected)
            protected.add(selected.parent.parent)
        for descriptor in selection_value.get("review_decisions", {}).values():
            if isinstance(descriptor, dict):
                decision = _product_path(
                    product_root,
                    descriptor.get("decision_path"),
                    "selection review decision",
                )
                protected.add(decision.parent)
    cutoff = time.time() - older_than_days * 86400
    candidates = []
    if status == "discarded":
        for experiment_file in root.glob("*/*/experiments/*/*/experiment.json"):
            experiment = experiment_file.parent.resolve()
            if _json(experiment_file).get("status") == status and experiment not in protected and experiment.stat().st_mtime < cutoff:
                candidates.append(experiment)
    elif status == "failed":
        for run_file in root.glob("*/*/experiments/*/*/attempts/*/run.json"):
            attempt = run_file.parent.resolve()
            if _json(run_file).get("status") == "failed" and attempt not in protected and attempt.stat().st_mtime < cutoff:
                candidates.append(attempt)
    elif status == "unselected":
        for review in root.glob("*/*/reviews/*/*"):
            if review.is_dir() and review.resolve() not in protected and review.stat().st_mtime < cutoff:
                candidates.append(review.resolve())
        for candidate in root.glob("*/*/print-candidates/*"):
            if candidate.is_dir() and candidate.resolve() not in protected and candidate.stat().st_mtime < cutoff:
                candidates.append(candidate.resolve())
    else:
        raise AuthoringError("cleanup status must be discarded, failed, or unselected")
    if apply:
        for candidate in candidates:
            candidate.relative_to(root)
            shutil.rmtree(candidate)
    return candidates
