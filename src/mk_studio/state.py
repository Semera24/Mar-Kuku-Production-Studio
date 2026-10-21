"""SQLite state store with short transactions, step receipts, and restart reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from mk_studio.config import compute_sha256

MIGRATION_001_PATH = Path(__file__).parent / "migrations" / "001.sql"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_idempotency_key(step_name: str, step_kind: str, input_hash: str) -> str:
    raw = f"{step_name}|{step_kind}|{input_hash}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class StateError(RuntimeError):
    """Base error for durable state store failures."""


class BlockedRunError(StateError):
    """Raised when a step or run is blocked due to an unknown external/editor outcome."""


class UncleanStepError(StateError):
    """Raised when a running step exists that must be reconciled before execution."""


@dataclass(frozen=True)
class StepTicket:
    """Handle returned by begin_step."""

    event_id: str
    run_id: str
    step_name: str
    step_kind: str
    idempotency_key: str
    input_hash: str
    expected_output_path: str | None = None
    already_completed: bool = False
    cached_output_hash: str | None = None
    cached_external_target_id: str | None = None
    cached_receipt: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconciliationSummary:
    """Summary of reconcile_run actions."""

    reconciled_events: list[str]
    blocked_events: list[str]
    failed_events: list[str]
    invalidated_events: list[str]


class StateStore:
    """SQLite state store using short per-operation transactions (never held across I/O)."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve(strict=False)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _short_tx(self) -> Iterator[sqlite3.Connection]:
        """Open a short transaction with WAL + busy_timeout and close immediately after commit."""
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        sql = MIGRATION_001_PATH.read_text(encoding="utf-8")
        with self._short_tx() as conn:
            conn.executescript(sql)

    def list_tables(self) -> list[str]:
        with self._short_tx() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            return [str(r["name"]) for r in rows]

    def upsert_episode(
        self,
        *,
        episode_id: str,
        title: str,
        production_root: str,
        status: str = "in_progress",
    ) -> None:
        now = utc_now_iso()
        with self._short_tx() as conn:
            conn.execute(
                """
                INSERT INTO episodes (episode_id, title, status, production_root, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(episode_id) DO UPDATE SET
                    title = excluded.title,
                    status = excluded.status,
                    production_root = excluded.production_root,
                    updated_at = excluded.updated_at
                """,
                (episode_id, title, status, production_root, now, now),
            )

    def upsert_chapter(
        self,
        *,
        episode_id: str,
        chapter_id: str,
        title: str,
        audio_duration_seconds: float | None = None,
        audio_total_samples: int | None = None,
        total_frames: int | None = None,
        status: str = "in_progress",
    ) -> str:
        chapter_key = f"{episode_id}:{chapter_id}"
        now = utc_now_iso()
        with self._short_tx() as conn:
            conn.execute(
                """
                INSERT INTO chapters (
                    chapter_key, episode_id, chapter_id, title,
                    audio_duration_seconds, audio_total_samples, total_frames,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chapter_key) DO UPDATE SET
                    title = excluded.title,
                    audio_duration_seconds = COALESCE(excluded.audio_duration_seconds, chapters.audio_duration_seconds),
                    audio_total_samples = COALESCE(excluded.audio_total_samples, chapters.audio_total_samples),
                    total_frames = COALESCE(excluded.total_frames, chapters.total_frames),
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    chapter_key,
                    episode_id,
                    chapter_id,
                    title,
                    audio_duration_seconds,
                    audio_total_samples,
                    total_frames,
                    status,
                    now,
                    now,
                ),
            )
        return chapter_key

    def record_approval(
        self,
        *,
        episode_id: str,
        chapter_id: str | None,
        scope: str,
        target_path: str,
        target_sha256: str,
        status: str,
        approved_by: str,
        evidence_path: str | None = None,
        notes: str = "",
    ) -> str:
        approval_id = hashlib.sha256(
            f"{episode_id}:{chapter_id}:{scope}:{target_path}:{target_sha256}".encode("utf-8")
        ).hexdigest()[:32]
        now = utc_now_iso()
        with self._short_tx() as conn:
            conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, episode_id, chapter_id, scope,
                    target_path, target_sha256, status, approved_by,
                    evidence_path, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(approval_id) DO UPDATE SET
                    status = excluded.status,
                    approved_by = excluded.approved_by,
                    evidence_path = excluded.evidence_path,
                    notes = excluded.notes
                """,
                (
                    approval_id,
                    episode_id,
                    chapter_id,
                    scope,
                    target_path,
                    target_sha256,
                    status,
                    approved_by,
                    evidence_path,
                    notes,
                    now,
                ),
            )
        return approval_id

    def record_artifact(
        self,
        *,
        episode_id: str,
        chapter_id: str | None,
        role: str,
        path: str,
        sha256: str,
        bytes_size: int,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        artifact_id = hashlib.sha256(
            f"{episode_id}:{chapter_id}:{role}:{path}:{sha256}".encode("utf-8")
        ).hexdigest()[:32]
        now = utc_now_iso()
        with self._short_tx() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, episode_id, chapter_id, role,
                    path, sha256, bytes_size, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    sha256 = excluded.sha256,
                    bytes_size = excluded.bytes_size,
                    metadata_json = excluded.metadata_json
                """,
                (
                    artifact_id,
                    episode_id,
                    chapter_id,
                    role,
                    path,
                    sha256,
                    bytes_size,
                    json.dumps(metadata or {}, sort_keys=True),
                    now,
                ),
            )
        return artifact_id

    def record_file_operation(
        self,
        *,
        run_id: str,
        step_name: str,
        op_type: str,
        source_path: str | None = None,
        source_sha256: str | None = None,
        dest_path: str | None = None,
        dest_sha256: str | None = None,
        bytes_size: int = 0,
        status: str = "succeeded",
    ) -> str:
        op_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._short_tx() as conn:
            conn.execute(
                """
                INSERT INTO file_operations (
                    op_id, run_id, step_name, op_type,
                    source_path, source_sha256, dest_path, dest_sha256,
                    bytes_size, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    op_id,
                    run_id,
                    step_name,
                    op_type,
                    source_path,
                    source_sha256,
                    dest_path,
                    dest_sha256,
                    bytes_size,
                    status,
                    now,
                ),
            )
        return op_id

    def create_run(
        self,
        *,
        episode_id: str,
        chapter_id: str | None,
        workflow_name: str,
        input_hash: str,
        run_id: str | None = None,
    ) -> str:
        rid = run_id or uuid.uuid4().hex
        now = utc_now_iso()
        with self._short_tx() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, episode_id, chapter_id, workflow_name,
                    input_hash, status, pid, started_at
                )
                VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    input_hash = excluded.input_hash,
                    status = 'running',
                    pid = excluded.pid,
                    error_message = NULL,
                    finished_at = NULL
                """,
                (rid, episode_id, chapter_id, workflow_name, input_hash, os.getpid(), now),
            )
        return rid

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        error_message: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with self._short_tx() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?, error_message = COALESCE(?, error_message), finished_at = ?
                WHERE run_id = ?
                """,
                (status, error_message, now, run_id),
            )

    def finish_run(
        self,
        run_id: str,
        status: str = "succeeded",
        *,
        error_message: str | None = None,
    ) -> None:
        self.update_run_status(run_id, status, error_message=error_message)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._short_tx() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def get_step_events(self, step_name: str | None = None) -> list[dict[str, Any]]:
        with self._short_tx() as conn:
            if step_name:
                rows = conn.execute(
                    "SELECT * FROM run_events WHERE step_name = ? ORDER BY started_at ASC",
                    (step_name,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM run_events ORDER BY started_at ASC").fetchall()
            return [dict(r) for r in rows]

    def begin_step(
        self,
        *,
        run_id: str,
        step_name: str,
        step_kind: str,
        input_hash: str,
        expected_output_path: str | Path | None = None,
    ) -> StepTicket:
        """Start or short-circuit a step using a short SQLite transaction."""
        idem_key = compute_idempotency_key(step_name, step_kind, input_hash)
        exp_path_str = str(Path(expected_output_path).resolve(strict=False)) if expected_output_path else None
        now = utc_now_iso()

        with self._short_tx() as conn:
            prior_rows = conn.execute(
                "SELECT * FROM run_events WHERE step_name = ? ORDER BY started_at DESC",
                (step_name,),
            ).fetchall()

            for row in prior_rows:
                if row["status"] == "blocked":
                    raise BlockedRunError(
                        f"Step '{step_name}' is blocked due to unknown external outcome: {row['error_message']}"
                    )
                if row["status"] == "running":
                    raise UncleanStepError(
                        f"Step '{step_name}' has an interrupted running event ({row['event_id']}); call reconcile_run() first."
                    )

            # Invalidate prior succeeded/reconciled receipts if input_hash changed
            conn.execute(
                """
                UPDATE run_events
                SET status = 'invalidated'
                WHERE step_name = ?
                  AND status IN ('succeeded', 'reconciled')
                  AND input_hash != ?
                """,
                (step_name, input_hash),
            )

            # Check for matching valid receipt
            matching = conn.execute(
                """
                SELECT * FROM run_events
                WHERE idempotency_key = ?
                  AND status IN ('succeeded', 'reconciled')
                ORDER BY finished_at DESC
                LIMIT 1
                """,
                (idem_key,),
            ).fetchone()

            if matching is not None:
                out_path = matching["expected_output_path"]
                out_hash = matching["output_hash"]
                output_still_valid = True
                if out_path:
                    p = Path(out_path)
                    if not p.exists():
                        output_still_valid = False
                    elif out_hash and compute_sha256(p).lower() != str(out_hash).lower():
                        output_still_valid = False

                if output_still_valid:
                    return StepTicket(
                        event_id=str(matching["event_id"]),
                        run_id=run_id,
                        step_name=step_name,
                        step_kind=step_kind,
                        idempotency_key=idem_key,
                        input_hash=input_hash,
                        expected_output_path=exp_path_str,
                        already_completed=True,
                        cached_output_hash=matching["output_hash"],
                        cached_external_target_id=matching["external_target_id"],
                        cached_receipt=json.loads(matching["receipt_json"] or "{}"),
                    )
                else:
                    conn.execute(
                        "UPDATE run_events SET status = 'invalidated' WHERE event_id = ?",
                        (matching["event_id"],),
                    )

            event_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO run_events (
                    event_id, run_id, step_name, step_kind,
                    idempotency_key, input_hash, status,
                    expected_output_path, started_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    event_id,
                    run_id,
                    step_name,
                    step_kind,
                    idem_key,
                    input_hash,
                    exp_path_str,
                    now,
                ),
            )

        return StepTicket(
            event_id=event_id,
            run_id=run_id,
            step_name=step_name,
            step_kind=step_kind,
            idempotency_key=idem_key,
            input_hash=input_hash,
            expected_output_path=exp_path_str,
            already_completed=False,
        )

    def finish_step(
        self,
        ticket: StepTicket,
        *,
        status: str = "succeeded",
        output_hash: str | None = None,
        external_target_id: str | None = None,
        receipt: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        """Complete a step in a short SQLite transaction."""
        if ticket.already_completed:
            return
        now = utc_now_iso()
        computed_hash = output_hash
        if status == "succeeded" and computed_hash is None and ticket.expected_output_path:
            out_file = Path(ticket.expected_output_path)
            if out_file.is_file():
                computed_hash = compute_sha256(out_file)

        with self._short_tx() as conn:
            conn.execute(
                """
                UPDATE run_events
                SET status = ?,
                    output_hash = ?,
                    external_target_id = ?,
                    receipt_json = ?,
                    error_message = ?,
                    finished_at = ?
                WHERE event_id = ?
                """,
                (
                    status,
                    computed_hash,
                    external_target_id,
                    json.dumps(receipt or {}, sort_keys=True),
                    error_message,
                    now,
                    ticket.event_id,
                ),
            )

    def reconcile_run(
        self,
        run_id: str | None = None,
        *,
        output_validator: Callable[[Path], bool] | None = None,
        editor_inspector: Callable[[dict[str, Any]], tuple[bool, str | None, dict[str, Any]]] | None = None,
    ) -> ReconciliationSummary:
        """Reconcile any interrupted 'running' step events before retrying or resuming."""
        reconciled: list[str] = []
        blocked: list[str] = []
        failed: list[str] = []
        invalidated: list[str] = []

        with self._short_tx() as conn:
            if run_id:
                running_rows = conn.execute(
                    "SELECT * FROM run_events WHERE run_id = ? AND status = 'running' ORDER BY started_at ASC",
                    (run_id,),
                ).fetchall()
            else:
                running_rows = conn.execute(
                    "SELECT * FROM run_events WHERE status = 'running' ORDER BY started_at ASC"
                ).fetchall()
            events = [dict(r) for r in running_rows]

        for ev in events:
            ev_id = str(ev["event_id"])
            ev_run_id = str(ev["run_id"])
            step_name = str(ev["step_name"])
            step_kind = str(ev["step_kind"])
            exp_path_str = ev.get("expected_output_path")
            now = utc_now_iso()

            if step_kind == "local_file" and exp_path_str:
                exp_path = Path(exp_path_str)
                is_valid = exp_path.is_file() and exp_path.stat().st_size > 0
                if is_valid and output_validator is not None:
                    try:
                        is_valid = bool(output_validator(exp_path))
                    except Exception:
                        is_valid = False

                if is_valid:
                    digest = compute_sha256(exp_path)
                    size = exp_path.stat().st_size
                    with self._short_tx() as conn:
                        conn.execute(
                            """
                            UPDATE run_events
                            SET status = 'reconciled',
                                output_hash = ?,
                                receipt_json = ?,
                                finished_at = ?
                            WHERE event_id = ?
                            """,
                            (
                                digest,
                                json.dumps(
                                    {"reconciled_output": str(exp_path), "sha256": digest, "bytes_size": size},
                                    sort_keys=True,
                                ),
                                now,
                                ev_id,
                            ),
                        )
                        conn.execute(
                            """
                            INSERT INTO file_operations (
                                op_id, run_id, step_name, op_type,
                                dest_path, dest_sha256, bytes_size, status, created_at
                            )
                            VALUES (?, ?, ?, 'reconcile_adopt', ?, ?, ?, 'reconciled', ?)
                            """,
                            (uuid.uuid4().hex, ev_run_id, step_name, str(exp_path), digest, size, now),
                        )
                        conn.execute(
                            "UPDATE runs SET status = 'reconciled', finished_at = ? WHERE run_id = ? AND status = 'running'",
                            (now, ev_run_id),
                        )
                    reconciled.append(ev_id)
                else:
                    with self._short_tx() as conn:
                        conn.execute(
                            """
                            UPDATE run_events
                            SET status = 'failed',
                                error_message = 'Interrupted before valid local output completed',
                                finished_at = ?
                            WHERE event_id = ?
                            """,
                            (now, ev_id),
                        )
                        conn.execute(
                            "UPDATE runs SET status = 'failed', error_message = 'Interrupted step had no valid output', finished_at = ? WHERE run_id = ?",
                            (now, ev_run_id),
                        )
                    failed.append(ev_id)

            elif step_kind == "editor_mutation":
                verified = False
                ext_id: str | None = None
                receipt_data: dict[str, Any] = {}
                if editor_inspector is not None:
                    try:
                        verified, ext_id, receipt_data = editor_inspector(ev)
                    except Exception as exc:
                        verified = False
                        receipt_data = {"inspector_error": str(exc)}

                if verified:
                    with self._short_tx() as conn:
                        conn.execute(
                            """
                            UPDATE run_events
                            SET status = 'reconciled',
                                external_target_id = ?,
                                receipt_json = ?,
                                finished_at = ?
                            WHERE event_id = ?
                            """,
                            (ext_id, json.dumps(receipt_data, sort_keys=True), now, ev_id),
                        )
                        conn.execute(
                            "UPDATE runs SET status = 'reconciled', finished_at = ? WHERE run_id = ? AND status = 'running'",
                            (now, ev_run_id),
                        )
                    reconciled.append(ev_id)
                else:
                    msg = (
                        f"UNKNOWN_EDITOR_OUTCOME: step '{step_name}' interrupted during editor mutation and state "
                        "could not be deterministically verified; blocking automatic retry."
                    )
                    with self._short_tx() as conn:
                        conn.execute(
                            """
                            UPDATE run_events
                            SET status = 'blocked',
                                error_message = ?,
                                receipt_json = ?,
                                finished_at = ?
                            WHERE event_id = ?
                            """,
                            (msg, json.dumps(receipt_data, sort_keys=True), now, ev_id),
                        )
                        conn.execute(
                            "UPDATE runs SET status = 'blocked', error_message = ?, finished_at = ? WHERE run_id = ?",
                            (msg, now, ev_run_id),
                        )
                    blocked.append(ev_id)
            else:
                with self._short_tx() as conn:
                    conn.execute(
                        """
                        UPDATE run_events
                        SET status = 'failed',
                            error_message = 'Interrupted compute/verification step',
                            finished_at = ?
                        WHERE event_id = ?
                        """,
                        (now, ev_id),
                    )
                failed.append(ev_id)

        return ReconciliationSummary(
            reconciled_events=reconciled,
            blocked_events=blocked,
            failed_events=failed,
            invalidated_events=invalidated,
        )
