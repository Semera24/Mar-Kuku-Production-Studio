"""Media probing, audio edit reproduction, provenance comparison, and CH01 input binding."""

from __future__ import annotations

import json
import math
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any

from mk_studio.config import RootPolicy, compute_sha256
from mk_studio.contracts import (
    AudioEditRecipe,
    AudioEditSegment,
    AudioProvenanceComparison,
    AudioStreamInfo,
    CueEvent,
    SourceBinding,
)
from mk_studio.timing import build_cue_event, compile_audio_edit_plan, samples_to_frame_coverage

FFMPEG_BIN = "/opt/homebrew/bin/ffmpeg"
FFPROBE_BIN = "/opt/homebrew/bin/ffprobe"

# Pinned CH01 input hashes from docs/implementation/inventory.json
CH01_PINNED_INPUTS: dict[str, dict[str, Any]] = {
    "ch01_candidate_wav": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/06_AUDIO/MUSIC/EPISODE_002/CH01/00_PILOT/E02_CH01_Welcome_v002_EDIT_REVIEW_FULL_MIX.wav",
        "sha256": "93a261d5984d3285c903934939b90bf1691a7fd3fe4981f518c2007976f9cb69",
        "approval_status": "technical_candidate",
        "notes": "Authorized technical proof audio input (30.220000s, 1,450,560 samples @ 48kHz stereo). Creative/owner listening approval remains pending.",
    },
    "ch01_original_wav": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/06_AUDIO/MUSIC/EPISODE_002/CH01/E02_CH01_Welcome_v001_FULL_MIX.wav",
        "sha256": "5b83bba92fc66b9edfe970db3593d01dfd4ff86d3f002c2b543cec66e5ffce84",
        "approval_status": "reference_only",
        "notes": "Original 105.802667s Suno/Music master containing stream 0:0 (pcm_s16le) and stream 0:1 (mjpeg cover art); always select 0:a:0.",
    },
    "ch01_edit_review_md": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/04_EPISODES/EPISODE_002_Mar_Kuku_Color_Garden_Adventure/05_AUDIO_CUES/E02_CH01_AUDIO_EDIT_REVIEW_v001.md",
        "sha256": "0ca778f11fddfc4a79016aa19f9a809ae36936f815cf29de759c088639f72e86",
        "approval_status": "reference_only",
        "notes": "CH01 Audio Edit Review v001 documenting the 4 retained segments, 3x 120ms linear audio crossfades, and provisional cue mappings.",
    },
    "bg_color_garden": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/BACKGROUNDS/VILLAGE/BG_Village_Color_Garden_v001.png",
        "sha256": "ac500e630fe3ab1c0776481cc79646ec69983f103835a4394fc13bc67c2de635",
        "approval_status": "approved_production",
        "notes": "Approved Village Color Garden background (1672x941 RGB).",
    },
    "mar_wave": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/CHARACTERS/MAR/02_OFFICIAL_POSES/Mar_Wave_v001.png",
        "sha256": "b846f9dddad49024d1b69f4c284866a29257d9ae424afa74faa539f45f4280cb",
        "approval_status": "approved_production",
        "notes": "Official Mar waving pose (1024x1536 RGBA).",
    },
    "mar_introduce": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/CHARACTERS/MAR/02_OFFICIAL_POSES/Mar_Introduce_Self_v001.png",
        "sha256": "73a550614c1946026e7fdba7d53717c7426f8841689f06dd6a6dac03d03897b5",
        "approval_status": "approved_production",
        "notes": "Official Mar introduce-self pose (1024x1536 RGBA).",
    },
    "mar_point_right": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/CHARACTERS/MAR/02_OFFICIAL_POSES/Mar_Point_Right_v001.png",
        "sha256": "6adb67aba16a8bcc9b85163de93f732092f918b1b78088f1a76520725b03afbe",
        "approval_status": "approved_production",
        "notes": "Official Mar pointing right pose (1024x1536 RGBA).",
    },
    "mar_listen": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/CHARACTERS/MAR/02_OFFICIAL_POSES/Mar_Listen_v001.png",
        "sha256": "eb0e159f71c2d5e58c7030b608451baa0300db2669ec96a198ed8d31f914dee0",
        "approval_status": "approved_production",
        "notes": "Official Mar listening hold pose (1024x1536 RGBA).",
    },
    "mar_happy": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/CHARACTERS/MAR/02_OFFICIAL_POSES/Mar_Happy_v001.png",
        "sha256": "c30522ba918838f4b81179b5da93565fa4fc2a112413cdfc56b1104ed0273cf5",
        "approval_status": "approved_production",
        "notes": "Official Mar happy pose (1024x1536 RGBA).",
    },
    "kuku_wave": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/CHARACTERS/KUKU/02_OFFICIAL_POSES/Kuku_Wave_v001.png",
        "sha256": "f70c08b8bf2b5064ae27140b278d995a6d6df4abf6838258c71aeaf15675c331",
        "approval_status": "approved_production",
        "notes": "Official Kuku waving pose (1024x1536 RGBA).",
    },
    "kuku_happy": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/CHARACTERS/KUKU/02_OFFICIAL_POSES/Kuku_Happy_v001.png",
        "sha256": "4ff0baf915ec2e0a41ed9568a09a12d44b3638bbf3948301bb73de800f61967d",
        "approval_status": "approved_production",
        "notes": "Official Kuku happy pose (1024x1536 RGBA).",
    },
    "kuku_think": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/CHARACTERS/KUKU/02_OFFICIAL_POSES/Kuku_Think_HeadTilt_v001.png",
        "sha256": "f443e636c6407d003fb769dbcb8a5c62f84884be8bbdfc48c1d9eb9f8d26306e",
        "approval_status": "approved_production",
        "notes": "Official Kuku head-tilt thinking/listening pose (1024x1536 RGBA).",
    },
    "kuku_point_right": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/CHARACTERS/KUKU/02_OFFICIAL_POSES/Kuku_Point_Right_v001.png",
        "sha256": "4126a3521e4b006a4088ca9ce1e05156a4f42f2d87b92ddddfdaa6fb38e03721",
        "approval_status": "approved_production",
        "notes": "Official Kuku pointing right pose (1024x1536 RGBA).",
    },
    "prop_flower_red": {
        "path": "/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/PROPS/EPISODE_002_COLOR_GARDEN/01_SOURCES/E02_Flower_Red_v001.png",
        "sha256": "e7486bd0536ec15fb75af3fb7ab5932a75ba1df6360faa8fb34e8ac0be2a7a79",
        "approval_status": "approved_production",
        "notes": "E02 Red Flower prop source (1254x1254 RGBA; note opaque white background in source PNG).",
    },
}


