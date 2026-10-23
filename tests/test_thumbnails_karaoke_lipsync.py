"""Unit tests for ThumbnailGenerator, KaraokeSubtitleGenerator, and CharacterLipSyncEngine."""

import wave
from pathlib import Path

from mk_studio.lipsync import CharacterLipSyncEngine
from mk_studio.subtitles import KaraokeSubtitleGenerator
from mk_studio.thumbnails import ThumbnailGenerator


def test_thumbnail_generator_variants(tmp_path: Path):
    gen = ThumbnailGenerator()
    manifest = gen.generate_all_variants(
        episode_title="Test Fruit Song",
        subtitle="16:9 Toddler Learning",
        output_dir=tmp_path / "thumbs",
    )
    assert manifest["aspect_ratio"] == "16:9"
    assert manifest["resolution"] == "1920x1080"
    assert len(manifest["variants"]) == 3
    for v in manifest["variants"]:
        p = Path(v["file_path"])
        assert p.is_file()
        assert p.stat().st_size > 0
    assert (tmp_path / "thumbs" / "09_youtube_thumbnail.png").is_file()
    assert (tmp_path / "thumbs" / "09_thumbnail_manifest.json").is_file()


def test_karaoke_subtitle_generator(tmp_path: Path):
    sub_gen = KaraokeSubtitleGenerator()
    cues = [
        {"start": 0.5, "end": 4.0, "text": "Look at the red apple!"},
        {"start": 4.5, "end": 8.5, "text": "Can you see it?", "is_interactive": True},
    ]
    res = sub_gen.generate_subtitles(cues, output_dir=tmp_path / "subs", stem_name="test_karaoke")
    assert res["status"] == "generated"
    assert res["cue_count"] == 2

    ass_content = Path(res["ass_path"]).read_text(encoding="utf-8")
    assert "PlayResX: 1920" in ass_content
    assert "PlayResY: 1080" in ass_content
    assert r"{\k" in ass_content
    assert "InteractiveQuestion" in ass_content

    srt_content = Path(res["srt_path"]).read_text(encoding="utf-8")
    assert "00:00:00,500 --> 00:00:04,000" in srt_content

    capcut_tracks = sub_gen.convert_to_capcut_text_track(cues)
    assert len(capcut_tracks) == 2
    assert capcut_tracks[0]["type"] == "text"
    assert capcut_tracks[1]["animation"]["in_animation"] == "bounce_in"


def test_character_lipsync_engine(tmp_path: Path):
    # Create fake 48kHz 16-bit PCM mono WAV (1 second = 30 video frames)
    wav_path = tmp_path / "test_voice.wav"
    sample_rate = 48000
    total_samples = sample_rate  # 1 sec

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Alternate silence and audio pulses
        raw_data = bytearray()
        for i in range(total_samples):
            # First 0.5s silence (0), second 0.5s 440Hz sine wave tone
            if i < total_samples // 2:
                sample_val = 0
            else:
                sample_val = int(12000 * (1 if (i % 50) < 25 else -1))
            raw_data.extend(sample_val.to_bytes(2, byteorder="little", signed=True))
        wf.writeframes(raw_data)

    engine = CharacterLipSyncEngine()
    manifest = engine.generate_lipsync_manifest(
        wav_path=wav_path,
        output_dir=tmp_path / "lipsync",
        episode_id="EP_TEST",
    )
    assert manifest["total_frames"] == 30
    assert "viseme_distribution" in manifest
    # Should have closed frames in the first half and open/wide frames in second half
    assert manifest["viseme_distribution"]["mouth_closed"] > 0
    assert (tmp_path / "lipsync" / "07_lipsync_sequence.json").is_file()

    mouth_sprites = engine.render_viseme_mouth_sprites(tmp_path / "visemes")
    assert len(mouth_sprites) == 4
    for _, sprite_path in mouth_sprites.items():
        assert sprite_path.is_file()
