"""Episode-neutral scene compiler with compensated foot-anchored cutout animation."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from mk_studio.contracts import (
    AssetManifest,
    ChapterSpec,
    ClipPlan,
    KeyframeRow,
    MarkerPlan,
    MotionProfileSpec,
    PoseRegistration,
    PoseSpanSpec,
    PropSpanSpec,
    RegistrationCalibration,
    StaticTransformPlan,
    TimelinePlan,
    TrackPlan,
)
from mk_studio.timing import samples_to_frame_coverage


class SceneCompilationError(ValueError):
    """Raised when a ChapterSpec or pose span violates scene compilation rules."""


def solve_foot_anchored_state(
    reg: PoseRegistration,
    *,
    anchor_canvas_x: float,
    anchor_canvas_y: float,
    target_visible_height_norm: float,
    scale_factor: float = 1.0,
    rotation_deg: float = 0.0,
    canvas_width: int = 1920,
    canvas_height: int = 1080,
) -> dict[str, float]:
    """Compute Palmier width, height, centerX, centerY, topLeftX, topLeftY pinning support_foot to (anchor_canvas_x, anchor_canvas_y)."""
    height_norm = (
        target_visible_height_norm
        * scale_factor
        * (float(reg.image_height) / float(reg.reference_visible_height_px))
    )
    width_norm = height_norm * (
        (float(reg.image_width) * float(canvas_height))
        / (float(reg.image_height) * float(canvas_width))
    )

    dx_px = ((reg.support_foot_x / float(reg.image_width)) - 0.5) * (width_norm * canvas_width)
    dy_px = ((reg.support_foot_y / float(reg.image_height)) - 0.5) * (height_norm * canvas_height)

    rad = math.radians(rotation_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)

    rot_dx_px = dx_px * cos_a - dy_px * sin_a
    rot_dy_px = dx_px * sin_a + dy_px * cos_a

    center_x = anchor_canvas_x - (rot_dx_px / float(canvas_width))
    center_y = anchor_canvas_y - (rot_dy_px / float(canvas_height))
    top_left_x = center_x - (width_norm / 2.0)
    top_left_y = center_y - (height_norm / 2.0)

    return {
        "width": round(width_norm, 8),
        "height": round(height_norm, 8),
        "centerX": round(center_x, 8),
        "centerY": round(center_y, 8),
        "topLeftX": round(top_left_x, 8),
        "topLeftY": round(top_left_y, 8),
        "rotation": round(rotation_deg, 8),
    }


def project_landmark_to_canvas_px(
    reg: PoseRegistration,
    *,
    landmark_x_px: float,
    landmark_y_px: float,
    top_left_x: float,
    top_left_y: float,
    width_norm: float,
    height_norm: float,
    rotation_deg: float,
    canvas_width: int = 1920,
    canvas_height: int = 1080,
) -> tuple[float, float]:
    """Project an image pixel landmark (landmark_x_px, landmark_y_px) onto canvas pixel coordinates."""
    center_x = top_left_x + (width_norm / 2.0)
    center_y = top_left_y + (height_norm / 2.0)

    dx_px = ((landmark_x_px / float(reg.image_width)) - 0.5) * (width_norm * canvas_width)
    dy_px = ((landmark_y_px / float(reg.image_height)) - 0.5) * (height_norm * canvas_height)

    rad = math.radians(rotation_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)

    rot_dx_px = dx_px * cos_a - dy_px * sin_a
    rot_dy_px = dx_px * sin_a + dy_px * cos_a

    canvas_x_px = (center_x * canvas_width) + rot_dx_px
    canvas_y_px = (center_y * canvas_height) + rot_dy_px
    return canvas_x_px, canvas_y_px


def _generate_grounded_keyframes(
    reg: PoseRegistration,
    *,
    start_frame: int,
    end_frame: int,
    anchor_canvas_x: float,
    anchor_canvas_y: float,
    target_visible_height_norm: float,
    motion: MotionProfileSpec,
    canvas_width: int,
    canvas_height: int,
    keyframe_step: int = 5,
) -> tuple[StaticTransformPlan, list[KeyframeRow], list[KeyframeRow], list[KeyframeRow]]:
    """Generate static fallback transform and compensated position/scale/rotation keyframe tracks."""
    duration = end_frame - start_frame
    if duration <= 0:
        raise SceneCompilationError(f"Invalid clip duration: [{start_frame}, {end_frame})")

    base_state = solve_foot_anchored_state(
        reg,
        anchor_canvas_x=anchor_canvas_x,
        anchor_canvas_y=anchor_canvas_y,
        target_visible_height_norm=target_visible_height_norm,
        scale_factor=1.0,
        rotation_deg=0.0,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    static_tf = StaticTransformPlan(
        centerX=base_state["centerX"],
        centerY=base_state["centerY"],
        width=base_state["width"],
        height=base_state["height"],
        rotation=0.0,
    )

    if motion.mode == "still_hold":
        # Hold exact grounded position at start and end of clip
        offsets = [0, max(0, duration - 1)] if duration > 1 else [0]
        pos_rows = [
            KeyframeRow(
                frame_offset=off,
                values=[base_state["topLeftX"], base_state["topLeftY"]],
                interpolation="linear",
            )
            for off in offsets
        ]
        scale_rows = [
            KeyframeRow(
                frame_offset=off,
                values=[base_state["width"], base_state["height"]],
                interpolation="linear",
            )
            for off in offsets
        ]
        rot_rows = [
            KeyframeRow(
                frame_offset=off,
                values=[0.0],
                interpolation="linear",
            )
            for off in offsets
        ]
        return static_tf, pos_rows, scale_rows, rot_rows

    offsets_set = set(range(0, duration, max(1, keyframe_step)))
    offsets_set.add(max(0, duration - 1))
    sorted_offsets = sorted(offsets_set)

    pos_rows = []
    scale_rows = []
    rot_rows = []
    cycle = max(4, motion.cycle_frames)

    for off in sorted_offsets:
        global_frame = start_frame + off
        phase = (2.0 * math.pi * float(global_frame - motion.shared_phase_origin_frame)) / float(cycle)

        if motion.mode == "prop_pop_idle" and off < min(12, duration):
            # Gentle scale pop-in over first 10 frames while keeping base grounded
            pop_progress = float(off) / 10.0
            scale_factor = (0.88 + 0.12 * pop_progress) * (1.0 + motion.scale_amplitude * math.sin(phase))
        else:
            scale_factor = 1.0 + motion.scale_amplitude * math.sin(phase)

        rotation_deg = motion.rotation_amplitude_deg * math.sin(phase / 2.0)

        st = solve_foot_anchored_state(
            reg,
            anchor_canvas_x=anchor_canvas_x,
            anchor_canvas_y=anchor_canvas_y,
            target_visible_height_norm=target_visible_height_norm,
            scale_factor=scale_factor,
            rotation_deg=rotation_deg,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
        pos_rows.append(
            KeyframeRow(
                frame_offset=off,
                values=[st["topLeftX"], st["topLeftY"]],
                interpolation="linear",
            )
        )
        scale_rows.append(
            KeyframeRow(
                frame_offset=off,
                values=[st["width"], st["height"]],
                interpolation="linear",
            )
        )
        rot_rows.append(
            KeyframeRow(
                frame_offset=off,
                values=[st["rotation"]],
                interpolation="linear",
            )
        )

    return static_tf, pos_rows, scale_rows, rot_rows


def _interpolate_keyframe_rows(rows: list[KeyframeRow], clip_offset: float, fallback: list[float]) -> list[float]:
    if not rows:
        return fallback
    if clip_offset <= rows[0].frame_offset:
        return list(rows[0].values)
    if clip_offset >= rows[-1].frame_offset:
        return list(rows[-1].values)

    for i in range(len(rows) - 1):
        r0 = rows[i]
        r1 = rows[i + 1]
        if r0.frame_offset <= clip_offset <= r1.frame_offset:
            span = float(r1.frame_offset - r0.frame_offset)
            if span <= 1e-9 or r0.interpolation == "hold":
                return list(r0.values)
            t = (clip_offset - float(r0.frame_offset)) / span
            if r0.interpolation == "smooth":
                t = t * t * (3.0 - 2.0 * t)
            return [v0 + (v1 - v0) * t for v0, v1 in zip(r0.values, r1.values)]
    return list(rows[-1].values)


def evaluate_clip_motion_at_frame(
    clip: ClipPlan,
    reg: PoseRegistration,
    timeline_frame: float,
    *,
    canvas_width: int = 1920,
    canvas_height: int = 1080,
) -> dict[str, float]:
    """Evaluate a compiled ClipPlan at any integer or fractional timeline frame (including interpolated frames)."""
    if timeline_frame < clip.start_frame or timeline_frame >= clip.end_frame:
        raise ValueError(f"Frame {timeline_frame} outside clip range [{clip.start_frame}, {clip.end_frame})")

    offset = float(timeline_frame) - float(clip.start_frame)
    default_tl_x = clip.transform.centerX - (clip.transform.width / 2.0)
    default_tl_y = clip.transform.centerY - (clip.transform.height / 2.0)

    tl_x, tl_y = _interpolate_keyframe_rows(
        clip.position_keyframes,
        offset,
        [default_tl_x, default_tl_y],
    )
    w_norm, h_norm = _interpolate_keyframe_rows(
        clip.scale_keyframes,
        offset,
        [clip.transform.width, clip.transform.height],
    )
    rot_deg = _interpolate_keyframe_rows(
        clip.rotation_keyframes,
        offset,
        [clip.transform.rotation],
    )[0]

    foot_x_px, foot_y_px = project_landmark_to_canvas_px(
        reg,
        landmark_x_px=reg.support_foot_x,
        landmark_y_px=reg.support_foot_y,
        top_left_x=tl_x,
        top_left_y=tl_y,
        width_norm=w_norm,
        height_norm=h_norm,
        rotation_deg=rot_deg,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    head_x_px, head_y_px = project_landmark_to_canvas_px(
        reg,
        landmark_x_px=reg.head_top_x,
        landmark_y_px=reg.head_top_y,
        top_left_x=tl_x,
        top_left_y=tl_y,
        width_norm=w_norm,
        height_norm=h_norm,
        rotation_deg=rot_deg,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    anchor_x_px = (clip.anchor_canvas_x if clip.anchor_canvas_x is not None else 0.5) * canvas_width
    anchor_y_px = (clip.anchor_canvas_y if clip.anchor_canvas_y is not None else 0.5) * canvas_height
    foot_drift_px = math.hypot(foot_x_px - anchor_x_px, foot_y_px - anchor_y_px)

    return {
        "timeline_frame": timeline_frame,
        "clip_offset": offset,
        "topLeftX": tl_x,
        "topLeftY": tl_y,
        "width": w_norm,
        "height": h_norm,
        "rotation": rot_deg,
        "support_foot_x_px": foot_x_px,
        "support_foot_y_px": foot_y_px,
        "anchor_x_px": anchor_x_px,
        "anchor_y_px": anchor_y_px,
        "foot_drift_px": foot_drift_px,
        "head_top_x_px": head_x_px,
        "head_top_y_px": head_y_px,
    }


def _validate_non_overlapping_spans(spans: list[PoseSpanSpec | PropSpanSpec], group_label: str) -> None:
    sorted_spans = sorted(spans, key=lambda s: (s.start_frame, s.end_frame))
    prev_end = 0
    for s in sorted_spans:
        if s.start_frame < 0 or s.end_frame <= s.start_frame:
            raise SceneCompilationError(
                f"Invalid span bounds in {group_label} ({s.span_id}): [{s.start_frame}, {s.end_frame})"
            )
        if s.start_frame < prev_end:
            raise SceneCompilationError(
                f"Overlapping pose/prop spans prohibited on {group_label}: {s.span_id} starts at {s.start_frame} < {prev_end}"
            )
        prev_end = s.end_frame


def compile_scene(
    chapter_spec: ChapterSpec,
    asset_manifest: AssetManifest,
    calibration: RegistrationCalibration,
) -> TimelinePlan:
    """Compile an episode-neutral ChapterSpec into a deterministic TimelinePlan."""
    if chapter_spec.fps_den != 1 or chapter_spec.fps_num <= 0:
        raise SceneCompilationError(
            f"Unsupported frame rate {chapter_spec.fps_num}/{chapter_spec.fps_den}"
        )

    coverage = samples_to_frame_coverage(
        chapter_spec.audio_total_samples,
        sample_rate=chapter_spec.audio_sample_rate,
        fps_num=chapter_spec.fps_num,
        fps_den=chapter_spec.fps_den,
    )
    expected_frames = int(coverage["total_frames"])
    if chapter_spec.total_frames != expected_frames:
        raise SceneCompilationError(
            f"ChapterSpec total_frames ({chapter_spec.total_frames}) does not match audio sample coverage ({expected_frames})"
        )

    if chapter_spec.background_asset_id not in asset_manifest.assets:
        raise SceneCompilationError(
            f"Background asset '{chapter_spec.background_asset_id}' missing from AssetManifest"
        )
    if chapter_spec.audio_asset_id not in asset_manifest.assets:
        raise SceneCompilationError(
            f"Audio asset '{chapter_spec.audio_asset_id}' missing from AssetManifest"
        )

    # Group character pose spans by character_id preserving insertion order of characters
    char_groups: dict[str, list[PoseSpanSpec]] = {}
    for span in chapter_spec.pose_spans:
        if span.asset_id not in asset_manifest.assets:
            raise SceneCompilationError(f"Pose asset '{span.asset_id}' missing from AssetManifest")
        if span.asset_id not in calibration.poses:
            raise SceneCompilationError(f"Pose asset '{span.asset_id}' missing from RegistrationCalibration")
        char_groups.setdefault(span.character_id, []).append(span)

    for char_id, spans in char_groups.items():
        _validate_non_overlapping_spans(spans, f"character:{char_id}")

    _validate_non_overlapping_spans(chapter_spec.prop_spans, "props")

    tracks: list[TrackPlan] = []
    next_track_idx = 0

    # 1. Props track on top (index 0) when prop_spans are present
    if chapter_spec.prop_spans:
        prop_clips: list[ClipPlan] = []
        for pspan in sorted(chapter_spec.prop_spans, key=lambda s: s.start_frame):
            if pspan.asset_id not in asset_manifest.assets or pspan.asset_id not in calibration.poses:
                raise SceneCompilationError(f"Prop asset '{pspan.asset_id}' missing from manifest or calibration")
            asset = asset_manifest.assets[pspan.asset_id]
            reg = calibration.poses[pspan.asset_id]
            tf, pos_kf, scale_kf, rot_kf = _generate_grounded_keyframes(
                reg,
                start_frame=pspan.start_frame,
                end_frame=pspan.end_frame,
                anchor_canvas_x=pspan.anchor_canvas_x,
                anchor_canvas_y=pspan.anchor_canvas_y,
                target_visible_height_norm=pspan.target_visible_height_Norm,
                motion=pspan.motion,
                canvas_width=chapter_spec.canvas_width,
                canvas_height=chapter_spec.canvas_height,
            )
            prop_clips.append(
                ClipPlan(
                    clip_key=pspan.span_id,
                    track_name="Props",
                    track_type="video",
                    asset_id=pspan.asset_id,
                    source_path=asset.path,
                    source_sha256=asset.sha256,
                    start_frame=pspan.start_frame,
                    end_frame=pspan.end_frame,
                    duration_frames=pspan.end_frame - pspan.start_frame,
                    transform=tf,
                    position_keyframes=pos_kf,
                    scale_keyframes=scale_kf,
                    rotation_keyframes=rot_kf,
                    fade_in_frames=0,
                    fade_out_frames=0,
                    anchor_canvas_x=pspan.anchor_canvas_x,
                    anchor_canvas_y=pspan.anchor_canvas_y,
                    motion_mode=pspan.motion.mode,
                )
            )
        tracks.append(
            TrackPlan(
                track_index=next_track_idx,
                track_name="Props",
                track_type="video",
                clips=prop_clips,
            )
        )
        next_track_idx += 1

    # 2. Character tracks (one track per character, hard pose swaps only)
    for char_id, spans in char_groups.items():
        char_clips: list[ClipPlan] = []
        for span in sorted(spans, key=lambda s: s.start_frame):
            asset = asset_manifest.assets[span.asset_id]
            reg = calibration.poses[span.asset_id]
            tf, pos_kf, scale_kf, rot_kf = _generate_grounded_keyframes(
                reg,
                start_frame=span.start_frame,
                end_frame=span.end_frame,
                anchor_canvas_x=span.anchor_canvas_x,
                anchor_canvas_y=span.anchor_canvas_y,
                target_visible_height_norm=span.target_visible_height_Norm,
                motion=span.motion,
                canvas_width=chapter_spec.canvas_width,
                canvas_height=chapter_spec.canvas_height,
            )
            char_clips.append(
                ClipPlan(
                    clip_key=span.span_id,
                    track_name=char_id,
                    track_type="video",
                    asset_id=span.asset_id,
                    source_path=asset.path,
                    source_sha256=asset.sha256,
                    start_frame=span.start_frame,
                    end_frame=span.end_frame,
                    duration_frames=span.end_frame - span.start_frame,
                    transform=tf,
                    position_keyframes=pos_kf,
                    scale_keyframes=scale_kf,
                    rotation_keyframes=rot_kf,
                    fade_in_frames=0,
                    fade_out_frames=0,
                    anchor_canvas_x=span.anchor_canvas_x,
                    anchor_canvas_y=span.anchor_canvas_y,
                    motion_mode=span.motion.mode,
                )
            )
        tracks.append(
            TrackPlan(
                track_index=next_track_idx,
                track_name=char_id,
                track_type="video",
                clips=char_clips,
            )
        )
        next_track_idx += 1

    # 3. Background track (bottom visual layer, freezing subtle zoom during response hold window)
    bg_asset = asset_manifest.assets[chapter_spec.background_asset_id]
    s0 = chapter_spec.background_scale_start
    s1 = chapter_spec.background_scale_end
    last_f = max(0, chapter_spec.total_frames - 1)

    hold_cue = next(
        (c for c in chapter_spec.cues if "HOLD" in c.cue_id.upper() or "RESPONSE" in c.cue_id.upper()),
        None,
    )
    if hold_cue and 0 < hold_cue.start_frame < hold_cue.end_frame < last_f:
        h_start = hold_cue.start_frame
        h_end = hold_cue.end_frame
        active_frames = max(1, last_f - (h_end - h_start))
        s_mid = round(s0 + (s1 - s0) * (float(h_start) / float(active_frames)), 8)
        bg_pos_kf = [
            KeyframeRow(frame_offset=0, values=[round((1.0 - s0) / 2.0, 8), round((1.0 - s0) / 2.0, 8)], interpolation="linear"),
            KeyframeRow(frame_offset=h_start, values=[round((1.0 - s_mid) / 2.0, 8), round((1.0 - s_mid) / 2.0, 8)], interpolation="linear"),
            KeyframeRow(frame_offset=h_end, values=[round((1.0 - s_mid) / 2.0, 8), round((1.0 - s_mid) / 2.0, 8)], interpolation="linear"),
            KeyframeRow(frame_offset=last_f, values=[round((1.0 - s1) / 2.0, 8), round((1.0 - s1) / 2.0, 8)], interpolation="linear"),
        ]
        bg_scale_kf = [
            KeyframeRow(frame_offset=0, values=[s0, s0], interpolation="linear"),
            KeyframeRow(frame_offset=h_start, values=[s_mid, s_mid], interpolation="linear"),
            KeyframeRow(frame_offset=h_end, values=[s_mid, s_mid], interpolation="linear"),
            KeyframeRow(frame_offset=last_f, values=[s1, s1], interpolation="linear"),
        ]
    else:
        bg_pos_kf = [
            KeyframeRow(frame_offset=0, values=[round((1.0 - s0) / 2.0, 8), round((1.0 - s0) / 2.0, 8)], interpolation="linear"),
            KeyframeRow(frame_offset=last_f, values=[round((1.0 - s1) / 2.0, 8), round((1.0 - s1) / 2.0, 8)], interpolation="linear"),
        ]
        bg_scale_kf = [
            KeyframeRow(frame_offset=0, values=[s0, s0], interpolation="linear"),
            KeyframeRow(frame_offset=last_f, values=[s1, s1], interpolation="linear"),
        ]

    bg_clip = ClipPlan(
        clip_key=f"{chapter_spec.chapter_id}_bg_full",
        track_name="Background",
        track_type="video",
        asset_id=chapter_spec.background_asset_id,
        source_path=bg_asset.path,
        source_sha256=bg_asset.sha256,
        start_frame=0,
        end_frame=chapter_spec.total_frames,
        duration_frames=chapter_spec.total_frames,
        transform=StaticTransformPlan(
            centerX=0.5,
            centerY=0.5,
            width=s0,
            height=s0,
            rotation=0.0,
        ),
        position_keyframes=bg_pos_kf,
        scale_keyframes=bg_scale_kf,
        motion_mode="ken_burns_subtle",
    )
    tracks.append(
        TrackPlan(
            track_index=next_track_idx,
            track_name="Background",
            track_type="video",
            clips=[bg_clip],
        )
    )
    next_track_idx += 1

    # 4. Audio master track
    audio_asset = asset_manifest.assets[chapter_spec.audio_asset_id]
    audio_clip = ClipPlan(
        clip_key=f"{chapter_spec.chapter_id}_audio_master",
        track_name="Audio_Master",
        track_type="audio",
        asset_id=chapter_spec.audio_asset_id,
        source_path=audio_asset.path,
        source_sha256=audio_asset.sha256,
        start_frame=0,
        end_frame=chapter_spec.total_frames,
        duration_frames=chapter_spec.total_frames,
        volume_db=0.0,
        motion_mode="audio_locked",
    )
    tracks.append(
        TrackPlan(
            track_index=next_track_idx,
            track_name="Audio_Master",
            track_type="audio",
            clips=[audio_clip],
        )
    )

    markers = [
        MarkerPlan(
            marker_key=c.cue_id,
            name=c.name,
            start_frame=c.start_frame,
            duration_frames=0,
            comment=f"[{c.authority}] {c.lyric_or_action}",
            color="#3B82F6" if c.authority == "report_derived" else "#10B981",
        )
        for c in chapter_spec.cues
    ]

    plan_digest = hashlib.sha256(
        json.dumps(
            {
                "episode_id": chapter_spec.episode_id,
                "chapter_id": chapter_spec.chapter_id,
                "total_frames": chapter_spec.total_frames,
                "tracks": [t.model_dump() for t in tracks],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]

    return TimelinePlan(
        plan_id=f"{chapter_spec.episode_id}_{chapter_spec.chapter_id}_{plan_digest}",
        episode_id=chapter_spec.episode_id,
        chapter_id=chapter_spec.chapter_id,
        timeline_name=f"{chapter_spec.episode_id}_{chapter_spec.chapter_id}_Proof",
        canvas_width=chapter_spec.canvas_width,
        canvas_height=chapter_spec.canvas_height,
        fps=chapter_spec.fps_num,
        total_frames=chapter_spec.total_frames,
        audio_duration_seconds=float(coverage["audio_duration_seconds"]),
        audio_total_samples=chapter_spec.audio_total_samples,
        video_duration_seconds=float(coverage["video_duration_seconds"]),
        visual_overhang_ms=float(coverage["visual_overhang_ms"]),
        visual_overhang_samples=int(coverage["visual_overhang_samples"]),
        tracks=tracks,
        markers=markers,
    )


def verify_plan_motion_and_registration(
    plan: TimelinePlan,
    calibration: RegistrationCalibration,
    chapter_spec: ChapterSpec | None = None,
) -> dict[str, Any]:
    """Evaluate all cutout character/prop clips across all integer and half-frames to verify <=2px foot drift and >=3px head motion."""
    clip_metrics: dict[str, dict[str, Any]] = {}
    all_passed = True
    hold_max_excursion = 0.0
    hold_start = 501
    hold_end = 621
    if chapter_spec is not None:
        for c in chapter_spec.cues:
            if "HOLD" in c.cue_id.upper() or "RESPONSE" in c.cue_id.upper():
                hold_start = c.start_frame
                hold_end = c.end_frame
                break

    for tr in plan.tracks:
        if tr.track_type != "video" or tr.track_name == "Background":
            continue
        for clip in tr.clips:
            if clip.asset_id not in calibration.poses:
                continue
            reg = calibration.poses[clip.asset_id]
            max_foot_drift = 0.0
            head_positions: list[tuple[float, float]] = []

            for f in range(clip.start_frame, clip.end_frame):
                st = evaluate_clip_motion_at_frame(
                    clip,
                    reg,
                    float(f),
                    canvas_width=plan.canvas_width,
                    canvas_height=plan.canvas_height,
                )
                max_foot_drift = max(max_foot_drift, st["foot_drift_px"])
                head_positions.append((st["head_top_x_px"], st["head_top_y_px"]))

                if f + 0.5 < clip.end_frame - 1:
                    hst = evaluate_clip_motion_at_frame(
                        clip,
                        reg,
                        float(f) + 0.5,
                        canvas_width=plan.canvas_width,
                        canvas_height=plan.canvas_height,
                    )
                    max_foot_drift = max(max_foot_drift, hst["foot_drift_px"])

            min_hx = min(p[0] for p in head_positions)
            max_hx = max(p[0] for p in head_positions)
            min_hy = min(p[1] for p in head_positions)
            max_hy = max(p[1] for p in head_positions)
            head_excursion = math.hypot(max_hx - min_hx, max_hy - min_hy)

            foot_ok = max_foot_drift <= 2.0
            if clip.motion_mode == "grounded_bob":
                head_ok = head_excursion >= 3.0
            elif clip.motion_mode == "still_hold":
                head_ok = head_excursion <= 0.50
                hold_max_excursion = max(hold_max_excursion, head_excursion)
            else:
                head_ok = True

            if not (foot_ok and head_ok):
                all_passed = False

            clip_metrics[clip.clip_key] = {
                "track_name": tr.track_name,
                "asset_id": clip.asset_id,
                "start_frame": clip.start_frame,
                "end_frame": clip.end_frame,
                "motion_mode": clip.motion_mode,
                "analytic_prediction": True,
                "max_analytic_foot_drift_px": max_foot_drift,
                "max_foot_drift_px": max_foot_drift,
                "head_excursion_px": head_excursion,
                "passed": foot_ok and head_ok,
            }

    return {
        "passed": all_passed,
        "evaluation_mode": "analytic_prediction",
        "hold_window": {
            "start_frame": hold_start,
            "end_frame": hold_end,
            "max_excursion_px": hold_max_excursion,
            "passed": hold_max_excursion <= 0.50,
        },
        "clips": clip_metrics,
    }