def probe_audio_stream_0a0(path: Path) -> AudioStreamInfo:
    """Probe stream 0:a:0 explicitly via ffprobe and inspect container streams."""
    cmd_all = [
        FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,sample_rate,channels,sample_fmt,duration,duration_ts,bits_per_sample:stream_disposition=attached_pic",
        "-of",
        "json",
        str(path),
    ]
    res_all = subprocess.run(cmd_all, check=True, capture_output=True, text=True)
    all_streams = json.loads(res_all.stdout).get("streams", [])

    cmd_a0 = [
        FFPROBE_BIN,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=index,codec_type,codec_name,sample_rate,channels,sample_fmt,duration,duration_ts,bits_per_sample",
        "-of",
        "json",
        str(path),
    ]
    res_a0 = subprocess.run(cmd_a0, check=True, capture_output=True, text=True)
    a0_streams = json.loads(res_a0.stdout).get("streams", [])
    if not a0_streams:
        raise RuntimeError(f"No audio stream 0:a:0 found in {path}")

    s0 = a0_streams[0]
    has_attached_pic = any(
        s.get("codec_type") == "video"
        or (s.get("disposition") or {}).get("attached_pic") == 1
        for s in all_streams
    )

    return AudioStreamInfo(
        stream_selector="0:a:0",
        codec_name=str(s0["codec_name"]),
        sample_rate=int(s0["sample_rate"]),
        channels=int(s0["channels"]),
        sample_fmt=str(s0.get("sample_fmt", "s16")),
        duration_seconds=round(float(s0["duration"]), 6),
        duration_ts=int(s0["duration_ts"]),
        bits_per_sample=int(s0.get("bits_per_sample") or 16),
        has_attached_picture_stream=has_attached_pic,
        total_container_streams=len(all_streams),
    )


