"""Seed lifecycle, transparency checks, and subject-variant generation."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import secrets
import shutil
import tempfile
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any

from PIL import Image, ImageOps

from .authoring import save_png
from .core import (
    DesignIdentity,
    StaticPrintError,
    read_json,
    require_png,
    sha256_file,
    utc_now,
    validate_id,
    write_json,
)
from .image_quality import (
    print_readiness_report_for_path,
    print_readiness_warning,
)

POOL_NAMES = {"active", "graduated", "abandoned"}
OPAQUE_FULL_BLEED = "opaque-full-bleed"
PRESERVE_TRANSPARENCY = "preserve-transparency"
PRINT_QUALITY_PROMPT = (
    "Optimize the returned pixels for production printing at the requested canvas "
    "size: preserve crisp typography, line art, pattern edges, and intentional fine "
    "detail; use solid fully opaque pixels inside visible artwork; confine partial "
    "alpha to a narrow antialiased outer edge; and avoid blur, compression artifacts, "
    "color halos, stray pixels, broad translucent washes, or transparent holes inside "
    "solid artwork. Preserve intentional painterly texture without making the "
    "complete design fuzzy."
)


@dataclass(frozen=True)
class Subject:
    subject_id: str
    display_name: str
    subject_kind: str
    subject_species: str
    generation_description: str
    hero_strategy: str
    collection_path: str


@dataclass(frozen=True)
class DesignIdProposal:
    """A theme-derived seed ID before local uniqueness is applied."""

    design_id: str
    method: str
    model: str | None = None
    request_id: str | None = None


DesignIdGenerator = Callable[[Path], DesignIdProposal | str]


_MACHINE_FILENAME_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        (
            r"(?:^|-)(?:chatgpt|openai|gpt-image|dall-e|dalle|midjourney|"
            r"stable-diffusion|gemini|flux|ai-generated)(?:-|$)"
        ),
        (
            r"^(?:img|dsc|dscn|pxl|screenshot|screen-shot|image|photo|download|"
            r"untitled|output|generated|generated-image)(?:-|$)"
        ),
        (
            r"(?:^|-)(?:19|20)\d{2}-(?:0?[1-9]|1[0-2])-"
            r"(?:0?[1-9]|[12]\d|3[01])(?:-at)?-(?:[01]?\d|2[0-3])-"
            r"[0-5]\d(?:-[0-5]\d)?(?:-am|-pm)?(?:-|$)"
        ),
        r"(?:^|-)(?:19|20)\d{6}-(?:[01]\d|2[0-3])[0-5]\d[0-5]\d(?:-|$)",
        r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$",
        r"^[0-9a-f]{16,}$",
    )
)


def _collection_id(value: str, label: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    identifier = "-".join(part for part in re.split(r"[^a-z0-9]+", ascii_value) if part)
    if not identifier:
        raise StaticPrintError(
            f"{label} cannot be converted to a design ID; actual={value!r}"
        )
    return validate_id(identifier, label)


def _theme_design_id(value: str) -> str:
    """Normalize and constrain a model-proposed theme identifier."""

    candidate = value.strip().strip("`\"'")
    if "\n" in candidate:
        candidate = candidate.splitlines()[0].strip().strip("`\"'")
    design_id = _collection_id(candidate, "generated design ID")
    tokens = design_id.split("-")
    if not 3 <= len(tokens) <= 8 or len(design_id) > 80:
        raise StaticPrintError(
            "generated design ID must contain 3-8 descriptive kebab-case words "
            f"and be at most 80 characters; actual={design_id!r}; raw={value!r}"
        )
    generic = {"art", "design", "image", "picture", "photo", "chatgpt", "generated"}
    if all(token in generic or token.isdigit() for token in tokens):
        raise StaticPrintError(
            "generated design ID does not describe the image theme; "
            f"actual={design_id!r}"
        )
    return design_id


def _has_machine_generated_filename(source: Path) -> bool:
    """Return whether a source stem is clearly an export/generated identifier."""

    normalized = _collection_id(source.stem, "source filename")
    return any(pattern.search(normalized) for pattern in _MACHINE_FILENAME_PATTERNS)


def openai_design_id_generator(source: Path) -> DesignIdProposal:
    """Use a low-detail vision request to propose a descriptive design ID."""

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise StaticPrintError(
            "semantic design-ID generation requires OPENAI_API_KEY in "
            "work/static-print.env; add the key, restart the server, and retry. "
            "To opt out explicitly, set STATIC_PRINT_DESIGN_ID_MODE=filename."
        )
    model = os.environ.get("STATIC_PRINT_DESIGN_ID_MODEL", "gpt-5.4-mini").strip()
    if not model:
        raise StaticPrintError("STATIC_PRINT_DESIGN_ID_MODEL cannot be empty")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise StaticPrintError(
            "the OpenAI SDK is not installed; run .venv/bin/python -m pip install -e ."
        ) from exc

    prompt = (
        "Create a stable catalog identifier for this print-design image. Return only "
        "one lowercase kebab-case slug containing 3 to 8 meaningful words. Describe "
        "the visible theme using the main animal or character, occasion or setting, "
        "action, and distinctive slogan concept when readable. Do not use the source "
        "filename, dates, timestamps, IDs, or generic words such as image, design, art, "
        "generated, or chatgpt. Do not invent text that is not visible. Example output: "
        "vintage-cat-santa-chimney"
    )
    try:
        with Image.open(source) as opened:
            opened.load()
            preview = ImageOps.exif_transpose(opened).convert("RGBA")
            preview.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            preview.save(buffer, format="PNG", optimize=True)
    except (OSError, ValueError) as exc:
        raise StaticPrintError(
            f"could not prepare review image for semantic ID generation: "
            f"{source.resolve()}; error={exc}"
        ) from exc
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    try:
        response = OpenAI(api_key=api_key).responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{encoded}",
                            "detail": "low",
                        },
                    ],
                }
            ],
            max_output_tokens=128,
            store=False,
            timeout=60,
        )
    except Exception as exc:
        request_id = getattr(exc, "request_id", None) or getattr(exc, "requestID", None)
        code = getattr(exc, "code", None)
        raise StaticPrintError(
            "semantic design-ID generation failed; "
            f"source={source.resolve()}; provider=openai; model={model!r}; "
            f"code={code!r}; request_id={request_id!r}; error={exc}"
        ) from exc
    raw = str(getattr(response, "output_text", "") or "").strip()
    if not raw:
        raise StaticPrintError(
            "semantic design-ID generation returned no text; "
            f"source={source.resolve()}; provider=openai; model={model!r}; "
            f"request_id={getattr(response, '_request_id', None)!r}"
        )
    return DesignIdProposal(
        design_id=_theme_design_id(raw),
        method="openai-vision",
        model=model,
        request_id=getattr(response, "_request_id", None),
    )


def configured_design_id_generator(source: Path) -> DesignIdProposal:
    """Resolve the explicit intake naming mode from private environment config."""

    mode = os.environ.get("STATIC_PRINT_DESIGN_ID_MODE", "auto").strip().lower()
    if mode == "auto":
        if not _has_machine_generated_filename(source):
            return DesignIdProposal(
                design_id=_collection_id(source.stem, "design ID"),
                method="filename",
            )
        return openai_design_id_generator(source)
    if mode == "semantic":
        return openai_design_id_generator(source)
    if mode == "filename":
        return DesignIdProposal(
            design_id=_collection_id(source.stem, "design ID"),
            method="filename",
        )
    raise StaticPrintError(
        "STATIC_PRINT_DESIGN_ID_MODE must be 'auto', 'semantic', or 'filename'; "
        f"actual={mode!r}"
    )


def _intake_assignments(
    roots: Iterable[tuple[str, Path]], collection: Path
) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """Index prior source-path assignments across all lifecycle pools."""

    assignments: dict[str, tuple[str, str, dict[str, Any]]] = {}
    collection_value = str(collection)
    for pool, root in roots:
        if not root.is_dir():
            continue
        for candidate in sorted(path for path in root.iterdir() if path.is_dir()):
            intake_path = candidate / "intake.json"
            if not intake_path.is_file():
                continue
            intake = read_json(intake_path)
            if intake.get("collection_root") != collection_value:
                continue
            source_file = intake.get("source_file")
            if not isinstance(source_file, str) or not source_file:
                continue
            key = str(Path(source_file).expanduser().resolve())
            previous = assignments.get(key)
            if previous is not None and previous[:2] != (pool, candidate.name):
                raise StaticPrintError(
                    "review source is assigned to multiple design IDs; repair the "
                    f"duplicate intake records before retrying: source={key}; "
                    f"first_pool={previous[0]!r}; first_design_id={previous[1]!r}; "
                    f"second_pool={pool!r}; second_design_id={candidate.name!r}"
                )
            assignments[key] = (pool, candidate.name, intake)
    return assignments


def _unique_design_id(
    base_design_id: str,
    occupied: set[str],
    *,
    collection_id: str,
    filename_derived: bool,
) -> str:
    if base_design_id not in occupied:
        return base_design_id

    # A meaningful filename is the preferred stable identity. If it collides,
    # qualify it with the collection before using an arbitrary number. This
    # keeps independently curated collections readable without paid image
    # interpretation. Semantic/model proposals already describe their theme,
    # so a collision goes directly to a numeric suffix.
    collision_base = base_design_id
    suffix = 2
    if filename_derived:
        collision_base = validate_id(
            f"{collection_id}-{base_design_id}", "collection-qualified design ID"
        )
        if collision_base not in occupied:
            return collision_base
        suffix = 1
    while f"{collision_base}-{suffix}" in occupied:
        suffix += 1
    return validate_id(f"{collision_base}-{suffix}", "design ID")


def import_review_collection(
    work_root: Path,
    collection_root: Path,
    *,
    design_id_generator: DesignIdGenerator | None = None,
) -> dict[str, Any]:
    """Copy a flat folder of PNG candidates into the private active-review pool."""

    collection = collection_root.expanduser().resolve()
    if not collection.is_dir():
        raise StaticPrintError(
            f"review collection must be a readable directory: {collection}"
        )
    sources = sorted(
        path.resolve()
        for path in collection.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".png"
        and not path.name.startswith(".")
    )
    if not sources:
        raise StaticPrintError(
            f"review collection contains no PNG files directly inside it: {collection}"
        )
    collection_id = _collection_id(collection.name, "collection ID")
    for source in sources:
        require_png(source, "review candidate")

    active_root = pool_root(work_root, "active")
    graduated_root = pool_root(work_root, "graduated")
    abandoned_root = pool_root(work_root, "abandoned")
    active_root.mkdir(parents=True, exist_ok=True)
    roots = (
        ("active", active_root),
        ("graduated", graduated_root),
        ("abandoned", abandoned_root),
    )
    assignments = _intake_assignments(roots, collection)
    occupied = {
        path.name
        for _, root in roots
        if root.is_dir()
        for path in root.iterdir()
        if path.is_dir()
    }
    generator = design_id_generator or configured_design_id_generator
    result: dict[str, Any] = {
        "collection_id": collection_id,
        "collection_root": str(collection),
        "active_root": str(active_root.resolve()),
        "discovered": len(sources),
        "semantic_id_calls": 0,
        "imported": [],
        "updated": [],
        "unchanged": [],
        "skipped": [],
    }
    for source in sources:
        source_key = str(source)
        assignment = assignments.get(source_key)
        existing_intake: dict[str, Any] | None = None
        if assignment is not None:
            pool, design_id, existing_intake = assignment
            directory = pool_root(work_root, pool) / design_id
            proposal = existing_intake.get("design_id_generation")
            if not isinstance(proposal, dict):
                proposal = {
                    "method": "legacy-intake",
                    "base_design_id": design_id,
                    "selected_design_id": design_id,
                }
        else:
            generated = generator(source)
            if isinstance(generated, DesignIdProposal):
                method = generated.method
                model = generated.model
                request_id = generated.request_id
                base_design_id = (
                    _collection_id(generated.design_id, "design ID")
                    if method == "filename"
                    else _theme_design_id(generated.design_id)
                )
            else:
                base_design_id = _theme_design_id(str(generated))
                method = "injected"
                model = None
                request_id = None
            design_id = _unique_design_id(
                base_design_id,
                occupied,
                collection_id=collection_id,
                filename_derived=method == "filename",
            )
            pool = "active"
            proposal = {
                "method": method,
                "base_design_id": base_design_id,
                "selected_design_id": design_id,
            }
            if model:
                proposal["model"] = model
            if request_id:
                proposal["request_id"] = request_id
            occupied.add(design_id)
            result["semantic_id_calls"] += int(method == "openai-vision")

        if pool in {"graduated", "abandoned"}:
            result["skipped"].append(
                {
                    "design_id": design_id,
                    "reason": f"already exists in {pool} pool",
                    "path": str(directory.resolve()),
                }
            )
            continue

        destination_dir = active_root / design_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / "source-design.png"
        source_digest = sha256_file(source)
        source_transparency = transparency_report(source)
        inferred_canvas_policy = canvas_policy_for_report(source_transparency)
        intake_path = destination_dir / "intake.json"
        if intake_path.is_file():
            existing_intake = read_json(intake_path)
            existing_collection = existing_intake.get("collection_root")
            if existing_collection != str(collection):
                result["skipped"].append(
                    {
                        "design_id": design_id,
                        "reason": (
                            "active design ID belongs to another collection; "
                            f"existing={existing_collection!r}"
                        ),
                        "path": str(destination_dir.resolve()),
                    }
                )
                continue
        elif destination.is_file():
            result["skipped"].append(
                {
                    "design_id": design_id,
                    "reason": "active design is manually managed and has no intake metadata",
                    "path": str(destination_dir.resolve()),
                }
            )
            continue
        recorded_canvas_policy = (
            existing_intake.get("canvas_policy")
            if existing_intake is not None
            else None
        )
        canvas_policy = (
            str(recorded_canvas_policy)
            if recorded_canvas_policy in {OPAQUE_FULL_BLEED, PRESERVE_TRANSPARENCY}
            else inferred_canvas_policy
        )
        if (
            canvas_policy == OPAQUE_FULL_BLEED
            and source_transparency["has_transparent_pixels"]
        ):
            raise StaticPrintError(
                "review collection source violates its recorded opaque full-bleed "
                "canvas policy; remove unintended alpha or explicitly update the "
                "intake policy after visual review: "
                f"design_id={design_id!r}; source={source}; "
                f"transparent_pixel_percent="
                f"{source_transparency['transparent_pixel_percent']}"
            )
        status = "imported"
        if destination.is_file():
            status = (
                "unchanged" if sha256_file(destination) == source_digest else "updated"
            )
        if status != "unchanged":
            descriptor, temporary = tempfile.mkstemp(
                prefix=".source-design.", suffix=".partial", dir=destination_dir
            )
            os.close(descriptor)
            temporary_path = Path(temporary)
            try:
                shutil.copyfile(source, temporary_path)
                os.replace(temporary_path, destination)
            finally:
                temporary_path.unlink(missing_ok=True)
        write_json(
            intake_path,
            {
                "schema": "pawmarvel-static-review-intake-v1",
                "collection_id": collection_id,
                "collection_root": str(collection),
                "source_file": str(source),
                "source_sha256": source_digest,
                "design_id": design_id,
                "design_id_generation": proposal,
                "canvas_policy": canvas_policy,
                "source_transparency": source_transparency,
                "imported_at": utc_now(),
            },
        )
        result[status].append(
            {
                "design_id": design_id,
                "source": str(source),
                "destination": str(destination.resolve()),
            }
        )
    return result


def load_subject_catalog(path: Path) -> tuple[dict[str, Any], tuple[Subject, ...]]:
    value = read_json(path)
    if value.get("schema") != "pawmarvel-static-subject-catalog-v1":
        raise StaticPrintError(f"unsupported subject catalog schema: {path.resolve()}")
    expected_pattern = "<seed-design-id>--subject-<subject-id>"
    if value.get("design_id_pattern") != expected_pattern:
        raise StaticPrintError(
            f"subject catalog design_id_pattern must be {expected_pattern!r}: "
            f"{path.resolve()}; actual={value.get('design_id_pattern')!r}"
        )
    if value.get("workspace_pattern") != "<seed-design-id>/<subject-id>":
        raise StaticPrintError(
            "subject catalog workspace_pattern must be "
            "'<seed-design-id>/<subject-id>': "
            f"{path.resolve()}; actual={value.get('workspace_pattern')!r}"
        )
    expected_subject_contract = {
        "authority": "explicit-subject-id",
        "format": "lowercase-kebab-case",
        "immutable": True,
    }
    if value.get("subject_id_contract") != expected_subject_contract:
        raise StaticPrintError(
            "subject catalog subject_id_contract mismatch: "
            f"{path.resolve()}; expected={expected_subject_contract}; "
            f"actual={value.get('subject_id_contract')!r}"
        )
    raw_subjects = value.get("subjects")
    if not isinstance(raw_subjects, list) or not raw_subjects:
        raise StaticPrintError(
            f"subject catalog must contain subjects: {path.resolve()}"
        )
    subjects: list[Subject] = []
    seen: set[str] = set()
    seen_priorities: set[int] = set()
    for index, raw in enumerate(raw_subjects):
        if not isinstance(raw, dict):
            raise StaticPrintError(
                f"subject catalog entry {index} must be an object: {path.resolve()}"
            )
        subject_id = validate_id(raw.get("subject_id"), f"subject {index} ID")
        if subject_id in seen:
            raise StaticPrintError(
                f"duplicate subject ID {subject_id!r}: {path.resolve()}"
            )
        seen.add(subject_id)
        priority = raw.get("priority")
        if not isinstance(priority, int) or priority < 1:
            raise StaticPrintError(
                f"subject {subject_id!r} priority must be a positive integer: "
                f"{path.resolve()}; actual={priority!r}"
            )
        if priority in seen_priorities:
            raise StaticPrintError(
                f"duplicate subject priority {priority}: {path.resolve()}"
            )
        seen_priorities.add(priority)
        subject_species = str(raw.get("subject_species", "")).strip()
        if subject_species not in {"dog", "cat"}:
            raise StaticPrintError(
                f"subject {subject_id!r} species must be 'dog' or 'cat': "
                f"{path.resolve()}; actual={subject_species!r}"
            )
        generation_description = str(
            raw.get("generation_description", "")
        ).strip()
        if not generation_description or len(generation_description) > 200:
            raise StaticPrintError(
                f"subject {subject_id!r} generation_description must contain "
                f"1-200 characters: {path.resolve()}"
            )
        if not re.search(
            rf"\b{re.escape(subject_species)}\b",
            generation_description,
            flags=re.IGNORECASE,
        ):
            raise StaticPrintError(
                f"subject {subject_id!r} generation_description must explicitly "
                f"name its species {subject_species!r}: {path.resolve()}; "
                f"actual={generation_description!r}"
            )
        subjects.append(
            Subject(
                subject_id=subject_id,
                display_name=str(raw.get("display_name", "")).strip(),
                subject_kind=str(raw.get("subject_kind", "")).strip(),
                subject_species=subject_species,
                generation_description=generation_description,
                hero_strategy=str(raw.get("hero_strategy", "")).strip(),
                collection_path=str(raw.get("collection_path", "")).strip(),
            )
        )
    return value, tuple(subjects)


def transparency_report(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    try:
        with Image.open(resolved) as image:
            image.load()
            bands = image.getbands()
            if "A" not in bands:
                return {
                    "has_alpha_channel": False,
                    "has_transparent_pixels": False,
                    "has_transparent_background": False,
                    "transparent_pixel_percent": 0.0,
                    "transparent_border_percent": 0.0,
                    "alpha_min": 255,
                    "alpha_max": 255,
                    "result": "opaque",
                }
            alpha = image.getchannel("A")
            extrema = alpha.getextrema()
            histogram = alpha.histogram()
            total = image.width * image.height
            non_opaque = total - histogram[255]
            pixels = alpha.load()
            border_values = [pixels[x, 0] for x in range(image.width)]
            if image.height > 1:
                border_values.extend(
                    pixels[x, image.height - 1] for x in range(image.width)
                )
            if image.width > 1 and image.height > 2:
                border_values.extend(pixels[0, y] for y in range(1, image.height - 1))
                border_values.extend(
                    pixels[image.width - 1, y] for y in range(1, image.height - 1)
                )
            transparent_border = sum(value < 255 for value in border_values)
            transparent_border_percent = (
                (transparent_border / len(border_values)) * 100
                if border_values
                else 0.0
            )
            corners = {
                pixels[0, 0],
                pixels[image.width - 1, 0],
                pixels[0, image.height - 1],
                pixels[image.width - 1, image.height - 1],
            }
            has_transparent_background = non_opaque > 0 and (
                transparent_border_percent >= 5 or any(value < 255 for value in corners)
            )
            return {
                "has_alpha_channel": True,
                "has_transparent_pixels": non_opaque > 0,
                "has_transparent_background": has_transparent_background,
                "transparent_pixel_percent": round((non_opaque / total) * 100, 3),
                "transparent_border_percent": round(transparent_border_percent, 3),
                "alpha_min": int(extrema[0]),
                "alpha_max": int(extrema[1]),
                "result": (
                    "transparent-background"
                    if has_transparent_background
                    else "partial-alpha"
                    if non_opaque > 0
                    else "opaque"
                ),
            }
    except (OSError, ValueError) as exc:
        raise StaticPrintError(
            f"could not analyze source transparency: {resolved}; error={exc}"
        ) from exc


def canvas_policy_for_report(report: dict[str, Any]) -> str:
    """Infer whether a source is an opaque full-canvas design or transparent art."""

    return (
        PRESERVE_TRANSPARENCY
        if bool(report.get("has_transparent_pixels"))
        else OPAQUE_FULL_BLEED
    )


def source_canvas_policy(path: Path) -> tuple[str, dict[str, Any]]:
    report = transparency_report(path)
    return canvas_policy_for_report(report), report


def pool_root(work_root: Path, pool: str) -> Path:
    if pool not in POOL_NAMES:
        raise StaticPrintError(
            f"pool must be one of {sorted(POOL_NAMES)}; actual={pool!r}"
        )
    folder = {
        "active": "static-design-inputs",
        "graduated": "static-graduated-designs",
        "abandoned": "static-abandoned-designs",
    }[pool]
    return work_root / folder


def move_seed(
    work_root: Path, *, seed_design_id: str, source: str, target: str
) -> Path:
    seed_design_id = validate_id(seed_design_id, "seed design ID")
    if source == target:
        raise StaticPrintError("source and target pools must be different")
    source_root = pool_root(work_root, source)
    target_root = pool_root(work_root, target)
    origin = (source_root / seed_design_id).resolve()
    destination = (target_root / seed_design_id).resolve()
    if not (origin / "source-design.png").is_file():
        raise StaticPrintError(f"seed design does not exist in {source} pool: {origin}")
    if destination.exists():
        raise StaticPrintError(
            f"seed design already exists in {target} pool: {destination}"
        )
    target_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(origin), str(destination))
    return destination


def move_seeds(
    work_root: Path,
    *,
    seed_design_ids: Iterable[str],
    source: str,
    target: str,
) -> list[Path]:
    """Move several seeds after preflighting the complete batch."""

    identifiers = list(dict.fromkeys(seed_design_ids))
    if not identifiers:
        raise StaticPrintError("seed_design_ids must contain at least one design ID")
    if source == target:
        raise StaticPrintError("source and target pools must be different")
    source_root = pool_root(work_root, source)
    target_root = pool_root(work_root, target)
    moves: list[tuple[Path, Path]] = []
    for raw_design_id in identifiers:
        design_id = validate_id(raw_design_id, "seed design ID")
        origin = (source_root / design_id).resolve()
        destination = (target_root / design_id).resolve()
        if not (origin / "source-design.png").is_file():
            raise StaticPrintError(
                f"batch seed does not exist in {source} pool: "
                f"design_id={design_id!r}; path={origin}"
            )
        if destination.exists():
            raise StaticPrintError(
                f"batch seed already exists in {target} pool: "
                f"design_id={design_id!r}; path={destination}"
            )
        moves.append((origin, destination))

    target_root.mkdir(parents=True, exist_ok=True)
    completed: list[tuple[Path, Path]] = []
    try:
        for origin, destination in moves:
            shutil.move(str(origin), str(destination))
            completed.append((origin, destination))
    except Exception as exc:
        rollback_failures: list[str] = []
        for origin, destination in reversed(completed):
            try:
                shutil.move(str(destination), str(origin))
            # Rollback is best-effort across arbitrary filesystem failures; retain
            # every failure so the operator can repair all affected paths.
            except Exception as rollback_exc:  # noqa: BLE001  # pragma: no cover
                rollback_failures.append(f"{destination} -> {origin}: {rollback_exc}")
        suffix = f"; rollback failures={rollback_failures}" if rollback_failures else ""
        raise StaticPrintError(f"batch seed move failed: {exc}{suffix}") from exc
    return [destination for _, destination in moves]


def _closest_request_size(width: int, height: int) -> str:
    target_ratio = width / height
    target_pixels = width * height
    candidates: list[tuple[float, int, int]] = []
    for candidate_width in range(512, 2049, 16):
        raw_height = candidate_width / target_ratio
        for candidate_height in {
            max(512, min(2048, int(raw_height // 16) * 16)),
            max(512, min(2048, int((raw_height + 15) // 16) * 16)),
        }:
            pixels = candidate_width * candidate_height
            ratio = max(candidate_width, candidate_height) / min(
                candidate_width, candidate_height
            )
            if pixels < 655_360 or pixels > 4_194_304 or ratio > 3:
                continue
            score = abs((candidate_width / candidate_height) - target_ratio) * 100
            score += abs(pixels - target_pixels) / max(target_pixels, 1)
            candidates.append((score, candidate_width, candidate_height))
    if not candidates:
        return "auto"
    _, candidate_width, candidate_height = min(candidates)
    return f"{candidate_width}x{candidate_height}"


def _subject_behavior_instruction(subject: Subject) -> str:
    if subject.subject_species == "cat":
        return (
            "Preserve the seed pet's emotional intent, but express it through "
            "natural cat behavior and anatomy rather than copying dog-specific "
            "gestures. Use cat-appropriate eyes, ears, whiskers, mouth, paws, body "
            "posture, and tail. Do not copy dog-like open-mouth panting or a casually "
            "protruding tongue. Prefer a naturally closed, relaxed feline mouth and "
            "express happiness, surprise, excitement, or playfulness through feline "
            "eyes, ears, whiskers, head angle, and posture. An open cat mouth is "
            "allowed only when it is natural and meaningful in the design context, "
            "such as meowing, hissing, yawning, grooming, licking, eating, or drinking; "
            "any visible tongue must belong to that specific feline action rather than "
            "imitating a dog. "
            "Keep the requested cat coat/type identity recognizable. "
        )
    return (
        "Preserve the seed pet's emotional intent, but express it through natural "
        "dog and requested-breed behavior and anatomy. Use breed-appropriate ears, "
        "mouth, tongue, paws, body posture, and tail. Show a protruding tongue only "
        "when it is a natural expression for the requested dog and the seed's "
        "emotional intent; do not force a tongue-out pose. Keep the requested dog "
        "breed/type identity recognizable. "
    )


def subject_prompt(subject: Subject, *, canvas_policy: str) -> str:
    if canvas_policy == OPAQUE_FULL_BLEED:
        canvas_instruction = (
            "The approved seed is an opaque, full-bleed design. Fill the entire "
            "canvas edge-to-edge with finished artwork. Return an opaque image with "
            "no transparent, blank, padded, letterboxed, or pillarboxed pixels at "
            "any edge."
        )
    elif canvas_policy == PRESERVE_TRANSPARENCY:
        canvas_instruction = (
            "Preserve the seed's intentional alpha transparency and transparent "
            "border treatment exactly; do not add an opaque backdrop."
        )
    else:  # Defensive guard for future callers.
        raise StaticPrintError(f"unsupported canvas policy: {canvas_policy!r}")
    return (
        "Image A is the approved seed design. Recreate the complete design on the "
        "same canvas and replace only its featured pet with a representative "
        f"{subject.generation_description}. The replacement and principal featured "
        f"subject must be a {subject.subject_species} animal, never a human, person, "
        "child, owner, or human-like character. Do not add a human that is not "
        "already present in Image A. "
        "Preserve every non-pet pixel and design decision as closely as possible: "
        "exact wording, typography, spelling, colors, background, border, ornaments, "
        "composition, spacing, crop, accessories, and rendering style. Keep the "
        "replacement pet in the same visual role and preserve the intended emotion, "
        "but do not copy species-inappropriate anatomy or body language. "
        f"{_subject_behavior_instruction(subject)}"
        "Keep the replacement pet anatomically recognizable and visually cohesive "
        "with the seed. Do not add a second pet or new text. "
        f"{canvas_instruction} {PRINT_QUALITY_PROMPT} "
        "Output only the complete design without a mockup, product, room, shadow, "
        "or surrounding scene."
    )


SubjectGenerator = Callable[[Path, Subject, str, tuple[int, int]], Image.Image]
SubjectValidator = Callable[[Path, Path, Subject], dict[str, Any]]


class SubjectGenerationError(StaticPrintError):
    """An image-provider failure with explicit retry/throttle semantics."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        rate_limited: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited
        self.retry_after_seconds = retry_after_seconds


