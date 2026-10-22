"""Unit tests for the Google Music -> Manus -> ChatGPT -> Listening Approval -> Palmier workflow."""

import json
from pathlib import Path
from unittest.mock import MagicMock
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
from mk_studio.contracts import CueEvent


def test_audio_intake_provenance(tmp_path: Path) -> None:
    source_wav = Path("scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav")
    assert source_wav.is_file()

    target_wav = tmp_path / "intake_master_48k.wav"
    artifact = intake_audio_master(
        source_wav,
        target_wav,
        source_service="Google Music (MusicFX/Lyria)",
        source_url="https://aitestkitchen.withgoogle.com/tools/music-fx",
        revision_kind="review_candidate",
    )

    assert artifact.source_service == "Google Music (MusicFX/Lyria)"
    assert artifact.source_url == "https://aitestkitchen.withgoogle.com/tools/music-fx"
    assert artifact.revision_kind == "review_candidate"
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
    assert transcript.audio_sha256 == artifact.sha256


def test_empty_manus_transcript_blocks_verification(tmp_path: Path) -> None:
    script_file = tmp_path / "test_script.md"
    script_file.write_text("# Scene 1\nMAR: Hello!\n", encoding="utf-8")

    source_wav = Path("scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav")
    artifact = intake_audio_master(source_wav, tmp_path / "empty.wav")

    # Empty transcript scenario
    empty_transcript = transcribe_with_manus(artifact, mock_transcript_text="Sorry, no speech detected.")
    assert len(empty_transcript.timed_segments) == 0

    review = review_and_align_with_chatgpt(empty_transcript, script_file)
    assert not review.hold_window_verified
    assert review.alignment_status == "missing_transcription"
    assert review.chatgpt_verdict == "REPAIR_REQUIRED"
    assert len(review.conflicts) > 0

    ticket = create_listening_approval_ticket(
        tmp_path,
        episode_id="EP02",
        chapter_id="CH01",
        intake=artifact,
        transcript=empty_transcript,
        review=review,
    )
    assert ticket.status == "conflicts_detected"
    with pytest.raises(ValueError, match="unresolved conflicts"):
        approve_listening_ticket(tmp_path / f"{ticket.ticket_id}.json")


def test_manus_out_of_range_timestamp_rejected(tmp_path: Path) -> None:
    source_wav = Path("scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav")
    artifact = intake_audio_master(source_wav, tmp_path / "out_range.wav")

    # Duration is ~30.22s. Timestamp at 01:45.000 is 105s (out of range)
    out_of_range_response = """
[00:01.000 - 00:03.000] MAR: Hello friends!
[01:45.000 - 01:50.000] MAR: Out of bounds line!
"""
    transcript = transcribe_with_manus(artifact, mock_transcript_text=out_of_range_response)
    assert len(transcript.timed_segments) == 1
    assert len(transcript.invalid_segments) == 1
    assert "out of range" in transcript.invalid_segments[0]["reason"].lower()


def test_manus_file_upload_invocation(tmp_path: Path) -> None:
    source_wav = Path("scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav")
    artifact = intake_audio_master(source_wav, tmp_path / "upload.wav")

    mock_controller = MagicMock()
    mock_controller.consult_manus_web.return_value = {
        "status": "succeeded",
        "response": "[00:01.000 - 00:03.000] MAR: Hello little friend!\n[00:21.000 - 00:23.000] MAR: Let us look!",
        "file_attached": True,
        "attached_file": artifact.file_path,
    }

    transcript = transcribe_with_manus(artifact, browser_controller=mock_controller)
    mock_controller.consult_manus_web.assert_called_once()
    call_kwargs = mock_controller.consult_manus_web.call_args.kwargs
    assert call_kwargs["file_path"] == artifact.file_path
    assert len(transcript.timed_segments) == 2


def test_chatgpt_consultation_invoked_and_hashes_bound(tmp_path: Path) -> None:
    script_file = tmp_path / "approved_script.md"
    script_file.write_text("# Scene 1\nMAR: Hello little friend!\n", encoding="utf-8")

    source_wav = Path("scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav")
    artifact = intake_audio_master(source_wav, tmp_path / "bound.wav")

    mock_controller = MagicMock()
    mock_controller.consult_chatgpt_web.return_value = {
        "status": "succeeded",
        "response": "Wording matches approved script. Hold window 16.70s-20.70s is silent. APPROVED_AND_ALIGNED",
    }

    transcript = transcribe_with_manus(
        artifact,
        mock_transcript_text="[00:01.000 - 00:03.000] MAR: Hello little friend!\n[00:21.000 - 00:23.000] MAR: Look together!",
    )
    review = review_and_align_with_chatgpt(transcript, script_file, browser_controller=mock_controller)

    mock_controller.consult_chatgpt_web.assert_called_once()
    assert review.script_sha256 == artifact.sha256 or len(review.script_sha256) == 64
    assert review.audio_sha256 == artifact.sha256
    assert review.chatgpt_verdict == "APPROVED_AND_ALIGNED"
    assert review.hold_window_verified
    assert review.alignment_status == "aligned"


