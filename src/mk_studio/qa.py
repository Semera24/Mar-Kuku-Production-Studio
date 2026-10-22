"""Technical verification, production inventory audit, MP4 decode & rendered-frame QA, and proof report generation."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

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
from mk_studio.media import CH01_PINNED_INPUTS, compare_wav_provenance
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


def _inspect_rendered_mp4_frames(
    mp4_path: Path,
    *,
    frames_dir: Path,
    ffmpeg_bin: str | Path = DEFAULT_FFMPEG_BIN,
    last_frame: int = 906,
) -> dict[str, Any]:
    """Extract key frames from the exported MP4 and verify rendered motion, response hold stillness, and prop appearance."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    sample_frames = [0, 15, 30, 501, 560, 620, 725, last_frame]
    extracted_paths: dict[int, Path] = {}

    select_expr = "+".join(f"eq(n\\,{fn})" for fn in sample_frames)
    pattern = frames_dir / "sample_%03d.png"
    res = run_cancellable_subprocess(
        [
            str(ffmpeg_bin),
            "-y",
            "-v",
            "error",
            "-i",
            str(mp4_path),
            "-vf",
            f"select='{select_expr}'",
            "-fps_mode",
            "passthrough",
            str(pattern),
        ],
        check=False,
    )
    if res.returncode != 0:
        return {
            "passed": False,
            "error": (res.stderr or "").strip() or "Failed extracting MP4 sample frames",
        }

    for idx, fn in enumerate(sample_frames, start=1):
        src_p = frames_dir / f"sample_{idx:03d}.png"
        dst_p = frames_dir / f"frame_{fn:04d}.png"
        if src_p.is_file():
            src_p.replace(dst_p)
            extracted_paths[fn] = dst_p

    if len(extracted_paths) != len(sample_frames):
        return {
            "passed": False,
            "error": f"Expected {len(sample_frames)} extracted frames, got {len(extracted_paths)}",
        }

    def _rms_diff(img_a: Image.Image, img_b: Image.Image, box: tuple[int, int, int, int] | None = None) -> float:
        ca = img_a.crop(box) if box else img_a
        cb = img_b.crop(box) if box else img_b
        ba = ca.convert("RGB").tobytes()
        bb = cb.convert("RGB").tobytes()
        if not ba or len(ba) != len(bb):
            return 999.0
        sq_sum = sum((a - b) * (a - b) for a, b in zip(ba, bb))
        return math.sqrt(sq_sum / float(len(ba)))

    with (
        Image.open(extracted_paths[0]) as f0,
        Image.open(extracted_paths[15]) as f15,
        Image.open(extracted_paths[501]) as f501,
        Image.open(extracted_paths[560]) as f560,
        Image.open(extracted_paths[620]) as f620,
        Image.open(extracted_paths[725]) as f725,
        Image.open(extracted_paths[last_frame]) as f_last,
    ):
        # 1. Verify frames are non-black
        mean_luma_f0 = sum(f0.convert("L").tobytes()) / float(1920 * 1080)
        mean_luma_last = sum(f_last.convert("L").tobytes()) / float(1920 * 1080)

        # 2. Verify active character bobbing between frame 0 and frame 15 in character region
        char_box = (150, 250, 1200, 1060)
        bob_rms_0_to_15 = _rms_diff(f0, f15, char_box)

        # 3. Verify response hold stillness between frame 501, 560, and 620
        hold_rms_501_to_560 = _rms_diff(f501, f560, char_box)
        hold_rms_560_to_620 = _rms_diff(f560, f620, char_box)

        # 4. Verify flower prop appearance in right foreground box (comparing frame 725 against frame 501 or baseline frame 30)
        prop_box = (1300, 550, 1820, 1040)
        f30 = Image.open(extracted_paths[30])
        prop_rms_appear = max(_rms_diff(f501, f725, prop_box), _rms_diff(f30, f725, prop_box))
        f30.close()

    non_black_ok = mean_luma_f0 > 20.0 and mean_luma_last > 20.0
    bob_active_ok = bob_rms_0_to_15 > 5.0
    hold_still_ok = (
        hold_rms_501_to_560 < 4.0
        and hold_rms_560_to_620 < 4.0
        and hold_rms_501_to_560 < (bob_rms_0_to_15 * 0.25)
    )
    prop_appear_ok = prop_rms_appear > 10.0

    return {
        "passed": bool(non_black_ok and bob_active_ok and hold_still_ok and prop_appear_ok),
        "frames_dir": str(frames_dir),
        "extracted_frames": {str(k): str(v) for k, v in extracted_paths.items()},
        "mean_luma_frame_0": round(mean_luma_f0, 3),
        "mean_luma_last_frame": round(mean_luma_last, 3),
        "character_bob_rms_0_to_15": round(bob_rms_0_to_15, 4),
        "hold_stillness_rms_501_to_560": round(hold_rms_501_to_560, 4),
        "hold_stillness_rms_560_to_620": round(hold_rms_560_to_620, 4),
        "flower_prop_appearance_rms": round(prop_rms_appear, 4),
    }