def _retry_after_seconds(error: BaseException) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return None
    candidates = (
        ("retry-after-ms", 0.001),
        ("retry-after", 1.0),
        ("x-ratelimit-reset-images", 1.0),
        ("x-ratelimit-reset-requests", 1.0),
    )
    for name, multiplier in candidates:
        raw = headers.get(name)
        if raw is None:
            continue
        value = str(raw).strip().lower()
        unit_multiplier = multiplier
        if value.endswith("ms"):
            value = value[:-2]
            unit_multiplier = 0.001
        elif value.endswith("s"):
            value = value[:-1]
        elif value.endswith("m"):
            value = value[:-1]
            unit_multiplier = 60.0
        try:
            seconds = float(value) * unit_multiplier
        except ValueError:
            continue
        if seconds > 0:
            return seconds
    return None


def openai_subject_generator(
    source: Path,
    subject: Subject,
    prompt: str,
    target_size: tuple[int, int],
) -> Image.Image:
    canvas_policy, _ = source_canvas_policy(source)
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise StaticPrintError(
            "subject generation requires OPENAI_API_KEY in the shared environment"
        )
    model = os.environ.get(
        "STATIC_PRINT_SUBJECT_MODEL", "gpt-image-2.5-sunburst"
    ).strip()
    quality = os.environ.get("STATIC_PRINT_SUBJECT_QUALITY", "high").strip()
    if not model.startswith("gpt-image-"):
        raise StaticPrintError(
            f"STATIC_PRINT_SUBJECT_MODEL must be a GPT Image model; actual={model!r}"
        )
    allowed_qualities = (
        {"low", "medium", "high", "xhigh", "max", "auto"}
        if model.startswith("gpt-image-2.5-")
        else {"low", "medium", "high", "auto"}
    )
    if quality not in allowed_qualities:
        raise StaticPrintError(
            f"STATIC_PRINT_SUBJECT_QUALITY is unsupported by {model!r}; "
            f"actual={quality!r}; allowed={sorted(allowed_qualities)}"
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise StaticPrintError(
            "the OpenAI SDK is not installed; run .venv/bin/python -m pip install -e ."
        ) from exc
    try:
        with source.open("rb") as image_file:
            result = OpenAI(api_key=api_key, max_retries=0).images.edit(
                model=model,
                image=[image_file],
                prompt=prompt,
                quality=quality,
                size=_closest_request_size(*target_size),
                background=(
                    "opaque" if canvas_policy == OPAQUE_FULL_BLEED else "transparent"
                ),
                output_format="png",
                n=1,
            )
    except Exception as exc:
        request_id = getattr(exc, "request_id", None) or getattr(exc, "requestID", None)
        code = getattr(exc, "code", None)
        status_code = getattr(exc, "status_code", None)
        error_text = f"{type(exc).__name__}: {exc}".lower()
        rate_limited = (
            status_code == 429
            or str(code).lower() in {"rate_limit_exceeded", "rate_limit_error"}
            or "ratelimit" in error_text
            or "rate limit" in error_text
        )
        retryable = rate_limited or (
            isinstance(status_code, int) and status_code >= 500
        ) or any(
            token in error_text
            for token in (
                "timeout",
                "timed out",
                "connection error",
                "connection reset",
                "temporarily unavailable",
            )
        )
        raise SubjectGenerationError(
            f"subject generation failed for {subject.subject_id!r}; "
            f"provider=openai; model={model!r}; code={code!r}; "
            f"status_code={status_code!r}; request_id={request_id!r}; error={exc}",
            retryable=retryable,
            rate_limited=rate_limited,
            retry_after_seconds=_retry_after_seconds(exc),
        ) from exc
    data = getattr(result, "data", None)
    encoded = getattr(data[0], "b64_json", None) if data else None
    if not encoded:
        raise StaticPrintError(
            f"OpenAI returned no image bytes for subject {subject.subject_id!r}"
        )
    try:
        generated = Image.open(io.BytesIO(base64.b64decode(encoded)))
        generated.load()
        normalized = ImageOps.fit(
            generated.convert("RGBA"),
            target_size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        if canvas_policy == OPAQUE_FULL_BLEED:
            # GPT Image can return residual alpha even for a full-canvas edit. The
            # source contract is authoritative: retain RGB pixels and normalize the
            # output to a genuinely opaque canvas.
            normalized = normalized.convert("RGB").convert("RGBA")
    except Exception as exc:
        raise StaticPrintError(
            f"generated image is not a readable PNG for {subject.subject_id!r}: {exc}"
        ) from exc
    return normalized


def openai_subject_validator(
    source: Path, output: Path, subject: Subject
) -> dict[str, Any]:
    """Verify that the principal subject matches the catalog identity contract."""

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise StaticPrintError(
            "subject validation requires OPENAI_API_KEY in the shared environment"
        )
    model = os.environ.get(
        "STATIC_PRINT_SUBJECT_VALIDATION_MODEL", "gpt-5.4-mini"
    ).strip()
    if not model:
        raise StaticPrintError("STATIC_PRINT_SUBJECT_VALIDATION_MODEL cannot be empty")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise StaticPrintError(
            "the OpenAI SDK is not installed; run .venv/bin/python -m pip install -e ."
        ) from exc
    source_encoded = base64.b64encode(source.read_bytes()).decode("ascii")
    output_encoded = base64.b64encode(output.read_bytes()).decode("ascii")
    prompt = (
        "Image A is the approved seed design and Image B is the generated subject "
        "variant. Inspect only the principal illustrated character occupying the "
        "featured-pet role; ignore small decorative animals, printed patterns, and "
        "text. The required featured species is "
        f"{subject.subject_species}. The requested subject identity is: "
        f"{subject.generation_description}. Determine whether Image B preserves Image "
        "A's emotional intent while translating the face, mouth, tongue, pose, and "
        "tail into natural behavior and anatomy for the requested animal. For cats, "
        "dog-like panting or a copied casually protruding tongue is inappropriate. "
        "Prefer a naturally closed feline mouth and translate playful or happy emotion "
        "through cat-appropriate eyes, ears, whiskers, posture, and tail. An open mouth "
        "is acceptable when it clearly expresses a natural feline action in this design "
        "context, such as meowing, hissing, yawning, grooming, licking, eating, or "
        "drinking. A visible tongue is acceptable only when it belongs to that specific "
        "action; mark an ambiguous but plausible expression borderline for operator "
        "review rather than automatically failing it. "
        "A hidden or cropped "
        "tail is acceptable and should be marked appropriate. Report whether the "
        "requested breed/coat/type identity matches and whether a human/person was "
        "substituted into the featured-pet role."
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "featured_subject_species": {
                "type": "string",
                "enum": ["dog", "cat", "human", "other", "none"],
            },
            "expected_subject_visible": {"type": "boolean"},
            "subject_identity_matches": {"type": "boolean"},
            "emotion_preserved": {"type": "boolean"},
            "facial_expression_appropriate": {"type": "boolean"},
            "mouth_and_tongue_appropriate": {"type": "boolean"},
            "tongue_visible": {"type": "boolean"},
            "behavior_context": {
                "type": "string",
                "enum": ["natural", "borderline", "inappropriate"],
            },
            "operator_review_recommended": {"type": "boolean"},
            "pose_appropriate": {"type": "boolean"},
            "tail_appropriate": {"type": "boolean"},
            "human_replaced_pet": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": [
            "featured_subject_species",
            "expected_subject_visible",
            "subject_identity_matches",
            "emotion_preserved",
            "facial_expression_appropriate",
            "mouth_and_tongue_appropriate",
            "tongue_visible",
            "behavior_context",
            "operator_review_recommended",
            "pose_appropriate",
            "tail_appropriate",
            "human_replaced_pet",
            "reason",
        ],
    }
    try:
        response = OpenAI(api_key=api_key).responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{source_encoded}",
                            "detail": "low",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{output_encoded}",
                            "detail": "low",
                        },
                    ],
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "subject_preview_validation",
                    "strict": True,
                    "schema": schema,
                }
            },
            max_output_tokens=350,
            store=False,
            timeout=60,
        )
    except Exception as exc:
        request_id = getattr(exc, "request_id", None) or getattr(exc, "requestID", None)
        code = getattr(exc, "code", None)
        raise StaticPrintError(
            "subject preview validation failed; "
            f"subject_id={subject.subject_id!r}; output={output.resolve()}; "
            f"provider=openai; model={model!r}; code={code!r}; "
            f"request_id={request_id!r}; error={exc}"
        ) from exc
    try:
        value = json.loads(str(getattr(response, "output_text", "") or ""))
    except (TypeError, ValueError) as exc:
        raise StaticPrintError(
            "subject preview validation returned invalid JSON; "
            f"subject_id={subject.subject_id!r}; output={output.resolve()}; "
            f"model={model!r}; response={getattr(response, 'output_text', None)!r}"
        ) from exc
    featured_species = str(value.get("featured_subject_species", ""))
    expected_visible = value.get("expected_subject_visible") is True
    identity_matches = value.get("subject_identity_matches") is True
    emotion_preserved = value.get("emotion_preserved") is True
    facial_expression_appropriate = (
        value.get("facial_expression_appropriate") is True
    )
    mouth_and_tongue_appropriate = (
        value.get("mouth_and_tongue_appropriate") is True
    )
    tongue_visible = value.get("tongue_visible") is True
    behavior_context = str(value.get("behavior_context", "inappropriate"))
    operator_review_recommended = (
        value.get("operator_review_recommended") is True
    )
    pose_appropriate = value.get("pose_appropriate") is True
    tail_appropriate = value.get("tail_appropriate") is True
    human_replaced_pet = value.get("human_replaced_pet") is True
    behavior_is_acceptable = (
        (
            behavior_context == "natural"
            and facial_expression_appropriate
            and mouth_and_tongue_appropriate
        )
        or (
            behavior_context == "borderline"
            and operator_review_recommended
        )
    )
    passed = (
        featured_species == subject.subject_species
        and expected_visible
        and identity_matches
        and emotion_preserved
        and behavior_is_acceptable
        and pose_appropriate
        and tail_appropriate
        and not human_replaced_pet
    )
    return {
        "passed": passed,
        "model": model,
        "request_id": getattr(response, "_request_id", None),
        "expected_species": subject.subject_species,
        "featured_subject_species": featured_species,
        "expected_subject_visible": expected_visible,
        "subject_identity_matches": identity_matches,
        "emotion_preserved": emotion_preserved,
        "facial_expression_appropriate": facial_expression_appropriate,
        "mouth_and_tongue_appropriate": mouth_and_tongue_appropriate,
        "tongue_visible": tongue_visible,
        "behavior_context": behavior_context,
        "operator_review_recommended": operator_review_recommended,
        "pose_appropriate": pose_appropriate,
        "tail_appropriate": tail_appropriate,
        "human_replaced_pet": human_replaced_pet,
        "reason": str(value.get("reason", "")).strip(),
    }


