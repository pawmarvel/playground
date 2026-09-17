"""Create deterministic local review artifacts for experiment comparisons."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .artifact_io import sha256
from .renderer import render_to_files


class ComparisonArtifactError(ValueError):
    """A comparison review artifact cannot be created immutably."""


def render_comparison_contact_sheet(
    *,
    kind: str,
    authoring_product: Path,
    review_id: str,
    candidates: list[dict[str, Any]],
    composition_template: Path | None = None,
    composition_pet_name: str | None = None,
) -> dict[str, Any] | None:
    """Render labeled thumbnails for art, pet, or layout attempts."""
    filenames = {
        "art": "art.png",
        "pet": "transformed-pet.png",
        "layout": "preview.png",
    }
    if kind not in filenames:
        raise ComparisonArtifactError("contact sheets support art, pet, or layout")
    filename = filenames[kind]
    composed_pet = kind == "pet" and composition_template is not None
    if composed_pet and not composition_pet_name:
        raise ComparisonArtifactError(
            "pet composition contact sheet requires the layout fixture name"
        )
    artifact_kind = (
        "pet-composition-contact-sheet" if composed_pet else f"{kind}-contact-sheet"
    )
    tiles: list[dict[str, Any]] = []
    for candidate in candidates:
        experiment_id = candidate["experiment_id"]
        experiment = authoring_product / "experiments" / kind / experiment_id
        generation = candidate.get("configuration") or {}
        parameters = generation.get("parameters") or {}
        for attempt in candidate.get("attempts", []):
            art = (
                experiment
                / "attempts"
                / attempt["attempt_id"]
                / "outputs"
                / filename
            )
            try:
                with Image.open(art) as image:
                    image.verify()
            except OSError:
                continue
            tiles.append(
                {
                    "experiment_id": experiment_id,
                    "attempt_id": attempt["attempt_id"],
                    "path": art,
                    "sha256": sha256(art),
                    "duration_seconds": attempt.get("duration_seconds"),
                    "hard_gates": attempt["hard_gates"]["status"],
                    "input_pet_sha256": (
                        attempt.get("input_pet_sha256")
                        or attempt.get("representative_pet_sha256")
                    ),
                    "fixture_label": " / ".join(
                        str(value)
                        for value in (
                            attempt.get("fixture_id"),
                            attempt.get("fixture_species"),
                            attempt.get("fixture_breed"),
                            attempt.get("fixture_size_class"),
                        )
                        if value
                    ),
                    "configuration": attempt.get("review_label")
                    or (
                        f"{generation.get('provider', '?')}/"
                        f"{generation.get('model', '?')} "
                        f"quality={parameters.get('quality', '?')}"
                    ),
                }
            )
    if len(tiles) < 2:
        return None
    tiles.sort(
        key=lambda tile: (
            tile["input_pet_sha256"] or "",
            tile["experiment_id"],
            tile["attempt_id"],
        )
    )

    destination = authoring_product / "reviews" / kind / review_id / "artifacts"
    partial = destination.with_name(destination.name + ".partial")
    if destination.exists() or partial.exists():
        raise ComparisonArtifactError(
            f"review artifacts already exist or are partial: {destination}"
        )
    partial.mkdir(parents=True)
    try:
        if composed_pet:
            composed = partial / "composed"
            composed.mkdir()
            for tile in tiles:
                preview = composed / f"{tile['experiment_id']}--{tile['attempt_id']}.png"
                render_to_files(
                    template_dir=composition_template,
                    pet_image=tile["path"],
                    pet_name=composition_pet_name,
                    output=preview,
                )
                tile["review_path"] = preview
        else:
            for tile in tiles:
                tile["review_path"] = tile["path"]

        columns = min(3, len(tiles))
        rows = (len(tiles) + columns - 1) // columns
        cell_width, cell_height = 360, 430
        image_left, image_top, image_size = 20, 62, 320
        sheet = Image.new(
            "RGB",
            (columns * cell_width, rows * cell_height),
            (235, 235, 235),
        )
        draw = ImageDraw.Draw(sheet)
        for index, tile in enumerate(tiles):
            left = (index % columns) * cell_width
            top = (index // columns) * cell_height
            draw.rectangle(
                (left + 8, top + 8, left + cell_width - 8, top + cell_height - 8),
                fill=(255, 255, 255),
                outline=(180, 180, 180),
            )
            draw.text(
                (left + 16, top + 16),
                tile["experiment_id"][:48],
                fill=(20, 20, 20),
            )
            draw.text(
                (left + 16, top + 29),
                tile["configuration"][:56],
                fill=(70, 70, 70),
            )
            detail = f"{tile['attempt_id'][:28]}  gates={tile['hard_gates']}"
            if isinstance(tile["duration_seconds"], (int, float)):
                detail += f"  {float(tile['duration_seconds']):.1f}s"
            draw.text((left + 16, top + 44), detail, fill=(70, 70, 70))
            if tile["fixture_label"]:
                draw.text(
                    (left + 16, top + 54),
                    tile["fixture_label"][:56],
                    fill=(70, 70, 70),
                )

            checker = _checkerboard(image_size)
            with Image.open(tile["review_path"]) as source:
                preview = source.convert("RGBA")
                preview.thumbnail((image_size, image_size), Image.Resampling.LANCZOS)
                x = (image_size - preview.width) // 2
                y = (image_size - preview.height) // 2
                checker.paste(preview, (x, y), preview)
            sheet.paste(checker, (left + image_left, top + image_top))
            draw.text(
                (left + 16, top + 390),
                f"sha256={tile['sha256'][:16]}...",
                fill=(70, 70, 70),
            )

        sheet_name = "pet-composition-comparison.png" if composed_pet else f"{kind}-comparison.png"
        contact_sheet = partial / sheet_name
        sheet.save(contact_sheet, format="PNG")
        os.replace(partial, destination)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        # Creating artifacts/.partial may also create the otherwise-empty
        # immutable review directory. Remove it so a corrected comparison can
        # reuse the same review ID.
        try:
            destination.parent.rmdir()
        except OSError:
            pass
        raise

    sheet_name = "pet-composition-comparison.png" if composed_pet else f"{kind}-comparison.png"
    contact_sheet = destination / sheet_name
    candidate_descriptors = []
    for tile in tiles:
        descriptor = {
            "experiment_id": tile["experiment_id"],
            "attempt_id": tile["attempt_id"],
            "source_sha256": tile["sha256"],
            "input_pet_sha256": tile["input_pet_sha256"],
        }
        if composed_pet:
            composed_preview = (
                destination
                / "composed"
                / f"{tile['experiment_id']}--{tile['attempt_id']}.png"
            )
            descriptor.update(
                review_path=composed_preview.relative_to(authoring_product).as_posix(),
                review_sha256=sha256(composed_preview),
            )
        candidate_descriptors.append(descriptor)

    return {
        "kind": artifact_kind,
        "path": contact_sheet.relative_to(authoring_product).as_posix(),
        "media_type": "image/png",
        "sha256": sha256(contact_sheet),
        "bytes": contact_sheet.stat().st_size,
        "candidates": candidate_descriptors,
    }


def _checkerboard(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), (244, 244, 244))
    draw = ImageDraw.Draw(image)
    block = 16
    for y in range(0, size, block):
        for x in range(0, size, block):
            if (x // block + y // block) % 2:
                draw.rectangle(
                    (x, y, min(x + block - 1, size - 1), min(y + block - 1, size - 1)),
                    fill=(216, 216, 216),
                )
    return image
