"""Technical verification, production inventory audit, MP4 decode QA, and proof report generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mk_studio.config import (
    DEFAULT_FFMPEG_BIN,
    DEFAULT_FFPROBE_BIN,
    RootPolicy,
    compute_sha256,
)
from mk_studio.contracts import (
    ChapterSpec,
    RegistrationCalibration,
    TimelinePlan,
)
from mk_studio.runner import run_cancellable_subprocess
from mk_studio.scene import verify_plan_motion_and_registration
from mk_studio.state import StateStore

DEFAULT_INVENTORY_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "implementation" / "inventory.json"
)


def verify_production_inventory(
    inventory_path: str | Path = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    """Verify that all production files recorded in inventory.json remain byte-identical."""
    inv_file = Path(inventory_path).expanduser().resolve(strict=True)
    data = json.loads(inv_file.read_text(encoding="utf-8"))
    records = data.get("records", [])

    checked = 0
    mismatches: list[dict[str, Any]] = []
    ds_store_changes: list[dict[str, Any]] = []
    missing: list[str] = []

    for rec in records:
        p = Path(rec["path"])
        if not p.is_file():
            if p.name == ".DS_Store":
                continue
            missing.append(str(p))
            continue
        actual_sha = compute_sha256(p)
        actual_size = p.stat().st_size
        checked += 1
        if actual_sha != rec["sha256"] or actual_size != int(rec["bytes"]):
            entry = {
                "path": str(p),
                "expected_sha256": rec["sha256"],
                "actual_sha256": actual_sha,
                "expected_bytes": rec["bytes"],
                "actual_bytes": actual_size,
            }
            if p.name == ".DS_Store":
                ds_store_changes.append(entry)
            else:
                mismatches.append(entry)

    return {
        "passed": len(mismatches) == 0 and len(missing) == 0,
        "inventory_path": str(inv_file),
        "total_records": len(records),
        "checked_records": checked,
        "missing_files": missing,
        "mismatches": mismatches,
        "ds_store_changes": ds_store_changes,
    }


def verify_exported_mp4(
    mp4_path: str | Path,
    *,
    expected_duration_seconds: float = 30.22,
    duration_tolerance_seconds: float = 0.10,
    ffprobe_bin: str | Path = DEFAULT_FFPROBE_BIN,
    ffmpeg_bin: str | Path = DEFAULT_FFMPEG_BIN,
) -> dict[str, Any]:
    """Verify an exported MP4 for H.264 1920x1080@30fps, 48kHz audio, duration, and zero decode errors."""
    target = Path(mp4_path).expanduser().resolve(strict=False)
    if not target.is_file() or target.stat().st_size == 0:
        return {
            "exists": False,
            "passed": False,
            "path": str(target),
            "reason": "Exported MP4 file does not exist yet.",
        }

    probe_res = run_cancellable_subprocess(
        [
            str(ffprobe_bin),
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_name,codec_type,width,height,r_frame_rate,sample_rate,duration",
            "-of",
            "json",
            str(target),
        ]
    )
    info = json.loads(probe_res.stdout)
    streams = info.get("streams", [])
    fmt = info.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    decode_res = run_cancellable_subprocess(
        [
            str(ffmpeg_bin),
            "-v",
            "error",
            "-i",
            str(target),
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    decode_errors = (decode_res.stderr or "").strip()

    v_codec = video_stream.get("codec_name") if video_stream else None
    width = int(video_stream.get("width", 0)) if video_stream else 0
    height = int(video_stream.get("height", 0)) if video_stream else 0
    r_fps = video_stream.get("r_frame_rate") if video_stream else None
    sample_rate = int(audio_stream.get("sample_rate", 0)) if audio_stream else 0
    duration = float(fmt.get("duration") or (video_stream or {}).get("duration") or 0.0)
    duration_diff = abs(duration - expected_duration_seconds)

    passed = (
        v_codec == "h264"
        and width == 1920
        and height == 1080
        and r_fps in ("30/1", "30")
        and audio_stream is not None
        and sample_rate == 48000
        and duration_diff <= duration_tolerance_seconds
        and decode_res.returncode == 0
        and decode_errors == ""
    )

    return {
        "exists": True,
        "passed": passed,
        "path": str(target),
        "bytes": target.stat().st_size,
        "sha256": compute_sha256(target),
        "video_codec": v_codec,
        "width": width,
        "height": height,
        "r_frame_rate": r_fps,
        "audio_sample_rate": sample_rate,
        "duration_seconds": duration,
        "expected_duration_seconds": expected_duration_seconds,
        "duration_diff_seconds": duration_diff,
        "decode_returncode": decode_res.returncode,
        "decode_errors": decode_errors,
    }


def generate_proof_report(
    policy: RootPolicy | None = None,
    *,
    inventory_path: str | Path = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    """Run all technical proof checks on scratch/ch01-proof and write proof-report.md."""
    pol = policy or RootPolicy()
    scratch = pol.output_root_resolved

    inputs_path = scratch / "inputs.json"
    repro_wav_path = scratch / "E02_CH01_Welcome_reproduced_v002.wav"
    reg_path = scratch / "registration.json"
    spec_path = scratch / "chapter-spec.json"
    plan_path = scratch / "timeline-plan.json"
    db_path = scratch / "state.db"
    palmier_proj_path = scratch / "E02_CH01_Technical_Proof_v001.palmier"
    mp4_path = scratch / "E02_CH01_Technical_Proof_v001.mp4"
    report_path = pol.resolve_output(scratch / "proof-report.md", overwrite=True, create_parents=True)

    inputs_data = json.loads(inputs_path.read_text(encoding="utf-8"))
    calibration = RegistrationCalibration.model_validate_json(reg_path.read_text(encoding="utf-8"))
    chapter_spec = ChapterSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    plan = TimelinePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

    motion_qa = verify_plan_motion_and_registration(plan, calibration, chapter_spec)
    inventory_qa = verify_production_inventory(inventory_path)
    mp4_qa = verify_exported_mp4(mp4_path)

    store = StateStore(db_path)
    tables = store.list_tables()

    palmier_exists = palmier_proj_path.exists()
    audio_repro = inputs_data["provenance_comparison"]
    cand_audio = inputs_data["candidate_audio_stream_0a0"]
    frame_cov = inputs_data["compiled_edit_plan"]["frame_coverage"]
    pcm_exact = audio_repro["max_abs_sample_diff"] == 0 and audio_repro["exact_sample_count_match"]

    now_iso = datetime.now(timezone.utc).isoformat()

    lines = [
        "# Milestone M0 Technical Proof Report — Episode 002 / CH01",
        "",
        f"- **Generated At (UTC):** `{now_iso}`",
        f"- **Production Root (Read-Only):** `{pol.production_root_resolved}`",
        f"- **Scratch Output Root:** `{scratch}`",
        "",
        "## 1. Production Root Immutability Audit",
        "",
        f"- **Inventory File:** `{inventory_qa['inventory_path']}`",
        f"- **Records Verified:** `{inventory_qa['checked_records']} / {inventory_qa['total_records']}`",
        f"- **Missing Files:** `{len(inventory_qa['missing_files'])}`",
        f"- **Hash/Byte Mismatches:** `{len(inventory_qa['mismatches'])}`",
        f"- **Verdict:** **{'PASS' if inventory_qa['passed'] else 'FAIL'}**",
        "",
        "## 2. Deterministic Audio Timing & Edit Reproduction (`stream 0:a:0`)",
        "",
        f"- **Candidate WAV (`E02_CH01_Welcome_v002_EDIT_REVIEW_FULL_MIX.wav`):**",
        f"  - Sample Rate: `{cand_audio['sample_rate']} Hz`",
        f"  - Total Samples (`duration_ts`): `{cand_audio['duration_ts']}`",
        f"  - Exact Duration: `{cand_audio['duration_seconds']:.6f} s`",
        f"  - Frame Coverage @ 30 fps: `{frame_cov['total_frames']} frames` (`{frame_cov['video_duration_seconds']:.6f} s`, visual overhang `+{frame_cov['visual_overhang_ms']:.6f} ms` / `{frame_cov['visual_overhang_samples']} samples`)",
        f"- **Reproduced WAV (`scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav`):**",
        f"  - Container SHA-256: `{audio_repro['reproduced_sha256']}`",
        f"  - Candidate Container SHA-256: `{audio_repro['candidate_sha256']}`",
        f"  - Max Sample Diff (LSB): `{audio_repro['max_abs_sample_diff']}`",
        f"  - RMS Sample Diff: `{audio_repro['rms_sample_diff']}`",
        f"  - Bit-Identical PCM Match: **`{pcm_exact}`** ({audio_repro['explanation']})",
        "",
        "## 3. Cutout Registration & Grounded Motion QA (`907 frames @ 30 fps`)",
        "",
        f"- **Motion & Registration Verdict:** **{'PASS' if motion_qa['passed'] else 'FAIL'}**",
        f"- **Response Hold Stillness (`frames {motion_qa['hold_window']['start_frame']}..{motion_qa['hold_window']['end_frame']}` = `4.00 s`):**",
        f"  - Max Hold Excursion: `{motion_qa['hold_window']['max_excursion_px']:.4f} px` (threshold `<= 0.50 px`)",
        "",
        "| Clip Key | Track | Asset ID | Frames | Motion Mode | Max Foot Drift (px) | Head Excursion (px) |",
        "|---|---|---|---:|---|---:|---:|",
    ]

    for c_id, c_metrics in motion_qa["clips"].items():
        lines.append(
            f"| `{c_id}` | `{c_metrics['track_name']}` | `{c_metrics['asset_id']}` | "
            f"`{c_metrics['start_frame']}..{c_metrics['end_frame']}` | `{c_metrics['motion_mode']}` | "
            f"`{c_metrics['max_foot_drift_px']:.4f}` | `{c_metrics['head_excursion_px']:.2f}` |"
        )

    lines.extend(
        [
            "",
            "## 4. SQLite Durable State Store (`scratch/ch01-proof/state.db`)",
            "",
            f"- **Database Path:** `{db_path}`",
            f"- **Tables Initialized (`{len(tables)}`):** `{', '.join(tables)}`",
            "",
            "## 5. Native Palmier Scratch Assembly & MP4 Export Status",
            "",
            f"- **Scratch Project Path:** `{palmier_proj_path}` (`exists={palmier_exists}`)",
            f"- **Exported MP4 Path:** `{mp4_path}` (`exists={mp4_qa['exists']}`)",
        ]
    )

    if mp4_qa["exists"]:
        lines.extend(
            [
                f"- **MP4 Technical QA Verdict:** **{'PASS' if mp4_qa['passed'] else 'FAIL'}**",
                f"  - Codec / Resolution / FPS: `{mp4_qa['video_codec']}` `{mp4_qa['width']}x{mp4_qa['height']}` @ `{mp4_qa['r_frame_rate']}`",
                f"  - Audio Sample Rate: `{mp4_qa['audio_sample_rate']} Hz`",
                f"  - Duration: `{mp4_qa['duration_seconds']:.6f} s` (diff `{mp4_qa['duration_diff_seconds']:.6f} s`)",
                f"  - Full Decode Errors: `{mp4_qa['decode_errors'] or 'none (0 errors)'}`",
            ]
        )
    else:
        lines.extend(
            [
                "- **Blocked on Single Owner UI Action (Rule-Compliant):**",
                "  - Binary inspection of `/Applications/PalmierPro.app/Contents/MacOS/PalmierPro` (`AppState.createProject(named:)` at `0x10176eee8`) confirmed that Palmier's MCP `manage_project(action='create')` rejects path components (`\"isn't a valid project name. Use a plain name without slashes or path components.\"`) and writes exclusively to `~/Documents/Palmier Pro/<name>.palmier`.",
                "  - Per the Native Palmier Scratch Project Rule in `docs/implementation/antigravity-task.md`, `mk-studio` refused to create a project in `~/Documents/Palmier Pro/` or hand-write `.palmier` JSON files.",
                "  - **One-Step Owner Action to Unblock Live Assembly & Export:**",
                "    1. In Palmier Pro, press `Cmd+N` (`File -> New Project...`) and save the empty project to:",
                f"       `{palmier_proj_path}`",
                "    2. Run:",
                "       `.venv/bin/mk-studio assemble-ch01 && .venv/bin/mk-studio verify-ch01`",
            ]
        )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "report_path": str(report_path),
        "inventory_passed": inventory_qa["passed"],
        "motion_passed": motion_qa["passed"],
        "audio_pcm_exact_match": pcm_exact,
        "palmier_project_exists": palmier_exists,
        "mp4_qa": mp4_qa,
    }
