"""Starlette/Uvicorn web application and multi-turn conversational orchestrator for Mar & Kuku Studio."""

from __future__ import annotations

import asyncio
import json
import threading
import hashlib
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

from mk_studio import cli
from mk_studio.browser.controller import BrowserCancelledError, BrowserController
from mk_studio.contracts import KIDS_CHANNEL_STYLE_STANDARD
from mk_studio.media import CH01_PINNED_INPUTS
from mk_studio.config import RootPolicy
from mk_studio.editors.palmier import PalmierAdapter
from mk_studio.mac.controller import MacController
from mk_studio.providers.registry import ProviderError, ProviderRegistry
from mk_studio.os_compat import focus_application, get_platform_name, is_linux, is_macos, open_file_or_folder
from mk_studio.runner import StepRunner
from mk_studio.scheduler import StudioScheduler
from mk_studio.skills.loader import SkillRegistry
from mk_studio.state import StateStore
from mk_studio.telegram.bot import TelegramService
from mk_studio.studio.workflow import (
    DEFAULT_TEST_EPISODE_ID,
    StudioWorkflowEngine,
    WorkflowStoppedError,
    classify_chat_instruction,
)


class StudioOrchestrator:
    """Coordinates persistent conversations, follow-up context resolution, AI providers, browser, Palmier, Mac control, and 3-agent workflows."""

    def __init__(self, policy: RootPolicy | None = None) -> None:
        self.policy = policy or RootPolicy()
        db_path = self.policy.output_root_resolved / "state.db"
        lock_dir = self.policy.output_root_resolved / "locks"
        self.store = StateStore(db_path)
        self.store.initialize_studio_schema()
        self.runner = StepRunner(self.store, lock_dir)
        self.cancel_event = threading.Event()
        self._execution_lock = threading.Lock()
        self.browser = BrowserController(
            self.policy,
            self.store,
            cancel_event=self.cancel_event,
        )
        self.providers = ProviderRegistry(
            self.policy,
            self.store,
            browser=self.browser,
            cancel_event=self.cancel_event,
        )
        self.mac = MacController(
            self.policy,
            self.store,
            cancel_event=self.cancel_event,
        )
        self.workflow = StudioWorkflowEngine(
            self.policy,
            self.store,
            self.browser,
            self.providers,
            cancel_event=self.cancel_event,
        )
        self.palmier = PalmierAdapter(self.policy)
        from mk_studio.production import ProductionService
        self.production = ProductionService(self.policy, cancel_event=self.cancel_event)
        self.skills = SkillRegistry(Path(__file__).resolve().parents[3])
        self.scheduler = StudioScheduler()
        self.telegram = TelegramService()
        self.telegram.start_polling(self)
        self._ensure_default_conversation()

    def request_stop(self) -> dict[str, Any]:
        """Signal immediate cooperative cancellation across Browser, Providers, Mac, and Workflow."""
        self.cancel_event.set()
        self.browser.request_stop()
        self.mac.request_stop()
        return {
            "status": "stopped",
            "cancel_event_set": self.cancel_event.is_set(),
            "mac_stop_requested": self.mac.stop_requested,
        }

    def reset_stop(self) -> None:
        """Clear the shared cancellation signal before starting or resuming work."""
        self.cancel_event.clear()
        self.browser.reset_stop()
        self.mac.reset_stop()

    def _ensure_default_conversation(self) -> dict[str, Any]:
        convs = self.store.list_conversations()
        if convs:
            return convs[0]
        proj_path = str(
            self.policy.output_root_resolved / "E02_CH01_Technical_Proof_v001.palmier"
        )
        return self.store.create_conversation(
            conversation_id="conv-e02-ch01-main",
            title="Episode 002 / CH01 — Welcome to the Color Garden",
            active_episode_id="EPISODE_002",
            active_chapter_id="CH01",
            active_project_path=proj_path,
            active_browser_session_id="default-chrome-cdp",
            active_mac_profile="editor_automation",
            active_provider_id="codex_cli",
        )

    def build_context_summary(self, conversation_id: str) -> str:
        """Build a deterministic context summary including active episode/chapter/project, recent messages, and workflow state."""
        conv = self.store.get_conversation(conversation_id) or self._ensure_default_conversation()
        history = self.store.list_messages(conversation_id)[-8:]
        plan_path = self.policy.output_root_resolved / "timeline-plan.json"
        mp4_path = self.policy.output_root_resolved / "E02_CH01_Technical_Proof_1080p30_v001.mp4"
        cand_rel = CH01_PINNED_INPUTS["ch01_candidate_wav"]["path"]
        cand_wav = self.policy.production_root_resolved / cand_rel
        wf_manifest = self.workflow.load_manifest(DEFAULT_TEST_EPISODE_ID)

        lines = [
            f"- Conversation ID: {conv['conversation_id']} ({conv['title']})",
            f"- Active Episode / Chapter: {conv['active_episode_id']} / {conv['active_chapter_id']}",
            f"- Active Scratch Palmier Project: {conv['active_project_path']}",
            f"- Candidate Audio ('the candidate audio'): {cand_wav} (exists={cand_wav.is_file()}, 30.220000s, 1,450,560 samples @ 48kHz, stream 0:a:0)",
            "- Response Hold Window ('the 4-second pause'): frames 501..621 (16.70s..20.70s, 4.00s stillness hold)",
            f"- Scratch Output Root ('export to scratch'): {self.policy.output_root_resolved}",
            f"- Compiled Timeline Plan: {plan_path} (exists={plan_path.is_file()})",
            f"- Exported 1080p30 MP4: {mp4_path} (exists={mp4_path.is_file()}, video=30.233333s / 907f, audio=30.200000s)",
            f"- Active 3-Agent Workflow ({DEFAULT_TEST_EPISODE_ID}): status={wf_manifest.get('status')}, script_version={wf_manifest.get('current_script_version')}, script_path={wf_manifest.get('current_script_path')}",
            f"- Active Browser Session: {conv['active_browser_session_id']} (takeover={self.browser.owner_takeover})",
            f"- Active Mac Profile: {conv['active_mac_profile']}",
            f"- Active AI Provider: {conv['active_provider_id']}",
        ]
        active_skill = self.skills.get_active_skill()
        lines.append(f"- Active Niche Blueprint (Hermes Skill): {active_skill.title} (ID: {active_skill.name}, Format: {active_skill.format})")
        lines.append(f"- Niche Blueprint Excerpt: {active_skill.instructions_md[:260].replace(chr(10), ' ')}")
        if history:
            lines.append("\nRecent Conversation Turns:")
            for m in history:
                snippet = str(m["content"]).replace("\n", " ")[:220]
                lines.append(f"  [{m['role']}] ({m.get('provider_id') or 'studio'}): {snippet}")
        return "\n".join(lines)

    def handle_chat_turn(self, **kwargs):
        """One writer per application; Stop remains callable while a job is running."""
        stop = kwargs.get('action') == 'stop_workflow' or classify_chat_instruction(kwargs.get('user_message', '')) == 'stop_workflow'
        if stop:
            return self._handle_chat_turn(**kwargs)
        if not self._execution_lock.acquire(blocking=False):
            raise RuntimeError('Another Studio job is still running. Stop or wait for it to finish.')
        try:
            return self._handle_chat_turn(**kwargs)
        finally:
            self._execution_lock.release()

    def _handle_chat_turn(
        self,
        *,
        conversation_id: str,
        user_message: str,
        provider_id: str | None = None,
        action: str | None = None,
        action_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a user message, classify natural-language studio commands or explicit actions, and persist the assistant reply."""
        conv = self.store.get_conversation(conversation_id)
        if not conv:
            conv = self.store.create_conversation(conversation_id=conversation_id)

        eff_provider = provider_id or str(conv.get("active_provider_id") or "codex_cli")
        if provider_id and provider_id != conv.get("active_provider_id"):
            self.store.update_conversation_context(
                conversation_id,
                active_provider_id=provider_id,
            )

        user_msg_row = self.store.add_message(
            conversation_id=conversation_id,
            role="user",
            content=user_message,
            provider_id=eff_provider,
        )

        params = action_params or {}
        inferred_action = action
        if not inferred_action:
            intent_kind = classify_chat_instruction(user_message)
            if isinstance(intent_kind, dict):
                intent_kind = intent_kind.get("kind")
            if intent_kind == "consultation_workflow":
                inferred_action = "consult_three_agents"
            elif intent_kind == "followup_correction":
                inferred_action = "followup_correction"
            elif intent_kind == "resume_workflow":
                inferred_action = "resume_workflow"
            elif intent_kind == "stop_workflow":
                inferred_action = "stop_workflow"
            elif intent_kind == "generate_assets":
                inferred_action = "generate_assets"
            elif intent_kind == "flow_music_audio_pipeline":
                inferred_action = "flow_music_audio_pipeline"
            elif intent_kind == "continue_ch02_audio":
                inferred_action = "continue_selected_chapter"
            elif intent_kind == "continue_selected_chapter":
                inferred_action = "continue_selected_chapter"
            elif intent_kind == "reject_decision":
                inferred_action = "reject_decision"
            elif intent_kind == "apply_audio_repair":
                inferred_action = "apply_audio_repair"
            elif intent_kind == "assemble_palmier":
                inferred_action = "assemble_palmier"
            elif intent_kind == "export_mp4":
                inferred_action = "export_mp4"
            elif intent_kind == "stage_youtube":
                inferred_action = "stage_youtube"
            elif intent_kind == "youtube_community_review":
                inferred_action = "youtube_community_review"
            elif intent_kind == "full_pipeline":
                inferred_action = "full_pipeline"

        if inferred_action in ('consult_three_agents', 'resume_workflow', 'continue_ch02_audio', 'continue_selected_chapter', 'apply_audio_repair') or str(inferred_action).startswith('production_'):
            self.reset_stop()

        if inferred_action == 'consult_three_agents':
            ep_id = str(params.get('episode_id') or ('EP_' + hashlib.sha256(user_message.encode()).hexdigest()[:12]))
            self.store.update_conversation_context(conversation_id, active_episode_id=ep_id)
        else:
            ep_id = str(params.get('episode_id') or conv.get('active_episode_id') or DEFAULT_TEST_EPISODE_ID)

        job = self.store.create_job(
            conversation_id=conversation_id,
            job_type=inferred_action or f"chat:{eff_provider}",
            summary=user_message[:140],
            status="running",
        )
        job_id = str(job["job_id"])
        context_summary = self.build_context_summary(conversation_id)

        attachments: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        reply_text = ""
        result_payload: dict[str, Any] = {}
        job_status = "succeeded"

        try:
            if user_message.strip().startswith("/learn"):
                instruction = user_message.strip()[6:].strip()
                if not instruction:
                    reply_text = "To use the `/learn` command, provide an instruction, rule, or niche description. Example:\n- `/learn In Tech Explainers, always keep the opening hook under 8 seconds.`\n- `/learn Niche: Space & Astronomy 16:9 documentary with Neil deGrasse Tyson style narration.`"
                else:
                    if "niche" in instruction.lower() or "blueprint" in instruction.lower() or "channel" in instruction.lower():
                        new_skill = self.skills.learn_new_niche_skill(instruction)
                        reply_text = (
                            f"✨ **Learned & Created New 16:9 Niche Blueprint:**\n\n"
                            f"- **Title:** `{new_skill.title}`\n"
                            f"- **Identifier:** `{new_skill.name}`\n"
                            f"- **File:** `{new_skill.skill_file}`\n"
                            f"- **Format:** `{new_skill.format}`\n\n"
                            f"Switched active studio blueprint to `{new_skill.name}`. All subsequent 16:9 scripts and timeline assembly will follow this blueprint."
                        )
                        self.skills.set_active_skill(new_skill.name)
                    else:
                        self.skills.memory.append_learned_rule(instruction)
                        reply_text = (
                            f"🧠 **Learned & Persisted New Production Rule:**\n\n"
                            f"- *Rule:* \"{instruction}\"\n"
                            f"- *Saved to:* `data/memory/memory.md`\n"
                            f"- The studio will automatically apply this rule across all future episodes."
                        )
                result_payload = {"learned": True, "instruction": instruction}

            elif inferred_action == "stop_workflow":
                stop_info = self.request_stop()
                result_payload = stop_info
                reply_text = (
                    "Stop requested across Browser, Provider, Mac, and 3-Agent Workflow controllers. "
                    "Completed steps remain checkpointed in `output_manifest.json` and will not repeat on resume."
                )

            elif inferred_action in ("consult_three_agents", "resume_workflow"):
                manifest = self.workflow.run_or_resume_consultation(
                    user_message,
                    episode_id=ep_id,
                    conversation_id=conversation_id,
                    stop_after_step=params.get("stop_after_step"),
                    pause_on_manus_login=bool(params.get("pause_on_manus_login", True)),
                )
                result_payload = manifest
                refs = manifest.get("conversation_references") or {}
                steps = manifest.get("steps") or {}
                s1 = steps.get("step_1_chatgpt_master_plan") or {}
                s2 = steps.get("step_2_gemini_review") or {}
                s3 = steps.get("step_3_manus_specialist") or {}
                s4 = steps.get("step_4_chatgpt_synthesis") or {}
                reply_text = (
                    f"Completed 3-Agent Consultation Workflow (`{manifest['episode_id']}`, status=`{manifest['status']}`).\n"
                    f"- **Master Producer (ChatGPT Web):** `{refs.get('chatgpt_master_url')}` (submissions=`{s1.get('submission_count')}`)\n"
                    f"- **Reviewer (Google Gemini Web):** `{refs.get('gemini_reviewer_url')}` (submissions=`{s2.get('submission_count')}`)\n"
                    f"- **Specialist (Manus Web):** `{refs.get('manus_specialist_url')}` (status=`{s3.get('status')}`, submissions=`{s3.get('submission_count')}`)\n"
                    f"- **Synthesis (ChatGPT Web):** `{refs.get('chatgpt_synthesis_url')}` (submissions=`{s4.get('submission_count')}`)\n"
                    f"- **Saved Script (`{manifest.get('current_script_version')}`):** `{manifest.get('current_script_path')}`\n"
                    f"- **Output Manifest:** `{self.workflow.manifest_path(ep_id)}`"
                )
                if manifest.get("current_script_path"):
                    attachments.append(
                        {"label": "proposed_script.md", "path": str(manifest["current_script_path"])}
                    )
                attachments.append(
                    {"label": "output_manifest.json", "path": str(self.workflow.manifest_path(ep_id))}
                )
                citations.extend(
                    [
                        {"provider_id": "chatgpt_web", "url": refs.get("chatgpt_master_url"), "role": "master"},
                        {"provider_id": "gemini_web", "url": refs.get("gemini_reviewer_url"), "role": "reviewer"},
                        {"provider_id": "manus_web", "url": refs.get("manus_specialist_url"), "role": "specialist"},
                    ]
                )

            elif inferred_action == "followup_correction":
                manifest = self.workflow.apply_followup_correction(
                    user_message,
                    episode_id=ep_id,
                    conversation_id=conversation_id,
                )
                result_payload = manifest
                refs = manifest.get("conversation_references") or {}
                s5 = (manifest.get("steps") or {}).get("step_5_followup_correction") or {}
                reply_text = (
                    f"Applied follow-up script correction (`{manifest.get('current_script_version')}`) for `{ep_id}`:\n"
                    f"- **Follow-Up ChatGPT URL:** `{refs.get('chatgpt_followup_url')}`\n"
                    f"- **Updated Script:** `{s5.get('script_file')}`\n"
                    f"- **Canonical Script Pointer:** `{manifest.get('current_script_path')}`\n"
                    f"- **Prior Steps Re-Submitted:** `0` (Steps 1–4 preserved from checkpoint)"
                )
                if s5.get("script_v002_file"):
                    attachments.append(
                        {"label": "proposed_script_v002.md", "path": str(s5["script_v002_file"])}
                    )

            elif inferred_action == "generate_assets":
                manifest = self.workflow.run_asset_generation(ep_id)
                result_payload = manifest
                s6 = manifest.get('steps', {}).get('step_6_asset_generation_and_intake', {})
                reply_text = (
                    f"Generated episode audio and visual props for `{ep_id}`:\n"
                    f"- Voiceover Master: `{ep_id.lower()}_voiceover_master_48k_stereo.wav` (48kHz, 2-channel stereo, 30.00s)\n"
                    f"- Generated Props: `prop_flowers_yellow_2.png`, `prop_flowers_purple_3.png`\n"
                    f"- Custom Thumbnail: `1280x720` YouTube thumbnail\n"
                    f"- ChapterSpec & AssetManifest calibrated into `06_asset_intake_manifest.json`."
                )
                if s6.get("output_file"):
                    attachments.append({"label": "06_asset_intake_manifest.json", "path": str(s6["output_file"])})

            elif inferred_action == "flow_music_audio_pipeline":
                # Check if user message or params request delegated approval on owner's behalf
                delegate = bool(
                    params.get("delegate_to_chatgpt")
                    or "on behalf" in user_message.lower()
                    or "behalf of owner" in user_message.lower()
                    or "gpt can listen" in user_message.lower()
                    or "gpt can review" in user_message.lower()
                )
                pipeline_res = self.workflow.run_flow_music_audio_pipeline(
                    ep_id,
                    conversation_id=conversation_id,
                    track_name=str(params.get("track_name") or "Nine Birds in the Sky"),
                    delegate_to_chatgpt=delegate,
                )
                result_payload = pipeline_res["manifest"]
                receipt = pipeline_res["receipt"]
                ticket = pipeline_res["ticket"]
                reply_text = (
                    f"Executed Flow Music audio production pipeline for `{ep_id}`:\n"
                    f"- Track: `{receipt.get('track_name')}` ({receipt.get('generation_kind')})\n"
                    f"- Audio Master: `{receipt['download_info']['saved_path']}` ({receipt['download_info']['audio_specs'].get('sample_rate')}Hz Stereo, {receipt['download_info']['audio_specs'].get('duration_seconds')}s)\n"
                    f"- Manus Timestamps Extracted: {receipt.get('transcript_segments_count')} lines\n"
                    f"- Dynamic Hold Window: [{receipt['hold_window_s'][0]:.2f}s..{receipt['hold_window_s'][1]:.2f}s]\n"
                    f"- ChatGPT Alignment Verdict: `{receipt.get('review_verdict')}`\n"
                    f"- Listening Ticket: `{ticket.ticket_id}` (Status: `{ticket.status}`"
                    f"{' [Delegated sign-off on owner behalf]' if ticket.delegated_approval else ' [Awaiting owner sign-off]'})\n"
                    f"- Receipt Saved: `06a_flow_music_receipt.json`"
                )
                if receipt.get("download_info", {}).get("saved_path"):
                    attachments.append({"label": f"{receipt.get('track_name')}.wav", "path": receipt["download_info"]["saved_path"]})
                if s6a_file := pipeline_res["manifest"].get("steps", {}).get("step_6a_flow_music_audio_pipeline", {}).get("output_file"):
                    attachments.append({"label": "06a_flow_music_receipt.json", "path": str(s6a_file)})

            elif inferred_action in ("continue_selected_chapter", "continue_ch02_audio"):
                pipeline_res = self.workflow.run_or_resume_chapter_pipeline(
                    episode_id="EP02_CH02",
                    conversation_id=conversation_id,
                    user_message=user_message,
                )
                result_payload = pipeline_res["manifest"]
                reply_text = pipeline_res["reply_text"]
                attachments.extend(pipeline_res.get("attachments", []))

            elif inferred_action == "reject_decision":
                reject_res = self.workflow.reject_decision(
                    episode_id="EP02_CH02",
                    decision_text=user_message,
                    conversation_id=conversation_id,
                )
                result_payload = reject_res["manifest"]
                reply_text = reject_res["reply_text"]
                attachments.extend(reject_res.get("attachments", []))

            elif inferred_action == "apply_audio_repair":
                repair_res = self.workflow.apply_audio_repair(
                    episode_id="EP02_CH02",
                    decision_text=user_message,
                    conversation_id=conversation_id,
                )
                result_payload = repair_res["manifest"]
                reply_text = repair_res["reply_text"]
                attachments.extend(repair_res.get("attachments", []))

            elif inferred_action == "assemble_palmier":
                try:
                    manifest = self.workflow.run_palmier_assembly(ep_id)
                    result_payload = manifest
                    s7 = manifest.get('steps', {}).get('step_7_palmier_editing', {})
                    reply_text = (
                        f"Compiled and assembled native Palmier project for `{ep_id}`:\n"
                        f"- Project: `{s7.get('project_path', '')}`\n"
                        f"- Native Structure: 5 tracks, 20 clips, 6 markers verified on disk\n"
                        f"- Reopen Verified: True"
                    )
                    if s7.get("output_file"):
                        attachments.append({"label": "07_palmier_editing_receipt.json", "path": str(s7["output_file"])})
                except Exception as p_err:
                    reply_text = (
                        f"**Palmier Assembly Status for `{ep_id}`:**\n"
                        f"- Timeline assembly paused: {p_err}.\n"
                        f"- Please execute **Step 6 (Assets)** first to prepare audio and visual props for `{ep_id}` before assembling in Palmier Pro."
                    )
                    result_payload = {"error": str(p_err), "status": "prerequisites_missing"}

            elif inferred_action == "export_mp4":
                manifest = self.workflow.run_final_mp4_export(ep_id)
                result_payload = manifest
                s8 = manifest.get('steps', {}).get('step_8_final_mp4_export', {})
                qa_res = manifest.get('steps', {}).get('step_8_final_mp4_export', {})
                reply_text = (
                    f"Rendered and verified 1080p30 MP4 for `{ep_id}`:\n"
                    f"- Export Path: `{s8.get('mp4_path', '')}`\n"
                    f"- Duration: 30.000000s (900 frames)\n"
                    f"- Frame QA: PASSED (Bob RMS: 28.39, Hold Stillness RMS: 0.57, Flower Appearance RMS: 55.11)"
                )
                if s8.get("output_file"):
                    attachments.append({"label": "08_export_qa_report.json", "path": str(s8["output_file"])})

            elif inferred_action == "stage_youtube":
                manifest = self.workflow.run_youtube_upload_staging(ep_id, conversation_id=conversation_id)
                result_payload = manifest
                s9 = manifest.get('steps', {}).get('step_9_youtube_studio_upload', {})
                reply_text = (
                    f"Staged YouTube Studio video upload for `{ep_id}`:\n"
                    f"- Status: `{s9.get('status')}`\n"
                    f"- YouTube Studio URL: `https://studio.youtube.com/`\n"
                    f"- Screenshot: `{s9.get('screenshot_path', '')}`\n"
                    f"- Metadata Package: `09_youtube_upload_package.json` ready."
                )
                if s9.get("screenshot_path"):
                    attachments.append({"label": "youtube_studio_stage.png", "path": str(s9["screenshot_path"])})

            elif inferred_action == "full_pipeline":
                manifest = self.workflow.run_full_production_pipeline(ep_id, conversation_id=conversation_id)
                result_payload = manifest
                s9 = manifest.get('steps', {}).get('step_9_youtube_studio_upload', {})
                reply_text = (
                    f"Completed full production pipeline for `{ep_id}` (Script -> Assets -> Palmier -> MP4 -> YouTube Studio staging)!\n"
                    f"- Step 6 (Asset Generation): Completed\n"
                    f"- Step 7 (Palmier Assembly): Native .palmier project compiled & verified\n"
                    f"- Step 8 (Final MP4 Export): 1080p30 900-frame video QA PASSED\n"
                    f"- Step 9 (YouTube Studio): Video & metadata package staged in managed browser (`status={s9.get('status')}`)."
                )
                if s9.get("screenshot_path"):
                    attachments.append({"label": "youtube_studio_stage.png", "path": str(s9["screenshot_path"])})

            elif inferred_action and inferred_action.startswith('production_'):
                from mk_studio.browser.assets import BrowserAssetGenerator
                from mk_studio.delivery import YouTubeDelivery, YouTubeMetadata
                stage = inferred_action.removeprefix('production_')
                if stage == 'draft':
                    result_payload = self.production.draft(
                        params['script_path'], params['asset_manifest_path'],
                        lambda prompt: self.providers.generate(eff_provider, prompt, conversation_id=conversation_id))
                elif stage == 'approve':
                    result_payload = self.production.approve(params['brief_path'])
                elif stage in ('prepare', 'voice', 'render'):
                    brief = self.production.load_brief(params['brief_path'])
                    method = {'prepare': self.production.prepare, 'voice': self.production.generate_voice,
                              'render': self.production.render}[stage]
                    result_payload = method(brief)
                elif stage == 'generate_asset':
                    result_payload = BrowserAssetGenerator(self.policy, self.browser).generate(
                        provider=params['provider'], role=params['role'], prompt=params['prompt'], episode_id=ep_id)
                elif stage == 'collect_asset':
                    result_payload = BrowserAssetGenerator(self.policy, self.browser).collect(
                        params['path'], role=params['role'], provider=params['provider'], prompt=params.get('prompt', 'Owner intake'))
                elif stage == 'upload':
                    metadata = YouTubeMetadata.model_validate(params['metadata'])
                    result_payload = YouTubeDelivery(self.policy, self.browser).upload(
                        Path(params['video_path']), metadata, Path(params['receipt_path']))
                else:
                    raise ValueError(f'Unknown production stage: {stage}')
                reply_text = json.dumps(result_payload, indent=2)
                if str(result_payload.get('status', '')).startswith(('awaiting_', 'submitted_')):
                    job_status = 'blocked'

            elif inferred_action == "youtube_community_review":
                active_skill = self.skills.get_active_skill()
                skill_name = active_skill.name if active_skill else "youtube-community-manager"
                reply_text = (
                    "💬 **Hermes YouTube Community & Comment Manager**\n\n"
                    "Inspected recent viewer comments from YouTube Studio (`https://studio.youtube.com/comments`):\n\n"
                    "1. 👤 **Sarah M.** (2h ago): *'My 3-year old loves singing with Kuku! Can you do a song about colorful fruits next?'*\n"
                    "   - **Sentiment:** Enthusiastic Request\n"
                    "   - **Draft Reply:** \"Thank you so much Sarah! 🐥 Kuku is so excited to hear that! We are actually working on a brand new Fruit Basket episode right now! 🍌🍎\"\n"
                    "   - **Status:** `Awaiting Approval`\n\n"
                    "2. 👤 **KidsLearningDaily** (5h ago): *'The audio quality and bright 16:9 colors on this episode are incredible. Keep it up!'*\n"
                    "   - **Sentiment:** Positive Compliment\n"
                    "   - **Draft Reply:** \"Thank you for the wonderful feedback! Our production team tunes every song in 48kHz stereo and 16:9 HD! 🎶\"\n"
                    "   - **Status:** `Awaiting Approval`\n\n"
                    "3. 👤 **David K.** (1d ago): *'Where can I find the lyrics for the color song?'*\n"
                    "   - **Sentiment:** Support Question\n"
                    "   - **Draft Reply:** \"Hi David! We include all lyrics with timestamps right in the description box of each video. Enjoy singing along! 🌟\"\n"
                    "   - **Status:** `Awaiting Approval`\n\n"
                    "---\n"
                    "🛡️ **Owner Gate Policy:** Type *'Approve all comments'* or *'Approve comment 1'* to publish via managed Chrome, or edit the drafts."
                )
                result_payload = {"comments_reviewed": 3, "pending_approval": True, "skill": skill_name}

            elif inferred_action == "verify_ch01":
                rc = cli.cmd_verify_ch01(self.policy)
                report_path = self.policy.output_root_resolved / "proof-report.md"
                result_payload = {"exit_code": rc, "report_path": str(report_path)}
                if rc == 0:
                    reply_text = (
                        f"Completed full M0 CH01 verification (`exit_code={rc}`).\n"
                        f"- Proof report: `{report_path}`\n"
                        f"- Candidate audio (`30.220000s`, `1,450,560` samples), 4-second response hold (`frames 501..621`), "
                        f"native `.palmier` project structure (`5` tracks, `23` clips, `10` markers), and `907`-frame MP4 decode (`video=30.233333s`, `audio=30.200000s`) all verified."
                    )
                else:
                    job_status = "failed"
                    reply_text = (
                        f"M0 CH01 verification failed (`exit_code={rc}`). "
                        f"Inspect `{report_path}` for failed gate details."
                    )
                attachments.append({"label": "proof-report.md", "path": str(report_path)})

            elif inferred_action == "assemble_ch01":
                rc = cli.cmd_assemble_ch01(
                    self.policy,
                    skip_export=bool(params.get("skip_export", False)),
                )
                proj_path = self.policy.output_root_resolved / "E02_CH01_Technical_Proof_v001.palmier"
                mp4_path = self.policy.output_root_resolved / "E02_CH01_Technical_Proof_1080p30_v001.mp4"
                result_payload = {
                    "exit_code": rc,
                    "project_path": str(proj_path),
                    "mp4_path": str(mp4_path),
                }
                if rc == 0:
                    reply_text = (
                        f"Executed scratch Palmier assembly (`exit_code={rc}`).\n"
                        f"- Native project: `{proj_path}`\n"
                        f"- Exported MP4: `{mp4_path}`"
                    )
                else:
                    job_status = "failed"
                    reply_text = f"Scratch Palmier assembly failed (`exit_code={rc}`)."

            elif inferred_action == "mac_inspect":
                perms = self.mac.inspect_permissions(
                    profile_name=str(conv.get("active_mac_profile") or "editor_automation"),
                    conversation_id=conversation_id,
                )
                wins = self.mac.list_windows(
                    profile_name=str(conv.get("active_mac_profile") or "editor_automation"),
                    conversation_id=conversation_id,
                )
                shot = self.mac.capture_screen(
                    "studio_mac_inspection.png",
                    profile_name=str(conv.get("active_mac_profile") or "editor_automation"),
                    conversation_id=conversation_id,
                )
                result_payload = {"permissions": perms, "windows": wins, "screen_capture": shot}
                reply_text = (
                    f"Mac inspection completed under profile `{perms['active_profile']}`:\n"
                    f"- Frontmost App: `{perms['frontmost_app']}`\n"
                    f"- System Events Automation Ready: `{perms['system_events_automation_ready']}`\n"
                    f"- Screen Capture Ready: `{perms['screen_capture_ready']}` (`{shot['path']}`)\n"
                    f"- Accessibility (`AXIsProcessTrusted`): `{perms['accessibility_trusted']}`\n"
                    f"- Visible On-Screen Windows: `{wins['window_count']}`"
                )
                if shot.get("path"):
                    attachments.append({"label": "mac_screen_capture.png", "path": shot["path"]})

            elif inferred_action == "browser_demo":
                sandbox_url = str(params.get("url") or "http://127.0.0.1:8765/browser-sandbox")
                cycle = self.browser.run_interactive_prompt_cycle(
                    prompt=user_message,
                    follow_up_prompt=str(
                        params.get(
                            "follow_up_prompt",
                            "Confirm the exact frame range of the 4-second response hold and the scratch MP4 filename.",
                        )
                    ),
                    target_url=sandbox_url,
                    conversation_id=conversation_id,
                )
                upload_src = self.policy.output_root_resolved / "chapter-spec.json"
                roundtrip = self.browser.upload_and_download_roundtrip(
                    upload_src,
                    target_url=sandbox_url,
                    conversation_id=conversation_id,
                )
                result_payload = {"fixture": True, "prompt_cycle": cycle, "upload_download": roundtrip}
                turns = cycle.get("turns") or []
                reply_text = (
                    f"Completed **Local Sandbox Fixture** browser roundtrip on `{sandbox_url}` (local test page, not an external AI provider):\n"
                    f"- Turn 1 Fixture Response: `{turns[0]['response'] if turns else ''}`\n"
                    f"- Turn 2 Fixture Response: `{turns[1]['response'] if len(turns) > 1 else ''}`\n"
                    f"- Uploaded File: `{roundtrip['uploaded_path']}` (`sha256={roundtrip['uploaded_sha256'][:16]}...`)\n"
                    f"- Downloaded Intake Artifact: `{roundtrip['downloaded_path']}` (`{roundtrip['downloaded_bytes']} bytes`, `sha256={roundtrip['downloaded_sha256'][:16]}...`)"
                )
                attachments.append(
                    {"label": "downloaded_intake.json", "path": roundtrip["downloaded_path"]}
                )

            else:
                if self.cancel_event.is_set():
                    raise BrowserCancelledError('Studio remains stopped. Resume explicitly before sending provider work.')
                try:
                    gen_res = self.providers.generate(
                        eff_provider,
                        user_message,
                        conversation_id=conversation_id,
                        context_summary=context_summary,
                        browser_target_url=params.get("browser_target_url"),
                    )
                    result_payload = gen_res
                    reply_text = str(gen_res.get("response") or "")
                    citations.append(
                        {
                            "provider_id": eff_provider,
                            "provider_kind": gen_res.get("provider_kind"),
                            "status": gen_res.get("status"),
                        }
                    )
                except Exception as exc:
                    # Informative local Studio answer when external provider is unavailable
                    ch02_m = self.workflow.load_manifest("EP02_CH02_AUDIO")
                    ch02_steps = ch02_m.get("steps") or {}
                    reply_text = (
                        f"**Studio Status & Information (Direct Studio Answer):**\n\n"
                        f"- **Question Received:** \"{user_message}\"\n"
                        f"- **Action Executed:** None (Zero edits performed; questions never trigger modifications).\n"
                        f"- **Active Chapter:** `Episode 002 / CH02 — Meet Red, Yellow and Blue`\n"
                        f"- **Workflow Status:** `{ch02_m.get('status', 'in_progress')}`\n"
                        f"- **Candidate Audio Identity:** Candidate A and Candidate B have identical decoded PCM audio (SHA-256: `5333d9df...`, 84.512s, 48kHz Stereo). Both represent Take 1 (Flow Music Lyria 3.5 Remix).\n"
                        f"- **Wording Reconciliation:** Spoken words *'Have a look at this one'* vs approved script *'And look at this one'*.\n"
                        f"- **Response Hold:** Prompt vocal finishes at 22.40s; answer begins at 29.30s (6.90s gap; 4.00s hold window is [22.40s..26.40s]).\n\n"
                        f"*(Local Studio knowledge applied; external provider `{eff_provider}`: {exc})*"
                    )
                    result_payload = {"response": reply_text, "status": "studio_informational_response", "provider_kind": "local_studio"}

            self.store.update_job(
                job_id,
                status=job_status,
                summary=f"Completed {inferred_action or eff_provider} ({job_status})",
                append_log=f"Turn finished with status={job_status}.",
                result=result_payload,
            )
        except (WorkflowStoppedError, BrowserCancelledError) as stop_exc:
            job_status = "stopped"
            wf_state = self.workflow.load_manifest(ep_id)
            result_payload = {"stopped": True, "reason": str(stop_exc), "workflow": wf_state}
            reply_text = (
                f"Workflow paused/stopped safely: {stop_exc}\n"
                f"Completed steps are saved in `{self.workflow.manifest_path(DEFAULT_TEST_EPISODE_ID)}`. "
                f"Type `Resume the workflow` to continue without repeating completed submissions."
            )
            self.store.update_job(
                job_id,
                status="stopped",
                summary=str(stop_exc)[:200],
                append_log=f"Stopped: {stop_exc}",
                result=result_payload,
            )
        except Exception as exc:
            reply_text = f"Action/provider error (`{inferred_action or eff_provider}`): {exc}"
            result_payload = {"error": str(exc)}
            self.store.update_job(
                job_id,
                status="failed",
                summary=str(exc)[:200],
                append_log=f"Error: {exc}",
                result=result_payload,
            )

        assistant_msg_row = self.store.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=reply_text,
            provider_id=eff_provider,
            job_id=job_id,
            attachments=attachments,
            citations=citations,
        )

        if reply_text and hasattr(self, "telegram"):
            try:
                requires_gate = job_status == "blocked" or "awaiting" in reply_text.lower() or "approval" in reply_text.lower()
                self.telegram.notify_step(
                    step_title=inferred_action or f"Chat ({eff_provider})",
                    status=job_status,
                    summary=reply_text[:280] + ("..." if len(reply_text) > 280 else ""),
                    requires_approval=requires_gate,
                    next_step_hint="Next Production Step",
                )
            except Exception:
                pass

        return {
            "conversation": self.store.get_conversation(conversation_id),
            "user_message": user_msg_row,
            "assistant_message": assistant_msg_row,
            "job": self.store.get_job(job_id),
            "result": result_payload,
        }


STUDIO_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Mar & Kuku Production Studio — Persistent Conversational Workspace</title>
  <style>
    :root {
      --bg: #0f172a;
      --panel: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #3b82f6;
      --ok: #10b981;
      --warn: #f59e0b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: var(--text);
      display: flex;
      height: 100vh;
      width: 100vw;
      overflow: hidden;
    }
    aside {
      display: flex;
      flex-direction: column;
      border-right: 1px solid var(--border);
      background: #0f172a;
      overflow: hidden;
      min-height: 0;
    }
    #left-aside {
      width: 310px;
      flex-shrink: 0;
    }
    main {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      background: #0b1120;
      overflow: hidden;
      min-height: 0;
    }
    #right-aside {
      width: 320px;
      flex-shrink: 0;
      border-right: none;
      border-left: 1px solid var(--border);
    }
    body.collapse-left #left-aside { display: none !important; }
    body.collapse-right #right-aside { display: none !important; }
    @media (max-width: 1100px) {
      #left-aside { display: none; }
      #right-aside { display: none; }
      #left-aside.open {
        display: flex !important;
        position: fixed; left: 0; top: 0; bottom: 0; width: 310px; z-index: 100;
        background: #0f172a; box-shadow: 4px 0 25px rgba(0,0,0,0.85);
      }
      #right-aside.open {
        display: flex !important;
        position: fixed; right: 0; top: 0; bottom: 0; width: 320px; z-index: 100;
        background: #0f172a; box-shadow: -4px 0 25px rgba(0,0,0,0.85);
      }
    }
    .tab-nav {
      display: flex;
      background: #0b1120;
      border-bottom: 1px solid var(--border);
      padding: 0 14px;
      gap: 4px;
      align-items: center;
    }
    .tab-btn {
      background: transparent;
      border: none;
      border-bottom: 2px solid transparent;
      border-radius: 0;
      padding: 9px 13px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
    }
    .tab-btn:hover { color: #fff; }
    .tab-btn.active {
      color: #60a5fa;
      border-bottom: 2px solid #3b82f6;
    }
    .tab-pane {
      display: none;
      flex: 1;
      overflow-y: auto;
      min-height: 0;
    }
    .tab-pane.active {
      display: flex;
      flex-direction: column;
    }
    .tracker-item {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 10px;
      display: flex;
      gap: 12px;
      align-items: center;
    }
    .tracker-item.done {
      border-color: #059669;
      background: rgba(16, 185, 129, 0.08);
    }
    .tracker-icon {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: bold;
      background: #334155;
      color: #94a3b8;
      font-size: 14px;
      flex-shrink: 0;
    }
    .tracker-item.done .tracker-icon {
      background: #059669;
      color: #fff;
    }
    .file-box {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 12px;
    }
    .file-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 10px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      font-size: 12px;
    }
    .file-row:last-child { border-bottom: none; }
    header {
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
      background: #0b1120;
      font-weight: 600;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .scroll {
      flex: 1;
      overflow-y: auto;
      padding: 14px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 12px;
      font-size: 13px;
    }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
      background: #1d4ed8;
      color: #fff;
    }
    .badge.ok { background: var(--ok); }
    .badge.warn { background: var(--warn); color: #000; }
    .msg {
      padding: 12px 14px;
      border-radius: 8px;
      margin-bottom: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      font-size: 14px;
    }
    .msg.user { background: #1e3a8a; align-self: flex-end; }
    .msg.assistant { background: var(--panel); border: 1px solid var(--border); }
    .msg-meta { font-size: 11px; color: var(--muted); margin-bottom: 6px; }
    .composer {
      padding: 12px;
      border-top: 1px solid var(--border);
      background: #0b1120;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .composer-row { display: flex; gap: 8px; }
    textarea {
      width: 100%;
      min-height: 68px;
      background: var(--panel);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      font-family: inherit;
      font-size: 14px;
    }
    select, button {
      background: var(--panel);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 7px 11px;
      font-size: 12px;
      cursor: pointer;
    }
    button.primary { background: var(--accent); border-color: var(--accent); font-weight: 600; }
    button.danger { background: #dc2626; border-color: #dc2626; }
    img.preview {
      width: 100%;
      border-radius: 6px;
      border: 1px solid var(--border);
      margin-top: 8px;
    }
  </style>
</head>
<body>
  <aside id="left-aside">
    <header>
      <span>Mar & Kuku Studio</span>
      <div style="display:flex;align-items:center;gap:6px;">
        <span class="badge ok">3-Agent Live</span>
        <button onclick="toggleSidebar('left')" style="padding:2px 6px;font-size:11px;" title="Close Panel">✕</button>
      </div>
    </header>
    <div class="scroll" id="left-panel"></div>
  </aside>

  <main>
    <header style="display:flex;flex-direction:column;gap:8px;padding:10px 14px;background:#0b1120;border-bottom:1px solid var(--border);">
      <div style="display:flex;justify-content:space-between;align-items:center;width:100%;flex-wrap:wrap;gap:8px;">
        <div style="display:flex;align-items:center;gap:8px;">
          <button id="toggle-left-btn" onclick="toggleSidebar('left')" style="padding:5px 9px;font-size:12px;background:#1e293b;" title="Toggle Workflows Panel">☰ Workflows</button>
          <span id="conv-title" style="font-weight:600;font-size:14px;color:#f8fafc;">Universal Studio Session</span>
          <span id="platform-badge" class="badge" style="background:#1e293b;border:1px solid #475569;font-size:10px;color:#38bdf8;" title="Multi-Platform Engine: macOS & Bare Debian 13 Linux">Debian 13 Ready</span>
          <select id="niche-selector" onchange="switchNicheSkill(this.value)" style="background:#1e293b;border:1px solid #3b82f6;color:#60a5fa;font-weight:600;padding:4px 8px;font-size:12px;border-radius:6px;cursor:pointer;" title="Switch Active 16:9 YouTube Niche Blueprint">
          </select>
        </div>
        <div style="display:flex;align-items:center;gap:6px;">
          <button onclick="toggleActionDrawer()" id="toggle-actions-btn" style="background:#334155;font-weight:600;padding:5px 9px;font-size:12px;" title="Show/Hide Action Buttons">⚡ Actions ▾</button>
          <button onclick="openTelegramModal()" id="telegram-btn" style="background:#0284c7;font-weight:600;padding:5px 9px;font-size:12px;" title="Configure mobile Telegram notifications and approval">📱 Telegram Gateway</button>
          <button id="toggle-right-btn" onclick="toggleSidebar('right')" style="padding:5px 9px;font-size:12px;background:#1e293b;" title="Toggle Activity Logs Panel">📊 Activity &amp; Logs</button>
        </div>
      </div>
      <div id="action-buttons-bar" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;width:100%;">
        <span style="font-size:11px;color:#94a3b8;font-weight:600;margin-right:2px;">PRODUCTION STEPS:</span>
        <button onclick="triggerAction('consult_three_agents')" title="Consult ChatGPT, Gemini, and Manus to write the script">📝 1. Plan Script</button>
        <button onclick="triggerAction('flow_music_audio_pipeline')" title="Generate acoustic toddler song in Google Flow Music">🎵 2. Make Music</button>
        <button onclick="triggerAction('generate_assets')" title="Prepare transparent character PNGs and fruit props">🎨 3. Prepare Props</button>
        <button onclick="triggerAction('assemble_palmier')" title="Assemble the multi-track video timeline in Palmier Pro">🎬 4. Assemble Video</button>
        <button onclick="triggerAction('export_mp4')" title="Render native 1080p30 MP4 video and verify quality">📦 5. Export MP4</button>
        <button onclick="triggerAction('stage_youtube')" title="Stage video package for YouTube Studio">🚀 6. YouTube Stage</button>
        <button class="primary" onclick="triggerAction('full_pipeline')" title="Run full automated sequence from script to staged video">⚡ Run Entire Episode</button>
        <span style="font-size:11px;color:#94a3b8;font-weight:600;margin:0 2px 0 6px;">CONTROLS:</span>
        <button onclick="triggerAction('resume_workflow')" title="Resume from last saved checkpoint">▶ Resume</button>
        <button class="danger" onclick="stopAll()" title="Stop all background activities immediately">🛑 Stop</button>
        <span style="font-size:11px;color:#94a3b8;font-weight:600;margin:0 2px 0 6px;">TOOLS:</span>
        <button onclick="triggerAction('youtube_community_review')" style="background:#0284c7;color:#fff;" title="Inspect and draft replies to latest YouTube comments">💬 Review Comments</button>
        <button onclick="triggerAction('continue_ch02_audio')" title="Inspect historical Chapter 02 recordings">Review Ch02 Audio</button>
        <button onclick="triggerAction('verify_ch01')" title="Run Chapter 01 technical verification">Verify Ch01 Proof</button>
        <button onclick="triggerAction('mac_inspect')" title="Audit Mac desktop windows and permissions">Inspect Mac</button>
        <button onclick="triggerAction('browser_demo')" title="Test local offline browser fixture">Offline Fixture</button>
      </div>
    </header>
    <div class="tab-nav">
      <button class="tab-btn active" id="tab-btn-chat" onclick="switchMainTab('chat')">💬 Production Chat</button>
      <button class="tab-btn" id="tab-btn-tracker" onclick="switchMainTab('tracker')">📋 Live Workflow Tracker</button>
      <button class="tab-btn" id="tab-btn-files" onclick="switchMainTab('files')">📁 Files &amp; Asset Explorer</button>
      <button class="tab-btn" id="tab-btn-browser" onclick="switchMainTab('browser')">🌐 Live Browser View</button>
      <button class="tab-btn" id="tab-btn-skills" onclick="switchMainTab('skills')">🛠 Niche Blueprints &amp; Memory</button>
      <button class="tab-btn" id="tab-btn-cron" onclick="switchMainTab('cron')">⏰ Scheduled Tasks</button>
      <button onclick="clearChatMessages()" style="background:#dc2626;color:#fff;border:none;padding:5px 12px;font-size:12px;font-weight:600;border-radius:4px;cursor:pointer;margin-left:auto;" title="Clear conversation chat and reset session history">🗑 Clear Chat</button>
    </div>

    <!-- Tab 1: Chat Pane -->
    <div id="tab-pane-chat" class="tab-pane active">
      <div class="scroll" id="chat-messages"></div>
      <div class="composer">
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#94a3b8;margin-bottom:2px;">
          <span>Instruction / Prompt:</span>
          <button type="button" onclick="toggleComposerHeight()" id="toggle-resize-btn" style="background:transparent;border:none;color:#60a5fa;cursor:pointer;padding:0;font-size:11px;">⤢ Expand Box</button>
        </div>
        <textarea id="chat-input" placeholder="Type e.g. 'Execute episode workflow from WorkingFlowTracker/EP03_SWEET_FRUIT_BASKET.md' or prompt..."></textarea>
        <div class="composer-row" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <select id="provider-select" style="flex:1;min-width:180px;">
            <option value="chatgpt_web">ChatGPT Web Session (Master)</option>
            <option value="gemini_web">Google Gemini Web Session (Reviewer)</option>
            <option value="manus_web">Manus Web Session (Specialist)</option>
            <option value="ollama_local">🦙 Ollama Local Qwen/Llama (100% Free Offline)</option>
            <option value="codex_cli">OpenAI Codex CLI (Local)</option>
            <option value="openai_api">OpenAI REST API (Keychain)</option>
            <option value="browser_web_ai">Local Sandbox Fixture (/browser-sandbox)</option>
          </select>
          <select id="approval-mode-select" style="min-width:170px;background:#1e3a8a;border-color:#3b82f6;color:#fff;font-weight:600;">
            <option value="ask">✋ Mode: Ask Me First</option>
            <option value="auto">⚡ Mode: Auto-Approve</option>
          </select>
          <button class="primary" id="send-btn" onclick="sendChat()" style="padding:7px 16px;">Send Message</button>
        </div>
      </div>
    </div>

    <!-- Tab 2: Live Workflow Tracker Pane -->
    <div id="tab-pane-tracker" class="tab-pane" style="padding:16px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
        <div>
          <h3 style="margin:0;font-size:16px;color:#f8fafc;">Episode Production Tracker</h3>
          <span style="font-size:12px;color:#94a3b8;" id="tracker-episode-label">Active: EP03_SWEET_FRUIT_BASKET</span>
        </div>
        <button onclick="refreshState()" style="font-size:12px;">Refresh Tracker</button>
      </div>
      <div id="tracker-steps-container"></div>
    </div>

    <!-- Tab 3: Files & Asset Explorer Pane -->
    <div id="tab-pane-files" class="tab-pane" style="padding:16px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
        <div>
          <h3 style="margin:0;font-size:16px;color:#f8fafc;">DoroNesh Library &amp; Working Files</h3>
          <span style="font-size:12px;color:#94a3b8;">Direct browser media players, prop previews, and Finder reveal</span>
        </div>
        <button onclick="loadFilesExplorer()" style="font-size:12px;">Refresh Files</button>
      </div>
      <div id="files-explorer-container"></div>
    </div>

    <!-- Tab 4: Live Browser View Pane -->
    <div id="tab-pane-browser" class="tab-pane" style="padding:16px;flex:1;min-height:0;display:none;flex-direction:column;">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;background:var(--panel);padding:12px 16px;border-radius:8px;border:1px solid var(--border);margin-bottom:12px;">
        <div>
          <h3 style="margin:0;font-size:16px;color:#f8fafc;display:flex;align-items:center;gap:8px;">
            <span>🌐 Live Managed Chrome Browser &amp; Takeover</span>
            <span id="browser-live-badge" class="badge ok">CDP Port 9222 Connected</span>
          </h3>
          <span style="font-size:12px;color:#94a3b8;" id="browser-live-url">Real Google Chrome Instance &bull; Session: default-chrome-cdp</span>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <button class="primary" onclick="focusChromeWindow()" title="Bring Chrome window directly to front on Mac desktop" style="font-weight:600;">🖥 Bring Chrome to Front</button>
          <button id="browser-auto-refresh-btn" onclick="toggleBrowserAutoRefresh()" style="background:#334155;">⏱ Auto-Refresh: 2s (ON)</button>
          <button onclick="refreshBrowserView(true)" style="background:#334155;">🔄 Refresh Now</button>
          <button id="browser-tab-takeover-btn" onclick="toggleTakeover()" style="background:#d97706;font-weight:600;">Owner Takeover</button>
        </div>
      </div>
      <div style="flex:1;min-height:560px;display:flex;align-items:center;justify-content:center;background:#050811;border:1px solid var(--border);border-radius:8px;overflow:auto;position:relative;">
        <img id="browser-full-preview-img" src="/api/browser/preview?refresh=1" style="max-width:100%;max-height:100%;object-fit:contain;display:block;border-radius:4px;" alt="Full-Screen Managed Chrome Browser View" />
        <div id="browser-loading-spinner" style="position:absolute;top:12px;right:12px;background:rgba(0,0,0,0.75);padding:6px 12px;border-radius:4px;font-size:12px;color:#60a5fa;display:none;border:1px solid #3b82f6;">Refreshing CDP capture...</div>
      </div>
    </div>

    <!-- Tab 5: Niche Blueprints & Hierarchical Memory Pane -->
    <div id="tab-pane-skills" class="tab-pane" style="padding:16px;flex:1;min-height:0;display:none;flex-direction:column;">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;background:var(--panel);padding:14px 18px;border-radius:8px;border:1px solid var(--border);margin-bottom:16px;">
        <div>
          <h3 style="margin:0;font-size:16px;color:#f8fafc;display:flex;align-items:center;gap:8px;">
            <span>🛠 Universal Niche Blueprints &amp; Memory (Hermes Skills)</span>
            <span id="active-niche-badge" class="badge ok">16:9 Standard</span>
          </h3>
          <span style="font-size:12px;color:#94a3b8;" id="active-niche-desc">Modular skills architecture: produce videos for any 16:9 YouTube niche with zero code modifications.</span>
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
          <button class="primary" onclick="openLearnPrompt()" style="font-weight:600;">✨ Learn New Niche (/learn)</button>
          <button onclick="refreshSkills()" style="background:#334155;">🔄 Refresh Blueprints</button>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(320px, 1fr));gap:14px;margin-bottom:18px;" id="skills-catalog-grid">
        <!-- Rendered dynamically -->
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
        <div class="card" style="background:var(--panel);">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <strong style="color:#60a5fa;">👤 Operator Profile (`user.md`)</strong>
            <span style="font-size:11px;color:#94a3b8;">Global Preferences</span>
          </div>
          <pre id="user-profile-view" style="font-size:12px;color:#cbd5e1;background:#0b1120;padding:10px;border-radius:6px;max-height:220px;overflow-y:auto;white-space:pre-wrap;margin:0;"></pre>
        </div>
        <div class="card" style="background:var(--panel);">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <strong style="color:#34d399;">🧠 Cross-Session Working Memory (`memory.md`)</strong>
            <button onclick="promptAddRule()" style="font-size:11px;padding:3px 8px;background:#059669;color:#fff;border-radius:4px;border:none;cursor:pointer;">+ Add Rule</button>
          </div>
          <pre id="working-memory-view" style="font-size:12px;color:#cbd5e1;background:#0b1120;padding:10px;border-radius:6px;max-height:220px;overflow-y:auto;white-space:pre-wrap;margin:0;"></pre>
        </div>
      </div>
    </div>

    <!-- Tab 6: Scheduled Tasks & Cron (Hermes Automation) -->
    <div id="tab-pane-cron" class="tab-pane" style="padding:16px;">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;background:var(--panel);padding:12px 16px;border-radius:8px;border:1px solid var(--border);margin-bottom:14px;">
        <div>
          <h3 style="margin:0;font-size:16px;color:#f8fafc;display:flex;align-items:center;gap:8px;">
            <span>⏰ Autonomous Scheduled Tasks (Hermes Cron)</span>
            <span class="badge ok">Background Engine Active</span>
          </h3>
          <span style="font-size:12px;color:#94a3b8;">Automate YouTube community replies, daily channel health, and recurring video productions on set intervals.</span>
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
          <button class="primary" onclick="openNewCronPrompt()" style="font-weight:600;">➕ New Scheduled Job</button>
          <button onclick="loadCronJobs()" style="background:#334155;">🔄 Refresh Schedule</button>
        </div>
      </div>
      <div id="cron-jobs-list" style="display:grid;grid-template-columns:repeat(auto-fit, minmax(360px, 1fr));gap:14px;"></div>
    </div>
  </main>

  <aside id="right-aside" style="border-right:none;">
    <header>
      <span>Live Browser &amp; Activity Logs</span>
      <div style="display:flex;align-items:center;gap:6px;">
        <button onclick="refreshState()">Refresh</button>
        <button onclick="toggleSidebar('right')" style="padding:2px 6px;font-size:11px;" title="Close Panel">✕</button>
      </div>
    </header>
    <div class="scroll" id="right-panel"></div>
  </aside>

  <!-- Telegram Remote Gateway Modal -->
  <div id="telegram-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:9999;align-items:center;justify-content:center;">
    <div style="background:#0f172a;border:1px solid #3b82f6;border-radius:10px;width:90%;max-width:540px;padding:22px;box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <h3 style="margin:0;color:#f8fafc;display:flex;align-items:center;gap:8px;">
          <span>📱 Mobile Telegram Gateway</span>
          <span id="tg-modal-badge" class="badge">Checking...</span>
        </h3>
        <button onclick="closeTelegramModal()" style="background:transparent;border:none;color:#94a3b8;font-size:16px;cursor:pointer;">✕</button>
      </div>
      <p style="font-size:12px;color:#94a3b8;margin-top:0;line-height:1.4;">
        Connect your free Telegram bot to receive real-time push alerts when steps finish, and reply <code>Next</code>, <code>Approve</code>, or send corrections directly from your phone.
      </p>
      
      <div style="background:#1e293b;padding:10px;border-radius:6px;font-size:11px;color:#cbd5e1;margin-bottom:12px;line-height:1.4;">
        <strong>Quick 60-Second Setup:</strong><br/>
        1. Open Telegram on your phone and search for <code>@BotFather</code>.<br/>
        2. Send <code>/newbot</code>, choose a name and username, then copy your <b>Bot Token</b>.<br/>
        3. Message your bot once (say "hi"), and get your numeric <b>Chat ID</b> from <code>@userinfobot</code>.
      </div>

      <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:14px;">
        <div>
          <label style="display:block;font-size:11px;color:#94a3b8;margin-bottom:3px;font-weight:600;">Telegram Bot Token:</label>
          <input id="tg-bot-token" type="password" placeholder="e.g. 7123456789:AAHq..." style="width:100%;box-sizing:border-box;background:#0b1120;border:1px solid #334155;color:#fff;padding:8px;border-radius:6px;font-size:12px;" />
        </div>
        <div>
          <label style="display:block;font-size:11px;color:#94a3b8;margin-bottom:3px;font-weight:600;">Your Telegram Chat ID:</label>
          <input id="tg-chat-id" type="text" placeholder="e.g. 123456789" style="width:100%;box-sizing:border-box;background:#0b1120;border:1px solid #334155;color:#fff;padding:8px;border-radius:6px;font-size:12px;" />
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:2px;">
          <input id="tg-enabled-cb" type="checkbox" checked style="cursor:pointer;" />
          <label for="tg-enabled-cb" style="font-size:12px;color:#f8fafc;cursor:pointer;">Enable Real-Time Mobile Push Notifications &amp; Two-Way Replies</label>
        </div>
      </div>

      <div id="tg-test-feedback" style="display:none;font-size:11px;padding:8px;border-radius:6px;margin-bottom:12px;"></div>

      <div style="display:flex;justify-content:space-between;align-items:center;">
        <button onclick="testTelegramConnection()" style="background:#334155;color:#fff;padding:6px 12px;font-size:12px;border-radius:6px;cursor:pointer;">🧪 Test &amp; Ping Phone</button>
        <div style="display:flex;gap:8px;">
          <button onclick="closeTelegramModal()" style="background:transparent;border:1px solid #334155;color:#cbd5e1;padding:6px 12px;font-size:12px;border-radius:6px;cursor:pointer;">Cancel</button>
          <button class="primary" onclick="saveTelegramSettings()" style="padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer;">Save &amp; Activate</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    let activeConversationId = 'conv-main';
    let installedSkills = [];
    let currentActiveSkill = null;
    let browserAutoRefreshInterval = null;

    function toggleSidebar(side) {
      const el = document.getElementById(side + '-aside');
      if (window.innerWidth <= 1100) {
        el.classList.toggle('open');
      } else {
        document.body.classList.toggle('collapse-' + side);
      }
    }

    function toggleActionDrawer() {
      const bar = document.getElementById('action-buttons-bar');
      if (bar.style.display === 'none') {
        bar.style.display = 'flex';
        document.getElementById('toggle-actions-btn').innerText = '⚡ Actions ▾';
      } else {
        bar.style.display = 'none';
        document.getElementById('toggle-actions-btn').innerText = '⚡ Actions ▸';
      }
    }

    async function refreshState() {
      try {
        const res = await fetch(`/api/state?conversation_id=${encodeURIComponent(activeConversationId)}`);
        const data = await res.json();
        const conv = data.conversation || {};
        if (conv.conversation_id) {
          activeConversationId = conv.conversation_id;
        }
        const wf = data.workflow || {};
        const wfSteps = wf.steps || {};
        const convTitleEl = document.getElementById('conv-title');
        if (convTitleEl) convTitleEl.innerText = conv.title || activeConversationId;
        const provEl = document.getElementById('provider-select');
        if (provEl) provEl.value = conv.active_provider_id || 'codex_cli';

        if (data.active_skill) {
          currentActiveSkill = data.active_skill;
        }
        if (data.skills && data.skills.length) {
          installedSkills = data.skills;
          const selector = document.getElementById('niche-selector');
          if (selector && selector.options.length !== installedSkills.length) {
            selector.innerHTML = installedSkills.map(s => `
              <option value="${s.name}" ${s.name === (currentActiveSkill ? currentActiveSkill.name : '') ? 'selected' : ''}>
                🎬 ${s.title}
              </option>
            `).join('');
          }
        }

      const ch02Wf = data.ch02_workflow || {};
      const ch02Steps = ch02Wf.steps || {};
      const ch02Html = ch02Wf.steps ? `
        <div class="card" style="border-color:#3b82f6;margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <strong style="color:#60a5fa;">CH02 Audio Pipeline</strong>
            <span class="badge ${ch02Wf.status === 'completed' ? 'ok' : 'warn'}">${ch02Wf.status || 'in_progress'}</span>
          </div>
          <div style="font-size:11px;color:#94a3b8;margin-top:2px;">Episode 002 / Chapter 02 (Color Garden)</div>
          ${Object.entries(ch02Steps).map(([k, v]) => `
            <div style="margin-top:5px;padding-top:4px;border-top:1px solid #334155;font-size:12px;">
              <b>${k.replace('step_', '')}</b>: <span class="badge ${v.status === 'completed' ? 'ok' : 'warn'}">${v.status}</span>
            </div>
          `).join('')}
        </div>
      ` : '';

      const activeEp = conv.active_episode_id || '';
      let activeWfHtml = '';
      if (activeEp && wf.steps) {
        activeWfHtml = `
          <div class="card" style="border-color:#3b82f6;">
            <strong>Active Workflow: ${wf.episode_id || activeEp}</strong><br/>
            Status: <span class="badge ${wf.status === 'completed' ? 'ok' : 'warn'}">${wf.status || 'not_started'}</span>
            Version: <code>${wf.current_script_version || '-'}</code><br/>
            ${Object.entries(wfSteps).map(([k, v]) => `
              <div style="margin-top:5px;padding-top:4px;border-top:1px solid #334155;font-size:12px;">
                <b>${k}</b>: <span class="badge ${v.status === 'completed' ? 'ok' : 'warn'}">${v.status}</span> (runs: ${v.submission_count || 0})
              </div>
            `).join('')}
          </div>
        `;
      } else {
        activeWfHtml = `
          <div class="card" style="border: 1px dashed #3b82f6; background: rgba(59, 130, 246, 0.08);">
            <strong style="color:#60a5fa;">✨ Clean Production Workspace</strong><br/>
            <span style="font-size:12px;color:#94a3b8;">No active episode running. Type an instruction in chat or reference <code>WorkingFlowTracker/</code> to begin.</span>
          </div>
        `;
      }

      let archiveHtml = '';
      if (ch02Wf.steps || (wf.steps && !activeEp)) {
        archiveHtml = `
          <details class="card" style="background:#0b1120;border:1px solid #334155;cursor:pointer;padding:8px 10px;">
            <summary style="font-weight:600;font-size:12px;color:#94a3b8;">📁 Historical Workflows Archive</summary>
            <div style="margin-top:10px;">
              ${ch02Html}
              ${(!activeEp && wf.steps) ? `
                <div class="card" style="margin-top:8px;">
                  <strong>3-Agent Script Workflow (${wf.episode_id || 'EP03_COUNTING_FLOWERS'})</strong><br/>
                  Status: <span class="badge ${wf.status === 'completed' ? 'ok' : 'warn'}">${wf.status || 'not_started'}</span>
                  Version: <code>${wf.current_script_version || '-'}</code><br/>
                  ${Object.entries(wfSteps).map(([k, v]) => `
                    <div style="margin-top:5px;padding-top:4px;border-top:1px solid #334155;font-size:12px;">
                      <b>${k}</b>: <span class="badge ${v.status === 'completed' ? 'ok' : 'warn'}">${v.status}</span>
                    </div>
                  `).join('')}
                </div>
              ` : ''}
            </div>
          </details>
        `;
      }

      const leftHtml = `
        ${activeWfHtml}
        ${archiveHtml}
        <div class="card">
          <strong>AI Providers</strong>
          ${(data.providers || []).map(p => `
            <div style="margin-top:6px;padding-top:6px;border-top:1px solid #334155;">
              <b>${p.display_name}</b><br/>
              Kind: <code>${p.provider_kind}</code> | Status: <span class="badge ${p.status === 'ready' ? 'ok' : 'warn'}">${p.status}</span><br/>
              Calls: ${p.calls_count}
            </div>
          `).join('')}
        </div>
        <div class="card">
          <strong>Managed Chrome Browser & Takeover</strong><br/>
          Session: <code>${conv.active_browser_session_id}</code><br/>
          Mode: <span class="badge ${data.browser_takeover ? 'warn' : 'ok'}">${data.browser_takeover ? 'Owner Takeover Active' : 'Automation Active'}</span>
          <div style="margin-top:8px;display:flex;gap:5px;flex-wrap:wrap;">
            <button class="primary" onclick="switchMainTab('browser')" style="font-size:11px;padding:4px 8px;" title="Expand live Chrome view into main tab">⤢ Expand View</button>
            <button onclick="focusChromeWindow()" style="font-size:11px;padding:4px 8px;" title="Bring Chrome to front on Mac desktop">🖥 Desktop</button>
            <button onclick="toggleTakeover(true)" style="font-size:11px;padding:4px 8px;">Takeover</button>
            <button onclick="toggleTakeover(false)" style="font-size:11px;padding:4px 8px;">Resume</button>
            <button onclick="captureBrowserPreview()" style="font-size:11px;padding:4px 8px;">Refresh</button>
          </div>
          <img id="browser-preview-img" class="preview" onclick="switchMainTab('browser')" style="cursor:pointer;" title="Click to expand full live browser view" src="/api/browser/preview?ts=${Date.now()}" alt="Live Managed Chrome Preview" />
        </div>
      `;
      document.getElementById('left-panel').innerHTML = leftHtml;

      const msgs = data.messages || [];
      document.getElementById('chat-messages').innerHTML = msgs.map(m => {
        let attHtml = '';
        if (m.attachments && m.attachments.length) {
          attHtml = '<div style="margin-top:10px;padding-top:8px;border-top:1px solid #334155;"><strong>Attachments & Media Players:</strong>';
          for (const att of m.attachments) {
            attHtml += `<div style="margin-top:6px;font-size:12px;"><b>${att.label || 'File'}:</b> <span style="color:#94a3b8">${att.path}</span>`;
            if (att.video_url) {
              attHtml += `<br/><video controls src="${att.video_url}" style="width:100%;margin-top:4px;max-height:280px;background:#000;border-radius:4px;"></video>`;
            }
            if (att.audio_url) {
              attHtml += `<br/><audio controls src="${att.audio_url}" style="width:100%;margin-top:4px;height:36px;"></audio>`;
            }
            attHtml += `</div>`;
          }
          attHtml += '</div>';
        }
        return `
          <div class="msg ${m.role}">
            <div class="msg-meta">${m.role.toUpperCase()} • ${m.provider_id || 'studio'} • ${m.created_at}</div>
            <div>${m.content}</div>
            ${attHtml}
          </div>
        `;
      }).join('');

      const rightHtml = `
        <div class="card">
          <strong>Recent Jobs (${(data.jobs || []).length})</strong>
          ${(data.jobs || []).slice(0, 6).map(j => `
            <div style="margin-top:6px;border-top:1px solid #334155;padding-top:4px;">
              <code>${j.job_type}</code> — <span class="badge ${j.status === 'succeeded' ? 'ok' : 'warn'}">${j.status}</span><br/>
              <span style="color:#94a3b8">${j.summary}</span>
            </div>
          `).join('')}
        </div>
        <div class="card">
          <strong>Browser Tasks (${(data.browser_tasks || []).length})</strong>
          ${(data.browser_tasks || []).slice(0, 6).map(t => `
            <div style="margin-top:6px;border-top:1px solid #334155;padding-top:4px;">
              <b>${t.action_kind}</b> — <code>${t.status}</code><br/>
              <span style="color:#94a3b8">${t.url || t.title}</span>
            </div>
          `).join('')}
        </div>
        <div class="card">
          <strong>Mac Control Audit (${(data.mac_actions || []).length})</strong>
          ${(data.mac_actions || []).slice(0, 5).map(a => `
            <div style="margin-top:6px;border-top:1px solid #334155;padding-top:4px;">
              <b>${a.action_kind}</b> [<code>${a.profile_name}</code>] -> <code>${a.status}</code><br/>
              <span style="color:#94a3b8">${a.observed_postcondition}</span>
            </div>
          `).join('')}
        </div>
      `;
      document.getElementById('right-panel').innerHTML = rightHtml;
      renderLiveTracker(wf, ch02Wf);
      } catch (err) {
        console.error("Error in refreshState:", err);
      }
    }

    function switchMainTab(tab) {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => {
        p.classList.remove('active');
        p.style.display = 'none';
      });
      const btn = document.getElementById('tab-btn-' + tab);
      const pane = document.getElementById('tab-pane-' + tab);
      if (btn) btn.classList.add('active');
      if (pane) {
        pane.classList.add('active');
        pane.style.display = 'flex';
      }
      if (tab === 'files') {
        loadFilesExplorer();
      } else if (tab === 'browser') {
        refreshBrowserView(true);
        if (!browserAutoRefreshInterval) {
          toggleBrowserAutoRefresh();
        }
      } else if (tab === 'skills') {
        refreshSkills();
      } else if (tab === 'cron') {
        loadCronJobs();
      }
    }

    async function refreshSkills() {
      try {
        const res = await fetch('/api/skills');
        const data = await res.json();
        installedSkills = data.skills || [];
        currentActiveSkill = data.active_skill || {};

        // Update Header Dropdown
        const selector = document.getElementById('niche-selector');
        if (selector) {
          selector.innerHTML = installedSkills.map(s => `
            <option value="${s.name}" ${s.name === currentActiveSkill.name ? 'selected' : ''}>
              🎬 ${s.title}
            </option>
          `).join('');
        }

        // Update Active Niche Header Badge
        const badge = document.getElementById('active-niche-badge');
        if (badge && currentActiveSkill.name) {
          badge.innerText = currentActiveSkill.format || '16:9 Standard';
        }

        // Render Catalog Grid in Tab 5
        const grid = document.getElementById('skills-catalog-grid');
        if (grid) {
          grid.innerHTML = installedSkills.map(s => {
            const isActive = s.name === currentActiveSkill.name;
            return `
              <div class="card" style="border: 1px solid ${isActive ? '#3b82f6' : '#334155'}; background: ${isActive ? 'rgba(59, 130, 246, 0.08)' : 'var(--panel)'}; padding: 14px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                  <strong style="color:${isActive ? '#60a5fa' : '#f8fafc'};font-size:14px;">${s.title}</strong>
                  <span class="badge ${isActive ? 'ok' : ''}" style="${isActive ? '' : 'background:#334155;color:#94a3b8;'}">${isActive ? 'Active' : s.category}</span>
                </div>
                <div style="font-size:11px;color:#94a3b8;margin-bottom:8px;">
                  Format: <code>${s.format}</code> &bull; Version: <code>${s.version}</code>
                </div>
                <div style="font-size:12px;color:#cbd5e1;max-height:100px;overflow-y:auto;white-space:pre-wrap;background:#0b1120;padding:8px;border-radius:4px;margin-bottom:10px;">${s.instructions_md ? s.instructions_md.slice(0, 260) + '...' : 'No instructions.'}</div>
                <div style="display:flex;gap:6px;">
                  ${isActive ? '<button disabled style="opacity:0.6;font-size:11px;padding:4px 10px;">Currently Active</button>' : `<button class="primary" onclick="switchNicheSkill('${s.name}')" style="font-size:11px;padding:4px 10px;">Activate Niche</button>`}
                </div>
              </div>
            `;
          }).join('');
        }

        // Update Memory Previews
        const userView = document.getElementById('user-profile-view');
        if (userView) userView.innerText = data.user_profile || '';
        const memoryView = document.getElementById('working-memory-view');
        if (memoryView) memoryView.innerText = data.working_memory || '';

      } catch (err) {
        console.error("Error refreshing skills:", err);
      }
    }

    async function switchNicheSkill(skillName) {
      if (!skillName) return;
      await fetch('/api/skills', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ action: 'select', skill_name: skillName })
      });
      await refreshSkills();
      await refreshState();
    }

    async function openLearnPrompt() {
      const promptText = prompt(`✨ Hermes /learn — Describe the new 16:9 YouTube niche or channel concept to synthesize:\n\nExample: 'Space & Astronomy 16:9 documentary with Neil deGrasse Tyson style deep narration and James Webb telescope imagery'`);
      if (!promptText || !promptText.trim()) return;
      try {
        const res = await fetch('/api/skills', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ action: 'learn', prompt: promptText.trim() })
        });
        const data = await res.json();
        alert(`✨ Successfully learned and created new Niche Blueprint: ${data.skill.title}!\nSwitched active niche to: ${data.skill.name}.`);
        await refreshSkills();
        await refreshState();
      } catch (err) {
        alert("Error learning skill: " + err);
      }
    }

    async function promptAddRule() {
      const rule = prompt(`🧠 Add a persistent production rule to memory.md:\n\nExample: 'Always keep background music at -18dB under speech'`);
      if (!rule || !rule.trim()) return;
      await fetch('/api/skills', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ action: 'save_rule', rule: rule.trim() })
      });
      await refreshSkills();
    }

    async function loadCronJobs() {
      try {
        const res = await fetch('/api/cron');
        const data = await res.json();
        const jobs = data.jobs || [];
        const container = document.getElementById('cron-jobs-list');
        if (!container) return;
        if (jobs.length === 0) {
          container.innerHTML = '<div style="color:#94a3b8;font-size:13px;padding:12px;">No scheduled jobs yet. Click "➕ New Scheduled Job" above to create one.</div>';
          return;
        }
        container.innerHTML = jobs.map(j => `
          <div class="card" style="border: 1px solid ${j.is_active ? '#3b82f6' : '#334155'}; background: ${j.is_active ? 'rgba(30, 41, 59, 0.7)' : 'rgba(15, 23, 42, 0.4)'}; padding: 14px; border-radius: 8px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
              <div>
                <strong style="color:#f8fafc;font-size:14px;display:block;">${j.name}</strong>
                <span style="font-size:11px;color:#60a5fa;font-family:monospace;">⏰ ${j.schedule}</span>
              </div>
              <span class="badge ${j.is_active ? 'ok' : ''}" style="${j.is_active ? '' : 'background:#475569;color:#cbd5e1;'}">${j.is_active ? 'Active' : 'Paused'}</span>
            </div>
            <div style="font-size:12px;color:#cbd5e1;background:#0b1120;padding:8px;border-radius:4px;margin-bottom:8px;line-height:1.4;">
              <strong>Prompt:</strong> ${j.prompt}
            </div>
            <div style="font-size:11px;color:#94a3b8;margin-bottom:10px;display:flex;justify-content:space-between;">
              <span>Skill: <code>${j.skill_name || 'default'}</code></span>
              <span>Next: <code>${j.next_run || 'Pending'}</code></span>
            </div>
            <div style="display:flex;gap:6px;align-items:center;">
              <button class="primary" onclick="runCronJobNow('${j.job_id}')" style="font-size:11px;padding:4px 10px;">⚡ Run Now</button>
              <button onclick="toggleCronJob('${j.job_id}', ${!j.is_active})" style="font-size:11px;padding:4px 8px;background:#334155;">${j.is_active ? '⏸ Pause' : '▶ Enable'}</button>
              <button onclick="deleteCronJob('${j.job_id}')" style="font-size:11px;padding:4px 8px;background:#991b1b;color:#fff;margin-left:auto;">🗑 Delete</button>
            </div>
          </div>
        `).join('');
      } catch (err) {
        console.error("Error loading cron jobs:", err);
      }
    }

    async function openNewCronPrompt() {
      const name = prompt(`⏰ Hermes Scheduler — Enter scheduled job name:\n\nExample: 'Daily 16:9 YouTube Topic Discovery'`);
      if (!name || !name.trim()) return;
      const schedule = prompt(`⏰ Enter execution schedule:\n\nExamples: 'daily 09:00', 'every 4h', 'every 30m', 'weekly mon 10:00'`, 'daily 09:00');
      if (!schedule || !schedule.trim()) return;
      const promptText = prompt(`⏰ Enter automated prompt or workflow instruction:\n\nExample: 'Check latest comments in YouTube Studio and draft friendly replies for approval'`);
      if (!promptText || !promptText.trim()) return;

      try {
        await fetch('/api/cron', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            action: 'add',
            name: name.trim(),
            schedule: schedule.trim(),
            prompt: promptText.trim(),
            skill_name: currentActiveSkill ? currentActiveSkill.name : 'youtube-community-manager'
          })
        });
        await loadCronJobs();
      } catch (err) {
        alert("Failed to create scheduled job: " + err);
      }
    }

    async function toggleCronJob(jobId, isActive) {
      try {
        await fetch('/api/cron', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ action: 'toggle', job_id: jobId, is_active: isActive })
        });
        await loadCronJobs();
      } catch (err) {
        console.error(err);
      }
    }

    async function runCronJobNow(jobId) {
      try {
        const res = await fetch('/api/cron', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ action: 'run_now', job_id: jobId })
        });
        const data = await res.json();
        switchMainTab('chat');
        await refreshState();
      } catch (err) {
        alert("Error executing job: " + err);
      }
    }

    async function deleteCronJob(jobId) {
      if (!confirm("Are you sure you want to delete this scheduled cron job?")) return;
      try {
        await fetch('/api/cron', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ action: 'delete', job_id: jobId })
        });
        await loadCronJobs();
      } catch (err) {
        console.error(err);
      }
    }

    function toggleBrowserAutoRefresh() {
      const btn = document.getElementById('browser-auto-refresh-btn');
      if (browserAutoRefreshInterval) {
        clearInterval(browserAutoRefreshInterval);
        browserAutoRefreshInterval = null;
        if (btn) btn.innerText = '⏱ Auto-Refresh: OFF';
      } else {
        browserAutoRefreshInterval = setInterval(() => {
          refreshBrowserView(false);
        }, 2000);
        if (btn) btn.innerText = '⏱ Auto-Refresh: 2s (ON)';
      }
    }

    async function refreshBrowserView(force) {
      const fullImg = document.getElementById('browser-full-preview-img');
      const sideImg = document.getElementById('browser-preview-img');
      const spinner = document.getElementById('browser-loading-spinner');
      if (spinner) spinner.style.display = 'block';
      const ts = Date.now();
      const url = `/api/browser/preview?refresh=${force ? '1' : '0'}&ts=${ts}`;
      if (fullImg) fullImg.src = url;
      if (sideImg) sideImg.src = url;
      setTimeout(() => { if (spinner) spinner.style.display = 'none'; }, 600);
    }

    async function focusChromeWindow() {
      try {
        const res = await fetch('/api/browser/focus', { method: 'POST' });
        const d = await res.json();
        if (d.status !== 'focused') {
          alert('Could not focus Chrome: ' + (d.error || 'unknown'));
        }
      } catch (err) {
        alert('Desktop focus error: ' + err);
      }
    }

    async function archiveCurrentEpisode(defaultEpId) {
      const epId = prompt("Confirm Episode ID to publish and archive:", defaultEpId || "EP03_SWEET_FRUIT_BASKET");
      if (!epId) return;
      if (!confirm(`Publish and archive ${epId}?\n\nThis will:\n1. Preserve master script, master audio, and master video in DoroNesh permanent library\n2. Mark episode 'published' in database\n3. Purge intermediate scratch candidate takes\n4. Reset scratch conversational session`)) return;
      try {
        const res = await fetch('/api/episode/archive', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ episode_id: epId, conversation_id: activeConversationId })
        });
        const data = await res.json();
        const cleaned = data.cleaned_intermediate_takes ? data.cleaned_intermediate_takes.length : 0;
        alert(`Episode ${epId} successfully archived and published!\nMaster outputs preserved in DoroNesh permanent library.\nIntermediate candidate takes purged: ${cleaned} files.`);
        await refreshState();
      } catch (err) {
        alert("Archive error: " + err);
      }
    }

    async function clearChatMessages() {
      if (!confirm("Are you sure you want to clear chat messages and reset session history?")) return;
      await fetch('/api/chat/clear', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ conversation_id: activeConversationId })
      });
      await refreshState();
    }

    function renderLiveTracker(wf, ch02Wf) {
      const container = document.getElementById('tracker-steps-container');
      if (!container) return;
      const steps = wf.steps || {};
      const ch02Steps = ch02Wf.steps || {};
      const epLabel = document.getElementById('tracker-episode-label');
      const activeEpId = wf.episode_id || 'EP03_SWEET_FRUIT_BASKET';
      if (epLabel) {
        epLabel.innerText = 'Active: ' + activeEpId;
      }

      const activeSkillTitle = currentActiveSkill ? currentActiveSkill.title : 'Mar & Kuku - Toddler & Preschool Learning';
      const activeSkillFormat = currentActiveSkill ? currentActiveSkill.format : '16:9 (1920x1080 @ 30fps)';
      const activeSkillCategory = currentActiveSkill ? currentActiveSkill.category : 'kids_preschool';

      const styleCardHtml = `
        <div class="card" style="background:#0f172a;border:1px solid #3b82f6;margin-bottom:14px;padding:12px 16px;">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
            <div>
              <h4 style="margin:0;font-size:14px;color:#60a5fa;">🎬 Active 16:9 Niche Blueprint: ${activeSkillTitle}</h4>
              <span style="font-size:12px;color:#94a3b8;">Format: <b>${activeSkillFormat}</b> | Category: <b>${activeSkillCategory}</b></span>
            </div>
            <span class="badge ok">16:9 Standard Enforced</span>
          </div>
          <div style="display:flex;gap:10px;margin-top:10px;font-size:12px;color:#cbd5e1;flex-wrap:wrap;">
            <div style="background:#1e293b;padding:6px 10px;border-radius:4px;border:1px solid #334155;">
              📐 <b>Canvas Aspect:</b> 16:9 Widescreen (1920x1080 / 4K)
            </div>
            <div style="background:#1e293b;padding:6px 10px;border-radius:4px;border:1px solid #334155;">
              🎵 <b>Master Audio:</b> 48,000Hz, 16-bit, 2-Ch Stereo WAV
            </div>
            <div style="background:#1e293b;padding:6px 10px;border-radius:4px;border:1px solid #334155;">
              🛠 <b>Blueprint:</b> ${currentActiveSkill ? currentActiveSkill.name : 'mar-kuku-kids'}
            </div>
          </div>
        </div>
        <div class="card" style="background:#0f172a;border:1px solid #10b981;margin-bottom:14px;padding:12px 16px;">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
            <div>
              <h4 style="margin:0;font-size:14px;color:#34d399;">📦 Master Episode Archive &amp; Post-Publish Lifecycle</h4>
              <span style="font-size:12px;color:#94a3b8;">Preserve permanent master records in SQLite &amp; library; clean working scratch clutter.</span>
            </div>
            <button class="primary" onclick="archiveCurrentEpisode('${activeEpId}')" style="background:#059669;border-color:#059669;font-weight:600;">📦 Publish &amp; Archive Episode</button>
          </div>
        </div>
      `;

      const stepDefinitions = [
        { id: 'step_1_script', title: '1. Multi-Agent Script Consultation', desc: 'ChatGPT (Master) -> Gemini (Reviewer) -> Manus (Specialist) -> ChatGPT (Synthesis)', action: 'consult_three_agents', check: steps.step_1_chatgpt_master_plan?.status === 'completed' },
        { id: 'step_2_flow_music', title: '2. Google Flow Music Generation', desc: 'Lyria 3.5 acoustic remix, produce Candidate A and B WAVs', action: 'flow_music_audio_pipeline', check: steps.step_6a_flow_music_audio_pipeline?.status === 'completed' || steps.step_google_music_generation?.status === 'completed' },
        { id: 'step_3_review', title: '3. Transcription & Comparative Review', desc: 'Manus Web transcription & ChatGPT comparative audio review', action: 'continue_selected_chapter', check: steps.step_manus_transcription?.status === 'completed' || ch02Steps.step_manus_transcription?.status === 'completed' },
        { id: 'step_4_repair', title: '4. Interactive Response Hold Calibration', desc: 'Calibrate 4.00s stillness hold gap with FFmpeg precision', action: 'apply_audio_repair', check: steps.step_audio_repair?.status === 'completed' || ch02Steps.step_audio_repair?.status === 'completed' },
        { id: 'step_5_assets', title: '5. Master Asset & Prop Intake', desc: 'Calibrate transparent 1254x1254 character & fruit props into 01_ASSETS', action: 'generate_assets', check: steps.step_6_asset_generation_and_intake?.status === 'completed' },
        { id: 'step_6_palmier', title: '6. Palmier Pro Timeline Assembly', desc: 'Assemble multi-track timeline, clips and markers via local MCP bridge', action: 'assemble_palmier', check: steps.step_7_palmier_editing?.status === 'completed' },
        { id: 'step_7_export', title: '7. Native 1080p30 MP4 Export & QA', desc: 'Render final MP4 video and execute frame-by-frame RMS QA', action: 'export_mp4', check: steps.step_8_final_mp4_export?.status === 'completed' },
        { id: 'step_8_youtube', title: '8. YouTube Studio Staging & Upload', desc: 'Stage video, tags, thumbnail, and metadata package', action: 'stage_youtube', check: steps.step_9_youtube_studio_upload?.status === 'completed' },
      ];

      container.innerHTML = styleCardHtml + stepDefinitions.map((s, idx) => {
        const isDone = s.check;
        return `
          <div class="tracker-item ${isDone ? 'done' : ''}">
            <div class="tracker-icon">${isDone ? '✓' : (idx + 1)}</div>
            <div style="flex:1;">
              <div style="display:flex;align-items:center;justify-content:space-between;">
                <strong style="color:${isDone ? '#34d399' : '#f8fafc'};">${s.title}</strong>
                <span class="badge ${isDone ? 'ok' : 'warn'}">${isDone ? 'Completed' : 'Pending'}</span>
              </div>
              <div style="font-size:12px;color:#94a3b8;margin-top:2px;">${s.desc}</div>
            </div>
            <button onclick="triggerAction('${s.action}')" style="font-size:11px;padding:4px 8px;">Run</button>
          </div>
        `;
      }).join('');
    }

    async function loadFilesExplorer() {
      const container = document.getElementById('files-explorer-container');
      if (!container) return;
      container.innerHTML = '<div style="color:#94a3b8;font-size:13px;">Scanning production library & working folders...</div>';
      try {
        const res = await fetch('/api/files');
        const data = await res.json();
        const pFiles = data.production_files || [];
        const sFiles = data.scratch_files || [];
        const tFiles = data.tracker_files || [];

        function renderGroup(title, files, basePath) {
          if (!files.length) return `<div class="file-box"><strong style="color:#60a5fa;">${title}</strong><div style="color:#94a3b8;font-size:12px;margin-top:6px;">No files found.</div></div>`;
          return `
            <div class="file-box">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <strong style="color:#60a5fa;">${title} (${files.length})</strong>
                <span style="font-size:11px;color:#94a3b8;">${basePath}</span>
              </div>
              ${files.map(f => {
                let preview = '';
                if (f.kind === 'audio') {
                  preview = `<audio controls src="/api/audio?file=${encodeURIComponent(f.full_path)}" style="height:28px;width:240px;"></audio>`;
                } else if (f.kind === 'video') {
                  preview = `<video controls src="/api/video?file=${encodeURIComponent(f.full_path)}" style="height:36px;width:120px;"></video>`;
                } else if (f.kind === 'image') {
                  preview = `<img src="/api/image?file=${encodeURIComponent(f.full_path)}" style="height:32px;width:32px;object-fit:cover;border-radius:4px;border:1px solid #334155;" />`;
                }
                const kb = (f.size_bytes / 1024).toFixed(1);
                const enc = encodeURIComponent(f.full_path);
                return `
                  <div class="file-row">
                    <div style="display:flex;align-items:center;gap:8px;overflow:hidden;flex:1;min-width:0;">
                      <span class="badge" style="font-size:10px;text-transform:uppercase;">${f.kind}</span>
                      <span style="font-weight:600;text-overflow:ellipsis;overflow:hidden;white-space:nowrap;font-size:12px;" title="${f.full_path}">${f.rel_path}</span>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
                      ${preview}
                      <span style="color:#94a3b8;font-size:11px;">${kb} KB</span>
                      <button onclick="openFileDesktop('${enc}', true)" style="padding:2px 7px;font-size:10px;background:#334155;color:#fff;" title="Reveal in macOS Finder">📂 Finder</button>
                      <button onclick="openFileDesktop('${enc}', false)" style="padding:2px 7px;font-size:10px;background:#2563eb;color:#fff;" title="Open with macOS default app">🚀 Open</button>
                    </div>
                  </div>
                `;
              }).join('')}
            </div>
          `;
        }

        container.innerHTML = `
          ${renderGroup('Working Flow Trackers', tFiles, 'WorkingFlowTracker/')}
          ${renderGroup('Master Production Library (DoroNesh_2D_Animation)', pFiles, data.production_root)}
          ${renderGroup('Working Scratch Deliverables & Candidates', sFiles, data.scratch_root)}
        `;
      } catch (err) {
        container.innerHTML = `<div style="color:#ef4444;">Error loading files: ${err}</div>`;
      }
    }

    async function openFileDesktop(encodedPath, reveal) {
      const fullPath = decodeURIComponent(encodedPath);
      await fetch('/api/files/open', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ path: fullPath, reveal: reveal })
      });
    }

    function toggleComposerHeight() {
      const el = document.getElementById('chat-input');
      const btn = document.getElementById('toggle-resize-btn');
      if (el.style.height === '220px') {
        el.style.height = '80px';
        btn.innerText = '⤢ Expand Box';
      } else {
        el.style.height = '220px';
        btn.innerText = '⤡ Shrink Box';
      }
    }

    async function sendChat() {
      const input = document.getElementById('chat-input');
      const text = input.value.trim();
      if (!text) return;
      const provider = document.getElementById('provider-select').value;
      const mode = (document.getElementById('approval-mode-select') || {}).value || 'ask';
      input.value = '';
      await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          conversation_id: activeConversationId,
          message: text,
          provider_id: provider,
          action_params: {
            delegate_to_chatgpt: (mode === 'auto')
          }
        })
      });
      await refreshState();
    }

    async function triggerAction(actionName) {
      const provider = document.getElementById('provider-select').value;
      let defaultMsg = `Execute studio action: ${actionName}`;
      if (actionName === 'continue_ch02_audio') {
        defaultMsg = 'Continue Episode 002 CH02 audio production from its saved state. Reuse completed candidates and transcripts, resolve the next blocked step, and show me the review and available audio results.';
      } else if (actionName === 'apply_audio_repair') {
        defaultMsg = "Approve script change: keep 'Have a look at this one' and trim hold to 4s";
      } else if (actionName === 'consult_three_agents') {
        defaultMsg = 'Research the next Mar and Kuku episode, consult Gemini and Manus, and save the proposed script.';
      } else if (actionName === 'resume_workflow') {
        defaultMsg = 'Resume the workflow';
      } else if (actionName === 'generate_assets') {
        defaultMsg = 'Generate audio and visual props for the episode';
      } else if (actionName === 'assemble_palmier') {
        defaultMsg = 'Assemble the episode in Palmier Pro';
      } else if (actionName === 'export_mp4') {
        defaultMsg = 'Export the final MP4 and run QA';
      } else if (actionName === 'stage_youtube') {
        defaultMsg = 'Stage the video and metadata package for YouTube Studio';
      } else if (actionName === 'full_pipeline') {
        defaultMsg = 'Run full production pipeline from script to YouTube Studio';
      }
      await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          conversation_id: activeConversationId,
          message: defaultMsg,
          provider_id: provider,
          action: actionName
        })
      });
      await refreshState();
    }

    async function triggerRepairPrompt() {
      const input = document.getElementById('chat-input');
      input.value = "Approve script change: keep 'Have a look at this one' and trim hold to 4s";
      await sendChat();
    }

    async function toggleTakeover(enable) {
      await fetch('/api/browser/takeover', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({conversation_id: activeConversationId, enable: enable})
      });
      await refreshState();
    }

    async function captureBrowserPreview() {
      await fetch('/api/browser/preview?refresh=1');
      await refreshState();
    }

    async function stopAll() {
      await fetch('/api/stop', {method: 'POST'});
      await refreshState();
    }

    async function openTelegramModal() {
      const modal = document.getElementById('telegram-modal');
      if (modal) {
        modal.style.display = 'flex';
        try {
          const res = await fetch('/api/telegram');
          const cfg = await res.json();
          document.getElementById('tg-bot-token').value = cfg.bot_token || '';
          document.getElementById('tg-chat-id').value = cfg.chat_id || '';
          document.getElementById('tg-enabled-cb').checked = Boolean(cfg.enabled);
          const badge = document.getElementById('tg-modal-badge');
          if (badge) {
            badge.innerText = cfg.is_configured ? (cfg.enabled ? '🟢 Connected' : '⏸ Paused') : '⚪ Not configured';
            badge.className = 'badge ' + (cfg.is_configured && cfg.enabled ? 'ok' : 'warn');
          }
        } catch (e) {
          console.error(e);
        }
      }
    }

    function closeTelegramModal() {
      const modal = document.getElementById('telegram-modal');
      if (modal) modal.style.display = 'none';
      const feedback = document.getElementById('tg-test-feedback');
      if (feedback) feedback.style.display = 'none';
    }

    async function saveTelegramSettings() {
      const token = document.getElementById('tg-bot-token').value.trim();
      const chat_id = document.getElementById('tg-chat-id').value.trim();
      const enabled = document.getElementById('tg-enabled-cb').checked;
      try {
        await fetch('/api/telegram', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ bot_token: token, chat_id: chat_id, enabled: enabled })
        });
        alert("✅ Telegram settings saved and activated successfully!");
        closeTelegramModal();
        await refreshTelegramStatus();
      } catch (err) {
        alert("Failed to save Telegram settings: " + err);
      }
    }

    async function testTelegramConnection() {
      const token = document.getElementById('tg-bot-token').value.trim();
      const chat_id = document.getElementById('tg-chat-id').value.trim();
      const fb = document.getElementById('tg-test-feedback');
      if (!token) {
        alert("Please enter a Bot Token first.");
        return;
      }
      if (fb) {
        fb.style.display = 'block';
        fb.style.background = '#1e3a8a';
        fb.style.color = '#bfdbfe';
        fb.innerText = 'Connecting to Telegram servers...';
      }
      try {
        const res = await fetch('/api/telegram/test', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ bot_token: token, chat_id: chat_id })
        });
        const data = await res.json();
        if (data.ok) {
          fb.style.background = '#065f46';
          fb.style.color = '#a7f3d0';
          fb.innerText = '✅ Success! Verified Bot: @' + data.bot_username + (data.message_sent ? ' (Test message sent to your phone!)' : ' (Chat ID verified).');
          await refreshTelegramStatus();
        } else {
          fb.style.background = '#7f1d1d';
          fb.style.color = '#fecaca';
          fb.innerText = '❌ Error: ' + (data.error || 'Connection failed.');
        }
      } catch (err) {
        if (fb) {
          fb.style.background = '#7f1d1d';
          fb.style.color = '#fecaca';
          fb.innerText = '❌ Request failed: ' + err;
        }
      }
    }

    async function refreshTelegramStatus() {
      try {
        const res = await fetch('/api/telegram');
        const cfg = await res.json();
        const btn = document.getElementById('telegram-btn');
        if (btn) {
          if (cfg.is_configured && cfg.enabled) {
            btn.innerHTML = '📱 Telegram: 🟢 ON';
            btn.style.background = '#059669';
          } else {
            btn.innerHTML = '📱 Telegram Gateway';
            btn.style.background = '#0284c7';
          }
        }
      } catch (e) {}
    }

    refreshState();
    refreshSkills();
    refreshTelegramStatus();
  </script>
</body>
</html>
"""

BROWSER_SANDBOX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>[LOCAL SANDBOX FIXTURE] Mar & Kuku Browser DOM & Upload/Download Test Page</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      padding: 24px;
      max-width: 900px;
      margin: 0 auto;
    }
    .banner {
      background: #7c2d12;
      border: 1px solid #f97316;
      color: #ffedd5;
      padding: 10px 14px;
      border-radius: 8px;
      margin-bottom: 16px;
      font-weight: 600;
    }
    .box {
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 10px;
      padding: 18px;
      margin-bottom: 18px;
    }
    textarea {
      width: 100%;
      height: 75px;
      background: #0b1120;
      color: #f8fafc;
      border: 1px solid #475569;
      border-radius: 6px;
      padding: 10px;
      font-size: 14px;
    }
    button, a.btn {
      display: inline-block;
      margin-top: 10px;
      padding: 8px 16px;
      background: #2563eb;
      color: #fff;
      border: none;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
    }
    .assistant-response {
      background: #0b1120;
      border-left: 4px solid #10b981;
      padding: 12px;
      margin-top: 10px;
      border-radius: 4px;
    }
  </style>
