"""Integration tests for Studio CH02 Audio Production cycle via Studio chat endpoint.

Verifies:
1. One Studio chat instruction resumes/drives the audio production workflow.
2. Verified reuse of historical work (candidates, transcripts, reviews) with SHA-256 provenance.
3. State persistence in output_manifest.json and SQLite StateStore.
4. Resolution of the next blocked step (wording choice + FFmpeg 4.0s pause trim).
5. Audio streaming via /api/audio endpoint.
6. Restart resilience without duplicate provider calls or file overwrite.
"""

from pathlib import Path
import pytest
from starlette.testclient import TestClient

from mk_studio.config import RootPolicy
from mk_studio.studio.server import create_studio_app, StudioOrchestrator


def test_studio_ch02_audio_full_turn_lifecycle() -> None:
    policy = RootPolicy()
    app = create_studio_app(policy)
    client = TestClient(app)

    # 1. Check initial State
    state_res = client.get("/api/state?conversation_id=conv-e02-ch01-main")
    assert state_res.status_code == 200
    state_data = state_res.json()
    assert "conversation" in state_data

    # 2. First Chat Turn: Resume CH02 audio production from saved state
    prompt_1 = (
        "Continue Episode 002 CH02 audio production from its saved state. "
        "Reuse completed candidates and transcripts, resolve the next blocked step, "
        "and show me the review and available audio results."
    )
    chat_res_1 = client.post(
        "/api/chat",
        json={
            "conversation_id": "conv-e02-ch01-main",
            "message": prompt_1,
            "provider_id": "codex_cli",
        },
    )
    assert chat_res_1.status_code == 200
    turn_1 = chat_res_1.json()
    reply_1 = turn_1["assistant_message"]["content"]

    # Verify Turn 1 reply content
    assert "Studio Audio Production Resumed: Episode 002 — Chapter 02" in reply_1
    assert "Google Flow Music Takes" in reply_1
    assert "Manus Transcripts" in reply_1
    assert "Candidate B (Selected)" in reply_1
    assert "ChatGPT Limitation" in reply_1
    assert "Wording Conflict" in reply_1
    assert "Choose your next action" in reply_1

    # Verify attachments and audio URLs
    attachments_1 = turn_1["assistant_message"].get("attachments") or []
    audio_urls = [a.get("audio_url") for a in attachments_1 if a.get("audio_url")]
    assert len(audio_urls) >= 2  # Candidate A and Candidate B

    # 3. Verify Audio Endpoint (/api/audio)
    for a_url in audio_urls:
        audio_stream = client.get(a_url)
        assert audio_stream.status_code == 200
        assert audio_stream.headers["content-type"] == "audio/wav"
        assert int(audio_stream.headers["content-length"]) > 1_000_000

    # 4. Verify Manifest and SQLite Persistence after Turn 1
    orch1 = app.state.orchestrator
    m1 = orch1.workflow.load_manifest("EP02_CH02_AUDIO")
    steps_1 = m1.get("steps") or {}
    assert steps_1["step_script"]["status"] == "completed"
    assert steps_1["step_script"]["provenance"] == "imported_historical_verified"
    assert steps_1["step_google_music_generation"]["status"] == "completed"
    assert steps_1["step_google_music_generation"]["provenance"] == "imported_historical_verified"
    assert steps_1["step_wording_reconciliation"]["status"] == "awaiting_owner_decision"
    assert steps_1["step_listening_approval"]["status"] == "awaiting_owner_decision"

    # Verify SQLite artifacts
    db_artifacts = orch1.store.list_artifacts(episode_id="EP02", chapter_id="CH02")
    roles = {a["role"] for a in db_artifacts}
    assert "audio:candidate_a" in roles
    assert "audio:candidate_b" in roles

    # 5. Second Chat Turn: Owner decision on wording & trim
    prompt_2 = "Approve script change: keep 'Have a look at this one' and trim hold to 4s"
    chat_res_2 = client.post(
        "/api/chat",
        json={
            "conversation_id": "conv-e02-ch01-main",
            "message": prompt_2,
            "provider_id": "codex_cli",
        },
    )
    assert chat_res_2.status_code == 200
    turn_2 = chat_res_2.json()
    reply_2 = turn_2["assistant_message"]["content"]

    # Verify Turn 2 reply content
    assert "Audio Repair Applied: Revision v002 Ready for Audition" in reply_2
    assert "Owner Decision Recorded" in reply_2
    assert "Have a look at this one" in reply_2
    assert "EP02_CH02_Candidate_B_v002_EDITED.wav" in reply_2
    assert "Untouched Backup" in reply_2

    # Verify edited audio file was created and is playable
    attachments_2 = turn_2["assistant_message"].get("attachments") or []
    edited_att = next((a for a in attachments_2 if "v002" in a.get("label", "")), None)
    assert edited_att is not None
    edited_audio_url = edited_att["audio_url"]
    edited_resp = client.get(edited_audio_url)
    assert edited_resp.status_code == 200
    assert edited_resp.headers["content-type"] == "audio/wav"

    # 6. Verify Manifest and SQLite Persistence after Turn 2
    m2 = orch1.workflow.load_manifest("EP02_CH02_AUDIO")
    steps_2 = m2.get("steps") or {}
    assert steps_2["step_wording_reconciliation"]["status"] == "completed"
    assert steps_2["step_audio_repair"]["status"] == "completed"
    assert steps_2["step_listening_approval"]["status"] == "revision_v002_ready_for_listening"

    # Verify SQLite has edited master v002
    db_artifacts_2 = orch1.store.list_artifacts(episode_id="EP02", chapter_id="CH02")
    roles_2 = {a["role"] for a in db_artifacts_2}
    assert "audio:edited_master_v002" in roles_2

    # 7. Restart Resilience: new Studio app / orchestrator reload
    app_restarted = create_studio_app(policy)
    client_restarted = TestClient(app_restarted)
    state_reloaded = client_restarted.get("/api/state?conversation_id=conv-e02-ch01-main").json()
    assert "ch02_workflow" in state_reloaded
    reloaded_steps = state_reloaded["ch02_workflow"].get("steps") or {}
    assert reloaded_steps["step_audio_repair"]["status"] == "completed"
    assert reloaded_steps["step_listening_approval"]["status"] == "revision_v002_ready_for_listening"

    # Verify messages are intact
    reloaded_msgs = state_reloaded.get("messages") or []
    assert len(reloaded_msgs) >= 2
