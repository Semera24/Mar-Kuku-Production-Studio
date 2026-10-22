"""Deterministic 30/1 frame conversion, audio edit timing math, and cue mapping."""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from mk_studio.contracts import AudioEditRecipe, AudioEditSegment, CueEvent


class TimingError(ValueError):
    """Raised when timing parameters, edit segments, or speed changes are invalid."""


def seconds_to_frame_half_up(
    seconds: float | int | str | Decimal,
    fps_num: int = 30,
    fps_den: int = 1,
) -> int:
    """Convert seconds to integer frame index using deterministic half-up rounding at fps_num/fps_den."""
    if fps_num <= 0 or fps_den <= 0:
        raise TimingError(f"Invalid frame rate {fps_num}/{fps_den}")
    sec_dec = Decimal(str(seconds))
    if sec_dec < Decimal("0"):
        raise TimingError(f"Negative timestamp is not allowed: {seconds}")
    exact = (sec_dec * Decimal(fps_num)) / Decimal(fps_den)
    return int((exact + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))


def samples_to_frame_coverage(
    total_samples: int,
    sample_rate: int = 48000,
    fps_num: int = 30,
    fps_den: int = 1,
) -> dict[str, int | float]:
    """Compute minimum integer video frame count covering total_samples without truncating audio."""
    if total_samples <= 0 or sample_rate <= 0:
        raise TimingError(f"Invalid sample count ({total_samples}) or sample_rate ({sample_rate})")
    if fps_num <= 0 or fps_den <= 0:
        raise TimingError(f"Invalid frame rate {fps_num}/{fps_den}")

    num = total_samples * fps_num
    den = sample_rate * fps_den
    total_frames = (num + den - 1) // den

    audio_seconds = total_samples / float(sample_rate)
    video_seconds = (total_frames * fps_den) / float(fps_num)
    video_equiv_samples = int(round((total_frames * fps_den * sample_rate) / float(fps_num)))
    overhang_samples = video_equiv_samples - total_samples
    overhang_ms = (video_seconds - audio_seconds) * 1000.0

    return {
        "total_samples": total_samples,
        "sample_rate": sample_rate,
        "audio_duration_seconds": round(audio_seconds, 6),
        "total_frames": total_frames,
        "video_duration_seconds": round(video_seconds, 6),
        "visual_overhang_samples": overhang_samples,
        "visual_overhang_ms": round(overhang_ms, 6),
    }


