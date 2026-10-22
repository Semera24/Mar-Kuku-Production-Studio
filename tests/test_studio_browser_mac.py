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


