"""Reusable Welcome/Teaching Intro chapter template and CH01 proof bundle builder."""

from __future__ import annotations

import json
from pathlib import Path

from mk_studio.config import RootPolicy
from mk_studio.contracts import (
    AssetEntry,
    AssetManifest,
    ChapterSpec,
    MotionProfileSpec,
    PoseSpanSpec,
    PropSpanSpec,
    RegistrationCalibration,
    TimelinePlan,
)
from mk_studio.media import CH01_PINNED_INPUTS, build_ch01_cues, probe_audio_stream_0a0
from mk_studio.registration import calibrate_manifest_assets
from mk_studio.scene import compile_scene
from mk_studio.timing import samples_to_frame_coverage


def build_ch01_asset_manifest(policy: RootPolicy) -> AssetManifest:
    """Build the verified AssetManifest for CH01 from CH01_PINNED_INPUTS."""
    role_map = {
        "bg_color_garden": ("background", None, None),
        "ch01_candidate_wav": ("audio_master", None, None),
        "mar_wave": ("character_pose", "Mar", "Wave"),
        "mar_introduce": ("character_pose", "Mar", "Introduce_Self"),
        "mar_point_right": ("character_pose", "Mar", "Point_Right"),
        "mar_listen": ("character_pose", "Mar", "Listen"),
        "mar_happy": ("character_pose", "Mar", "Happy"),
        "kuku_wave": ("character_pose", "Kuku", "Wave"),
        "kuku_happy": ("character_pose", "Kuku", "Happy"),
        "kuku_think": ("character_pose", "Kuku", "Think_HeadTilt"),
        "kuku_point_right": ("character_pose", "Kuku", "Point_Right"),
        "prop_flower_red": ("prop", "Prop_Flower_Red", "Flower_Red"),
    }
    entries: dict[str, AssetEntry] = {}
    for asset_id, (role, char_id, pose_name) in role_map.items():
        pin = CH01_PINNED_INPUTS[asset_id]
        resolved = policy.resolve_read(pin["path"], expected_sha256=pin["sha256"])
        entries[asset_id] = AssetEntry(
            asset_id=asset_id,
            role=role,  # type: ignore[arg-type]
            character_id=char_id,
            pose_name=pose_name,
            path=str(resolved),
            sha256=pin["sha256"],
        )
    return AssetManifest(assets=entries)