def verify_exported_mp4(
    mp4_path: str | Path,
    *,
    expected_duration_seconds: float = 30.22,
    expected_frames: int = 907,
    duration_tolerance_seconds: float = 0.10,
    ffprobe_bin: str | Path = DEFAULT_FFPROBE_BIN,
    ffmpeg_bin: str | Path = DEFAULT_FFMPEG_BIN,
    inspect_rendered_frames: bool = True,
) -> dict[str, Any]:
    """Verify an exported MP4 for H.264 1920x1080@30fps, 907 decoded frames, 48kHz stereo audio, zero decode errors, and rendered motion."""
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
            "-count_frames",
            "-show_entries",
            "format=duration,size:stream=index,codec_name,codec_type,width,height,r_frame_rate,avg_frame_rate,nb_read_frames,nb_frames,sample_rate,channels,duration",
            "-of",
            "json",
            str(target),
        ],
        check=False,
    )
    if probe_res.returncode != 0:
        return {
            "exists": True,
            "passed": False,
            "path": str(target),
            "reason": f"ffprobe failed: {(probe_res.stderr or '').strip()}",
        }

    info = json.loads(probe_res.stdout or "{}")
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
    avg_fps = video_stream.get("avg_frame_rate") if video_stream else None
    nb_read_frames = int(
        (video_stream or {}).get("nb_read_frames")
        or (video_stream or {}).get("nb_frames")
        or 0
    )
    sample_rate = int(audio_stream.get("sample_rate", 0)) if audio_stream else 0
    channels = int(audio_stream.get("channels", 0)) if audio_stream else 0
    duration = float(fmt.get("duration") or (video_stream or {}).get("duration") or 0.0)
    video_duration = float((video_stream or {}).get("duration") or duration)
    audio_duration = float((audio_stream or {}).get("duration") or 0.0)
    duration_diff = abs(duration - expected_duration_seconds)
    audio_duration_diff = abs(audio_duration - expected_duration_seconds) if audio_duration else 0.0

    rendered_qa: dict[str, Any] = {"passed": True, "skipped": True}
    if inspect_rendered_frames and decode_res.returncode == 0 and width == 1920 and height == 1080:
        frames_dir = target.parent / "rendered-frames"
        rendered_qa = _inspect_rendered_mp4_frames(
            target,
            frames_dir=frames_dir,
            ffmpeg_bin=ffmpeg_bin,
            last_frame=max(0, expected_frames - 1),
        )

    passed = (
        v_codec == "h264"
        and width == 1920
        and height == 1080
        and r_fps in ("30/1", "30")
        and nb_read_frames == expected_frames
        and audio_stream is not None
        and sample_rate == 48000
        and channels >= 1
        and duration_diff <= duration_tolerance_seconds
        and decode_res.returncode == 0
        and decode_errors == ""
        and bool(rendered_qa.get("passed", False))
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
        "avg_frame_rate": avg_fps,
        "nb_read_frames": nb_read_frames,
        "expected_frames": expected_frames,
        "audio_sample_rate": sample_rate,
        "audio_channels": channels,
        "duration_seconds": duration,
        "video_stream_duration_seconds": video_duration,
        "audio_stream_duration_seconds": audio_duration,
        "audio_duration_diff_seconds": round(audio_duration_diff, 6),
        "expected_duration_seconds": expected_duration_seconds,
        "duration_diff_seconds": duration_diff,
        "decode_returncode": decode_res.returncode,
        "decode_errors": decode_errors,
        "rendered_frame_qa": rendered_qa,
    }


