from __future__ import annotations

import os
import re
import shutil
import warnings
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, ImageOps

from . import __version__
from .artifact_io import atomic_json, mismatch, read_json, sha256, utc_now
from .bundle import (
    authoring_prompt_contract,
    BundleError,
    canonical_reference_paths,
    catalog_template_id,
    media_type,
    prompt_contract,
    validate_layout_pair,
    validate_raster,
    validate_utc_timestamp,
)
from .config import ConfigError, load_layout
from .font_license import FontLicenseError, resolve_ofl_license
from .generation_contract import provider_request_parameters
from .image_size import is_gpt_image_2
from .personalization import (
    PersonalizationError,
    pet_name_policy,
    validate_pet_name,
)
from .product_profile import ProductProfileError, load_product_profile
from .renderer import render_to_files


def _json(path: Path) -> dict[str, Any]:
    return read_json(
        path,
        label="bundle artifact",
        error_type=BundleError,
        require_object=True,
        correction="fix the file or regenerate it from the selected authoring artifacts.",
    )


def _attempt_experiment(
    attempt: Path, record: dict[str, Any], expected_kind: str
) -> tuple[Path, dict[str, Any]]:
    attempt = attempt.expanduser().resolve()
    if attempt.parent.name != "attempts":
        raise BundleError(f"selected attempt has an invalid path: {attempt}")
    experiment = attempt.parent.parent
    metadata = _json(experiment / "experiment.json")
    expected = {
        "attempt_id": attempt.name,
        "experiment_id": experiment.name,
        "kind": expected_kind,
    }
    actual = {
        "attempt_id": record.get("attempt_id"),
        "experiment_id": record.get("experiment_id"),
        "kind": record.get("kind"),
    }
    if actual != expected or metadata.get("kind") != expected_kind:
        raise BundleError(
            mismatch(
                f"selected attempt identity ({attempt})",
                expected={**expected, "experiment_kind": expected_kind},
                actual={**actual, "experiment_kind": metadata.get("kind")},
            )
        )
    return experiment, metadata


def _asset(path: Path, root: Path) -> dict[str, Any]:
    value: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "media_type": media_type(path),
    }
    if path.suffix.lower() == ".png":
        with warnings.catch_warnings():
            if path.relative_to(root).as_posix() == "print/art.png":
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                value["width"], value["height"] = image.size
    return value


def _next_revision(template_root: Path, product_root: Path, template_id: str) -> int:
    """Allocate after both staged bundles and retained publication receipts."""
    revisions: list[int] = []
    if template_root.exists():
        for child in template_root.iterdir():
            if (
                child.is_dir()
                and len(child.name) == 7
                and child.name.startswith("v")
                and child.name[1:].isdigit()
            ):
                revisions.append(int(child.name[1:]))
    for receipt_path in sorted(
        product_root.glob("graduations/*/publications/*.json")
    ):
        receipt = _json(receipt_path)
        revision = receipt.get("bundle_revision")
        release_id = receipt.get("release_id")
        expected_name = (
            f"{release_id}--v{revision:06d}.json"
            if isinstance(release_id, str)
            and isinstance(revision, int)
            and not isinstance(revision, bool)
            and revision > 0
            else None
        )
        if receipt.get("template_id") != template_id or receipt_path.name != expected_name:
            raise BundleError(
                "publication receipt identity is inconsistent; "
                f"receipt={receipt_path.resolve()}; expected_template_id={template_id!r}; "
                f"actual_template_id={receipt.get('template_id')!r}; "
                f"bundle_revision={revision!r}"
            )
        revisions.append(revision)
    return max(revisions, default=0) + 1


