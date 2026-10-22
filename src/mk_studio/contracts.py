"""Episode-neutral Pydantic contracts for inputs, registration, scene compilation, and editor plans."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base contract with strict extra-field rejection."""

    model_config = ConfigDict(extra="forbid")


class SourceBinding(StrictModel):
    """Pinned read-only file reference with verified SHA-256."""

    role: str
    path: str
    sha256: str
    bytes_size: int
    approval_status: Literal[
        "approved_production",
        "technical_candidate",
        "creative_approved",
        "listening_approved",
        "reference_only",
    ]
    notes: str = ""


class AudioStreamInfo(StrictModel):
    """Probed audio stream metadata from ffprobe (stream 0:a:0)."""

    stream_selector: str = "0:a:0"
    codec_name: str
    sample_rate: int
    channels: int
    sample_fmt: str
    duration_seconds: float
    duration_ts: int
    bits_per_sample: int = 16
    has_attached_picture_stream: bool = False
    total_container_streams: int = 1


class AudioEditSegment(StrictModel):
    """A kept or removed segment in an audio edit decision list."""

    segment_id: str
    action: Literal["keep", "remove"]
    start_seconds: float
    end_seconds: float
    crossfade_after_seconds: float = 0.0
    crossfade_curve: Literal["tri", "qsin", "hsin", "esin", "log", "par"] = "tri"
    notes: str = ""


class AudioEditRecipe(StrictModel):
    """Deterministic FFmpeg audio edit recipe."""

    source_audio_path: str
    source_audio_sha256: str
    stream_selector: str = "0:a:0"
    sample_rate: int = 48000
    speed: float = 1.0
    segments: list[AudioEditSegment]
    fade_out_start_seconds: float | None = None
    fade_out_duration_seconds: float = 0.0


class AudioProvenanceComparison(StrictModel):
    """Comparison between candidate WAV and FFmpeg-reproduced WAV from original master."""

    candidate_path: str
    candidate_sha256: str
    candidate_samples: int
    candidate_duration_seconds: float
    reproduced_path: str
    reproduced_sha256: str
    reproduced_samples: int
    reproduced_duration_seconds: float
    exact_byte_match: bool
    exact_sample_count_match: bool
    max_abs_sample_diff: int
    rms_sample_diff: float
    explanation: str


class CueEvent(StrictModel):
    """Audio cue event with explicit provenance (signal_verified vs report_derived)."""

    cue_id: str
    name: str
    start_seconds: float
    end_seconds: float
    start_frame: int
    end_frame: int
    lyric_or_action: str
    authority: Literal["signal_verified", "report_derived", "transcript_verified"]
    notes: str = ""


class AlphaBounds(StrictModel):
    """Pixel bounding box [left, top, right_exclusive, bottom_exclusive] at a given alpha threshold."""

    threshold: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


class PoseRegistration(StrictModel):
    """Calibrated visual bounds and support-foot contact landmarks for a character/prop PNG."""

    asset_id: str
    character_or_prop: str
    pose_name: str
    path: str
    sha256: str
    image_width: int
    image_height: int
    has_transparency: bool
    alpha_bounds_t1: AlphaBounds
    alpha_bounds_t128: AlphaBounds
    alpha_bounds_t254: AlphaBounds
    left_foot_x: float
    left_foot_y: float
    right_foot_x: float
    right_foot_y: float
    support_foot_x: float
    support_foot_y: float
    head_top_x: float
    head_top_y: float
    reference_visible_height_px: float
    transparent_pixel_count: int = 0
    alpha_min: int = 0
    alpha_max: int = 255
    landmark_method: str = "visually_verified_interfoot_gap"


class RegistrationCalibration(StrictModel):
    """Collection of calibrated poses and props for a scene."""

    canvas_width: int = 1920
    canvas_height: int = 1080
    poses: dict[str, PoseRegistration]


class AssetEntry(StrictModel):
    """Asset manifest entry."""

    asset_id: str
    role: Literal["background", "character_pose", "prop", "audio_master"]
    character_id: str | None = None
    pose_name: str | None = None
    path: str
    sha256: str


class AssetManifest(StrictModel):
    """Episode-neutral manifest of assets available to a chapter compilation."""

    assets: dict[str, AssetEntry]


class MotionProfileSpec(StrictModel):
    """Grounded motion parameters for a pose span."""

    mode: Literal["grounded_bob", "still_hold", "prop_pop_idle"] = "grounded_bob"
    cycle_frames: int = 60
    scale_amplitude: float = 0.028
    rotation_amplitude_deg: float = 1.6
    sway_amplitude_px: float = 4.0
    shared_phase_origin_frame: int = 0


