"""Regression tests for M0 review findings R1 through R8."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from mk_studio import cli
from mk_studio.config import RootPolicy
from mk_studio.editors.palmier import (
    PalmierAdapter,
    PalmierEditorError,
    load_palmier_tool_schemas,
)
from mk_studio.registration import calibrate_pose_png
from mk_studio.runner import (
    ConcurrentRunError,
    StepRunner,
    run_cancellable_subprocess,
)
from mk_studio.state import BlockedRunError, StateStore
from test_editor_reconciliation import (
    FakePalmierMcpServer,
    _build_synthetic_ch01_plan,
)


def test_r1_reentrant_editor_writer_lock_and_second_runner_contention(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "r1.db")
    runner1 = StepRunner(store, tmp_path / "locks")
    runner2 = StepRunner(store, tmp_path / "locks")
    run_id = store.create_run(
        episode_id="EPISODE_002",
        chapter_id="CH01",
        workflow_name="test-r1",
        input_hash="h1",
    )

    called: list[str] = []
    with runner1.acquire_editor_writer_lock():
        # Same StepRunner re-entering editor_writer_lock via execute_step must succeed
        runner1.execute_step(
            run_id=run_id,
            step_name="nested-mutation",
            step_kind="editor_mutation",
            input_hash="h1",
            action=lambda _: called.append("ok") or {},
        )
        # Second StepRunner contending for the lock must still raise ConcurrentRunError
        with pytest.raises(ConcurrentRunError):
            with runner2.acquire_editor_writer_lock():
                pass

    assert called == ["ok"]


def test_r2_palmier_adapter_tool_calls_conform_to_live_schemas(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    package = scratch / "test.palmier"
    package.mkdir()

    policy = RootPolicy(output_root=scratch)
    fake = FakePalmierMcpServer(package)
    adapter = PalmierAdapter(policy, tool_caller=fake.call_tool)
    store = StateStore(scratch / "state.db")
    runner = StepRunner(store, scratch / "locks")
    run_id = store.create_run(
        episode_id="EPISODE_002",
        chapter_id="CH01",
        workflow_name="test-r2",
        input_hash="p",
    )
    plan = _build_synthetic_ch01_plan(tmp_path)

    adapter.apply_plan(
        plan,
        store=store,
        runner=runner,
        run_id=run_id,
        scratch_project_path=package,
    )

    schemas = load_palmier_tool_schemas()
    mismatches = []
    for name, args in fake.mutation_calls:
        schema = schemas[name]
        missing = set(schema.get("required", [])) - set(args.keys())
        extra = set(args.keys()) - set(schema.get("properties", {}).keys())
        if missing or extra:
            mismatches.append((name, sorted(missing), sorted(extra)))

    assert mismatches == []


def test_r3_verify_ch01_fails_when_native_project_or_mp4_missing(tmp_path: Path) -> None:
    qa_root = tmp_path / "qa-scratch"
    qa_root.mkdir(parents=True)
    policy = RootPolicy(output_root=qa_root)

    # Compile offline prep artifacts in qa_root without assembling/exporting
    assert cli.cmd_compile_ch01(policy) == 0
    assert cli.cmd_verify_prep_ch01(policy) == 0
    assert cli.cmd_verify_ch01(policy) == 1


def test_r4_semantic_reconciliation_detects_corrupted_keyframes_and_transforms(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    package = scratch / "test.palmier"
    package.mkdir()

    policy = RootPolicy(output_root=scratch)
    fake = FakePalmierMcpServer(package)
    adapter = PalmierAdapter(policy, tool_caller=fake.call_tool)
    store = StateStore(scratch / "state.db")
    runner = StepRunner(store, scratch / "locks")
    run_id = store.create_run(
        episode_id="EPISODE_002",
        chapter_id="CH01",
        workflow_name="test-r4",
        input_hash="p",
    )
    plan = _build_synthetic_ch01_plan(tmp_path)
    adapter.apply_plan(
        plan,
        store=store,
        runner=runner,
        run_id=run_id,
        scratch_project_path=package,
    )
    assert adapter.reconcile_plan(plan, adapter.read_state()).is_noop

    mar_track = next(t for t in fake.tracks if t["name"] == "Mar")
    clip0 = mar_track["clips"][0]
    clip0["keyframes"]["position"] = [[0, 100.0, 100.0, "linear"]]
    clip0["positionKeyframes"] = clip0["keyframes"]["position"]
    clip0["keyframes"]["rotation"] = []
    clip0["keyframes"]["scale"] = []
    clip0["transform"] = {"centerX": 100.0, "centerY": 100.0}

    diff = adapter.reconcile_plan(plan, adapter.read_state())
    assert not diff.is_noop
    assert any(c.clip_key == "mar_00" for c, _ in diff.clips_needing_keyframes)


def test_r5_ambiguous_editor_timeout_blocks_blind_retry_and_scopes_by_episode(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "r5.db")
    runner = StepRunner(store, tmp_path / "locks")
    run_id = store.create_run(
        episode_id="EPISODE_002",
        chapter_id="CH01",
        workflow_name="test-r5",
        input_hash="x",
    )

    remote_side_effects: list[str] = []

    def ambiguous_remote(_: Any) -> dict[str, Any]:
        remote_side_effects.append("created")
        raise TimeoutError("response lost after remote accepted request")

    with pytest.raises(TimeoutError):
        runner.execute_step(
            run_id=run_id,
            step_name="remote-add",
            step_kind="editor_mutation",
            input_hash="same",
            action=ambiguous_remote,
        )

    # Second attempt without reconciliation must raise BlockedRunError without calling ambiguous_remote again
    with pytest.raises(BlockedRunError):
        runner.execute_step(
            run_id=run_id,
            step_name="remote-add",
            step_kind="editor_mutation",
            input_hash="same",
            action=ambiguous_remote,
        )

    assert len(remote_side_effects) == 1
    assert [r["status"] for r in store.get_step_events("remote-add")] == ["blocked"]

    # Reconciling with inspector confirming remote mutation landed marks event 'reconciled'
    store.reconcile_run(run_id, editor_inspector=lambda _: "applied")
    assert [r["status"] for r in store.get_step_events("remote-add")] == ["reconciled"]

    # Cross-episode scoping: EPISODE_002 and EPISODE_003 must not share step receipts
    episodes_called: list[str] = []
    for ep in ("EPISODE_002", "EPISODE_003"):
        ep_run = store.create_run(
            episode_id=ep,
            chapter_id="CH01",
            workflow_name="cross-ep",
            input_hash="same",
        )
        ticket, _ = runner.execute_step(
            run_id=ep_run,
            step_name="import-same-media",
            step_kind="editor_mutation",
            input_hash="same",
            action=lambda _, e=ep: episodes_called.append(e) or {},
        )
        assert not ticket.already_completed

    assert episodes_called == ["EPISODE_002", "EPISODE_003"]


def test_r6_export_preserves_existing_mp4_and_requires_terminal_completed_job(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    policy = RootPolicy(output_root=scratch)

    existing = scratch / "existing.mp4"
    existing.write_bytes(b"previous-good-export")

    def reject_export(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("new export could not be submitted")

    failing_adapter = PalmierAdapter(policy, tool_caller=reject_export)
    with pytest.raises(RuntimeError):
        failing_adapter.export_video_mp4(existing, timeout_seconds=0.1)

    assert existing.exists()
    assert existing.read_bytes() == b"previous-good-export"

    growing = scratch / "growing.mp4"

    def pending_export(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "export_project":
            growing.write_bytes(b"partial-header")
            return {"jobId": "pending-job", "status": "started"}
        return {"exports": []}

    pending_adapter = PalmierAdapter(policy, tool_caller=pending_export)
    with pytest.raises(PalmierEditorError, match="Timed out waiting for terminal 'completed' status"):
        pending_adapter.export_video_mp4(growing, poll_interval_seconds=0.01, timeout_seconds=0.1)


def test_r7_cancellable_subprocess_drains_large_stdout_without_deadlock() -> None:
    res = run_cancellable_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1048576)"],
        timeout_seconds=5.0,
    )
    assert res.returncode == 0
    assert len(res.stdout) == 1048576


def test_r8_inter_foot_gap_segmentation_ignores_outstretched_arm_skew(tmp_path: Path) -> None:
    img = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Character body centered around x=200..300, with an outstretched right arm reaching x=560
    draw.rectangle([200, 100, 300, 500], fill=(255, 180, 120, 255))
    draw.rectangle([300, 220, 560, 260], fill=(255, 180, 120, 255))
    # Two distinct feet at the bottom (x=190..230 and x=270..310, inter-foot gap=40px, midpoint=250)
    draw.rectangle([190, 500, 230, 560], fill=(80, 50, 30, 255))
    draw.rectangle([270, 500, 310, 560], fill=(80, 50, 30, 255))

    p = tmp_path / "Outstretched_Arm_Pose.png"
    img.save(p)

    policy = RootPolicy(output_root=tmp_path)
    reg = calibrate_pose_png(
        policy,
        asset_id="test_outstretched",
        character_or_prop="TestChar",
        pose_name="Point_Right",
        path=p,
    )
    # Bounding box midpoint would be (190+560)/2 = 375, whereas true foot midpoint is ~250
    assert abs(reg.support_foot_x - 250.0) <= 5.0
    assert reg.transparent_pixel_count > 0
