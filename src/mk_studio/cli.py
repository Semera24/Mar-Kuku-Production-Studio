"""Command-line interface for mk-studio M0 technical proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from mk_studio.config import (
    DEFAULT_FFMPEG_BIN,
    DEFAULT_FFPROBE_BIN,
    RootPolicy,
    compute_sha256,
)
from mk_studio.contracts import TimelinePlan
from mk_studio.editors.palmier import (
    PalmierAdapter,
    PalmierEditorError,
    ScratchProjectCreationBlockedError,
)
from mk_studio.media import CH01_PINNED_INPUTS, bind_ch01_inputs
from mk_studio.qa import generate_proof_report
from mk_studio.runner import StepRunner
from mk_studio.state import StateStore
from mk_studio.templates.welcome import build_ch01_proof_bundle


def _get_store_and_runner(policy: RootPolicy) -> tuple[StateStore, StepRunner]:
    db_path = policy.output_root_resolved / "state.db"
    lock_dir = policy.output_root_resolved / "locks"
    store = StateStore(db_path)
    runner = StepRunner(store, lock_dir)
    return store, runner


def cmd_doctor(policy: RootPolicy) -> int:
    store, _ = _get_store_and_runner(policy)
    adapter = PalmierAdapter(policy)
    try:
        palmier_info = adapter.discover()
    except PalmierEditorError as exc:
        palmier_info = {"reachable": False, "error": str(exc)}

    report = {
        "python": sys.version.split()[0],
        "production_root": str(policy.production_root_resolved),
        "production_root_exists": policy.production_root_resolved.is_dir(),
        "output_root": str(policy.output_root_resolved),
        "ffmpeg_bin": str(DEFAULT_FFMPEG_BIN),
        "ffmpeg_exists": Path(DEFAULT_FFMPEG_BIN).is_file(),
        "ffprobe_bin": str(DEFAULT_FFPROBE_BIN),
        "ffprobe_exists": Path(DEFAULT_FFPROBE_BIN).is_file(),
        "state_db": str(store.db_path),
        "state_tables": store.list_tables(),
        "palmier_mcp": palmier_info,
    }
    print(json.dumps(report, indent=2))
    return 0


def cmd_bind_ch01(policy: RootPolicy) -> int:
    store, runner = _get_store_and_runner(policy)
    store.upsert_episode(
        episode_id="EPISODE_002",
        title="Mar & Kuku's Color Garden Adventure",
        production_root=str(policy.production_root_resolved),
        status="in_progress",
    )
    store.upsert_chapter(
        episode_id="EPISODE_002",
        chapter_id="CH01",
        title="Welcome to the Color Garden",
        status="binding_inputs",
    )

    pin_hash = hashlib.sha256(
        json.dumps(CH01_PINNED_INPUTS, sort_keys=True).encode("utf-8")
    ).hexdigest()

    with runner.acquire_run_lock():
        run_id = store.create_run(
            run_id="run-m0-bind-ch01",
            episode_id="EPISODE_002",
            chapter_id="CH01",
            workflow_name="bind-ch01",
            input_hash=pin_hash,
        )
        store.reconcile_run(run_id)

        def _do_bind(_: Any) -> dict[str, Any]:
            summary = bind_ch01_inputs(policy)
            out_file = policy.output_root_resolved / "inputs.json"
            return {
                "output_path": str(out_file),
                "output_sha256": compute_sha256(out_file),
                "summary": summary,
            }

        _, res = runner.execute_step(
            run_id=run_id,
            step_name="bind_and_reproduce_ch01_audio",
            step_kind="file_output",
            input_hash=pin_hash,
            expected_output_path=str(policy.output_root_resolved / "inputs.json"),
            action=_do_bind,
        )

        inputs_file = policy.output_root_resolved / "inputs.json"
        inputs_data = json.loads(inputs_file.read_text(encoding="utf-8"))

        # Record pinned approvals and artifacts in state.db
        cand_pin = CH01_PINNED_INPUTS["ch01_candidate_wav"]
        rev_pin = CH01_PINNED_INPUTS["ch01_edit_review_md"]
        store.record_approval(
            episode_id="EPISODE_002",
            chapter_id="CH01",
            scope="audio_edit_candidate_technical_proof",
            target_path=str(policy.resolve_read(cand_pin["path"])),
            target_sha256=cand_pin["sha256"],
            status="approved_for_m0_proof",
            approved_by="owner_edit_review_v001",
            evidence_path=str(policy.resolve_read(rev_pin["path"])),
            notes="Verified bit-identical PCM reproduction against E02_CH01_AUDIO_EDIT_REVIEW_v001.md recipe.",
        )

        for key, item in inputs_data["bindings"].items():
            store.record_artifact(
                episode_id="EPISODE_002",
                chapter_id="CH01",
                role=f"input:{key}",
                path=item["path"],
                sha256=item["sha256"],
                bytes_size=int(item["bytes_size"]),
            )

        repro_wav = policy.output_root_resolved / "E02_CH01_Welcome_reproduced_v002.wav"
        repro_sha = compute_sha256(repro_wav)
        prov = inputs_data["provenance_comparison"]
        store.record_artifact(
            episode_id="EPISODE_002",
            chapter_id="CH01",
            role="scratch:reproduced_wav",
            path=str(repro_wav),
            sha256=repro_sha,
            bytes_size=repro_wav.stat().st_size,
            metadata=prov,
        )
        store.record_file_operation(
            run_id=run_id,
            step_name="bind_and_reproduce_ch01_audio",
            op_type="write_scratch_wav",
            source_path=inputs_data["bindings"]["ch01_original_wav"]["path"],
            source_sha256=inputs_data["bindings"]["ch01_original_wav"]["sha256"],
            dest_path=str(repro_wav),
            dest_sha256=repro_sha,
            bytes_size=repro_wav.stat().st_size,
        )

        cand_stream = inputs_data["candidate_audio_stream_0a0"]
        cov = inputs_data["compiled_edit_plan"]["frame_coverage"]
        store.upsert_chapter(
            episode_id="EPISODE_002",
            chapter_id="CH01",
            title="Welcome to the Color Garden",
            audio_duration_seconds=float(cand_stream["duration_seconds"]),
            audio_total_samples=int(cand_stream["duration_ts"]),
            total_frames=int(cov["total_frames"]),
            status="inputs_bound",
        )
        store.finish_run(run_id, status="succeeded")

    print(
        json.dumps(
            {
                "status": "ok",
                "inputs_json": str(inputs_file),
                "reproduced_wav": str(repro_wav),
                "pcm_exact_match": prov["max_abs_sample_diff"] == 0,
            },
            indent=2,
        )
    )
    return 0


def cmd_compile_ch01(policy: RootPolicy) -> int:
    store, runner = _get_store_and_runner(policy)
    inputs_file = policy.output_root_resolved / "inputs.json"
    if not inputs_file.is_file():
        cmd_bind_ch01(policy)

    in_hash = compute_sha256(inputs_file)
    plan_file = policy.output_root_resolved / "timeline-plan.json"

    with runner.acquire_run_lock():
        run_id = store.create_run(
            run_id="run-m0-compile-ch01",
            episode_id="EPISODE_002",
            chapter_id="CH01",
            workflow_name="compile-ch01",
            input_hash=in_hash,
        )
        store.reconcile_run(run_id)

        def _do_compile(_: Any) -> dict[str, Any]:
            _, _, _, plan = build_ch01_proof_bundle(policy)
            return {
                "output_path": str(plan_file),
                "output_sha256": compute_sha256(plan_file),
                "plan_id": plan.plan_id,
            }

        runner.execute_step(
            run_id=run_id,
            step_name="compile_ch01_scene_and_registration",
            step_kind="file_output",
            input_hash=in_hash,
            expected_output_path=str(plan_file),
            action=_do_compile,
        )

        for role, fname in [
            ("scratch:registration", "registration.json"),
            ("scratch:chapter_spec", "chapter-spec.json"),
            ("scratch:timeline_plan", "timeline-plan.json"),
        ]:
            fpath = policy.output_root_resolved / fname
            fsha = compute_sha256(fpath)
            store.record_artifact(
                episode_id="EPISODE_002",
                chapter_id="CH01",
                role=role,
                path=str(fpath),
                sha256=fsha,
                bytes_size=fpath.stat().st_size,
            )
            store.record_file_operation(
                run_id=run_id,
                step_name="compile_ch01_scene_and_registration",
                op_type="write_scratch_json",
                dest_path=str(fpath),
                dest_sha256=fsha,
                bytes_size=fpath.stat().st_size,
            )

        store.upsert_chapter(
            episode_id="EPISODE_002",
            chapter_id="CH01",
            title="Welcome to the Color Garden",
            status="compiled",
        )
        store.finish_run(run_id, status="succeeded")

    plan = TimelinePlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": "ok",
                "plan_id": plan.plan_id,
                "total_frames": plan.total_frames,
                "tracks": len(plan.tracks),
                "clips": sum(len(t.clips) for t in plan.tracks),
                "markers": len(plan.markers),
                "timeline_plan_path": str(plan_file),
            },
            indent=2,
        )
    )
    return 0


def cmd_assemble_ch01(policy: RootPolicy, scratch_project: str | None = None) -> int:
    store, runner = _get_store_and_runner(policy)
    plan_file = policy.output_root_resolved / "timeline-plan.json"
    if not plan_file.is_file():
        cmd_compile_ch01(policy)

    plan = TimelinePlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
    proj_path = (
        Path(scratch_project).expanduser().resolve(strict=False)
        if scratch_project
        else policy.output_root_resolved / "E02_CH01_Technical_Proof_v001.palmier"
    )
    mp4_path = policy.output_root_resolved / "E02_CH01_Technical_Proof_v001.mp4"

    adapter = PalmierAdapter(policy)
    with runner.acquire_run_lock(), runner.acquire_editor_writer_lock():
        run_id = store.create_run(
            run_id="run-m0-assemble-ch01",
            episode_id="EPISODE_002",
            chapter_id="CH01",
            workflow_name="assemble-ch01",
            input_hash=plan.plan_id,
        )
        try:
            apply_res = adapter.apply_plan(
                plan,
                store=store,
                runner=runner,
                run_id=run_id,
                scratch_project_path=proj_path,
            )
            reopened = adapter.close_and_reopen(proj_path)
            reopen_diff = adapter.reconcile_plan(plan, reopened)
            if not reopen_diff.is_noop:
                raise PalmierEditorError(
                    f"Reopened Palmier project state drifted from plan: {reopen_diff}"
                )
            export_res = adapter.export_video_mp4(mp4_path)
            store.finish_run(run_id, status="succeeded")
        except ScratchProjectCreationBlockedError as exc:
            store.finish_run(run_id, status="blocked", error_message=str(exc))
            print(
                json.dumps(
                    {
                        "status": "blocked_on_owner_ui_save",
                        "scratch_project_path": str(proj_path),
                        "message": str(exc),
                    },
                    indent=2,
                )
            )
            return 2

    print(
        json.dumps(
            {
                "status": "ok",
                "apply_result": apply_res,
                "reopen_reconciled_noop": True,
                "export_result": export_res,
                "mp4_path": str(mp4_path),
            },
            indent=2,
        )
    )
    return 0


def cmd_verify_ch01(policy: RootPolicy) -> int:
    plan_file = policy.output_root_resolved / "timeline-plan.json"
    if not plan_file.is_file():
        cmd_compile_ch01(policy)

    summary = generate_proof_report(policy)
    print(json.dumps(summary, indent=2))
    if not summary["inventory_passed"] or not summary["motion_passed"] or not summary["audio_pcm_exact_match"]:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mk-studio",
        description="Mar & Kuku Production Studio M0 Technical Proof CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Inspect environment, paths, SQLite state, and Palmier MCP")
    sub.add_parser("bind-ch01", help="Bind CH01 inputs and reproduce candidate audio in scratch")
    sub.add_parser("compile-ch01", help="Calibrate cutouts and compile CH01 TimelinePlan")
    p_assemble = sub.add_parser(
        "assemble-ch01",
        help="Reconcile CH01 TimelinePlan into scratch Palmier project, reopen, and export MP4",
    )
    p_assemble.add_argument(
        "--project",
        default=None,
        help="Path to scratch .palmier project (defaults to scratch/ch01-proof/E02_CH01_Technical_Proof_v001.palmier)",
    )
    sub.add_parser("verify-ch01", help="Verify production immutability, audio, motion, and generate proof-report.md")

    args = parser.parse_args(argv)
    policy = RootPolicy()

    if args.command == "doctor":
        return cmd_doctor(policy)
    if args.command == "bind-ch01":
        return cmd_bind_ch01(policy)
    if args.command == "compile-ch01":
        return cmd_compile_ch01(policy)
    if args.command == "assemble-ch01":
        return cmd_assemble_ch01(policy, scratch_project=args.project)
    if args.command == "verify-ch01":
        return cmd_verify_ch01(policy)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
