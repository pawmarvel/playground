"""Durable operation and per-work-unit progress records for resumable batches."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .core import StaticPrintError, utc_now, validate_id

ITEM_STATUSES = {"pending", "running", "succeeded", "skipped", "failed"}


def initialize_operations(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """
                CREATE TABLE IF NOT EXISTS operations (
                  operation_id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  label TEXT NOT NULL,
                  status TEXT NOT NULL,
                  request_json TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  error TEXT
                )
                """
        )
        connection.execute(
            """
                CREATE TABLE IF NOT EXISTS operation_events (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  operation_id TEXT NOT NULL,
                  item_key TEXT,
                  severity TEXT NOT NULL,
                  message TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY (operation_id) REFERENCES operations(operation_id)
                    ON DELETE CASCADE
                )
                """
        )
        connection.execute(
            """
                CREATE TABLE IF NOT EXISTS operation_items (
                  operation_id TEXT NOT NULL,
                  item_key TEXT NOT NULL,
                  ordinal INTEGER NOT NULL,
                  label TEXT NOT NULL,
                  status TEXT NOT NULL,
                  error TEXT,
                  PRIMARY KEY (operation_id, item_key),
                  FOREIGN KEY (operation_id) REFERENCES operations(operation_id)
                    ON DELETE CASCADE
                )
                """
        )


def _encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def start_operation(
    database: Path,
    *,
    operation_id: str,
    kind: str,
    label: str,
    request: dict[str, Any],
    items: list[tuple[str, str]],
    resume: bool = False,
) -> None:
    operation_id = validate_id(operation_id, "operation ID")
    if not items:
        raise StaticPrintError("tracked operation must contain at least one item")
    if len({item_key for item_key, _ in items}) != len(items):
        raise StaticPrintError(
            f"tracked operation contains duplicate item keys: operation_id={operation_id!r}"
        )
    initialize_operations(database)
    now = utc_now()
    request_json = _encoded(request)
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            existing = connection.execute(
                "SELECT kind, label, status, request_json FROM operations "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if not resume:
                    raise StaticPrintError(
                        f"operation ID already exists: operation_id={operation_id!r}; "
                        "resume or choose a new operation ID"
                    )
                mismatches = {
                    key: {"expected": expected, "actual": existing[key]}
                    for key, expected in {
                        "kind": kind,
                        "label": label,
                        "request_json": request_json,
                    }.items()
                    if existing[key] != expected
                }
                if mismatches:
                    raise StaticPrintError(
                        f"operation resume request mismatch: operation_id={operation_id!r}; "
                        f"mismatches={mismatches}"
                    )
                recorded = connection.execute(
                    "SELECT item_key, label FROM operation_items "
                    "WHERE operation_id = ? ORDER BY ordinal",
                    (operation_id,),
                ).fetchall()
                actual_items = [(row["item_key"], row["label"]) for row in recorded]
                if actual_items != items:
                    raise StaticPrintError(
                        f"operation resume item mismatch: operation_id={operation_id!r}; "
                        f"expected={items}; actual={actual_items}"
                    )
                connection.execute(
                    "UPDATE operations SET status = 'running', updated_at = ?, error = NULL "
                    "WHERE operation_id = ?",
                    (now, operation_id),
                )
                connection.execute(
                    "UPDATE operation_items SET status = 'pending', error = NULL "
                    "WHERE operation_id = ? AND status IN ('running', 'failed')",
                    (operation_id,),
                )
                return
            connection.execute(
                "INSERT INTO operations(operation_id, kind, label, status, request_json, "
                "started_at, updated_at, error) VALUES (?, ?, ?, 'running', ?, ?, ?, NULL)",
                (operation_id, kind, label, request_json, now, now),
            )
            connection.executemany(
                "INSERT INTO operation_items(operation_id, item_key, ordinal, label, "
                "status, error) VALUES (?, ?, ?, ?, 'pending', NULL)",
                [
                    (operation_id, item_key, ordinal, item_label)
                    for ordinal, (item_key, item_label) in enumerate(items)
                ],
            )


def update_operation_item(
    database: Path,
    *,
    operation_id: str,
    item_key: str,
    status: str,
    error: str | None = None,
) -> None:
    if status not in ITEM_STATUSES:
        raise ValueError(f"unsupported operation item status: {status!r}")
    initialize_operations(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        cursor = connection.execute(
            "UPDATE operation_items SET status = ?, error = ? "
            "WHERE operation_id = ? AND item_key = ?",
            (status, error, operation_id, item_key),
        )
        if cursor.rowcount != 1:
            raise StaticPrintError(
                f"operation item does not exist: operation_id={operation_id!r}; "
                f"item_key={item_key!r}"
            )
        connection.execute(
            "UPDATE operations SET updated_at = ? WHERE operation_id = ?",
            (utc_now(), operation_id),
        )


def append_operation_event(
    database: Path,
    *,
    operation_id: str,
    message: str,
    severity: str = "info",
    item_key: str | None = None,
) -> None:
    if severity not in {"info", "warning", "error"}:
        raise ValueError(f"unsupported operation event severity: {severity!r}")
    message = message.strip()
    if not message:
        raise ValueError("operation event message must not be empty")
    initialize_operations(database)
    now = utc_now()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO operation_events(operation_id, item_key, severity, "
            "message, created_at) VALUES (?, ?, ?, ?, ?)",
            (operation_id, item_key, severity, message, now),
        )
        connection.execute(
            "UPDATE operations SET updated_at = ? WHERE operation_id = ?",
            (now, operation_id),
        )


def finish_operation(
    database: Path, *, operation_id: str, error: str | None = None
) -> None:
    initialize_operations(database)
    status = "failed" if error else "succeeded"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE operations SET status = ?, updated_at = ?, error = ? "
            "WHERE operation_id = ?",
            (status, utc_now(), error, operation_id),
        )


def mark_running_operations_interrupted(database: Path) -> list[str]:
    initialize_operations(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        rows = connection.execute(
            "SELECT operation_id FROM operations WHERE status = 'running'"
        ).fetchall()
        identifiers = [str(row[0]) for row in rows]
        if identifiers:
            now = utc_now()
            connection.execute(
                "UPDATE operations SET status = 'interrupted', updated_at = ?, "
                "error = 'server stopped before the operation completed' "
                "WHERE status = 'running'",
                (now,),
            )
            connection.execute(
                "UPDATE operation_items SET status = 'pending', "
                "error = 'interrupted before commit' "
                "WHERE status = 'running'"
            )
            connection.executemany(
                "INSERT INTO operation_events(operation_id, item_key, severity, "
                "message, created_at) VALUES (?, NULL, 'warning', ?, ?)",
                [
                    (
                        operation_id,
                        (
                            "Server stopped before the operation completed. "
                            "Recommended fix: use Resume when offered; otherwise "
                            "verify committed output and retry the batch."
                        ),
                        now,
                    )
                    for operation_id in identifiers
                ],
            )
    return identifiers


def mark_operation_cleaned(database: Path, operation_id: str) -> None:
    initialize_operations(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE operations SET status = 'cleaned', updated_at = ?, error = NULL "
            "WHERE operation_id = ?",
            (utc_now(), operation_id),
        )


def operation_snapshots(
    database: Path,
    *,
    operation_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return bounded progress snapshots, or one exact historical operation."""

    if operation_id is not None:
        operation_id = validate_id(operation_id, "operation ID")
    if limit <= 0:
        raise ValueError("operation snapshot limit must be positive")
    initialize_operations(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        if operation_id is None:
            operations = connection.execute(
                "SELECT operation_id, kind, label, status, request_json, started_at, "
                "updated_at, error FROM operations "
                "ORDER BY started_at DESC, operation_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            operations = connection.execute(
                "SELECT operation_id, kind, label, status, request_json, started_at, "
                "updated_at, error FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchall()
        identifiers = [str(row["operation_id"]) for row in operations]
        if not identifiers:
            return []
        placeholders = ",".join("?" for _ in identifiers)
        items = connection.execute(
            "SELECT operation_id, item_key, ordinal, label, status, error "
            f"FROM operation_items WHERE operation_id IN ({placeholders}) "
            "ORDER BY operation_id, ordinal",
            identifiers,
        ).fetchall()
        events = connection.execute(
            "WITH ranked AS ("
            " SELECT operation_id, sequence, item_key, severity, message, created_at,"
            " ROW_NUMBER() OVER (PARTITION BY operation_id ORDER BY sequence DESC) AS rank"
            f" FROM operation_events WHERE operation_id IN ({placeholders})"
            ") SELECT operation_id, sequence, item_key, severity, message, created_at "
            "FROM ranked WHERE rank <= 12 ORDER BY operation_id, sequence",
            identifiers,
        ).fetchall()
        event_counts = connection.execute(
            "SELECT operation_id, "
            "SUM(CASE WHEN severity = 'warning' THEN 1 ELSE 0 END) AS warnings, "
            "SUM(CASE WHEN severity = 'error' THEN 1 ELSE 0 END) AS errors "
            f"FROM operation_events WHERE operation_id IN ({placeholders}) "
            "GROUP BY operation_id",
            identifiers,
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in items:
        grouped.setdefault(str(row["operation_id"]), []).append(
            {
                "item_key": str(row["item_key"]),
                "label": str(row["label"]),
                "status": str(row["status"]),
                "error": row["error"],
            }
        )
    grouped_events: dict[str, list[dict[str, Any]]] = {}
    for row in events:
        grouped_events.setdefault(str(row["operation_id"]), []).append(
            {
                "sequence": int(row["sequence"]),
                "item_key": row["item_key"],
                "severity": str(row["severity"]),
                "message": str(row["message"]),
                "created_at": str(row["created_at"]),
            }
        )
    grouped_event_counts = {
        str(row["operation_id"]): {
            "warnings": int(row["warnings"] or 0),
            "errors": int(row["errors"] or 0),
        }
        for row in event_counts
    }
    result: list[dict[str, Any]] = []
    for row in operations:
        operation_id = str(row["operation_id"])
        operation_items = grouped.get(operation_id, [])
        completed = sum(
            item["status"] in {"succeeded", "skipped", "failed"}
            for item in operation_items
        )
        failed = sum(item["status"] == "failed" for item in operation_items)
        status_counts = {
            status: sum(item["status"] == status for item in operation_items)
            for status in ITEM_STATUSES
        }
        operation_events = grouped_events.get(operation_id, [])
        operation_event_counts = grouped_event_counts.get(
            operation_id, {"warnings": 0, "errors": 0}
        )
        running_by_key = {
            item["item_key"]: item["label"]
            for item in operation_items
            if item["status"] == "running"
        }
        current = next(
            (
                running_by_key[event["item_key"]]
                for event in reversed(operation_events)
                if event["item_key"] in running_by_key
            ),
            next(iter(running_by_key.values()), None),
        )
        result.append(
            {
                "operation_id": operation_id,
                "kind": str(row["kind"]),
                "label": str(row["label"]),
                "status": str(row["status"]),
                "request": json.loads(str(row["request_json"])),
                "total": len(operation_items),
                "completed_count": completed,
                "failed_count": failed,
                "error_count": max(
                    failed,
                    1 if str(row["status"]) in {"failed", "interrupted"} else 0,
                ),
                "warning_count": operation_event_counts["warnings"],
                "status_counts": status_counts,
                "current": current,
                "items": operation_items,
                "recent_output": operation_events[-12:],
                "started_at": str(row["started_at"]),
                "updated_at": str(row["updated_at"]),
                "error": row["error"],
                "resumable": str(row["kind"])
                in {"subject-generation", "product-generation"},
            }
        )
    return result


def operation_snapshot(database: Path, operation_id: str) -> dict[str, Any]:
    operation_id = validate_id(operation_id, "operation ID")
    values = operation_snapshots(database, operation_id=operation_id)
    operation = values[0] if values else None
    if operation is None:
        raise StaticPrintError(
            f"operation does not exist: operation_id={operation_id!r}"
        )
    return operation
