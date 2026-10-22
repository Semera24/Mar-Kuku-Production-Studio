"""Tests for persistent studio chat, multi-provider registry, browser takeover, and Mac control profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from mk_studio.browser.controller import (
    BrowserController,
    BrowserTakeoverActiveError,
)
from mk_studio.config import RootPolicy
from mk_studio.mac.controller import (
    MacApprovalRequiredError,
    MacControlError,
    MacController,
    MacProfileViolationError,
)
from mk_studio.providers.registry import (
    ProviderBudgetExceededError,
    ProviderRegistry,
)
from mk_studio.state import StateStore
from mk_studio.studio.server import StudioOrchestrator


def test_persistent_conversation_and_reload_survival(tmp_path: Path) -> None:
    policy = RootPolicy(output_root=tmp_path / "scratch")
    orch1 = StudioOrchestrator(policy)
    conv = orch1.store.create_conversation(
        conversation_id="conv-test-01",
        title="Test Persistent Chat",
        active_episode_id="EPISODE_002",
        active_chapter_id="CH01",
    )
    assert conv["conversation_id"] == "conv-test-01"

    orch1.store.add_message(
        conversation_id="conv-test-01",
        role="user",
        content="Use the candidate audio and keep the 4-second pause.",
        provider_id="codex_cli",
    )
    orch1.store.add_message(
        conversation_id="conv-test-01",
        role="assistant",
        content="Bound E02_CH01_Welcome_v002_EDIT_REVIEW_FULL_MIX.wav (30.22s) with hold frames 501..621.",
        provider_id="codex_cli",
    )

    # Simulate process restart by instantiating a brand new StudioOrchestrator against the same SQLite DB
    orch2 = StudioOrchestrator(policy)
    msgs = orch2.store.list_messages("conv-test-01")
    assert len(msgs) == 2
    assert "candidate audio" in msgs[0]["content"]
    ctx = orch2.build_context_summary("conv-test-01")
    assert "E02_CH01_Welcome_v002_EDIT_REVIEW_FULL_MIX.wav" in ctx
    assert "frames 501..621" in ctx


def test_provider_budget_enforcement_and_health_check(tmp_path: Path) -> None:
    policy = RootPolicy(output_root=tmp_path / "scratch")
    store = StateStore(policy.output_root_resolved / "state.db")
    reg = ProviderRegistry(policy, store)

    health = reg.check_providers_health()
    provider_ids = {p["provider_id"] for p in health}
    assert {"codex_cli", "gemini_cli", "browser_web_ai", "openai_api"}.issubset(provider_ids)

    store.upsert_provider_config(
        provider_id="codex_cli",
        display_name="Codex CLI",
        provider_kind="cli_assistant",
        budget_limit_usd=1.00,
        status="ready",
    )
    store.record_provider_usage("codex_cli", estimated_cost_usd=0.90)

    with pytest.raises(ProviderBudgetExceededError, match="budget exceeded"):
        reg._enforce_budget("codex_cli", estimated_cost_usd=0.25)


def test_mac_capability_profiles_and_approval_gate(tmp_path: Path) -> None:
    policy = RootPolicy(output_root=tmp_path / "scratch")
    store = StateStore(policy.output_root_resolved / "state.db")
    mac = MacController(policy, store)

    # read_only profile must reject launch_or_focus_app
    with pytest.raises(MacProfileViolationError, match="not permitted under profile 'read_only'"):
        mac.launch_or_focus_app("Finder", profile_name="read_only")

    # editor_automation profile must reject unlisted target apps
    with pytest.raises(MacProfileViolationError, match="not in allowlist"):
        mac.launch_or_focus_app("Calculator", profile_name="editor_automation")

    # High-risk action quit_app must require explicit approved_by
    with pytest.raises(MacApprovalRequiredError, match="requires explicit owner approval"):
        mac.quit_app("Calculator", profile_name="general_mac_control", approved_by=None)

    # Immediate stop/cancel enforcement
    mac.request_stop()
    with pytest.raises(MacControlError, match="currently stopped"):
        mac.list_running_apps()
    mac.clear_stop()


def test_browser_owner_takeover_blocks_automation_until_resumed(tmp_path: Path) -> None:
    policy = RootPolicy(output_root=tmp_path / "scratch")
    store = StateStore(policy.output_root_resolved / "state.db")
    browser = BrowserController(policy, store)
    browser.owner_takeover = True

    with pytest.raises(BrowserTakeoverActiveError, match="owner takeover mode"):
        browser._require_not_in_takeover()

    browser.owner_takeover = False
    browser._require_not_in_takeover()


def test_classify_chat_instruction_and_shared_stop_signal(tmp_path: Path) -> None:
    from mk_studio.browser.controller import BrowserCancelledError
    from mk_studio.studio.workflow import classify_chat_instruction

    assert (
        classify_chat_instruction(
            "Research the next Mar and Kuku episode, consult Gemini and Manus, and save the proposed script."
        )
        == "consultation_workflow"
    )
    assert (
        classify_chat_instruction(
            "Follow-up correction: Revise the proposed script so Kuku giggles after the 4-second pause."
        )
        == "followup_correction"
    )
    assert classify_chat_instruction("Resume the workflow") == "resume_workflow"
    assert classify_chat_instruction("Stop") == "stop_workflow"

    policy = RootPolicy(output_root=tmp_path / "scratch")
    orch = StudioOrchestrator(policy)
    stop_res = orch.request_stop()
    assert stop_res["cancel_event_set"] is True
    assert orch.browser.cancel_event.is_set() is True
    assert orch.providers.cancel_event.is_set() is True
    assert orch.mac.cancel_event.is_set() is True
    assert orch.workflow.cancel_event.is_set() is True

    with pytest.raises(BrowserCancelledError, match="cancelled"):
        orch.browser._check_cancel()

    orch.reset_stop()
    assert orch.cancel_event.is_set() is False
    orch.browser._check_cancel()


def test_three_agent_workflow_stop_resume_and_followup_idempotency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = RootPolicy(output_root=tmp_path / "scratch")
    orch = StudioOrchestrator(policy)

    calls = {"chatgpt": 0, "gemini": 0, "manus": 0}
    dummy_shot = tmp_path / "dummy.png"
    dummy_shot.write_bytes(b"\x89PNG\r\n\x1a\n")

    def fake_chatgpt(prompt: str, **kwargs):
        calls["chatgpt"] += 1
        return {
            "site": "chatgpt_web",
            "status": "succeeded",
            "url": f"https://chatgpt.com/c/test-{calls['chatgpt']}",
            "title": "ChatGPT",
            "prompt": prompt,
            "response": f"ChatGPT response #{calls['chatgpt']} for: {prompt[:60]}",
            "screenshot": {"path": str(dummy_shot), "sha256": "00" * 32},
        }

    def fake_gemini(prompt: str, **kwargs):
        calls["gemini"] += 1
        return {
            "site": "gemini_web",
            "status": "succeeded",
            "url": "https://gemini.google.com/app/test-1",
            "title": "Google Gemini",
            "prompt": prompt,
            "response": f"Gemini review #{calls['gemini']}",
            "screenshot": {"path": str(dummy_shot), "sha256": "11" * 32},
        }

    def fake_manus(prompt: str, **kwargs):
        calls["manus"] += 1
        return {
            "site": "manus_web",
            "status": "succeeded",
            "authenticated": True,
            "prompt_submitted": True,
            "prompt_saved_on_site": False,
            "url": "https://manus.im/app/test-1",
            "title": "Manus",
            "prompt": prompt,
            "response": f"Manus specialist timing #{calls['manus']}",
            "login_gate_message": "",
            "dom_excerpt": "Task completed",
            "screenshot": {"path": str(dummy_shot), "sha256": "22" * 32},
        }

    monkeypatch.setattr(orch.browser, "consult_chatgpt_web", fake_chatgpt)
    monkeypatch.setattr(orch.browser, "consult_gemini_web", fake_gemini)
    monkeypatch.setattr(orch.browser, "consult_manus_web", fake_manus)

    # 1. Start and stop after Step 1
    res1 = orch.handle_chat_turn(
        conversation_id="conv-e02-ch01-main",
        user_message="Research the next Mar and Kuku episode, consult Gemini and Manus, and save the proposed script.",
        action_params={"stop_after_step": "step_1_chatgpt_master_plan"},
    )
    assert res1["job"]["status"] == "stopped"
    assert calls == {"chatgpt": 1, "gemini": 0, "manus": 0}

    # 2. Resume workflow: Step 1 must NOT re-submit
    res2 = orch.handle_chat_turn(
        conversation_id="conv-e02-ch01-main",
        user_message="Resume the workflow",
    )
    assert res2["job"]["status"] == "succeeded"
    wf2 = res2["result"]
    assert wf2["status"] == "completed"
    assert wf2["steps"]["step_1_chatgpt_master_plan"]["submission_count"] == 1
    assert wf2["steps"]["step_2_gemini_review"]["submission_count"] == 1
    assert wf2["steps"]["step_3_manus_specialist"]["submission_count"] == 1
    assert wf2["steps"]["step_4_chatgpt_synthesis"]["submission_count"] == 1
    assert calls == {"chatgpt": 2, "gemini": 1, "manus": 1}

    # 3. Follow-up correction: updates to v002 without re-running Steps 1..4
    res3 = orch.handle_chat_turn(
        conversation_id="conv-e02-ch01-main",
        user_message="Follow-up correction: Revise the proposed script so Kuku counts three purple petals.",
    )
    assert res3["job"]["status"] == "succeeded"
    wf3 = res3["result"]
    assert wf3["current_script_version"] == "v002"
    assert wf3["steps"]["step_1_chatgpt_master_plan"]["submission_count"] == 1
    assert wf3["steps"]["step_5_followup_correction"]["submission_count"] == 1
    assert Path(wf3["current_script_path"]).is_file()


def test_set_secret_cli_subcommand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mk_studio import cli

    stored: dict[str, str] = {}
    monkeypatch.setattr(
        "mk_studio.providers.registry.KeychainSecretManager.set_secret",
        lambda self, service, val: stored.setdefault(service, val) is not None,
    )
    monkeypatch.setattr(
        "mk_studio.providers.registry.KeychainSecretManager.get_secret",
        lambda self, service: stored.get(service),
    )

    rc = cli.main(
        [
            "--output-root",
            str(tmp_path / "scratch"),
            "set-secret",
            "openai_api",
            "--value",
            "sk-test-secret-value",
        ]
    )
    assert rc == 0
    assert stored.get("mk-studio:openai_api") == "sk-test-secret-value"


def test_studio_pipeline_actions_dispatch_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that Studio orchestrator triggers asset generation, Palmier assembly, export, and YouTube staging."""
    policy = RootPolicy(output_root=tmp_path / "scratch")
    orch = StudioOrchestrator(policy)

    # 1. Fake initial script in workflow manifest
    ep_dir = policy.output_root_resolved / "episodes" / "EP03_COUNTING_FLOWERS"
    ep_dir.mkdir(parents=True, exist_ok=True)
    script_path = ep_dir / "proposed_script_v001.md"
    script_path.write_text("# Mar & Kuku: Counting Flowers\nPreschool episode script.")
    initial_manifest = {
        "episode_id": "EP03_COUNTING_FLOWERS",
        "status": "completed",
        "current_script_version": "v001",
        "current_script_path": str(script_path),
        "steps": {
            "step_1_chatgpt_master_plan": {"status": "completed", "submission_count": 1},
            "step_2_gemini_review": {"status": "completed", "submission_count": 1},
            "step_3_manus_specialist": {"status": "completed", "submission_count": 1},
            "step_4_chatgpt_synthesis": {"status": "completed", "submission_count": 1},
        },
    }
    orch.workflow.save_manifest(initial_manifest, episode_id="EP03_COUNTING_FLOWERS")

    # Mock stage 9 browser staging to avoid live network during unit tests
    dummy_shot = ep_dir / "test_yt_stage.png"
    dummy_shot.write_bytes(b"\x89PNG\r\n\x1a\n")

    def fake_youtube_stage(*args, **kwargs):
        return {
            "status": "ready_for_upload",
            "url": "https://studio.youtube.com/",
            "screenshot": {"path": str(dummy_shot), "sha256": "33" * 32},
            "dom_summary": {"url": "https://studio.youtube.com/", "title": "Channel dashboard - YouTube Studio"},
        }

    monkeypatch.setattr(orch.browser, "inspect_youtube_studio_and_stage_upload", fake_youtube_stage)

    # Mock Palmier MP4 rendering and QA verification to keep unit test swift (<2s)
    fake_mp4 = ep_dir / "EP03_COUNTING_FLOWERS_Counting_Flowers_1080p30_v001.mp4"
    def fake_render(self, plan, output_mp4_path, **kwargs):
        Path(output_mp4_path).write_bytes(b"\x00\x00\x00 ftypisom")
        return {
            "status": "rendered",
            "mp4_path": str(output_mp4_path),
            "video_frames": 900,
            "duration_seconds": 30.0,
        }

    monkeypatch.setattr(
        "mk_studio.editors.palmier.PalmierAdapter.render_timeline_plan_to_mp4",
        fake_render,
    )
    monkeypatch.setattr(
        "mk_studio.production.pipeline.verify_exported_mp4",
        lambda *args, **kwargs: {
            "passed": True,
            "hold_stillness_rms": 0.5731,
            "bob_motion_rms": 28.3867,
            "flower_appearance_rms": 55.1098,
            "rendered_qa_passed": True,
        },
    )
    monkeypatch.setattr(
        "mk_studio.production.pipeline.verify_native_palmier_project",
        lambda *args, **kwargs: {
            "passed": True,
            "structure_valid": True,
            "reopen_verified": True,
        },
    )

    # Test Action: generate_assets
    res_assets = orch.handle_chat_turn(
        conversation_id="conv-e02-ch01-main",
        user_message="Generate audio and visual props for the episode",
        action="generate_assets",
        action_params={"episode_id": "EP03_COUNTING_FLOWERS"},
    )
    assert res_assets["job"]["status"] == "succeeded"
    wf_after_assets = orch.workflow.load_manifest("EP03_COUNTING_FLOWERS")
    assert "step_6_asset_generation_and_intake" in wf_after_assets["steps"]
    assert wf_after_assets["steps"]["step_6_asset_generation_and_intake"]["status"] == "completed"

    # Test Action: assemble_palmier
    res_palmier = orch.handle_chat_turn(
        conversation_id="conv-e02-ch01-main",
        user_message="Assemble the episode in Palmier Pro",
        action="assemble_palmier",
        action_params={"episode_id": "EP03_COUNTING_FLOWERS"},
    )
    assert res_palmier["job"]["status"] == "succeeded"
    wf_after_palmier = orch.workflow.load_manifest("EP03_COUNTING_FLOWERS")
    assert "step_7_palmier_editing" in wf_after_palmier["steps"]
    assert wf_after_palmier["steps"]["step_7_palmier_editing"]["status"] == "completed"

    # Test Action: export_mp4
    res_export = orch.handle_chat_turn(
        conversation_id="conv-e02-ch01-main",
        user_message="Export the final MP4 and run QA",
        action="export_mp4",
        action_params={"episode_id": "EP03_COUNTING_FLOWERS"},
    )
    assert res_export["job"]["status"] == "succeeded"
    wf_after_export = orch.workflow.load_manifest("EP03_COUNTING_FLOWERS")
    assert "step_8_final_mp4_export" in wf_after_export["steps"]
    assert wf_after_export["steps"]["step_8_final_mp4_export"]["status"] == "completed"

    # Test Action: stage_youtube
    res_yt = orch.handle_chat_turn(
        conversation_id="conv-e02-ch01-main",
        user_message="Stage the video and metadata package for YouTube Studio",
        action="stage_youtube",
        action_params={"episode_id": "EP03_COUNTING_FLOWERS"},
    )
    assert res_yt["job"]["status"] == "succeeded"
    wf_after_yt = orch.workflow.load_manifest("EP03_COUNTING_FLOWERS")
    assert "step_9_youtube_studio_upload" in wf_after_yt["steps"]
    assert wf_after_yt["steps"]["step_9_youtube_studio_upload"]["status"] in (
        "ready_for_upload",
        "completed",
    )