def verify_native_palmier_project(
    palmier_proj_path: Path,
    reopen_receipt_path: Path | None = None,
    *,
    min_tracks: int = 1,
    min_clips: int = 1,
    min_markers: int = 0,
    require_reopen: bool = False,
) -> dict[str, Any]:
    """Verify that the native .palmier package exists on disk, contains a populated project.json, and passed close/reopen reconciliation."""
    exists = palmier_proj_path.is_dir() and (palmier_proj_path / "project.json").is_file()
    if not exists:
        return {
            "exists": False,
            "passed": False,
            "path": str(palmier_proj_path),
            "reopen_verified": False,
            "reason": "Native scratch .palmier bundle (with project.json) does not exist.",
        }

    proj_json_path = palmier_proj_path / "project.json"
    proj_sha = compute_sha256(proj_json_path)
    try:
        proj_data = json.loads(proj_json_path.read_text(encoding="utf-8"))
    except Exception:
        proj_data = {}

    timelines = proj_data.get("timelines") or []
    tl = timelines[0] if timelines else {}
    tracks = tl.get("tracks") or []
    clips_on_disk = sum(len(t.get("clips") or []) for t in tracks if isinstance(t, dict))
    markers_on_disk = len(tl.get("markers") or [])
    structure_valid = len(tracks) >= min_tracks and clips_on_disk >= min_clips and markers_on_disk >= min_markers

    reopen_verified = False
    reopen_details: dict[str, Any] = {}
    if reopen_receipt_path and reopen_receipt_path.is_file():
        reopen_details = json.loads(reopen_receipt_path.read_text(encoding="utf-8"))
        reopen_verified = bool(reopen_details.get("reopen_reconciled_noop") is True)

    passed = bool(exists and structure_valid and (reopen_verified if require_reopen else True))

    return {
        "exists": True,
        "passed": passed,
        "path": str(palmier_proj_path),
        "project_json_sha256": proj_sha,
        "structure_valid": structure_valid,
        "tracks_on_disk": len(tracks),
        "clips_on_disk": clips_on_disk,
        "markers_on_disk": markers_on_disk,
        "reopen_verified": reopen_verified,
        "reopen_details": reopen_details,
    }


