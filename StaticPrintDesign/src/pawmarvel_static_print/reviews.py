from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Any

from .core import StaticPrintError, utc_now, validate_design_id

VERDICTS = {"approve", "changes", "reject"}


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
                CREATE TABLE IF NOT EXISTS feedback (
                  design_id TEXT NOT NULL,
                  source_sha256 TEXT NOT NULL,
                  reviewer TEXT NOT NULL,
                  verdict TEXT NOT NULL,
                  comment TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (design_id, source_sha256, reviewer)
                )
                """
        )
        connection.execute(
            """
                CREATE TABLE IF NOT EXISTS operator_decisions (
                  design_id TEXT NOT NULL,
                  source_sha256 TEXT NOT NULL,
                  decision TEXT NOT NULL,
                  note TEXT NOT NULL,
                  decided_at TEXT NOT NULL,
                  PRIMARY KEY (design_id, source_sha256)
                )
                """
        )


def save_feedback(
    database: Path,
    *,
    design_id: str,
    source_sha256: str,
    reviewer: str,
    verdict: str,
    comment: str,
) -> dict[str, str]:
    return save_feedbacks(
        database,
        reviewer=reviewer,
        feedback=[
            {
                "design_id": design_id,
                "source_sha256": source_sha256,
                "verdict": verdict,
                "comment": comment,
            }
        ],
    )[0]


def save_feedbacks(
    database: Path,
    *,
    reviewer: str,
    feedback: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    reviewer = reviewer.strip()
    if not reviewer or len(reviewer) > 200:
        raise StaticPrintError("reviewer must contain 1-200 characters")
    raw_records = list(feedback)
    if not raw_records:
        raise StaticPrintError("feedback batch requires at least one design")
    updated_at = utc_now()
    records: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            raise StaticPrintError(f"feedback item {index} must be an object")
        design_id = validate_design_id(str(raw.get("design_id", "")), "design ID")
        source_sha256 = str(raw.get("source_sha256", ""))
        if len(source_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in source_sha256
        ):
            raise StaticPrintError(
                f"source_sha256 must be a lowercase SHA-256 digest: design_id={design_id!r}"
            )
        identity = (design_id, source_sha256)
        if identity in identities:
            raise StaticPrintError(
                f"duplicate design in feedback batch: design_id={design_id!r}"
            )
        identities.add(identity)
        verdict = str(raw.get("verdict", ""))
        if verdict not in VERDICTS:
            raise StaticPrintError(
                f"verdict must be one of {sorted(VERDICTS)}; "
                f"design_id={design_id!r}; actual={verdict!r}"
            )
        comment = str(raw.get("comment", "")).strip()
        if len(comment) > 4000:
            raise StaticPrintError(
                f"feedback comment must not exceed 4000 characters: design_id={design_id!r}"
            )
        records.append(
            {
                "design_id": design_id,
                "source_sha256": source_sha256,
                "reviewer": reviewer,
                "verdict": verdict,
                "comment": comment,
                "updated_at": updated_at,
            }
        )
    initialize_database(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executemany(
            """
                INSERT INTO feedback(
                  design_id, source_sha256, reviewer, verdict, comment, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(design_id, source_sha256, reviewer) DO UPDATE SET
                  verdict=excluded.verdict,
                  comment=excluded.comment,
                  updated_at=excluded.updated_at
                """,
            [
                (
                    item["design_id"],
                    item["source_sha256"],
                    item["reviewer"],
                    item["verdict"],
                    item["comment"],
                    item["updated_at"],
                )
                for item in records
            ],
        )
    return records


def list_feedback(database: Path) -> dict[str, list[dict[str, Any]]]:
    initialize_database(database)
    result: dict[str, list[dict[str, Any]]] = {}
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT design_id, source_sha256, reviewer, verdict, comment, updated_at "
            "FROM feedback ORDER BY design_id, updated_at DESC, reviewer"
        ).fetchall()
    for row in rows:
        result.setdefault(str(row["design_id"]), []).append(dict(row))
    return result


def save_operator_decision(
    database: Path,
    *,
    design_id: str,
    source_sha256: str,
    decision: str,
    note: str = "",
) -> dict[str, str]:
    return save_operator_decisions(
        database,
        designs=[(design_id, source_sha256)],
        decision=decision,
        note=note,
    )[0]


def save_operator_decisions(
    database: Path,
    *,
    designs: Iterable[tuple[str, str]],
    decision: str,
    note: str = "",
) -> list[dict[str, str]]:
    selected = list(dict.fromkeys(designs))
    if not selected:
        raise StaticPrintError("operator decisions require at least one design")
    if decision not in {"approved", "rework", "abandoned"}:
        raise StaticPrintError(
            "operator decision must be approved, rework, or abandoned; "
            f"actual={decision!r}"
        )
    for design_id, source_sha256 in selected:
        validate_design_id(design_id, "design ID")
        if len(source_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in source_sha256
        ):
            raise StaticPrintError(
                f"source_sha256 must be a lowercase SHA-256 digest: design_id={design_id!r}"
            )
    note = note.strip()
    if len(note) > 4000:
        raise StaticPrintError("operator decision note must not exceed 4000 characters")
    decided_at = utc_now()
    initialize_database(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executemany(
            """
                INSERT INTO operator_decisions(
                  design_id, source_sha256, decision, note, decided_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(design_id, source_sha256) DO UPDATE SET
                  decision=excluded.decision,
                  note=excluded.note,
                  decided_at=excluded.decided_at
                """,
            [
                (design_id, source_sha256, decision, note, decided_at)
                for design_id, source_sha256 in selected
            ],
        )
    return [
        {
            "design_id": design_id,
            "source_sha256": source_sha256,
            "decision": decision,
            "note": note,
            "decided_at": decided_at,
        }
        for design_id, source_sha256 in selected
    ]


def list_operator_decisions(database: Path) -> dict[str, list[dict[str, Any]]]:
    initialize_database(database)
    result: dict[str, list[dict[str, Any]]] = {}
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT design_id, source_sha256, decision, note, decided_at "
            "FROM operator_decisions ORDER BY design_id, decided_at DESC"
        ).fetchall()
    for row in rows:
        result.setdefault(str(row["design_id"]), []).append(dict(row))
    return result