def test_chatgpt_hold_conflict_detection(tmp_path: Path) -> None:
    script_file = tmp_path / "test_script.md"
    script_file.write_text("# Scene 1\nMAR: Hello friends!\nKUKU: Look!\n", encoding="utf-8")

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


def test_listening_ticket_lifecycle_and_palmier_cues(tmp_path: Path) -> None:
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
        episode_id="EP02",
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
    assert ticket.audio_sha256 == artifact.sha256

    # Cannot compile cues prior to approval
    with pytest.raises(ValueError, match="not 'approved'"):
        compile_palmier_cues_from_approval(ticket)

    # Approve ticket
    approved = approve_listening_ticket(ticket_json, owner_notes="Sound quality and timing approved")
    assert approved.status == "approved"
    assert approved.owner_notes == "Sound quality and timing approved"

    # Now compile succeeds and returns typed CueSpecs for Palmier
    cues = compile_palmier_cues_from_approval(approved)
    assert len(cues) >= 3  # 2 dialogue cues + 1 hold cue
    assert all(isinstance(c, CueEvent) for c in cues)
    # Check hold cue exists at 16.70s (frame 501)
    hold_cue = next(c for c in cues if "HOLD" in c.cue_id)
    assert hold_cue.start_frame == 501
    assert hold_cue.end_frame == 621


def test_palmier_assembly_enforces_listening_approval_e2e(tmp_path: Path) -> None:
    from mk_studio.config import RootPolicy
    from mk_studio.production.pipeline import generate_episode_assets, assemble_episode_palmier

    policy = RootPolicy(output_root=tmp_path)
    output_dir = tmp_path / "episodes" / "EP_TEST"
    output_dir.mkdir(parents=True)

    script_file = output_dir / "proposed_script.md"
    script_file.write_text(
        """# Scene 1 - Welcome
MAR: "Hello, little friend! Welcome to our garden!"
[RESPONSE: Pause for 4 seconds. No vocals.]
MAR: "Let's look together!"
""",
        encoding="utf-8",
    )

    source_wav = Path("scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav")
    assert source_wav.is_file()

    # 1. Intake real audio and run Step 6
    artifact = intake_audio_master(
        source_wav,
        output_dir / "ch01_real_master.wav",
        source_service="Google Music (MusicFX/Lyria)",
    )

    # 2. Transcribe with Manus
    manus_resp = """
[00:00.600 - 00:04.200] MAR: Hello little friend welcome to our garden!
[00:20.700 - 00:23.500] MAR: Let us look together!
"""
    transcript = transcribe_with_manus(artifact, mock_transcript_text=manus_resp)
    assert len(transcript.timed_segments) == 2

    # 3. ChatGPT Review
    review = review_and_align_with_chatgpt(
        transcript,
        script_file,
        audio_artifact=artifact,
        mock_chatgpt_response="Approved and aligned. 4s hold 16.70s-20.70s is silent. APPROVED_AND_ALIGNED",
    )
    assert review.hold_window_verified
    assert review.alignment_status == "aligned"

    # 4. Stage Listening Ticket (pending owner approval)
    ticket = create_listening_approval_ticket(
        output_dir,
        episode_id="EP_TEST",
        chapter_id="CH01",
        intake=artifact,
        transcript=transcript,
        review=review,
    )
    assert ticket.status == "pending_owner_approval"

    # Generate assets with real audio intake
    gen_res = generate_episode_assets(
        "EP_TEST",
        output_dir,
        policy,
        script_path=script_file,
        source_audio_path=artifact.file_path,
    )
    assert gen_res["audio_provider"] == "google_music_intake"
    assert not gen_res["is_test_provider"]

    # 5. Palmier Assembly MUST FAIL while ticket is pending approval
    with pytest.raises(ValueError, match="Listening approval required"):
        assemble_episode_palmier("EP_TEST", output_dir, policy)

    # 6. Approve ticket
    ticket_path = output_dir / f"{ticket.ticket_id}.json"
    approve_listening_ticket(ticket_path, owner_notes="Owner verified audio and 4s silent hold.")

    # 7. Palmier Assembly now SUCCEEDS
    palmier_res = assemble_episode_palmier("EP_TEST", output_dir, policy)
    assert palmier_res["status"] == "completed"
    assert palmier_res["listening_approval_ticket"] == ticket.ticket_id
    assert palmier_res["reopen_verified"]

    # Verify native project exists
    project_path = Path(palmier_res["project_path"])
    assert project_path.is_dir()
    project_json = json.loads((project_path / "project.json").read_text())
    assert len(project_json["timelines"][0]["tracks"]) >= 5
    assert len(project_json["timelines"][0]["markers"]) >= 3