class PoseSpanSpec(StrictModel):
    """A single character pose span on a character track."""

    span_id: str
    character_id: str
    asset_id: str
    start_frame: int
    end_frame: int
    anchor_canvas_x: float
    anchor_canvas_y: float
    target_visible_height_Norm: float
    motion: MotionProfileSpec = Field(default_factory=MotionProfileSpec)
    cue_id: str | None = None


class PropSpanSpec(StrictModel):
    """A teaching prop appearance span on the props track."""

    span_id: str
    prop_id: str
    asset_id: str
    start_frame: int
    end_frame: int
    anchor_canvas_x: float
    anchor_canvas_y: float
    target_visible_height_Norm: float
    motion: MotionProfileSpec = Field(
        default_factory=lambda: MotionProfileSpec(
            mode="prop_pop_idle",
            cycle_frames=45,
            scale_amplitude=0.035,
            rotation_amplitude_deg=2.0,
            sway_amplitude_px=3.0,
        )
    )
    cue_id: str | None = None


class ChapterSpec(StrictModel):
    """Episode-neutral specification for compiling a chapter timeline."""

    episode_id: str
    chapter_id: str
    title: str
    canvas_width: int = 1920
    canvas_height: int = 1080
    fps_num: int = 30
    fps_den: int = 1
    audio_asset_id: str
    audio_duration_seconds: float
    audio_sample_rate: int = 48000
    audio_total_samples: int
    total_frames: int
    background_asset_id: str
    background_scale_start: float = 1.015
    background_scale_end: float = 1.035
    pose_spans: list[PoseSpanSpec]
    prop_spans: list[PropSpanSpec] = Field(default_factory=list)
    cues: list[CueEvent] = Field(default_factory=list)


class KeyframeRow(StrictModel):
    """Normalized Palmier keyframe row."""

    frame_offset: int
    values: list[float]
    interpolation: Literal["linear", "hold", "smooth"] = "smooth"

    def to_palmier_row(self) -> list[int | float | str]:
        return [self.frame_offset, *self.values, self.interpolation]


class StaticTransformPlan(StrictModel):
    """Static Palmier clip transform in 0..1 normalized canvas coordinates."""

    centerX: float = 0.5
    centerY: float = 0.5
    width: float = 1.0
    height: float = 1.0
    rotation: float = 0.0
    flipHorizontal: bool = False
    flipVertical: bool = False


class ClipPlan(StrictModel):
    """Deterministic clip specification in a compiled TimelinePlan."""

    clip_key: str
    track_name: str
    track_type: Literal["video", "audio"]
    asset_id: str
    source_path: str
    source_sha256: str
    start_frame: int
    end_frame: int
    duration_frames: int
    transform: StaticTransformPlan = Field(default_factory=StaticTransformPlan)
    position_keyframes: list[KeyframeRow] = Field(default_factory=list)
    scale_keyframes: list[KeyframeRow] = Field(default_factory=list)
    rotation_keyframes: list[KeyframeRow] = Field(default_factory=list)
    opacity_keyframes: list[KeyframeRow] = Field(default_factory=list)
    volume_db: float = 0.0
    fade_in_frames: int = 0
    fade_out_frames: int = 0
    anchor_canvas_x: float | None = None
    anchor_canvas_y: float | None = None
    motion_mode: str = "static"


class TrackPlan(StrictModel):
    """Deterministic track in a compiled TimelinePlan (index 0 is topmost visual track)."""

    track_index: int
    track_name: str
    track_type: Literal["video", "audio"]
    clips: list[ClipPlan]


class MarkerPlan(StrictModel):
    """Timeline marker specification."""

    marker_key: str
    name: str
    start_frame: int
    duration_frames: int = 0
    comment: str = ""
    color: str = "#3B82F6"


class TimelinePlan(StrictModel):
    """Complete deterministic editor-neutral timeline plan."""

    plan_id: str
    episode_id: str
    chapter_id: str
    timeline_name: str
    canvas_width: int = 1920
    canvas_height: int = 1080
    fps: int = 30
    total_frames: int
    audio_duration_seconds: float
    audio_total_samples: int
    video_duration_seconds: float
    visual_overhang_ms: float
    visual_overhang_samples: int
    tracks: list[TrackPlan]
    markers: list[MarkerPlan]