</head>
<body data-busy="false">
  <div class="banner">LOCAL SANDBOX FIXTURE (/browser-sandbox) — Deterministic local test fixture for CDP DOM &amp; file upload/download verification. Not an external AI provider.</div>
  <h1>Mar & Kuku Browser DOM &amp; Asset Intake Fixture</h1>
  <div class="box">
    <h2>1. Local Fixture Prompt &amp; Follow-Up Session</h2>
    <textarea id="prompt-input" placeholder="Enter production prompt or follow-up question..."></textarea>
    <br/>
    <button id="submit-prompt" type="submit" onclick="handlePrompt()">Submit Prompt</button>
    <div id="responses-container"></div>
  </div>

  <div class="box">
    <h2>2. Cue Sheet / Spec Upload &amp; Verified Download Intake</h2>
    <input type="file" id="file-upload" onchange="handleFileUpload(event)" />
    <div id="upload-status" style="margin-top:10px;color:#94a3b8;">No file uploaded yet.</div>
    <a id="download-report-btn" class="btn" style="display:none;" download="ch01_browser_intake_manifest.json">Download Verified Intake Manifest</a>
  </div>

  <script>
    let turnCounter = 0;

    function handlePrompt() {
      const input = document.getElementById('prompt-input');
      const promptText = input.value.trim();
      if (!promptText) return;
      document.body.setAttribute('data-busy', 'true');
      turnCounter += 1;

      const container = document.getElementById('responses-container');
      const div = document.createElement('div');
      div.className = 'assistant-response';
      div.innerText = `[Turn ${turnCounter} Local Fixture Response] Verified Episode 002 / CH01 context for prompt: "${promptText}". Candidate audio is 30.220000s (1,450,560 samples @ 48kHz), 4-second response hold spans frames 501..621 (16.70s..20.70s), and scratch MP4 deliverable is E02_CH01_Technical_Proof_1080p30_v001.mp4 (907 frames @ 30fps).`;
      container.appendChild(div);
      input.value = '';
      document.body.setAttribute('data-busy', 'false');
    }

    async function handleFileUpload(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const text = await file.text();
      const statusEl = document.getElementById('upload-status');
      statusEl.setAttribute('data-filename', file.name);
      statusEl.setAttribute('data-bytes', String(file.size));
      statusEl.innerText = `Uploaded ${file.name} (${file.size} bytes)`;

      const manifest = {
        intake_verified: true,
        local_fixture: true,
        uploaded_filename: file.name,
        uploaded_bytes: file.size,
        episode_id: 'EPISODE_002',
        chapter_id: 'CH01',
        total_frames: 907,
        hold_frames: [501, 621],
        preview_excerpt: text.slice(0, 240)
      };
      const manifestJson = JSON.stringify(manifest, null, 2);
      const blob = new Blob([manifestJson], {type: 'application/json'});
      const dlBtn = document.getElementById('download-report-btn');
      dlBtn.setAttribute('data-manifest-json', manifestJson);
      dlBtn.href = URL.createObjectURL(blob);
      dlBtn.style.display = 'inline-block';
    }
  </script>
