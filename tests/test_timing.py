"""Tests for deterministic 30/1 frame conversion, audio edit plans, and cue mapping."""

from __future__ import annotations

import pytest

from mk_studio.contracts import AudioEditRecipe, AudioEditSegment
from mk_studio.timing import (
    TimingError,
    build_cue_event,
    compile_audio_edit_plan,
    map_source_span_to_candidate,
    samples_to_frame_coverage,
    seconds_to_frame_half_up,
)


def test_half_up_frame_rounding_at_30fps() -> None:
    # 0.05s * 30 = 1.5 -> half-up rounds to 2 frames
    assert seconds_to_frame_half_up(0.05, 30, 1) == 2
    assert seconds_to_frame_half_up("0.05", 30, 1) == 2
    assert seconds_to_frame_half_up(0.0, 30, 1) == 0
    assert seconds_to_frame_half_up(0.60, 30, 1) == 18
    assert seconds_to_frame_half_up(2.08, 30, 1) == 62
    assert seconds_to_frame_half_up(4.22, 30, 1) == 127
    assert seconds_to_frame_half_up(15.70, 30, 1) == 471
    assert seconds_to_frame_half_up(20.70, 30, 1) == 621
    assert seconds_to_frame_half_up(24.16, 30, 1) == 725
    assert seconds_to_frame_half_up(30.22, 30, 1) == 907


def test_samples_to_frame_coverage_ch01() -> None:
    cov = samples_to_frame_coverage(1_450_560, sample_rate=48000, fps_num=30, fps_den=1)
    assert cov["total_frames"] == 907
    assert cov["audio_duration_seconds"] == 30.22
    assert cov["video_duration_seconds"] == 30.233333
    assert cov["visual_overhang_samples"] == 640
    assert cov["visual_overhang_ms"] == 13.333333


def test_ch01_edit_plan_trims_removals_and_crossfades() -> None:
    recipe = AudioEditRecipe(
        source_audio_path="/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/06_AUDIO/MUSIC/EPISODE_002/CH01/E02_CH01_Welcome_v001_FULL_MIX.wav",
        source_audio_sha256="5b83bba92fc66b9edfe970db3593d01dfd4ff86d3f002c2b543cec66e5ffce84",
        segments=[
            AudioEditSegment(
                segment_id="seg1_keep",
                action="keep",
                start_seconds=0.00,
                end_seconds=6.35,
                crossfade_after_seconds=0.12,
            ),
            AudioEditSegment(
                segment_id="gap1_remove",
                action="remove",
                start_seconds=6.35,
                end_seconds=11.71,
            ),
            AudioEditSegment(
                segment_id="seg2_keep",
                action="keep",
                start_seconds=11.71,
                end_seconds=17.37,
                crossfade_after_seconds=0.12,
            ),
            AudioEditSegment(
                segment_id="gap2_remove",
                action="remove",
                start_seconds=17.37,
                end_seconds=27.11,
            ),
            AudioEditSegment(
                segment_id="seg3_keep",
                action="keep",
                start_seconds=27.11,
                end_seconds=34.16,
                crossfade_after_seconds=0.12,
            ),
            AudioEditSegment(
                segment_id="gap3_remove",
                action="remove",
                start_seconds=34.16,
                end_seconds=46.10,
            ),
            AudioEditSegment(
                segment_id="seg4_keep",
                action="keep",
                start_seconds=46.10,
                end_seconds=57.62,
                crossfade_after_seconds=0.0,
            ),
            AudioEditSegment(
                segment_id="tail_remove",
                action="remove",
                start_seconds=57.62,
                end_seconds=105.802667,
            ),
        ],
        fade_out_start_seconds=29.97,
        fade_out_duration_seconds=0.25,
    )

    plan = compile_audio_edit_plan(recipe, source_duration_seconds=105.802667)
    assert plan["removed_count"] == 4
    assert plan["total_duration_seconds"] == 30.22
    assert plan["total_samples"] == 1_450_560
    starts = [s["candidate_start"] for s in plan["kept_segments"]]
    assert starts == [0.0, 6.23, 11.77, 18.70]

    # Check source-to-candidate mapping against E02_CH01_AUDIO_EDIT_REVIEW_v001.md
    assert map_source_span_to_candidate(0.60, 1.78, plan) == (0.60, 1.78)
    assert map_source_span_to_candidate(12.08, 14.12, plan) == (6.60, 8.64)
    assert map_source_span_to_candidate(27.48, 30.76, plan) == (12.14, 15.42)
    assert map_source_span_to_candidate(48.10, 51.14, plan) == (20.70, 23.74)
    assert map_source_span_to_candidate(51.56, 57.02, plan) == (24.16, 29.62)


def test_speed_change_rejected() -> None:
    recipe = AudioEditRecipe(
        source_audio_path="/tmp/a.wav",
        source_audio_sha256="abc",
        speed=1.05,
        segments=[
            AudioEditSegment(
                segment_id="s1",
                action="keep",
                start_seconds=0.0,
                end_seconds=5.0,
            )
        ],
    )
    with pytest.raises(TimingError, match="Speed changes are prohibited"):
        compile_audio_edit_plan(recipe)


def test_invalid_bounds_and_crossfade_rejected() -> None:
    with pytest.raises(TimingError, match="Negative timestamp"):
        seconds_to_frame_half_up(-0.1, 30, 1)

    bad_end = AudioEditRecipe(
        source_audio_path="/tmp/a.wav",
        source_audio_sha256="abc",
        segments=[
            AudioEditSegment(
                segment_id="s1",
                action="keep",
                start_seconds=5.0,
                end_seconds=4.0,
            )
        ],
    )
    with pytest.raises(TimingError, match="end .* <= start"):
        compile_audio_edit_plan(bad_end)

    exceeds_source = AudioEditRecipe(
        source_audio_path="/tmp/a.wav",
        source_audio_sha256="abc",
        segments=[
            AudioEditSegment(
                segment_id="s1",
                action="keep",
                start_seconds=0.0,
                end_seconds=20.0,
            )
        ],
    )
    with pytest.raises(TimingError, match="exceeds source duration"):
        compile_audio_edit_plan(exceeds_source, source_duration_seconds=10.0)

    bad_xfade = AudioEditRecipe(
        source_audio_path="/tmp/a.wav",
        source_audio_sha256="abc",
        segments=[
            AudioEditSegment(
                segment_id="s1",
                action="keep",
                start_seconds=0.0,
                end_seconds=1.0,
                crossfade_after_seconds=1.2,
            ),
            AudioEditSegment(
                segment_id="s2",
                action="keep",
                start_seconds=2.0,
                end_seconds=4.0,
            ),
        ],
    )
    with pytest.raises(TimingError, match="Crossfade"):
        compile_audio_edit_plan(bad_xfade)

    with pytest.raises(TimingError):
        build_cue_event(
            cue_id="c1",
            name="bad",
            candidate_start_seconds=5.0,
            candidate_end_seconds=5.0,
            lyric_or_action="bad",
        )