def build_welcome_chapter_spec(
    *,
    episode_id: str,
    chapter_id: str,
    title: str,
    audio_asset_id: str,
    audio_path: Path,
    background_asset_id: str,
    mar_poses: dict[str, str],
    kuku_poses: dict[str, str],
    prop_asset_id: str,
) -> ChapterSpec:
    """Build a Welcome/Intro ChapterSpec aligned to the 30.22s CH01 cue structure."""
    stream = probe_audio_stream_0a0(audio_path)
    coverage = samples_to_frame_coverage(stream.duration_ts, sample_rate=stream.sample_rate)
    total_frames = int(coverage["total_frames"])
    cues = build_ch01_cues(audio_path)

    mar_bob = MotionProfileSpec(
        mode="grounded_bob",
        cycle_frames=60,
        scale_amplitude=0.030,
        rotation_amplitude_deg=1.5,
        sway_amplitude_px=0.0,
        shared_phase_origin_frame=0,
    )
    kuku_bob = MotionProfileSpec(
        mode="grounded_bob",
        cycle_frames=50,
        scale_amplitude=0.034,
        rotation_amplitude_deg=1.8,
        sway_amplitude_px=0.0,
        shared_phase_origin_frame=15,
    )
    hold_motion = MotionProfileSpec(
        mode="still_hold",
        cycle_frames=60,
        scale_amplitude=0.0,
        rotation_amplitude_deg=0.0,
        sway_amplitude_px=0.0,
        shared_phase_origin_frame=0,
    )

    mar_x, mar_y, mar_h = 0.28, 0.89, 0.58
    kuku_x, kuku_y, kuku_h = 0.56, 0.89, 0.39

    pose_spans = [
        # Mar contiguous hard-cut pose timeline (0..907)
        PoseSpanSpec(
            span_id="mar_01_wave",
            character_id="Mar",
            asset_id=mar_poses["wave"],
            start_frame=0,
            end_frame=62,
            anchor_canvas_x=mar_x,
            anchor_canvas_y=mar_y,
            target_visible_height_Norm=mar_h,
            motion=mar_bob,
            cue_id="CUE_01_HELLO",
        ),
        PoseSpanSpec(
            span_id="mar_02_introduce",
            character_id="Mar",
            asset_id=mar_poses["introduce"],
            start_frame=62,
            end_frame=180,
            anchor_canvas_x=mar_x,
            anchor_canvas_y=mar_y,
            target_visible_height_Norm=mar_h,
            motion=mar_bob,
            cue_id="CUE_02_IDENTITY",
        ),
        PoseSpanSpec(
            span_id="mar_03_happy_picnic",
            character_id="Mar",
            asset_id=mar_poses["happy"],
            start_frame=180,
            end_frame=354,
            anchor_canvas_x=mar_x,
            anchor_canvas_y=mar_y,
            target_visible_height_Norm=mar_h,
            motion=mar_bob,
            cue_id="CUE_04_PICNIC",
        ),
        PoseSpanSpec(
            span_id="mar_04_point_match",
            character_id="Mar",
            asset_id=mar_poses["point_right"],
            start_frame=354,
            end_frame=501,
            anchor_canvas_x=mar_x,
            anchor_canvas_y=mar_y,
            target_visible_height_Norm=mar_h,
            motion=mar_bob,
            cue_id="CUE_06_MATCH_INVITATION",
        ),
        PoseSpanSpec(
            span_id="mar_05_listen_hold",
            character_id="Mar",
            asset_id=mar_poses["listen"],
            start_frame=501,
            end_frame=621,
            anchor_canvas_x=mar_x,
            anchor_canvas_y=mar_y,
            target_visible_height_Norm=mar_h,
            motion=hold_motion,
            cue_id="CUE_08_RESPONSE_HOLD",
        ),
        PoseSpanSpec(
            span_id="mar_06_happy_look",
            character_id="Mar",
            asset_id=mar_poses["happy"],
            start_frame=621,
            end_frame=725,
            anchor_canvas_x=mar_x,
            anchor_canvas_y=mar_y,
            target_visible_height_Norm=mar_h,
            motion=mar_bob,
            cue_id="CUE_09_LOOK_TOGETHER",
        ),
        PoseSpanSpec(
            span_id="mar_07_point_flower",
            character_id="Mar",
            asset_id=mar_poses["point_right"],
            start_frame=725,
            end_frame=total_frames,
            anchor_canvas_x=mar_x,
            anchor_canvas_y=mar_y,
            target_visible_height_Norm=mar_h,
            motion=mar_bob,
            cue_id="CUE_10_FIRST_FLOWER_RED",
        ),
        # Kuku contiguous hard-cut pose timeline (0..907)
        PoseSpanSpec(
            span_id="kuku_01_wave",
            character_id="Kuku",
            asset_id=kuku_poses["wave"],
            start_frame=0,
            end_frame=127,
            anchor_canvas_x=kuku_x,
            anchor_canvas_y=kuku_y,
            target_visible_height_Norm=kuku_h,
            motion=kuku_bob,
            cue_id="CUE_01_HELLO",
        ),
        PoseSpanSpec(
            span_id="kuku_02_happy_welcome",
            character_id="Kuku",
            asset_id=kuku_poses["happy"],
            start_frame=127,
            end_frame=354,
            anchor_canvas_x=kuku_x,
            anchor_canvas_y=kuku_y,
            target_visible_height_Norm=kuku_h,
            motion=kuku_bob,
            cue_id="CUE_03_WELCOME",
        ),
        PoseSpanSpec(
            span_id="kuku_03_point_match",
            character_id="Kuku",
            asset_id=kuku_poses["point_right"],
            start_frame=354,
            end_frame=501,
            anchor_canvas_x=kuku_x,
            anchor_canvas_y=kuku_y,
            target_visible_height_Norm=kuku_h,
            motion=kuku_bob,
            cue_id="CUE_06_MATCH_INVITATION",
        ),
        PoseSpanSpec(
            span_id="kuku_04_think_hold",
            character_id="Kuku",
            asset_id=kuku_poses["think"],
            start_frame=501,
            end_frame=621,
            anchor_canvas_x=kuku_x,
            anchor_canvas_y=kuku_y,
            target_visible_height_Norm=kuku_h,
            motion=hold_motion,
            cue_id="CUE_08_RESPONSE_HOLD",
        ),
        PoseSpanSpec(
            span_id="kuku_05_happy_look",
            character_id="Kuku",
            asset_id=kuku_poses["happy"],
            start_frame=621,
            end_frame=725,
            anchor_canvas_x=kuku_x,
            anchor_canvas_y=kuku_y,
            target_visible_height_Norm=kuku_h,
            motion=kuku_bob,
            cue_id="CUE_09_LOOK_TOGETHER",
        ),
        PoseSpanSpec(
            span_id="kuku_06_point_flower",
            character_id="Kuku",
            asset_id=kuku_poses["point_right"],
            start_frame=725,
            end_frame=total_frames,
            anchor_canvas_x=kuku_x,
            anchor_canvas_y=kuku_y,
            target_visible_height_Norm=kuku_h,
            motion=kuku_bob,
            cue_id="CUE_10_FIRST_FLOWER_RED",
        ),
    ]

    prop_spans = [
        PropSpanSpec(
            span_id="prop_01_red_flower",
            prop_id="Flower_Red",
            asset_id=prop_asset_id,
            start_frame=725,
            end_frame=total_frames,
            anchor_canvas_x=0.81,
            anchor_canvas_y=0.86,
            target_visible_height_Norm=0.28,
            cue_id="CUE_10_FIRST_FLOWER_RED",
        )
    ]

    return ChapterSpec(
        episode_id=episode_id,
        chapter_id=chapter_id,
        title=title,
        canvas_width=1920,
        canvas_height=1080,
        fps_num=30,
        fps_den=1,
        audio_asset_id=audio_asset_id,
        audio_duration_seconds=stream.duration_seconds,
        audio_sample_rate=stream.sample_rate,
        audio_total_samples=stream.duration_ts,
        total_frames=total_frames,
        background_asset_id=background_asset_id,
        pose_spans=pose_spans,
        prop_spans=prop_spans,
        cues=cues,
    )


