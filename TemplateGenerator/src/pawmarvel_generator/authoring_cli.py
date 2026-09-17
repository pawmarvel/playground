# CLI purpose:
# Manage immutable offline art, pet-runtime, and layout experiments; compare
# attempts, keep evaluation/decision review packets together, prepare print
# finalists, graduate and trace selections, record publications, safely clean
# up losing artifacts, and initialize shared and per-design private configs.

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
from typing import Sequence

from .authoring import (
    AuthoringError, benchmark, cleanup, compare, create_experiment,
    graduate, prepare_print_candidate, record_decision, record_publication,
    run_attempt, set_status, trace_graduation,
)
from .cli_errors import add_debug_argument, report_unexpected
from .fixture_set import load_fixture_set, write_fixture_selection
from .operation_config import write_operation_config, write_shared_config


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _path_argument(value: str) -> Path:
    if not value.strip():
        raise argparse.ArgumentTypeError(
            "path must not be empty; check the corresponding shell variable"
        )
    return Path(value)


def _current_user() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "unknown-operator"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawmarvel-author",
        description="Manage immutable template authoring experiments.",
    )
    add_debug_argument(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    init_shared = commands.add_parser(
        "init-shared-config",
        help="generate reusable private provider and AWS/S3 config under ignored work/",
    )
    init_shared.add_argument(
        "--project-root",
        type=_path_argument,
        default=DEFAULT_PROJECT_ROOT,
        help="repository root containing work/ (default: installed editable source root)",
    )
    init_shared.add_argument(
        "--force",
        action="store_true",
        help="replace the existing shared config, including any secrets it contains",
    )

    init_config = commands.add_parser(
        "init-config",
        help="generate a credential-free design/product config under ignored work/",
    )
    init_config.add_argument(
        "--project-root",
        type=_path_argument,
        default=DEFAULT_PROJECT_ROOT,
        help="repository root containing work/ (default: installed editable source root)",
    )
    init_config.add_argument("--design-id", required=True)
    init_config.add_argument("--product-profile-id", required=True)
    init_config.add_argument(
        "--version-number",
        type=int,
        default=1,
        help="positive config iteration number used in the derived filename (default: 1)",
    )
    init_config.add_argument(
        "--force",
        action="store_true",
        help="replace an existing design/product config",
    )

    fixture = commands.add_parser(
        "validate-fixture-set",
        help="validate fixture metadata, tier size, image bytes, and pinned hashes",
    )
    fixture.add_argument("--fixture-set", type=_path_argument, required=True)

    prepare_benchmark = commands.add_parser(
        "prepare-benchmark",
        help="write a no-cost, reviewable fixture selection for benchmark and compare",
    )
    prepare_benchmark.add_argument("--fixture-set", type=_path_argument, required=True)
    prepare_benchmark.add_argument(
        "--fixture-count",
        type=int,
        help="select the first N fixtures remaining after filters",
    )
    prepare_benchmark.add_argument(
        "--fixture-filter",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help=(
            "filter by id, species, breed, size_class, morphology, or risk_tag; repeat "
            "for OR within one field and AND across different fields"
        ),
    )
    prepare_benchmark.add_argument("--output", type=_path_argument, required=True)
    prepare_benchmark.add_argument(
        "--force",
        action="store_true",
        help="replace an existing draft selection",
    )

    create = commands.add_parser("create-experiment")
    create.add_argument("--kind", choices=("art", "pet", "layout"), required=True)
    create.add_argument("--experiment-id", required=True)
    create.add_argument("--design-id", required=True)
    create.add_argument("--product-profile", type=_path_argument, required=True)
    create.add_argument(
        "--reference-design", type=_path_argument, action="append", default=[]
    )
    create.add_argument("--prompt-file", type=_path_argument)
    create.add_argument("--provider", choices=("openai", "gemini"))
    create.add_argument("--model")
    create.add_argument(
        "--quality",
        choices=("low", "medium", "high", "auto"),
        default="high",
        help="provider generation quality for art or pet attempts (default: high)",
    )
    create.add_argument("--art-attempt", type=_path_argument)
    create.add_argument("--pet-attempt", type=_path_argument)
    create.add_argument(
        "--font-catalog", type=_path_argument, action="append", default=[]
    )
    create.add_argument(
        "--font-reference",
        type=_path_argument,
        help="confirmed reference-image text region artifact for a layout experiment",
    )
    create.add_argument(
        "--layout-reference",
        type=_path_argument,
        help="reference-image pet/name regions used to initialize a layout experiment",
    )
    create.add_argument("--parent-experiment-id")
    create.add_argument("--base-bundle-revision", type=int)
    create.add_argument("--created-by")
    create.add_argument("--authoring-root", type=_path_argument, required=True)

    run = commands.add_parser("run-attempt")
    run.add_argument("--experiment", type=_path_argument, required=True)
    run.add_argument("--attempt-id", required=True)
    run.add_argument("--pet-image", type=_path_argument)
    run.add_argument("--pet-name", default="PET")
    run.add_argument(
        "--reference-text",
        help="initial exact text visible in the reference font region",
    )
    run.add_argument(
        "--layout-file",
        type=_path_argument,
        help="Import an existing layout without opening the editor",
    )

    bench = commands.add_parser("benchmark")
    bench.add_argument("--experiment", type=_path_argument, required=True)
    bench.add_argument("--fixture-set", type=_path_argument, required=True)
    bench.add_argument("--evaluation-protocol", type=_path_argument, required=True)
    bench.add_argument(
        "--attempts-per-fixture",
        type=int,
        help="must match the manifest protocol (MVP default: 1)",
    )
    bench.add_argument("--attempt-id-prefix", default="benchmark")
    bench.add_argument("--fixture-selection", type=_path_argument, required=True)

    comparison = commands.add_parser("compare")
    comparison.add_argument("--kind", choices=("art", "pet", "layout", "assembly"), required=True)
    comparison.add_argument("--review-id", required=True)
    comparison.add_argument(
        "--experiment",
        action="append",
        default=[],
        help=(
            "experiment ID to evaluate; repeat to compare prompt/model candidates "
            "side by side (art, pet, and layout comparisons emit contact sheets)"
        ),
    )
    comparison.add_argument(
        "--evaluation-protocol", type=_path_argument, required=True
    )
    comparison.add_argument("--fixture-set", type=_path_argument)
    comparison.add_argument("--fixture-selection", type=_path_argument)
    comparison.add_argument(
        "--attempt-prefix",
        help="include only attempt IDs with this prefix (useful for controlled benchmarks)",
    )
    comparison.add_argument("--art-attempt", type=_path_argument)
    comparison.add_argument("--pet-experiment", type=_path_argument)
    comparison.add_argument("--layout-attempt", type=_path_argument)
    comparison.add_argument("--base-bundle-revision", type=int)
    comparison.add_argument("--authoring-product", type=_path_argument, required=True)

    decision = commands.add_parser("record-decision")
    decision.add_argument("--review", type=_path_argument, required=True)
    decision.add_argument("--selected-experiment")
    decision.add_argument("--selected-attempt")
    decision.add_argument("--selected-by")
    decision.add_argument("--notes", default="")

    print_candidate = commands.add_parser("prepare-print")
    print_candidate.add_argument("--candidate-id", required=True)
    print_candidate.add_argument("--authoring-product", type=_path_argument, required=True)
    print_candidate.add_argument(
        "--art-attempt", type=_path_argument,
        help="advanced override; requires --pet-attempt and --layout-attempt",
    )
    print_candidate.add_argument(
        "--pet-attempt", type=_path_argument,
        help="advanced override; requires --art-attempt and --layout-attempt",
    )
    print_candidate.add_argument(
        "--layout-attempt", type=_path_argument,
        help="advanced override; requires --art-attempt and --pet-attempt",
    )
    print_candidate.add_argument(
        "--art-review", type=_path_argument,
        help="recommended winner review; requires pet and layout reviews",
    )
    print_candidate.add_argument(
        "--pet-review", type=_path_argument,
        help="recommended winner review; requires art and layout reviews",
    )
    print_candidate.add_argument(
        "--layout-review", type=_path_argument,
        help="recommended winner review; requires art and pet reviews",
    )
    print_candidate.add_argument("--pet-name", required=True)
    print_candidate.add_argument(
        "--backend", choices=("deterministic", "bria"), default="deterministic"
    )
    print_candidate.add_argument(
        "--reuse-template-from",
        type=_path_argument,
        help="reuse hash-verified print art/layout/font assets from this print candidate",
    )

    choose = commands.add_parser("graduate")
    choose.add_argument("--graduation-id", required=True)
    choose.add_argument("--print-candidate", type=_path_argument, required=True)
    choose.add_argument("--art-review", type=_path_argument, required=True)
    choose.add_argument("--pet-review", type=_path_argument, required=True)
    choose.add_argument("--layout-review", type=_path_argument, required=True)
    choose.add_argument("--assembly-review", type=_path_argument, required=True)
    choose.add_argument("--selected-by")
    choose.add_argument("--notes", default="")
    choose.add_argument("--authoring-root", type=_path_argument, required=True)

    trace = commands.add_parser("trace")
    trace.add_argument("--graduation", type=_path_argument, required=True)

    status = commands.add_parser("set-status")
    status.add_argument("--experiment", type=_path_argument, required=True)
    status.add_argument("--status", choices=("draft", "evaluated", "discarded"), required=True)

    publication = commands.add_parser("record-publication")
    publication.add_argument("--selection", type=_path_argument, required=True)
    publication.add_argument("--bundle-manifest", type=_path_argument, required=True)
    publication.add_argument("--release-catalog", type=_path_argument, required=True)
    publication.add_argument("--transfer-location", required=True)
    publication.add_argument("--authoring-root", type=_path_argument, required=True)

    clean = commands.add_parser("cleanup")
    clean.add_argument("--authoring-root", type=_path_argument, required=True)
    clean.add_argument(
        "--status",
        choices=("discarded", "failed", "unselected"),
        default="discarded",
    )
    clean.add_argument("--older-than-days", type=int, default=30)
    mode = clean.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init-shared-config":
            result = write_shared_config(
                project_root=args.project_root,
                force=args.force,
            )
        elif args.command == "init-config":
            result = write_operation_config(
                project_root=args.project_root,
                design_id=args.design_id,
                product_profile_id=args.product_profile_id,
                version_number=args.version_number,
                force=args.force,
            )
        elif args.command == "validate-fixture-set":
            import json

            print(json.dumps(load_fixture_set(args.fixture_set).summary(), indent=2))
            return 0
        elif args.command == "prepare-benchmark":
            result = write_fixture_selection(
                args.fixture_set,
                output=args.output,
                fixture_count=args.fixture_count,
                filters=tuple(args.fixture_filter),
                force=args.force,
            )
        elif args.command == "create-experiment":
            result = create_experiment(
                kind=args.kind,
                experiment_id=args.experiment_id,
                design_id=args.design_id,
                product_profile=args.product_profile,
                authoring_root=args.authoring_root,
                references=args.reference_design,
                prompt_file=args.prompt_file,
                provider=args.provider,
                model=args.model,
                quality=args.quality,
                art_attempt=args.art_attempt,
                pet_attempt=args.pet_attempt,
                font_catalogs=args.font_catalog,
                parent_experiment_id=args.parent_experiment_id,
                base_bundle_revision=args.base_bundle_revision,
                created_by=args.created_by or _current_user(),
                font_reference=args.font_reference,
                layout_reference=args.layout_reference,
            )
        elif args.command == "run-attempt":
            result = run_attempt(experiment=args.experiment, attempt_id=args.attempt_id, pet_image=args.pet_image,
                                 pet_name=args.pet_name, layout_file=args.layout_file,
                                 reference_text=args.reference_text)
        elif args.command == "benchmark":
            if args.attempts_per_fixture is not None and args.attempts_per_fixture < 1:
                raise AuthoringError("attempts per fixture must be positive")
            results = benchmark(experiment=args.experiment, fixture_set=args.fixture_set,
                evaluation_protocol=args.evaluation_protocol, attempts_per_fixture=args.attempts_per_fixture,
                attempt_id_prefix=args.attempt_id_prefix,
                fixture_selection=args.fixture_selection)
            for result in results:
                print(result)
            return 0
        elif args.command == "compare":
            result = compare(kind=args.kind, review_id=args.review_id, authoring_product=args.authoring_product,
                experiments=args.experiment, evaluation_protocol=args.evaluation_protocol, fixture_set=args.fixture_set,
                art_attempt=args.art_attempt, pet_experiment=args.pet_experiment, layout_attempt=args.layout_attempt,
                base_bundle_revision=args.base_bundle_revision,
                attempt_prefix=args.attempt_prefix,
                fixture_selection=args.fixture_selection)
        elif args.command == "record-decision":
            result = record_decision(
                review=args.review,
                selected_by=args.selected_by or _current_user(),
                notes=args.notes,
                selected_experiment=args.selected_experiment,
                selected_attempt=args.selected_attempt,
            )
        elif args.command == "prepare-print":
            result = prepare_print_candidate(
                candidate_id=args.candidate_id,
                authoring_product=args.authoring_product,
                art_attempt=args.art_attempt,
                pet_attempt=args.pet_attempt,
                layout_attempt=args.layout_attempt,
                art_review=args.art_review,
                pet_review=args.pet_review,
                layout_review=args.layout_review,
                pet_name=args.pet_name,
                backend=args.backend,
                reuse_template_from=args.reuse_template_from,
            )
        elif args.command == "graduate":
            result = graduate(graduation_id=args.graduation_id,
                print_candidate=args.print_candidate,
                art_review=args.art_review,
                pet_review=args.pet_review,
                layout_review=args.layout_review,
                assembly_review=args.assembly_review,
                selected_by=args.selected_by or _current_user(),
                notes=args.notes,
                authoring_root=args.authoring_root)
        elif args.command == "trace":
            result = trace_graduation(args.graduation)
        elif args.command == "set-status":
            result = set_status(args.experiment, args.status)
        elif args.command == "record-publication":
            result = record_publication(selection=args.selection, bundle_manifest=args.bundle_manifest,
                release_catalog=args.release_catalog, transfer_location=args.transfer_location,
                authoring_root=args.authoring_root)
        else:
            results = cleanup(authoring_root=args.authoring_root, status=args.status,
                              older_than_days=args.older_than_days, apply=args.apply)
            print("APPLY" if args.apply else "DRY RUN")
            for result in results:
                print(result)
            return 0
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return report_unexpected("pawmarvel-author", exc, debug=args.debug)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