def compile_audio_edit_plan(
    recipe: AudioEditRecipe,
    *,
    source_duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Validate an AudioEditRecipe and compute exact kept timeline offsets and sample counts."""
    if not math.isclose(recipe.speed, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise TimingError(
            f"Speed changes are prohibited for vocal/music masters (got speed={recipe.speed})"
        )
    if recipe.sample_rate <= 0:
        raise TimingError(f"Invalid sample rate: {recipe.sample_rate}")
    if not recipe.segments:
        raise TimingError("AudioEditRecipe must contain at least one segment")

    prev_end = 0.0
    kept_segments: list[AudioEditSegment] = []
    removed_segments: list[AudioEditSegment] = []

    for seg in recipe.segments:
        if seg.start_seconds < -1e-9:
            raise TimingError(f"Segment {seg.segment_id} has negative start: {seg.start_seconds}")
        if seg.end_seconds <= seg.start_seconds:
            raise TimingError(
                f"Segment {seg.segment_id} has end ({seg.end_seconds}) <= start ({seg.start_seconds})"
            )
        if seg.start_seconds < prev_end - 1e-6:
            raise TimingError(
                f"Segment {seg.segment_id} overlaps previous segment end ({seg.start_seconds} < {prev_end})"
            )
        if source_duration_seconds is not None and seg.end_seconds > source_duration_seconds + 1e-3:
            raise TimingError(
                f"Segment {seg.segment_id} end ({seg.end_seconds}) exceeds source duration ({source_duration_seconds})"
            )
        if seg.crossfade_after_seconds < 0.0:
            raise TimingError(
                f"Segment {seg.segment_id} has negative crossfade: {seg.crossfade_after_seconds}"
            )
        prev_end = seg.end_seconds
        if seg.action == "keep":
            kept_segments.append(seg)
        else:
            removed_segments.append(seg)

    if not kept_segments:
        raise TimingError("AudioEditRecipe must retain at least one 'keep' segment")

    timeline_Cursor = Decimal("0")
    compiled_kept: list[dict[str, Any]] = []

    for idx, seg in enumerate(kept_segments):
        seg_in = Decimal(str(seg.start_seconds))
        seg_out = Decimal(str(seg.end_seconds))
        seg_dur = seg_out - seg_in
        xfade = Decimal(str(seg.crossfade_after_seconds)) if idx < len(kept_segments) - 1 else Decimal("0")

        if xfade >= seg_dur:
            raise TimingError(
                f"Crossfade ({xfade}s) on segment {seg.segment_id} is >= segment duration ({seg_dur}s)"
            )
        if idx < len(kept_segments) - 1:
            next_seg = kept_segments[idx + 1]
            next_dur = Decimal(str(next_seg.end_seconds)) - Decimal(str(next_seg.start_seconds))
            if xfade >= next_dur:
                raise TimingError(
                    f"Crossfade ({xfade}s) after {seg.segment_id} is >= next segment duration ({next_dur}s)"
                )

        cand_start = timeline_Cursor
        cand_end = cand_start + seg_dur
        compiled_kept.append(
            {
                "segment_id": seg.segment_id,
                "original_in": float(seg_in),
                "original_out": float(seg_out),
                "duration_seconds": float(seg_dur),
                "candidate_start": float(cand_start),
                "candidate_end": float(cand_end),
                "crossfade_after_seconds": float(xfade),
                "crossfade_curve": seg.crossfade_curve,
                "notes": seg.notes,
            }
        )
        timeline_Cursor = cand_end - xfade

    total_duration_seconds = float(timeline_Cursor)
    total_samples = int(round(Decimal(str(total_duration_seconds)) * Decimal(recipe.sample_rate)))

    if recipe.fade_out_start_seconds is not None:
        if recipe.fade_out_start_seconds < 0 or recipe.fade_out_start_seconds > total_duration_seconds + 1e-6:
            raise TimingError(
                f"Fade-out start ({recipe.fade_out_start_seconds}) outside compiled duration ({total_duration_seconds})"
            )

    coverage = samples_to_frame_coverage(total_samples, sample_rate=recipe.sample_rate)

    return {
        "kept_segments": compiled_kept,
        "removed_count": len(removed_segments),
        "total_duration_seconds": round(total_duration_seconds, 6),
        "total_samples": total_samples,
        "sample_rate": recipe.sample_rate,
        "frame_coverage": coverage,
    }


def map_source_span_to_candidate(
    orig_start: float,
    orig_end: float,
    compiled_plan: dict[str, Any],
) -> tuple[float, float]:
    """Map an original source [orig_start, orig_end] span into candidate seconds through the compiled edit plan."""
    if orig_end <= orig_start:
        raise TimingError(f"Invalid source span [{orig_start}, {orig_end}]")
    for seg in compiled_plan["kept_segments"]:
        if orig_start >= seg["original_in"] - 1e-6 and orig_end <= seg["original_out"] + 1e-6:
            offset_in = Decimal(str(orig_start)) - Decimal(str(seg["original_in"]))
            offset_out = Decimal(str(orig_end)) - Decimal(str(seg["original_in"]))
            cand_start = Decimal(str(seg["candidate_start"])) + offset_in
            cand_end = Decimal(str(seg["candidate_start"])) + offset_out
            return float(round(cand_start, 6)), float(round(cand_end, 6))
    raise TimingError(
        f"Source span [{orig_start}, {orig_end}] is not contained within any single kept segment"
    )


def build_cue_event(
    *,
    cue_id: str,
    name: str,
    candidate_start_seconds: float,
    candidate_end_seconds: float,
    lyric_or_action: str,
    authority: str = "report_derived",
    notes: str = "",
    fps_num: int = 30,
    fps_den: int = 1,
) -> CueEvent:
    """Create a CueEvent with deterministic 30/1 frame boundaries."""
    if candidate_end_seconds <= candidate_start_seconds:
        raise TimingError(
            f"Cue {cue_id} end ({candidate_end_seconds}) <= start ({candidate_start_seconds})"
        )
    start_frame = seconds_to_frame_half_up(candidate_start_seconds, fps_num=fps_num, fps_den=fps_den)
    end_frame = seconds_to_frame_half_up(candidate_end_seconds, fps_num=fps_num, fps_den=fps_den)
    return CueEvent(
        cue_id=cue_id,
        name=name,
        start_seconds=candidate_start_seconds,
        end_seconds=candidate_end_seconds,
        start_frame=start_frame,
        end_frame=max(start_frame + 1, end_frame),
        lyric_or_action=lyric_or_action,
        authority=authority,  # type: ignore[arg-type]
        notes=notes,
    )