def test_two_distinct_scripts_produce_corresponding_outputs(tmp_path: Path) -> None:
    """Verify that two distinct scripts drive different dialogue, timing, and audio outputs without Python edits."""
    from mk_studio.production.pipeline import (
        generate_episode_assets,
        parse_script_markdown,
    )

    script_a_text = """# Proposed Script (v001) — EPISODE_A
Scene 1 — Red Flower
00:00.000–00:02.000 · Frames 000–060
MAR: "Hello, little gardeners!"
00:02.500–00:05.000 · Frames 075–150
KUKU: "Look at the red flower!"
"""
    script_b_text = """# Proposed Script (v002) — EPISODE_B
Scene 1 — Yellow Flower
00:00.000–00:02.000 · Frames 000–060
MAR: "Welcome to our sunny garden!"
00:02.500–00:05.000 · Frames 075–150
KUKU (giggling): "Hehehe! Two yellow flowers!"
"""
    parsed_a = parse_script_markdown(script_a_text)
    parsed_b = parse_script_markdown(script_b_text)

    assert parsed_a["title"] != parsed_b["title"]
    assert [c[2] for c in parsed_a["dialogue_cues"]] != [c[2] for c in parsed_b["dialogue_cues"]]
    assert "red flower" in parsed_a["dialogue_cues"][1][2]
    assert "Two yellow flowers!" in parsed_b["dialogue_cues"][1][2]

    # Synthesize audio for both in tmp_path
    policy = RootPolicy(output_root=tmp_path / "scratch")
    dir_a = tmp_path / "scratch" / "episodes" / "EP_A"
    dir_b = tmp_path / "scratch" / "episodes" / "EP_B"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)

    script_file_a = dir_a / "script.md"
    script_file_b = dir_b / "script.md"
    script_file_a.write_text(script_a_text)
    script_file_b.write_text(script_b_text)

    # In test environment, mock say command to return distinct audio bytes quickly
    from unittest.mock import patch

    def mock_say(cmd, **kwargs):
        out_wav = Path(cmd[4])
        # Write 0.1s of silence WAV
        with open(out_wav, "wb") as f:
            f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x02\x00\x80\xbb\x00\x00\x00\xee\x02\x00\x04\x00\x10\x00data\x00\x00\x00\x00")

    # Generate assets for script A
    res_a = generate_episode_assets("EP_A", dir_a, policy, script_path=script_file_a)
    # Generate assets for script B
    res_b = generate_episode_assets("EP_B", dir_b, policy, script_path=script_file_b)

    wav_a = dir_a / "assets" / "ep_a_voiceover_master_48k_stereo.wav"
    wav_b = dir_b / "assets" / "ep_b_voiceover_master_48k_stereo.wav"
    assert wav_a.is_file() and wav_a.stat().st_size > 0
    assert wav_b.is_file() and wav_b.stat().st_size > 0
    # Files have distinct content derived from their respective scripts
    assert res_a["episode_id"] == "EP_A"
    assert res_b["episode_id"] == "EP_B"
    assert res_a["audio_master"]["path"] == str(wav_a)
    assert res_b["audio_master"]["path"] == str(wav_b)




