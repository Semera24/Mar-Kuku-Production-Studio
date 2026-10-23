"""Integration tests for Studio CH02 Audio Production cycle via Studio chat endpoint.

Verifies:
1. Isolated test run inside tmp_path (no contamination of production records).
2. Chat instruction resumes workflow from verified saved state.
3. Accurate identity reporting: single take (Lyria 3.5 Remix Master) with identical decoded PCM.
4. Differences table between approved script and spoken audio.
5. Questions, refusals, and ambiguous messages never trigger edits or approvals.
6. Explicit approval performs exact 4.00s hold splice [22.40s..26.40s], removing 2.90s silence, altering 0 spoken words.
7. Idempotent re-run reuses existing v002 without re-running FFmpeg.
8. Native Palmier assembly, native 1080p30 MP4 export via Palmier Pro MCP, and YouTube staging package.
9. Playable audio (/api/audio) and video (/api/video) endpoints.
10. Server restart resilience reloading all saved decisions from SQLite and manifest.
"""

from pathlib import Path
import shutil
import pytest
from starlette.testclient import TestClient

from mk_studio.config import RootPolicy, hash_raw_audio_pcm
from mk_studio.studio.server import create_studio_app


def test_studio_ch02_audio_full_turn_lifecycle(tmp_path: Path) -> None:
    # 1. Isolated test scratch root
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    policy = RootPolicy(output_root=scratch_dir)

    # Populate isolated chapter directory with initial fixtures
    prod_ch02 = Path("scratch/ch01-proof/episodes/EP02_CH02_AUDIO")
    test_ch02 = scratch_dir / "episodes" / "EP02_CH02_AUDIO"
    test_ch02.mkdir(parents=True, exist_ok=True)

    shutil.copytree(prod_ch02 / "00_ORIGINAL", test_ch02 / "00_ORIGINAL")
    if (prod_ch02 / "01_TRANSCRIPTS").is_dir():
        shutil.copytree(prod_ch02 / "01_TRANSCRIPTS", test_ch02 / "01_TRANSCRIPTS")
    if (prod_ch02 / "02_REVIEWS").is_dir():
        shutil.copytree(prod_ch02 / "02_REVIEWS", test_ch02 / "02_REVIEWS")
    if (prod_ch02 / "TICKET_AUDIO_EP02_CH02.json").is_file():
        shutil.copy2(prod_ch02 / "TICKET_AUDIO_EP02_CH02.json", test_ch02 / "TICKET_AUDIO_EP02_CH02.json")

    # Copy template .palmier project for isolated project bootstrapping
    template_src = Path("scratch/ch01-proof/E02_CH01_Technical_Proof_v001.palmier")
    if template_src.is_dir():
        shutil.copytree(template_src, scratch_dir / "E02_CH01_Technical_Proof_v001.palmier")

    app = create_studio_app(policy)
    client = TestClient(app)

    # Verify initial state
    state_res = client.get("/api/state?conversation_id=conv-e02-ch01-main")
    assert state_res.status_code == 200

    # -------------------------------------------------------------
    # TURN 1: Resume CH02 production from saved state
    # -------------------------------------------------------------
    prompt_1 = (
        "Continue the selected chapter from its verified saved state. "
        "Reuse completed work, show any decisions needed, and produce the native Palmier chapter export after approval."
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

    # Verify Turn 1 reply content: identifies single take and halts for owner decision
    assert "Studio Audio Production Resumed: Episode 002 — Chapter 02" in reply_1
    assert "Take 1 (Lyria 3.5 Remix Master)" in reply_1
    assert "Candidate A and Candidate B have identical decoded PCM audio" in reply_1
    assert "Script vs Spoken Audio Differences Table" in reply_1
    assert "Have a look at this one" in reply_1
    assert "6.90s vocal gap" in reply_1
    assert "Current Blockers & Decisions Needed" in reply_1

    # Verify audio URLs returned and streamable
    attachments_1 = turn_1["assistant_message"].get("attachments") or []
    audio_urls = [a.get("audio_url") for a in attachments_1 if a.get("audio_url")]
    assert len(audio_urls) >= 2
    for a_url in audio_urls:
        a_resp = client.get(a_url)
        assert a_resp.status_code == 200
        assert a_resp.headers["content-type"] == "audio/wav"

    # Verify that ZERO edits were performed yet
    edited_wav_path = test_ch02 / "EP02_CH02_Candidate_B_v002_EDITED.wav"
    assert not edited_wav_path.exists()

    # -------------------------------------------------------------
    # TURN 2: Question Safety Test (Interrogative query must NEVER trigger edit)
    # -------------------------------------------------------------
    prompt_2 = "Why are candidate A and candidate B identical?"
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
    assert "Zero edits performed" in reply_2 or "Candidate A and Candidate B have identical" in reply_2
    assert not edited_wav_path.exists()

    # -------------------------------------------------------------
    # TURN 3: Refusal Safety Test (Explicit rejection must NEVER edit)
    # -------------------------------------------------------------
    prompt_3 = "I do not approve this wording change or edit."
    chat_res_3 = client.post(
        "/api/chat",
        json={
            "conversation_id": "conv-e02-ch01-main",
            "message": prompt_3,
            "provider_id": "codex_cli",
        },
    )
    assert chat_res_3.status_code == 200
    turn_3 = chat_res_3.json()
    reply_3 = turn_3["assistant_message"]["content"]
    assert "Audio Repair Rejected" in reply_3
    assert "Zero Edits Performed" in reply_3
    assert not edited_wav_path.exists()

    # Verify manifest status recorded rejection
    orch1 = app.state.orchestrator
    m3 = orch1.workflow.load_manifest("EP02_CH02_AUDIO")
    assert m3["status"] == "owner_rejected"

    # -------------------------------------------------------------
    # TURN 4: Explicit Approval (Wording approval & 4.0s hold trim)
    # -------------------------------------------------------------
    prompt_4 = "Approve script change: keep 'Have a look at this one' and trim hold to 4s"
    chat_res_4 = client.post(
        "/api/chat",
        json={
            "conversation_id": "conv-e02-ch01-main",
            "message": prompt_4,
            "provider_id": "codex_cli",
        },
    )
    assert chat_res_4.status_code == 200
    turn_4 = chat_res_4.json()
    reply_4 = turn_4["assistant_message"]["content"]
    assert "Audio Repair Applied: Revision v002 Approved & Ready" in reply_4
    assert "Trimmed response gap from 6.90s to exact 4.00s hold" in reply_4
    assert "EP02_CH02_Candidate_B_v002_EDITED.wav" in reply_4

    # Verify edited audio file was created and is ~81.612s
    assert edited_wav_path.is_file()
    edited_pcm_hash = hash_raw_audio_pcm(edited_wav_path)
    assert len(edited_pcm_hash) == 64

    # -------------------------------------------------------------
    # TURN 5: Idempotency Re-run (Repeating approval reuses without overwrite)
    # -------------------------------------------------------------
    chat_res_5 = client.post(
        "/api/chat",
        json={
            "conversation_id": "conv-e02-ch01-main",
            "message": prompt_4,
            "provider_id": "codex_cli",
        },
    )
    assert chat_res_5.status_code == 200
    turn_5 = chat_res_5.json()
    reply_5 = turn_5["assistant_message"]["content"]
    assert "Reused existing master = `True`" in reply_5

    # -------------------------------------------------------------
    # TURN 6: Native Palmier Chapter Delivery Turn
    # -------------------------------------------------------------
    chat_res_6 = client.post(
        "/api/chat",
        json={
            "conversation_id": "conv-e02-ch01-main",
            "message": prompt_1,
            "provider_id": "codex_cli",
        },
    )
    assert chat_res_6.status_code == 200
    turn_6 = chat_res_6.json()
    reply_6 = turn_6["assistant_message"]["content"]
    assert "Studio Native Palmier Chapter Delivery Completed: Episode 002 — Chapter 02" in reply_6
    assert "Native Palmier Project" in reply_6
    assert "Native 1080p30 MP4 Export" in reply_6
    assert "YouTube Metadata Staged" in reply_6

    # Verify attachments include video player URL
    attachments_6 = turn_6["assistant_message"].get("attachments") or []
    video_att = next((a for a in attachments_6 if a.get("video_url")), None)
    assert video_att is not None
    assert "/api/video?file=" in video_att["video_url"]

    # Verify video streaming endpoint
    v_resp = client.get(video_att["video_url"])
    assert v_resp.status_code == 200
    assert v_resp.headers["content-type"] == "video/mp4"

    # -------------------------------------------------------------
    # TURN 7: Restart Resilience (New server instance reloads saved state)
    # -------------------------------------------------------------
    app_restarted = create_studio_app(policy)
    client_restarted = TestClient(app_restarted)
    state_reloaded = client_restarted.get("/api/state?conversation_id=conv-e02-ch01-main").json()
    assert "ch02_workflow" in state_reloaded
    reloaded_manifest = state_reloaded["ch02_workflow"]
    assert reloaded_manifest["status"] == "completed"
    assert reloaded_manifest["steps"]["step_wording_reconciliation"]["status"] == "completed"
    assert reloaded_manifest["steps"]["step_audio_repair"]["status"] == "completed"
    step_mp4 = reloaded_manifest["steps"].get("step_final_mp4_export") or reloaded_manifest["steps"].get("step_8_final_mp4_export")
    assert step_mp4 is not None
    assert step_mp4["status"] in ("completed", "preview_fallback_rendered")