def generate_proof_report(
    policy: RootPolicy | None = None,
    *,
    inventory_path: str | Path = DEFAULT_INVENTORY_PATH,
    require_editor_and_mp4: bool = True,
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
    reopen_receipt_path = scratch / "reopen-verification.json"

    mp4_1080p = scratch / "E02_CH01_Technical_Proof_1080p30_v001.mp4"
    mp4_legacy = scratch / "E02_CH01_Technical_Proof_v001.mp4"
    mp4_path = mp4_1080p if mp4_1080p.is_file() else mp4_legacy

    report_path = pol.resolve_output(scratch / "proof-report.md", overwrite=True, create_parents=True)

    inputs_data = json.loads(inputs_path.read_text(encoding="utf-8"))
    calibration = RegistrationCalibration.model_validate_json(reg_path.read_text(encoding="utf-8"))
    chapter_spec = ChapterSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    plan = TimelinePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

    motion_qa = verify_plan_motion_and_registration(plan, calibration, chapter_spec)
    inventory_qa = verify_production_inventory(inventory_path)

    # Fresh PCM verification directly against the WAV files on disk when reproduced WAV exists
    cand_wav_path = pol.resolve_read(
        CH01_PINNED_INPUTS["ch01_candidate_wav"]["path"],
        expected_sha256=CH01_PINNED_INPUTS["ch01_candidate_wav"]["sha256"],
    )
    if repro_wav_path.is_file():
        fresh_audio_repro = compare_wav_provenance(cand_wav_path, repro_wav_path).model_dump()
    else:
        fresh_audio_repro = dict(inputs_data.get("provenance_comparison", {}))
        fresh_audio_repro["reproduced_wav_present_on_disk"] = False

    pcm_exact = (
        fresh_audio_repro.get("max_abs_sample_diff") == 0
        and bool(fresh_audio_repro.get("exact_sample_count_match"))
    )

    palmier_qa = verify_native_palmier_project(
        palmier_proj_path,
        reopen_receipt_path,
        min_tracks=5,
        min_clips=16,
        min_markers=10,
        require_reopen=True,
    )
    mp4_qa = verify_exported_mp4(mp4_path)

    store = StateStore(db_path)
    tables = store.list_tables()

    cand_audio = inputs_data["candidate_audio_stream_0a0"]
    frame_cov = inputs_data["compiled_edit_plan"]["frame_coverage"]

    prep_passed = bool(inventory_qa["passed"] and motion_qa["passed"] and pcm_exact)
    full_proof_passed = bool(
        prep_passed
        and repro_wav_path.is_file()
        and palmier_qa["passed"]
        and mp4_qa["passed"]
    )

    now_iso = datetime.now(timezone.utc).isoformat()

    lines = [
        "# Milestone M0 Technical Proof Report — Episode 002 / CH01",
        "",
        f"- **Generated At (UTC):** `{now_iso}`",
        f"- **Production Root (Read-Only):** `{pol.production_root_resolved}`",
        f"- **Scratch Output Root:** `{scratch}`",
        f"- **Offline Prep Verdict:** **{'PASS' if prep_passed else 'FAIL'}**",
        f"- **Full End-to-End Proof Verdict:** **{'PASS' if full_proof_passed else 'FAIL'}**",
        "",
        "## 1. Production Root Immutability Audit",
        "",
        f"- **Inventory File:** `{inventory_qa['inventory_path']}`",
        f"- **Records Verified:** `{inventory_qa['checked_records']} / {inventory_qa['total_records']}`",
        f"- **Missing Files:** `{len(inventory_qa['missing_files'])}`",
        f"- **Hash/Byte Mismatches:** `{len(inventory_qa['mismatches'])}`",
        f"- **Verdict:** **{'PASS' if inventory_qa['passed'] else 'FAIL'}**",
        "",
        "## 2. Deterministic Audio Timing & Fresh Edit Reproduction (`stream 0:a:0`)",
        "",
        f"- **Candidate WAV (`E02_CH01_Welcome_v002_EDIT_REVIEW_FULL_MIX.wav`):**",
        f"  - Sample Rate: `{cand_audio['sample_rate']} Hz`",
        f"  - Total Samples (`duration_ts`): `{cand_audio['duration_ts']}`",
        f"  - Exact Duration: `{cand_audio['duration_seconds']:.6f} s`",
        f"  - Frame Coverage @ 30 fps: `{frame_cov['total_frames']} frames` (`{frame_cov['video_duration_seconds']:.6f} s`, visual overhang `+{frame_cov['visual_overhang_ms']:.6f} ms` / `{frame_cov['visual_overhang_samples']} samples`)",
        f"- **Freshly Verified Reproduced WAV (`{repro_wav_path.name}`):**",
        f"  - Container SHA-256: `{fresh_audio_repro['reproduced_sha256']}`",
        f"  - Candidate Container SHA-256: `{fresh_audio_repro['candidate_sha256']}`",
        f"  - Max Sample Diff (LSB): `{fresh_audio_repro['max_abs_sample_diff']}`",
        f"  - RMS Sample Diff: `{fresh_audio_repro['rms_sample_diff']}`",
        f"  - Bit-Identical PCM Match: **`{pcm_exact}`** ({fresh_audio_repro['explanation']})",
        "",
        "## 3. Cutout Registration & Analytic Motion Model (`907 frames @ 30 fps`)",
        "",
        f"- **Analytic Motion & Registration Verdict:** **{'PASS' if motion_qa['passed'] else 'FAIL'}** (`evaluation_mode={motion_qa.get('evaluation_mode', 'analytic_prediction')}`)",
        f"- **Response Hold Stillness (`frames {motion_qa['hold_window']['start_frame']}..{motion_qa['hold_window']['end_frame']}` = `4.00 s`):**",
        f"  - Max Analytic Hold Excursion: `{motion_qa['hold_window']['max_excursion_px']:.4f} px` (threshold `<= 0.50 px`)",
        "",
        "| Clip Key | Track | Asset ID | Frames | Motion Mode | Analytic Foot Drift (px) | Analytic Head Excursion (px) |",
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
            "## 5. Native Palmier Scratch Assembly, Reopen Verification & MP4 Export QA",
            "",
            f"- **Scratch Project Path:** `{palmier_proj_path}` (`exists={palmier_qa['exists']}`, `reopen_verified={palmier_qa['reopen_verified']}`)",
            f"- **Exported MP4 Path:** `{mp4_path}` (`exists={mp4_qa['exists']}`, `passed={mp4_qa['passed']}`)",
        ]
    )

    if mp4_qa["exists"]:
        rqa = mp4_qa.get("rendered_frame_qa", {})
        lines.extend(
            [
                f"- **MP4 Technical QA Verdict:** **{'PASS' if mp4_qa['passed'] else 'FAIL'}**",
                f"  - Codec / Resolution / FPS: `{mp4_qa.get('video_codec')}` `{mp4_qa.get('width')}x{mp4_qa.get('height')}` @ `{mp4_qa.get('r_frame_rate')}`",
                f"  - Decoded Frame Count (`nb_read_frames`): `{mp4_qa.get('nb_read_frames')} / {mp4_qa.get('expected_frames')}`",
                f"  - Audio Stream: `{mp4_qa.get('audio_sample_rate')} Hz` (`{mp4_qa.get('audio_channels')} ch`)",
                f"  - Duration: `{mp4_qa.get('duration_seconds', 0.0):.6f} s` (diff `{mp4_qa.get('duration_diff_seconds', 0.0):.6f} s`)",
                f"  - Full Decode Errors: `{mp4_qa.get('decode_errors') or 'none (0 errors)'}`",
                f"  - Rendered Frame Inspection (`{rqa.get('frames_dir', 'N/A')}`): `passed={rqa.get('passed')}`",
                f"    - Character bob RMS (`frame 0 -> 15`): `{rqa.get('character_bob_rms_0_to_15')}`",
                f"    - Response hold RMS (`frame 501 -> 560 -> 620`): `{rqa.get('hold_stillness_rms_501_to_560')} / {rqa.get('hold_stillness_rms_560_to_620')}`",
                f"    - Red flower appearance RMS (`frame 501 -> 725`): `{rqa.get('red_flower_appearance_rms_501_to_725')}`",
            ]
        )
    else:
        lines.append(
            "- **Status:** Native `.palmier` assembly and MP4 export have not completed yet (`verify-ch01` fails until both exist and pass)."
        )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "report_path": str(report_path),
        "prep_passed": prep_passed,
        "full_proof_passed": full_proof_passed,
        "inventory_passed": inventory_qa["passed"],
        "motion_passed": motion_qa["passed"],
        "audio_pcm_exact_match": pcm_exact,
        "palmier_project_exists": palmier_qa["exists"],
        "palmier_qa": palmier_qa,
        "mp4_qa": mp4_qa,
    }