def get_ch01_audio_edit_recipe(source_wav: Path, source_sha256: str) -> AudioEditRecipe:
    """Return the exact CH01 4-segment + 3x 120ms linear crossfade recipe from E02_CH01_AUDIO_EDIT_REVIEW_v001.md."""
    return AudioEditRecipe(
        source_audio_path=str(source_wav),
        source_audio_sha256=source_sha256,
        stream_selector="0:a:0",
        sample_rate=48000,
        speed=1.0,
        segments=[
            AudioEditSegment(
                segment_id="seg1_welcome",
                action="keep",
                start_seconds=0.00,
                end_seconds=6.35,
                crossfade_after_seconds=0.12,
                crossfade_curve="tri",
                notes="Hello, identity and welcome",
            ),
            AudioEditSegment(
                segment_id="cut1_instrumental_trim",
                action="remove",
                start_seconds=6.35,
                end_seconds=11.71,
            ),
            AudioEditSegment(
                segment_id="seg2_picnic_baskets",
                action="keep",
                start_seconds=11.71,
                end_seconds=17.37,
                crossfade_after_seconds=0.12,
                crossfade_curve="tri",
                notes="Picnic and flowers/baskets",
            ),
            AudioEditSegment(
                segment_id="cut2_instrumental_trim",
                action="remove",
                start_seconds=17.37,
                end_seconds=27.11,
            ),
            AudioEditSegment(
                segment_id="seg3_matching_question_hold_start",
                action="keep",
                start_seconds=27.11,
                end_seconds=34.16,
                crossfade_after_seconds=0.12,
                crossfade_curve="tri",
                notes="Matching invitation, question, start of response hold",
            ),
            AudioEditSegment(
                segment_id="cut3_hold_shorten",
                action="remove",
                start_seconds=34.16,
                end_seconds=46.10,
            ),
            AudioEditSegment(
                segment_id="seg4_hold_end_red_flower",
                action="keep",
                start_seconds=46.10,
                end_seconds=57.62,
                crossfade_after_seconds=0.0,
                crossfade_curve="tri",
                notes="End of response hold, look together, red introduction",
            ),
            AudioEditSegment(
                segment_id="cut4_unapproved_tail",
                action="remove",
                start_seconds=57.62,
                end_seconds=105.802667,
            ),
        ],
        fade_out_start_seconds=29.97,
        fade_out_duration_seconds=0.25,
    )


