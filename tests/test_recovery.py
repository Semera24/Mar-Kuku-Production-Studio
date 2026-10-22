"""Tests for SQLite durability, short transactions, reconciliation, locks, and cancellation."""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
import pytest

from mk_studio.runner import (
    ConcurrentRunError,
    StepRunner,
    SubprocessCancelledError,
    run_cancellable_subprocess,
)
from mk_studio.state import (
    BlockedRunError,
    StateStore,
    StepTicket,
    UncleanStepError,
)


def test_seven_tables_created(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    tables = store.list_tables()
    assert tables == [
        "approvals",
        "artifacts",
        "chapters",
        "episodes",
        "file_operations",
        "run_events",
        "runs",
    ]


def test_idempotent_step_and_changed_input_invalidation(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    runner = StepRunner(store, tmp_path / "locks")
    run_id = store.create_run(
        episode_id="EPISODE_002",
        chapter_id="CH01",
        workflow_name="test_workflow",
        input_hash="hash_v1",
    )

    out_file = tmp_path / "derived.wav"
    call_count = 0

    def produce_file(ticket: StepTicket) -> dict[str, str]:
        nonlocal call_count
        call_count += 1
        out_file.write_bytes(f"audio-payload-{call_count}".encode("utf-8"))
        return {"produced_by": ticket.event_id}

    t1, r1 = runner.execute_step(
        run_id=run_id,
        step_name="conform_audio",
        step_kind="local_file",
        input_hash="input_v1",
        expected_output_path=out_file,
        action=produce_file,
    )
    assert not t1.already_completed
    assert call_count == 1

    # Rerun with identical input_hash -> must skip execution
    t2, r2 = runner.execute_step(
        run_id=run_id,
        step_name="conform_audio",
        step_kind="local_file",
        input_hash="input_v1",
        expected_output_path=out_file,
        action=produce_file,
    )
    assert t2.already_completed
    assert call_count == 1
    assert r2 == r1

    # Rerun with changed input_hash -> must invalidate v1 receipt and re-execute
    t3, _ = runner.execute_step(
        run_id=run_id,
        step_name="conform_audio",
        step_kind="local_file",
        input_hash="input_v2",
        expected_output_path=out_file,
        action=produce_file,
    )
    assert not t3.already_completed
    assert call_count == 2

    events = store.get_step_events("conform_audio")
    statuses = [e["status"] for e in events]
    assert statuses == ["invalidated", "succeeded"]


def test_interruption_after_external_output_before_receipt_commit(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    runner = StepRunner(store, tmp_path / "locks")
    run_id = store.create_run(
        episode_id="EPISODE_002",
        chapter_id="CH01",
        workflow_name="crash_test",
        input_hash="hash_v1",
    )
    out_file = tmp_path / "export.mp4"

    # Simulate step starting and writing output, then crashing before finish_step
    ticket = store.begin_step(
        run_id=run_id,
        step_name="export_mp4",
        step_kind="local_file",
        input_hash="export_hash_1",
        expected_output_path=out_file,
    )
    assert not ticket.already_completed
    out_file.write_bytes(b"valid-mp4-stream-bytes")

    # Attempting to begin_step without reconciliation raises UncleanStepError
    with pytest.raises(UncleanStepError):
        store.begin_step(
            run_id=run_id,
            step_name="export_mp4",
            step_kind="local_file",
            input_hash="export_hash_1",
            expected_output_path=out_file,
        )

    # Reconcile adopts the valid output without re-running external work
    summary = store.reconcile_run(
        run_id,
        output_validator=lambda p: p.read_bytes().startswith(b"valid-mp4"),
    )
    assert summary.reconciled_events == [ticket.event_id]
    assert summary.blocked_events == []

    # Subsequent execute_step sees the reconciled receipt and does not re-run
    called = False

    def should_not_run(_: StepTicket) -> dict[str, str]:
        nonlocal called
        called = True
        return {}

    t_after, receipt = runner.execute_step(
        run_id=run_id,
        step_name="export_mp4",
        step_kind="local_file",
        input_hash="export_hash_1",
        expected_output_path=out_file,
        action=should_not_run,
    )
    assert t_after.already_completed
    assert not called
    assert receipt["reconciled_output"] == str(out_file.resolve())


def test_unknown_editor_outcome_blocks_retry(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    runner = StepRunner(store, tmp_path / "locks")
    run_id = store.create_run(
        episode_id="EPISODE_002",
        chapter_id="CH01",
        workflow_name="editor_crash",
        input_hash="hash_v1",
    )

    ticket = store.begin_step(
        run_id=run_id,
        step_name="palmier_add_clips",
        step_kind="editor_mutation",
        input_hash="clips_hash_1",
    )

    # Reconcile when editor state cannot be verified -> must transition to blocked
    summary = store.reconcile_run(
        run_id,
        editor_inspector=lambda _: (False, None, {"reason": "MCP connection dropped mid-mutation"}),
    )
    assert summary.blocked_events == [ticket.event_id]

    run_row = store.get_run(run_id)
    assert run_row is not None
    assert run_row["status"] == "blocked"

    # Retrying a blocked step must raise BlockedRunError instead of duplicating clips
    with pytest.raises(BlockedRunError, match="UNKNOWN_EDITOR_OUTCOME"):
        runner.execute_step(
            run_id=run_id,
            step_name="palmier_add_clips",
            step_kind="editor_mutation",
            input_hash="clips_hash_1",
            action=lambda _: {},
        )


def test_concurrent_run_and_editor_locks_rejected(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    runner1 = StepRunner(store, tmp_path / "locks")
    runner2 = StepRunner(store, tmp_path / "locks")

    with runner1.acquire_run_lock():
        with pytest.raises(ConcurrentRunError, match="Cannot acquire run lock"):
            with runner2.acquire_run_lock():
                pass

    with runner1.acquire_editor_writer_lock():
        with pytest.raises(ConcurrentRunError, match="Cannot acquire editor-writer lock"):
            with runner2.acquire_editor_writer_lock():
                pass


def test_second_db_connection_commits_while_external_step_is_stalled(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store1 = StateStore(db_path)
    store2 = StateStore(db_path)
    runner = StepRunner(store1, tmp_path / "locks")

    run_id = store1.create_run(
        episode_id="EPISODE_002",
        chapter_id="CH01",
        workflow_name="concurrent_db_test",
        input_hash="h1",
    )

    second_conn_committed = False

    def stalled_external_step(_: StepTicket) -> dict[str, str]:
        nonlocal second_conn_committed
        # While this external step is stalled in flight, a second connection writes and commits
        store2.upsert_episode(
            episode_id="EPISODE_002",
            title="Mar and Kuku Color Garden Adventure",
            production_root="/Users/yonassayfu/Desktop/DoroNesh_2D_Animation",
        )
        raw_conn = sqlite3.connect(str(db_path), timeout=0.2)
        try:
            with raw_conn:
                raw_conn.execute(
                    "UPDATE episodes SET status = 'verified_concurrent_commit' WHERE episode_id = 'EPISODE_002'"
                )
            second_conn_committed = True
        finally:
            raw_conn.close()
        return {"status": "ok"}

    ticket, receipt = runner.execute_step(
        run_id=run_id,
        step_name="slow_ffmpeg_render",
        step_kind="compute",
        input_hash="render_1",
        action=stalled_external_step,
    )
    assert second_conn_committed
    assert not ticket.already_completed
    assert receipt["status"] == "ok"


def test_subprocess_group_cancellation(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    cancel_event = threading.Event()

    # Spawn a python parent that spawns a child sleep process and writes its PID
    script = (
        "import subprocess, sys, time, pathlib; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )

    def trigger_cancel() -> None:
        deadline = time.monotonic() + 3.0
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        cancel_event.set()

    canceller = threading.Thread(target=trigger_cancel, daemon=True)
    canceller.start()

    with pytest.raises(SubprocessCancelledError):
        run_cancellable_subprocess(
            [sys.executable, "-c", script],
            cancel_event=cancel_event,
            timeout_seconds=5.0,
        )

    canceller.join(timeout=1.0)
    assert pid_file.exists()
    grandchild_pid = int(pid_file.read_text().strip())

    # Verify the grandchild in the process group was also terminated
    time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild_pid, 0)