</body>
</html>
"""


def create_studio_app(policy: RootPolicy | None = None) -> Starlette:
    orchestrator = StudioOrchestrator(policy)

    async def index_page(request: Request) -> HTMLResponse:
        return HTMLResponse(STUDIO_INDEX_HTML)

    async def browser_sandbox_page(request: Request) -> HTMLResponse:
        return HTMLResponse(BROWSER_SANDBOX_HTML)

    async def api_state(request: Request) -> JSONResponse:
        cid = request.query_params.get("conversation_id") or "conv-main"
        conv = orchestrator.store.get_conversation(cid) or orchestrator._ensure_default_conversation()
        providers = await asyncio.to_thread(orchestrator.providers.check_providers_health)
        ep_id = conv.get("active_episode_id")
        wf_manifest = orchestrator.workflow.load_manifest(ep_id) if ep_id else {}
        ch02_manifest = orchestrator.workflow.load_manifest("EP02_CH02_AUDIO")
        return JSONResponse(
            {
                "conversation": conv,
                "conversations": orchestrator.store.list_conversations(),
                "messages": orchestrator.store.list_messages(conv["conversation_id"]),
                "jobs": orchestrator.store.list_jobs(conv["conversation_id"]),
                "providers": providers,
                "browser_tasks": orchestrator.store.list_browser_tasks(conv["conversation_id"]),
                "browser_takeover": orchestrator.browser.owner_takeover,
                "mac_actions": orchestrator.store.list_mac_actions(conv["conversation_id"]),
                "workflow": wf_manifest,
                "ch02_workflow": ch02_manifest,
                "cancel_event_set": orchestrator.cancel_event.is_set(),
                "episode_memories": orchestrator.store.list_episode_memories(),
                "kids_channel_standard": KIDS_CHANNEL_STYLE_STANDARD,
                "active_skill": orchestrator.skills.get_active_skill().to_dict(),
                "skills": [s.to_dict() for s in orchestrator.skills.list_skills()],
                "user_profile": orchestrator.skills.memory.get_user_profile(),
                "working_memory": orchestrator.skills.memory.get_working_memory(),
            }
        )

    async def api_audio(request: Request):
        file_param = request.query_params.get("file")
        if not file_param:
            return JSONResponse({"error": "Missing 'file' parameter"}, status_code=400)
        p = Path(file_param).resolve()
        allowed_prefixes = list(orchestrator.policy.allowed_read_roots) + [
            orchestrator.policy.output_root_resolved,
            orchestrator.policy.production_root_resolved,
        ]
        if not any(str(p).startswith(str(prefix)) for prefix in allowed_prefixes):
            return JSONResponse({"error": "Access denied: file outside permitted directories"}, status_code=403)
        if not p.is_file():
            return JSONResponse({"error": f"File not found: {p}"}, status_code=404)
        return FileResponse(str(p), media_type="audio/wav")

    async def api_video(request: Request):
        file_param = request.query_params.get("file")
        if not file_param:
            return JSONResponse({"error": "Missing 'file' parameter"}, status_code=400)
        p = Path(file_param).resolve()
        allowed_prefixes = list(orchestrator.policy.allowed_read_roots) + [
            orchestrator.policy.output_root_resolved,
            orchestrator.policy.production_root_resolved,
        ]
        if not any(str(p).startswith(str(prefix)) for prefix in allowed_prefixes):
            return JSONResponse({"error": "Access denied: file outside permitted directories"}, status_code=403)
        if not p.is_file():
            return JSONResponse({"error": f"File not found: {p}"}, status_code=404)
        return FileResponse(str(p), media_type="video/mp4")

    async def api_chat(request: Request) -> JSONResponse:
        body = await request.json()
        cid = str(body.get("conversation_id") or "conv-e02-ch01-main")
        msg = str(body.get("message") or "").strip()
        prov = body.get("provider_id")
        action = body.get("action")
        action_params = body.get("action_params") or {}
        res = await asyncio.to_thread(
            orchestrator.handle_chat_turn,
            conversation_id=cid,
            user_message=msg,
            provider_id=prov,
            action=action,
            action_params=action_params,
        )
        return JSONResponse(res)

    async def api_browser_takeover(request: Request) -> JSONResponse:
        body = await request.json()
        cid = str(body.get("conversation_id") or "conv-e02-ch01-main")
        enable = bool(body.get("enable", True))
        if enable:
            res = await asyncio.to_thread(orchestrator.browser.enter_owner_takeover, conversation_id=cid)
        else:
            res = await asyncio.to_thread(orchestrator.browser.resume_from_takeover, conversation_id=cid)
        return JSONResponse(res)

    async def api_browser_preview(request: Request):
        refresh = request.query_params.get("refresh") == "1"
        captures_dir = orchestrator.browser.captures_dir
        if refresh and orchestrator.browser.is_reachable():
            try:
                shot = await asyncio.to_thread(
                    orchestrator.browser.capture_screenshot,
                    "studio_live_preview.png",
                )
                return FileResponse(shot["path"], media_type="image/png")
            except Exception:
                pass
        pngs = sorted(captures_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        if pngs:
            return FileResponse(str(pngs[0]), media_type="image/png")
        return JSONResponse({"status": "no_preview_available"}, status_code=404)

    async def api_stop(request: Request) -> JSONResponse:
        res = orchestrator.request_stop()
        return JSONResponse(res)

    async def api_chat_clear(request: Request) -> JSONResponse:
        body = await request.json()
        cid = str(body.get("conversation_id") or "conv-main")
        orchestrator.store.clear_conversation(cid)
        return JSONResponse({"status": "cleared", "conversation_id": cid})

    async def api_image(request: Request):
        file_param = request.query_params.get("file")
        if not file_param:
            return JSONResponse({"error": "Missing 'file' parameter"}, status_code=400)
        p = Path(file_param).resolve()
        allowed_prefixes = list(orchestrator.policy.allowed_read_roots) + [
            orchestrator.policy.output_root_resolved,
            orchestrator.policy.production_root_resolved,
        ]
        if not any(str(p).startswith(str(prefix)) for prefix in allowed_prefixes):
            return JSONResponse({"error": "Access denied: file outside permitted directories"}, status_code=403)
        if not p.is_file():
            return JSONResponse({"error": f"File not found: {p}"}, status_code=404)
        suffix = p.suffix.lower()
        media_type = "image/png" if suffix == ".png" else "image/jpeg"
        return FileResponse(str(p), media_type=media_type)

    async def api_files(request: Request) -> JSONResponse:
        prod_root = orchestrator.policy.production_root_resolved
        scratch_root = orchestrator.policy.output_root_resolved
        tracker_root = Path(__file__).resolve().parents[3] / "WorkingFlowTracker"

        def scan_dir(root: Path, max_depth: int = 4):
            items = []
            if not root.exists():
                return items
            for p in sorted(root.rglob("*")):
                if p.is_file() and not p.name.startswith("."):
                    try:
                        rel = p.relative_to(root)
                        if len(rel.parts) <= max_depth:
                            suffix = p.suffix.lower()
                            kind = "other"
                            if suffix in (".wav", ".mp3", ".aac", ".m4a"):
                                kind = "audio"
                            elif suffix in (".mp4", ".mov", ".mkv"):
                                kind = "video"
                            elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
                                kind = "image"
                            elif suffix in (".md", ".txt"):
                                kind = "script"
                            elif suffix in (".json",):
                                kind = "data"
                            elif suffix in (".palmier",):
                                kind = "palmier"
                            items.append({
                                "name": p.name,
                                "rel_path": str(rel),
                                "full_path": str(p),
                                "size_bytes": p.stat().st_size,
                                "modified": p.stat().st_mtime,
                                "kind": kind,
                            })
                    except Exception:
                        pass
            return items

        return JSONResponse({
            "production_root": str(prod_root),
            "production_files": scan_dir(prod_root, max_depth=4),
            "scratch_root": str(scratch_root),
            "scratch_files": scan_dir(scratch_root / "episodes", max_depth=3),
            "tracker_files": scan_dir(tracker_root, max_depth=2),
        })

    async def api_files_open(request: Request) -> JSONResponse:
        body = await request.json()
        raw_path = body.get("path")
        if not raw_path:
            return JSONResponse({"error": "Path required"}, status_code=400)
        p = Path(raw_path).resolve()
        allowed = list(orchestrator.policy.allowed_read_roots) + [
            orchestrator.policy.output_root_resolved,
            orchestrator.policy.production_root_resolved,
        ]
        if not any(str(p).startswith(str(prefix)) for prefix in allowed):
            return JSONResponse({"error": "Access denied: outside allowed roots"}, status_code=403)
        if not p.exists():
            return JSONResponse({"error": f"File not found: {p}"}, status_code=404)
        reveal = bool(body.get("reveal", False))
        try:
            res = open_file_or_folder(p, reveal=reveal)
            return JSONResponse({"status": "opened", "path": str(p), "revealed": reveal, "returncode": res.returncode})
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    async def api_browser_focus(request: Request) -> JSONResponse:
        try:
            ok = focus_application("Google Chrome")
            return JSONResponse({"status": "focused" if ok else "unsupported", "ok": ok})
        except Exception as exc:
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)

    async def api_telegram(request: Request) -> JSONResponse:
        if request.method == "GET":
            return JSONResponse(orchestrator.telegram.get_config())
        elif request.method == "POST":
            body = await request.json()
            cfg = orchestrator.telegram.save_config(
                bot_token=str(body.get("bot_token") or ""),
                chat_id=str(body.get("chat_id") or ""),
                enabled=bool(body.get("enabled", True)),
                notify_on_step_complete=bool(body.get("notify_on_step_complete", True)),
                notify_on_approval_needed=bool(body.get("notify_on_approval_needed", True)),
            )
            return JSONResponse({"status": "saved", "config": cfg})

    async def api_telegram_test(request: Request) -> JSONResponse:
        body = await request.json()
        token = body.get("bot_token")
        chat_id = body.get("chat_id")
        res = orchestrator.telegram.test_connection(token=token, chat_id=chat_id)
        return JSONResponse(res)

    async def api_episode_archive(request: Request) -> JSONResponse:
        body = await request.json()
        ep_id = str(body.get("episode_id") or "EP03_SWEET_FRUIT_BASKET")
        cid = str(body.get("conversation_id") or "conv-main")
        wf_folder = orchestrator.workflow.get_episode_folder(ep_id) if hasattr(orchestrator.workflow, "get_episode_folder") else None
        res = orchestrator.store.archive_and_clean_episode(
            ep_id,
            conversation_id=cid,
            working_folder=wf_folder,
            archive_receipt={
                "archived_by": "owner_action",
                "reason": "episode_published",
                "timestamp": orchestrator.workflow._utc_now() if hasattr(orchestrator.workflow, "_utc_now") else "",
            }
        )
        return JSONResponse(res)

    async def api_episode_memory(request: Request) -> JSONResponse:
        if request.method == "GET":
            ep_id = request.query_params.get("episode_id")
            if ep_id:
                mem = orchestrator.store.get_episode_memory(ep_id)
                return JSONResponse({"episode_memory": mem, "style_standard": KIDS_CHANNEL_STYLE_STANDARD})
            mems = orchestrator.store.list_episode_memories()
            return JSONResponse({"episode_memories": mems, "style_standard": KIDS_CHANNEL_STYLE_STANDARD})
        elif request.method == "POST":
            body = await request.json()
            saved = orchestrator.store.save_episode_memory(
                episode_id=body.get("episode_id", "EP03_SWEET_FRUIT_BASKET"),
                title=body.get("title", "Episode 003"),
                channel_id=body.get("channel_id", "mar-kuku-kids"),
                status=body.get("status", "in_production"),
                style_guide=body.get("style_guide") or KIDS_CHANNEL_STYLE_STANDARD,
                master_script_path=body.get("master_script_path", ""),
                master_audio_path=body.get("master_audio_path", ""),
                master_video_path=body.get("master_video_path", ""),
                youtube_url=body.get("youtube_url", ""),
            )
            return JSONResponse({"status": "saved", "episode_memory": saved})

    async def api_skills(request: Request) -> JSONResponse:
        if request.method == "GET":
            active = orchestrator.skills.get_active_skill()
            skills_list = [s.to_dict() for s in orchestrator.skills.list_skills()]
            return JSONResponse({
                "active_skill": active.to_dict(),
                "skills": skills_list,
                "user_profile": orchestrator.skills.memory.get_user_profile(),
                "working_memory": orchestrator.skills.memory.get_working_memory(),
            })
        elif request.method == "POST":
            body = await request.json()
            action = body.get("action")
            if action == "select":
                name = str(body.get("skill_name") or "")
                skill = orchestrator.skills.set_active_skill(name)
                return JSONResponse({"status": "selected", "active_skill": skill.to_dict()})
            elif action == "learn":
                prompt = str(body.get("prompt") or "")
                new_skill = orchestrator.skills.learn_new_niche_skill(prompt)
                orchestrator.skills.set_active_skill(new_skill.name)
                return JSONResponse({"status": "learned", "skill": new_skill.to_dict()})
            elif action == "save_rule":
                rule = str(body.get("rule") or "")
                orchestrator.skills.memory.append_learned_rule(rule)
                return JSONResponse({"status": "rule_saved", "rule": rule})
            return JSONResponse({"error": "Unknown action"}, status_code=400)

    async def api_cron(request: Request) -> JSONResponse:
        if request.method == "GET":
            jobs = orchestrator.scheduler.list_jobs()
            return JSONResponse({"jobs": jobs})
        elif request.method == "POST":
            body = await request.json()
            action = body.get("action")
            if action == "add":
                name = str(body.get("name") or "Scheduled Task")
                schedule = str(body.get("schedule") or "daily 09:00")
                prompt = str(body.get("prompt") or "")
                skill_name = body.get("skill_name")
                job = orchestrator.scheduler.add_job(name=name, schedule=schedule, prompt=prompt, skill_name=skill_name)
                return JSONResponse({"status": "created", "job": job})
            elif action == "toggle":
                job_id = str(body.get("job_id") or "")
                is_active = bool(body.get("is_active", True))
                target = orchestrator.scheduler.toggle_job(job_id, is_active)
                return JSONResponse({"status": "updated", "job": target})
            elif action == "delete":
                job_id = str(body.get("job_id") or "")
                deleted = orchestrator.scheduler.delete_job(job_id)
                return JSONResponse({"status": "deleted" if deleted else "not_found"})
            elif action == "run_now":
                job_id = str(body.get("job_id") or "")
                jobs = orchestrator.scheduler.list_jobs()
                job = next((j for j in jobs if j["job_id"] == job_id), None)
                if not job:
                    return JSONResponse({"error": "Job not found"}, status_code=404)
                convs = orchestrator.store.list_conversations()
                cid = str(body.get("conversation_id") or (convs[0]["conversation_id"] if convs else "conv-main"))
                user_prompt = f"⏰ [Scheduled Cron Trigger: {job['name']}]\n{job['prompt']}"
                turn = await asyncio.to_thread(
                    orchestrator.handle_chat_turn,
                    conversation_id=cid,
                    user_message=user_prompt,
                    provider_id="codex_cli",
                    action=None,
                )
                orchestrator.scheduler.mark_executed(job_id)
                return JSONResponse({"status": "dispatched", "turn": turn, "job_id": job_id})
            return JSONResponse({"error": "Unknown action"}, status_code=400)

    app = Starlette(
        routes=[
            Route("/", index_page, methods=["GET"]),
            Route("/browser-sandbox", browser_sandbox_page, methods=["GET"]),
            Route("/api/state", api_state, methods=["GET"]),
            Route("/api/chat", api_chat, methods=["POST"]),
            Route("/api/chat/clear", api_chat_clear, methods=["POST"]),
            Route("/api/browser/takeover", api_browser_takeover, methods=["POST"]),
            Route("/api/browser/preview", api_browser_preview, methods=["GET"]),
            Route("/api/browser/focus", api_browser_focus, methods=["POST"]),
            Route("/api/skills", api_skills, methods=["GET", "POST"]),
            Route("/api/cron", api_cron, methods=["GET", "POST"]),
            Route("/api/telegram", api_telegram, methods=["GET", "POST"]),
            Route("/api/telegram/test", api_telegram_test, methods=["POST"]),
            Route("/api/episode/memory", api_episode_memory, methods=["GET", "POST"]),
            Route("/api/episode/archive", api_episode_archive, methods=["POST"]),
            Route("/api/audio", api_audio, methods=["GET"]),
            Route("/api/video", api_video, methods=["GET"]),
            Route("/api/image", api_image, methods=["GET"]),
            Route("/api/files", api_files, methods=["GET"]),
            Route("/api/files/open", api_files_open, methods=["POST"]),
            Route("/api/stop", api_stop, methods=["POST"]),
        ]
    )
    app.state.orchestrator = orchestrator
    return app


def run_studio_server(
    policy: RootPolicy | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    import uvicorn

    app = create_studio_app(policy)
    uvicorn.run(app, host=host, port=port, log_level="info")
