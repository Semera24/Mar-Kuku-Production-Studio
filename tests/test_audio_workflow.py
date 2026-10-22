"""Unit tests for the Google Music -> Manus -> ChatGPT -> Listening Approval -> Palmier workflow."""

from pathlib import Path
import pytest

from mk_studio.audio import (
    AudioIntakeArtifact,
    TimedDialogueSegment,
    approve_listening_ticket,
    compile_palmier_cues_from_approval,
    create_listening_approval_ticket,
    intake_audio_master,
    review_and_align_with_chatgpt,
    transcribe_with_manus,
)


def test_audio_intake_provenance(tmp_path: Path) -> None:
    # Use existing verified WAV
    source_wav = Path("scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav")
    assert source_wav.is_file()

    target_wav = tmp_path / "intake_master_48k.wav"
    artifact = intake_audio_master(
        source_wav,
        target_wav,
        source_service="Google Music (MusicFX/Lyria)",
        source_url="https://aitestkitchen.withgoogle.com/tools/music-fx",
    )

    assert artifact.source_service == "Google Music (MusicFX/Lyria)"
    assert artifact.source_url == "https://aitestkitchen.withgoogle.com/tools/music-fx"
    assert artifact.file_path == str(target_wav)
    assert artifact.sample_rate == 48000
    assert artifact.channels == 2
    assert artifact.duration_seconds > 0.0
    assert len(artifact.sha256) == 64
    assert not artifact.is_test_provider


def test_manus_timestamped_transcription_parsing(tmp_path: Path) -> None:
    source_wav = Path("scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav")
    artifact = intake_audio_master(source_wav, tmp_path / "m.wav")

    mock_manus_response = """
Here is the timestamped transcription:
[00:00.500 - 00:03.200] MAR: Hello little friends!
[00:04.100 - 00:06.800] KUKU: Welcome to the color garden!
[00:14.200 - 00:16.200] MAR: Can you count three purple flowers?
[00:21.000 - 00:23.500] KUKU: You found them! Wonderful job!
"""
    transcript = transcribe_with_manus(artifact, mock_transcript_text=mock_manus_response)
    assert len(transcript.timed_segments) == 4
    assert transcript.timed_segments[0].speaker == "MAR"
    assert transcript.timed_segments[0].start_s == 0.500
    assert transcript.timed_segments[0].end_s == 3.200
    assert transcript.timed_segments[1].speaker == "KUKU"
    assert transcript.timed_segments[2].start_s == 14.200
    assert transcript.timed_segments[3].start_s == 21.000


def test_chatgpt_hold_conflict_detection(tmp_path: Path) -> None:
    script_file = tmp_path / "test_script.md"
    script_file.write_text(
        """# Scene 1
MAR: "Hello friends!"
KUKU: "Look at the flowers!"
""",
        encoding="utf-8",
    )

    source_wav = Path("scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav")
    artifact = intake_audio_master(source_wav, tmp_path / "t.wav")

    # 1. Collision scenario: speech inside 16.70s - 20.70s
    colliding_response = """
[00:02.000 - 00:04.000] MAR: Hello friends!
[00:17.500 - 00:19.500] KUKU: I am speaking inside the hold!
"""
    bad_transcript = transcribe_with_manus(artifact, mock_transcript_text=colliding_response)
    bad_review = review_and_align_with_chatgpt(bad_transcript, script_file)

    assert not bad_review.hold_window_verified
    assert bad_review.alignment_status == "conflicts_detected"
    assert len(bad_review.conflicts) == 1
    assert "collides with" in bad_review.conflicts[0]["message"] or "spans into" in bad_review.conflicts[0]["message"]

    # 2. Clean scenario: speech before 16.70s and after 20.70s
    clean_response = """
[00:02.000 - 00:04.000] MAR: Hello friends!
[00:14.000 - 00:16.000] MAR: Look closely!
[00:21.000 - 00:23.000] KUKU: Wonderful!
"""
    good_transcript = transcribe_with_manus(artifact, mock_transcript_text=clean_response)
    good_review = review_and_align_with_chatgpt(good_transcript, script_file)

    assert good_review.hold_window_verified
    assert good_review.alignment_status == "aligned"
    assert len(good_review.conflicts) == 0


def test_listening_ticket_lifecycle(tmp_path: Path) -> None:
    script_file = tmp_path / "test_script.md"
    script_file.write_text("# Scene 1\nMAR: Hello!\n", encoding="utf-8")

    source_wav = Path("scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav")
    artifact = intake_audio_master(source_wav, tmp_path / "master.wav")

    good_transcript = transcribe_with_manus(
        artifact,
        mock_transcript_text="[00:01.000 - 00:03.000] MAR: Hello!\n[00:21.000 - 00:23.000] KUKU: Great!",
    )
    review = review_and_align_with_chatgpt(good_transcript, script_file)

    # Stage ticket
    ticket = create_listening_approval_ticket(
        tmp_path,
        episode_id="EP03",
        chapter_id="CH01",
        intake=artifact,
        transcript=good_transcript,
        review=review,
    )

    ticket_json = tmp_path / f"{ticket.ticket_id}.json"
    ticket_md = tmp_path / f"{ticket.ticket_id}.md"
    assert ticket_json.is_file()
    assert ticket_md.is_file()
    assert ticket.status == "pending_owner_approval"

    # Cannot compile cues prior to approval
    with pytest.raises(ValueError, match="not 'approved'"):
        compile_palmier_cues_from_approval(ticket)

    # Approve ticket
    approved = approve_listening_ticket(ticket_json, owner_notes="Sound quality and timing approved")
    assert approved.status == "approved"
    assert approved.owner_notes == "Sound quality and timing approved"

    # Now compile succeeds
    cues = compile_palmier_cues_from_approval(approved)
    assert len(cues) == 2
    assert cues[0][0] == 1.0
    assert cues[1][0] == 21.0
