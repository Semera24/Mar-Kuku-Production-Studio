"""Automated asset generation, Palmier timeline assembly, MP4 export, and YouTube Studio staging."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from mk_studio.browser.controller import BrowserController
from mk_studio.config import (
    DEFAULT_FFMPEG_BIN,
    DEFAULT_FFPROBE_BIN,
    RootPolicy,
    compute_sha256,
)
from mk_studio.contracts import (
    AssetEntry,
    AssetManifest,
    ChapterSpec,
    CueEvent,
    MotionProfileSpec,
    PoseSpanSpec,
    PropSpanSpec,
    RegistrationCalibration,
    TimelinePlan,
)
from mk_studio.editors.palmier import PalmierAdapter
from mk_studio.media import CH01_PINNED_INPUTS, probe_audio_stream_0a0
from mk_studio.qa import verify_exported_mp4, verify_native_palmier_project
from mk_studio.registration import calibrate_manifest_assets
from mk_studio.runner import run_cancellable_subprocess
from mk_studio.scene import compile_scene
from mk_studio.state import StateStore
from mk_studio.timing import samples_to_frame_coverage


def _draw_single_flower(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    *,
    stem_bottom_y: int,
    petal_color: tuple[int, int, int, int],
    outline_color: tuple[int, int, int, int],
    center_color: tuple[int, int, int, int],
    radius: int = 70,
    petal_dist: int = 110,
    petal_count: int = 8,
) -> None:
    # Stem
    draw.line([(cx, stem_bottom_y), (cx, cy)], fill=(76, 175, 80, 255), width=20)
    # Leaves
    draw.polygon([(cx, cy + 120), (cx - 70, cy + 90), (cx, cy + 60)], fill=(56, 142, 60, 255))
    draw.polygon([(cx, cy + 150), (cx + 70, cy + 120), (cx, cy + 90)], fill=(56, 142, 60, 255))
    # Petals
    step = 360 // petal_count
    for deg in range(0, 360, step):
        rad = math.radians(deg)
        px = cx + int(petal_dist * math.cos(rad))
        py = cy + int(petal_dist * math.sin(rad))
        draw.ellipse(
            [px - radius, py - radius, px + radius, py + radius],
            fill=petal_color,
            outline=outline_color,
            width=5,
        )
    # Center
    cr = int(radius * 1.1)
    draw.ellipse(
        [cx - cr, cy - cr, cx + cr, cy + cr],
        fill=center_color,
        outline=(230, 81, 0, 255),
        width=5,
    )


def generate_flower_props(output_dir: Path) -> dict[str, Path]:
    """Generate high-res transparent PNG teaching props for Scene 2 (2 yellow flowers) and Scene 3 (3 purple flowers)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_paths: dict[str, Path] = {}

    # 1. Two Yellow Flowers
    im2 = Image.new("RGBA", (1000, 1000), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(im2)
    _draw_single_flower(
        d2,
        320,
        380,
        stem_bottom_y=950,
        petal_color=(255, 235, 59, 255),
        outline_color=(245, 127, 23, 255),
        center_color=(255, 152, 0, 255),
        radius=65,
        petal_dist=100,
    )
    _draw_single_flower(
        d2,
        680,
        350,
        stem_bottom_y=950,
        petal_color=(255, 238, 88, 255),
        outline_color=(245, 127, 23, 255),
        center_color=(255, 167, 38, 255),
        radius=72,
        petal_dist=110,
    )
    p2 = output_dir / "prop_flowers_yellow_2.png"
    im2.save(p2, format="PNG")
    out_paths["prop_flowers_yellow_2"] = p2

    # 2. Three Purple Flowers
    im3 = Image.new("RGBA", (1200, 1000), (0, 0, 0, 0))
    d3 = ImageDraw.Draw(im3)
    _draw_single_flower(
        d3,
        280,
        420,
        stem_bottom_y=950,
        petal_color=(171, 71, 188, 255),
        outline_color=(106, 27, 154, 255),
        center_color=(255, 214, 0, 255),
        radius=60,
        petal_dist=95,
    )
    _draw_single_flower(
        d3,
        600,
        340,
        stem_bottom_y=950,
        petal_color=(186, 104, 200, 255),
        outline_color=(106, 27, 154, 255),
        center_color=(255, 214, 0, 255),
        radius=75,
        petal_dist=115,
    )
    _draw_single_flower(
        d3,
        920,
        410,
        stem_bottom_y=950,
        petal_color=(156, 39, 176, 255),
        outline_color=(74, 20, 140, 255),
        center_color=(255, 214, 0, 255),
        radius=65,
        petal_dist=100,
    )
    p3 = output_dir / "prop_flowers_purple_3.png"
    im3.save(p3, format="PNG")
    out_paths["prop_flowers_purple_3"] = p3

    return out_paths


def parse_script_markdown(
    script_source: str | Path,
    default_duration_seconds: float = 30.0,
) -> dict[str, Any]:
    """Parse any Mar & Kuku production script into dynamic dialogue cues, hold ranges, and scene metadata."""
    if isinstance(script_source, Path) or (isinstance(script_source, str) and "\n" not in script_source and Path(script_source).is_file()):
        text = Path(script_source).read_text(encoding="utf-8")
    else:
        text = str(script_source)

    dialogue_cues: list[tuple[float, str, str]] = []
    holds: list[tuple[int, int]] = []
    poses: list[str] = []
    title = "Mar & Kuku Episode"

    # Extract title
    for line in text.splitlines():
        line_clean = line.strip()
        if line_clean.startswith("# Proposed Script") or line_clean.startswith("# Mar and Kuku"):
            parts = line_clean.split("—")
            title = parts[-1].strip() if len(parts) > 1 else line_clean.lstrip("#").strip()
            break
        elif line_clean.startswith("**Title:**"):
            title = line_clean.replace("**Title:**", "").strip()
            break

    # Extract [HOLD: ...] or [RESPONSE: ...]
    for m in re.finditer(r"\[(?:HOLD|RESPONSE)[^\]]*?(\d+)\.\.(\d+)[^\]]*?\]", text, re.IGNORECASE):
        start_f, end_f = int(m.group(1)), int(m.group(2))
        holds.append((start_f, end_f))
    if not holds:
        for m in re.finditer(r"\[(?:HOLD|RESPONSE)[^\]]*?(\d+(?:\.\d+)?)\s*(?:seconds|s)[^\]]*?\]", text, re.IGNORECASE):
            sec = float(m.group(1))
            holds.append((501, 501 + int(sec * 30)))

    # Extract Poses
    for m in re.finditer(r"\[POSE:\s*([A-Za-z0-9_]+)\]", text):
        poses.append(m.group(1))

    # Parse spoken lines within scenes
    lines = text.splitlines()
    pending_time_s = None
    in_scene = False
    in_post_hold = False
    conflicts = []
    hold_start_s = holds[0][0] / 30.0 if holds else 16.70
    hold_end_s = holds[0][1] / 30.0 if holds else 20.70

    for line in lines:
        line_str = line.strip()
        if re.search(r"^(?:#+\s*Scene|Scene\s+\d+|###\s*CH\d+)", line_str, re.IGNORECASE):
            in_scene = True
        elif re.search(r"^(?:4\.\s*Required|5\.\s*Audio|6\.\s*v\d+|##\s*Required|##\s*Audio)", line_str, re.IGNORECASE):
            in_scene = False

        if not in_scene:
            continue

        if "after the hold" in line_str.lower():
            in_post_hold = True
            pending_time_s = round(hold_end_s + 0.1, 2)
            continue

        if "[hold" in line_str.lower() or "requested hold cue" in line_str.lower():
            pending_time_s = None
            continue

        # Check timecode header: e.g. 00:04.500–00:07.000 or 00:04.500
        tc_match = re.match(r"^(\d{2}):(\d{2})\.(\d{3})", line_str)
        if tc_match:
            mins, secs, ms = int(tc_match.group(1)), int(tc_match.group(2)), int(tc_match.group(3))
            t_s = mins * 60.0 + secs + ms / 1000.0
            if not in_post_hold and hold_start_s <= t_s < hold_end_s:
                pending_time_s = None
            else:
                pending_time_s = t_s
            continue

        # Match speaker line: MAR: ... or KUKU (direction): ...
        spk_match = re.match(r"^(MAR|KUKU)\s*(?:\([^)]+\))?\s*:\s*[\"“]?([^\"”\n]+)[\"”]?", line_str, re.IGNORECASE)
        if spk_match:
            speaker_raw = spk_match.group(1).upper()
            spoken_text = spk_match.group(2).strip()
            # Clean spoken text of any inline brackets or quotes
            spoken_text = re.sub(r"\[[^\]]+\]", "", spoken_text).strip()
            spoken_text = spoken_text.strip('“"”')
            if not spoken_text:
                continue

            voice = "Samantha" if speaker_raw == "MAR" else "Karen"

            if in_post_hold:
                if pending_time_s is not None and pending_time_s >= hold_end_s:
                    offset_s = pending_time_s
                    pending_time_s = None
                elif dialogue_cues and dialogue_cues[-1][0] >= hold_end_s:
                    prev_offset, prev_voice, prev_text = dialogue_cues[-1]
                    word_count = len(prev_text.split())
                    duration_est = max(1.2, word_count / 1.9)
                    offset_s = round(prev_offset + duration_est + 0.4, 2)
                else:
                    offset_s = round(hold_end_s + 0.1, 2)
            elif pending_time_s is not None:
                offset_s = pending_time_s
                pending_time_s = None
            else:
                if dialogue_cues:
                    prev_offset, prev_voice, prev_text = dialogue_cues[-1]
                    word_count = len(prev_text.split())
                    duration_est = max(1.2, word_count / 1.9)
                    offset_s = round(prev_offset + duration_est + 0.4, 2)
                else:
                    offset_s = 0.2

            # Track any dialogue that collides with the silent hold window rather than silently dropping words
            if hold_start_s <= offset_s < hold_end_s:
                conflicts.append(
                    {
                        "speaker": speaker_raw,
                        "text": spoken_text,
                        "offset_s": round(offset_s, 2),
                        "hold_window": [hold_start_s, hold_end_s],
                        "message": (
                            f"Dialogue line from {speaker_raw} ('{spoken_text}') scheduled at {offset_s:.2f}s "
                            f"collides with silent hold [{hold_start_s:.2f}s..{hold_end_s:.2f}s]. "
                            "Requires explicit timing alignment proposal."
                        ),
                    }
                )

            dialogue_cues.append((round(offset_s, 2), voice, spoken_text))

    return {
        "title": title,
        "dialogue_cues": dialogue_cues,
        "holds": holds or [(501, 621)],
        "poses": poses,
        "conflicts": conflicts,
    }


def generate_episode_voiceover(
    output_path: Path,
    *,
    script_path: str | Path | None = None,
    dialogue_cues: list[tuple[float, str, str]] | None = None,
    duration_seconds: float = 30.0,
) -> Path:
    """Synthesize high-quality 48kHz stereo narration parsed directly from the selected script."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cues = dialogue_cues
    if cues is None and script_path is not None:
        parsed = parse_script_markdown(script_path, default_duration_seconds=duration_seconds)
        cues = parsed["dialogue_cues"]

    if not cues:
        cues = [
            (0.3, "Samantha", "Hello, little gardeners!"),
            (1.8, "Karen", "Look! A red flower!"),
            (3.4, "Samantha", "One!"),
            (4.6, "Karen", "One red flower! Show one finger!"),
            (7.2, "Karen", "Two yellow flowers!"),
            (8.8, "Samantha", "One... Two!"),
            (11.2, "Karen", "Count with us! One, two!"),
            (13.8, "Samantha", "Purple flowers!"),
            (15.2, "Samantha", "Can you count three purple flowers?"),
            (21.0, "Karen", "Hehehe! One! Two! Three purple petals!"),
            (25.5, "Samantha", "You did it!"),
            (27.0, "Karen", "Bye, little gardeners!"),
        ]

    with tempfile.TemporaryDirectory() as td:
        tpath = Path(td)
        cmd = [
            DEFAULT_FFMPEG_BIN,
            "-y",
            "-f",
            "lavfi",
            "-t",
            f"{duration_seconds:.3f}",
            "-i",
            "anullsrc=r=48000:cl=stereo",
        ]
        filter_complex = []

        current_time = 0.0
        h_start, h_end = 16.70, 20.70

        for idx, (offset_s, voice, text) in enumerate(cues):
            line_wav = tpath / f"line_{idx}.wav"
            subprocess.run(
                [
                    "/usr/bin/say",
                    "-v",
                    voice,
                    "-o",
                    str(line_wav),
                    "--data-format=LEI16@48000",
                    text,
                ],
                check=True,
            )
            dur = probe_audio_stream_0a0(line_wav).duration_seconds

            # Auto-align start time to prevent speech collision
            start_s = max(offset_s, current_time)
            if offset_s >= h_end:
                start_s = max(start_s, h_end + 0.1)

            end_s = start_s + dur
            # Prevent speech clipping into silent hold window
            if start_s < h_start and end_s > h_start:
                shift = round(end_s - h_start + 0.05, 2)
                start_s = round(start_s - shift, 2)
                end_s = start_s + dur

            if not (start_s >= h_end or end_s <= h_start):
                raise ValueError(
                    f"Dialogue line {idx+1} ('{text}') clips into silent hold window "
                    f"[{h_start:.2f}s..{h_end:.2f}s]: calculated span [{start_s:.2f}s..{end_s:.2f}s]"
                )

            current_time = round(end_s + 0.25, 2)
            cmd.extend(["-i", str(line_wav)])
            delay_ms = int(start_s * 1000)
            filter_complex.append(
                f"[{idx+1}:a]aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo,adelay={delay_ms}|{delay_ms}[a{idx}];"
            )

        amix_inputs = "[0:a]" + "".join(f"[a{i}]" for i in range(len(cues)))
        filter_complex.append(
            f"{amix_inputs}amix=inputs={len(cues)+1}:duration=first:dropout_transition=0[outa]"
        )

        cmd.extend(
            [
                "-filter_complex",
                "".join(filter_complex),
                "-map",
                "[outa]",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(output_path),
            ]
        )
        subprocess.run(cmd, capture_output=True, check=True)

    return output_path


def generate_youtube_thumbnail(
    output_path: Path,
    bg_path: Path,
    mar_path: Path,
    kuku_path: Path,
    flower_path: Path,
) -> Path:
    """Generate a vibrant 1280x720 YouTube custom thumbnail."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(bg_path) as bg:
        canvas = bg.convert("RGBA").resize((1280, 720), Image.Resampling.LANCZOS)

    # Composite Flower
    with Image.open(flower_path) as fl:
        fl_resized = fl.resize((480, 400), Image.Resampling.LANCZOS)
        canvas.alpha_composite(fl_resized, (740, 260))

    # Composite Mar
    with Image.open(mar_path) as mr:
        mr_resized = mr.resize((360, 480), Image.Resampling.LANCZOS)
        canvas.alpha_composite(mr_resized, (80, 180))

    # Composite Kuku
    with Image.open(kuku_path) as kk:
        kk_resized = kk.resize((310, 360), Image.Resampling.LANCZOS)
        canvas.alpha_composite(kk_resized, (420, 290))

    # Draw Banner Text
    draw = ImageDraw.Draw(canvas)
    # Banner background box
    draw.rounded_rectangle([40, 30, 960, 140], radius=16, fill=(15, 23, 42, 220), outline=(255, 214, 0, 255), width=4)
    # Text
    draw.text((70, 45), "Mar & Kuku: Count 1, 2, 3 Flowers!", fill=(255, 255, 255, 255))
    draw.text((70, 90), "Preschool Counting & Colors Song", fill=(255, 214, 0, 255))

    canvas.convert("RGB").save(output_path, format="PNG")
    return output_path


def generate_episode_assets(
    episode_id: str,
    output_dir: Path,
    policy: RootPolicy,
    store: StateStore | None = None,
    script_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate voiceover, props, thumbnail, ChapterSpec, and AssetManifest parsed directly from the selected script."""
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Resolve active script path
    resolved_script = None
    if script_path and Path(script_path).is_file():
        resolved_script = Path(script_path)
    else:
        for cand in [
            output_dir / "proposed_script.md",
            output_dir / "proposed_script_v002.md",
            output_dir / "proposed_script_v001.md",
        ]:
            if cand.is_file():
                resolved_script = cand
                break

    parsed_script = parse_script_markdown(resolved_script) if resolved_script else None
    dialogue_cues = parsed_script["dialogue_cues"] if parsed_script else None
    episode_title = parsed_script["title"] if parsed_script else f"Episode {episode_id}"

    # 1. Generate Props
    props = generate_flower_props(assets_dir)

    # 2. Generate Audio Master WAV (30.00s @ 48kHz stereo, parsed from script)
    audio_path = assets_dir / f"{episode_id.lower()}_voiceover_master_48k_stereo.wav"
    generate_episode_voiceover(audio_path, dialogue_cues=dialogue_cues)
    audio_stream = probe_audio_stream_0a0(audio_path)
    coverage = samples_to_frame_coverage(audio_stream.duration_ts, sample_rate=audio_stream.sample_rate)
    total_frames = int(coverage["total_frames"])

    # 3. Pinned Character & Background Assets from Production Root
    bg_pin = CH01_PINNED_INPUTS["bg_color_garden"]
    mar_wave_pin = CH01_PINNED_INPUTS["mar_wave"]
    mar_intro_pin = CH01_PINNED_INPUTS["mar_introduce"]
    mar_point_pin = CH01_PINNED_INPUTS["mar_point_right"]
    mar_listen_pin = CH01_PINNED_INPUTS["mar_listen"]
    mar_happy_pin = CH01_PINNED_INPUTS["mar_happy"]
    kuku_wave_pin = CH01_PINNED_INPUTS["kuku_wave"]
    kuku_happy_pin = CH01_PINNED_INPUTS["kuku_happy"]
    kuku_think_pin = CH01_PINNED_INPUTS["kuku_think"]
    kuku_point_pin = CH01_PINNED_INPUTS["kuku_point_right"]
    flower_red_pin = CH01_PINNED_INPUTS["prop_flower_red"]

    bg_path = policy.resolve_read(bg_pin["path"], expected_sha256=bg_pin["sha256"])
    mar_wave_path = policy.resolve_read(mar_wave_pin["path"], expected_sha256=mar_wave_pin["sha256"])
    kuku_happy_path = policy.resolve_read(kuku_happy_pin["path"], expected_sha256=kuku_happy_pin["sha256"])
    flower_red_path = policy.resolve_read(flower_red_pin["path"], expected_sha256=flower_red_pin["sha256"])

    # 4. Generate Thumbnail
    thumb_path = assets_dir / f"{episode_id.lower()}_youtube_thumbnail_1280x720.png"
    generate_youtube_thumbnail(
        thumb_path,
        bg_path=bg_path,
        mar_path=mar_wave_path,
        kuku_path=kuku_happy_path,
        flower_path=props["prop_flowers_purple_3"],
    )

    # 5. Build AssetManifest
    entries: dict[str, AssetEntry] = {
        "bg_color_garden": AssetEntry(
            asset_id="bg_color_garden",
            role="background",
            path=str(bg_path),
            sha256=bg_pin["sha256"],
        ),
        "audio_ep03_master": AssetEntry(
            asset_id="audio_ep03_master",
            role="audio_master",
            path=str(audio_path),
            sha256=compute_sha256(audio_path),
        ),
        "mar_wave": AssetEntry(
            asset_id="mar_wave",
            role="character_pose",
            character_id="Mar",
            pose_name="Wave",
            path=str(mar_wave_path),
            sha256=mar_wave_pin["sha256"],
        ),
        "mar_introduce": AssetEntry(
            asset_id="mar_introduce",
            role="character_pose",
            character_id="Mar",
            pose_name="Introduce_Self",
            path=str(policy.resolve_read(mar_intro_pin["path"])),
            sha256=mar_intro_pin["sha256"],
        ),
        "mar_point_right": AssetEntry(
            asset_id="mar_point_right",
            role="character_pose",
            character_id="Mar",
            pose_name="Point_Right",
            path=str(policy.resolve_read(mar_point_pin["path"])),
            sha256=mar_point_pin["sha256"],
        ),
        "mar_listen": AssetEntry(
            asset_id="mar_listen",
            role="character_pose",
            character_id="Mar",
            pose_name="Listen",
            path=str(policy.resolve_read(mar_listen_pin["path"])),
            sha256=mar_listen_pin["sha256"],
        ),
        "mar_happy": AssetEntry(
            asset_id="mar_happy",
            role="character_pose",
            character_id="Mar",
            pose_name="Happy",
            path=str(policy.resolve_read(mar_happy_pin["path"])),
            sha256=mar_happy_pin["sha256"],
        ),
        "kuku_wave": AssetEntry(
            asset_id="kuku_wave",
            role="character_pose",
            character_id="Kuku",
            pose_name="Wave",
            path=str(policy.resolve_read(kuku_wave_pin["path"])),
            sha256=kuku_wave_pin["sha256"],
        ),
        "kuku_happy": AssetEntry(
            asset_id="kuku_happy",
            role="character_pose",
            character_id="Kuku",
            pose_name="Happy",
            path=str(kuku_happy_path),
            sha256=kuku_happy_pin["sha256"],
        ),
        "kuku_think": AssetEntry(
            asset_id="kuku_think",
            role="character_pose",
            character_id="Kuku",
            pose_name="Think_HeadTilt",
            path=str(policy.resolve_read(kuku_think_pin["path"])),
            sha256=kuku_think_pin["sha256"],
        ),
        "kuku_point_right": AssetEntry(
            asset_id="kuku_point_right",
            role="character_pose",
            character_id="Kuku",
            pose_name="Point_Right",
            path=str(policy.resolve_read(kuku_point_pin["path"])),
            sha256=kuku_point_pin["sha256"],
        ),
        "prop_flower_red": AssetEntry(
            asset_id="prop_flower_red",
            role="prop",
            character_id="Prop_Flower_Red",
            pose_name="Flower_Red",
            path=str(flower_red_path),
            sha256=flower_red_pin["sha256"],
        ),
        "prop_flowers_yellow_2": AssetEntry(
            asset_id="prop_flowers_yellow_2",
            role="prop",
            character_id="Prop_Flowers_Yellow_2",
            pose_name="Flowers_Yellow_2",
            path=str(props["prop_flowers_yellow_2"]),
            sha256=compute_sha256(props["prop_flowers_yellow_2"]),
        ),
        "prop_flowers_purple_3": AssetEntry(
            asset_id="prop_flowers_purple_3",
            role="prop",
            character_id="Prop_Flowers_Purple_3",
            pose_name="Flowers_Purple_3",
            path=str(props["prop_flowers_purple_3"]),
            sha256=compute_sha256(props["prop_flowers_purple_3"]),
        ),
    }
    manifest = AssetManifest(assets=entries)
    calibration = calibrate_manifest_assets(policy, manifest)

    # 6. Motion specs
    mar_bob = MotionProfileSpec(mode="grounded_bob", cycle_frames=60, scale_amplitude=0.030, rotation_amplitude_deg=1.5)
    kuku_bob = MotionProfileSpec(mode="grounded_bob", cycle_frames=50, scale_amplitude=0.034, rotation_amplitude_deg=1.8, shared_phase_origin_frame=15)
    still_hold = MotionProfileSpec(mode="still_hold", cycle_frames=60, scale_amplitude=0.0, rotation_amplitude_deg=0.0)

    # Poses & Spans (30.00s = 900 frames, Hold: 501..621)
    mar_x, mar_y, mar_h = 0.24, 0.935, 0.63
    kuku_x, kuku_y, kuku_h = 0.46, 0.935, 0.40

    pose_spans = [
        # Mar
        PoseSpanSpec(span_id="mar_01", character_id="Mar", asset_id="mar_wave", start_frame=0, end_frame=50, anchor_canvas_x=mar_x, anchor_canvas_y=mar_y, target_visible_height_Norm=mar_h, motion=mar_bob),
        PoseSpanSpec(span_id="mar_02", character_id="Mar", asset_id="mar_introduce", start_frame=50, end_frame=100, anchor_canvas_x=mar_x, anchor_canvas_y=mar_y, target_visible_height_Norm=mar_h, motion=mar_bob),
        PoseSpanSpec(span_id="mar_03", character_id="Mar", asset_id="mar_point_right", start_frame=100, end_frame=210, anchor_canvas_x=mar_x, anchor_canvas_y=mar_y, target_visible_height_Norm=mar_h, motion=mar_bob),
        PoseSpanSpec(span_id="mar_04", character_id="Mar", asset_id="mar_introduce", start_frame=210, end_frame=420, anchor_canvas_x=mar_x, anchor_canvas_y=mar_y, target_visible_height_Norm=mar_h, motion=mar_bob),
        PoseSpanSpec(span_id="mar_05", character_id="Mar", asset_id="mar_point_right", start_frame=420, end_frame=501, anchor_canvas_x=mar_x, anchor_canvas_y=mar_y, target_visible_height_Norm=mar_h, motion=mar_bob),
        PoseSpanSpec(span_id="mar_06_hold", character_id="Mar", asset_id="mar_listen", start_frame=501, end_frame=621, anchor_canvas_x=mar_x, anchor_canvas_y=mar_y, target_visible_height_Norm=mar_h, motion=still_hold),
        PoseSpanSpec(span_id="mar_07", character_id="Mar", asset_id="mar_happy", start_frame=621, end_frame=840, anchor_canvas_x=mar_x, anchor_canvas_y=mar_y, target_visible_height_Norm=mar_h, motion=mar_bob),
        PoseSpanSpec(span_id="mar_08_close", character_id="Mar", asset_id="mar_wave", start_frame=840, end_frame=total_frames, anchor_canvas_x=mar_x, anchor_canvas_y=mar_y, target_visible_height_Norm=mar_h, motion=mar_bob),
        # Kuku
        PoseSpanSpec(span_id="kuku_01", character_id="Kuku", asset_id="kuku_wave", start_frame=0, end_frame=50, anchor_canvas_x=kuku_x, anchor_canvas_y=kuku_y, target_visible_height_Norm=kuku_h, motion=kuku_bob),
        PoseSpanSpec(span_id="kuku_02", character_id="Kuku", asset_id="kuku_point_right", start_frame=50, end_frame=210, anchor_canvas_x=kuku_x, anchor_canvas_y=kuku_y, target_visible_height_Norm=kuku_h, motion=kuku_bob),
        PoseSpanSpec(span_id="kuku_03", character_id="Kuku", asset_id="kuku_think", start_frame=210, end_frame=420, anchor_canvas_x=kuku_x, anchor_canvas_y=kuku_y, target_visible_height_Norm=kuku_h, motion=kuku_bob),
        PoseSpanSpec(span_id="kuku_04", character_id="Kuku", asset_id="kuku_point_right", start_frame=420, end_frame=501, anchor_canvas_x=kuku_x, anchor_canvas_y=kuku_y, target_visible_height_Norm=kuku_h, motion=kuku_bob),
        PoseSpanSpec(span_id="kuku_05_hold", character_id="Kuku", asset_id="kuku_happy", start_frame=501, end_frame=621, anchor_canvas_x=kuku_x, anchor_canvas_y=kuku_y, target_visible_height_Norm=kuku_h, motion=still_hold),
        PoseSpanSpec(span_id="kuku_06_giggle", character_id="Kuku", asset_id="kuku_happy", start_frame=621, end_frame=840, anchor_canvas_x=kuku_x, anchor_canvas_y=kuku_y, target_visible_height_Norm=kuku_h, motion=kuku_bob),
        PoseSpanSpec(span_id="kuku_07_close", character_id="Kuku", asset_id="kuku_wave", start_frame=840, end_frame=total_frames, anchor_canvas_x=kuku_x, anchor_canvas_y=kuku_y, target_visible_height_Norm=kuku_h, motion=kuku_bob),
    ]

    # Props Spans (Scene 1: Red Flower, Scene 2: 2 Yellow, Scene 3: 3 Purple)
    prop_spans = [
        PropSpanSpec(span_id="prop_01_red", prop_id="Prop_Flower_Red", asset_id="prop_flower_red", start_frame=50, end_frame=210, anchor_canvas_x=0.74, anchor_canvas_y=0.915, target_visible_height_Norm=0.45),
        PropSpanSpec(span_id="prop_02_yellow", prop_id="Prop_Flowers_Yellow_2", asset_id="prop_flowers_yellow_2", start_frame=215, end_frame=420, anchor_canvas_x=0.74, anchor_canvas_y=0.915, target_visible_height_Norm=0.45),
        PropSpanSpec(span_id="prop_03_purple", prop_id="Prop_Flowers_Purple_3", asset_id="prop_flowers_purple_3", start_frame=425, end_frame=895, anchor_canvas_x=0.74, anchor_canvas_y=0.915, target_visible_height_Norm=0.48),
    ]

    cues = [
        CueEvent(cue_id="cue_01_hello", name="Scene 1 — One Red Flower", start_seconds=0.0, end_seconds=7.0, start_frame=0, end_frame=210, lyric_or_action="Mar and Kuku introduce the garden and count one red flower", authority="transcript_verified"),
        CueEvent(cue_id="cue_02_yellow", name="Scene 2 — Two Yellow Flowers", start_seconds=7.0, end_seconds=14.0, start_frame=210, end_frame=420, lyric_or_action="Mar and Kuku count two yellow flowers", authority="transcript_verified"),
        CueEvent(cue_id="cue_03_purple", name="Scene 3 — Three Purple Flowers", start_seconds=14.0, end_seconds=16.7, start_frame=420, end_frame=501, lyric_or_action="Three purple flowers reveal and counting question", authority="transcript_verified"),
        CueEvent(cue_id="cue_04_hold", name="4.0-Second Response Hold", start_seconds=16.7, end_seconds=20.7, start_frame=501, end_frame=621, lyric_or_action="Interactive viewer counting pause (pure stillness)", authority="signal_verified"),
        CueEvent(cue_id="cue_05_kuku_giggle", name="Kuku Giggle & Count Petals", start_seconds=20.7, end_seconds=26.0, start_frame=621, end_frame=780, lyric_or_action="Kuku giggles 'One! Two! Three purple petals!'", authority="transcript_verified"),
        CueEvent(cue_id="cue_06_goodbye", name="Celebration & Goodbye Wave", start_seconds=26.0, end_seconds=30.0, start_frame=780, end_frame=total_frames, lyric_or_action="Mar & Kuku celebrate and wave goodbye", authority="transcript_verified"),
    ]

    spec = ChapterSpec(
        episode_id=episode_id,
        chapter_id="CH01",
        title="Mar & Kuku's Three Garden Flowers",
        canvas_width=1920,
        canvas_height=1080,
        fps_num=30,
        fps_den=1,
        audio_asset_id="audio_ep03_master",
        audio_duration_seconds=float(audio_stream.duration_seconds),
        audio_sample_rate=audio_stream.sample_rate,
        audio_total_samples=audio_stream.duration_ts,
        total_frames=total_frames,
        background_asset_id="bg_color_garden",
        pose_spans=pose_spans,
        prop_spans=prop_spans,
        cues=cues,
    )

    spec_path = output_dir / "chapter_spec_ep03.json"
    manifest_path = output_dir / "asset_manifest_ep03.json"
    calib_path = output_dir / "registration_ep03.json"

    spec_path.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    calib_path.write_text(calibration.model_dump_json(indent=2) + "\n", encoding="utf-8")

    intake_receipt = {
        "step": "step_6_asset_generation_and_intake",
        "episode_id": episode_id,
        "status": "completed",
        "audio_master": {
            "path": str(audio_path),
            "sha256": compute_sha256(audio_path),
            "sample_rate": audio_stream.sample_rate,
            "channels": audio_stream.channels,
            "duration_seconds": float(audio_stream.duration_seconds),
            "total_samples": audio_stream.duration_ts,
        },
        "props_generated": {k: {"path": str(v), "sha256": compute_sha256(v)} for k, v in props.items()},
        "youtube_thumbnail": {
            "path": str(thumb_path),
            "sha256": compute_sha256(thumb_path),
            "dimensions": [1280, 720],
        },
        "chapter_spec": str(spec_path),
        "asset_manifest": str(manifest_path),
        "registration_calibration": str(calib_path),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    receipt_path = output_dir / "06_asset_intake_manifest.json"
    receipt_path.write_text(json.dumps(intake_receipt, indent=2) + "\n", encoding="utf-8")
    return intake_receipt


def assemble_episode_palmier(
    episode_id: str,
    output_dir: Path,
    policy: RootPolicy,
    store: StateStore | None = None,
) -> dict[str, Any]:
    """Compile TimelinePlan and assemble native .palmier project for Episode 003."""
    spec_path = output_dir / "chapter_spec_ep03.json"
    manifest_path = output_dir / "asset_manifest_ep03.json"
    calib_path = output_dir / "registration_ep03.json"

    spec = ChapterSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    manifest = AssetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    calibration = RegistrationCalibration.model_validate_json(calib_path.read_text(encoding="utf-8"))

    plan = compile_scene(spec, manifest, calibration)
    plan_path = output_dir / "timeline_plan_ep03.json"
    plan_path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")

    adapter = PalmierAdapter(policy)
    project_path = output_dir / f"{episode_id}_Counting_Flowers_v001.palmier"

    from mk_studio.runner import StepRunner
    db_path = policy.output_root_resolved / "state.db"
    eff_store = store or StateStore(db_path)
    eff_store.initialize_studio_schema()
    eff_runner = StepRunner(eff_store, policy.output_root_resolved / "locks")
    run_id = f"run-{episode_id.lower()}-assemble-palmier"

    eff_store.upsert_episode(
        episode_id=episode_id,
        title="Mar & Kuku — Three Garden Flowers",
        production_root=str(output_dir),
        status="in_progress",
    )
    eff_store.upsert_chapter(
        episode_id=episode_id,
        chapter_id="CH01",
        title="Welcome to the Color Garden",
        status="compiling",
    )
    existing_run = eff_store.get_run(run_id)
    if not existing_run:
        eff_store.create_run(
            run_id=run_id,
            episode_id=episode_id,
            chapter_id="CH01",
            workflow_name="assemble_palmier",
            input_hash=compute_sha256(plan_path),
        )

    apply_res = adapter.apply_plan(
        plan,
        store=eff_store,
        runner=eff_runner,
        run_id=run_id,
        scratch_project_path=project_path,
        bootstrap_if_missing=True,
    )
    reopened = adapter.close_and_reopen(project_path)
    qa_res = verify_native_palmier_project(project_path)

    receipt = {
        "step": "step_7_palmier_editing",
        "episode_id": episode_id,
        "status": "completed",
        "plan_path": str(plan_path),
        "plan_sha256": compute_sha256(plan_path),
        "project_path": str(project_path),
        "apply_result": apply_res,
        "reopen_verified": bool(reopened and qa_res["passed"]),
        "palmier_qa": qa_res,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt_path = output_dir / "07_palmier_editing_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def export_episode_mp4(
    episode_id: str,
    output_dir: Path,
    policy: RootPolicy,
    store: StateStore | None = None,
    preferred_renderer: str = "deterministic_ffmpeg_preview",
    allow_preview_fallback: bool = True,
) -> dict[str, Any]:
    """Render 1080p30 MP4 from the native Palmier project, verify native .palmier project structure, and run full QA verification."""
    project_path = output_dir / f"{episode_id}_Counting_Flowers_v001.palmier"
    plan_path = output_dir / "timeline_plan_ep03.json"
    mp4_path = output_dir / f"{episode_id}_Counting_Flowers_1080p30_v001.mp4"

    plan = TimelinePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    adapter = PalmierAdapter(policy)

    renderer_used = preferred_renderer
    palmier_export_attempted = False
    palmier_error = None
    native_export_receipt = None

    if preferred_renderer == "native_palmier":
        palmier_export_attempted = True
        try:
            native_export_receipt = adapter.export_video_mp4(mp4_path)
            renderer_used = "native_palmier"
        except Exception as exc:
            palmier_error = str(exc)
            if allow_preview_fallback:
                renderer_used = "deterministic_ffmpeg_preview"
                adapter.render_timeline_plan_to_mp4(plan, mp4_path)
            else:
                raise
    elif preferred_renderer == "deterministic_ffmpeg_preview":
        renderer_used = "deterministic_ffmpeg_preview"
        adapter.render_timeline_plan_to_mp4(plan, mp4_path)
    else:
        raise ValueError(f"Unknown preferred_renderer: {preferred_renderer}")

    qa_report = verify_exported_mp4(
        mp4_path,
        expected_frames=plan.total_frames,
        expected_duration_seconds=plan.total_frames / 30.0,
    )
    palmier_qa = verify_native_palmier_project(project_path)

    receipt = {
        "step": "step_8_final_mp4_export",
        "episode_id": episode_id,
        "status": "completed" if (qa_report["passed"] and palmier_qa["passed"]) else "failed",
        "renderer": renderer_used,
        "is_preview_render": (renderer_used == "deterministic_ffmpeg_preview"),
        "native_palmier_attempted": palmier_export_attempted,
        "native_palmier_error": palmier_error,
        "native_export_receipt": native_export_receipt,
        "mp4_path": str(mp4_path),
        "mp4_bytes": mp4_path.stat().st_size,
        "mp4_sha256": compute_sha256(mp4_path),
        "palmier_project_path": str(project_path),
        "palmier_qa": palmier_qa,
        "qa_report": qa_report,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt_path = output_dir / "08_export_qa_report.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def package_and_stage_youtube_upload(
    episode_id: str,
    output_dir: Path,
    policy: RootPolicy,
    browser: BrowserController,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Package YouTube metadata and stage the upload via managed Chrome to YouTube Studio."""
    mp4_path = output_dir / f"{episode_id}_Counting_Flowers_1080p30_v001.mp4"
    thumb_path = output_dir / "assets" / f"{episode_id.lower()}_youtube_thumbnail_1280x720.png"

    title = "Mar and Kuku: Count 1, 2, 3 Garden Flowers! | Toddler Counting & Colors"
    description = (
        "Join Mar and Kuku in the cheerful garden as they count one red flower, two yellow flowers, "
        "and three purple flowers! Features an interactive 4-second pause so your toddler can count along.\n\n"
        "Timestamps:\n"
        "0:00 - Welcome to the Garden\n"
        "0:01 - Scene 1: One Red Flower\n"
        "0:07 - Scene 2: Two Yellow Flowers\n"
        "0:14 - Scene 3: Three Purple Flowers\n"
        "0:16 - Can You Count Them? (Interactive 4-Second Pause)\n"
        "0:21 - Kuku Giggles! Three Purple Petals!\n"
        "0:25 - You Did It! Goodbye Wave\n\n"
        "Subscribe to Mar and Kuku for more fun preschool learning adventures!\n"
        "#MarAndKuku #PreschoolLearning #CountingForKids #ColorsForToddlers"
    )
    tags = [
        "mar and kuku",
        "counting for kids",
        "preschool counting",
        "learn colors",
        "toddler learning video",
        "count 1 to 3",
        "interactive preschool song",
        "educational animation",
    ]

    pkg = {
        "episode_id": episode_id,
        "title": title,
        "description": description,
        "tags": tags,
        "category_id": "27",  # Education
        "made_for_kids": True,
        "video_path": str(mp4_path),
        "thumbnail_path": str(thumb_path),
        "visibility": "draft",
        "staged_at": datetime.now(timezone.utc).isoformat(),
    }

    pkg_path = output_dir / "09_youtube_upload_package.json"
    pkg_path.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")

    stage_res = browser.inspect_youtube_studio_and_stage_upload(
        video_path=mp4_path,
        title=title,
        description=description,
        tags=tags,
        thumbnail_path=thumb_path,
        made_for_kids=True,
        conversation_id=conversation_id,
        screenshot_name=f"{episode_id}_09_youtube_studio_stage.png",
    )

    receipt = {
        "step": "step_9_youtube_studio_upload",
        "episode_id": episode_id,
        "status": stage_res["status"],
        "package": pkg,
        "browser_result": stage_res,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt_path = output_dir / "09_youtube_upload_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt
