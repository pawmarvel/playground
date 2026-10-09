"""Plan and execute restart-safe authoring evidence for scalable design workflows.

The workflow artifacts in this module are private operator records. They
coordinate the existing immutable experiment/review APIs and never enter a
production bundle or alter the FE-facing contract.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .artifact_io import atomic_json, read_json, sha256, utc_now
from .authoring import benchmark, compare, create_experiment, run_attempt
from .fixture_set import (
    load_fixture_selection,
    load_fixture_set,
    write_fixture_selection,
)
from .generation_contract import validate_generation_quality
from .operation_config import DEFAULT_MODELS, NAME_MODES
from .production_bundle import validate_production_bundle


class ScalingWorkflowError(ValueError):
    """Raised when a private scaling workflow is unsafe or inconsistent."""


SCENARIOS = ("new-design", "profile-expansion", "category-variant")
CHECKPOINTS = ("art-review", "smoke-review", "release-review")
REVIEW_GATES = ("scratch", "candidates", "release")
_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_PROVIDER_PROMPT_LABEL = {"openai": "gpt", "gemini": "gemini"}
_PET_NAME_PLACEHOLDER = "{{PET_NAME}}"


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ScalingWorkflowError(
            f"{label} must use lowercase letters, numbers, dots, underscores, or hyphens: {value!r}"
        )
    return value


def _resolve_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ScalingWorkflowError(f"{label} is not a readable file: {resolved}")
    return resolved


def _resolve_directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ScalingWorkflowError(f"{label} is not a directory: {resolved}")
    return resolved


def _read_object(path: Path, label: str) -> dict[str, Any]:
    return read_json(
        path,
        label=label,
        error_type=ScalingWorkflowError,
        require_object=True,
    )


def _copy(source: Path, target: Path) -> Path:
    source = _resolve_file(source, "workflow input")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ScalingWorkflowError(f"{label} must be a nonempty workflow-relative path")
    relative = Path(value)
    if relative.is_absolute():
        raise ScalingWorkflowError(f"{label} must be workflow-relative: {value!r}")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ScalingWorkflowError(f"{label} escapes the workflow root: {value!r}")
    return resolved


def _prompt_from_bundle(
    bundle: Path, manifest: dict[str, Any], kind: str
) -> Path | None:
    key = "art_template" if kind == "art" else "pet_transform"
    value = manifest.get("prompts", {}).get(key)
    if value is None and kind == "art":
        return None
    if not isinstance(value, str) or not value:
        raise ScalingWorkflowError(
            f"source bundle does not declare a usable {key} prompt: {bundle / 'bundle.json'}"
        )
    path = (bundle / value).resolve()
    if not path.is_relative_to(bundle) or not path.is_file():
        raise ScalingWorkflowError(
            f"source bundle {key} prompt is missing or escapes the bundle: {path}"
        )
    return path


def _references_from_bundle(bundle: Path) -> list[Path]:
    references: list[Path] = []
    primary = bundle / "reference-design.png"
    if primary.is_file():
        references.append(primary)
    support = bundle / "reference-designs"
    if support.is_dir():
        references.extend(sorted(path for path in support.glob("*.png") if path.is_file()))
    if len(references) > 4:
        raise ScalingWorkflowError(
            f"source bundle exceeds the four-reference runtime limit: {bundle}"
        )
    return references


def _profile_ratio(profile_path: Path) -> tuple[int, int]:
    value = _read_object(profile_path, "product profile")
    try:
        canvas = value["print"]["canvas"]
        width = int(canvas["width"])
        height = int(canvas["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ScalingWorkflowError(
            f"product profile has no valid print canvas: {profile_path}"
        ) from exc
    if width <= 0 or height <= 0:
        raise ScalingWorkflowError(
            f"product profile print canvas must be positive: {profile_path}"
        )
    return width, height


def _same_ratio(left: Path, right: Path) -> bool:
    left_width, left_height = _profile_ratio(left)
    right_width, right_height = _profile_ratio(right)
    return left_width * right_height == right_width * left_height


def _variant_delta(path: Path) -> dict[str, Any]:
    value = _read_object(path, "variant delta")
    allowed = {"preserve", "change", "forbid", "art", "pet", "notes"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ScalingWorkflowError(
            f"variant delta contains unsupported fields: {unknown}; file={path.resolve()}"
        )

    def validate_section(section: object, label: str) -> None:
        if not isinstance(section, dict):
            raise ScalingWorkflowError(f"{label} must be an object")
        for key in ("preserve", "change", "forbid"):
            entries = section.get(key, [])
            if not isinstance(entries, list) or not all(
                isinstance(item, str) and item.strip() for item in entries
            ):
                raise ScalingWorkflowError(f"{label}.{key} must be an array of nonempty strings")

    validate_section(value, "variant delta")
    for layer in ("art", "pet"):
        if layer in value:
            validate_section(value[layer], f"variant delta.{layer}")
    if not value.get("change") and not any(
        isinstance(value.get(layer), dict) and value[layer].get("change")
        for layer in ("art", "pet")
    ):
        raise ScalingWorkflowError("variant delta must declare at least one change")
    return value


def _delta_block(delta: dict[str, Any], layer: str) -> str:
    merged: dict[str, list[str]] = {key: [] for key in ("preserve", "change", "forbid")}
    for source in (delta, delta.get(layer, {})):
        if not isinstance(source, dict):
            continue
        for key in merged:
            for item in source.get(key, []):
                if item not in merged[key]:
                    merged[key].append(item)
    lines = ["", "## Authoring variant requirements", ""]
    for key, title in (
        ("preserve", "Preserve"),
        ("change", "Change"),
        ("forbid", "Forbid"),
    ):
        lines.append(f"### {title}")
        lines.extend(f"- {item}" for item in merged[key])
        if not merged[key]:
            lines.append("- No additional requirement declared.")
        lines.append("")
    lines.append(
        "Treat these requirements as authoritative for this target while preserving "
        "the existing prompt's safety, transparency, pet-identity, and layer-ownership rules."
    )
    return "\n".join(lines).rstrip() + "\n"


def _profile_block(profile: Path, source_profile: Path) -> str:
    target_width, target_height = _profile_ratio(profile)
    source_width, source_height = _profile_ratio(source_profile)
    ratio_changed = target_width * source_height != source_width * target_height
    return (
        "\n\n## Target product-profile override\n\n"
        f"- Source print canvas: {source_width}x{source_height}.\n"
        f"- Target print canvas: {target_width}x{target_height}.\n"
        f"- Aspect ratio changed: {'yes' if ratio_changed else 'no'}.\n"
        "- Treat the target product profile and requested output dimensions as authoritative.\n"
        "- Preserve the approved visual language, but do not crop, stretch, letterbox, or copy "
        "source safe-zone assumptions when adapting the target composition.\n"
    )


def _snapshot_fixture_set(source: Path, target: Path) -> Path:
    inventory = load_fixture_set(source)
    raw = _read_object(source, "fixture set")
    raw_fixtures = raw.get("fixtures")
    if not isinstance(raw_fixtures, list) or len(raw_fixtures) != len(inventory.fixtures):
        raise ScalingWorkflowError(f"fixture set changed while being snapshotted: {source}")
    for index, fixture in enumerate(inventory.fixtures):
        suffix = fixture.image.suffix.lower() or ".png"
        image_target = target.parent / "images" / f"{fixture.id}{suffix}"
        _copy(fixture.image, image_target)
        raw_fixtures[index]["pet_image"] = f"images/{image_target.name}"
    atomic_json(target, raw)
    load_fixture_set(target)
    return target


def _write_text(source: Path, target: Path, suffix: str = "") -> Path:
    try:
        text = _resolve_file(source, "prompt").read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ScalingWorkflowError(f"prompt is not UTF-8 text: {source.resolve()}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.rstrip() + "\n" + suffix, encoding="utf-8")
    return target


def initialize_workflow(
    *,
    project_root: Path,
    workflow_id: str,
    scenario: str,
    design_id: str,
    product_profile: Path,
    art_prompt: Path | None,
    pet_prompt: Path | None,
    references: list[Path],
    source_bundle: Path | None,
    variant_delta: Path | None,
    name_mode: str | None,
    pet_name: str | None,
    art_provider: str,
    art_model: str | None,
    art_quality: str,
    pet_provider: str | None,
    pet_model: str | None,
    pet_quality: str | None,
    smoke_fixture_set: Path,
    release_fixture_set: Path,
    evaluation_protocol: Path,
    smoke_fixture_count: int | None,
    release_fixture_count: int | None,
    skip_smoke: bool,
    scratch_approved: bool,
    created_by: str,
    empty_canvas: bool = False,
    max_paid_calls: int = 24,
    force: bool = False,
) -> Path:
    """Create one editable private workflow specification and input snapshot."""
    project = _resolve_directory(project_root, "project root")
    workflow_id = _identifier(workflow_id, "workflow ID")
    design_id = _identifier(design_id, "design ID")
    if scenario not in SCENARIOS:
        raise ScalingWorkflowError(f"scenario must be one of: {', '.join(SCENARIOS)}")
    if isinstance(max_paid_calls, bool) or max_paid_calls < 1:
        raise ScalingWorkflowError("max paid calls must be a positive integer")
    if empty_canvas and art_prompt is not None:
        raise ScalingWorkflowError("--empty-canvas cannot be combined with --art-prompt")
    if empty_canvas and scenario != "new-design":
        raise ScalingWorkflowError(
            "--empty-canvas is valid only for a new-design workflow; derived targets "
            "must preserve or explicitly adapt their source art intent"
        )
    profile = _resolve_file(product_profile, "target product profile")
    profile_value = _read_object(profile, "target product profile")
    profile_id = _identifier(str(profile_value.get("profile_id", "")), "product profile ID")
    source_path: Path | None = None
    source_manifest: dict[str, Any] | None = None
    if scenario == "new-design":
        if source_bundle is not None or variant_delta is not None:
            raise ScalingWorkflowError(
                "new-design does not accept --source-bundle or --variant-delta"
            )
    else:
        if source_bundle is None:
            raise ScalingWorkflowError(f"{scenario} requires --source-bundle")
        source_path = _resolve_directory(source_bundle, "source bundle")
        try:
            source_manifest = validate_production_bundle(source_path)
        except Exception as exc:
            raise ScalingWorkflowError(str(exc)) from exc
        if scenario == "profile-expansion" and source_manifest.get("design_id") != design_id:
            raise ScalingWorkflowError(
                "profile expansion must retain the source design ID; "
                f"expected={source_manifest.get('design_id')!r}; actual={design_id!r}"
            )
        if scenario == "category-variant" and source_manifest.get("design_id") == design_id:
            raise ScalingWorkflowError(
                "category variant requires a new target design ID distinct from the source"
            )
        if scenario == "category-variant" and variant_delta is None:
            raise ScalingWorkflowError("category-variant requires --variant-delta")

    source_runtime = (
        source_manifest.get("runtime", {}) if source_manifest is not None else {}
    )
    pet_provider = pet_provider or source_runtime.get("provider") or "openai"
    if art_provider not in _PROVIDER_PROMPT_LABEL:
        raise ScalingWorkflowError(f"unsupported art provider: {art_provider!r}")
    if pet_provider not in _PROVIDER_PROMPT_LABEL:
        raise ScalingWorkflowError(f"unsupported pet provider: {pet_provider!r}")
    pet_model = pet_model or source_runtime.get("model") or DEFAULT_MODELS[pet_provider]
    pet_quality = (
        pet_quality
        or source_runtime.get("request_parameters", {}).get("quality")
        or "low"
    )
    art_model = art_model or DEFAULT_MODELS[art_provider]
    if pet_provider != "openai":
        raise ScalingWorkflowError(
            "production scaling workflows require OpenAI pet runtime because bundle-v1 "
            "does not permit a Gemini production runtime"
        )
    try:
        validate_generation_quality(
            provider=art_provider, model=art_model, quality=art_quality
        )
        validate_generation_quality(
            provider=pet_provider, model=pet_model, quality=pet_quality
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ScalingWorkflowError(str(exc)) from exc

    if name_mode is None:
        if source_manifest is not None:
            name_mode = source_manifest.get("renderer", {}).get("name_mode")
        else:
            name_mode = "layout-text"
    if name_mode not in NAME_MODES:
        raise ScalingWorkflowError(f"name mode must be one of: {', '.join(NAME_MODES)}")
    if pet_name is None and source_manifest is not None and name_mode != "none":
        source_name = source_manifest.get("provenance", {}).get("qa_fixture", {}).get(
            "pet_name"
        )
        if isinstance(source_name, str) and source_name.strip():
            pet_name = source_name
    if name_mode == "embedded-in-pet" and not (pet_name or "").strip():
        raise ScalingWorkflowError("embedded-in-pet workflows require a representative pet name")
    pet_name = (pet_name or "").strip() or None

    root = (project / "work" / "workflows" / workflow_id).resolve()
    stage = root.with_name(root.name + ".partial")
    if root.exists() or stage.exists():
        if not force:
            raise ScalingWorkflowError(
                f"workflow already exists or is partial: {root}; use --force to replace the private draft"
            )
        if root.exists():
            protected = [
                path
                for directory in ("plans", "runs", "approvals")
                for path in (root / directory).glob("*.json")
            ]
            if protected:
                raise ScalingWorkflowError(
                    "--force cannot replace a workflow with planned, executed, or approved "
                    f"history; create a successor workflow ID. Protected files: {protected}"
                )
            shutil.rmtree(root)
        if stage.exists():
            shutil.rmtree(stage)
    stage.mkdir(parents=True)
    warnings: list[str] = []
    try:
        inputs = stage / "inputs"
        profile_target = _copy(profile, inputs / "product-profile.json")
        source_profile: Path | None = None
        if source_path is not None:
            source_profile = _resolve_file(
                source_path / "product-profile.json", "source bundle product profile"
            )

        selected_art_prompt = art_prompt
        selected_pet_prompt = pet_prompt
        if source_path is not None and source_manifest is not None:
            selected_art_prompt = selected_art_prompt or _prompt_from_bundle(
                source_path, source_manifest, "art"
            )
            selected_pet_prompt = selected_pet_prompt or _prompt_from_bundle(
                source_path, source_manifest, "pet"
            )
        if selected_pet_prompt is None:
            raise ScalingWorkflowError("workflow requires a pet prompt")
        if selected_art_prompt is None and not empty_canvas and not (
            source_manifest
            and source_manifest.get("prompts", {}).get("art_template") is None
        ):
            raise ScalingWorkflowError("workflow requires an art prompt")
        try:
            pet_prompt_text = _resolve_file(
                selected_pet_prompt, "pet prompt"
            ).read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ScalingWorkflowError(
                f"pet prompt is not UTF-8 text: {Path(selected_pet_prompt).resolve()}"
            ) from exc
        uses_pet_name = _PET_NAME_PLACEHOLDER in pet_prompt_text
        if name_mode == "embedded-in-pet" and not uses_pet_name:
            raise ScalingWorkflowError(
                "embedded-in-pet workflow requires {{PET_NAME}} in the pet prompt"
            )
        if name_mode != "embedded-in-pet" and uses_pet_name:
            raise ScalingWorkflowError(
                f"{name_mode} workflow must not contain {{PET_NAME}} in the pet prompt"
            )

        delta_value: dict[str, Any] | None = None
        if variant_delta is not None:
            delta_source = _resolve_file(variant_delta, "variant delta")
            delta_value = _variant_delta(delta_source)
            _copy(delta_source, inputs / "variant-delta.json")

        art_suffix = ""
        pet_suffix = ""
        if source_profile is not None:
            profile_suffix = _profile_block(profile_target, source_profile)
            art_suffix += profile_suffix
            if not _same_ratio(profile_target, source_profile):
                warnings.append(
                    "target profile aspect ratio differs from the source; art and layout require explicit visual review"
                )
        if delta_value is not None:
            art_suffix += "\n" + _delta_block(delta_value, "art")
            pet_suffix += "\n" + _delta_block(delta_value, "pet")
            warnings.append(
                "variant prompts are deterministic drafts; visually approve scratch output before paid durable evidence"
            )

        art_prompt_target: Path | None = None
        art_empty_canvas = empty_canvas or selected_art_prompt is None
        if selected_art_prompt is not None:
            art_prompt_target = _write_text(
                selected_art_prompt,
                inputs / f"art-template-{_PROVIDER_PROMPT_LABEL[art_provider]}.md",
                art_suffix,
            )
        pet_prompt_target = _write_text(
            selected_pet_prompt,
            inputs / f"pet-transform-{_PROVIDER_PROMPT_LABEL[pet_provider]}.md",
            pet_suffix,
        )

        selected_references = [_resolve_file(item, "reference design") for item in references]
        if not selected_references and source_path is not None:
            selected_references = _references_from_bundle(source_path)
        if scenario == "category-variant" and not references:
            raise ScalingWorkflowError(
                "category variant requires at least one target --reference-design; "
                "do not silently reuse the source finished design"
            )
        if len(selected_references) > 4:
            raise ScalingWorkflowError("workflow supports at most four ordered references")
        reference_targets: list[Path] = []
        for index, reference in enumerate(selected_references, start=1):
            suffix = reference.suffix.lower() or ".png"
            reference_targets.append(
                _copy(
                    reference,
                    inputs / "references" / f"reference-design-{index:04d}{suffix}",
                )
            )
        if delta_value is not None:
            count = len(reference_targets)
            role_block = (
                "\n\n## Workflow input binding\n\n"
                f"- This target uses {count} ordered finished-design reference image"
                f"{'s' if count != 1 else ''}.\n"
                "- For pet transformation, the customer pet is always Image A and the "
                "finished-design references follow in their recorded order.\n"
                "- Treat this binding as authoritative if an inherited image-count or "
                "image-letter sentence conflicts with it; update that sentence during "
                "scratch review before approval.\n"
            )
            if art_prompt_target is not None:
                with art_prompt_target.open("a", encoding="utf-8") as stream:
                    stream.write(role_block)
            with pet_prompt_target.open("a", encoding="utf-8") as stream:
                stream.write(role_block)
        if not reference_targets:
            warnings.append(
                "workflow has no finished-design reference; "
                + (
                    "art uses a deterministic empty canvas"
                    if art_empty_canvas
                    else "art is prompt-only"
                )
                + " and pet generation uses only the customer pet"
            )

        smoke_manifest = _snapshot_fixture_set(
            _resolve_file(smoke_fixture_set, "smoke fixture set"),
            inputs / "fixture-sets" / "smoke" / "fixture-set.json",
        )
        release_manifest = _snapshot_fixture_set(
            _resolve_file(release_fixture_set, "release fixture set"),
            inputs / "fixture-sets" / "release" / "fixture-set.json",
        )
        protocol_target = _copy(
            _resolve_file(evaluation_protocol, "evaluation protocol"),
            inputs / "evaluation-protocol.json",
        )
        smoke_selection = write_fixture_selection(
            smoke_manifest,
            output=inputs / "fixture-sets" / "smoke" / "selection.json",
            fixture_count=smoke_fixture_count,
        )
        release_selection = write_fixture_selection(
            release_manifest,
            output=inputs / "fixture-sets" / "release" / "selection.json",
            fixture_count=(
                (6 if skip_smoke else 3)
                if release_fixture_count is None
                else release_fixture_count
            ),
            prior_selection=None if skip_smoke else smoke_selection,
        )
        active_selections = (
            (release_selection,) if skip_smoke else (smoke_selection, release_selection)
        )
        if skip_smoke:
            warnings.append(
                "pet smoke benchmark is intentionally disabled; release evidence is the "
                "first durable multi-fixture pet quality gate"
            )
        for selection in active_selections:
            selection_value = _read_object(selection, "fixture selection")
            warnings.extend(str(item) for item in selection_value.get("warnings", []))

        source_record: dict[str, Any] | None = None
        if source_path is not None and source_manifest is not None:
            source_record = {
                "bundle": str(source_path),
                "bundle_manifest_sha256": sha256(source_path / "bundle.json"),
                "template_id": source_manifest["template_id"],
                "bundle_revision": source_manifest["bundle_revision"],
                "design_id": source_manifest["design_id"],
                "product_profile_id": source_manifest["product_profile_id"],
            }

        spec = {
            "schema_version": 1,
            "workflow_id": workflow_id,
            "scenario": scenario,
            "created_at": utc_now(),
            "created_by": created_by,
            "project_root": str(project),
            "authoring_root": os.path.relpath(
                (project / "work" / "authoring").resolve(), start=stage
            ),
            "target": {
                "design_id": design_id,
                "product_profile_id": profile_id,
                "product_profile": _relative(stage, profile_target),
            },
            "source": source_record,
            "inputs": {
                "art_prompt": (
                    _relative(stage, art_prompt_target)
                    if art_prompt_target is not None
                    else None
                ),
                "art_empty_canvas": art_empty_canvas,
                "pet_prompt": _relative(stage, pet_prompt_target),
                "references": [_relative(stage, item) for item in reference_targets],
                "variant_delta": (
                    "inputs/variant-delta.json" if delta_value is not None else None
                ),
                "evaluation_protocol": _relative(stage, protocol_target),
                "fixture_sets": {
                    "smoke": {
                        "manifest": _relative(stage, smoke_manifest),
                        "selection": _relative(stage, smoke_selection),
                    },
                    "release": {
                        "manifest": _relative(stage, release_manifest),
                        "selection": _relative(stage, release_selection),
                    },
                },
            },
            "generation": {
                "art": {
                    "provider": "local" if art_empty_canvas else art_provider,
                    "model": (
                        "deterministic-empty-canvas-v1" if art_empty_canvas else art_model
                    ),
                    "quality": None if art_empty_canvas else art_quality,
                },
                "pet": {
                    "provider": pet_provider,
                    "model": pet_model,
                    "quality": pet_quality,
                },
            },
            "personalization": {
                "name_mode": name_mode,
                "representative_pet_name": pet_name,
            },
            "quality_gates": {"scratch_approved": bool(scratch_approved)},
            "execution": {
                "max_paid_calls": max_paid_calls,
                "smoke_enabled": not skip_smoke,
            },
            "ids": {
                "art_experiment": (
                    f"art-local-{workflow_id}"
                    if art_empty_canvas
                    else f"art-{_PROVIDER_PROMPT_LABEL[art_provider]}-{workflow_id}"
                ),
                "art_attempt": "attempt-0001",
                "art_review": f"art-{workflow_id}-review",
                "pet_experiment": f"pet-{_PROVIDER_PROMPT_LABEL[pet_provider]}-{workflow_id}",
                "smoke_review": f"pet-{workflow_id}-smoke-review",
                "release_review": f"pet-{workflow_id}-release-review",
            },
            "warnings": sorted(set(warnings)),
        }
        atomic_json(stage / "workflow.json", spec)
        root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, root)
        return root / "workflow.json"
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def load_workflow_spec(path: Path) -> tuple[Path, dict[str, Any]]:
    spec_path = _resolve_file(path, "workflow specification")
    root = spec_path.parent
    value = _read_object(spec_path, "workflow specification")
    required = {
        "schema_version",
        "workflow_id",
        "scenario",
        "created_at",
        "created_by",
        "project_root",
        "authoring_root",
        "target",
        "source",
        "inputs",
        "generation",
        "personalization",
        "quality_gates",
        "execution",
        "ids",
        "warnings",
    }
    if set(value) != required or value.get("schema_version") != 1:
        raise ScalingWorkflowError(
            f"workflow specification must match the closed authoring-workflow-v1 contract: {spec_path}"
        )
    _identifier(str(value.get("workflow_id", "")), "workflow ID")
    if value.get("scenario") not in SCENARIOS:
        raise ScalingWorkflowError(f"unsupported workflow scenario: {value.get('scenario')!r}")
    authoring_value = value.get("authoring_root")
    if not isinstance(authoring_value, str) or not authoring_value.strip():
        raise ScalingWorkflowError(f"workflow authoring_root must be a nonempty path: {spec_path}")
    target = value.get("target")
    if not isinstance(target, dict) or set(target) != {
        "design_id",
        "product_profile_id",
        "product_profile",
    }:
        raise ScalingWorkflowError(f"workflow target must be an object: {spec_path}")
    _identifier(str(target.get("design_id", "")), "target design ID")
    _identifier(str(target.get("product_profile_id", "")), "target product profile ID")
    for field in ("product_profile",):
        _resolve_file(_path(root, target.get(field), f"target.{field}"), f"target.{field}")
    inputs = value.get("inputs")
    expected_input_fields = {
        "art_prompt",
        "art_empty_canvas",
        "pet_prompt",
        "references",
        "variant_delta",
        "evaluation_protocol",
        "fixture_sets",
    }
    if not isinstance(inputs, dict) or set(inputs) != expected_input_fields:
        raise ScalingWorkflowError(f"workflow inputs must be an object: {spec_path}")
    if not isinstance(inputs.get("art_empty_canvas"), bool):
        raise ScalingWorkflowError("workflow inputs.art_empty_canvas must be boolean")
    if inputs["art_empty_canvas"] != (inputs.get("art_prompt") is None):
        raise ScalingWorkflowError(
            "workflow art_empty_canvas must be true exactly when art_prompt is null"
        )
    for field in ("pet_prompt", "evaluation_protocol"):
        _resolve_file(_path(root, inputs.get(field), f"inputs.{field}"), f"inputs.{field}")
    if inputs.get("art_prompt") is not None:
        _resolve_file(_path(root, inputs["art_prompt"], "inputs.art_prompt"), "inputs.art_prompt")
    references = inputs.get("references")
    if not isinstance(references, list) or len(references) > 4:
        raise ScalingWorkflowError("workflow references must be an array of at most four paths")
    for index, reference in enumerate(references):
        _resolve_file(_path(root, reference, f"inputs.references[{index}]"), "reference")
    variant_delta = inputs.get("variant_delta")
    if variant_delta is not None:
        _variant_delta(_path(root, variant_delta, "inputs.variant_delta"))
    fixtures = inputs.get("fixture_sets")
    if not isinstance(fixtures, dict) or set(fixtures) != {"smoke", "release"}:
        raise ScalingWorkflowError("workflow must define smoke and release fixture sets")
    for tier in ("smoke", "release"):
        entry = fixtures[tier]
        if not isinstance(entry, dict) or set(entry) != {"manifest", "selection"}:
            raise ScalingWorkflowError(f"workflow fixture_sets.{tier} is invalid")
        fixture_set = load_fixture_set(_path(root, entry["manifest"], f"{tier} manifest"))
        load_fixture_selection(
            fixture_set, _path(root, entry["selection"], f"{tier} selection")
        )
    generation = value.get("generation")
    if not isinstance(generation, dict) or set(generation) != {"art", "pet"}:
        raise ScalingWorkflowError("workflow generation must define exactly art and pet")
    for kind in ("art", "pet"):
        entry = generation[kind]
        if not isinstance(entry, dict) or set(entry) != {"provider", "model", "quality"}:
            raise ScalingWorkflowError(
                f"workflow generation.{kind} must define provider, model, and quality"
            )
        if entry["provider"] == "local":
            if kind != "art" or not inputs["art_empty_canvas"]:
                raise ScalingWorkflowError("local generation is valid only for empty-canvas art")
            if entry["model"] != "deterministic-empty-canvas-v1" or entry["quality"] is not None:
                raise ScalingWorkflowError("empty-canvas art generation settings are invalid")
        else:
            try:
                validate_generation_quality(
                    provider=entry["provider"],
                    model=entry["model"],
                    quality=entry["quality"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ScalingWorkflowError(
                    f"workflow generation.{kind} is invalid: {exc}"
                ) from exc
    if generation["pet"]["provider"] != "openai":
        raise ScalingWorkflowError(
            "workflow pet provider must remain OpenAI for the bundle-v1 runtime contract"
        )
    personalization = value.get("personalization")
    if not isinstance(personalization, dict) or set(personalization) != {
        "name_mode",
        "representative_pet_name",
    }:
        raise ScalingWorkflowError("workflow personalization block is invalid")
    if personalization["name_mode"] not in NAME_MODES:
        raise ScalingWorkflowError(
            f"workflow name mode must be one of: {', '.join(NAME_MODES)}"
        )
    if personalization["name_mode"] == "embedded-in-pet" and not isinstance(
        personalization.get("representative_pet_name"), str
    ):
        raise ScalingWorkflowError(
            "embedded-in-pet workflow requires representative_pet_name"
        )
    pet_prompt_text = _path(root, inputs["pet_prompt"], "inputs.pet_prompt").read_text(
        encoding="utf-8"
    )
    uses_pet_name = _PET_NAME_PLACEHOLDER in pet_prompt_text
    if uses_pet_name != (personalization["name_mode"] == "embedded-in-pet"):
        raise ScalingWorkflowError(
            "workflow pet prompt {{PET_NAME}} token does not match personalization.name_mode"
        )
    quality_gates = value.get("quality_gates")
    if (
        not isinstance(quality_gates, dict)
        or set(quality_gates) != {"scratch_approved"}
        or not isinstance(quality_gates.get("scratch_approved"), bool)
    ):
        raise ScalingWorkflowError("workflow quality_gates block is invalid")
    ids = value.get("ids")
    expected_ids = {
        "art_experiment",
        "art_attempt",
        "art_review",
        "pet_experiment",
        "smoke_review",
        "release_review",
    }
    if not isinstance(ids, dict) or set(ids) != expected_ids:
        raise ScalingWorkflowError("workflow IDs block is invalid")
    for label, identifier in ids.items():
        _identifier(identifier, f"workflow IDs.{label}")
    source = value.get("source")
    if source is not None:
        if not isinstance(source, dict):
            raise ScalingWorkflowError("workflow source must be null or an object")
        expected_source_fields = {
            "bundle",
            "bundle_manifest_sha256",
            "template_id",
            "bundle_revision",
            "design_id",
            "product_profile_id",
        }
        if set(source) != expected_source_fields:
            raise ScalingWorkflowError(
                f"workflow source must match the closed source descriptor: {spec_path}"
            )
        digest = source.get("bundle_manifest_sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ScalingWorkflowError(
                f"workflow source bundle manifest hash is invalid: {digest!r}"
            )
    execution = value.get("execution")
    if (
        not isinstance(execution, dict)
        or set(execution) != {"max_paid_calls", "smoke_enabled"}
        or isinstance(execution.get("max_paid_calls"), bool)
        or not isinstance(execution.get("max_paid_calls"), int)
        or execution["max_paid_calls"] < 1
        or not isinstance(execution.get("smoke_enabled"), bool)
    ):
        raise ScalingWorkflowError(
            "workflow execution must define a positive max_paid_calls integer and "
            "boolean smoke_enabled"
        )
    return root, value


def _input_inventory(root: Path) -> list[dict[str, Any]]:
    inventory = []
    for path in sorted((root / "inputs").rglob("*")):
        if path.is_file():
            inventory.append(
                {
                    "path": _relative(root, path),
                    "sha256": sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
    return inventory


def _approval_descriptor(root: Path, gate: str) -> dict[str, Any] | None:
    path = root / "approvals" / f"{gate}.json"
    if not path.is_file():
        return None
    value = _read_object(path, f"{gate} workflow approval")
    if value.get("schema_version") != 1 or value.get("gate") != gate:
        raise ScalingWorkflowError(f"invalid {gate} workflow approval: {path}")
    if gate != "scratch":
        evidence = value.get("evidence")
        if not isinstance(evidence, dict) or not evidence:
            raise ScalingWorkflowError(
                f"{gate} workflow approval has no review evidence: {path}"
            )
        for label, descriptor in evidence.items():
            if not isinstance(descriptor, dict):
                raise ScalingWorkflowError(
                    f"{gate} workflow approval evidence is invalid: {label!r}"
                )
            evidence_path = _resolve_file(
                Path(str(descriptor.get("path", ""))),
                f"{gate} approval evidence {label}",
            )
            actual = sha256(evidence_path)
            if actual != descriptor.get("sha256"):
                raise ScalingWorkflowError(
                    f"{gate} workflow approval evidence changed; file={evidence_path}; "
                    f"expected_sha256={descriptor.get('sha256')!r}; actual_sha256={actual!r}"
                )
    return {"path": _relative(root, path), "sha256": sha256(path)}


def _scratch_approval_is_current(root: Path) -> bool:
    path = root / "approvals" / "scratch.json"
    if not path.is_file():
        return False
    approval = _read_object(path, "scratch workflow approval")
    return approval.get("evidence", {}).get("input_inventory") == _input_inventory(root)


def _plan_fingerprint(
    spec_path: Path,
    inventory: list[dict[str, Any]],
    checkpoint: str,
    approvals: dict[str, Any],
) -> str:
    import hashlib

    payload = json.dumps(
        {
            "spec_sha256": sha256(spec_path),
            "inputs": inventory,
            "checkpoint": checkpoint,
            "quality_gate_evidence": approvals,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _selection_count(root: Path, spec: dict[str, Any], tier: str) -> int:
    entry = spec["inputs"]["fixture_sets"][tier]
    value = _read_object(_path(root, entry["selection"], f"{tier} selection"), "fixture selection")
    selected_ids = value.get("selected_fixture_ids")
    if (
        not isinstance(selected_ids, list)
        or not selected_ids
        or not all(isinstance(item, str) for item in selected_ids)
        or len(selected_ids) != len(set(selected_ids))
    ):
        raise ScalingWorkflowError(
            f"{tier} fixture selection has invalid selected_fixture_ids"
        )
    if tier == "release" and spec["execution"]["smoke_enabled"]:
        smoke_entry = spec["inputs"]["fixture_sets"]["smoke"]
        smoke_path = _path(root, smoke_entry["selection"], "smoke selection")
        smoke = _read_object(smoke_path, "smoke fixture selection")
        smoke_ids = smoke.get("selected_fixture_ids")
        release_manifest = load_fixture_set(
            _path(root, entry["manifest"], "release fixture set")
        )
        release_inventory_ids = {fixture.id for fixture in release_manifest.fixtures}
        expected_prior_ids = [
            item for item in smoke_ids or [] if item in release_inventory_ids
        ]
        expected_prior = {
            "selection_sha256": sha256(smoke_path),
            "selected_fixture_ids": expected_prior_ids,
        }
        actual_prior = value.get("prior_coverage")
        if actual_prior != expected_prior:
            raise ScalingWorkflowError(
                "release selection is not bound to the current smoke selection; "
                f"expected_prior_coverage={expected_prior!r}; "
                f"actual_prior_coverage={actual_prior!r}"
            )
        overlap = sorted(set(selected_ids) & set(expected_prior_ids))
        if overlap:
            raise ScalingWorkflowError(
                "release selection duplicates paid smoke coverage; "
                f"duplicate_fixture_ids={overlap}"
            )
    return len(selected_ids)


def _planned_tasks(
    root: Path, spec: dict[str, Any], checkpoint: str
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = [
        {"task_id": "create-art-experiment", "paid_calls": 0},
        {
            "task_id": "run-art-attempt",
            "paid_calls": 0 if spec["inputs"]["art_empty_canvas"] else 1,
        },
        {"task_id": "compare-art", "paid_calls": 0},
    ]
    smoke_enabled = spec["execution"]["smoke_enabled"]
    if checkpoint == "smoke-review" and not smoke_enabled:
        raise ScalingWorkflowError(
            "smoke-review is disabled for this workflow; plan art-review, approve the "
            "candidates gate, then plan release-review"
        )
    if checkpoint in {"smoke-review", "release-review"}:
        tasks.extend(
            [
                {"task_id": "create-pet-experiment", "paid_calls": 0},
            ]
        )
        if smoke_enabled:
            tasks.extend(
                [
                    {
                        "task_id": "benchmark-pet-smoke",
                        "paid_calls": _selection_count(root, spec, "smoke"),
                    },
                    {"task_id": "compare-pet-smoke", "paid_calls": 0},
                ]
            )
    if checkpoint == "release-review":
        tasks.extend(
            [
                {
                    "task_id": "benchmark-pet-release",
                    "paid_calls": _selection_count(root, spec, "release"),
                },
                {"task_id": "compare-pet-release", "paid_calls": 0},
            ]
        )
    return tasks


def plan_workflow(
    *, spec_path: Path, checkpoint: str, output: Path | None = None
) -> Path:
    """Write an immutable, no-cost execution plan through one review checkpoint."""
    if checkpoint not in CHECKPOINTS:
        raise ScalingWorkflowError(
            f"checkpoint must be one of: {', '.join(CHECKPOINTS)}"
        )
    root, spec = load_workflow_spec(spec_path)
    scratch_descriptor = _approval_descriptor(root, "scratch")
    scratch_current = _scratch_approval_is_current(root)
    if scratch_descriptor is not None and not scratch_current:
        raise ScalingWorkflowError(
            "workflow inputs changed after scratch approval; create a successor workflow "
            "and review the changed scratch output"
        )
    if not spec["quality_gates"].get("scratch_approved") and not scratch_current:
        raise ScalingWorkflowError(
            "scratch quality gate is not approved; inspect representative art/pet output, "
            "then run `pawmarvel-author workflow approve --gate scratch ...`"
        )
    inventory = _input_inventory(root)
    approvals: dict[str, Any] = {}
    if scratch_descriptor is not None:
        approvals["scratch"] = scratch_descriptor
    candidate_approval = _approval_descriptor(root, "candidates")
    if checkpoint == "release-review":
        if candidate_approval is None:
            raise ScalingWorkflowError(
                "release evidence requires a recorded candidates approval; inspect the art and "
                "smoke comparison artifacts, then run `pawmarvel-author workflow approve "
                "--gate candidates ...`"
            )
        approvals["candidates"] = candidate_approval
    fingerprint = _plan_fingerprint(
        spec_path.resolve(), inventory, checkpoint, approvals
    )
    ids = spec["ids"]
    tasks = _planned_tasks(root, spec, checkpoint)
    plan = {
        "schema_version": 1,
        "plan_id": f"{spec['workflow_id']}--{checkpoint}--{fingerprint[:12]}",
        "workflow_id": spec["workflow_id"],
        "scenario": spec["scenario"],
        "checkpoint": checkpoint,
        "created_at": utc_now(),
        "specification": {
            "path": str(spec_path.resolve()),
            "sha256": sha256(spec_path.resolve()),
        },
        "input_inventory": inventory,
        "quality_gate_evidence": approvals,
        "source": deepcopy(spec.get("source")),
        "target": deepcopy(spec["target"]),
        "tasks": tasks,
        "estimated_paid_calls": sum(int(task["paid_calls"]) for task in tasks),
        "warnings": list(spec.get("warnings", [])),
        "resolved_ids": deepcopy(ids),
    }
    if plan["estimated_paid_calls"] > spec["execution"]["max_paid_calls"]:
        raise ScalingWorkflowError(
            "workflow plan exceeds its paid-call budget; "
            f"estimated={plan['estimated_paid_calls']}; "
            f"maximum={spec['execution']['max_paid_calls']}; "
            "reduce fixture selections or initialize a reviewed workflow with a larger budget"
        )
    target = (
        output.expanduser().resolve()
        if output is not None
        else root / "plans" / f"{plan['plan_id']}.json"
    )
    if target.exists():
        existing = _read_object(target, "workflow plan")
        stable_fields = (
            "schema_version",
            "plan_id",
            "workflow_id",
            "scenario",
            "checkpoint",
            "specification",
            "input_inventory",
            "quality_gate_evidence",
            "source",
            "target",
            "tasks",
            "estimated_paid_calls",
            "warnings",
            "resolved_ids",
        )
        if all(existing.get(field) == plan.get(field) for field in stable_fields):
            return target
        raise ScalingWorkflowError(
            f"workflow plan already exists with different bytes: {target}; create a new plan path"
        )
    atomic_json(target, plan)
    return target


def record_workflow_review(
    *,
    spec_path: Path,
    gate: str,
    reviewed_by: str,
    notes: str,
    accept_warnings: bool = False,
) -> Path:
    """Record a private human checkpoint bound to immutable review bytes."""
    if gate not in REVIEW_GATES:
        raise ScalingWorkflowError(
            f"workflow review gate must be one of: {', '.join(REVIEW_GATES)}"
        )
    reviewer = reviewed_by.strip()
    review_notes = notes.strip()
    if not reviewer:
        raise ScalingWorkflowError("workflow review requires a nonempty reviewer")
    if not review_notes:
        raise ScalingWorkflowError("workflow review requires nonempty notes")
    root, spec = load_workflow_spec(spec_path)
    if gate == "release" and _approval_descriptor(root, "candidates") is None:
        raise ScalingWorkflowError(
            "release approval requires the earlier candidates approval"
        )
    if gate == "scratch":
        approval = {
            "schema_version": 1,
            "workflow_id": spec["workflow_id"],
            "gate": gate,
            "reviewed_at": utc_now(),
            "reviewed_by": reviewer,
            "notes": review_notes,
            "accepted_warnings": [],
            "evidence": {"input_inventory": _input_inventory(root)},
        }
        output = root / "approvals" / "scratch.json"
        if output.exists():
            existing = _read_object(output, "scratch workflow approval")
            comparable = dict(approval)
            comparable["reviewed_at"] = existing.get("reviewed_at")
            if existing == comparable:
                return output
            raise ScalingWorkflowError(
                f"workflow scratch approval already exists for different inputs or notes: {output}; "
                "create a successor workflow after changing an approved draft"
            )
        atomic_json(output, approval)
        return output

    target = spec["target"]
    product = (
        (root / Path(spec["authoring_root"]).expanduser()).resolve()
        / target["design_id"]
        / target["product_profile_id"]
    )
    ids = spec["ids"]
    if gate == "candidates":
        review_paths = {
            "art": product / "reviews" / "art" / ids["art_review"] / "evaluation.json",
        }
        if spec["execution"]["smoke_enabled"]:
            review_paths["pet_smoke"] = (
                product / "reviews" / "pet" / ids["smoke_review"] / "evaluation.json"
            )
    else:
        review_paths = {
            "pet_release": product
            / "reviews"
            / "pet"
            / ids["release_review"]
            / "evaluation.json"
        }
    evidence: dict[str, Any] = {}
    warnings: list[str] = []
    for label, path in review_paths.items():
        _assert_review(
            path,
            spec=spec,
            root=root,
            kind="art" if label == "art" else "pet",
            tier=(
                "smoke"
                if label == "pet_smoke"
                else "release" if label == "pet_release" else None
            ),
        )
        evaluation = _read_object(path, f"{label} evaluation")
        if evaluation.get("design_id") != target["design_id"] or evaluation.get(
            "product_profile_id"
        ) != target["product_profile_id"]:
            raise ScalingWorkflowError(
                f"{label} evaluation identity does not match workflow target: {path}"
            )
        if evaluation.get("hard_gates", {}).get("status") != "passed":
            raise ScalingWorkflowError(
                f"{label} evaluation hard gates did not pass: {path}"
            )
        warnings.extend(str(item) for item in evaluation.get("warnings", []))
        evidence[label] = {
            "path": str(path.resolve()),
            "sha256": sha256(path),
            "review_id": evaluation.get("review_id"),
        }
    if warnings and not accept_warnings:
        raise ScalingWorkflowError(
            "workflow review contains warnings that require explicit acceptance; "
            f"warnings={warnings!r}; rerun with --accept-warnings after review"
        )
    approval = {
        "schema_version": 1,
        "workflow_id": spec["workflow_id"],
        "gate": gate,
        "reviewed_at": utc_now(),
        "reviewed_by": reviewer,
        "notes": review_notes,
        "accepted_warnings": warnings if accept_warnings else [],
        "evidence": evidence,
    }
    output = root / "approvals" / f"{gate}.json"
    if output.exists():
        existing = _read_object(output, f"{gate} workflow approval")
        comparable = dict(approval)
        comparable["reviewed_at"] = existing.get("reviewed_at")
        if existing == comparable:
            return output
        raise ScalingWorkflowError(
            f"workflow approval is immutable and already exists: {output}; "
            "create a successor workflow for a different review decision"
        )
    atomic_json(output, approval)
    return output


def workflow_status(*, spec_path: Path) -> dict[str, Any]:
    """Return rebuildable navigation state without creating approval evidence."""
    root, spec = load_workflow_spec(spec_path)
    target = spec["target"]
    product = (
        (root / Path(spec["authoring_root"]).expanduser()).resolve()
        / target["design_id"]
        / target["product_profile_id"]
    )
    ids = spec["ids"]
    paths = {
        "art_experiment": product / "experiments" / "art" / ids["art_experiment"],
        "art_review": product / "reviews" / "art" / ids["art_review"] / "evaluation.json",
        "pet_experiment": product / "experiments" / "pet" / ids["pet_experiment"],
        "smoke_review": product / "reviews" / "pet" / ids["smoke_review"] / "evaluation.json",
        "release_review": product / "reviews" / "pet" / ids["release_review"] / "evaluation.json",
        "candidates_approval": root / "approvals" / "candidates.json",
        "release_approval": root / "approvals" / "release.json",
    }
    exists = {key: path.exists() for key, path in paths.items()}
    smoke_enabled = spec["execution"]["smoke_enabled"]
    if not exists["art_review"] or (smoke_enabled and not exists["smoke_review"]):
        next_action = (
            "plan and run the smoke-review checkpoint"
            if smoke_enabled
            else "plan and run the art-review checkpoint"
        )
    elif not exists["candidates_approval"]:
        next_action = (
            "inspect art/smoke evidence and approve the candidates gate"
            if smoke_enabled
            else "inspect art evidence and approve the candidates gate"
        )
    elif not exists["release_review"]:
        next_action = "plan and run the release-review checkpoint"
    elif not exists["release_approval"]:
        next_action = "inspect release evidence and approve the release gate"
    else:
        next_action = (
            "record ordinary art/pet decisions, then continue with deterministic "
            "layout proposal and the existing graduation workflow"
        )
    return {
        "schema_version": 1,
        "workflow_id": spec["workflow_id"],
        "scenario": spec["scenario"],
        "target": target,
        "paths": {key: str(path) for key, path in paths.items()},
        "exists": exists,
        "next_action": next_action,
        "warnings": spec.get("warnings", []),
    }


def _attempt_succeeded(path: Path) -> bool:
    if not (path / "run.json").is_file():
        return False
    return _read_object(path / "run.json", "attempt record").get("status") == "succeeded"


def _assert_art_attempt(attempt: Path, experiment: Path) -> None:
    record = _read_object(attempt / "run.json", "art attempt record")
    experiment_record = _read_object(
        experiment / "experiment.json", "art experiment record"
    )
    expected = {
        "attempt_id": attempt.name,
        "experiment_id": experiment.name,
        "kind": "art",
        "status": "succeeded",
        "experiment_sha256": sha256(experiment / "experiment.json"),
        "resolved_generation": experiment_record.get("generation"),
    }
    actual = {key: record.get(key) for key in expected}
    output = attempt / "outputs" / "art.png"
    descriptor = next(
        (
            item
            for item in record.get("outputs", [])
            if isinstance(item, dict) and item.get("path") == "outputs/art.png"
        ),
        None,
    )
    output_valid = (
        output.is_file()
        and isinstance(descriptor, dict)
        and descriptor.get("sha256") == sha256(output)
        and descriptor.get("bytes") == output.stat().st_size
    )
    if actual != expected or not output_valid:
        raise ScalingWorkflowError(
            f"existing art attempt conflicts with workflow plan; attempt={attempt}; "
            f"expected_record={expected!r}; actual_record={actual!r}; "
            f"output_inventory_valid={output_valid!r}"
        )


def _assert_experiment(
    experiment: Path,
    *,
    spec: dict[str, Any],
    root: Path,
    kind: str,
) -> None:
    meta = _read_object(experiment / "experiment.json", "experiment")
    generation = spec["generation"][kind]
    expected = {
        "kind": kind,
        "design_id": spec["target"]["design_id"],
        "product_profile_id": spec["target"]["product_profile_id"],
        "provider": generation["provider"],
        "model": generation["model"],
    }
    actual = {
        "kind": meta.get("kind"),
        "design_id": meta.get("design_id"),
        "product_profile_id": meta.get("product_profile_id"),
        "provider": meta.get("generation", {}).get("provider"),
        "model": meta.get("generation", {}).get("model"),
    }
    if actual != expected:
        raise ScalingWorkflowError(
            f"existing experiment conflicts with workflow plan; experiment={experiment}; "
            f"expected={expected!r}; actual={actual!r}"
        )
    expected_quality = generation.get("quality")
    actual_quality = meta.get("generation", {}).get("parameters", {}).get("quality")
    if expected_quality is not None and actual_quality != expected_quality:
        raise ScalingWorkflowError(
            f"existing experiment quality conflicts with workflow plan; experiment={experiment}; "
            f"expected={expected_quality!r}; actual={actual_quality!r}"
        )
    profile_descriptor = meta.get("inputs", {}).get("product_profile")
    expected_profile_hash = sha256(
        _path(root, spec["target"]["product_profile"], "product profile")
    )
    if (
        not isinstance(profile_descriptor, dict)
        or profile_descriptor.get("sha256") != expected_profile_hash
    ):
        raise ScalingWorkflowError(
            f"existing experiment product profile conflicts with workflow plan: {experiment}"
        )
    input_key = "art_prompt" if kind == "art" else "pet_prompt"
    expected_prompt = spec["inputs"].get(input_key)
    prompt_descriptor = meta.get("inputs", {}).get("prompt")
    if expected_prompt is not None:
        expected_hash = sha256(_path(root, expected_prompt, input_key))
        if not isinstance(prompt_descriptor, dict) or prompt_descriptor.get("sha256") != expected_hash:
            raise ScalingWorkflowError(
                f"existing experiment prompt conflicts with workflow plan: {experiment}"
            )
    elif prompt_descriptor is not None:
        raise ScalingWorkflowError(
            f"empty-canvas workflow found an unexpected experiment prompt: {experiment}"
        )
    if kind == "pet":
        expected_variables = (
            {"pet_name": spec["personalization"]["representative_pet_name"]}
            if spec["personalization"]["name_mode"] == "embedded-in-pet"
            else None
        )
        actual_variables = meta.get("generation", {}).get("prompt_variables")
        if actual_variables != expected_variables:
            raise ScalingWorkflowError(
                f"existing pet experiment name variables conflict with workflow plan; "
                f"experiment={experiment}; expected={expected_variables!r}; "
                f"actual={actual_variables!r}"
            )
    expected_reference_hashes = [
        sha256(_path(root, value, "reference"))
        for value in spec["inputs"]["references"]
    ]
    descriptors = meta.get("inputs", {}).get(
        "layout_references" if generation["provider"] == "local" else "references",
        [],
    )
    actual_reference_hashes = [item.get("sha256") for item in descriptors]
    if actual_reference_hashes != expected_reference_hashes:
        raise ScalingWorkflowError(
            f"existing experiment references conflict with workflow plan; experiment={experiment}; "
            f"expected={expected_reference_hashes!r}; actual={actual_reference_hashes!r}"
        )


def _assert_review(
    review_path: Path,
    *,
    spec: dict[str, Any],
    root: Path,
    kind: str,
    tier: str | None = None,
) -> None:
    evaluation = _read_object(review_path, f"{kind} workflow review")
    expected_identity = {
        "kind": kind,
        "design_id": spec["target"]["design_id"],
        "product_profile_id": spec["target"]["product_profile_id"],
        "evaluation_protocol_sha256": sha256(
            _path(root, spec["inputs"]["evaluation_protocol"], "evaluation protocol")
        ),
    }
    actual_identity = {key: evaluation.get(key) for key in expected_identity}
    if actual_identity != expected_identity:
        raise ScalingWorkflowError(
            f"existing review conflicts with workflow plan; review={review_path}; "
            f"expected={expected_identity!r}; actual={actual_identity!r}"
        )
    product = (
        (root / Path(spec["authoring_root"]).expanduser()).resolve()
        / spec["target"]["design_id"]
        / spec["target"]["product_profile_id"]
    ).resolve()
    for descriptor in evaluation.get("review_artifacts", []):
        if not isinstance(descriptor, dict):
            raise ScalingWorkflowError(
                f"existing review has an invalid artifact descriptor: {review_path}"
            )
        artifact = (product / str(descriptor.get("path", ""))).resolve()
        if not artifact.is_relative_to(product) or not artifact.is_file():
            raise ScalingWorkflowError(
                f"existing review artifact is missing or escapes the product: {artifact}"
            )
        if sha256(artifact) != descriptor.get("sha256"):
            raise ScalingWorkflowError(
                f"existing review artifact hash mismatch: {artifact}"
            )
    expected_experiment = (
        spec["ids"]["art_experiment"]
        if kind == "art"
        else spec["ids"]["pet_experiment"]
    )
    candidate_ids = [
        item.get("experiment_id")
        for item in evaluation.get("candidates", [])
        if isinstance(item, dict)
    ]
    if candidate_ids != [expected_experiment]:
        raise ScalingWorkflowError(
            f"existing review candidates conflict with workflow plan; review={review_path}; "
            f"expected={[expected_experiment]!r}; actual={candidate_ids!r}"
        )
    if tier is None:
        if evaluation.get("attempt_id_prefix") is not None:
            raise ScalingWorkflowError(
                f"art workflow review has unexpected attempt prefix: {review_path}"
            )
        return
    fixture_entry = spec["inputs"]["fixture_sets"][tier]
    expected_fixture = {
        "fixture_set_sha256": sha256(
            _path(root, fixture_entry["manifest"], f"{tier} fixture set")
        ),
        "attempt_id_prefix": f"{tier}-",
        "selection_sha256": sha256(
            _path(root, fixture_entry["selection"], f"{tier} fixture selection")
        ),
    }
    actual_fixture = {
        "fixture_set_sha256": evaluation.get("fixture_set_sha256"),
        "attempt_id_prefix": evaluation.get("attempt_id_prefix"),
        "selection_sha256": evaluation.get("fixture_selection", {}).get(
            "config_sha256"
        ),
    }
    if actual_fixture != expected_fixture:
        raise ScalingWorkflowError(
            f"existing review fixture binding conflicts with workflow plan; review={review_path}; "
            f"expected={expected_fixture!r}; actual={actual_fixture!r}"
        )


def _verify_plan(plan_path: Path) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    resolved = _resolve_file(plan_path, "workflow plan")
    plan = _read_object(resolved, "workflow plan")
    if plan.get("schema_version") != 1:
        raise ScalingWorkflowError(f"workflow plan must use schema_version 1: {resolved}")
    specification = plan.get("specification")
    if not isinstance(specification, dict):
        raise ScalingWorkflowError(f"workflow plan has no specification binding: {resolved}")
    spec_path = _resolve_file(Path(str(specification.get("path", ""))), "workflow specification")
    if sha256(spec_path) != specification.get("sha256"):
        raise ScalingWorkflowError(
            f"workflow specification changed after planning: {spec_path}; create a new plan"
        )
    root, spec = load_workflow_spec(spec_path)
    actual_inventory = _input_inventory(root)
    if actual_inventory != plan.get("input_inventory"):
        raise ScalingWorkflowError(
            f"workflow inputs changed after planning: {root / 'inputs'}; create and review a new plan"
        )
    gate_evidence = plan.get("quality_gate_evidence", {})
    if not isinstance(gate_evidence, dict):
        raise ScalingWorkflowError(
            f"workflow plan quality_gate_evidence must be an object: {resolved}"
        )
    for gate, descriptor in gate_evidence.items():
        if gate not in REVIEW_GATES or not isinstance(descriptor, dict):
            raise ScalingWorkflowError(
                f"workflow plan contains unsupported quality-gate evidence: {gate!r}"
            )
        approval = _resolve_file(
            _path(root, descriptor.get("path"), f"{gate} approval"),
            f"{gate} approval",
        )
        if sha256(approval) != descriptor.get("sha256"):
            raise ScalingWorkflowError(
                f"workflow approval changed after planning: {approval}; create a new plan"
            )
    checkpoint = plan.get("checkpoint")
    if checkpoint not in CHECKPOINTS:
        raise ScalingWorkflowError(
            f"workflow plan has unsupported checkpoint: {checkpoint!r}"
        )
    expected_tasks = _planned_tasks(root, spec, checkpoint)
    expected_plan_id = (
        f"{spec['workflow_id']}--{checkpoint}--"
        f"{_plan_fingerprint(spec_path, actual_inventory, checkpoint, gate_evidence)[:12]}"
    )
    expected_fields = {
        "plan_id": expected_plan_id,
        "workflow_id": spec["workflow_id"],
        "scenario": spec["scenario"],
        "source": spec.get("source"),
        "target": spec["target"],
        "tasks": expected_tasks,
        "estimated_paid_calls": sum(
            int(task["paid_calls"]) for task in expected_tasks
        ),
        "resolved_ids": spec["ids"],
    }
    actual_fields = {key: plan.get(key) for key in expected_fields}
    if actual_fields != expected_fields:
        raise ScalingWorkflowError(
            f"workflow plan fields do not match its bound specification; plan={resolved}; "
            f"expected={expected_fields!r}; actual={actual_fields!r}"
        )
    return resolved, plan, root, spec


def run_workflow(
    *,
    plan_path: Path,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Execute a reviewed plan and reconcile already-completed immutable tasks."""
    progress = progress or (lambda message: None)
    resolved_plan, plan, root, spec = _verify_plan(plan_path)
    authoring_root = (root / Path(spec["authoring_root"]).expanduser()).resolve()
    target = spec["target"]
    product = authoring_root / target["design_id"] / target["product_profile_id"]
    ids = spec["ids"]
    art_experiment = product / "experiments" / "art" / ids["art_experiment"]
    pet_experiment = product / "experiments" / "pet" / ids["pet_experiment"]
    art_attempt = art_experiment / "attempts" / ids["art_attempt"]
    art_review = product / "reviews" / "art" / ids["art_review"] / "evaluation.json"
    smoke_review = product / "reviews" / "pet" / ids["smoke_review"] / "evaluation.json"
    release_review = product / "reviews" / "pet" / ids["release_review"] / "evaluation.json"
    state_path = root / "runs" / f"{plan['plan_id']}.json"
    state: dict[str, Any] = {
        "schema_version": 1,
        "plan_id": plan["plan_id"],
        "plan_sha256": sha256(resolved_plan),
        "status": "running",
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "tasks": [],
    }
    if state_path.is_file():
        previous = _read_object(state_path, "workflow run state")
        if previous.get("plan_sha256") != state["plan_sha256"]:
            raise ScalingWorkflowError(
                f"workflow run state belongs to different plan bytes: {state_path}"
            )
        state["started_at"] = previous.get("started_at", state["started_at"])
        previous_tasks = previous.get("tasks", [])
        if isinstance(previous_tasks, list):
            state["tasks"] = previous_tasks

    def record(task_id: str, status: str, message: str) -> None:
        state["updated_at"] = utc_now()
        state["tasks"].append(
            {"task_id": task_id, "status": status, "message": message, "at": state["updated_at"]}
        )
        atomic_json(state_path, state)
        progress(message)

    references = [
        _path(root, value, "reference") for value in spec["inputs"]["references"]
    ]
    profile = _path(root, target["product_profile"], "product profile")
    source_revision = (
        int(spec["source"]["bundle_revision"]) if spec.get("source") is not None else None
    )
    embedded_name = (
        spec["personalization"].get("representative_pet_name")
        if spec["personalization"]["name_mode"] == "embedded-in-pet"
        else None
    )
    try:
        for task in plan["tasks"]:
            task_id = task["task_id"]
            if task_id == "create-art-experiment":
                if art_experiment.is_dir():
                    _assert_experiment(art_experiment, spec=spec, root=root, kind="art")
                    record(task_id, "reconciled", f"Reused matching art experiment {art_experiment}")
                    continue
                progress(f"Creating art experiment {ids['art_experiment']}")
                art_generation = spec["generation"]["art"]
                create_experiment(
                    kind="art",
                    experiment_id=ids["art_experiment"],
                    design_id=target["design_id"],
                    product_profile=profile,
                    authoring_root=authoring_root,
                    references=references,
                    prompt_file=(
                        None
                        if spec["inputs"]["art_prompt"] is None
                        else _path(root, spec["inputs"]["art_prompt"], "art prompt")
                    ),
                    empty_canvas=bool(spec["inputs"]["art_empty_canvas"]),
                    provider=(
                        None if art_generation["provider"] == "local" else art_generation["provider"]
                    ),
                    model=(
                        None if art_generation["provider"] == "local" else art_generation["model"]
                    ),
                    quality=art_generation.get("quality") or "high",
                    art_attempt=None,
                    pet_attempt=None,
                    font_catalogs=[],
                    parent_experiment_id=None,
                    base_bundle_revision=source_revision,
                    created_by=f"workflow:{spec['workflow_id']}",
                )
                record(task_id, "succeeded", f"Created art experiment {art_experiment}")
            elif task_id == "run-art-attempt":
                if art_attempt.exists():
                    _assert_art_attempt(art_attempt, art_experiment)
                    record(task_id, "reconciled", f"Reused successful art attempt {art_attempt}")
                    continue
                if art_attempt.with_name(art_attempt.name + ".partial").exists():
                    raise ScalingWorkflowError(
                        f"art attempt has an interrupted partial record: {art_attempt}.partial; inspect provider submission before retrying"
                    )
                run_attempt(
                    experiment=art_experiment,
                    attempt_id=ids["art_attempt"],
                    pet_image=None,
                )
                record(task_id, "succeeded", f"Generated art attempt {art_attempt}")
            elif task_id == "compare-art":
                if art_review.is_file():
                    _assert_review(
                        art_review, spec=spec, root=root, kind="art"
                    )
                    record(task_id, "reconciled", f"Reused art review {art_review}")
                    continue
                compare(
                    kind="art",
                    review_id=ids["art_review"],
                    authoring_product=product,
                    experiments=[ids["art_experiment"]],
                    evaluation_protocol=_path(
                        root, spec["inputs"]["evaluation_protocol"], "evaluation protocol"
                    ),
                    fixture_set=None,
                    art_attempt=None,
                    pet_experiment=None,
                    layout_attempt=None,
                    base_bundle_revision=source_revision,
                )
                record(task_id, "succeeded", f"Created art review {art_review}")
            elif task_id == "create-pet-experiment":
                if pet_experiment.is_dir():
                    _assert_experiment(pet_experiment, spec=spec, root=root, kind="pet")
                    record(task_id, "reconciled", f"Reused matching pet experiment {pet_experiment}")
                    continue
                pet_generation = spec["generation"]["pet"]
                create_experiment(
                    kind="pet",
                    experiment_id=ids["pet_experiment"],
                    design_id=target["design_id"],
                    product_profile=profile,
                    authoring_root=authoring_root,
                    references=references,
                    prompt_file=_path(root, spec["inputs"]["pet_prompt"], "pet prompt"),
                    provider=pet_generation["provider"],
                    model=pet_generation["model"],
                    quality=pet_generation["quality"],
                    pet_name=embedded_name,
                    art_attempt=None,
                    pet_attempt=None,
                    font_catalogs=[],
                    parent_experiment_id=None,
                    base_bundle_revision=source_revision,
                    created_by=f"workflow:{spec['workflow_id']}",
                )
                record(task_id, "succeeded", f"Created pet experiment {pet_experiment}")
            elif task_id.startswith("benchmark-pet-"):
                tier = task_id.rsplit("-", 1)[-1]
                entry = spec["inputs"]["fixture_sets"][tier]
                fixture_set = _path(root, entry["manifest"], f"{tier} fixture set")
                selection = _path(root, entry["selection"], f"{tier} fixture selection")
                selected = load_fixture_selection(load_fixture_set(fixture_set), selection)
                expected_attempts = [
                    pet_experiment / "attempts" / f"{tier}-{fixture.id}-0001"
                    for fixture in selected
                ]
                all_preexisting = bool(expected_attempts) and all(
                    _attempt_succeeded(path) for path in expected_attempts
                )
                partials = [
                    path.with_name(path.name + ".partial")
                    for path in expected_attempts
                    if path.with_name(path.name + ".partial").exists()
                ]
                if partials:
                    raise ScalingWorkflowError(
                        "pet benchmark contains interrupted partial attempts; inspect provider submissions before retrying: "
                        + ", ".join(str(path) for path in partials)
                    )
                completed = benchmark(
                    experiment=pet_experiment,
                    fixture_set=fixture_set,
                    evaluation_protocol=_path(
                        root, spec["inputs"]["evaluation_protocol"], "evaluation protocol"
                    ),
                    attempts_per_fixture=None,
                    attempt_id_prefix=tier,
                    fixture_selection=selection,
                    pet_name=embedded_name,
                    resume_existing=True,
                )
                if len(completed) != len(expected_attempts):
                    progress(
                        f"WARNING: {tier} benchmark completed with "
                        f"{len(completed)}/{len(expected_attempts)} successful attempts; "
                        "the comparison will preserve the coverage gap for manual review"
                    )
                status = "reconciled" if all_preexisting else "succeeded"
                record(task_id, status, f"Completed {tier} pet benchmark")
            elif task_id.startswith("compare-pet-"):
                tier = task_id.rsplit("-", 1)[-1]
                review_id = ids[f"{tier}_review"]
                review_path = product / "reviews" / "pet" / review_id / "evaluation.json"
                if review_path.is_file():
                    _assert_review(
                        review_path,
                        spec=spec,
                        root=root,
                        kind="pet",
                        tier=tier,
                    )
                    record(task_id, "reconciled", f"Reused pet review {review_path}")
                    continue
                entry = spec["inputs"]["fixture_sets"][tier]
                compare(
                    kind="pet",
                    review_id=review_id,
                    authoring_product=product,
                    experiments=[ids["pet_experiment"]],
                    evaluation_protocol=_path(
                        root, spec["inputs"]["evaluation_protocol"], "evaluation protocol"
                    ),
                    fixture_set=_path(root, entry["manifest"], f"{tier} fixture set"),
                    art_attempt=None,
                    pet_experiment=None,
                    layout_attempt=None,
                    base_bundle_revision=source_revision,
                    attempt_prefix=f"{tier}-",
                    fixture_selection=_path(
                        root, entry["selection"], f"{tier} fixture selection"
                    ),
                )
                record(task_id, "succeeded", f"Created {tier} pet review {review_path}")
            else:
                raise ScalingWorkflowError(f"workflow plan contains unsupported task: {task_id}")
        state.update(status="succeeded", completed_at=utc_now(), updated_at=utc_now())
        atomic_json(state_path, state)
        return state_path
    except Exception as exc:
        state.update(
            status="failed",
            completed_at=utc_now(),
            updated_at=utc_now(),
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        atomic_json(state_path, state)
        raise

