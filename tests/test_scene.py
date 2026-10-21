"""Tests for PNG registration calibration, grounded motion compensation, pose swaps, and episode neutrality."""

from __future__ import annotations

import math
from pathlib import Path
import pytest

from mk_studio.config import RootPolicy
from mk_studio.contracts import (
    AlphaBounds,
    AssetEntry,
    AssetManifest,
    ChapterSpec,
    MotionProfileSpec,
    PoseRegistration,
    PoseSpanSpec,
    RegistrationCalibration,
)
from mk_studio.scene import (
    SceneCompilationError,
    compile_scene,
    evaluate_clip_motion_at_frame,
)
from mk_studio.templates.welcome import build_ch01_proof_bundle


def test_ch01_registration_and_grounded_motion_bounds() -> None:
    policy = RootPolicy()
    _, calibration, _, plan = build_ch01_proof_bundle(policy)

    assert plan.total_frames == 907
    assert plan.canvas_width == 1920
    assert plan.canvas_height == 1080
    assert plan.fps == 30

    track_By_name = {t.track_name: t for t in plan.tracks}
    assert ["Props", "Mar", "Kuku", "Background", "Audio_Master"] == [t.track_name for t in plan.tracks]

    for char_name in ("Mar", "Kuku"):
        char_track = track_By_name[char_name]
        # Verify contiguous hard cuts covering 0..907 with zero crossfades
        assert char_track.clips[0].start_frame == 0
        assert char_track.clips[-1].end_frame == 907
        for idx, clip in enumerate(char_track.clips):
            assert clip.fade_in_frames == 0
            assert clip.fade_out_frames == 0
            if idx > 0:
                assert clip.start_frame == char_track.clips[idx - 1].end_frame

            reg = calibration.poses[clip.asset_id]
            head_positions: list[tuple[float, float]] = []
            max_foot_drift = 0.0

            # Sample EVERY integer frame and half-frame (both keyframes and interpolated frames)
            for f in range(clip.start_frame, clip.end_frame):
                state = evaluate_clip_motion_at_frame(clip, reg, float(f))
                max_foot_drift = max(max_foot_drift, state["foot_drift_px"])
                head_positions.append((state["head_top_x_px"], state["head_top_y_px"]))

                if f + 0.5 < clip.end_frame - 1:
                    half_state = evaluate_clip_motion_at_frame(clip, reg, float(f) + 0.5)
                    max_foot_drift = max(max_foot_drift, half_state["foot_drift_px"])

            # Support foot must stay within <= 2.0 px across all keyframes and interpolated frames
            assert max_foot_drift <= 2.0, (
                f"{char_name} clip {clip.clip_key} exceeded 2px foot drift: {max_foot_drift:.4f}px"
            )

            # Non-anchor body landmark (head_top) must move >= 3.0 px on grounded_bob clips
            min_hx = min(p[0] for p in head_positions)
            max_hx = max(p[0] for p in head_positions)
            min_hy = min(p[1] for p in head_positions)
            max_hy = max(p[1] for p in head_positions)
            head_excursion = math.hypot(max_hx - min_hx, max_hy - min_hy)

            if clip.motion_mode == "grounded_bob":
                assert head_excursion >= 3.0, (
                    f"{char_name} clip {clip.clip_key} head motion too small: {head_excursion:.2f}px"
                )
            elif clip.motion_mode == "still_hold":
                assert head_excursion == pytest.approx(0.0, abs=1e-4)


def test_pose_swap_foot_anchor_continuity() -> None:
    policy = RootPolicy()
    _, calibration, _, plan = build_ch01_proof_bundle(policy)
    track_by_name = {t.track_name: t for t in plan.tracks}

    for char_name in ("Mar", "Kuku"):
        clips = track_by_name[char_name].clips
        for idx in range(len(clips) - 1):
            c_prev = clips[idx]
            c_next = clips[idx + 1]
            reg_prev = calibration.poses[c_prev.asset_id]
            reg_next = calibration.poses[c_next.asset_id]

            end_state = evaluate_clip_motion_at_frame(c_prev, reg_prev, float(c_prev.end_frame - 1))
            start_state = evaluate_clip_motion_at_frame(c_next, reg_next, float(c_next.start_frame))

            # Support foot position across pose swap must remain grounded within <= 2.0 px
            swap_foot_delta = math.hypot(
                start_state["support_foot_x_px"] - end_state["support_foot_x_px"],
                start_state["support_foot_y_px"] - end_state["support_foot_y_px"],
            )
            assert swap_foot_delta <= 2.0, (
                f"{char_name} pose swap {c_prev.clip_key}->{c_next.clip_key} foot jump {swap_foot_delta:.4f}px > 2px"
            )


