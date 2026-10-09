"""Operator-facing discovery and execution for the graduated-design workflow.

This module deliberately delegates every state-changing operation to the same
immutable authoring, bundle, and release functions used by the command-line
guide.  The gallery is an operator console over those contracts, not a second
artifact format.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .artifact_io import atomic_json, sha256
from .authoring import (
    benchmark,
    compare,
    create_experiment,
    graduate,
    prepare_print_candidate,
    record_decision,
    run_attempt,
)
from .fixture_set import load_fixture_set, write_fixture_selection
from .layout_proposal import propose_layout
from .production_bundle import build_from_selection
from .release_catalog import build_release
from .s3_publisher import publish_s3


class OperatorWorkflowError(ValueError):
    """Raised for an invalid or incomplete operator workflow action."""


_SAFE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_ARTIFACT_NAMES = {
    "art": "art-comparison.png",
    "pet": "pet-comparison.png",
    "layout": "layout-comparison.png",
    "assembly": "preview.png",
}


@dataclass(frozen=True)
class OperatorWorkflowConfig:
    project_root: Path
    authoring_root: Path
    exchange_root: Path
    jobs_root: Path
    graduation_root: Path | None = None
    release_root: Path | None = None
    s3_bucket: str | None = None
    s3_prefix: str = ""
    aws_profile: str | None = None
    aws_region: str | None = None

    @property
    def evaluation_protocol(self) -> Path:
        return (
            self.project_root
            / "examples/authoring/evaluation-protocols/mvp-image-v1.json"
        )

    def fixture_set(self, tier: str) -> Path:
        folder = "mvp-pets-smoke-v1" if tier == "smoke" else "mvp-pets-v1"
        return self.project_root / "examples/authoring/fixture-sets" / folder / "fixture-set.json"

    @property
    def font_catalog(self) -> Path:
        return self.project_root / "assets/fonts"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperatorWorkflowError(f"not readable JSON: {path.resolve()}") from exc
    if not isinstance(value, dict):
        raise OperatorWorkflowError(f"JSON document must be an object: {path.resolve()}")
    return value


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value.strip()) is None:
        raise OperatorWorkflowError(
            f"{label} must use lowercase letters, digits, dots, underscores, or hyphens; "
            f"actual={value!r}"
        )
    return value.strip()


def _contained(root: Path, *parts: str) -> Path:
    root = root.resolve()
    result = root.joinpath(*parts).resolve()
    if not result.is_relative_to(root):
        raise OperatorWorkflowError(f"resolved path escapes configured root: {result}")
    return result


def _asset_url(path: Path, assets: dict[str, Path]) -> str:
    resolved = path.resolve()
    token = sha256_text(str(resolved))[:24]
    url = f"/operator-workflow-assets/{token}{resolved.suffix.lower()}"
    assets[url] = resolved
    return url


def sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def discover_releases(
    exchange_root: Path,
    authoring_root: Path | None = None,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Index local releases and mark entries backed by publication receipts."""
    releases: dict[tuple[str, str], list[dict[str, Any]]] = {}
    published: set[tuple[str, str, int]] = set()
    invalidated: set[tuple[str, str, int]] = set()
    if authoring_root is not None and authoring_root.is_dir():
        for receipt in authoring_root.glob("*/*/graduations/*/publications/*.json"):
            try:
                value = _read_json(receipt)
            except OperatorWorkflowError:
                continue
            release_id = value.get("release_id")
            template_id = value.get("template_id")
            revision = value.get("bundle_revision")
            if (
                isinstance(release_id, str)
                and isinstance(template_id, str)
                and isinstance(revision, int)
            ):
                published.add((release_id, template_id, revision))
        for event_path in authoring_root.glob("*/*/operator-iterations/*.json"):
            try:
                event = _read_json(event_path)
            except OperatorWorkflowError:
                continue
            for item in event.get("invalidated_releases", []):
                if not isinstance(item, dict):
                    continue
                release_id = item.get("release_id")
                template_id = item.get("template_id")
                revision = item.get("bundle_revision")
                if (
                    isinstance(release_id, str)
                    and isinstance(template_id, str)
                    and isinstance(revision, int)
                ):
                    invalidated.add((release_id, template_id, revision))
    root = exchange_root.resolve() / "releases"
    if not root.is_dir():
        return releases
    for catalog in sorted(root.glob("*/catalog.json")):
        try:
            value = _read_json(catalog)
        except OperatorWorkflowError:
            continue
        release_id = str(value.get("release_id", catalog.parent.name))
        for entry in value.get("templates", []):
            if not isinstance(entry, dict):
                continue
            design_id = entry.get("design_id")
            profile_id = entry.get("product_profile_id")
            if not isinstance(design_id, str) or not isinstance(profile_id, str):
                continue
            release_entry = {
                "release_id": release_id,
                "bundle_revision": entry.get("bundle_revision"),
                "template_id": entry.get("template_id"),
                "catalog": str(catalog.resolve()),
                "manifest_path": entry.get("manifest_path"),
            }
            release_entry["published"] = (
                release_id,
                str(release_entry["template_id"]),
                int(release_entry["bundle_revision"]),
            ) in published if isinstance(release_entry["bundle_revision"], int) else False
            release_entry["active"] = (
                release_id,
                str(release_entry["template_id"]),
                int(release_entry["bundle_revision"]),
            ) not in invalidated if isinstance(release_entry["bundle_revision"], int) else True
            releases.setdefault((design_id, profile_id), []).append(release_entry)
    for entries in releases.values():
        entries.sort(key=lambda item: (str(item["release_id"]), int(item.get("bundle_revision") or 0)))
    return releases


def _experiment_entry(experiment: Path) -> dict[str, Any] | None:
    meta_path = experiment / "experiment.json"
    if not meta_path.is_file():
        return None
    try:
        meta = _read_json(meta_path)
    except OperatorWorkflowError:
        return None
    prompt_text = None
    prompt_name = None
    prompt = meta.get("inputs", {}).get("prompt")
    if isinstance(prompt, dict) and isinstance(prompt.get("path"), str):
        prompt_path = experiment / prompt["path"]
        if prompt_path.is_file():
            prompt_name = prompt_path.name
            try:
                prompt_text = prompt_path.read_text(encoding="utf-8")
            except OSError:
                prompt_text = None
    attempts = []
    for attempt in sorted((experiment / "attempts").glob("*")):
        run_path = attempt / "run.json"
        if not attempt.is_dir() or not run_path.is_file():
            continue
        try:
            run = _read_json(run_path)
        except OperatorWorkflowError:
            continue
        attempts.append(
            {
                "attempt_id": attempt.name,
                "status": run.get("status"),
                "duration_seconds": run.get("duration_seconds"),
                "error": run.get("error"),
            }
        )
    return {
        "experiment_id": experiment.name,
        "status": meta.get("status"),
        "generation": meta.get("generation"),
        "prompt_name": prompt_name,
        "prompt_text": prompt_text,
        "attempts": attempts,
        "created_at": meta.get("created_at"),
    }