def _validate_production_bundle(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    manifest_path = root / "bundle.json"
    manifest = _json(manifest_path)
    required_top_level = {
        "schema_version",
        "template_id",
        "design_id",
        "product_profile_id",
        "bundle_revision",
        "created_at",
        "runtime",
        "renderer",
        "personalization",
        "provenance",
        "prompts",
        "assets",
    }
    if set(manifest) != required_top_level or manifest.get("schema_version") != 1:
        raise BundleError("bundle manifest must match the closed bundle-v1 contract")
    revision = manifest.get("bundle_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise BundleError("bundle_revision must be a positive integer")
    validate_utc_timestamp(manifest.get("created_at"), "created_at")
    design_id = manifest.get("design_id")
    profile_id = manifest.get("product_profile_id")
    if not isinstance(design_id, str) or not isinstance(profile_id, str):
        raise BundleError("design_id and product_profile_id must be strings")
    expected_id = catalog_template_id(design_id, profile_id)
    if (
        manifest.get("template_id") != expected_id
        or root.parent.name != expected_id
        or root.name != f"v{revision:06d}"
    ):
        raise BundleError(
            mismatch(
                "bundle path/manifest identity",
                expected={
                    "template_id": expected_id,
                    "parent_directory": expected_id,
                    "revision_directory": f"v{revision:06d}",
                },
                actual={
                    "template_id": manifest.get("template_id"),
                    "parent_directory": root.parent.name,
                    "revision_directory": root.name,
                },
            )
        )

    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise BundleError("bundle assets must be a nonempty array")
    seen: set[str] = set()
    for item in assets:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise BundleError("bundle asset entries must contain a string path")
        relative_text = item["path"]
        relative = PurePosixPath(relative_text)
        if (
            relative.is_absolute()
            or relative_text != relative.as_posix()
            or "\\" in relative_text
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise BundleError(f"invalid bundle-relative asset path: {relative_text}")
        expected_keys = {"path", "sha256", "bytes", "media_type"}
        if relative.suffix.lower() == ".png":
            expected_keys.update({"width", "height"})
        if set(item) != expected_keys:
            raise BundleError(
                f"asset metadata has unsupported or missing fields: {relative_text}"
            )
        if relative_text in seen:
            raise BundleError(f"duplicate bundle asset: {relative_text}")
        resolved = root.joinpath(*relative.parts)
        digest = item.get("sha256")
        if not resolved.is_file() or resolved.is_symlink():
            raise BundleError(
                mismatch(
                    f"bundle asset file {relative_text}",
                    expected={"path": str(resolved), "regular_file": True},
                    actual={
                        "exists": resolved.exists(),
                        "is_file": resolved.is_file(),
                        "is_symlink": resolved.is_symlink(),
                    },
                )
            )
        actual_metadata = {
            "sha256": sha256(resolved),
            "bytes": resolved.stat().st_size,
            "media_type": media_type(resolved),
        }
        expected_metadata = {
            "sha256": digest,
            "bytes": item.get("bytes"),
            "media_type": item.get("media_type"),
        }
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or actual_metadata != expected_metadata
        ):
            raise BundleError(
                mismatch(
                    f"bundle asset metadata mismatch for {relative_text}",
                    expected=expected_metadata,
                    actual=actual_metadata,
                )
            )
        if relative.suffix.lower() == ".png":
            size = validate_raster(
                resolved,
                relative_text,
                require_png=True,
                allow_large=relative_text == "print/art.png",
            )
            if (item.get("width"), item.get("height")) != size:
                raise BundleError(
                    mismatch(
                        f"bundle asset dimensions {relative_text}",
                        expected=(item.get("width"), item.get("height")),
                        actual=size,
                    )
                )
        seen.add(relative_text)
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "bundle.json"
    }
    if seen != actual_files:
        raise BundleError(
            mismatch(
                "bundle asset inventory must inventory every file exactly once",
                expected=sorted(seen),
                actual=sorted(actual_files),
            )
        )

    try:
        profile = load_product_profile(root / "product-profile.json")
        preview = load_layout(root)
        print_layout = load_layout(
            root,
            layout_path=root / "layout-print.json",
            allow_large_art=True,
        )
    except (ProductProfileError, ConfigError) as exc:
        raise BundleError(str(exc)) from exc
    if profile.profile_id != profile_id:
        raise BundleError(
            mismatch(
                "product profile identity",
                expected=profile_id,
                actual=profile.profile_id,
            )
        )
    if (
        preview.art_relative != "art.png"
        or print_layout.art_relative != "print/art.png"
    ):
        raise BundleError("bundle layouts must use canonical preview and print art paths")
    if preview.schema_version != 2 or print_layout.schema_version != 2:
        raise BundleError("published layouts must use composition schema v2")
    if (preview.canvas_width, preview.canvas_height) != (
        profile.preview_art_size.width,
        profile.preview_art_size.height,
    ):
        raise BundleError(
            mismatch(
                "preview layout canvas",
                expected=(
                    profile.preview_art_size.width,
                    profile.preview_art_size.height,
                ),
                actual=(preview.canvas_width, preview.canvas_height),
            )
        )
    if (print_layout.canvas_width, print_layout.canvas_height) != (
        profile.print_size.width,
        profile.print_size.height,
    ):
        raise BundleError(
            mismatch(
                "print layout canvas",
                expected=(profile.print_size.width, profile.print_size.height),
                actual=(print_layout.canvas_width, print_layout.canvas_height),
            )
        )
    validate_layout_pair(preview, print_layout)

    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise BundleError("runtime must be an object")
    if set(runtime) != {
        "provider",
        "model",
        "transport",
        "prompt",
        "reference_assets",
        "input_image_order",
        "request_parameters",
        "output",
        "normalization",
    }:
        raise BundleError("runtime must match the closed bundle-v1 contract")
    provider = runtime.get("provider")
    model = runtime.get("model")
    transport = runtime.get("transport")
    expected_transport = {"openai": "images.edits"}
    if provider not in expected_transport or transport != expected_transport[provider]:
        raise BundleError("runtime provider and transport are unsupported or inconsistent")
    if not isinstance(model, str) or not model:
        raise BundleError("runtime model must be a nonempty string")
    if not model.startswith("gpt-image-"):
        raise BundleError("OpenAI runtime model must be a gpt-image model")
    request_parameters = runtime.get("request_parameters")
    if not isinstance(request_parameters, dict):
        raise BundleError("runtime.request_parameters must be an object")
    required_request = {"quality", "size", "background", "output_format", "n"}
    if not is_gpt_image_2(model):
        required_request.add("input_fidelity")
    if (
        set(request_parameters) != required_request
        or request_parameters.get("quality") not in {"low", "medium", "high", "auto"}
        or request_parameters.get("size")
        != f"{profile.preview_pet_size.width}x{profile.preview_pet_size.height}"
        or request_parameters.get("background") != "transparent"
        or request_parameters.get("output_format") != "png"
        or request_parameters.get("n") != 1
        or (
            "input_fidelity" in required_request
            and request_parameters.get("input_fidelity") != "high"
        )
    ):
        raise BundleError("OpenAI runtime request parameters are unsupported")
    prompts = manifest.get("prompts")
    if not isinstance(prompts, dict) or set(prompts) != {"art_template", "pet_transform"}:
        raise BundleError("prompts must identify exactly the art and pet prompt assets")
    art_prompt_name = prompts.get("art_template")
    pet_prompt_name = prompts.get("pet_transform")
    if not isinstance(art_prompt_name, str) or not isinstance(pet_prompt_name, str):
        raise BundleError("prompt paths must be strings")
    art_match = re.fullmatch(r"art-template-(gpt|gemini)\.md", art_prompt_name)
    if art_match is None:
        raise BundleError("art template prompt has an invalid provider-qualified path")
    art_provider = "openai" if art_match.group(1) == "gpt" else "gemini"
    prompt_contract(root / art_prompt_name, "art-template", art_provider)
    prompt_contract(root / pet_prompt_name, "pet-transform", str(provider))
    if runtime.get("prompt") != pet_prompt_name:
        raise BundleError(
            mismatch(
                "runtime.prompt",
                expected=pet_prompt_name,
                actual=runtime.get("prompt"),
            )
        )

    refs = runtime.get("reference_assets")
    if not isinstance(refs, list):
        raise BundleError("runtime reference_assets must be an ordered array")
    if not 1 <= len(refs) <= 4:
        raise BundleError(
            "runtime reference_assets must contain one to four finished-design references"
        )
    if refs != canonical_reference_paths(len(refs)):
        raise BundleError("runtime reference_assets must use canonical ordered paths")
    expected_order = ["user_pet"] + [
        f"reference_{index}" for index in range(1, len(refs) + 1)
    ]
    if runtime.get("input_image_order") != expected_order:
        raise BundleError(
            mismatch(
                "runtime.input_image_order must put user_pet first",
                expected=expected_order,
                actual=runtime.get("input_image_order"),
            )
        )
    output = runtime.get("output")
    if output != {
        "format": "png",
        "background": "transparent",
        "width": profile.preview_pet_size.width,
        "height": profile.preview_pet_size.height,
    }:
        raise BundleError(
            mismatch(
                "runtime.output",
                expected={
                    "format": "png",
                    "background": "transparent",
                    "width": profile.preview_pet_size.width,
                    "height": profile.preview_pet_size.height,
                },
                actual=output,
            )
        )
    if runtime.get("normalization") != {
        "policy": "transparent-rgba-contain",
        "version": 1,
        "alpha_failure": "reject",
    }:
        raise BundleError("runtime normalization policy is unsupported")
    expected_renderer = {
        "layout_schema_version": 2,
        "pet_fit": "contain-visible-alpha",
        "pet_anchor": "bottom-center",
        "name_mode": "layout-text" if preview.has_name else "embedded-in-pet",
        "version": 2,
    }
    if preview.has_name:
        expected_renderer["name_fit"] = (
            "nominal-size-shrink-only-visible-ink-contain"
        )
    if manifest.get("renderer") != expected_renderer:
        raise BundleError("renderer semantics are unsupported")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise BundleError("provenance must be an object")
    required_provenance = {
        "selection_id",
        "selection_sha256",
        "art_attempt_id",
        "pet_experiment_id",
        "representative_pet_attempt_id",
        "representative_pet_sha256",
        "qa_fixture",
        "layout_attempt_id",
        "component_evaluations",
        "compatibility_evaluation",
        "print_candidate",
        "selected",
        "print_derivation",
        "generator",
    }
    if set(provenance) != required_provenance:
        raise BundleError(
            mismatch(
                "provenance fields",
                expected=sorted(required_provenance),
                actual=sorted(provenance),
            )
        )

    def require_nonempty(value: object, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise BundleError(f"{label} must be a nonempty string")
        return value

    def require_sha256(value: object, label: str) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise BundleError(f"{label} must be SHA-256 hex")
        return value

    for field in (
        "selection_id",
        "art_attempt_id",
        "pet_experiment_id",
        "representative_pet_attempt_id",
        "layout_attempt_id",
    ):
        require_nonempty(provenance.get(field), f"provenance.{field}")
    require_sha256(provenance.get("selection_sha256"), "provenance.selection_sha256")
    require_sha256(
        provenance.get("representative_pet_sha256"),
        "provenance.representative_pet_sha256",
    )

    generator = provenance.get("generator")
    if (
        not isinstance(generator, dict)
        or set(generator) != {"version"}
        or not isinstance(generator.get("version"), str)
        or not generator["version"]
    ):
        raise BundleError("provenance.generator must contain one nonempty version")
    component_evaluations = provenance.get("component_evaluations")
    if (
        not isinstance(component_evaluations, dict)
        or set(component_evaluations) != {"art", "pet", "layout"}
    ):
        raise BundleError(
            "provenance.component_evaluations must contain art, pet, and layout"
        )
    def validate_evaluation(evidence: object, label: str) -> None:
        if (
            not isinstance(evidence, dict)
            or set(evidence) != {
                "evaluation_id",
                "evaluation_sha256",
                "status",
            }
            or not isinstance(evidence.get("evaluation_id"), str)
            or not evidence["evaluation_id"]
            or not isinstance(evidence.get("evaluation_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", evidence["evaluation_sha256"])
            is None
            or evidence.get("status") != "passed"
        ):
            raise BundleError(f"{label} is invalid")

    for kind, evidence in component_evaluations.items():
        validate_evaluation(evidence, f"provenance {kind} evaluation evidence")
    validate_evaluation(
        provenance.get("compatibility_evaluation"),
        "provenance compatibility evaluation evidence",
    )

    print_candidate = provenance.get("print_candidate")
    if not isinstance(print_candidate, dict) or set(print_candidate) != {
        "print_candidate_id",
        "print_candidate_sha256",
        "status",
    }:
        raise BundleError("provenance.print_candidate is invalid")
    require_nonempty(
        print_candidate.get("print_candidate_id"),
        "provenance.print_candidate.print_candidate_id",
    )
    require_sha256(
        print_candidate.get("print_candidate_sha256"),
        "provenance.print_candidate.print_candidate_sha256",
    )
    if print_candidate.get("status") != "passed":
        raise BundleError("provenance.print_candidate.status must be passed")

    selected = provenance.get("selected")
    selected_shapes = {
        "art": {"experiment_id", "attempt_id", "artifact_sha256"},
        "pet_runtime": {"experiment_id", "experiment_sha256"},
        "layout": {
            "experiment_id",
            "attempt_id",
            "layout_sha256",
            "font_sha256",
        },
    }
    if not isinstance(selected, dict) or set(selected) != set(selected_shapes):
        raise BundleError("provenance.selected must contain art, pet_runtime, and layout")
    for component, fields in selected_shapes.items():
        descriptor = selected.get(component)
        if not isinstance(descriptor, dict) or set(descriptor) != fields:
            raise BundleError(f"provenance.selected.{component} is invalid")
        for field, value in descriptor.items():
            if component == "layout" and field == "font_sha256":
                if preview.has_name:
                    require_sha256(value, f"provenance.selected.{component}.{field}")
                elif value is not None:
                    raise BundleError(
                        "provenance.selected.layout.font_sha256 must be null "
                        "when layout.name is absent"
                    )
                continue
            if field.endswith("sha256"):
                require_sha256(value, f"provenance.selected.{component}.{field}")
            else:
                require_nonempty(value, f"provenance.selected.{component}.{field}")

    print_derivation = provenance.get("print_derivation")
    if not isinstance(print_derivation, dict) or set(print_derivation) != {
        "mode",
        "print_candidate_id",
        "print_candidate_sha256",
        "backends",
        "template_source",
        "art_sha256",
        "layout_sha256",
    }:
        raise BundleError("provenance.print_derivation is invalid")
    if print_derivation.get("mode") != "selected-print-candidate":
        raise BundleError("provenance.print_derivation.mode is unsupported")
    for field in ("art_sha256", "layout_sha256", "print_candidate_sha256"):
        require_sha256(
            print_derivation.get(field), f"provenance.print_derivation.{field}"
        )
    require_nonempty(
        print_derivation.get("print_candidate_id"),
        "provenance.print_derivation.print_candidate_id",
    )
    if (
        print_derivation["print_candidate_id"] != print_candidate["print_candidate_id"]
        or print_derivation["print_candidate_sha256"]
        != print_candidate["print_candidate_sha256"]
    ):
        raise BundleError("provenance print candidate and print derivation disagree")
    backends = print_derivation.get("backends")
    if (
        not isinstance(backends, dict)
        or set(backends) != {"template", "pet"}
        or any(value not in {"deterministic", "bria"} for value in backends.values())
    ):
        raise BundleError("provenance.print_derivation.backends is unsupported")
    template_source = print_derivation.get("template_source")
    if not isinstance(template_source, dict):
        raise BundleError("provenance.print_derivation.template_source is invalid")
    if template_source.get("mode") == "generated":
        expected_template_source = {"mode"}
    elif template_source.get("mode") == "reused-print-candidate":
        expected_template_source = {"mode", "print_candidate_id", "manifest_sha256"}
        require_nonempty(
            template_source.get("print_candidate_id"),
            "provenance.print_derivation.template_source.print_candidate_id",
        )
        require_sha256(
            template_source.get("manifest_sha256"),
            "provenance.print_derivation.template_source.manifest_sha256",
        )
    else:
        raise BundleError("provenance.print_derivation.template_source.mode is unsupported")
    if set(template_source) != expected_template_source:
        raise BundleError("provenance.print_derivation.template_source is invalid")
    personalization = manifest.get("personalization")
    if (
        not isinstance(personalization, dict)
        or set(personalization) != {"pet_name"}
        or not isinstance(personalization.get("pet_name"), dict)
    ):
        raise BundleError("personalization must match the closed bundle-v1 contract")
    qa_fixture = provenance.get("qa_fixture")
    if not isinstance(qa_fixture, dict) or set(qa_fixture) != {"input_pet", "pet_name"}:
        raise BundleError(
            "provenance.qa_fixture must contain exactly input_pet and pet_name"
        )
    if qa_fixture.get("input_pet") != "qa/input-pet.png":
        raise BundleError("provenance.qa_fixture.input_pet must be qa/input-pet.png")
    try:
        validate_pet_name(
            qa_fixture["pet_name"],
            personalization["pet_name"],
        )
    except PersonalizationError as exc:
        raise BundleError(str(exc)) from exc
    required_assets = {
        "product-profile.json",
        "art.png",
        "print/art.png",
        "layout.json",
        "layout-print.json",
        art_prompt_name,
        pet_prompt_name,
        *refs,
        "qa/transformed-pet.png",
        "qa/input-pet.png",
        "qa/golden-preview.png",
        "qa/golden-preview-debug.png",
    }
    if preview.has_name:
        assert preview.font_relative is not None
        required_assets.update(
            {
                preview.font_relative,
                str(PurePosixPath(preview.font_relative).parent / "OFL.txt"),
            }
        )
    font_source = root / "fonts" / "source.json"
    font_metadata = root / "fonts" / "METADATA.pb"
    if not preview.has_name and (font_source.exists() or font_metadata.exists()):
        raise BundleError("font provenance is not allowed when layout.name is absent")
    if font_source.exists() != font_metadata.exists():
        raise BundleError(
            "remote font provenance requires both fonts/source.json and fonts/METADATA.pb"
        )
    if font_source.is_file() and font_metadata.is_file():
        source = _json(font_source)
        expected_source = {
            "schema_version",
            "source",
            "family_id",
            "family",
            "source_url",
            "font_filename",
            "font_sha256",
            "license_sha256",
            "metadata_sha256",
        }
        if set(source) != expected_source or source.get("source") != "google-fonts-ofl":
            raise BundleError("fonts/source.json has an unsupported contract")
        family_id = source.get("family_id")
        expected_source_url = (
            f"https://github.com/google/fonts/tree/main/ofl/{family_id}"
        )
        if (
            not isinstance(family_id, str)
            or re.fullmatch(r"[a-z0-9]{2,80}", family_id) is None
            or source.get("source_url") != expected_source_url
            or preview.font_path is None
            or source.get("font_filename") != preview.font_path.name
        ):
            raise BundleError(
                "fonts/source.json does not identify the selected Google Fonts OFL asset"
            )
        expected_hashes = {
            "font_sha256": sha256(preview.font_path),
            "license_sha256": sha256(root / "fonts" / "OFL.txt"),
            "metadata_sha256": sha256(font_metadata),
        }
        actual_hashes = {key: source.get(key) for key in expected_hashes}
        if actual_hashes != expected_hashes:
            raise BundleError(
                mismatch(
                    "remote font provenance hashes",
                    expected=expected_hashes,
                    actual=actual_hashes,
                )
            )
        required_assets.update({"fonts/source.json", "fonts/METADATA.pb"})
    if seen != required_assets:
        raise BundleError(
            mismatch(
                "bundle contract assets",
                expected=sorted(required_assets),
                actual=sorted(seen),
            )
        )
    # Layout provenance identifies the selected authoring artifacts. Bundling
    # rewrites their asset paths, so delivered-layout integrity is instead
    # enforced by the bundle asset inventory below.
    provenance_hashes = {
        "provenance.selected.art.artifact_sha256": (
            selected["art"]["artifact_sha256"],
            root / "art.png",
        ),
        "provenance.representative_pet_sha256": (
            provenance["representative_pet_sha256"],
            root / "qa" / "transformed-pet.png",
        ),
        "provenance.print_derivation.art_sha256": (
            print_derivation["art_sha256"],
            root / "print" / "art.png",
        ),
    }
    if preview.has_name:
        assert preview.font_path is not None
        provenance_hashes["provenance.selected.layout.font_sha256"] = (
            selected["layout"]["font_sha256"],
            preview.font_path,
        )
    for label, (expected_hash, artifact) in provenance_hashes.items():
        actual_hash = sha256(artifact)
        if expected_hash != actual_hash:
            raise BundleError(
                mismatch(
                    f"{label} ({artifact})",
                    expected=expected_hash,
                    actual=actual_hash,
                )
            )
    if print_layout.font_relative != preview.font_relative:
        raise BundleError(
            mismatch(
                "preview/print layout font",
                expected=preview.font_relative,
                actual=print_layout.font_relative,
            )
        )
    if preview.has_name:
        assert preview.font_relative is not None and preview.font_path is not None
        if re.fullmatch(r"fonts/[A-Za-z0-9._-]+\.ttf", preview.font_relative) is None:
            raise BundleError("bundle layout font must use fonts/<filename>.ttf")
        try:
            resolve_ofl_license(preview.font_path)
        except FontLicenseError as exc:
            raise BundleError(str(exc)) from exc
    validate_raster(
        root / "art.png",
        "art.png",
        require_png=True,
        require_alpha=True,
        expected_size=(profile.preview_art_size.width, profile.preview_art_size.height),
    )
    validate_raster(
        root / "print" / "art.png",
        "print/art.png",
        require_png=True,
        require_alpha=True,
        expected_size=(profile.print_size.width, profile.print_size.height),
        allow_large=True,
    )
    validate_raster(
        root / "qa" / "input-pet.png",
        "qa/input-pet.png",
        require_png=True,
    )
    validate_raster(
        root / "qa" / "transformed-pet.png",
        "qa/transformed-pet.png",
        require_png=True,
        require_alpha=True,
        expected_size=(profile.preview_pet_size.width, profile.preview_pet_size.height),
    )
    for reference in refs:
        validate_raster(root / reference, reference, require_png=True)
    for name in ("qa/golden-preview.png", "qa/golden-preview-debug.png"):
        validate_raster(
            root / name,
            name,
            require_png=True,
            expected_size=(
                profile.preview_art_size.width,
                profile.preview_art_size.height,
            ),
        )
    return manifest


def validate_production_bundle(root: Path) -> dict[str, Any]:
    """Validate a bundle while retaining its absolute path in every failure."""
    resolved = root.expanduser().resolve()
    try:
        return _validate_production_bundle(resolved)
    except BundleError as exc:
        raise BundleError(
            f"production bundle validation failed; bundle={resolved}; error={exc}"
        ) from exc


def build_from_selection(
    *,
    selection_path: Path,
    output_dir: Path,
    bundle_revision: str,
    pet_name_max_length: int,
    qa_input_pet: Path,
) -> Path:
    selection_path = selection_path.expanduser().resolve()
    selection = _json(selection_path)
    if (
        selection_path.name != "selection.json"
        or selection_path.parent.parent.name != "graduations"
    ):
        raise BundleError(
            "selection must be graduations/<graduation-id>/selection.json: "
            f"{selection_path}"
        )
    product_root = selection_path.parents[2]

    def product_path(value: object, label: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise BundleError(f"{label} must contain a product-relative path")
        relative = Path(value)
        if relative.is_absolute():
            raise BundleError(f"{label} must be product-relative: {value}")
        resolved = (product_root / relative).resolve()
        if not resolved.is_relative_to(product_root):
            raise BundleError(f"{label} escapes its product root: {value}")
        return resolved
    if (
        selection.get("schema_version") != 1
        or selection.get("compatibility_qa", {}).get("status") != "passed"
    ):
        raise BundleError(
            "bundle requires a valid selection with passing compatibility QA"
        )
    sources = selection.get("sources", {})
    try:
        art_attempt = product_path(sources["art_attempt"], "art attempt")
        pet_experiment = product_path(sources["pet_experiment"], "pet experiment")
        layout_attempt = product_path(sources["layout_attempt"], "layout attempt")
    except (KeyError, TypeError) as exc:
        raise BundleError("selection is missing resolved component sources") from exc
    art_run = _json(art_attempt / "run.json")
    layout_run = _json(layout_attempt / "run.json")
    if art_run.get("status") != "succeeded" or layout_run.get("status") != "succeeded":
        raise BundleError("selected attempts must be succeeded")
    art_experiment, art_meta = _attempt_experiment(art_attempt, art_run, "art")
    layout_experiment, _ = _attempt_experiment(
        layout_attempt, layout_run, "layout"
    )
    pet_meta = _json(pet_experiment / "experiment.json")
    pet_generation = pet_meta.get("generation")
    if not isinstance(pet_generation, dict) or pet_generation.get("provider") != "openai":
        raise BundleError(
            "MVP production bundles require an OpenAI pet runtime; "
            f"pet_experiment={pet_experiment}; "
            f"provider={pet_generation.get('provider') if isinstance(pet_generation, dict) else None!r}. "
            "Gemini remains available for offline experiments only."
        )
    for experiment_root, metadata in (
        (art_experiment, art_meta),
        (pet_experiment, pet_meta),
    ):
        for label in ("prompt", "product_profile"):
            descriptor = metadata.get("inputs", {}).get(label)
            if not isinstance(descriptor, dict):
                raise BundleError(f"selected experiment is missing {label} input")
            source = experiment_root / str(descriptor.get("path"))
            if not source.is_file():
                raise BundleError(
                    f"selected experiment {label} snapshot does not exist: {source.resolve()}"
                )
            actual_hash = sha256(source)
            if actual_hash != descriptor.get("sha256"):
                raise BundleError(
                    mismatch(
                        f"selected experiment {label} snapshot hash ({source.resolve()})",
                        expected=descriptor.get("sha256"),
                        actual=actual_hash,
                    )
                )
        for descriptor in metadata.get("inputs", {}).get("references", []):
            source = experiment_root / str(descriptor.get("path"))
            if not source.is_file():
                raise BundleError(
                    f"selected experiment reference snapshot does not exist: {source.resolve()}"
                )
            actual_hash = sha256(source)
            if actual_hash != descriptor.get("sha256"):
                raise BundleError(
                    mismatch(
                        f"selected experiment reference snapshot hash ({source.resolve()})",
                        expected=descriptor.get("sha256"),
                        actual=actual_hash,
                    )
                )
    if pet_meta.get("status") == "discarded":
        raise BundleError("selected pet experiment is discarded")
    component_identities = {
        (art_meta.get("design_id"), art_meta.get("product_profile_id")),
        (pet_meta.get("design_id"), pet_meta.get("product_profile_id")),
    }
    selection_identity = {
        (selection.get("design_id"), selection.get("product_profile_id"))
    }
    if component_identities != selection_identity:
        raise BundleError(
            mismatch(
                "selection source identity",
                expected=sorted(selection_identity, key=repr),
                actual=sorted(component_identities, key=repr),
            )
        )
    selected = selection.get("selected", {})
    selected_art = art_attempt / "outputs" / "art.png"
    selected_layout = layout_attempt / "outputs" / "layout.json"
    selected_layout_value = load_layout(layout_attempt / "outputs")
    hash_checks = [
        (
            "selection art hash",
            selected.get("art", {}).get("artifact_sha256"),
            selected_art,
        ),
        (
            "selection layout hash",
            selected.get("layout", {}).get("layout_sha256"),
            selected_layout,
        ),
        (
            "selection pet runtime hash",
            selected.get("pet_runtime", {}).get("experiment_sha256"),
            pet_experiment / "experiment.json",
        ),
    ]
    if selected_layout_value.has_name:
        assert selected_layout_value.font_path is not None
        hash_checks.append(
            (
                "selection font hash",
                selected.get("layout", {}).get("font_sha256"),
                selected_layout_value.font_path,
            )
        )
    for label, expected_hash, artifact_path in hash_checks:
        actual_hash = sha256(artifact_path)
        if expected_hash != actual_hash:
            raise BundleError(
                mismatch(
                    f"{label} ({artifact_path.resolve()})",
                    expected=expected_hash,
                    actual=actual_hash,
                )
            )
    component_evaluations = selection.get("component_qa")
    if (
        not isinstance(component_evaluations, dict)
        or set(component_evaluations) != {"art", "pet", "layout"}
    ):
        raise BundleError("selection is missing component evaluation evidence")
    for kind, descriptor in component_evaluations.items():
        if not isinstance(descriptor, dict) or descriptor.get("status") != "passed":
            raise BundleError(f"selection {kind} evaluation is not passing")
        component_path = product_path(
            descriptor.get("evaluation_path"), f"{kind} evaluation"
        )
        expected_component_path = (
            product_root
            / "reviews"
            / kind
            / str(descriptor.get("review_id"))
            / "evaluation.json"
        )
        if component_path != expected_component_path:
            raise BundleError(
                mismatch(
                    f"selection {kind} evaluation path",
                    expected=str(expected_component_path),
                    actual=str(component_path),
                )
            )
        if (
            not component_path.is_file()
            or descriptor.get("evaluation_sha256") != sha256(component_path)
            or _json(component_path).get("kind") != kind
        ):
            actual = {
                "path": str(component_path.resolve()),
                "exists": component_path.is_file(),
                "sha256": sha256(component_path) if component_path.is_file() else None,
                "kind": _json(component_path).get("kind") if component_path.is_file() else None,
            }
            raise BundleError(
                mismatch(
                    f"selection {kind} evaluation evidence",
                    expected={
                        "path": str(component_path.resolve()),
                        "exists": True,
                        "sha256": descriptor.get("evaluation_sha256"),
                        "kind": kind,
                    },
                    actual=actual,
                )
            )
    review_decisions = selection.get("review_decisions")
    if (
        not isinstance(review_decisions, dict)
        or set(review_decisions) != {"art", "pet", "layout", "assembly"}
    ):
        raise BundleError(
            "selection must contain art, pet, layout, and assembly review decisions"
        )
    for kind, descriptor in review_decisions.items():
        if not isinstance(descriptor, dict) or not isinstance(
            descriptor.get("review_id"), str
        ):
            raise BundleError(f"selection {kind} review decision is invalid")
        decision_path = product_path(
            descriptor.get("decision_path"), f"{kind} decision"
        )
        expected_decision_path = (
            product_root
            / "reviews"
            / kind
            / descriptor["review_id"]
            / "decision.json"
        )
        if decision_path != expected_decision_path:
            raise BundleError(
                mismatch(
                    f"selection {kind} decision path",
                    expected=str(expected_decision_path),
                    actual=str(decision_path),
                )
            )
        actual_hash = sha256(decision_path) if decision_path.is_file() else None
        if actual_hash != descriptor.get("decision_sha256"):
            raise BundleError(
                mismatch(
                    f"selection {kind} review decision hash ({decision_path})",
                    expected=descriptor.get("decision_sha256"),
                    actual=actual_hash,
                )
            )
        decision_value = _json(decision_path)
        expected_selected = {
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
                "art_attempt": art_attempt.relative_to(product_root).as_posix(),
                "pet_experiment": pet_experiment.relative_to(product_root).as_posix(),
                "layout_attempt": layout_attempt.relative_to(product_root).as_posix(),
            },
        }[kind]
        qa_descriptor = (
            component_evaluations[kind]
            if kind != "assembly"
            else selection.get("compatibility_qa", {})
        )
        expected_decision = {
            "kind": kind,
            "approved": True,
            "selected": expected_selected,
            "evaluation": {
                "path": "evaluation.json",
                "evaluation_sha256": qa_descriptor.get("evaluation_sha256"),
            },
        }
        actual_decision = {
            key: decision_value.get(key)
            for key in ("kind", "approved", "selected", "evaluation")
        }
        if actual_decision != expected_decision:
            raise BundleError(
                mismatch(
                    f"selection {kind} review decision content ({decision_path})",
                    expected=expected_decision,
                    actual=actual_decision,
                )
            )
    compatibility = selection.get("compatibility_qa", {})
    evaluation_path = product_path(
        compatibility.get("evaluation_path"), "assembly evaluation"
    )
    expected_evaluation_path = (
        product_root
        / "reviews"
        / "assembly"
        / str(compatibility.get("review_id"))
        / "evaluation.json"
    )
    if evaluation_path != expected_evaluation_path:
        raise BundleError(
            mismatch(
                "selection assembly evaluation path",
                expected=str(expected_evaluation_path),
                actual=str(evaluation_path),
            )
        )
    if (
        not evaluation_path.is_file()
        or compatibility.get("evaluation_sha256") != sha256(evaluation_path)
    ):
        raise BundleError(
            mismatch(
                "selection compatibility evaluation evidence",
                expected={
                    "path": str(evaluation_path.resolve()),
                    "exists": True,
                    "sha256": compatibility.get("evaluation_sha256"),
                },
                actual={
                    "path": str(evaluation_path.resolve()),
                    "exists": evaluation_path.is_file(),
                    "sha256": sha256(evaluation_path) if evaluation_path.is_file() else None,
                },
            )
        )
    print_qa = selection.get("print_qa", {})
    try:
        print_candidate = product_path(sources["print_candidate"], "print candidate")
    except (KeyError, TypeError) as exc:
        raise BundleError("selection is missing its print candidate") from exc
    print_candidate_manifest = print_candidate / "print-candidate.json"
    if (
        not print_candidate_manifest.is_file()
        or print_qa.get("status") != "passed"
        or print_qa.get("print_candidate_sha256") != sha256(print_candidate_manifest)
    ):
        raise BundleError(
            mismatch(
                "selection print candidate evidence",
                expected={
                    "path": str(print_candidate_manifest.resolve()),
                    "exists": True,
                    "status": "passed",
                    "sha256": print_qa.get("print_candidate_sha256"),
                },
                actual={
                    "path": str(print_candidate_manifest.resolve()),
                    "exists": print_candidate_manifest.is_file(),
                    "status": print_qa.get("status"),
                    "sha256": sha256(print_candidate_manifest) if print_candidate_manifest.is_file() else None,
                },
            )
        )
    print_record = _json(print_candidate_manifest)
    try:
        name_policy = pet_name_policy(pet_name_max_length)
        qa_pet_name = validate_pet_name(print_record.get("pet_name"), name_policy)
    except PersonalizationError as exc:
        raise BundleError(str(exc)) from exc
    try:
        representative_pet_attempt = product_path(
            print_record["sources"]["pet_attempt"],
            "representative pet attempt",
        )
    except (KeyError, TypeError) as exc:
        raise BundleError("selected print candidate has no representative pet attempt") from exc
    representative_run = _json(representative_pet_attempt / "run.json")
    if (
        representative_run.get("status") != "succeeded"
        or representative_pet_attempt.parent.parent != pet_experiment
    ):
        raise BundleError(
            "selected print candidate representative pet does not belong to the pet runtime"
        )
    representative_pet = (
        representative_pet_attempt / "outputs" / "transformed-pet.png"
    )
    if (
        not representative_pet.is_file()
        or print_record.get("source_hashes", {}).get("pet") != sha256(representative_pet)
    ):
        raise BundleError("selected representative pet hash does not resolve")
    qa_input_pet = qa_input_pet.expanduser().resolve()
    expected_input_hash = representative_run.get("input_pet_sha256")
    snapshotted_input_pet = representative_pet_attempt / "inputs" / "input-pet.png"
    if (
        not qa_input_pet.is_file()
        or not isinstance(expected_input_hash, str)
        or sha256(qa_input_pet) != expected_input_hash
        or not snapshotted_input_pet.is_file()
        or sha256(snapshotted_input_pet) != expected_input_hash
    ):
        raise BundleError(
            "--qa-input-pet must match the selected representative pet attempt"
        )
    print_outputs_by_path = {
        item.get("path"): item
        for item in print_record.get("outputs", [])
        if isinstance(item, dict)
    }
    for relative in ("outputs/art-print.png", "outputs/layout-print.json"):
        output = print_candidate / relative
        descriptor = print_outputs_by_path.get(relative)
        if (
            not output.is_file()
            or not isinstance(descriptor, dict)
            or descriptor.get("sha256") != sha256(output)
        ):
            raise BundleError(
                f"selected print candidate output does not resolve: {relative}"
            )
    print_art = print_candidate / "outputs" / "art-print.png"
    print_layout = print_candidate / "outputs" / "layout-print.json"
    profile_path = art_experiment / str(
        art_meta["inputs"]["product_profile"]["path"]
    )
    profile = load_product_profile(profile_path)
    design_id = str(selection["design_id"])
    template_id = catalog_template_id(design_id, profile.profile_id)
    template_root = output_dir.expanduser().resolve() / template_id
    revision = (
        _next_revision(template_root, product_root, template_id)
        if bundle_revision == "next"
        else int(bundle_revision)
    )
    if revision < 1:
        raise BundleError("bundle revision must be positive")
    destination = template_root / f"v{revision:06d}"
    partial = destination.with_name(destination.name + ".partial")
    if destination.exists() or partial.exists():
        raise BundleError(f"bundle revision already exists or is partial: {destination}")
    template_root.mkdir(parents=True, exist_ok=True)
    partial.mkdir()
    finalized = False
    try:
        layout_template = layout_attempt / "outputs"
        preview_layout = load_layout(layout_template)
        print_layout_value = load_layout(
            print_candidate / "outputs",
            layout_path=print_layout,
            allow_large_art=True,
        )
        validate_layout_pair(preview_layout, print_layout_value)
        if sha256(preview_layout.art_path) != sha256(selected_art):
            raise BundleError(
                mismatch(
                    "selected layout art hash",
                    expected={
                        "path": str(selected_art.resolve()),
                        "sha256": sha256(selected_art),
                    },
                    actual={
                        "path": str(preview_layout.art_path.resolve()),
                        "sha256": sha256(preview_layout.art_path),
                    },
                )
            )
        if (preview_layout.canvas_width, preview_layout.canvas_height) != (
            profile.preview_art_size.width,
            profile.preview_art_size.height,
        ):
            raise BundleError(
                mismatch(
                    "selected preview layout canvas",
                    expected=(
                        profile.preview_art_size.width,
                        profile.preview_art_size.height,
                    ),
                    actual=(preview_layout.canvas_width, preview_layout.canvas_height),
                )
            )
        if (
            print_layout_value.canvas_width,
            print_layout_value.canvas_height,
        ) != (profile.print_size.width, profile.print_size.height):
            raise BundleError(
                mismatch(
                    "selected print layout canvas",
                    expected=(profile.print_size.width, profile.print_size.height),
                    actual=(
                        print_layout_value.canvas_width,
                        print_layout_value.canvas_height,
                    ),
                )
            )

        template_source = dict(print_record["template_source"])
        template_source.pop("path", None)
        print_derivation = {
            "mode": "selected-print-candidate",
            "print_candidate_id": print_record["print_candidate_id"],
            "print_candidate_sha256": sha256(print_candidate_manifest),
            "backends": print_record["backends"],
            "template_source": template_source,
            "art_sha256": sha256(print_art),
            "layout_sha256": sha256(print_layout),
        }
        art_generation = art_meta.get("generation")
        generation = pet_meta.get("generation")
        if not isinstance(art_generation, dict) or not isinstance(generation, dict):
            raise BundleError(
                "selected art and pet experiments require generation contracts"
            )
        art_prompt, art_prompt_name = authoring_prompt_contract(
            art_experiment / str(art_meta["inputs"]["prompt"]["path"]),
            "art-template",
            str(art_generation.get("provider")),
        )
        pet_prompt, pet_prompt_name = authoring_prompt_contract(
            pet_experiment / str(pet_meta["inputs"]["prompt"]["path"]),
            "pet-transform",
            str(generation.get("provider")),
        )
        references = [
            pet_experiment / str(item["path"])
            for item in pet_meta["inputs"]["references"]
        ]
        refs = canonical_reference_paths(len(references))
        for index, reference in enumerate(references, 1):
            validate_raster(reference, f"finished reference design {index}")

        font_license = None
        if preview_layout.has_name:
            assert preview_layout.font_path is not None
            try:
                font_license = resolve_ofl_license(preview_layout.font_path)
            except FontLicenseError as exc:
                raise BundleError(str(exc)) from exc
        (partial / "print").mkdir()
        (partial / "qa").mkdir()
        if preview_layout.has_name:
            (partial / "fonts").mkdir()
        shutil.copyfile(selected_art, partial / "art.png")
        shutil.copyfile(print_art, partial / "print" / "art.png")
        shutil.copyfile(
            representative_pet, partial / "qa" / "transformed-pet.png"
        )
        try:
            with Image.open(qa_input_pet) as image:
                ImageOps.exif_transpose(image).convert("RGB").save(
                    partial / "qa" / "input-pet.png", format="PNG"
                )
        except OSError as exc:
            raise BundleError(f"QA input pet is not a readable image: {qa_input_pet}") from exc
        shutil.copyfile(profile_path, partial / "product-profile.json")
        shutil.copyfile(art_prompt, partial / art_prompt_name)
        shutil.copyfile(pet_prompt, partial / pet_prompt_name)
        if preview_layout.has_name:
            assert preview_layout.font_path is not None and font_license is not None
            shutil.copyfile(
                preview_layout.font_path,
                partial / "fonts" / preview_layout.font_path.name,
            )
            shutil.copyfile(font_license, partial / "fonts" / "OFL.txt")
            source_font_dir = preview_layout.font_path.parent
            remote_source = source_font_dir / "source.json"
            remote_metadata = source_font_dir / "METADATA.pb"
            if remote_source.exists() != remote_metadata.exists():
                raise BundleError(
                    "selected remote font requires both source.json and METADATA.pb"
                )
            if remote_source.is_file() and remote_metadata.is_file():
                shutil.copyfile(remote_source, partial / "fonts" / "source.json")
                shutil.copyfile(remote_metadata, partial / "fonts" / "METADATA.pb")
        for source, relative in zip(references, refs, strict=True):
            target = partial / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as image:
                ImageOps.exif_transpose(image).convert("RGB").save(
                    target, format="PNG"
                )

        preview_layout_data = preview_layout.to_dict()
        preview_layout_data["art"] = "art.png"
        print_layout_data = print_layout_value.to_dict()
        print_layout_data["art"] = "print/art.png"
        if preview_layout.has_name:
            assert preview_layout.font_path is not None
            preview_layout_data["name"]["font"] = (
                f"fonts/{preview_layout.font_path.name}"
            )
            print_layout_data["name"]["font"] = (
                f"fonts/{preview_layout.font_path.name}"
            )
        atomic_json(partial / "layout.json", preview_layout_data)
        atomic_json(partial / "layout-print.json", print_layout_data)
        render_to_files(
            template_dir=partial,
            pet_image=partial / "qa" / "transformed-pet.png",
            pet_name=qa_pet_name,
            output=partial / "qa" / "golden-preview.png",
            debug_output=partial / "qa" / "golden-preview-debug.png",
        )
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "template_id": template_id,
            "design_id": design_id,
            "product_profile_id": profile.profile_id,
            "bundle_revision": revision,
            "created_at": utc_now(),
            "runtime": {
                "provider": generation["provider"],
                "model": generation["model"],
                "transport": generation.get("transport"),
                "prompt": pet_prompt_name,
                "reference_assets": refs,
                "input_image_order": ["user_pet"]
                + [f"reference_{index}" for index in range(1, len(refs) + 1)],
                "request_parameters": provider_request_parameters(
                    provider=str(generation["provider"]),
                    model=str(generation["model"]),
                    quality=str(generation["parameters"]["quality"]),
                    size=(
                        f"{profile.preview_pet_size.width}x"
                        f"{profile.preview_pet_size.height}"
                    ),
                ),
                "output": {
                    "format": "png",
                    "background": "transparent",
                    "width": profile.preview_pet_size.width,
                    "height": profile.preview_pet_size.height,
                },
                "normalization": {
                    "policy": "transparent-rgba-contain",
                    "version": 1,
                    "alpha_failure": "reject",
                },
            },
            "renderer": {
                "layout_schema_version": 2,
                "pet_fit": "contain-visible-alpha",
                "pet_anchor": "bottom-center",
                "name_mode": (
                    "layout-text" if preview_layout.has_name else "embedded-in-pet"
                ),
                **(
                    {
                        "name_fit": (
                            "nominal-size-shrink-only-visible-ink-contain"
                        )
                    }
                    if preview_layout.has_name
                    else {}
                ),
                "version": 2,
            },
            "personalization": {"pet_name": name_policy},
            "provenance": {
                "selection_id": selection["selection_id"],
                "selection_sha256": sha256(selection_path),
                "art_attempt_id": art_attempt.name,
                "pet_experiment_id": pet_experiment.name,
                "representative_pet_attempt_id": representative_pet_attempt.name,
                "representative_pet_sha256": sha256(representative_pet),
                "qa_fixture": {
                    "input_pet": "qa/input-pet.png",
                    "pet_name": qa_pet_name,
                },
                "layout_attempt_id": layout_attempt.name,
                "component_evaluations": {
                    kind: {
                        "evaluation_id": evidence["review_id"],
                        "evaluation_sha256": evidence["evaluation_sha256"],
                        "status": evidence["status"],
                    }
                    for kind, evidence in selection["component_qa"].items()
                },
                "compatibility_evaluation": {
                    "evaluation_id": selection["compatibility_qa"]["review_id"],
                    "evaluation_sha256": selection["compatibility_qa"]["evaluation_sha256"],
                    "status": selection["compatibility_qa"]["status"],
                },
                "print_candidate": selection["print_qa"],
                "selected": selection["selected"],
                "print_derivation": print_derivation,
                "generator": {"version": __version__},
            },
            "prompts": {
                "art_template": art_prompt_name,
                "pet_transform": pet_prompt_name,
            },
            "assets": [],
        }
        manifest["assets"] = [
            _asset(path, partial)
            for path in sorted(partial.rglob("*"))
            if path.is_file() and path.name != "bundle.json"
        ]
        atomic_json(partial / "bundle.json", manifest)
        os.replace(partial, destination)
        finalized = True
        validate_production_bundle(destination)
        return destination
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        if finalized and destination.exists():
            shutil.rmtree(destination)
        raise