def test_overlapping_pose_spans_rejected() -> None:
    alpha = AlphaBounds(threshold=128, left=100, top=20, right=900, bottom=1400)
    reg = PoseRegistration(
        asset_id="pose_a",
        character_or_prop="Hero",
        pose_name="Wave",
        path="/tmp/pose_a.png",
        sha256="111",
        image_width=1000,
        image_height=1500,
        has_transparency=True,
        alpha_bounds_t1=alpha,
        alpha_bounds_t128=alpha,
        alpha_bounds_t254=alpha,
        left_foot_x=400.0,
        left_foot_y=1400.0,
        right_foot_x=600.0,
        right_foot_y=1400.0,
        support_foot_x=500.0,
        support_foot_y=1400.0,
        head_top_x=500.0,
        head_top_y=20.0,
        reference_visible_height_px=1380.0,
    )
    manifest = AssetManifest(
        assets={
            "bg": AssetEntry(asset_id="bg", role="background", path="/tmp/bg.png", sha256="b"),
            "aud": AssetEntry(asset_id="aud", role="audio_master", path="/tmp/a.wav", sha256="a"),
            "pose_a": AssetEntry(
                asset_id="pose_a",
                role="character_pose",
                character_id="Hero",
                pose_name="Wave",
                path="/tmp/pose_a.png",
                sha256="111",
            ),
        }
    )
    cal = RegistrationCalibration(poses={"pose_a": reg})
    bad_spec = ChapterSpec(
        episode_id="EPISODE_099",
        chapter_id="CH07",
        title="Overlap Test",
        audio_asset_id="aud",
        audio_duration_seconds=2.0,
        audio_total_samples=96000,
        total_frames=60,
        background_asset_id="bg",
        pose_spans=[
            PoseSpanSpec(
                span_id="s1",
                character_id="Hero",
                asset_id="pose_a",
                start_frame=0,
                end_frame=40,
                anchor_canvas_x=0.5,
                anchor_canvas_y=0.85,
                target_visible_height_Norm=0.5,
            ),
            PoseSpanSpec(
                span_id="s2",
                character_id="Hero",
                asset_id="pose_a",
                start_frame=30,
                end_frame=60,
                anchor_canvas_x=0.5,
                anchor_canvas_y=0.85,
                target_visible_height_Norm=0.5,
            ),
        ],
    )
    with pytest.raises(SceneCompilationError, match="Overlapping pose/prop spans prohibited"):
        compile_scene(bad_spec, manifest, cal)


def test_episode_neutral_contracts_and_modules() -> None:
    repo_root = Path("/Users/yonassayfu/Projects/mar-kuku-production-studio")
    for rel in ("src/mk_studio/contracts.py", "src/mk_studio/registration.py", "src/mk_studio/scene.py"):
        src_text = (repo_root / rel).read_text(encoding="utf-8")
        assert "EPISODE_002" not in src_text, f"Hardcoded EPISODE_002 found in {rel}"
        assert "E02_CH01" not in src_text, f"Hardcoded E02_CH01 found in {rel}"

    # Verify compile_scene works with synthetic EPISODE_005 / CH03 data
    alpha = AlphaBounds(threshold=128, left=100, top=50, right=700, bottom=1150)
    reg = PoseRegistration(
        asset_id="custom_pose",
        character_or_prop="CustomHost",
        pose_name="Idle",
        path="/tmp/custom.png",
        sha256="abc",
        image_width=800,
        image_height=1200,
        has_transparency=True,
        alpha_bounds_t1=alpha,
        alpha_bounds_t128=alpha,
        alpha_bounds_t254=alpha,
        left_foot_x=350.0,
        left_foot_y=1150.0,
        right_foot_x=450.0,
        right_foot_y=1150.0,
        support_foot_x=400.0,
        support_foot_y=1150.0,
        head_top_x=400.0,
        head_top_y=50.0,
        reference_visible_height_px=1100.0,
    )
    manifest = AssetManifest(
        assets={
            "bg_5": AssetEntry(asset_id="bg_5", role="background", path="/tmp/bg5.png", sha256="b5"),
            "aud_5": AssetEntry(asset_id="aud_5", role="audio_master", path="/tmp/a5.wav", sha256="a5"),
            "custom_pose": AssetEntry(
                asset_id="custom_pose",
                role="character_pose",
                character_id="CustomHost",
                pose_name="Idle",
                path="/tmp/custom.png",
                sha256="abc",
            ),
        }
    )
    cal = RegistrationCalibration(poses={"custom_pose": reg})
    spec = ChapterSpec(
        episode_id="EPISODE_005",
        chapter_id="CH03",
        title="Synthetic Episode Neutral Check",
        audio_asset_id="aud_5",
        audio_duration_seconds=3.0,
        audio_total_samples=144000,
        total_frames=90,
        background_asset_id="bg_5",
        pose_spans=[
            PoseSpanSpec(
                span_id="c1",
                character_id="CustomHost",
                asset_id="custom_pose",
                start_frame=0,
                end_frame=90,
                anchor_canvas_x=0.45,
                anchor_canvas_y=0.88,
                target_visible_height_Norm=0.52,
                motion=MotionProfileSpec(mode="grounded_bob", cycle_frames=30),
            )
        ],
    )
    plan = compile_scene(spec, manifest, cal)
    assert plan.episode_id == "EPISODE_005"
    assert plan.chapter_id == "CH03"
    assert plan.total_frames == 90