def _review_entry(review: Path, assets: dict[str, Path]) -> dict[str, Any] | None:
    evaluation_path = review / "evaluation.json"
    if not evaluation_path.is_file():
        return None
    try:
        evaluation = _read_json(evaluation_path)
    except OperatorWorkflowError as exc:
        kind = review.parent.name
        artifact_name = _ARTIFACT_NAMES.get(kind)
        comparison = review / "artifacts" / str(artifact_name)
        if kind == "pet":
            composed = review / "artifacts/pet-composition-comparison.png"
            if composed.is_file():
                comparison = composed
        return {
            "review_id": review.name,
            "kind": kind,
            "created_at": None,
            "review_mode": None,
            "hard_gates": {"status": "failed"},
            "warnings": [str(exc)],
            "measurements": {},
            "fixture_selection": None,
            "human_review": None,
            "fixture_tier": None,
            "attempt_id_prefix": None,
            "candidates": [],
            "evaluation_url": _asset_url(evaluation_path, assets),
            "comparison_url": (
                _asset_url(comparison, assets) if comparison.is_file() else None
            ),
            "composed": kind == "pet" and comparison.name == "pet-composition-comparison.png",
            "decision": None,
            "decision_error": f"Evaluation is unreadable and cannot be approved: {exc}",
        }
    kind = str(evaluation.get("kind", review.parent.name))
    artifact_name = _ARTIFACT_NAMES.get(kind)
    comparison = review / "artifacts" / str(artifact_name)
    if kind == "pet":
        composed = review / "artifacts/pet-composition-comparison.png"
        if composed.is_file():
            comparison = composed
    if kind == "assembly":
        comparison = review / "artifacts/preview.png"
    entry: dict[str, Any] = {
        "review_id": review.name,
        "kind": kind,
        "created_at": evaluation.get("created_at"),
        "review_mode": evaluation.get("review_mode"),
        "hard_gates": evaluation.get("hard_gates"),
        "warnings": evaluation.get("warnings", []),
        "measurements": evaluation.get("measurements", {}),
        "fixture_selection": evaluation.get("fixture_selection"),
        "human_review": evaluation.get("human_review"),
        "fixture_tier": evaluation.get("fixture_tier"),
        "attempt_id_prefix": evaluation.get("attempt_id_prefix"),
        "candidates": evaluation.get("candidates", []),
        "evaluation_url": _asset_url(evaluation_path, assets),
        "comparison_url": _asset_url(comparison, assets) if comparison.is_file() else None,
        "composed": kind == "pet" and comparison.name == "pet-composition-comparison.png",
        "decision": None,
    }
    decision_path = review / "decision.json"
    if decision_path.is_file():
        try:
            decision = _read_json(decision_path)
            entry["decision"] = decision
            entry["decision_url"] = _asset_url(decision_path, assets)
        except OperatorWorkflowError:
            entry["decision_error"] = f"Unreadable decision: {decision_path}"
    retirement_path = review / "decision-retired.json"
    review_retirement_path = review / "review-retired.json"
    retirement = retirement_path if retirement_path.is_file() else review_retirement_path
    entry["active"] = not retirement.is_file()
    if retirement.is_file():
        try:
            entry["retirement"] = _read_json(retirement)
            entry["retirement_url"] = _asset_url(retirement, assets)
        except OperatorWorkflowError:
            entry["retirement"] = {"reason": f"Unreadable retirement record: {retirement}"}
    return entry


def _retired_layout_experiments(product: Path) -> set[str]:
    retired: set[str] = set()
    for event_path in (product / "operator-iterations").glob("*.json"):
        try:
            event = _read_json(event_path)
        except OperatorWorkflowError:
            continue
        retired.update(
            str(value)
            for value in event.get("invalidated_layout_experiments", [])
            if isinstance(value, str)
        )
    return retired


def _proposal_entries(product: Path, assets: dict[str, Path]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    retired_experiments = _retired_layout_experiments(product)
    for record_path in sorted(product.glob("experiments/layout/*/proposals/*/proposal.json")):
        try:
            record = _read_json(record_path)
        except OperatorWorkflowError:
            continue
        proposal_root = record_path.parent
        ranked = proposal_root / "ranked-layout-proposals.png"
        candidates = []
        for candidate in record.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            layout = proposal_root / str(candidate.get("layout", ""))
            preview = proposal_root / str(candidate.get("preview", ""))
            if not layout.is_file() or not preview.is_file():
                continue
            candidates.append(
                {
                    "rank": candidate.get("rank"),
                    "candidate_id": candidate.get("candidate_id"),
                    "metrics": candidate.get("metrics", {}),
                    "preview_url": _asset_url(preview, assets),
                    "layout_path": str(layout.resolve()),
                }
            )
        result.append(
            {
                "experiment_id": record_path.parents[2].name,
                "proposal_id": proposal_root.name,
                "created_at": record.get("created_at"),
                "duration_seconds": record.get("duration_seconds"),
                "name_mode": record.get("name_mode"),
                "warnings": record.get("warnings", []),
                "ranked_url": _asset_url(ranked, assets) if ranked.is_file() else None,
                "candidates": candidates,
                "active": record_path.parents[2].name not in retired_experiments,
            }
        )
    return sorted(
        result,
        key=lambda item: (
            str(item.get("created_at") or ""),
            str(item.get("experiment_id") or ""),
            str(item.get("proposal_id") or ""),
        ),
    )


def _print_candidate_entries(
    product: Path, assets: dict[str, Path]
) -> list[dict[str, Any]]:
    """Expose immutable print finalists and their review images to the operator."""
    invalidated: set[str] = set()
    for event_path in (product / "operator-iterations").glob("*.json"):
        try:
            event = _read_json(event_path)
        except OperatorWorkflowError:
            continue
        invalidated.update(
            str(value)
            for value in event.get("invalidated_print_candidates", [])
            if isinstance(value, str)
        )
    result: list[dict[str, Any]] = []
    for candidate in sorted((product / "print-candidates").glob("*")):
        record_path = candidate / "print-candidate.json"
        if not candidate.is_dir() or not record_path.is_file():
            continue
        try:
            record = _read_json(record_path)
        except OperatorWorkflowError:
            continue
        final_print = candidate / "outputs/final-print.png"
        debug_print = candidate / "outputs/final-print-debug.png"
        result.append(
            {
                "candidate_id": candidate.name,
                "created_at": record.get("created_at"),
                "status": record.get("status"),
                "backend": record.get("backends", {}),
                "name_mode": record.get("name_mode"),
                "active": candidate.name not in invalidated,
                "final_print_url": (
                    _asset_url(final_print, assets) if final_print.is_file() else None
                ),
                "debug_print_url": (
                    _asset_url(debug_print, assets) if debug_print.is_file() else None
                ),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            str(item.get("created_at") or ""),
            str(item.get("candidate_id") or ""),
        ),
    )


def discover_operator_workflows(
    config: OperatorWorkflowConfig,
    design_ids: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Path]]:
    """Return operator workflow summaries plus a strict asset URL allow-list."""
    releases = discover_releases(config.exchange_root, config.authoring_root)
    assets: dict[str, Path] = {}
    workflows: dict[str, list[dict[str, Any]]] = {}
    for design_id in sorted(design_ids):
        design_root = _contained(config.authoring_root, design_id)
        products: list[dict[str, Any]] = []
        if design_root.is_dir():
            for product in sorted(path for path in design_root.iterdir() if path.is_dir()):
                if not (product / "product.json").is_file():
                    continue
                experiments: dict[str, list[dict[str, Any]]] = {}
                for kind in ("art", "pet", "layout"):
                    entries = [
                        entry
                        for path in sorted((product / "experiments" / kind).glob("*"))
                        if path.is_dir() and (entry := _experiment_entry(path)) is not None
                    ]
                    experiments[kind] = entries
                reviews: dict[str, list[dict[str, Any]]] = {}
                for kind in ("art", "pet", "layout", "assembly"):
                    reviews[kind] = [
                        entry
                        for path in sorted((product / "reviews" / kind).glob("*"))
                        if path.is_dir() and (entry := _review_entry(path, assets)) is not None
                    ]
                product_releases = releases.get((design_id, product.name), [])
                products.append(
                    {
                        "product_profile_id": product.name,
                        "experiments": experiments,
                        "reviews": reviews,
                        "layout_proposals": _proposal_entries(product, assets),
                        "print_candidates": _print_candidate_entries(product, assets),
                        "graduations": sorted(
                            path.name
                            for path in (product / "graduations").glob("*")
                            if (path / "selection.json").is_file()
                        ),
                        "releases": product_releases,
                        "released": any(
                            item.get("published") and item.get("active", True)
                            for item in product_releases
                        ),
                    }
                )
        # A released design may no longer have a local authoring tree.
        known_profiles = {item["product_profile_id"] for item in products}
        for (released_design, profile_id), entries in releases.items():
            if released_design == design_id and profile_id not in known_profiles:
                products.append(
                    {
                        "product_profile_id": profile_id,
                        "experiments": {"art": [], "pet": [], "layout": []},
                        "reviews": {"art": [], "pet": [], "layout": [], "assembly": []},
                        "layout_proposals": [],
                        "print_candidates": [],
                        "graduations": [],
                        "releases": entries,
                        "released": any(
                            item.get("published") and item.get("active", True)
                            for item in entries
                        ),
                    }
                )
        workflows[design_id] = sorted(products, key=lambda item: item["product_profile_id"])
    return workflows, assets


def _selected_review(
    product: Path,
    kind: str,
    *,
    fixture_tier: str | None = None,
) -> Path:
    decided: list[tuple[str, Path]] = []
    for path in (product / "reviews" / kind).glob("*/decision.json"):
        if not path.is_file():
            continue
        if (path.parent / "decision-retired.json").is_file():
            continue
        try:
            value = _read_json(path)
        except OperatorWorkflowError:
            continue
        if fixture_tier is not None:
            evaluation = _read_json(path.parent / "evaluation.json")
            if evaluation.get("fixture_tier") != fixture_tier:
                continue
        selected_at = value.get("decision", {}).get("selected_at", "")
        decided.append((str(selected_at), path.parent))
    if not decided:
        raise OperatorWorkflowError(
            f"{kind} decision"
            f"{f' for {fixture_tier} fixtures' if fixture_tier else ''} "
            f"is required before this action; product={product.resolve()}"
        )
    return max(decided, key=lambda item: (item[0], item[1].name))[1]


def _decision_selected(review: Path) -> dict[str, Any]:
    return _read_json(review / "decision.json").get("selected", {})


def _source_references(experiment: Path, meta: dict[str, Any]) -> list[Path]:
    references = []
    for descriptor in meta.get("inputs", {}).get("references", []):
        if isinstance(descriptor, dict) and isinstance(descriptor.get("path"), str):
            path = (experiment / descriptor["path"]).resolve()
            if path.is_file():
                references.append(path)
    return references


def _prompt_name(experiment: Path, meta: dict[str, Any], kind: str) -> str:
    descriptor = meta.get("inputs", {}).get("prompt")
    if isinstance(descriptor, dict) and isinstance(descriptor.get("path"), str):
        return Path(descriptor["path"]).name
    return f"{'art-template' if kind == 'art' else 'pet-transform'}-gpt.md"


def _fixture_selection(
    config: OperatorWorkflowConfig, product: Path, tier: str, experiment_id: str
) -> tuple[Path, Path]:
    fixture_set = config.fixture_set(tier)
    inventory = load_fixture_set(fixture_set)
    for candidate in sorted((product / "benchmark-selections").glob("*.json"), reverse=True):
        try:
            value = _read_json(candidate)
        except OperatorWorkflowError:
            continue
        if (
            value.get("tier") == tier
            and value.get("fixture_set_sha256") == sha256(inventory.path)
        ):
            return fixture_set, candidate
    output = product / "benchmark-selections" / f"operator-{experiment_id}-{tier}.json"
    write_fixture_selection(
        fixture_set,
        output=output,
        fixture_count=3 if tier == "smoke" else 6,
    )
    return fixture_set, output


def _fixture_selection_from_evaluation(
    config: OperatorWorkflowConfig,
    product: Path,
    evaluation: dict[str, Any],
) -> tuple[Path, Path]:
    tier = str(evaluation.get("fixture_tier") or "")
    if tier not in {"smoke", "release"}:
        raise OperatorWorkflowError(
            "selected pet evaluation does not identify a smoke/release fixture tier"
        )
    descriptor = evaluation.get("fixture_selection")
    expected_hash = descriptor.get("config_sha256") if isinstance(descriptor, dict) else None
    if not isinstance(expected_hash, str):
        raise OperatorWorkflowError(
            "selected pet evaluation does not pin a fixture-selection config hash"
        )
    matches = [
        path
        for path in (product / "benchmark-selections").glob("*.json")
        if path.is_file() and sha256(path) == expected_hash
    ]
    if len(matches) != 1:
        raise OperatorWorkflowError(
            "could not resolve the exact fixture selection pinned by the pet review; "
            f"expected_sha256={expected_hash}; matches={[str(path) for path in matches]}; "
            f"product={product.resolve()}"
        )
    fixture_set = config.fixture_set(tier)
    expected_set_hash = evaluation.get("fixture_set_sha256")
    if isinstance(expected_set_hash, str) and sha256(fixture_set) != expected_set_hash:
        raise OperatorWorkflowError(
            "configured fixture set no longer matches the selected pet evaluation; "
            f"fixture_set={fixture_set.resolve()}; expected_sha256={expected_set_hash}; "
            f"actual_sha256={sha256(fixture_set)}"
        )
    return fixture_set, matches[0]


