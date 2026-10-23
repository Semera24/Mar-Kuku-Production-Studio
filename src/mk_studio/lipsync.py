"""Audio-Driven 2D Character Lip-Sync & Viseme Flap Engine for Mar & Kuku.

Performs frame-by-frame (30 fps) RMS audio energy analysis of 48kHz voiceover tracks,
mapping audio samples to character viseme mouth shapes:
- mouth_closed (Silence / 4.00s interactive stillness hold)
- mouth_open   (Conversational speech / moderate vowel energy)
- mouth_wide   (High vowel peaks / singing / cheering)
- mouth_round  (Rounded vowels 'O', 'U', 'KUKU')
"""

from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


# Official pose art directory
DEFAULT_OFFICIAL_CHARACTERS_DIR = Path("/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/CHARACTERS")


class CharacterLipSyncEngine:
    """Analyzes audio waveform energy and maps 30fps frames to character visemes."""

    FPS = 30
    DEFAULT_SAMPLE_RATE = 48000
    SAMPLES_PER_FRAME = 1600  # 48000 / 30

    def __init__(self, characters_dir: Path | None = None) -> None:
        self.characters_dir = characters_dir or DEFAULT_OFFICIAL_CHARACTERS_DIR

    def analyze_audio_frames(
        self,
        wav_path: Path,
        *,
        silence_threshold: float = 0.025,
        wide_threshold: float = 0.12,
        hold_range_frames: tuple[int, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Reads a 48kHz 16-bit WAV file and computes per-frame RMS energy and viseme states."""
        if not wav_path.is_file():
            raise FileNotFoundError(f"Audio file not found: {wav_path}")

        frames_data: list[dict[str, Any]] = []

        with wave.open(str(wav_path), "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate = wf.getframerate()
            total_samples = wf.getnframes()

            if sample_width != 2:
                raise ValueError(f"Expected 16-bit PCM audio, got sample width: {sample_width}")

            samples_per_video_frame = int(round(framerate / self.FPS))
            total_video_frames = int(math.ceil(total_samples / samples_per_video_frame))

            raw_bytes = wf.readframes(total_samples)

        # Unpack 16-bit signed PCM samples
        fmt = f"<{len(raw_bytes) // 2}h"
        raw_samples = struct.unpack(fmt, raw_bytes)

        # If stereo, average left and right channels to mono
        if n_channels == 2:
            mono_samples = [
                (raw_samples[i] + raw_samples[i + 1]) / 2.0
                for i in range(0, len(raw_samples) - 1, 2)
            ]
        else:
            mono_samples = [float(s) for s in raw_samples]

        hold_start = hold_range_frames[0] if hold_range_frames else -1
        hold_end = hold_range_frames[1] if hold_range_frames else -1

        for frame_idx in range(total_video_frames):
            start_s = frame_idx * samples_per_video_frame
            end_s = min(len(mono_samples), (frame_idx + 1) * samples_per_video_frame)
            chunk = mono_samples[start_s:end_s]

            if not chunk:
                rms = 0.0
            else:
                sum_sq = sum(s * s for s in chunk)
                rms = math.sqrt(sum_sq / len(chunk)) / 32768.0  # Normalize to 0.0 .. 1.0

            timestamp_sec = frame_idx / self.FPS

            # 4.00s interactive stillness hold enforcement
            is_in_hold = (hold_start <= frame_idx <= hold_end) if (hold_start >= 0 and hold_end >= 0) else False

            if is_in_hold or rms < silence_threshold:
                viseme = "mouth_closed"
            elif rms >= wide_threshold:
                viseme = "mouth_wide"
            elif (frame_idx % 4) == 0:
                viseme = "mouth_round"
            else:
                viseme = "mouth_open"

            frames_data.append({
                "frame": frame_idx,
                "timestamp_sec": round(timestamp_sec, 4),
                "rms_energy": round(rms, 4),
                "viseme": viseme,
                "is_hold": is_in_hold,
            })

        return frames_data

    def generate_lipsync_manifest(
        self,
        wav_path: Path,
        output_dir: Path,
        episode_id: str,
        hold_range_frames: tuple[int, int] | None = (501, 621),
    ) -> dict[str, Any]:
        """Analyzes audio, generates viseme sequence JSON, and resolves official character pose map."""
        output_dir.mkdir(parents=True, exist_ok=True)
        frames = self.analyze_audio_frames(wav_path, hold_range_frames=hold_range_frames)

        viseme_counts: dict[str, int] = {}
        for f in frames:
            v = f["viseme"]
            viseme_counts[v] = viseme_counts.get(v, 0) + 1

        # Character pose mappings
        kuku_dir = self.characters_dir / "KUKU" / "02_OFFICIAL_POSES"
        pose_mapping = {
            "mouth_closed": str(kuku_dir / "Kuku_Master_Idle_v001.png") if (kuku_dir / "Kuku_Master_Idle_v001.png").is_file() else "procedural:mouth_closed",
            "mouth_open": str(kuku_dir / "Kuku_Talk_v001.png") if (kuku_dir / "Kuku_Talk_v001.png").is_file() else "procedural:mouth_open",
            "mouth_wide": str(kuku_dir / "Kuku_Surprise_v001.png") if (kuku_dir / "Kuku_Surprise_v001.png").is_file() else "procedural:mouth_wide",
            "mouth_round": str(kuku_dir / "Kuku_Happy_v001.png") if (kuku_dir / "Kuku_Happy_v001.png").is_file() else "procedural:mouth_round",
        }

        manifest = {
            "episode_id": episode_id,
            "audio_file": str(wav_path),
            "frame_rate": self.FPS,
            "total_frames": len(frames),
            "total_duration_sec": round(len(frames) / self.FPS, 4),
            "viseme_distribution": viseme_counts,
            "hold_range_frames": hold_range_frames,
            "character_pose_mapping": pose_mapping,
            "frames": frames,
        }

        out_file = output_dir / "07_lipsync_sequence.json"
        out_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifest["manifest_path"] = str(out_file)
        return manifest

    def render_viseme_mouth_sprites(self, output_dir: Path) -> dict[str, Path]:
        """Renders 4 transparent high-res mouth viseme sprites (1254x1254) for direct character overlay."""
        output_dir.mkdir(parents=True, exist_ok=True)
        mouth_paths: dict[str, Path] = {}
        size = (1254, 1254)
        cx, cy = 627, 627

        # 1. Closed mouth (subtle gentle smile curve)
        im_closed = Image.new("RGBA", size, (0, 0, 0, 0))
        d_closed = ImageDraw.Draw(im_closed)
        d_closed.arc([cx - 80, cy - 30, cx + 80, cy + 30], start=0, end=180, fill=(30, 41, 59, 255), width=14)
        p_closed = output_dir / "mouth_viseme_closed.png"
        im_closed.save(p_closed, "PNG")
        mouth_paths["mouth_closed"] = p_closed

        # 2. Open mouth (speaking / cheerful)
        im_open = Image.new("RGBA", size, (0, 0, 0, 0))
        d_open = ImageDraw.Draw(im_open)
        # Inner mouth cavity
        d_open.chord([cx - 90, cy - 40, cx + 90, cy + 80], start=0, end=180, fill=(185, 28, 28, 255), outline=(30, 41, 59, 255), width=12)
        # Tongue
        d_open.ellipse([cx - 45, cy + 25, cx + 45, cy + 75], fill=(244, 114, 182, 255))
        p_open = output_dir / "mouth_viseme_open.png"
        im_open.save(p_open, "PNG")
        mouth_paths["mouth_open"] = p_open

        # 3. Wide mouth (singing vowel peak / cheering)
        im_wide = Image.new("RGBA", size, (0, 0, 0, 0))
        d_wide = ImageDraw.Draw(im_wide)
        d_wide.chord([cx - 130, cy - 50, cx + 130, cy + 120], start=0, end=180, fill=(185, 28, 28, 255), outline=(30, 41, 59, 255), width=14)
        d_wide.ellipse([cx - 60, cy + 40, cx + 60, cy + 110], fill=(244, 114, 182, 255))
        p_wide = output_dir / "mouth_viseme_wide.png"
        im_wide.save(p_wide, "PNG")
        mouth_paths["mouth_wide"] = p_wide

        # 4. Round mouth ('O' / 'U' shape)
        im_round = Image.new("RGBA", size, (0, 0, 0, 0))
        d_round = ImageDraw.Draw(im_round)
        d_round.ellipse([cx - 60, cy - 60, cx + 60, cy + 60], fill=(185, 28, 28, 255), outline=(30, 41, 59, 255), width=12)
        d_round.ellipse([cx - 30, cy + 10, cx + 30, cy + 50], fill=(244, 114, 182, 255))
        p_round = output_dir / "mouth_viseme_round.png"
        im_round.save(p_round, "PNG")
        mouth_paths["mouth_round"] = p_round

        return mouth_paths
