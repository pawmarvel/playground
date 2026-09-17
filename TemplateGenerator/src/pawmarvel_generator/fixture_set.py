"""Load and validate immutable pet-image fixture sets used for offline QA."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .artifact_io import atomic_json, read_json, sha256


class FixtureSetError(ValueError):
    """A fixture-set manifest or one of its pinned images is invalid."""


_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_TIERS = {"smoke": (2, 3), "release": (6, 15)}
_SPECIES = {"cat", "dog"}
_SIZES = {"toy", "small", "medium", "large", "giant"}
_COAT_LENGTHS = {"short", "medium", "long"}
_COAT_TEXTURES = {"smooth", "double", "curly", "wavy", "fluffy", "wire"}
_TONE = {"light", "medium", "dark", "mixed"}
_FRAMING = {"full-body", "three-quarter", "head-and-shoulders"}
_VIEW = {"front", "three-quarter", "side"}
_COVERAGE = {"isolated", "mostly-isolated", "environmental"}
_BACKGROUND = {"transparent", "simple", "complex"}


@dataclass(frozen=True)
class PetFixture:
    id: str
    image: Path
    image_sha256: str
    species: str
    breed_id: str
    breed_label: str
    size_class: str
    morphology: tuple[str, ...]
    risk_tags: tuple[str, ...]

    def labels(self) -> dict[str, Any]:
        return {
            "fixture_id": self.id,
            "fixture_species": self.species,
            "fixture_breed": self.breed_label,
            "fixture_size_class": self.size_class,
            "fixture_morphology": list(self.morphology),
            "fixture_risk_tags": list(self.risk_tags),
        }


@dataclass(frozen=True)
class PetFixtureSet:
    path: Path
    fixture_set_id: str
    tier: str
    attempts_per_fixture: int
    fixtures: tuple[PetFixture, ...]

    def summary(self) -> dict[str, Any]:
        def values(attribute: str) -> list[str]:
            return sorted({str(getattr(fixture, attribute)) for fixture in self.fixtures})

        return {
            "fixture_set": str(self.path),
            "fixture_set_id": self.fixture_set_id,
            "tier": self.tier,
            "fixture_count": len(self.fixtures),
            "attempts_per_fixture": self.attempts_per_fixture,
            "species": values("species"),
            "size_classes": values("size_class"),
            "morphology": sorted(
                {tag for fixture in self.fixtures for tag in fixture.morphology}
            ),
            "risk_tags": sorted(
                {tag for fixture in self.fixtures for tag in fixture.risk_tags}
            ),
        }


_FILTER_FIELDS = {"id", "species", "breed", "size_class", "morphology", "risk_tag"}


def select_fixtures(
    fixture_set: PetFixtureSet,
    *,
    fixture_count: int | None = None,
    filters: tuple[str, ...] = (),
) -> tuple[PetFixture, ...]:
    """Resolve a deterministic tier-valid subset from repeatable FIELD=VALUE filters."""
    parsed: dict[str, set[str]] = {}
    for condition in filters:
        if not isinstance(condition, str) or "=" not in condition:
            raise FixtureSetError(
                "fixture filter must use FIELD=VALUE; supported fields are "
                + ", ".join(sorted(_FILTER_FIELDS))
            )
        field, raw_value = condition.split("=", 1)
        field = field.strip()
        raw_value = raw_value.strip()
        if field not in _FILTER_FIELDS:
            raise FixtureSetError(
                f"unsupported fixture filter field {field!r}; supported fields are "
                + ", ".join(sorted(_FILTER_FIELDS))
            )
        value = _identifier(raw_value, f"fixture filter {field}")
        parsed.setdefault(field, set()).add(value)

    def matches(fixture: PetFixture) -> bool:
        values = {
            "id": {fixture.id},
            "species": {fixture.species},
            "breed": {fixture.breed_id},
            "size_class": {fixture.size_class},
            "morphology": set(fixture.morphology),
            "risk_tag": set(fixture.risk_tags),
        }
        return all(values[field] & accepted for field, accepted in parsed.items())

    matched = tuple(fixture for fixture in fixture_set.fixtures if matches(fixture))
    if fixture_count is not None:
        if isinstance(fixture_count, bool) or fixture_count < 1:
            raise FixtureSetError("fixture count must be a positive integer")
        if fixture_count > len(matched):
            raise FixtureSetError(
                "fixture selection is smaller than the requested run size; "
                f"fixture_set={fixture_set.fixture_set_id}; filters={list(filters)}; "
                f"matched={len(matched)}; requested={fixture_count}"
            )
        matched = matched[:fixture_count]

    minimum, maximum = _TIERS[fixture_set.tier]
    if not minimum <= len(matched) <= maximum:
        raise FixtureSetError(
            f"{fixture_set.tier} run must select {minimum}-{maximum} pets; "
            f"filters={list(filters)}; selected={len(matched)}; "
            f"available={len(fixture_set.fixtures)}"
        )
    return matched


def write_fixture_selection(
    fixture_set_path: Path,
    *,
    output: Path,
    fixture_count: int | None = None,
    filters: tuple[str, ...] = (),
    force: bool = False,
) -> Path:
    """Write a reviewable, no-cost fixture selection for a later benchmark."""
    fixture_set = load_fixture_set(fixture_set_path)
    selected = select_fixtures(
        fixture_set,
        fixture_count=fixture_count,
        filters=filters,
    )
    output = output.expanduser().resolve()
    if output.exists() and not force:
        raise FixtureSetError(
            f"fixture selection already exists: {output}; use --force to replace it"
        )
    if output.exists() and not output.is_file():
        raise FixtureSetError(f"fixture selection output is not a file: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(
        output,
        {
            "schema_version": 1,
            "fixture_set_id": fixture_set.fixture_set_id,
            "fixture_set_sha256": sha256(fixture_set.path),
            "tier": fixture_set.tier,
            "source_filter": {
                "conditions": list(filters),
                "requested_count": fixture_count,
            },
            "selected_fixture_ids": [fixture.id for fixture in selected],
        },
    )
    return output


def load_fixture_selection(
    fixture_set: PetFixtureSet,
    selection_path: Path,
) -> tuple[PetFixture, ...]:
    """Resolve an operator-reviewed selection against its exact fixture set."""
    selection_path = selection_path.expanduser().resolve()
    value = read_json(
        selection_path,
        label="fixture selection",
        error_type=FixtureSetError,
        require_object=True,
        correction="regenerate it with prepare-benchmark or correct the selected IDs.",
    )
    if value.get("schema_version") != 1:
        raise FixtureSetError(
            f"fixture selection must use schema_version 1: {selection_path}"
        )
    if value.get("fixture_set_id") != fixture_set.fixture_set_id:
        raise FixtureSetError(
            "fixture selection belongs to a different fixture set; "
            f"selection={selection_path}; expected={fixture_set.fixture_set_id!r}; "
            f"actual={value.get('fixture_set_id')!r}"
        )
    actual_fixture_set_hash = sha256(fixture_set.path)
    if value.get("fixture_set_sha256") != actual_fixture_set_hash:
        raise FixtureSetError(
            "fixture selection was generated from different fixture-set bytes; "
            f"selection={selection_path}; expected={actual_fixture_set_hash}; "
            f"actual={value.get('fixture_set_sha256')!r}"
        )
    if value.get("tier") != fixture_set.tier:
        raise FixtureSetError(
            "fixture selection tier does not match the fixture set; "
            f"selection={selection_path}; expected={fixture_set.tier!r}; "
            f"actual={value.get('tier')!r}"
        )
    selected_ids = value.get("selected_fixture_ids")
    if not isinstance(selected_ids, list) or not selected_ids:
        raise FixtureSetError(
            f"fixture selection requires a nonempty selected_fixture_ids array: {selection_path}"
        )
    if not all(isinstance(fixture_id, str) for fixture_id in selected_ids):
        raise FixtureSetError(
            f"fixture selection IDs must be strings: {selection_path}"
        )
    if len(selected_ids) != len(set(selected_ids)):
        raise FixtureSetError(
            f"fixture selection IDs must be unique: {selection_path}"
        )
    fixtures_by_id = {fixture.id: fixture for fixture in fixture_set.fixtures}
    missing = [fixture_id for fixture_id in selected_ids if fixture_id not in fixtures_by_id]
    if missing:
        raise FixtureSetError(
            "fixture selection contains IDs absent from its fixture set; "
            f"selection={selection_path}; missing={missing}"
        )
    selected = tuple(fixtures_by_id[fixture_id] for fixture_id in selected_ids)
    minimum, maximum = _TIERS[fixture_set.tier]
    if not minimum <= len(selected) <= maximum:
        raise FixtureSetError(
            f"{fixture_set.tier} fixture selection must contain {minimum}-{maximum} pets; "
            f"selection={selection_path}; selected={len(selected)}"
        )
    return selected


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise FixtureSetError(
            f"{label} must use lowercase letters, numbers, and internal hyphens: {value!r}"
        )
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FixtureSetError(f"{label} must be an object")
    return value


def _choice(value: Any, choices: set[str], label: str) -> str:
    if value not in choices:
        raise FixtureSetError(f"{label} must be one of {sorted(choices)}; got {value!r}")
    return str(value)


def _tags(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise FixtureSetError(f"{label} must be a nonempty array")
    tags = tuple(_identifier(item, label) for item in value)
    if len(tags) != len(set(tags)):
        raise FixtureSetError(f"{label} must not contain duplicates")
    return tags


def load_fixture_set(path: Path) -> PetFixtureSet:
    """Load a v2 manifest and verify every referenced image and pinned hash."""
    path = path.expanduser().resolve()
    value = read_json(
        path,
        label="fixture set",
        error_type=FixtureSetError,
        require_object=True,
        correction="fix the manifest or create a new immutable fixture-set version.",
    )
    if value.get("schema_version") != 2:
        raise FixtureSetError(
            f"fixture set must use schema_version 2: {path}; got {value.get('schema_version')!r}"
        )
    fixture_set_id = _identifier(value.get("fixture_set_id"), "fixture_set_id")
    tier = _choice(value.get("tier"), set(_TIERS), "tier")
    attempts = value.get("attempts_per_fixture")
    if attempts != 1:
        raise FixtureSetError(
            f"attempts_per_fixture must be 1 for MVP {tier} fixtures; got {attempts!r}"
        )
    raw_fixtures = value.get("fixtures")
    minimum, maximum = _TIERS[tier]
    if (
        not isinstance(raw_fixtures, list)
        or not minimum <= len(raw_fixtures) <= maximum
    ):
        raise FixtureSetError(
            f"{tier} fixture set must contain {minimum}-{maximum} pets; "
            f"got {len(raw_fixtures) if isinstance(raw_fixtures, list) else 'non-array'}"
        )

    fixtures: list[PetFixture] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for index, raw in enumerate(raw_fixtures):
        fixture = _object(raw, f"fixtures[{index}]")
        fixture_id = _identifier(fixture.get("id"), f"fixtures[{index}].id")
        if fixture_id in seen_ids:
            raise FixtureSetError(f"duplicate fixture id: {fixture_id}")
        relative_value = fixture.get("pet_image")
        if not isinstance(relative_value, str) or not relative_value.strip():
            raise FixtureSetError(f"fixtures[{index}].pet_image must be a relative path")
        relative = Path(relative_value)
        if relative.is_absolute():
            raise FixtureSetError(f"fixture pet_image must be relative: {relative_value}")
        image = (path.parent / relative).resolve()
        if not image.is_file():
            raise FixtureSetError(
                f"fixture image does not exist: fixture={fixture_id}; resolved_path={image}"
            )
        declared_hash = fixture.get("sha256")
        actual_hash = sha256(image)
        if declared_hash != actual_hash:
            raise FixtureSetError(
                f"fixture image SHA-256 mismatch: fixture={fixture_id}; "
                f"expected={declared_hash!r}; actual={actual_hash}; resolved_path={image}"
            )
        if actual_hash in seen_hashes:
            raise FixtureSetError(
                f"duplicate fixture image bytes: fixture={fixture_id}; sha256={actual_hash}"
            )
        try:
            with Image.open(image) as opened:
                opened.verify()
        except OSError as exc:
            raise FixtureSetError(
                f"fixture image is not decodable: fixture={fixture_id}; resolved_path={image}"
            ) from exc

        breed = _object(fixture.get("breed"), f"fixtures[{index}].breed")
        breed_id = _identifier(breed.get("id"), f"fixtures[{index}].breed.id")
        breed_label = breed.get("label")
        if not isinstance(breed_label, str) or not breed_label.strip():
            raise FixtureSetError(f"fixtures[{index}].breed.label must be nonempty")
        if not isinstance(breed.get("mixed"), bool):
            raise FixtureSetError(f"fixtures[{index}].breed.mixed must be boolean")

        coat = _object(fixture.get("coat"), f"fixtures[{index}].coat")
        _choice(coat.get("length"), _COAT_LENGTHS, f"fixtures[{index}].coat.length")
        _choice(coat.get("texture"), _COAT_TEXTURES, f"fixtures[{index}].coat.texture")
        _choice(coat.get("tone"), _TONE, f"fixtures[{index}].coat.tone")
        capture = _object(fixture.get("capture"), f"fixtures[{index}].capture")
        _choice(capture.get("framing"), _FRAMING, f"fixtures[{index}].capture.framing")
        _choice(capture.get("view"), _VIEW, f"fixtures[{index}].capture.view")
        _choice(
            capture.get("subject_coverage"),
            _COVERAGE,
            f"fixtures[{index}].capture.subject_coverage",
        )
        _choice(
            capture.get("background_complexity"),
            _BACKGROUND,
            f"fixtures[{index}].capture.background_complexity",
        )
        rights = _object(fixture.get("rights"), f"fixtures[{index}].rights")
        for field in ("source_kind", "license", "intended_use"):
            if not isinstance(rights.get(field), str) or not rights[field].strip():
                raise FixtureSetError(f"fixtures[{index}].rights.{field} must be nonempty")
        if not isinstance(rights.get("reviewed"), bool):
            raise FixtureSetError(f"fixtures[{index}].rights.reviewed must be boolean")
        if "modifications" in rights and (
            not isinstance(rights["modifications"], str)
            or not rights["modifications"].strip()
        ):
            raise FixtureSetError(
                f"fixtures[{index}].rights.modifications must be nonempty when present"
            )

        fixtures.append(
            PetFixture(
                id=fixture_id,
                image=image,
                image_sha256=actual_hash,
                species=_choice(
                    fixture.get("species"), _SPECIES, f"fixtures[{index}].species"
                ),
                breed_id=breed_id,
                breed_label=breed_label.strip(),
                size_class=_choice(
                    fixture.get("size_class"), _SIZES, f"fixtures[{index}].size_class"
                ),
                morphology=_tags(
                    fixture.get("morphology"), f"fixtures[{index}].morphology"
                ),
                risk_tags=_tags(
                    fixture.get("risk_tags"), f"fixtures[{index}].risk_tags"
                ),
            )
        )
        seen_ids.add(fixture_id)
        seen_hashes.add(actual_hash)

    return PetFixtureSet(
        path=path,
        fixture_set_id=fixture_set_id,
        tier=tier,
        attempts_per_fixture=attempts,
        fixtures=tuple(fixtures),
    )