class OperatorWorkflowRunner:
    """Execute one validated operator action using the established core APIs."""

    supported_actions = (
        "rerun-experiment",
        "run-pet-benchmark",
        "record-decision",
        "generate-layout-proposal",
        "accept-layout-proposal",
        "reopen-stage",
        "generate-composition",
        "prepare-print",
        "build-local-release",
        "publish-s3",
    )

    def __init__(self, config: OperatorWorkflowConfig) -> None:
        self.config = config

    def _product(self, payload: dict[str, Any]) -> tuple[str, str, Path]:
        design_id = _safe_id(payload.get("design_id"), "design_id")
        profile_id = _safe_id(payload.get("product_profile_id"), "product_profile_id")
        product = _contained(self.config.authoring_root, design_id, profile_id)
        if not (product / "product.json").is_file():
            raise OperatorWorkflowError(f"authoring product does not exist: {product}")
        return design_id, profile_id, product

    def execute(
        self,
        payload: dict[str, Any],
        *,
        operator_id: str,
        progress: Callable[[str], None],
    ) -> dict[str, Any]:
        action = str(payload.get("action", "")).strip()
        self.validate_action(action)
        if action == "rerun-experiment":
            return self._rerun_experiment(payload, operator_id, progress)
        if action == "run-pet-benchmark":
            return self._run_pet_benchmark(payload, progress)
        if action == "record-decision":
            return self._record_decision(payload, operator_id, progress)
        if action == "generate-layout-proposal":
            return self._generate_layout(payload, operator_id, progress)
        if action == "accept-layout-proposal":
            return self._accept_layout(payload, operator_id, progress)
        if action == "reopen-stage":
            return self._reopen_stage(payload, operator_id, progress)
        if action == "generate-composition":
            return self._generate_composition(payload, progress)
        if action == "prepare-print":
            return self._prepare_print(payload, operator_id, progress)
        if action == "build-local-release":
            return self._build_local_release(payload, operator_id, progress)
        if action == "publish-s3":
            return self._publish_s3(payload, progress)

        # validate_action above and this dispatch table intentionally share the
        # same class-owned contract. Keep this guard for a future action that is
        # declared without an implementation.
        raise OperatorWorkflowError(
            f"workflow action is declared but not implemented: {action!r}"
        )

    def validate_action(self, action: object) -> str:
        value = str(action or "").strip()
        if value in self.supported_actions:
            return value
        raise OperatorWorkflowError(
            "unsupported workflow action; "
            f"actual={value!r}; supported={list(self.supported_actions)!r}. "
            "If the operator page offers this action, restart the gallery server "
            "so its Python backend matches the current UI files."
        )

    def _rerun_experiment(
        self, payload: dict[str, Any], operator_id: str, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        design_id, _, product = self._product(payload)
        kind = str(payload.get("kind", ""))
        if kind not in {"art", "pet"}:
            raise OperatorWorkflowError("rerun kind must be art or pet")
        source_id = _safe_id(payload.get("source_experiment_id"), "source_experiment_id")
        experiment_id = _safe_id(payload.get("experiment_id"), "experiment_id")
        source = _contained(product / "experiments" / kind, source_id)
        meta = _read_json(source / "experiment.json")
        prompt_text = payload.get("prompt")
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            raise OperatorWorkflowError("edited prompt must not be empty")
        generation = meta.get("generation")
        if not isinstance(generation, dict):
            raise OperatorWorkflowError(f"source experiment has no generation contract: {source}")
        prompt_dir = product / "operator-drafts" / kind / experiment_id
        prompt_dir.mkdir(parents=True, exist_ok=False)
        prompt_file = prompt_dir / _prompt_name(source, meta, kind)
        prompt_file.write_text(prompt_text.rstrip() + "\n", encoding="utf-8")
        parameters = generation.get("parameters", {})
        prompt_variables = generation.get("prompt_variables", {})
        pet_name = (
            str(prompt_variables.get("pet_name"))
            if isinstance(prompt_variables, dict) and prompt_variables.get("pet_name")
            else None
        )
        progress(f"Creating immutable {kind} experiment {experiment_id}")
        try:
            created = create_experiment(
                kind=kind,
                experiment_id=experiment_id,
                design_id=design_id,
                product_profile=source / str(meta["inputs"]["product_profile"]["path"]),
                authoring_root=self.config.authoring_root,
                references=_source_references(source, meta),
                prompt_file=prompt_file,
                provider=str(generation.get("provider")),
                model=str(generation.get("model")),
                quality=str(parameters.get("quality", "high")),
                art_attempt=None,
                pet_attempt=None,
                font_catalogs=[],
                parent_experiment_id=source_id,
                base_bundle_revision=meta.get("base_bundle_revision"),
                created_by=operator_id,
                pet_name=pet_name,
            )
        finally:
            # create_experiment snapshots the prompt. The mutable editor draft is
            # temporary and must not become another source of truth or block retry.
            shutil.rmtree(prompt_dir, ignore_errors=True)
        review_id = _safe_id(payload.get("review_id") or f"{experiment_id}-review", "review_id")
        if kind == "art":
            attempt_id = _safe_id(payload.get("attempt_id") or "attempt-0001", "attempt_id")
            progress(f"Running art attempt {attempt_id}; this paid call can take several minutes")
            run_attempt(experiment=created, attempt_id=attempt_id, pet_image=None)
            progress("Building art comparison")
            evaluation = compare(
                kind="art", review_id=review_id, authoring_product=product,
                experiments=[source_id, experiment_id], evaluation_protocol=self.config.evaluation_protocol,
                fixture_set=None, art_attempt=None, pet_experiment=None,
                layout_attempt=None, base_bundle_revision=None,
            )
        else:
            tier = str(payload.get("tier", "release"))
            if tier not in {"smoke", "release"}:
                raise OperatorWorkflowError("pet tier must be smoke or release")
            fixture_set, selection = _fixture_selection(self.config, product, tier, experiment_id)
            progress(f"Running {tier} pet benchmark; each selected fixture is a paid call")
            benchmark(
                experiment=created, fixture_set=fixture_set,
                evaluation_protocol=self.config.evaluation_protocol,
                attempts_per_fixture=None, attempt_id_prefix=tier,
                fixture_selection=selection, pet_name=pet_name,
            )
            progress("Building pet comparison")
            comparison_experiments = [experiment_id]
            if any(
                attempt.is_dir()
                and attempt.name.startswith(f"{tier}-")
                and (attempt / "run.json").is_file()
                for attempt in (source / "attempts").glob("*")
            ):
                comparison_experiments.insert(0, source_id)
            evaluation = compare(
                kind="pet", review_id=review_id, authoring_product=product,
                experiments=comparison_experiments,
                evaluation_protocol=self.config.evaluation_protocol,
                fixture_set=fixture_set, art_attempt=None, pet_experiment=None,
                layout_attempt=None, base_bundle_revision=None,
                attempt_prefix=f"{tier}-", fixture_selection=selection,
            )
        return {"experiment": str(created), "evaluation": str(evaluation)}

    def _run_pet_benchmark(
        self, payload: dict[str, Any], progress: Callable[[str], None]
    ) -> dict[str, Any]:
        _, _, product = self._product(payload)
        experiment_id = _safe_id(payload.get("experiment_id"), "experiment_id")
        experiment = _contained(product / "experiments/pet", experiment_id)
        meta = _read_json(experiment / "experiment.json")
        if meta.get("kind") != "pet":
            raise OperatorWorkflowError(f"selected experiment is not pet: {experiment}")
        tier = str(payload.get("tier", "release"))
        if tier not in {"smoke", "release"}:
            raise OperatorWorkflowError("pet tier must be smoke or release")
        fixture_set, selection = _fixture_selection(
            self.config, product, tier, experiment_id
        )
        prompt_variables = meta.get("generation", {}).get("prompt_variables", {})
        pet_name = (
            str(prompt_variables.get("pet_name"))
            if isinstance(prompt_variables, dict) and prompt_variables.get("pet_name")
            else None
        )
        progress(f"Running {tier} fixtures on existing pet experiment {experiment_id}")
        attempts = benchmark(
            experiment=experiment,
            fixture_set=fixture_set,
            evaluation_protocol=self.config.evaluation_protocol,
            attempts_per_fixture=None,
            attempt_id_prefix=tier,
            fixture_selection=selection,
            pet_name=pet_name,
        )
        review_id = _safe_id(
            payload.get("review_id") or f"{experiment_id}-{tier}-review",
            "review_id",
        )
        progress("Building pet comparison")
        evaluation = compare(
            kind="pet",
            review_id=review_id,
            authoring_product=product,
            experiments=[experiment_id],
            evaluation_protocol=self.config.evaluation_protocol,
            fixture_set=fixture_set,
            art_attempt=None,
            pet_experiment=None,
            layout_attempt=None,
            base_bundle_revision=None,
            attempt_prefix=f"{tier}-",
            fixture_selection=selection,
        )
        return {
            "experiment": str(experiment),
            "attempts": [str(path) for path in attempts],
            "evaluation": str(evaluation),
        }

    def _record_decision(
        self, payload: dict[str, Any], operator_id: str, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        _, _, product = self._product(payload)
        kind = str(payload.get("kind", ""))
        if kind not in {"art", "pet"}:
            raise OperatorWorkflowError("operator review decision kind must be art or pet")
        review_id = _safe_id(payload.get("review_id"), "review_id")
        selected_experiment = _safe_id(payload.get("selected_experiment"), "selected_experiment")
        selected_attempt = payload.get("selected_attempt")
        if kind == "art":
            selected_attempt = _safe_id(selected_attempt, "selected_attempt")
        else:
            selected_attempt = None
            evaluation = _read_json(
                product / "reviews" / kind / review_id / "evaluation.json"
            )
            if evaluation.get("fixture_tier") != "release":
                raise OperatorWorkflowError(
                    "operator pet decisions require release-tier fixture evidence; "
                    f"review={review_id!r}; fixture_tier={evaluation.get('fixture_tier')!r}"
                )
        progress(f"Recording immutable {kind} decision")
        output = record_decision(
            review=product / "reviews" / kind / review_id,
            selected_by=operator_id,
            notes=str(payload.get("notes", "")).strip(),
            selected_experiment=selected_experiment,
            selected_attempt=selected_attempt,
        )
        return {"decision": str(output)}

    def _reopen_stage(
        self,
        payload: dict[str, Any],
        operator_id: str,
        progress: Callable[[str], None],
    ) -> dict[str, Any]:
        """Retire an active winner and all downstream operator selections.

        Authoring evidence is append-only: retirement markers and one product-level
        event supersede earlier choices without deleting or rewriting them.
        """

        design_id, profile_id, product = self._product(payload)
        stage = str(payload.get("stage", "")).strip()
        if stage not in {"art", "pet", "layout"}:
            raise OperatorWorkflowError("reopen stage must be art, pet, or layout")
        reason = str(payload.get("reason", "")).strip()
        if not reason:
            raise OperatorWorkflowError(
                "reopening a finished stage requires a reason for the audit trail"
            )
        active_stage_decisions = [
            path
            for path in (product / "reviews" / stage).glob("*/decision.json")
            if path.is_file()
            and not (path.parent / "decision-retired.json").is_file()
        ]
        if not active_stage_decisions:
            raise OperatorWorkflowError(
                f"no active {stage} decision exists to reopen; product={product}"
            )
        releases = discover_releases(
            self.config.exchange_root,
            self.config.authoring_root,
        ).get((design_id, profile_id), [])
        active_releases = [item for item in releases if item.get("active", True)]
        published = [item for item in active_releases if item.get("published")]
        if published:
            raise OperatorWorkflowError(
                "published releases are immutable and cannot be reopened from the "
                "graduated workflow; create a new post-production authoring iteration. "
                f"published_releases={[item.get('release_id') for item in published]!r}"
            )

        event_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        retired_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        affected_kinds = {
            "art": ("art", "layout"),
            "pet": ("pet", "layout"),
            "layout": ("layout",),
        }[stage]
        retired_decisions: list[str] = []
        retired_reviews: list[str] = []

        def retire_decisions(kind: str) -> None:
            for decision in sorted((product / "reviews" / kind).glob("*/decision.json")):
                marker = decision.parent / "decision-retired.json"
                if marker.exists():
                    continue
                atomic_json(
                    marker,
                    {
                        "schema_version": 1,
                        "event_id": event_id,
                        "stage": stage,
                        "kind": kind,
                        "decision": decision.relative_to(product).as_posix(),
                        "decision_sha256": sha256(decision),
                        "retired_by": operator_id,
                        "retired_at": retired_at,
                        "reason": reason,
                    },
                )
                retired_decisions.append(decision.relative_to(product).as_posix())

        for kind in affected_kinds:
            retire_decisions(kind)
        retire_decisions("assembly")

        for evaluation in sorted((product / "reviews/pet").glob("*/evaluation.json")):
            review = evaluation.parent
            if not (review / "artifacts/pet-composition-comparison.png").is_file():
                continue
            marker = review / "review-retired.json"
            if marker.exists():
                continue
            atomic_json(
                marker,
                {
                    "schema_version": 1,
                    "event_id": event_id,
                    "stage": stage,
                    "review": review.relative_to(product).as_posix(),
                    "evaluation_sha256": sha256(evaluation),
                    "retired_by": operator_id,
                    "retired_at": retired_at,
                    "reason": reason,
                },
            )
            retired_reviews.append(review.relative_to(product).as_posix())

        invalidated_layout_experiments = (
            sorted(
                path.name
                for path in (product / "experiments/layout").glob("*")
                if path.is_dir() and (path / "experiment.json").is_file()
            )
            if stage in {"art", "pet"}
            else []
        )
        invalidated_releases = [
            {
                "release_id": item.get("release_id"),
                "template_id": item.get("template_id"),
                "bundle_revision": item.get("bundle_revision"),
                "catalog": item.get("catalog"),
            }
            for item in active_releases
        ]
        invalidated_print_candidates = sorted(
            path.name
            for path in (product / "print-candidates").glob("*")
            if path.is_dir() and (path / "print-candidate.json").is_file()
        )
        event = product / "operator-iterations" / f"{event_id}.json"
        atomic_json(
            event,
            {
                "schema_version": 1,
                "event_id": event_id,
                "event": "stage_reopened",
                "design_id": design_id,
                "product_profile_id": profile_id,
                "stage": stage,
                "reopened_by": operator_id,
                "reopened_at": retired_at,
                "reason": reason,
                "retired_decisions": retired_decisions,
                "retired_composition_reviews": retired_reviews,
                "invalidated_layout_experiments": invalidated_layout_experiments,
                "invalidated_print_candidates": invalidated_print_candidates,
                "invalidated_releases": invalidated_releases,
            },
        )
        progress(
            f"Reopened {stage}; retired {len(retired_decisions)} decisions and "
            f"{len(retired_reviews)} composed reviews"
        )
        return {
            "event": str(event),
            "retired_decisions": retired_decisions,
            "retired_composition_reviews": retired_reviews,
            "invalidated_layout_experiments": invalidated_layout_experiments,
            "invalidated_print_candidates": invalidated_print_candidates,
            "invalidated_releases": invalidated_releases,
        }

    def _generate_layout(
        self, payload: dict[str, Any], operator_id: str, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        design_id, _, product = self._product(payload)
        experiment_id = _safe_id(payload.get("experiment_id") or "layout-v01", "experiment_id")
        proposal_id = _safe_id(payload.get("proposal_id") or "proposal-v01", "proposal_id")
        art_selected = _decision_selected(_selected_review(product, "art"))
        pet_selected = _decision_selected(
            _selected_review(product, "pet", fixture_tier="release")
        )
        art_attempt = product / "experiments/art" / str(art_selected["experiment_id"]) / "attempts" / str(art_selected["attempt_id"])
        pet_experiment = product / "experiments/pet" / str(pet_selected["experiment_id"])
        successful_pet_attempts = []
        for attempt in sorted((pet_experiment / "attempts").glob("*")):
            if not (attempt / "run.json").is_file():
                continue
            run = _read_json(attempt / "run.json")
            if run.get("status") == "succeeded":
                successful_pet_attempts.append(attempt)
        if not successful_pet_attempts:
            raise OperatorWorkflowError(f"selected pet experiment has no successful attempt: {pet_experiment}")
        representative = next(
            (path for path in successful_pet_attempts if path.name.startswith("release-")),
            successful_pet_attempts[0],
        )
        art_meta = _read_json(art_attempt.parents[1] / "experiment.json")
        references = _source_references(art_attempt.parents[1], art_meta)
        progress("Creating layout experiment from the approved art and pet decisions")
        layout_experiment = create_experiment(
            kind="layout", experiment_id=experiment_id, design_id=design_id,
            product_profile=art_attempt.parents[1] / str(art_meta["inputs"]["product_profile"]["path"]),
            authoring_root=self.config.authoring_root, references=references[:1],
            prompt_file=None, provider=None, model=None, quality="high",
            art_attempt=art_attempt, pet_attempt=representative,
            font_catalogs=[self.config.font_catalog], parent_experiment_id=None,
            base_bundle_revision=None, created_by=operator_id,
        )
        progress("Ranking deterministic layout proposals")
        proposal = propose_layout(
            experiment=layout_experiment, proposal_id=proposal_id,
            name_mode=str(payload.get("name_mode", "auto")),
            pet_names=[], attempt_prefix=None, max_candidates=60, finalists=3,
        )
        return {"experiment": str(layout_experiment), "proposal": str(proposal)}

    def _accept_layout(
        self, payload: dict[str, Any], operator_id: str, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        _, _, product = self._product(payload)
        experiment_id = _safe_id(payload.get("experiment_id"), "experiment_id")
        proposal_id = _safe_id(payload.get("proposal_id"), "proposal_id")
        candidate_id = _safe_id(payload.get("candidate_id"), "candidate_id")
        attempt_id = _safe_id(payload.get("attempt_id") or "attempt-0001", "attempt_id")
        review_id = _safe_id(payload.get("review_id") or f"{experiment_id}-review", "review_id")
        experiment = product / "experiments/layout" / experiment_id
        proposal_root = experiment / "proposals" / proposal_id
        layout_file = proposal_root / "candidates" / candidate_id / "layout.json"
        if not layout_file.is_file():
            raise OperatorWorkflowError(f"selected layout proposal does not exist: {layout_file.resolve()}")
        proposal_record = _read_json(proposal_root / "proposal.json")
        proposal_warnings = [
            str(warning)
            for warning in proposal_record.get("warnings", [])
            if str(warning).strip()
        ]
        layout_value = _read_json(layout_file)
        progress(f"Importing layout proposal {candidate_id}")
        attempt = run_attempt(
            experiment=experiment, attempt_id=attempt_id, pet_image=None,
            layout_file=layout_file,
            no_pet_name="name" not in layout_value,
        )
        progress("Building and approving the layout comparison")
        compare(
            kind="layout", review_id=review_id, authoring_product=product,
            experiments=[experiment_id], evaluation_protocol=self.config.evaluation_protocol,
            fixture_set=None, art_attempt=None, pet_experiment=None,
            layout_attempt=None, base_bundle_revision=None,
            advisory_warnings=proposal_warnings,
        )
        decision = record_decision(
            review=product / "reviews/layout" / review_id,
            selected_by=operator_id,
            notes=str(payload.get("notes", "")).strip() or f"Accepted deterministic {proposal_id}/{candidate_id}",
            selected_experiment=experiment_id, selected_attempt=attempt_id,
        )
        composition_id = _safe_id(
            payload.get("composition_review_id") or f"{experiment_id}-composition",
            "composition_review_id",
        )
        progress("Generating the composed release-fixture comparison")
        composed = self._compose_selected(
            product=product,
            review_id=composition_id,
            layout_attempt=attempt,
        )
        return {"layout_attempt": str(attempt), "decision": str(decision), "composition": str(composed)}

    def _compose_selected(
        self,
        *,
        product: Path,
        review_id: str,
        layout_attempt: Path,
    ) -> Path:
        art_review = _selected_review(product, "art")
        pet_review = _selected_review(product, "pet", fixture_tier="release")
        art_selected = _decision_selected(art_review)
        pet_selected = _decision_selected(pet_review)
        pet_evaluation = _read_json(pet_review / "evaluation.json")
        fixture_set, selection = _fixture_selection_from_evaluation(
            self.config,
            product,
            pet_evaluation,
        )
        return compare(
            kind="pet",
            review_id=review_id,
            authoring_product=product,
            experiments=[str(pet_selected["experiment_id"])],
            evaluation_protocol=self.config.evaluation_protocol,
            fixture_set=fixture_set,
            art_attempt=(
                product
                / "experiments/art"
                / str(art_selected["experiment_id"])
                / "attempts"
                / str(art_selected["attempt_id"])
            ),
            pet_experiment=(
                product / "experiments/pet" / str(pet_selected["experiment_id"])
            ),
            layout_attempt=layout_attempt,
            base_bundle_revision=None,
            attempt_prefix=pet_evaluation.get("attempt_id_prefix"),
            fixture_selection=selection,
        )

    def _generate_composition(
        self, payload: dict[str, Any], progress: Callable[[str], None]
    ) -> dict[str, Any]:
        _, _, product = self._product(payload)
        layout_selected = _decision_selected(_selected_review(product, "layout"))
        layout_attempt = (
            product
            / "experiments/layout"
            / str(layout_selected["experiment_id"])
            / "attempts"
            / str(layout_selected["attempt_id"])
        )
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        review_id = _safe_id(
            payload.get("composition_review_id") or f"composition-ui-{suffix}",
            "composition_review_id",
        )
        progress("Generating the composed release-fixture comparison")
        output = self._compose_selected(
            product=product,
            review_id=review_id,
            layout_attempt=layout_attempt,
        )
        return {"layout_attempt": str(layout_attempt), "composition": str(output)}

    def _prepare_print(
        self,
        payload: dict[str, Any],
        operator_id: str,
        progress: Callable[[str], None],
    ) -> dict[str, Any]:
        """Approve composed QA and create a finalist without graduating it."""
        _, _, product = self._product(payload)
        if not bool(payload.get("composition_approved")):
            raise OperatorWorkflowError(
                "composition_approved=true is required after visual review"
            )
        active_compositions = [
            path.parent
            for path in (product / "reviews/pet").glob("*/evaluation.json")
            if (path.parent / "artifacts/pet-composition-comparison.png").is_file()
            and not (path.parent / "review-retired.json").is_file()
        ]
        if not active_compositions:
            raise OperatorWorkflowError(
                "an active composed release-fixture comparison is required before "
                f"print preparation; product={product.resolve()}"
            )
        art_review = _selected_review(product, "art")
        pet_review = _selected_review(product, "pet", fixture_tier="release")
        layout_review = _selected_review(product, "layout")
        art_selected = _decision_selected(art_review)
        pet_selected = _decision_selected(pet_review)
        layout_selected = _decision_selected(layout_review)
        art_attempt = (
            product
            / "experiments/art"
            / str(art_selected["experiment_id"])
            / "attempts"
            / str(art_selected["attempt_id"])
        )
        pet_experiment = (
            product / "experiments/pet" / str(pet_selected["experiment_id"])
        )
        layout_attempt = (
            product
            / "experiments/layout"
            / str(layout_selected["experiment_id"])
            / "attempts"
            / str(layout_selected["attempt_id"])
        )
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        assembly_id = _safe_id(
            payload.get("assembly_review_id") or f"assembly-ui-{suffix}",
            "assembly_review_id",
        )
        progress("Recording the approved component assembly")
        compare(
            kind="assembly",
            review_id=assembly_id,
            authoring_product=product,
            experiments=[],
            evaluation_protocol=self.config.evaluation_protocol,
            fixture_set=self.config.fixture_set("release"),
            art_attempt=art_attempt,
            pet_experiment=pet_experiment,
            layout_attempt=layout_attempt,
            base_bundle_revision=None,
        )
        assembly_review = product / "reviews/assembly" / assembly_id
        record_decision(
            review=assembly_review,
            selected_by=operator_id,
            notes=str(payload.get("notes", "")).strip()
            or "Approved composed operator comparison",
            selected_experiment=None,
            selected_attempt=None,
        )
        candidate_id = _safe_id(
            payload.get("candidate_id") or f"print-ui-{suffix}",
            "candidate_id",
        )
        progress("Preparing print finalist for explicit visual review")
        print_candidate = prepare_print_candidate(
            candidate_id=candidate_id,
            authoring_product=product,
            art_attempt=None,
            pet_attempt=None,
            layout_attempt=None,
            art_review=art_review,
            pet_review=pet_review,
            layout_review=layout_review,
            pet_name=None,
            backend=str(payload.get("backend", "deterministic")),
        )
        return {
            "assembly_review": str(assembly_review),
            "print_candidate": str(print_candidate),
        }

    def _build_local_release(
        self, payload: dict[str, Any], operator_id: str, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        design_id, profile_id, product = self._product(payload)
        art_review = _selected_review(product, "art")
        pet_review = _selected_review(product, "pet", fixture_tier="release")
        layout_review = _selected_review(product, "layout")
        pet_selected = _decision_selected(pet_review)
        pet_experiment = product / "experiments/pet" / str(pet_selected["experiment_id"])
        release_reviews = []
        for path in (product / "reviews/pet").glob("*/evaluation.json"):
            evaluation = _read_json(path)
            if evaluation.get("fixture_tier") != "release":
                continue
            candidate_ids = {
                item.get("experiment_id")
                for item in evaluation.get("candidates", [])
                if isinstance(item, dict)
            }
            if pet_selected.get("experiment_id") in candidate_ids:
                release_reviews.append(path.parent)
        if not release_reviews:
            raise OperatorWorkflowError("a release-tier pet comparison is required before release")
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        assembly_review = _selected_review(product, "assembly")
        candidate_id = _safe_id(payload.get("candidate_id"), "candidate_id")
        active_candidates = {
            str(item["candidate_id"]): item
            for item in _print_candidate_entries(product, {})
            if item.get("active", True) and item.get("status") == "succeeded"
        }
        if candidate_id not in active_candidates:
            raise OperatorWorkflowError(
                "selected print finalist is not an active succeeded candidate; "
                f"candidate_id={candidate_id!r}; available={sorted(active_candidates)!r}; "
                f"product={product.resolve()}"
            )
        print_candidate = product / "print-candidates" / candidate_id
        graduation_id = _safe_id(payload.get("graduation_id") or f"{design_id}-{profile_id}-{suffix}", "graduation_id")
        progress("Recording immutable graduation selection")
        selection = graduate(
            graduation_id=graduation_id, print_candidate=print_candidate,
            art_review=art_review, pet_review=pet_review, layout_review=layout_review,
            assembly_review=assembly_review, selected_by=operator_id,
            notes=str(payload.get("notes", "")).strip() or "Approved in operator workflow",
            authoring_root=self.config.authoring_root,
        )
        qa_hashes = {
            _read_json(path / "run.json").get("input_pet_sha256")
            for path in (pet_experiment / "attempts").glob("release-*")
            if (path / "run.json").is_file()
        }
        qa_input = None
        for fixture in load_fixture_set(self.config.fixture_set("release")).fixtures:
            if fixture.image_sha256 in qa_hashes:
                qa_input = fixture.image
                break
        if qa_input is None:
            raise OperatorWorkflowError("could not resolve a selected release fixture as bundle QA input")
        progress("Building production bundle revision")
        bundle = build_from_selection(
            selection_path=selection, output_dir=self.config.exchange_root / "bundles",
            bundle_revision="next", pet_name_max_length=int(payload.get("pet_name_max_length", 12)),
            qa_input_pet=qa_input,
        )
        release_id = payload.get("release_id")
        if release_id:
            release_id = str(release_id)
        else:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            existing = [
                path.parent.name
                for path in (self.config.exchange_root / "releases").glob(
                    f"{date}.*/catalog.json"
                )
            ]
            # The normal shape is YYYY-MM-DD.NNN; tolerate an empty release root.
            numbers = [int(value.rsplit(".", 1)[1]) for value in existing if value.rsplit(".", 1)[-1].isdigit()]
            release_id = f"{date}.{max(numbers, default=0) + 1:03d}"
        progress(f"Building local release catalog {release_id}")
        catalog = build_release(
            release_id=release_id, bundles=[bundle], exchange_root=self.config.exchange_root,
            asset_base_url=None,
        )
        return {"selection": str(selection), "bundle": str(bundle), "release_catalog": str(catalog)}

    def _publish_s3(
        self, payload: dict[str, Any], progress: Callable[[str], None]
    ) -> dict[str, Any]:
        design_id, profile_id, _ = self._product(payload)
        bucket = str(self.config.s3_bucket or "").strip()
        if not bucket:
            raise OperatorWorkflowError(
                "S3 publication is not configured; restart pawmarvel-gallery with "
                "--s3-bucket (or PAWMARVEL_S3_BUCKET), plus the intended prefix, "
                "AWS profile, and region"
            )
        releases = discover_releases(
            self.config.exchange_root,
            self.config.authoring_root,
        ).get((design_id, profile_id), [])
        active = [entry for entry in releases if entry.get("active", True)]
        unpublished = [entry for entry in active if not entry.get("published")]
        prior_published = [entry for entry in active if entry.get("published")]
        if not unpublished and not prior_published:
            raise OperatorWorkflowError(
                "a local release catalog is required before S3 publication; "
                f"design_id={design_id!r}; product_profile_id={profile_id!r}"
            )
        selected = unpublished[-1] if unpublished else prior_published[-1]
        catalog = Path(str(selected["catalog"])).resolve()
        if unpublished:
            progress(
                "Publishing immutable release to S3; existing objects must match "
                "the local checksums"
            )
            destination = publish_s3(
                release_catalog=catalog,
                exchange_root=self.config.exchange_root,
                bucket=bucket,
                prefix=self.config.s3_prefix,
                aws_profile=self.config.aws_profile,
                region=self.config.aws_region,
                authoring_root=self.config.authoring_root,
                execute=True,
                progress=progress,
            )
            refreshed = discover_releases(
                self.config.exchange_root,
                self.config.authoring_root,
            ).get((design_id, profile_id), [])
        else:
            release_key = f"releases/{selected.get('release_id')}/catalog.json"
            prefix = f"{self.config.s3_prefix}/" if self.config.s3_prefix else ""
            destination = f"s3://{bucket}/{prefix}{release_key}"
            refreshed = releases
            progress(
                "Publication receipt already exists; reconciling the local release-pool move"
            )
        published = [
            entry
            for entry in refreshed
            if entry.get("release_id") == selected.get("release_id")
            and entry.get("bundle_revision") == selected.get("bundle_revision")
            and entry.get("published")
        ]
        if not published:
            raise OperatorWorkflowError(
                "S3 upload returned successfully but no matching publication receipt "
                "was discovered; the design was not moved. Inspect the release catalog "
                f"and authoring receipts before retrying: {catalog}"
            )
        moved_to: str | None = None
        if self.config.graduation_root is not None and self.config.release_root is not None:
            source = _contained(self.config.graduation_root, design_id)
            target = _contained(self.config.release_root, design_id)
            if source.is_dir() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    source.replace(target)
                except OSError as exc:
                    raise OperatorWorkflowError(
                        "S3 publication and receipts succeeded, but moving the design "
                        "to the release pool failed; retry the operator publication action "
                        "after correcting "
                        f"the filesystem issue. source={source}; target={target}; error={exc}"
                    ) from exc
                moved_to = str(target)
            elif target.is_dir() and not source.exists():
                moved_to = str(target)
            else:
                raise OperatorWorkflowError(
                    "S3 publication and receipts succeeded, but the lifecycle pool move "
                    "is ambiguous; expected exactly the graduated source or released "
                    f"target directory. source={source} (exists={source.exists()}); "
                    f"target={target} (exists={target.exists()})"
                )
            progress(f"Moved published design to release pool: {target}")
        return {
            "release_id": selected.get("release_id"),
            "bundle_revision": selected.get("bundle_revision"),
            "release_catalog": str(catalog),
            "destination": destination,
            "release_pool": moved_to,
        }


class OperatorJobManager:
    """Small in-process background queue with durable, non-secret job receipts."""

    def __init__(self, runner: OperatorWorkflowRunner, jobs_root: Path) -> None:
        self.runner = runner
        self.jobs_root = jobs_root.resolve()
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        # Keep paid provider bursts and local CPU work bounded while still
        # making a multi-design batch useful. Queued jobs remain visible.
        self._execution_slots = threading.Semaphore(3)
        self._load_receipts()

    def _load_receipts(self) -> None:
        """Restore job history and surface work interrupted by a restart."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for path in sorted(self.jobs_root.glob("*.json")):
            try:
                record = _read_json(path)
            except OperatorWorkflowError:
                continue
            job_id = record.get("job_id")
            if not isinstance(job_id, str) or path.stem != job_id:
                continue
            if record.get("status") in {"queued", "running"}:
                message = (
                    "Interrupted when the gallery process stopped. Inspect the "
                    "immutable attempt/review artifacts before retrying."
                )
                record.update(
                    status="failed",
                    message=message,
                    updated_at=now,
                    error={
                        "type": "OperatorProcessRestarted",
                        "message": message,
                    },
                )
                atomic_json(path, record)
            self._jobs[job_id] = record

    def submit(self, payload: dict[str, Any], *, operator_id: str) -> dict[str, Any]:
        return self.submit_many([payload], operator_id=operator_id)[0]

    def submit_many(
        self, payloads: list[dict[str, Any]], *, operator_id: str
    ) -> list[dict[str, Any]]:
        if not payloads or len(payloads) > 50:
            raise OperatorWorkflowError(
                "workflow batch must contain between 1 and 50 actions"
            )
        targets: list[tuple[str, str]] = []
        records: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for index, payload in enumerate(payloads, start=1):
            if not isinstance(payload, dict):
                raise OperatorWorkflowError(
                    f"workflow batch action {index} must be an object"
                )
            validator = getattr(self.runner, "validate_action", None)
            if callable(validator):
                validator(payload.get("action"))
            design_id = _safe_id(payload.get("design_id"), f"action {index} design_id")
            profile_id = _safe_id(
                payload.get("product_profile_id"),
                f"action {index} product_profile_id",
            )
            target = (design_id, profile_id)
            if target in targets:
                raise OperatorWorkflowError(
                    "workflow batch contains duplicate design/product target; "
                    f"design_id={design_id!r}; product_profile_id={profile_id!r}"
                )
            targets.append(target)
            records.append(
                {
                    "job_id": uuid.uuid4().hex,
                    "status": "queued",
                    "action": payload.get("action"),
                    "design_id": design_id,
                    "product_profile_id": profile_id,
                    "operator_id": operator_id,
                    "message": "Queued",
                    "created_at": now,
                    "updated_at": now,
                }
            )
        with self._lock:
            active = {
                (str(job.get("design_id")), str(job.get("product_profile_id"))): job
                for job in self._jobs.values()
                if job.get("status") in {"queued", "running"}
            }
            conflicts = [active[target] for target in targets if target in active]
            if conflicts:
                conflict = conflicts[0]
                raise OperatorWorkflowError(
                    "another workflow job is already running for a selected "
                    "design/product; "
                    f"design_id={conflict['design_id']!r}; "
                    f"product_profile_id={conflict['product_profile_id']!r}; "
                    f"job_id={conflict['job_id']}"
                )
            inserted: list[dict[str, Any]] = []
            try:
                for record in records:
                    self._jobs[str(record["job_id"])] = record
                    inserted.append(record)
                    self._persist(record)
            except Exception:
                for record in inserted:
                    job_id = str(record["job_id"])
                    self._jobs.pop(job_id, None)
                    (self.jobs_root / f"{job_id}.json").unlink(missing_ok=True)
                raise
        for record, payload in zip(records, payloads, strict=True):
            job_id = str(record["job_id"])
            threading.Thread(
                target=self._run,
                args=(job_id, dict(payload), operator_id),
                daemon=True,
                name=f"pawmarvel-operator-{job_id[:8]}",
            ).start()
        return [dict(record) for record in records]

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            record = self._jobs[job_id]
            record.update(changes)
            record["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._persist(record)

    def _persist(self, record: dict[str, Any]) -> None:
        atomic_json(self.jobs_root / f"{record['job_id']}.json", record)

    def _run(self, job_id: str, payload: dict[str, Any], operator_id: str) -> None:
        with self._execution_slots:
            self._update(job_id, status="running", message="Starting")
            try:
                result = self.runner.execute(
                    payload,
                    operator_id=operator_id,
                    progress=lambda message: self._update(job_id, message=message),
                )
            except Exception as exc:
                self._update(
                    job_id,
                    status="failed",
                    message=str(exc),
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
                return
            self._update(
                job_id,
                status="succeeded",
                message="Completed",
                result=result,
            )

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                (dict(record) for record in self._jobs.values()),
                key=lambda item: str(item["created_at"]),
                reverse=True,
            )