def build_ch01_proof_bundle(
    policy: RootPolicy | None = None,
) -> tuple[AssetManifest, RegistrationCalibration, ChapterSpec, TimelinePlan]:
    """Calibrate CH01 assets, compile the CH01 TimelinePlan, and persist calibration/plan JSON in scratch."""
    pol = policy or RootPolicy()
    manifest = build_ch01_asset_manifest(pol)
    calibration = calibrate_manifest_assets(
        pol,
        manifest,
        save_path=pol.output_root_resolved / "registration.json",
    )
    cand_wav_path = Path(manifest.assets["ch01_candidate_wav"].path)
    chapter_spec = build_welcome_chapter_spec(
        episode_id="EPISODE_002",
        chapter_id="CH01",
        title="Welcome to the Color Garden",
        audio_asset_id="ch01_candidate_wav",
        audio_path=cand_wav_path,
        background_asset_id="bg_color_garden",
        mar_poses={
            "wave": "mar_wave",
            "introduce": "mar_introduce",
            "point_right": "mar_point_right",
            "listen": "mar_listen",
            "happy": "mar_happy",
        },
        kuku_poses={
            "wave": "kuku_wave",
            "happy": "kuku_happy",
            "think": "kuku_think",
            "point_right": "kuku_point_right",
        },
        prop_asset_id="prop_flower_red",
    )
    plan = compile_scene(chapter_spec, manifest, calibration)

    spec_out = pol.resolve_output(
        pol.output_root_resolved / "chapter-spec.json",
        overwrite=True,
        create_parents=True,
    )
    spec_out.write_text(json.dumps(chapter_spec.model_dump(), indent=2) + "\n", encoding="utf-8")

    plan_out = pol.resolve_output(
        pol.output_root_resolved / "timeline-plan.json",
        overwrite=True,
        create_parents=True,
    )
    plan_out.write_text(json.dumps(plan.model_dump(), indent=2) + "\n", encoding="utf-8")

    return manifest, calibration, chapter_spec, plan
