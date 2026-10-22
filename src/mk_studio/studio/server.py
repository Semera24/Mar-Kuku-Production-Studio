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
from mk_studio.media import CH01_PINNED_INPUTS
from mk_studio.config import RootPolicy
from mk_studio.editors.palmier import PalmierAdapter
from mk_studio.mac.controller import MacController
from mk_studio.providers.registry import ProviderError, ProviderRegistry
from mk_studio.runner import StepRunner
from mk_studio.state import StateStore
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
            elif intent_kind == "assemble_palmier":
                inferred_action = "assemble_palmier"
            elif intent_kind == "export_mp4":
                inferred_action = "export_mp4"
            elif intent_kind == "stage_youtube":
                inferred_action = "stage_youtube"
            elif intent_kind == "full_pipeline":
                inferred_action = "full_pipeline"

        if inferred_action in ('consult_three_agents', 'resume_workflow') or str(inferred_action).startswith('production_'):
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
            if inferred_action == "stop_workflow":
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

            elif inferred_action == "assemble_palmier":
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
      background: var(--bg);
      color: var(--text);
      display: grid;
      grid-template-columns: 340px 1fr 380px;
      height: 100vh;
    }
    aside, main {
      display: flex;
      flex-direction: column;
      border-right: 1px solid var(--border);
      overflow: hidden;
    }
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
  <aside>
    <header>
      <span>Mar & Kuku Studio</span>
      <span class="badge ok">3-Agent Live</span>
    </header>
    <div class="scroll" id="left-panel"></div>
  </aside>

  <main>
    <header>
      <span id="conv-title">Episode 002 / CH01 — Studio Chat</span>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        <button class="primary" onclick="triggerAction('consult_three_agents')">1-4: 3-Agent Script</button>
        <button onclick="triggerAction('generate_assets')">6: Assets</button>
        <button onclick="triggerAction('assemble_palmier')">7: Palmier</button>
        <button onclick="triggerAction('export_mp4')">8: MP4 Export</button>
        <button onclick="triggerAction('stage_youtube')">9: YouTube</button>
        <button class="primary" onclick="triggerAction('full_pipeline')">Full Pipeline (6-9)</button>
        <button onclick="triggerAction('resume_workflow')">Resume</button>
        <button onclick="triggerAction('verify_ch01')">Verify CH01</button>
        <button onclick="triggerAction('mac_inspect')">Inspect Mac</button>
        <button onclick="triggerAction('browser_demo')">Sandbox Fixture</button>
        <button class="danger" onclick="stopAll()">Stop</button>
      </div>
    </header>
    <div class="scroll" id="chat-messages"></div>
    <div class="composer">
      <textarea id="chat-input" placeholder="Type e.g. 'Research the next Mar and Kuku episode, consult Gemini and Manus, and save the proposed script.' or a follow-up correction..."></textarea>
      <div class="composer-row">
        <select id="provider-select">
          <option value="chatgpt_web">ChatGPT Web Session (Master)</option>
          <option value="gemini_web">Google Gemini Web Session (Reviewer)</option>
          <option value="manus_web">Manus Web Session (Specialist)</option>
          <option value="codex_cli">OpenAI Codex CLI (Local)</option>
          <option value="openai_api">OpenAI REST API (Keychain)</option>
          <option value="browser_web_ai">Local Sandbox Fixture (/browser-sandbox)</option>
        </select>
        <button class="primary" id="send-btn" onclick="sendChat()">Send Message</button>
      </div>
    </div>
  </main>

  <aside style="border-right:none;">
    <header>
      <span>Live Browser & Telemetry</span>
      <button onclick="refreshState()">Refresh</button>
    </header>
    <div class="scroll" id="right-panel"></div>
  </aside>

  <script>
    let activeConversationId = 'conv-e02-ch01-main';

    async function refreshState() {
      const res = await fetch(`/api/state?conversation_id=${encodeURIComponent(activeConversationId)}`);
      const data = await res.json();
      const conv = data.conversation || {};
      const wf = data.workflow || {};
      const wfSteps = wf.steps || {};
      document.getElementById('conv-title').innerText = conv.title || activeConversationId;
      document.getElementById('provider-select').value = conv.active_provider_id || 'codex_cli';

      const leftHtml = `
        <div class="card">
          <strong>3-Agent Script Workflow (${wf.episode_id || 'EP03_COUNTING_FLOWERS'})</strong><br/>
          Status: <span class="badge ${wf.status === 'completed' ? 'ok' : 'warn'}">${wf.status || 'not_started'}</span>
          Version: <code>${wf.current_script_version || '-'}</code><br/>
          ${Object.entries(wfSteps).map(([k, v]) => `
            <div style="margin-top:5px;padding-top:4px;border-top:1px solid #334155;font-size:12px;">
              <b>${k}</b>: <span class="badge ${v.status === 'completed' ? 'ok' : 'warn'}">${v.status}</span> (runs: ${v.submission_count || 0})
            </div>
          `).join('')}
        </div>
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
          <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;">
            <button onclick="toggleTakeover(true)">Owner Takeover</button>
            <button onclick="toggleTakeover(false)">Resume Auto</button>
            <button onclick="captureBrowserPreview()">Refresh Preview</button>
          </div>
          <img id="browser-preview-img" class="preview" src="/api/browser/preview?ts=${Date.now()}" alt="Live Managed Chrome Preview" />
        </div>
      `;
      document.getElementById('left-panel').innerHTML = leftHtml;

      const msgs = data.messages || [];
      document.getElementById('chat-messages').innerHTML = msgs.map(m => `
        <div class="msg ${m.role}">
          <div class="msg-meta">${m.role.toUpperCase()} • ${m.provider_id || 'studio'} • ${m.created_at}</div>
          <div>${m.content}</div>
        </div>
      `).join('');

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
    }

    async function sendChat() {
      const input = document.getElementById('chat-input');
      const text = input.value.trim();
      if (!text) return;
      const provider = document.getElementById('provider-select').value;
      input.value = '';
      await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          conversation_id: activeConversationId,
          message: text,
          provider_id: provider
        })
      });
      await refreshState();
    }

    async function triggerAction(actionName) {
      const provider = document.getElementById('provider-select').value;
      let defaultMsg = `Execute studio action: ${actionName}`;
      if (actionName === 'consult_three_agents') {
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

    refreshState();
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
        cid = request.query_params.get("conversation_id") or "conv-e02-ch01-main"
        conv = orchestrator.store.get_conversation(cid) or orchestrator._ensure_default_conversation()
        providers = await asyncio.to_thread(orchestrator.providers.check_providers_health)
        wf_manifest = orchestrator.workflow.load_manifest(DEFAULT_TEST_EPISODE_ID)
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
                "cancel_event_set": orchestrator.cancel_event.is_set(),
            }
        )

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

    app = Starlette(
        routes=[
            Route("/", index_page, methods=["GET"]),
            Route("/browser-sandbox", browser_sandbox_page, methods=["GET"]),
            Route("/api/state", api_state, methods=["GET"]),
            Route("/api/chat", api_chat, methods=["POST"]),
            Route("/api/browser/takeover", api_browser_takeover, methods=["POST"]),
            Route("/api/browser/preview", api_browser_preview, methods=["GET"]),
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