def generate_subject_variants(
    *,
    graduated_root: Path,
    seed_design_id: str,
    subjects: Iterable[Subject],
    generator: SubjectGenerator = openai_subject_generator,
    validator: SubjectValidator | None = None,
    force: bool = False,
    instruction: str = "",
    operation_id: str | None = None,
    before_image_request: Callable[[], None] | None = None,
    provider_max_attempts: int = 4,
    provider_retry_base_seconds: float = 10.0,
    throttle_image_requests: Callable[[float], None] | None = None,
    image_retry_event: Callable[[dict[str, Any]], None] | None = None,
    retry_sleeper: Callable[[float], None] = sleep,
) -> list[dict[str, Any]]:
    seed_design_id = validate_id(seed_design_id, "seed design ID")
    seed = (graduated_root / seed_design_id / "source-design.png").resolve()
    if not seed.is_file():
        raise StaticPrintError(
            f"graduated seed design does not exist: {seed}; graduate the seed first"
        )
    with Image.open(seed) as opened:
        opened.load()
        target_size = opened.size
    canvas_policy, source_transparency = source_canvas_policy(seed)
    selected = list(subjects)
    if not selected:
        raise StaticPrintError("select at least one subject to generate")
    instruction = instruction.strip()
    if len(instruction) > 2000:
        raise StaticPrintError(
            "subject generation instruction must not exceed 2000 characters"
        )
    if provider_max_attempts <= 0:
        raise StaticPrintError("provider_max_attempts must be positive")
    if provider_retry_base_seconds <= 0:
        raise StaticPrintError("provider_retry_base_seconds must be positive")
    results: list[dict[str, Any]] = []
    seed_digest = sha256_file(seed)
    if validator is None and generator is openai_subject_generator:
        validator = openai_subject_validator
    for subject in selected:
        seed_root = graduated_root / seed_design_id
        destination = seed_root / subject.subject_id
        final_output = destination / "source-design.png"
        if final_output.exists() and not force:
            raise StaticPrintError(
                f"subject preview already exists: {final_output.resolve()}; enable regenerate to replace it"
            )
        token = (
            validate_id(operation_id, "operation ID")
            if operation_id
            else secrets.token_hex(8)
        )
        staging = seed_root / f".{subject.subject_id}.partial-{token}"
        backup = seed_root / f".{subject.subject_id}.backup-{token}"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
        staging.mkdir(parents=True)
        output = staging / "source-design.png"
        prompt = subject_prompt(subject, canvas_policy=canvas_policy)
        if instruction:
            prompt += (
                "\n\nOperator improvement instruction: "
                f"{instruction}\nApply this instruction only where it does not "
                "conflict with identity, text accuracy, canvas, or transparency "
                "requirements above."
            )
        generation_prompt = prompt
        generation_source = seed
        validation_attempts: list[dict[str, Any]] = []
        provider_attempts: list[dict[str, Any]] = []
        semantic_max_attempts = 2 if validator is not None else 1
        try:
            for attempt in range(1, semantic_max_attempts + 1):
                for provider_attempt in range(1, provider_max_attempts + 1):
                    if before_image_request is not None:
                        before_image_request()
                    try:
                        image = generator(
                            generation_source,
                            subject,
                            generation_prompt,
                            target_size,
                        )
                    except SubjectGenerationError as exc:
                        provider_attempts.append(
                            {
                                "semantic_attempt": attempt,
                                "provider_attempt": provider_attempt,
                                "outcome": "failed",
                                "retryable": exc.retryable,
                                "rate_limited": exc.rate_limited,
                                "error": str(exc),
                            }
                        )
                        if not exc.retryable or provider_attempt >= provider_max_attempts:
                            raise
                        delay = max(
                            exc.retry_after_seconds or 0.0,
                            provider_retry_base_seconds
                            * (2 ** (provider_attempt - 1)),
                        )
                        retry_details = {
                            "semantic_attempt": attempt,
                            "provider_attempt": provider_attempt,
                            "next_provider_attempt": provider_attempt + 1,
                            "delay_seconds": round(delay, 3),
                            "rate_limited": exc.rate_limited,
                            "error": str(exc),
                        }
                        if image_retry_event is not None:
                            image_retry_event(retry_details)
                        if exc.rate_limited and throttle_image_requests is not None:
                            throttle_image_requests(delay)
                        else:
                            retry_sleeper(delay)
                        continue
                    provider_attempts.append(
                        {
                            "semantic_attempt": attempt,
                            "provider_attempt": provider_attempt,
                            "outcome": "succeeded",
                            "retryable": False,
                            "rate_limited": False,
                        }
                    )
                    break
                if image.size != target_size:
                    image = ImageOps.fit(
                        image.convert("RGBA"),
                        target_size,
                        method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.5),
                    )
                if canvas_policy == OPAQUE_FULL_BLEED:
                    image = image.convert("RGB").convert("RGBA")
                save_png(image.convert("RGBA"), output)
                if validator is None:
                    break
                validation = dict(validator(seed, output, subject))
                validation["attempt"] = attempt
                validation_attempts.append(validation)
                if validation.get("passed") is True:
                    break
                if attempt < semantic_max_attempts:
                    if subject.subject_species == "cat" and (
                        validation.get("mouth_and_tongue_appropriate") is False
                        or validation.get("behavior_context") == "inappropriate"
                    ):
                        generation_source = output
                        generation_prompt = (
                            "Image A is the generated design candidate. Correct only the "
                            "featured cat's species-inappropriate mouth while preserving "
                            "every other pixel and design detail as closely as possible. "
                            "Remove dog-like panting or a casually protruding tongue. Use "
                            "a natural, closed feline muzzle unless the scene clearly calls "
                            "for a cat-specific action such as meowing, hissing, yawning, "
                            "grooming, licking, eating, or drinking; in that case render "
                            "only the anatomically natural mouth/tongue needed for that action. "
                            "Preserve the cat's identity, coat, eyes, ears, whiskers, head "
                            "angle, emotional intent, pose, accessories, text, composition, "
                            "colors, canvas coverage, and rendering style. Return only the "
                            f"complete corrected design. {PRINT_QUALITY_PROMPT}"
                        )
                    else:
                        generation_source = seed
                        generation_prompt = (
                            prompt
                            + "\n\nCorrective retry: the previous candidate failed subject "
                            "validation. Regenerate from Image A. The featured replacement "
                            f"must be {subject.generation_description} and must not be a "
                            "human or human-like character. Validator reason: "
                            f"{validation.get('reason', 'wrong featured subject')}."
                        )
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        if validation_attempts and not validation_attempts[-1].get("passed"):
            failure = validation_attempts[-1]
            shutil.rmtree(staging, ignore_errors=True)
            raise StaticPrintError(
                "generated subject preview failed semantic validation after "
                f"{len(validation_attempts)} attempt(s); "
                f"subject_id={subject.subject_id!r}; "
                f"expected_species={subject.subject_species!r}; "
                f"actual_species={failure.get('featured_subject_species')!r}; "
                f"human_replaced_pet={failure.get('human_replaced_pet')!r}; "
                f"reason={failure.get('reason')!r}"
            )
        output_transparency = transparency_report(output)
        print_readiness = print_readiness_report_for_path(
            output,
            expected_size=target_size,
            canvas_policy=canvas_policy,
        )
        if (
            canvas_policy == OPAQUE_FULL_BLEED
            and output_transparency["has_transparent_pixels"]
        ):
            shutil.rmtree(staging, ignore_errors=True)
            raise StaticPrintError(
                "opaque full-bleed subject generation produced transparency; "
                f"subject_id={subject.subject_id!r}; output={output.resolve()}"
            )
        provenance = {
            "schema": "pawmarvel-static-subject-generation-v1",
            "operation_id": token,
            "generated_at": utc_now(),
            "seed_design_id": seed_design_id,
            "seed_sha256": seed_digest,
            "subject_id": subject.subject_id,
            "output_sha256": sha256_file(output),
            "provider": "openai"
            if generator is openai_subject_generator
            else "injected",
            "model": (
                os.environ.get("STATIC_PRINT_SUBJECT_MODEL", "gpt-image-2.5-sunburst")
                if generator is openai_subject_generator
                else "test-adapter"
            ),
            "quality": (
                os.environ.get("STATIC_PRINT_SUBJECT_QUALITY", "high")
                if generator is openai_subject_generator
                else "n/a"
            ),
            "request_size": (
                _closest_request_size(*target_size)
                if generator is openai_subject_generator
                else f"{target_size[0]}x{target_size[1]}"
            ),
            "output_size": {
                "width": target_size[0],
                "height": target_size[1],
            },
            "canvas_policy": canvas_policy,
            "source_transparency": source_transparency,
            "output_transparency": output_transparency,
            "print_readiness": print_readiness,
            "prompt": generation_prompt,
            "subject_contract": {
                "display_name": subject.display_name,
                "subject_kind": subject.subject_kind,
                "subject_species": subject.subject_species,
                "generation_description": subject.generation_description,
                "hero_strategy": subject.hero_strategy,
            },
            "semantic_validation": {
                "enabled": validator is not None,
                "attempts": validation_attempts,
            },
            "provider_retry": {
                "max_attempts_per_image": provider_max_attempts,
                "base_delay_seconds": provider_retry_base_seconds,
                "requests": provider_attempts,
            },
            "review_attention": (
                {
                    "required": True,
                    "kind": "subject-behavior",
                    "behavior_context": validation_attempts[-1].get(
                        "behavior_context", "borderline"
                    ),
                    "reason": validation_attempts[-1].get(
                        "reason", "Animal behavior needs operator review."
                    ),
                }
                if validation_attempts
                and validation_attempts[-1].get(
                    "operator_review_recommended"
                )
                else None
            ),
            "operator_instruction": instruction,
        }
        try:
            write_json(staging / "subject-generation.json", provenance)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        try:
            if destination.exists():
                os.replace(destination, backup)
            os.replace(staging, destination)
        except Exception:
            if not destination.exists() and backup.exists():
                os.replace(backup, destination)
            raise
        else:
            shutil.rmtree(backup, ignore_errors=True)
        output = final_output
        results.append(
            {
                "subject_id": subject.subject_id,
                "design_id": DesignIdentity(
                    seed_design_id, subject.subject_id
                ).design_id,
                "output": str(output.resolve()),
                "source_sha256": provenance["output_sha256"],
                "transparency": transparency_report(output),
                "print_readiness": print_readiness,
                "print_readiness_warning": print_readiness_warning(
                    f"subject preview {seed_design_id}/{subject.subject_id}",
                    print_readiness,
                ),
                "semantic_validation": (
                    validation_attempts[-1] if validation_attempts else None
                ),
                "operator_review_recommended": bool(
                    validation_attempts
                    and validation_attempts[-1].get(
                        "operator_review_recommended"
                    )
                ),
            }
        )
    return results
