"""Serve a private design-review gallery and persist reviewer votes in SQLite."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import mimetypes
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse


CHOICES = ("graduate", "consider", "pass")
MAX_BODY_BYTES = 16 * 1024
MAX_BATCH_BODY_BYTES = 256 * 1024
MAX_BATCH_VOTES = 250
MAX_BATCH_ACTIONS = 250
MAX_COMMENT_LENGTH = 1000
MAX_IDENTITY_LENGTH = 200


class GalleryError(ValueError):
    """Raised when gallery input or persisted data is invalid."""


@dataclass(frozen=True)
class GalleryConfig:
    root: Path
    index: Path
    database: Path
    authoring_root: Path | None = None
    abandoned_root: Path | None = None
    graduation_root: Path | None = None
    collections: tuple[str, ...] = ()
    reviewer_access_code: str | None = None
    operator_access_code: str | None = None
    retention_days: int = 30
    decisions_dir: Path | None = None


@dataclass(frozen=True)
class GallerySnapshot:
    concepts: list[dict[str, Any]]
    active_designs: dict[str, dict[str, Any]]
    all_designs: dict[str, dict[str, Any]]
    missing_design_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReconciliationResult:
    active: int
    added: tuple[str, ...]
    removed: tuple[str, ...]
    restored: tuple[str, ...]
    purged: tuple[str, ...]
    removed_without_disposition: tuple[str, ...]


class GalleryStore:
    def __init__(self, database: Path, decisions_dir: Path | None = None) -> None:
        self.database = database.resolve()
        self.decisions_dir = (
            decisions_dir or self.database.parent / "decisions"
        ).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.decisions_dir.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            with connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA secure_delete=ON")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS votes (
                        reviewer_id TEXT NOT NULL,
                        reviewer_name TEXT NOT NULL,
                        design_id TEXT NOT NULL,
                        choice TEXT NOT NULL CHECK(choice IN ('graduate','consider','pass')),
                        comment TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (reviewer_id, design_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS design_registry (
                        design_id TEXT PRIMARY KEY,
                        metadata_json TEXT NOT NULL,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        removed_at TEXT,
                        purge_after TEXT,
                        disposition TEXT CHECK(
                            disposition IS NULL OR
                            disposition IN ('graduated','abandoned')
                        ),
                        decided_at TEXT,
                        decided_by TEXT,
                        decision_notes TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                self._ensure_registry_column(
                    connection, "lifecycle_state", "TEXT NOT NULL DEFAULT 'active'"
                )
                self._ensure_registry_column(
                    connection, "current_pool_path", "TEXT"
                )
                self._ensure_registry_column(
                    connection, "review_round", "INTEGER NOT NULL DEFAULT 1"
                )
                self._ensure_registry_column(connection, "raw_purged_at", "TEXT")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS archived_votes (
                        design_id TEXT NOT NULL,
                        review_round INTEGER NOT NULL,
                        reviewer_id TEXT NOT NULL,
                        reviewer_name TEXT NOT NULL,
                        choice TEXT NOT NULL CHECK(
                            choice IN ('graduate','consider','pass')
                        ),
                        comment TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL,
                        purge_after TEXT NOT NULL,
                        PRIMARY KEY (design_id, review_round, reviewer_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS design_events (
                        event_id TEXT PRIMARY KEY,
                        design_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        from_state TEXT,
                        to_state TEXT,
                        review_round INTEGER NOT NULL,
                        operator_id TEXT NOT NULL,
                        reason TEXT NOT NULL DEFAULT '',
                        vote_summary_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )

    @staticmethod
    def _ensure_registry_column(
        connection: sqlite3.Connection, name: str, declaration: str
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(design_registry)")
        }
        if name not in columns:
            connection.execute(
                f"ALTER TABLE design_registry ADD COLUMN {name} {declaration}"
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA secure_delete=ON")
        return connection

    def reconcile(
        self,
        active_designs: dict[str, dict[str, Any]],
        *,
        retention_days: int,
        now: datetime | None = None,
    ) -> ReconciliationResult:
        if retention_days < 1:
            raise GalleryError(
                f"retention_days must be at least 1; actual={retention_days}"
            )
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise GalleryError("reconciliation time must include a timezone")
        timestamp = _timestamp(current)
        purge_after = _timestamp(current + timedelta(days=retention_days))
        added: list[str] = []
        removed: list[str] = []
        restored: list[str] = []
        purged: list[str] = []
        missing_disposition: list[str] = []

        with closing(self.connect()) as connection:
            existing = {
                row["design_id"]: dict(row)
                for row in connection.execute(
                    "SELECT * FROM design_registry ORDER BY design_id"
                ).fetchall()
            }
            with connection:
                for design_id, metadata in sorted(active_designs.items()):
                    metadata_json = json.dumps(
                        metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    prior = existing.get(design_id)
                    if prior is None:
                        connection.execute(
                            """
                            INSERT INTO design_registry (
                                design_id, metadata_json, first_seen_at, last_seen_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (design_id, metadata_json, timestamp, timestamp),
                        )
                        added.append(design_id)
                    else:
                        if prior["removed_at"] is not None:
                            restored.append(design_id)
                        connection.execute(
                            """
                            UPDATE design_registry
                            SET metadata_json=?, last_seen_at=?, removed_at=NULL,
                                purge_after=NULL
                            WHERE design_id=?
                            """,
                            (metadata_json, timestamp, design_id),
                        )

                for design_id, prior in sorted(existing.items()):
                    if design_id in active_designs or prior["removed_at"] is not None:
                        continue
                    connection.execute(
                        """
                        UPDATE design_registry
                        SET removed_at=?, purge_after=?
                        WHERE design_id=?
                        """,
                        (timestamp, purge_after, design_id),
                    )
                    removed.append(design_id)
                    if prior["disposition"] is None:
                        missing_disposition.append(design_id)

            changed_rows = list(
                connection.execute(
                    """
                    SELECT * FROM design_registry
                    WHERE removed_at IS NOT NULL
                    ORDER BY design_id
                    """
                ).fetchall()
            )
            for design_id in restored:
                row = connection.execute(
                    "SELECT * FROM design_registry WHERE design_id=?", (design_id,)
                ).fetchone()
                if row is not None:
                    changed_rows.append(row)
            for row in changed_rows:
                record = self._decision_record(connection, dict(row), timestamp)
                self._write_decision(record)

            expired = connection.execute(
                """
                SELECT * FROM design_registry
                WHERE removed_at IS NOT NULL AND purge_after <= ?
                ORDER BY design_id
                """,
                (timestamp,),
            ).fetchall()
            for row in expired:
                record = self._decision_record(
                    connection, dict(row), timestamp, minimal=True
                )
                self._write_decision(record)
            if expired:
                with connection:
                    for row in expired:
                        design_id = row["design_id"]
                        connection.execute(
                            "DELETE FROM votes WHERE design_id=?", (design_id,)
                        )
                        connection.execute(
                            """
                            UPDATE design_registry
                            SET raw_purged_at=?, purge_after=NULL
                            WHERE design_id=?
                            """,
                            (timestamp, design_id),
                        )
                        purged.append(design_id)
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            with connection:
                archived_purged = connection.execute(
                    "DELETE FROM archived_votes WHERE purge_after <= ?",
                    (timestamp,),
                ).rowcount
            if archived_purged:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        return ReconciliationResult(
            active=len(active_designs),
            added=tuple(added),
            removed=tuple(removed),
            restored=tuple(restored),
            purged=tuple(purged),
            removed_without_disposition=tuple(missing_disposition),
        )

    def sync_catalog(
        self,
        designs: dict[str, dict[str, Any]],
        *,
        retention_days: int,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(timezone.utc)
        timestamp = _timestamp(current)
        purge_after = _timestamp(current + timedelta(days=retention_days))
        with closing(self.connect()) as connection:
            with connection:
                for design_id, design in sorted(designs.items()):
                    state = str(design["lifecycle_state"])
                    metadata = {
                        key: value
                        for key, value in design.items()
                        if key not in {"lifecycle_state", "pool_path"}
                    }
                    metadata_json = json.dumps(
                        metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    row = connection.execute(
                        "SELECT * FROM design_registry WHERE design_id=?",
                        (design_id,),
                    ).fetchone()
                    if row is None:
                        inactive = state != "active"
                        connection.execute(
                            """
                            INSERT INTO design_registry (
                                design_id, metadata_json, first_seen_at, last_seen_at,
                                removed_at, purge_after, disposition, lifecycle_state,
                                current_pool_path, review_round
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                            """,
                            (
                                design_id,
                                metadata_json,
                                timestamp,
                                timestamp,
                                timestamp if inactive else None,
                                purge_after if inactive else None,
                                state if state in {"abandoned", "graduated"} else None,
                                state,
                                str(design["pool_path"]),
                            ),
                        )
                        continue
                    prior = dict(row)
                    review_round = int(prior["review_round"] or 1)
                    raw_purged_at = prior["raw_purged_at"]
                    if state == "active" and prior["lifecycle_state"] != "active":
                        if raw_purged_at:
                            review_round += 1
                            raw_purged_at = None
                        removed_at = None
                        next_purge = None
                    elif state != "active":
                        removed_at = prior["removed_at"] or timestamp
                        next_purge = prior["purge_after"]
                        if not raw_purged_at and not next_purge:
                            next_purge = purge_after
                    else:
                        removed_at = None
                        next_purge = None
                    connection.execute(
                        """
                        UPDATE design_registry
                        SET metadata_json=?, last_seen_at=?, lifecycle_state=?,
                            current_pool_path=?, removed_at=?, purge_after=?,
                            disposition=?, review_round=?, raw_purged_at=?
                        WHERE design_id=?
                        """,
                        (
                            metadata_json,
                            timestamp,
                            state,
                            str(design["pool_path"]),
                            removed_at,
                            next_purge,
                            state if state in {"abandoned", "graduated"} else None,
                            review_round,
                            raw_purged_at,
                            design_id,
                        ),
                    )

    def reset_review_round(
        self,
        *,
        design_id: str,
        operator_id: str,
        reason: str,
        retention_days: int,
    ) -> dict[str, Any]:
        current = datetime.now(timezone.utc)
        timestamp = _timestamp(current)
        purge_after = _timestamp(current + timedelta(days=retention_days))
        with closing(self.connect()) as connection:
            row = self._active_registry_row(connection, design_id)
            review_round = int(row["review_round"] or 1)
            summary = self._vote_summary(connection, design_id)
            event_path: Path | None = None
            try:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO archived_votes (
                            design_id, review_round, reviewer_id, reviewer_name,
                            choice, comment, updated_at, purge_after
                        )
                        SELECT design_id, ?, reviewer_id, reviewer_name, choice,
                               comment, updated_at, ?
                        FROM votes WHERE design_id=?
                        """,
                        (review_round, purge_after, design_id),
                    )
                    connection.execute(
                        "DELETE FROM votes WHERE design_id=?", (design_id,)
                    )
                    connection.execute(
                        "UPDATE design_registry SET review_round=? WHERE design_id=?",
                        (review_round + 1, design_id),
                    )
                    event = self._insert_event(
                        connection,
                        design_id=design_id,
                        event_type="review_round_reset",
                        from_state="active",
                        to_state="active",
                        review_round=review_round,
                        operator_id=operator_id,
                        reason=reason,
                        summary=summary,
                        timestamp=timestamp,
                    )
                    event_path = self._write_event(event)
            except Exception:
                if event_path is not None:
                    event_path.unlink(missing_ok=True)
                raise
        return event

    def transition_design(
        self,
        *,
        design_id: str,
        target_state: str,
        pool_path: Path,
        operator_id: str,
        reason: str,
        retention_days: int,
    ) -> dict[str, Any]:
        return self.transition_designs(
            design_ids_and_paths=((design_id, pool_path),),
            target_state=target_state,
            operator_id=operator_id,
            reason=reason,
            retention_days=retention_days,
        )[0]

    def transition_designs(
        self,
        *,
        design_ids_and_paths: tuple[tuple[str, Path], ...],
        target_state: str,
        operator_id: str,
        reason: str,
        retention_days: int,
    ) -> list[dict[str, Any]]:
        if target_state not in {"abandoned", "graduated"}:
            raise GalleryError(f"invalid lifecycle target: {target_state!r}")
        if not design_ids_and_paths:
            raise GalleryError(
                "at least one design is required for a lifecycle transition"
            )
        design_ids = [design_id for design_id, _ in design_ids_and_paths]
        if len(set(design_ids)) != len(design_ids):
            raise GalleryError("lifecycle transition contains duplicate design IDs")
        current = datetime.now(timezone.utc)
        timestamp = _timestamp(current)
        purge_after = _timestamp(current + timedelta(days=retention_days))
        event_paths: list[Path] = []
        events: list[dict[str, Any]] = []
        with closing(self.connect()) as connection:
            rows = {
                design_id: self._active_registry_row(connection, design_id)
                for design_id in design_ids
            }
            summaries = {
                design_id: self._vote_summary(connection, design_id)
                for design_id in design_ids
            }
            try:
                with connection:
                    for design_id, pool_path in design_ids_and_paths:
                        connection.execute(
                            """
                            UPDATE design_registry
                            SET lifecycle_state=?, current_pool_path=?, removed_at=?,
                                purge_after=?, disposition=?, decided_at=?, decided_by=?,
                                decision_notes=?, raw_purged_at=NULL
                            WHERE design_id=?
                            """,
                            (
                                target_state,
                                str(pool_path.resolve()),
                                timestamp,
                                purge_after,
                                target_state,
                                timestamp,
                                operator_id,
                                reason,
                                design_id,
                            ),
                        )
                        event = self._insert_event(
                            connection,
                            design_id=design_id,
                            event_type=target_state,
                            from_state="active",
                            to_state=target_state,
                            review_round=int(rows[design_id]["review_round"] or 1),
                            operator_id=operator_id,
                            reason=reason,
                            summary=summaries[design_id],
                            timestamp=timestamp,
                        )
                        events.append(event)
                        event_paths.append(self._write_event(event))
            except Exception:
                for event_path in event_paths:
                    event_path.unlink(missing_ok=True)
                raise
        return events

    def restore_design(
        self,
        *,
        design_id: str,
        pool_path: Path,
        operator_id: str,
        reason: str,
    ) -> dict[str, Any]:
        return self.restore_designs(
            design_ids_and_paths=((design_id, pool_path),),
            operator_id=operator_id,
            reason=reason,
        )[0]

    def restore_designs(
        self,
        *,
        design_ids_and_paths: tuple[tuple[str, Path], ...],
        operator_id: str,
        reason: str,
    ) -> list[dict[str, Any]]:
        if not design_ids_and_paths:
            raise GalleryError("at least one design is required for a restore")
        design_ids = [design_id for design_id, _ in design_ids_and_paths]
        if len(set(design_ids)) != len(design_ids):
            raise GalleryError("restore contains duplicate design IDs")
        timestamp = _timestamp(datetime.now(timezone.utc))
        event_paths: list[Path] = []
        events: list[dict[str, Any]] = []
        with closing(self.connect()) as connection:
            rows = {
                design_id: connection.execute(
                    "SELECT * FROM design_registry WHERE design_id=?", (design_id,)
                ).fetchone()
                for design_id in design_ids
            }
            for design_id, row in rows.items():
                if row is None or row["lifecycle_state"] not in {
                    "abandoned",
                    "graduated",
                }:
                    actual = None if row is None else row["lifecycle_state"]
                    raise GalleryError(
                        "design is not restorable from an inactive pool; "
                        f"design_id={design_id}; actual={actual!r}"
                    )
            summaries = {
                design_id: self._vote_summary(connection, design_id)
                for design_id in design_ids
            }
            try:
                with connection:
                    for design_id, pool_path in design_ids_and_paths:
                        row = rows[design_id]
                        assert row is not None
                        from_state = str(row["lifecycle_state"])
                        review_round = int(row["review_round"] or 1)
                        if row["raw_purged_at"]:
                            review_round += 1
                        connection.execute(
                            """
                            UPDATE design_registry
                            SET lifecycle_state='active', current_pool_path=?,
                                removed_at=NULL, purge_after=NULL, disposition=NULL,
                                review_round=?, raw_purged_at=NULL
                            WHERE design_id=?
                            """,
                            (str(pool_path.resolve()), review_round, design_id),
                        )
                        event = self._insert_event(
                            connection,
                            design_id=design_id,
                            event_type="restored",
                            from_state=from_state,
                            to_state="active",
                            review_round=review_round,
                            operator_id=operator_id,
                            reason=reason,
                            summary=summaries[design_id],
                            timestamp=timestamp,
                        )
                        events.append(event)
                        event_paths.append(self._write_event(event))
            except Exception:
                for event_path in event_paths:
                    event_path.unlink(missing_ok=True)
                raise
        return events

    def operator_designs(
        self, designs: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = {
                row["design_id"]: dict(row)
                for row in connection.execute(
                    "SELECT * FROM design_registry ORDER BY design_id"
                ).fetchall()
            }
            result = []
            for design_id, design in designs.items():
                registry = rows.get(design_id)
                if registry is None:
                    continue
                votes = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM votes WHERE design_id=? ORDER BY updated_at",
                        (design_id,),
                    ).fetchall()
                ]
                totals = {choice: 0 for choice in CHOICES}
                for vote in votes:
                    totals[vote["choice"]] += 1
                result.append(
                    {
                        **{
                            key: value
                            for key, value in design.items()
                            if key != "pool_path"
                        },
                        "review_round": registry["review_round"],
                        "removed_at": registry["removed_at"],
                        "purge_after": registry["purge_after"],
                        "raw_purged_at": registry["raw_purged_at"],
                        "vote_count": len(votes),
                        "vote_totals": totals,
                        "net_score": totals["graduate"] - totals["pass"],
                        "feedback": votes,
                        "operator_image_url": (
                            f"/operator-assets/{quote(design_id)}/reference-design.png"
                        ),
                    }
                )
        return result

    def _active_registry_row(
        self, connection: sqlite3.Connection, design_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM design_registry WHERE design_id=?", (design_id,)
        ).fetchone()
        if row is None or row["lifecycle_state"] != "active":
            raise GalleryError(f"design is not active: {design_id}")
        return row

    @staticmethod
    def _vote_summary(
        connection: sqlite3.Connection, design_id: str
    ) -> dict[str, int]:
        totals = {choice: 0 for choice in CHOICES}
        for row in connection.execute(
            "SELECT choice, COUNT(*) AS count FROM votes WHERE design_id=? GROUP BY choice",
            (design_id,),
        ):
            totals[row["choice"]] = row["count"]
        return totals

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        design_id: str,
        event_type: str,
        from_state: str,
        to_state: str,
        review_round: int,
        operator_id: str,
        reason: str,
        summary: dict[str, int],
        timestamp: str,
    ) -> dict[str, Any]:
        event = {
            "schema_version": 1,
            "event_id": uuid.uuid4().hex,
            "design_id": design_id,
            "event_type": event_type,
            "from_state": from_state,
            "to_state": to_state,
            "review_round": review_round,
            "operator_id": operator_id,
            "reason": reason,
            "vote_summary": summary,
            "created_at": timestamp,
        }
        connection.execute(
            """
            INSERT INTO design_events (
                event_id, design_id, event_type, from_state, to_state,
                review_round, operator_id, reason, vote_summary_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                design_id,
                event_type,
                from_state,
                to_state,
                review_round,
                operator_id,
                reason,
                json.dumps(summary, sort_keys=True),
                timestamp,
            ),
        )
        return event

    def _write_event(self, event: dict[str, Any]) -> Path:
        design_id = _safe_design_id(str(event["design_id"]))
        folder = self.decisions_dir / design_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (
            f"{event['created_at'].replace(':', '-')}-{event['event_type']}-"
            f"{event['event_id']}.json"
        )
        temporary = path.with_suffix(".json.partial")
        try:
            temporary.write_text(
                json.dumps(event, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return path

    def _decision_record(
        self,
        connection: sqlite3.Connection,
        registry: dict[str, Any],
        recorded_at: str,
        *,
        minimal: bool = False,
    ) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT choice, COUNT(*) AS count FROM votes WHERE design_id=? GROUP BY choice",
            (registry["design_id"],),
        ).fetchall()
        totals = {choice: 0 for choice in CHOICES}
        for row in rows:
            totals[row["choice"]] = row["count"]
        record = {
            "schema_version": 1,
            "design_id": registry["design_id"],
            "outcome": registry["disposition"] or "unclassified",
            "decided_at": registry["decided_at"],
            "status": "removed" if registry["removed_at"] else "active",
            "removed_at": registry["removed_at"],
            "recorded_at": recorded_at,
            "final_vote_count": sum(totals.values()),
            "final_vote_totals": totals,
        }
        if not minimal:
            record["decided_by"] = registry["decided_by"]
            record["notes"] = registry["decision_notes"]
        return record

    def _write_decision(self, record: dict[str, Any]) -> Path:
        design_id = _safe_design_id(str(record["design_id"]))
        folder = self.decisions_dir / "_reconciliation"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{design_id}.json"
        temporary = path.with_suffix(".json.partial")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
        return path

    def registry(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM design_registry ORDER BY design_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_vote(
        self,
        *,
        reviewer_id: str,
        reviewer_name: str,
        design_id: str,
        choice: str,
        comment: str,
    ) -> dict[str, str]:
        return self.save_votes(
            reviewer_id=reviewer_id,
            reviewer_name=reviewer_name,
            votes=[
                {
                    "design_id": design_id,
                    "choice": choice,
                    "comment": comment,
                }
            ],
        )[0]

    def save_votes(
        self,
        *,
        reviewer_id: str,
        reviewer_name: str,
        votes: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Atomically upsert one reviewer's changed votes."""
        if not votes:
            raise GalleryError("at least one changed vote is required")
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with closing(self.connect()) as connection:
            with connection:
                for vote in votes:
                    self._active_registry_row(connection, vote["design_id"])
                connection.executemany(
                    """
                    INSERT INTO votes (
                        reviewer_id, reviewer_name, design_id, choice, comment, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(reviewer_id, design_id) DO UPDATE SET
                        reviewer_name=excluded.reviewer_name,
                        choice=excluded.choice,
                        comment=excluded.comment,
                        updated_at=excluded.updated_at
                    """,
                    [
                        (
                            reviewer_id,
                            reviewer_name,
                            vote["design_id"],
                            vote["choice"],
                            vote["comment"],
                            timestamp,
                        )
                        for vote in votes
                    ],
                )
        return [
            {
                "reviewer_id": reviewer_id,
                "reviewer_name": reviewer_name,
                **vote,
                "updated_at": timestamp,
            }
            for vote in votes
        ]

    def votes_for(self, reviewer_id: str) -> dict[str, dict[str, str]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM votes WHERE reviewer_id=? ORDER BY design_id",
                (reviewer_id,),
            ).fetchall()
        return {row["design_id"]: dict(row) for row in rows}

    def all_votes(self) -> list[dict[str, str]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM votes ORDER BY design_id, reviewer_id"
            ).fetchall()
        return [dict(row) for row in rows]


class GalleryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        config: GalleryConfig,
        concepts: list[dict[str, Any]],
        designs: dict[str, dict[str, Any]],
        store: GalleryStore,
        reconciliation: ReconciliationResult,
        missing_design_ids: tuple[str, ...],
    ) -> None:
        super().__init__(address, handler)
        self.gallery_config = config
        self.concepts = concepts
        self.designs = designs
        self.concept_by_id = {item["design_id"]: item for item in concepts}
        self.review_asset_by_url = _review_asset_map(config, concepts)
        self.store = store
        self.reconciliation = reconciliation
        self.missing_design_ids = missing_design_ids
        reviewer_code = config.reviewer_access_code
        self.reviewer_auth_token = (
            hashlib.sha256(f"reviewer:{reviewer_code}".encode("utf-8")).hexdigest()
            if reviewer_code
            else None
        )
        self.operator_auth_token = (
            hashlib.sha256(
                f"operator:{config.operator_access_code}".encode("utf-8")
            ).hexdigest()
            if config.operator_access_code
            else None
        )
        self.auth_token = self.reviewer_auth_token
        self.lifecycle_lock = threading.Lock()

    def refresh(self) -> None:
        snapshot = _load_concepts(self.gallery_config)
        self.store.reconcile(
            snapshot.active_designs,
            retention_days=self.gallery_config.retention_days,
        )
        self.store.sync_catalog(
            snapshot.all_designs,
            retention_days=self.gallery_config.retention_days,
        )
        self.concepts = snapshot.concepts
        self.designs = snapshot.all_designs
        self.concept_by_id = {item["design_id"]: item for item in self.concepts}
        self.review_asset_by_url = _review_asset_map(
            self.gallery_config, self.concepts
        )
        self.missing_design_ids = snapshot.missing_design_ids


def _load_concepts(config: GalleryConfig) -> GallerySnapshot:
    root = config.root.resolve()
    abandoned_root = (
        config.abandoned_root or root.parent / "Abandoned Design Pool"
    ).resolve()
    graduation_root = (
        config.graduation_root or root.parent / "Graduation Pool"
    ).resolve()
    index = config.index.resolve()
    if not root.is_dir():
        raise GalleryError(f"gallery root is not a directory: {root}")
    try:
        document = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GalleryError(f"gallery index is not readable JSON: {index}") from exc
    raw = document.get("concepts")
    if not isinstance(raw, list):
        raise GalleryError(f"gallery index has no concepts array: {index}")
    selected: list[dict[str, Any]] = []
    active_designs: dict[str, dict[str, Any]] = {}
    all_designs: dict[str, dict[str, Any]] = {}
    missing_design_ids: list[str] = []
    allowed_collections = set(config.collections)
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise GalleryError(f"gallery concept is not an object: {item!r}")
        design_id = item.get("design_id")
        folder = item.get("folder")
        if not isinstance(design_id, str) or not design_id:
            raise GalleryError("gallery concept has an empty design_id")
        _safe_design_id(design_id)
        if design_id in seen:
            raise GalleryError(f"gallery design_id is duplicated: {design_id}")
        if folder != design_id:
            raise GalleryError(
                f"gallery folder/design_id mismatch: folder={folder!r}; design_id={design_id!r}"
            )
        seen.add(design_id)
        candidates = [
            ("active", (root / folder).resolve(), root),
            ("abandoned", (abandoned_root / folder).resolve(), abandoned_root),
            ("graduated", (graduation_root / folder).resolve(), graduation_root),
        ]
        located = [candidate for candidate in candidates if candidate[1].exists()]
        if len(located) > 1:
            paths = ", ".join(str(candidate[1]) for candidate in located)
            raise GalleryError(
                f"design exists in multiple lifecycle pools: {design_id}; paths={paths}"
            )
        if not located:
            missing_design_ids.append(design_id)
            continue
        lifecycle_state, design_folder, pool_root = located[0]
        try:
            design_folder.relative_to(pool_root)
        except ValueError as exc:
            raise GalleryError(
                f"gallery design folder escapes its lifecycle pool: {design_folder}"
            ) from exc
        if not design_folder.is_dir():
            raise GalleryError(f"gallery design path is not a directory: {design_folder}")
        image = (design_folder / "reference-design.png").resolve()
        try:
            image.relative_to(pool_root)
        except ValueError as exc:
            raise GalleryError(f"gallery reference image escapes root: {image}") from exc
        if not image.is_file():
            raise GalleryError(
                "gallery design folder exists but its reference image is missing; "
                f"repair or remove the whole design folder: {image}"
            )
        metadata = {
            "design_id": design_id,
            "collection": str(item.get("collection", "")),
            "concept": str(item.get("concept", "")),
            "headline": str(item.get("headline", "")),
            "bottom_line": str(item.get("bottom_line", "")),
            "bottom_line_option": item.get("bottom_line_option"),
            "priority": item.get("priority"),
            "row": item.get("row"),
        }
        all_designs[design_id] = {
            **metadata,
            "lifecycle_state": lifecycle_state,
            "pool_path": str(design_folder),
        }
        if lifecycle_state != "active":
            continue
        active_designs[design_id] = metadata
        if allowed_collections and item.get("collection") not in allowed_collections:
            continue
        selected.append(
            {
                **metadata,
                "image_url": f"/assets/{quote(design_id)}/reference-design.png",
                "review_contexts": _discover_review_contexts(config, design_id),
            }
        )
    return GallerySnapshot(
        concepts=selected,
        active_designs=active_designs,
        all_designs=all_designs,
        missing_design_ids=tuple(sorted(missing_design_ids)),
    )


def _select_review(
    root: Path,
    *,
    kind: str,
    artifact_name: str,
    preferred_id: str,
    require_release: bool = False,
) -> dict[str, str] | None:
    if not root.is_dir():
        return None
    candidates: list[dict[str, str]] = []
    for review in sorted(path for path in root.iterdir() if path.is_dir()):
        evaluation_path = review / "evaluation.json"
        comparison_path = review / "artifacts" / artifact_name
        if not evaluation_path.is_file() or not comparison_path.is_file():
            continue
        try:
            evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if evaluation.get("kind") != kind:
            continue
        if require_release and evaluation.get("fixture_tier") != "release":
            continue
        candidates.append(
            {
                "review_id": review.name,
                "created_at": str(evaluation.get("created_at", "")),
            }
        )
    if not candidates:
        return None
    return next(
        (candidate for candidate in candidates if candidate["review_id"] == preferred_id),
        max(candidates, key=lambda candidate: (candidate["created_at"], candidate["review_id"])),
    )


def _discover_review_contexts(
    config: GalleryConfig, design_id: str
) -> list[dict[str, str]]:
    if config.authoring_root is None:
        return []
    authoring_root = config.authoring_root.resolve()
    design_root = (authoring_root / design_id).resolve()
    try:
        design_root.relative_to(authoring_root)
    except ValueError:
        return []
    if not design_root.is_dir():
        return []
    contexts: list[dict[str, str]] = []
    for product in sorted(path for path in design_root.iterdir() if path.is_dir()):
        art = _select_review(
            product / "reviews/art",
            kind="art",
            artifact_name="art-comparison.png",
            preferred_id="art-baseline",
        )
        pet = _select_review(
            product / "reviews/pet",
            kind="pet",
            artifact_name="pet-comparison.png",
            preferred_id="pet-gpt-release",
            require_release=True,
        )
        if art is None or pet is None:
            continue
        base = f"/review-assets/{quote(design_id)}/{quote(product.name)}"
        contexts.append(
            {
                "product_profile_id": product.name,
                "art_review_id": art["review_id"],
                "pet_review_id": pet["review_id"],
                "art_comparison_url": f"{base}/art-comparison.png",
                "pet_comparison_url": f"{base}/pet-comparison.png",
                "art_evaluation_url": f"{base}/art-evaluation.json",
                "pet_evaluation_url": f"{base}/pet-evaluation.json",
            }
        )
        # The gallery is for concept voting, not cross-profile comparison. One
        # deterministic complete profile is sufficient supporting evidence.
        break
    return contexts


def _review_asset_map(
    config: GalleryConfig, concepts: list[dict[str, Any]]
) -> dict[str, Path]:
    if config.authoring_root is None:
        return {}
    authoring_root = config.authoring_root.resolve()
    assets: dict[str, Path] = {}
    for concept in concepts:
        design_id = concept["design_id"]
        for context in concept["review_contexts"]:
            product = authoring_root / design_id / context["product_profile_id"]
            art = product / "reviews/art" / context["art_review_id"]
            pet = product / "reviews/pet" / context["pet_review_id"]
            assets[context["art_comparison_url"]] = art / "artifacts/art-comparison.png"
            assets[context["pet_comparison_url"]] = pet / "artifacts/pet-comparison.png"
            assets[context["art_evaluation_url"]] = art / "evaluation.json"
            assets[context["pet_evaluation_url"]] = pet / "evaluation.json"
    return {url: path.resolve() for url, path in assets.items()}


def _static(name: str) -> bytes:
    path = Path(__file__).with_name("static") / name
    return path.read_bytes()


def _login_html(error: str = "", *, operator: bool = False) -> bytes:
    message = f'<p class="error">{error}</p>' if error else ""
    role = "Operator" if operator else "Reviewer"
    action = "/operator/login" if operator else "/login"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PawMarvel {role} Access</title><style>
body{{font:16px system-ui,sans-serif;background:#f4f2ed;margin:0;color:#222}}main{{max-width:420px;margin:12vh auto;background:#fff;padding:28px;border-radius:14px;box-shadow:0 8px 30px #0002}}input,button{{box-sizing:border-box;width:100%;font:inherit;padding:12px;margin-top:10px}}button{{background:#1f5b45;color:#fff;border:0;border-radius:8px;font-weight:700}}.error{{color:#a32121}}
</style></head><body><main><h1>{role} access</h1><p>Enter the {role.lower()} access code supplied by the gallery owner.</p>{message}
<form method="post" action="{action}"><input name="access_code" type="password" required autofocus autocomplete="current-password"><button>Continue</button></form>
</main></body></html>""".encode("utf-8")


def _operator_payload(server: GalleryHTTPServer) -> dict[str, Any]:
    designs = server.store.operator_designs(server.designs)
    active = sorted(
        (item for item in designs if item["lifecycle_state"] == "active"),
        key=lambda item: (
            -item["vote_totals"]["graduate"],
            -item["net_score"],
            -item["vote_count"],
            item["design_id"],
        ),
    )
    for rank, item in enumerate(active, start=1):
        item["rank"] = rank
    return {
        "schema_version": 1,
        "generated_at": _timestamp(datetime.now(timezone.utc)),
        "active": active,
        "abandoned": sorted(
            (item for item in designs if item["lifecycle_state"] == "abandoned"),
            key=lambda item: item["design_id"],
        ),
        "graduated": sorted(
            (item for item in designs if item["lifecycle_state"] == "graduated"),
            key=lambda item: item["design_id"],
        ),
        "missing_design_ids": list(server.missing_design_ids),
    }


def _operator_action(server: GalleryHTTPServer, payload: dict[str, Any]) -> dict[str, Any]:
    action = _identity(payload.get("action"), "action")
    design_id = _identity(payload.get("design_id"), "design_id")
    operator_id = _identity(payload.get("operator_id"), "operator_id")
    reason = str(payload.get("reason", "")).strip()
    if len(reason) > MAX_COMMENT_LENGTH:
        raise GalleryError(f"reason exceeds {MAX_COMMENT_LENGTH} characters")
    design = server.designs.get(design_id)
    if design is None:
        raise GalleryError(f"unknown design_id: {design_id}")

    if action == "new-round":
        event = server.store.reset_review_round(
            design_id=design_id,
            operator_id=operator_id,
            reason=reason or "Improvement iteration",
            retention_days=server.gallery_config.retention_days,
        )
        return {"event": event, "dashboard": _operator_payload(server)}

    roots = {
        "active": server.gallery_config.root,
        "abandoned": server.gallery_config.abandoned_root,
        "graduated": server.gallery_config.graduation_root,
    }
    current_state = str(design["lifecycle_state"])
    if action in {"abandon", "graduate"}:
        if current_state != "active":
            raise GalleryError(
                f"{action} requires an active design; actual={current_state}"
            )
        target_state = "abandoned" if action == "abandon" else "graduated"
    elif action == "restore":
        if current_state not in {"abandoned", "graduated"}:
            raise GalleryError(
                f"restore requires an abandoned or graduated design; actual={current_state}"
            )
        target_state = "active"
    else:
        raise GalleryError(
            "action must be new-round, abandon, graduate, or restore; "
            f"actual={action!r}"
        )

    source = Path(str(design["pool_path"])).resolve()
    target_root = roots[target_state]
    if target_root is None:
        raise GalleryError(f"lifecycle pool is not configured: {target_state}")
    target = (target_root / design_id).resolve()
    if not source.is_dir():
        raise GalleryError(f"design source folder is missing: {source}")
    if target.exists():
        raise GalleryError(f"design destination already exists: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
    except OSError as exc:
        raise GalleryError(
            f"failed to move design from {source} to {target}: {exc}"
        ) from exc
    try:
        if target_state == "active":
            event = server.store.restore_design(
                design_id=design_id,
                pool_path=target,
                operator_id=operator_id,
                reason=reason or "Restored for another review",
            )
        else:
            event = server.store.transition_design(
                design_id=design_id,
                target_state=target_state,
                pool_path=target,
                operator_id=operator_id,
                reason=reason or f"Moved to {target_state} pool",
                retention_days=server.gallery_config.retention_days,
            )
    except Exception:
        if target.exists() and not source.exists():
            target.replace(source)
        raise
    server.refresh()
    return {"event": event, "dashboard": _operator_payload(server)}


def _rollback_design_moves(moves: list[tuple[Path, Path]]) -> list[str]:
    errors: list[str] = []
    for source, target in reversed(moves):
        try:
            if target.exists() and not source.exists():
                target.replace(source)
        except OSError as exc:
            errors.append(f"{target} -> {source}: {exc}")
    return errors


def _operator_batch_action(
    server: GalleryHTTPServer, payload: dict[str, Any]
) -> dict[str, Any]:
    action = _identity(payload.get("action"), "action")
    if action not in {"abandon", "graduate", "restore"}:
        raise GalleryError(
            "batch action must be abandon, graduate, or restore; "
            f"actual={action!r}"
        )
    raw_design_ids = payload.get("design_ids")
    if not isinstance(raw_design_ids, list) or not raw_design_ids:
        raise GalleryError("design_ids must be a non-empty JSON array")
    if len(raw_design_ids) > MAX_BATCH_ACTIONS:
        raise GalleryError(
            f"design_ids exceeds the {MAX_BATCH_ACTIONS}-item batch limit; "
            f"actual={len(raw_design_ids)}"
        )
    design_ids = [
        _identity(value, f"design_ids[{index}]")
        for index, value in enumerate(raw_design_ids)
    ]
    if len(set(design_ids)) != len(design_ids):
        raise GalleryError("design_ids contains duplicate values")
    operator_id = _identity(payload.get("operator_id"), "operator_id")
    reason = str(payload.get("reason", "")).strip()
    if len(reason) > MAX_COMMENT_LENGTH:
        raise GalleryError(f"reason exceeds {MAX_COMMENT_LENGTH} characters")

    target_state = {
        "abandon": "abandoned",
        "graduate": "graduated",
        "restore": "active",
    }[action]
    target_root = {
        "active": server.gallery_config.root,
        "abandoned": server.gallery_config.abandoned_root,
        "graduated": server.gallery_config.graduation_root,
    }[target_state]
    if target_root is None:
        raise GalleryError(f"lifecycle pool is not configured: {target_state}")

    planned_moves: list[tuple[str, Path, Path]] = []
    for design_id in design_ids:
        design = server.designs.get(design_id)
        if design is None:
            raise GalleryError(f"unknown design_id in batch: {design_id}")
        current_state = str(design["lifecycle_state"])
        allowed_states = (
            {"abandoned", "graduated"} if action == "restore" else {"active"}
        )
        if current_state not in allowed_states:
            raise GalleryError(
                f"{action} requires every design to be in "
                f"{sorted(allowed_states)}; "
                f"design_id={design_id}; actual={current_state}"
            )
        source = Path(str(design["pool_path"])).resolve()
        target = (target_root / design_id).resolve()
        if not source.is_dir():
            raise GalleryError(
                f"batch preflight failed; design source folder is missing: {source}"
            )
        if target.exists():
            raise GalleryError(
                f"batch preflight failed; design destination already exists: {target}"
            )
        planned_moves.append((design_id, source, target))

    completed_moves: list[tuple[Path, Path]] = []
    try:
        for _, source, target in planned_moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            completed_moves.append((source, target))
    except OSError as exc:
        rollback_errors = _rollback_design_moves(completed_moves)
        detail = f"; rollback failures={rollback_errors}" if rollback_errors else ""
        raise GalleryError(f"batch folder move failed: {exc}{detail}") from exc

    try:
        design_ids_and_paths = tuple(
            (design_id, target) for design_id, _, target in planned_moves
        )
        if action == "restore":
            events = server.store.restore_designs(
                design_ids_and_paths=design_ids_and_paths,
                operator_id=operator_id,
                reason=reason or "Batch restored for another review",
            )
        else:
            events = server.store.transition_designs(
                design_ids_and_paths=design_ids_and_paths,
                target_state=target_state,
                operator_id=operator_id,
                reason=reason or f"Batch moved to {target_state} pool",
                retention_days=server.gallery_config.retention_days,
            )
    except Exception as exc:
        rollback_errors = _rollback_design_moves(completed_moves)
        if rollback_errors:
            raise GalleryError(
                f"batch decision persistence failed: {exc}; "
                f"folder rollback failures={rollback_errors}"
            ) from exc
        raise
    server.refresh()
    return {
        "events": events,
        "processed_count": len(events),
        "dashboard": _operator_payload(server),
    }


def _results_payload(
    server: GalleryHTTPServer, *, include_retired: bool = False
) -> dict[str, Any]:
    registry = server.store.registry()
    registry_by_id = {row["design_id"]: row for row in registry}
    allowed = set(registry_by_id) if include_retired else set(server.concept_by_id)
    votes = [
        vote for vote in server.store.all_votes() if vote["design_id"] in allowed
    ]
    totals = {design_id: {choice: 0 for choice in CHOICES} for design_id in allowed}
    for vote in votes:
        if vote["design_id"] in totals:
            totals[vote["design_id"]][vote["choice"]] += 1
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "design_count": len(allowed),
        "vote_count": len(votes),
        "include_retired": include_retired,
        "retired_design_ids": sorted(
            design_id
            for design_id in allowed
            if registry_by_id[design_id]["removed_at"] is not None
        ),
        "totals": totals,
        "votes": votes,
    }


def _query_flag(query: dict[str, list[str]], name: str) -> bool:
    value = query.get(name, [""])[0].strip().casefold()
    if value in {"", "0", "false", "no"}:
        return False
    if value in {"1", "true", "yes"}:
        return True
    raise GalleryError(
        f"query parameter {name} must be 1, 0, true, or false; actual={value!r}"
    )


def _handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server: GalleryHTTPServer

        def log_message(self, format: str, *args: object) -> None:
            super().log_message(format, *args)

        def _cookie_authorized(self, name: str, expected: str | None) -> bool:
            if expected is None:
                return False
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            supplied = cookie.get(name)
            return bool(supplied and hmac.compare_digest(supplied.value, expected))

        def _reviewer_authorized(self) -> bool:
            if (
                self.server.reviewer_auth_token is None
                and self.server.operator_auth_token is None
            ):
                return True
            return self._cookie_authorized(
                "pawmarvel_gallery_reviewer", self.server.reviewer_auth_token
            ) or self._cookie_authorized(
                "pawmarvel_gallery_operator", self.server.operator_auth_token
            )

        def _operator_authorized(self) -> bool:
            if (
                self.server.reviewer_auth_token is None
                and self.server.operator_auth_token is None
            ):
                return True
            return self._cookie_authorized(
                "pawmarvel_gallery_operator", self.server.operator_auth_token
            )

        def _send(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
            *,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "same-origin")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: HTTPStatus, value: Any) -> None:
            self._send(
                status,
                json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _require_reviewer(self) -> bool:
            if self._reviewer_authorized():
                return True
            if self.path.startswith("/api/"):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "access code required"})
            else:
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/login")
                self.end_headers()
            return False

        def _require_operator(self) -> bool:
            if self._operator_authorized():
                return True
            if self.path.startswith("/api/"):
                self._json(HTTPStatus.FORBIDDEN, {"error": "operator access required"})
            else:
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/operator/login")
                self.end_headers()
            return False

        def _read_body(self, max_bytes: int = MAX_BODY_BYTES) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise GalleryError("invalid Content-Length") from exc
            if length <= 0 or length > max_bytes:
                raise GalleryError(
                    f"request body must be between 1 and {max_bytes} bytes"
                )
            return self.rfile.read(length)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/login":
                self._send(HTTPStatus.OK, _login_html(), "text/html; charset=utf-8")
                return
            if parsed.path == "/operator/login":
                self._send(
                    HTTPStatus.OK,
                    _login_html(operator=True),
                    "text/html; charset=utf-8",
                )
                return
            operator_paths = {
                "/operator",
                "/static/operator.js",
                "/static/operator.css",
                "/api/operator/designs",
                "/api/results.json",
                "/api/results.csv",
                "/operator/logout",
            }
            if parsed.path in operator_paths or parsed.path.startswith(
                "/operator-assets/"
            ):
                if not self._require_operator():
                    return
                if parsed.path == "/operator":
                    self._send(
                        HTTPStatus.OK,
                        _static("operator.html"),
                        "text/html; charset=utf-8",
                    )
                    return
                if parsed.path == "/static/operator.js":
                    self._send(
                        HTTPStatus.OK,
                        _static("operator.js"),
                        "text/javascript; charset=utf-8",
                    )
                    return
                if parsed.path == "/static/operator.css":
                    self._send(
                        HTTPStatus.OK,
                        _static("operator.css"),
                        "text/css; charset=utf-8",
                    )
                    return
                if parsed.path == "/api/operator/designs":
                    self._json(HTTPStatus.OK, _operator_payload(self.server))
                    return
                if parsed.path.startswith("/operator-assets/"):
                    parts = parsed.path.strip("/").split("/")
                    if len(parts) != 3 or parts[2] != "reference-design.png":
                        self._json(HTTPStatus.NOT_FOUND, {"error": "asset not found"})
                        return
                    design = self.server.designs.get(parts[1])
                    if design is None:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "unknown design_id"})
                        return
                    image = Path(str(design["pool_path"])) / "reference-design.png"
                    self._send(
                        HTTPStatus.OK,
                        image.read_bytes(),
                        mimetypes.guess_type(image.name)[0] or "application/octet-stream",
                    )
                    return
                if parsed.path == "/operator/logout":
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/operator/login")
                    self.send_header(
                        "Set-Cookie",
                        "pawmarvel_gallery_operator=; Path=/; Max-Age=0; "
                        "HttpOnly; SameSite=Strict",
                    )
                    self.end_headers()
                    return
            if not self._require_reviewer():
                return
            if parsed.path == "/":
                self._send(HTTPStatus.OK, _static("gallery.html"), "text/html; charset=utf-8")
                return
            if parsed.path == "/static/gallery.css":
                self._send(HTTPStatus.OK, _static("gallery.css"), "text/css; charset=utf-8")
                return
            if parsed.path == "/static/gallery.js":
                self._send(HTTPStatus.OK, _static("gallery.js"), "text/javascript; charset=utf-8")
                return
            if parsed.path == "/api/gallery":
                query = parse_qs(parsed.query)
                reviewer = _identity(query.get("reviewer", [""])[0], "reviewer", optional=True)
                own_votes = self.server.store.votes_for(reviewer) if reviewer else {}
                self._json(
                    HTTPStatus.OK,
                    {
                        "concepts": self.server.concepts,
                        "choices": CHOICES,
                        "current_votes": own_votes,
                    },
                )
                return
            if parsed.path == "/api/results.json":
                try:
                    include_retired = _query_flag(
                        parse_qs(parsed.query), "include_retired"
                    )
                except GalleryError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self._json(
                    HTTPStatus.OK,
                    _results_payload(self.server, include_retired=include_retired),
                )
                return
            if parsed.path == "/api/results.csv":
                try:
                    include_retired = _query_flag(
                        parse_qs(parsed.query), "include_retired"
                    )
                except GalleryError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                output = io.StringIO(newline="")
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "design_id", "choice", "comment", "reviewer_name",
                        "reviewer_id", "updated_at",
                    ),
                )
                writer.writeheader()
                allowed = (
                    {row["design_id"] for row in self.server.store.registry()}
                    if include_retired
                    else set(self.server.concept_by_id)
                )
                writer.writerows(
                    vote
                    for vote in self.server.store.all_votes()
                    if vote["design_id"] in allowed
                )
                self._send(
                    HTTPStatus.OK,
                    output.getvalue().encode("utf-8"),
                    "text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=gallery-votes.csv"},
                )
                return
            if parsed.path.startswith("/assets/"):
                parts = parsed.path.strip("/").split("/")
                if len(parts) != 3 or parts[2] != "reference-design.png":
                    self._json(HTTPStatus.NOT_FOUND, {"error": "asset not found"})
                    return
                design_id = parts[1]
                concept = self.server.concept_by_id.get(design_id)
                if concept is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "unknown design_id"})
                    return
                image = self.server.gallery_config.root / design_id / "reference-design.png"
                body = image.read_bytes()
                media_type = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
                self._send(HTTPStatus.OK, body, media_type)
                return
            if parsed.path.startswith("/review-assets/"):
                asset = self.server.review_asset_by_url.get(parsed.path)
                if asset is None or not asset.is_file():
                    self._json(HTTPStatus.NOT_FOUND, {"error": "review asset not found"})
                    return
                media_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
                if asset.suffix == ".json":
                    media_type = "application/json; charset=utf-8"
                self._send(HTTPStatus.OK, asset.read_bytes(), media_type)
                return
            if parsed.path == "/logout":
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/login")
                self.send_header(
                    "Set-Cookie",
                    "pawmarvel_gallery_reviewer=; Path=/; Max-Age=0; "
                    "HttpOnly; SameSite=Strict",
                )
                self.end_headers()
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/login", "/operator/login"}:
                operator = parsed.path == "/operator/login"
                try:
                    values = parse_qs(self._read_body().decode("utf-8"))
                except (GalleryError, UnicodeDecodeError) as exc:
                    self._send(
                        HTTPStatus.BAD_REQUEST,
                        _login_html(str(exc), operator=operator),
                        "text/html; charset=utf-8",
                    )
                    return
                access_code = values.get("access_code", [""])[0]
                configured = (
                    self.server.gallery_config.operator_access_code
                    if operator
                    else self.server.gallery_config.reviewer_access_code
                )
                if configured is None or not hmac.compare_digest(access_code, configured):
                    self._send(
                        HTTPStatus.UNAUTHORIZED,
                        _login_html("Incorrect access code.", operator=operator),
                        "text/html; charset=utf-8",
                    )
                    return
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/operator" if operator else "/")
                cookie_name = (
                    "pawmarvel_gallery_operator"
                    if operator
                    else "pawmarvel_gallery_reviewer"
                )
                token = (
                    self.server.operator_auth_token
                    if operator
                    else self.server.reviewer_auth_token
                )
                self.send_header(
                    "Set-Cookie",
                    f"{cookie_name}={token}; Path=/; HttpOnly; SameSite=Strict",
                )
                self.end_headers()
                return
            if parsed.path in {"/api/operator/action", "/api/operator/actions"}:
                if not self._require_operator():
                    return
                try:
                    payload = json.loads(
                        self._read_body(
                            MAX_BATCH_BODY_BYTES
                            if parsed.path == "/api/operator/actions"
                            else MAX_BODY_BYTES
                        )
                    )
                    if not isinstance(payload, dict):
                        raise GalleryError("operator action payload must be a JSON object")
                    with self.server.lifecycle_lock:
                        result = (
                            _operator_batch_action(self.server, payload)
                            if parsed.path == "/api/operator/actions"
                            else _operator_action(self.server, payload)
                        )
                except (GalleryError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except Exception as exc:
                    self._json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": f"operator action failed: {exc}"},
                    )
                    return
                self._json(HTTPStatus.OK, result)
                return
            if not self._require_reviewer():
                return
            if parsed.path not in {"/api/vote", "/api/votes"}:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                payload = json.loads(
                    self._read_body(
                        MAX_BATCH_BODY_BYTES
                        if parsed.path == "/api/votes"
                        else MAX_BODY_BYTES
                    )
                )
                if not isinstance(payload, dict):
                    raise GalleryError("vote payload must be a JSON object")
                reviewer_id = _identity(payload.get("reviewer_id"), "reviewer_id")
                reviewer_name = _identity(payload.get("reviewer_name"), "reviewer_name")
                raw_votes = (
                    payload.get("votes")
                    if parsed.path == "/api/votes"
                    else [payload]
                )
                if not isinstance(raw_votes, list) or not raw_votes:
                    raise GalleryError("votes must be a non-empty array")
                if len(raw_votes) > MAX_BATCH_VOTES:
                    raise GalleryError(
                        f"votes exceeds the {MAX_BATCH_VOTES}-item batch limit; "
                        f"actual={len(raw_votes)}"
                    )
                votes: list[dict[str, str]] = []
                design_ids: set[str] = set()
                for index, raw_vote in enumerate(raw_votes, start=1):
                    if not isinstance(raw_vote, dict):
                        raise GalleryError(f"vote {index} must be a JSON object")
                    design_id = _identity(raw_vote.get("design_id"), "design_id")
                    choice = _identity(raw_vote.get("choice"), "choice")
                    comment = str(raw_vote.get("comment", "")).strip()
                    if design_id in design_ids:
                        raise GalleryError(
                            f"duplicate design_id in vote batch: {design_id}"
                        )
                    design_ids.add(design_id)
                    if design_id not in self.server.concept_by_id:
                        raise GalleryError(
                            f"vote {index} has unknown design_id: {design_id}"
                        )
                    if choice not in CHOICES:
                        raise GalleryError(
                            f"vote {index} choice must be one of "
                            f"{', '.join(CHOICES)}; actual={choice!r}"
                        )
                    if len(comment) > MAX_COMMENT_LENGTH:
                        raise GalleryError(
                            f"vote {index} comment exceeds "
                            f"{MAX_COMMENT_LENGTH} characters"
                        )
                    votes.append(
                        {
                            "design_id": design_id,
                            "choice": choice,
                            "comment": comment,
                        }
                    )
                with self.server.lifecycle_lock:
                    saved_votes = self.server.store.save_votes(
                        reviewer_id=reviewer_id.casefold(),
                        reviewer_name=reviewer_name,
                        votes=votes,
                    )
            except (GalleryError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if parsed.path == "/api/vote":
                self._json(
                    HTTPStatus.OK,
                    {"saved": True, "vote": saved_votes[0]},
                )
            else:
                self._json(
                    HTTPStatus.OK,
                    {
                        "saved": True,
                        "saved_count": len(saved_votes),
                        "votes": saved_votes,
                    },
                )

    return Handler


def _identity(value: object, label: str, *, optional: bool = False) -> str:
    if not isinstance(value, str):
        if optional and value is None:
            return ""
        raise GalleryError(f"{label} must be text")
    result = value.strip()
    if not result and not optional:
        raise GalleryError(f"{label} is required")
    if len(result) > MAX_IDENTITY_LENGTH:
        raise GalleryError(f"{label} exceeds {MAX_IDENTITY_LENGTH} characters")
    return result.casefold() if label in {"reviewer", "reviewer_id"} else result


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_design_id(value: str) -> str:
    if not value or Path(value).name != value or value in {".", ".."}:
        raise GalleryError(f"design_id is not a safe path segment: {value!r}")
    return value


def create_gallery_server(
    config: GalleryConfig,
    *,
    bind: str = "127.0.0.1",
    port: int = 0,
) -> GalleryHTTPServer:
    resolved = GalleryConfig(
        root=config.root.resolve(),
        index=config.index.resolve(),
        database=config.database.resolve(),
        authoring_root=(
            config.authoring_root.resolve() if config.authoring_root is not None else None
        ),
        abandoned_root=(
            config.abandoned_root.resolve()
            if config.abandoned_root is not None
            else (config.root.parent / "Abandoned Design Pool").resolve()
        ),
        graduation_root=(
            config.graduation_root.resolve()
            if config.graduation_root is not None
            else (config.root.parent / "Graduation Pool").resolve()
        ),
        collections=config.collections,
        reviewer_access_code=config.reviewer_access_code,
        operator_access_code=config.operator_access_code,
        retention_days=config.retention_days,
        decisions_dir=(
            config.decisions_dir.resolve() if config.decisions_dir is not None else None
        ),
    )
    assert resolved.abandoned_root is not None
    assert resolved.graduation_root is not None
    resolved.abandoned_root.mkdir(parents=True, exist_ok=True)
    resolved.graduation_root.mkdir(parents=True, exist_ok=True)
    snapshot = _load_concepts(resolved)
    store = GalleryStore(resolved.database, resolved.decisions_dir)
    reconciliation = store.reconcile(
        snapshot.active_designs, retention_days=resolved.retention_days
    )
    store.sync_catalog(
        snapshot.all_designs, retention_days=resolved.retention_days
    )
    return GalleryHTTPServer(
        (bind, port),
        _handler(),
        config=resolved,
        concepts=snapshot.concepts,
        designs=snapshot.all_designs,
        store=store,
        reconciliation=reconciliation,
        missing_design_ids=snapshot.missing_design_ids,
    )