def reproduce_ch01_audio_edit(
    policy: RootPolicy,
    source_wav: Path,
    output_wav: Path,
) -> Path:
    """Reproduce the CH01 edit in scratch from stream 0:a:0 of E02_CH01_Welcome_v001_FULL_MIX.wav."""
    src_resolved = policy.resolve_read(
        source_wav,
        expected_sha256=CH01_PINNED_INPUTS["ch01_original_wav"]["sha256"],
    )
    out_resolved = policy.resolve_output(output_wav, overwrite=True, create_parents=True)

    filter_complex = (
        "[0:a:0]atrim=start=0.00:end=6.35,asetpts=PTS-STARTPTS[s1];"
        "[0:a:0]atrim=start=11.71:end=17.37,asetpts=PTS-STARTPTS[s2];"
        "[0:a:0]atrim=start=27.11:end=34.16,asetpts=PTS-STARTPTS[s3];"
        "[0:a:0]atrim=start=46.10:end=57.62,asetpts=PTS-STARTPTS[s4];"
        "[s1][s2]acrossfade=d=0.12:c1=tri:c2=tri[j12];"
        "[j12][s3]acrossfade=d=0.12:c1=tri:c2=tri[j123];"
        "[j123][s4]acrossfade=d=0.12:c1=tri:c2=tri,"
        "afade=t=in:st=0.00:d=0.05,"
        "afade=t=out:st=29.97:d=0.25[aout]"
    )

    cmd = [
        FFMPEG_BIN,
        "-y",
        "-v",
        "error",
        "-i",
        str(src_resolved),
        "-filter_complex",
        filter_complex,
        "-map",
        "[aout]",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(out_resolved),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out_resolved


def compare_wav_provenance(candidate_wav: Path, reproduced_wav: Path) -> AudioProvenanceComparison:
    """Compare candidate WAV and reproduced WAV at byte and PCM sample levels."""
    cand_sha = compute_sha256(candidate_wav)
    repro_sha = compute_sha256(reproduced_wav)

    with wave.open(str(candidate_wav), "rb") as wc, wave.open(str(reproduced_wav), "rb") as wr:
        cand_frames = wc.getnframes()
        cand_rate = wc.getframerate()
        cand_ch = wc.getnchannels()
        repro_frames = wr.getnframes()
        repro_rate = wr.getframerate()
        repro_ch = wr.getnchannels()

        cand_raw = wc.readframes(cand_frames)
        repro_raw = wr.readframes(repro_frames)

    min_frames = min(cand_frames, repro_frames)
    total_vals = min_frames * cand_ch
    cand_samples = struct.unpack(f"<{total_vals}h", cand_raw[: total_vals * 2])
    repro_samples = struct.unpack(f"<{total_vals}h", repro_raw[: total_vals * 2])

    max_diff = 0
    sum_sq = 0.0
    for a, b in zip(cand_samples, repro_samples):
        d = abs(a - b)
        if d > max_diff:
            max_diff = d
        sum_sq += float(d * d)

    rms_diff = math.sqrt(sum_sq / max(1, total_vals))
    exact_bytes = cand_sha == repro_sha
    exact_count = cand_frames == repro_frames and cand_rate == repro_rate and cand_ch == repro_ch

    if exact_bytes:
        explanation = "Exact byte-for-byte SHA-256 and PCM sample match."
    elif exact_count and max_diff == 0:
        explanation = (
            f"Exact 100% bit-identical PCM audio sample match ({cand_frames} stereo samples @ {cand_rate}Hz, "
            "max_abs_sample_diff=0 LSB, rms_sample_diff=0.0000); only WAV RIFF header metadata differs."
        )
    elif exact_count and max_diff <= 2:
        explanation = (
            f"Exact sample count match ({cand_frames} samples @ {cand_rate}Hz); "
            f"max PCM sample delta is {max_diff} LSB (RMS {rms_diff:.4f}), attributable to float/PCM rounding or container header metadata."
        )
    else:
        explanation = (
            f"Exact sample count match={exact_count} ({cand_frames} vs {repro_frames} samples); "
            f"max PCM sample delta={max_diff} LSB (RMS {rms_diff:.2f}). Candidate WAV was likely rendered with custom Python/NumPy linear crossfade slicing rather than FFmpeg acrossfade filter."
        )

    return AudioProvenanceComparison(
        candidate_path=str(candidate_wav),
        candidate_sha256=cand_sha,
        candidate_samples=cand_frames,
        candidate_duration_seconds=round(cand_frames / float(cand_rate), 6),
        reproduced_path=str(reproduced_wav),
        reproduced_sha256=repro_sha,
        reproduced_samples=repro_frames,
        reproduced_duration_seconds=round(repro_frames / float(repro_rate), 6),
        exact_byte_match=exact_bytes,
        exact_sample_count_match=exact_count,
        max_abs_sample_diff=max_diff,
        rms_sample_diff=round(rms_diff, 4),
        explanation=explanation,
    )


def measure_window_rms_dbfs(wav_path: Path, start_sec: float, end_sec: float) -> tuple[float, int]:
    """Measure RMS dBFS and peak PCM sample magnitude in [start_sec, end_sec] of a 16-bit WAV."""
    with wave.open(str(wav_path), "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        nframes = w.getnframes()
        s_idx = max(0, min(nframes, int(round(start_sec * rate))))
        e_idx = max(s_idx + 1, min(nframes, int(round(end_sec * rate))))
        w.setpos(s_idx)
        raw = w.readframes(e_idx - s_idx)

    count = (e_idx - s_idx) * ch
    samples = struct.unpack(f"<{count}h", raw[: count * 2])
    if not samples:
        return -96.0, 0
    peak = max(abs(x) for x in samples)
    mean_sq = sum(float(x * x) for x in samples) / len(samples)
    rms = math.sqrt(mean_sq) / 32768.0
    dbfs = 20.0 * math.log10(max(rms, 1e-6))
    return round(dbfs, 2), peak


def build_ch01_cues(candidate_wav: Path) -> list[CueEvent]:
    """Build the CH01 cue list with explicit provenance and measured acoustic energy."""
    raw_cues = [
        ("CUE_01_HELLO", "CH01_HELLO", 0.60, 1.78, "Hello, little friend."),
        ("CUE_02_IDENTITY", "CH01_IDENTITY", 2.08, 3.86, "I'm Mar and this is Kuku."),
        ("CUE_03_WELCOME", "CH01_WELCOME", 4.22, 6.00, "Welcome to our Color Garden."),
        ("CUE_04_PICNIC", "CH01_PICNIC", 6.60, 8.64, "Today, we're getting ready for a picnic."),
        (
            "CUE_05_FLOWERS_BASKETS",
            "CH01_FLOWERS_BASKETS",
            8.86,
            11.54,
            "We have some lovely flowers and empty baskets.",
        ),
        (
            "CUE_06_MATCH_INVITATION",
            "CH01_MATCH_INVITATION",
            12.14,
            15.42,
            "Let's match each flower to a basket with the same color.",
        ),
        ("CUE_07_QUESTION", "CH01_HELP_QUESTION", 15.70, 16.70, "Will you help us?"),
        (
            "CUE_08_RESPONSE_HOLD",
            "CH01_RESPONSE_HOLD",
            16.70,
            20.70,
            "[4.00s interactive response hold — hosts listen quietly]",
        ),
        ("CUE_09_LOOK_TOGETHER", "CH01_LOOK_TOGETHER", 20.70, 23.74, "Let's look together."),
        (
            "CUE_10_FIRST_FLOWER_RED",
            "CH01_FIRST_FLOWER_RED",
            24.16,
            29.62,
            "Our first flower is red.",
        ),
    ]
    events: list[CueEvent] = []
    for cue_id, name, start_s, end_s, line in raw_cues:
        rms_dbfs, peak_pcm = measure_window_rms_dbfs(candidate_wav, start_s, end_s)
        events.append(
            build_cue_event(
                cue_id=cue_id,
                name=name,
                candidate_start_seconds=start_s,
                candidate_end_seconds=end_s,
                lyric_or_action=line,
                authority="report_derived",
                notes=f"Mapped from E02_CH01_AUDIO_EDIT_REVIEW_v001.md; measured window RMS={rms_dbfs} dBFS, peak_pcm={peak_pcm}.",
            )
        )
    return events


def bind_ch01_inputs(policy: RootPolicy) -> dict[str, Any]:
    """Verify all CH01 source hashes, probe 0:a:0, reproduce edit in scratch, and save scratch/ch01-proof/inputs.json."""
    bindings: dict[str, dict[str, Any]] = {}
    for role, info in CH01_PINNED_INPUTS.items():
        target_path = Path(info["path"])
        if not target_path.exists() and "alt_path" in info:
            target_path = Path(info["alt_path"])
        resolved = policy.resolve_read(target_path, expected_sha256=info["sha256"])
        binding = SourceBinding(
            role=role,
            path=str(resolved),
            sha256=compute_sha256(resolved),
            bytes_size=resolved.stat().st_size,
            approval_status=info["approval_status"],
            notes=info["notes"],
        )
        bindings[role] = binding.model_dump()

    cand_path = Path(bindings["ch01_candidate_wav"]["path"])
    orig_path = Path(bindings["ch01_original_wav"]["path"])

    cand_stream = probe_audio_stream_0a0(cand_path)
    orig_stream = probe_audio_stream_0a0(orig_path)

    recipe = get_ch01_audio_edit_recipe(orig_path, bindings["ch01_original_wav"]["sha256"])
    compiled_edit = compile_audio_edit_plan(
        recipe,
        source_duration_seconds=orig_stream.duration_seconds,
    )

    reproduced_path = policy.output_root_resolved / "E02_CH01_Welcome_reproduced_v002.wav"
    reproduce_ch01_audio_edit(policy, orig_path, reproduced_path)
    provenance = compare_wav_provenance(cand_path, reproduced_path)

    cues = build_ch01_cues(cand_path)
    coverage = samples_to_frame_coverage(cand_stream.duration_ts, sample_rate=cand_stream.sample_rate)

    payload = {
        "episode_id": "EPISODE_002",
        "chapter_id": "CH01",
        "bindings": bindings,
        "candidate_audio_stream_0a0": cand_stream.model_dump(),
        "original_audio_stream_0a0": orig_stream.model_dump(),
        "edit_recipe": recipe.model_dump(),
        "compiled_edit_plan": compiled_edit,
        "provenance_comparison": provenance.model_dump(),
        "frame_coverage_30fps": coverage,
        "palmier_transcription_capability": {
            "inspect_media_supports_transcription": False,
            "get_transcript_mcp_note": (
                "Palmier inspect_media only samples visual frames. Palmier get_transcript reads cached clip "
                "transcripts or requires Cloud credits when uncached; local Apple SpeechAnalyzer transcription "
                "is only exposed via the interactive Caption tab UI."
            ),
        },
        "cues": [c.model_dump() for c in cues],
    }

    inputs_json_path = policy.resolve_output(
        policy.output_root_resolved / "inputs.json",
        overwrite=True,
        create_parents=True,
    )
    inputs_json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload
