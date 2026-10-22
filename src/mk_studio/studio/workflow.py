"""Resumable 'idea -> three-agent consultation -> saved script' workflow orchestrated from Studio chat."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mk_studio.browser.controller import (
    BrowserCancelledError,
    BrowserControlError,
    BrowserController,
    BrowserTakeoverActiveError,
)
from mk_studio.config import RootPolicy, compute_sha256
from mk_studio.providers.registry import ProviderRegistry
from mk_studio.state import StateStore


DEFAULT_TEST_EPISODE_ID = "EP03_COUNTING_FLOWERS"
WorkflowStoppedError = BrowserCancelledError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_chat_instruction(message: str) -> str | None:
    """Classify a natural-language user message in Studio chat into an executable workflow action."""
    text = (message or "").strip().lower()
    if not text:
        return None
    if text in ("stop", "cancel", "halt", "stop workflow", "cancel workflow", "stop the workflow"):
        return "stop_workflow"
    if text in (
        "resume",
        "continue",
        "resume workflow",
        "continue workflow",
        "retry workflow",
        "resume the workflow",
        "continue the workflow",
    ):
        return "resume_workflow"
    if (
        text.startswith("correction:")
        or text.startswith("follow-up correction:")
        or text.startswith("followup correction:")
        or text.startswith("revise:")
        or text.startswith("update script:")
        or ("correction" in text and ("script" in text or "flower" in text or "kuku" in text or "mar" in text or "pause" in text))
        or ("revise" in text and "script" in text)
        or ("change" in text and "script" in text)
    ):
        return "followup_correction"
    if (
        ("research" in text or "brainstorm" in text or "idea" in text or "propose" in text or "create" in text)
        and ("episode" in text or "script" in text or "mar" in text or "kuku" in text)
        and ("gemini" in text or "manus" in text or "consult" in text or "agent" in text or "script" in text)
    ):
        return "consultation_workflow"
    return None


class StudioWorkflowEngine:
    """Executes and resumes the sequential 3-agent browser consultation workflow into a dedicated episode folder."""

    def __init__(
        self,
        policy: RootPolicy,
        store: StateStore,
        browser: BrowserController,
        providers: ProviderRegistry,
        *,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.policy = policy
        self.store = store
        self.browser = browser
        self.providers = providers
        self.cancel_event = cancel_event or browser.cancel_event

    def get_episode_folder(self, episode_id: str = DEFAULT_TEST_EPISODE_ID) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", episode_id.strip()) or DEFAULT_TEST_EPISODE_ID
        folder = self.policy.output_root_resolved / "episodes" / safe_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def manifest_path(self, episode_id: str = DEFAULT_TEST_EPISODE_ID) -> Path:
        return self.get_episode_folder(episode_id) / "output_manifest.json"

    def load_manifest(self, episode_id: str = "EP03_COUNTING_FLOWERS") -> dict[str, Any]:
        folder = self.get_episode_folder(episode_id)
        manifest_path = folder / "output_manifest.json"
        if manifest_path.is_file():
            try:
                return json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "workflow_id": f"wf-{episode_id.lower()}",
            "episode_id": episode_id,
            "dedicated_folder": str(folder),
            "status": "not_started",
            "initial_instruction": "",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "conversation_references": {
                "chatgpt_master_url": "",
                "gemini_reviewer_url": "",
                "manus_specialist_url": "",
            },
            "steps": {
                "step_1_chatgpt_master_plan": {"status": "pending", "submission_count": 0},
                "step_2_gemini_review": {"status": "pending", "submission_count": 0},
                "step_3_manus_specialist": {"status": "pending", "submission_count": 0},
                "step_4_chatgpt_synthesis": {"status": "pending", "submission_count": 0},
                "step_5_followup_correction": {"status": "pending", "submission_count": 0},
            },
            "script_revisions": [],
            "artifacts": [],
        }

    def save_manifest(self, manifest: dict[str, Any], episode_id: str = "EP03_COUNTING_FLOWERS") -> Path:
        folder = self.get_episode_folder(episode_id)
        manifest["updated_at"] = _utc_now()
        manifest_path = self.policy.resolve_output(
            folder / "output_manifest.json",
            overwrite=True,
            create_parents=True,
        )
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        self.store.record_artifact(
            episode_id=episode_id,
            chapter_id="CH01",
            role="workflow:output_manifest",
            path=str(manifest_path),
            sha256=compute_sha256(manifest_path),
            bytes_size=manifest_path.stat().st_size,
            metadata={"status": manifest.get("status"), "updated_at": manifest["updated_at"]},
        )
        return manifest_path

    def _step_already_completed(self, step_rec: dict[str, Any]) -> bool:
        status = str(step_rec.get("status") or "")
        out_file = step_rec.get("output_file")
        out_sha = step_rec.get("output_sha256")
        if status not in ("completed", "partial_awaiting_owner_login") or not out_file or not out_sha:
            return False
        p = Path(str(out_file))
        return p.is_file() and compute_sha256(p) == out_sha

    def _check_stop(self, manifest: dict[str, Any], episode_id: str, step_after: str | None = None) -> None:
        if self.cancel_event.is_set():
            manifest["status"] = "stopped"
            manifest["stopped_at_step"] = step_after or ""
            self.save_manifest(manifest, episode_id)
            raise BrowserCancelledError(f"Workflow stopped by owner signal (at {step_after or 'checkpoint'}).")

    def run_or_resume_consultation(
        self,
        instruction: str,
        *,
        episode_id: str = "EP03_COUNTING_FLOWERS",
        conversation_id: str = "conv-e02-ch01-main",
        pause_on_manus_login: bool = False,
        stop_after_step: str | None = None,
    ) -> dict[str, Any]:
        """Run or resume the 4-step 'idea -> 3-agent consultation -> saved script' workflow without repeating completed steps."""
        folder = self.get_episode_folder(episode_id)
        manifest = self.load_manifest(episode_id)
        if instruction and not manifest.get("initial_instruction"):
            manifest["initial_instruction"] = instruction
        eff_instruction = manifest.get("initial_instruction") or instruction or (
            "Research the next Mar and Kuku episode about counting three colorful garden flowers, "
            "consult Gemini and Manus, and save the proposed script."
        )

        self.store.upsert_episode(
            episode_id=episode_id,
            title="Mar & Kuku — Three Garden Flowers (Consultation Proof)",
            production_root=str(folder),
            status="in_progress",
        )
        manifest["status"] = "running"
        self.save_manifest(manifest, episode_id)

        steps = manifest["steps"]
        refs = manifest["conversation_references"]

        try:
            # ------------------------------------------------------------------
            # STEP 1: ChatGPT (Master Producer) — Concept + Specialist Prompts
            # ------------------------------------------------------------------
            s1 = steps["step_1_chatgpt_master_plan"]
            if not self._step_already_completed(s1):
                self._check_stop(manifest, episode_id, "before_step_1")
                prompt_1 = (
                    "You are the Master Creative Producer for the preschool 2D animated series 'Mar and Kuku'. "
                    f"Owner instruction: \"{eff_instruction}\"\n\n"
                    "Respond concisely with three clearly labeled sections:\n"
                    "1. EPISODE_CONCEPT: Title, learning goal (counting 1 to 3 garden flowers: red, yellow, purple), "
                    "and a 3-scene outline for hosts Mar and Kuku.\n"
                    "2. QUESTION_FOR_GEMINI: One specific review question asking Gemini to critique preschool age-appropriateness, "
                    "call-and-response clarity, and visual pose cues.\n"
                    "3. QUESTION_FOR_MANUS: One specific specialist task asking Manus for TTS voiceover pacing, words-per-minute, "
                    "and exact timing around a 4.0-second interactive counting pause."
                )
                c1 = self.browser.consult_chatgpt_web(
                    prompt_1,
                    conversation_id=conversation_id,
                    screenshot_name=f"{episode_id}_01_chatgpt_master_plan.png",
                )
                refs["chatgpt_master_url"] = str(c1.get("url") or "https://chatgpt.com/")
                out_json_1 = folder / "01_chatgpt_master_plan.json"
                out_md_1 = folder / "01_chatgpt_master_plan.md"
                payload_1 = {
                    "step": "step_1_chatgpt_master_plan",
                    "role": "master_producer",
                    "provider": "chatgpt_web",
                    "url": refs["chatgpt_master_url"],
                    "prompt": prompt_1,
                    "response": c1["response"],
                    "screenshot": c1["screenshot"]["path"],
                    "screenshot_sha256": c1["screenshot"]["sha256"],
                    "completed_at": _utc_now(),
                }
                out_json_1.write_text(json.dumps(payload_1, indent=2) + "\n", encoding="utf-8")
                out_md_1.write_text(
                    f"# Step 1 — ChatGPT Master Plan (`{refs['chatgpt_master_url']}`)\n\n"
                    f"## Prompt\n{prompt_1}\n\n## ChatGPT Response\n{c1['response']}\n",
                    encoding="utf-8",
                )
                sha_1 = compute_sha256(out_json_1)
                s1.update(
                    {
                        "status": "completed",
                        "submission_count": int(s1.get("submission_count") or 0) + 1,
                        "url": refs["chatgpt_master_url"],
                        "output_file": str(out_json_1),
                        "markdown_file": str(out_md_1),
                        "output_sha256": sha_1,
                        "screenshot_path": c1["screenshot"]["path"],
                        "completed_at": payload_1["completed_at"],
                    }
                )
                self.store.record_artifact(
                    episode_id=episode_id,
                    chapter_id="CH01",
                    role="consultation:step_1_chatgpt_master_plan",
                    path=str(out_json_1),
                    sha256=sha_1,
                    bytes_size=out_json_1.stat().st_size,
                )
                self.save_manifest(manifest, episode_id)
            else:
                payload_1 = json.loads(Path(s1["output_file"]).read_text(encoding="utf-8"))

            if stop_after_step == "step_1_chatgpt_master_plan":
                self.cancel_event.set()
            self._check_stop(manifest, episode_id, "after_step_1")

            # ------------------------------------------------------------------
            # STEP 2: Google Gemini (Reviewer) — Critique & Pacing Review
            # ------------------------------------------------------------------
            s2 = steps["step_2_gemini_review"]
            if not self._step_already_completed(s2):
                self._check_stop(manifest, episode_id, "before_step_2")
                prompt_2 = (
                    "You are the Educational & Script Reviewer for the preschool animated series 'Mar and Kuku'. "
                    "Here is the Master Producer's Episode Concept & Question from ChatGPT:\n\n"
                    f"{payload_1['response'][:1800]}\n\n"
                    "Please provide your concise Reviewer Report in 3 bullet points covering:\n"
                    "1. Preschool counting clarity & repetition strength;\n"
                    "2. Specific full-body pose swap suggestions for Mar and Kuku (no crossfades, hard pose swaps only);\n"
                    "3. One concrete improvement for the 4-second interactive viewer response hold."
                )
                c2 = self.browser.consult_gemini_web(
                    prompt_2,
                    conversation_id=conversation_id,
                    screenshot_name=f"{episode_id}_02_gemini_review.png",
                )
                refs["gemini_reviewer_url"] = str(c2.get("url") or "https://gemini.google.com/app")
                out_json_2 = folder / "02_gemini_review.json"
                out_md_2 = folder / "02_gemini_review.md"
                payload_2 = {
                    "step": "step_2_gemini_review",
                    "role": "reviewer",
                    "provider": "gemini_web",
                    "url": refs["gemini_reviewer_url"],
                    "prompt": prompt_2,
                    "response": c2["response"],
                    "screenshot": c2["screenshot"]["path"],
                    "screenshot_sha256": c2["screenshot"]["sha256"],
                    "completed_at": _utc_now(),
                }
                out_json_2.write_text(json.dumps(payload_2, indent=2) + "\n", encoding="utf-8")
                out_md_2.write_text(
                    f"# Step 2 — Google Gemini Reviewer Report (`{refs['gemini_reviewer_url']}`)\n\n"
                    f"## Prompt\n{prompt_2}\n\n## Gemini Response\n{c2['response']}\n",
                    encoding="utf-8",
                )
                sha_2 = compute_sha256(out_json_2)
                s2.update(
                    {
                        "status": "completed",
                        "submission_count": int(s2.get("submission_count") or 0) + 1,
                        "url": refs["gemini_reviewer_url"],
                        "output_file": str(out_json_2),
                        "markdown_file": str(out_md_2),
                        "output_sha256": sha_2,
                        "screenshot_path": c2["screenshot"]["path"],
                        "completed_at": payload_2["completed_at"],
                    }
                )
                self.store.record_artifact(
                    episode_id=episode_id,
                    chapter_id="CH01",
                    role="consultation:step_2_gemini_review",
                    path=str(out_json_2),
                    sha256=sha_2,
                    bytes_size=out_json_2.stat().st_size,
                )
                self.save_manifest(manifest, episode_id)
            else:
                payload_2 = json.loads(Path(s2["output_file"]).read_text(encoding="utf-8"))

            if stop_after_step == "step_2_gemini_review":
                self.cancel_event.set()
            self._check_stop(manifest, episode_id, "after_step_2")

            # ------------------------------------------------------------------
            # STEP 3: Manus (Specialist) — TTS Pacing & Pause Timing Task
            # ------------------------------------------------------------------
            s3 = steps["step_3_manus_specialist"]
            if not self._step_already_completed(s3):
                self._check_stop(manifest, episode_id, "before_step_3")
                prompt_3 = (
                    "Specialist Task for Manus (Speech/TTS & Timing): For a 30-second preschool animation chapter "
                    "('Mar and Kuku's Three Garden Flowers' at 30 fps = 900 frames), specify recommended TTS speaking rate "
                    "(WPM), breath pauses between counting 1, 2, 3, and exact frame boundaries for a 4.0-second (120-frame) "
                    "interactive response hold."
                )
                c3 = self.browser.consult_manus_web(
                    prompt_3,
                    conversation_id=conversation_id,
                    screenshot_name=f"{episode_id}_03_manus_specialist.png",
                )
                refs["manus_specialist_url"] = str(c3.get("url") or "https://manus.im/")
                out_json_3 = folder / "03_manus_specialist.json"
                out_md_3 = folder / "03_manus_specialist.md"
                manus_status = (
                    "completed" if c3.get("authenticated") and c3.get("response") else "partial_awaiting_owner_login"
                )
                payload_3 = {
                    "step": "step_3_manus_specialist",
                    "role": "tts_timing_specialist",
                    "provider": "manus_web",
                    "status": manus_status,
                    "authenticated": bool(c3.get("authenticated")),
                    "prompt_submitted": bool(c3.get("prompt_submitted")),
                    "prompt_saved_on_site": bool(c3.get("prompt_saved_on_site")),
                    "url": refs["manus_specialist_url"],
                    "prompt": prompt_3,
                    "response": c3.get("response") or "",
                    "login_gate_message": c3.get("login_gate_message") or "",
                    "dom_excerpt": c3.get("dom_excerpt") or "",
                    "screenshot": c3["screenshot"]["path"],
                    "screenshot_sha256": c3["screenshot"]["sha256"],
                    "completed_at": _utc_now(),
                }
                out_json_3.write_text(json.dumps(payload_3, indent=2) + "\n", encoding="utf-8")
                out_md_3.write_text(
                    f"# Step 3 — Manus Specialist Consultation (`{refs['manus_specialist_url']}`)\n\n"
                    f"- **Status:** `{manus_status}`\n"
                    f"- **Prompt Submitted to Manus Composer:** `{payload_3['prompt_submitted']}`\n"
                    f"- **Saved Pending Owner Login on Manus:** `{payload_3['prompt_saved_on_site']}`\n\n"
                    f"## Prompt\n{prompt_3}\n\n"
                    f"## Manus Website Observation\n{payload_3['response'] or payload_3['login_gate_message']}\n",
                    encoding="utf-8",
                )
                sha_3 = compute_sha256(out_json_3)
                s3.update(
                    {
                        "status": manus_status,
                        "submission_count": int(s3.get("submission_count") or 0) + 1,
                        "url": refs["manus_specialist_url"],
                        "output_file": str(out_json_3),
                        "markdown_file": str(out_md_3),
                        "output_sha256": sha_3,
                        "screenshot_path": c3["screenshot"]["path"],
                        "login_gate_message": payload_3["login_gate_message"],
                        "completed_at": payload_3["completed_at"],
                    }
                )
                self.store.record_artifact(
                    episode_id=episode_id,
                    chapter_id="CH01",
                    role="consultation:step_3_manus_specialist",
                    path=str(out_json_3),
                    sha256=sha_3,
                    bytes_size=out_json_3.stat().st_size,
                )
                self.save_manifest(manifest, episode_id)
                if pause_on_manus_login and manus_status == "partial_awaiting_owner_login":
                    self.browser.enter_owner_takeover(
                        reason="Sign into Manus (https://manus.im/) in the visible Chrome window to release the saved specialist prompt.",
                        conversation_id=conversation_id,
                    )
                    manifest["status"] = "awaiting_owner_login"
                    self.save_manifest(manifest, episode_id)
                    return manifest
            else:
                payload_3 = json.loads(Path(s3["output_file"]).read_text(encoding="utf-8"))

            if stop_after_step == "step_3_manus_specialist":
                self.cancel_event.set()
            self._check_stop(manifest, episode_id, "after_step_3")

            # ------------------------------------------------------------------
            # STEP 4: ChatGPT (Master Synthesis) — Proposed Script v001
            # ------------------------------------------------------------------
            s4 = steps["step_4_chatgpt_synthesis"]
            if not self._step_already_completed(s4):
                self._check_stop(manifest, episode_id, "before_step_4")
                manus_summary = (
                    payload_3["response"]
                    if payload_3.get("response")
                    else (
                        f"[Manus Web Status: {payload_3.get('login_gate_message')} — applying Studio 30fps timing standard: "
                        "115 WPM preschool delivery, 4.0-second (120-frame) interactive hold at frames 501..621 before final reveal.]"
                    )
                )
                prompt_4 = (
                    "As Master Producer for 'Mar and Kuku', synthesize the final proposed script (v001) incorporating:\n"
                    f"1. Your initial concept:\n{payload_1['response'][:900]}\n\n"
                    f"2. Gemini Reviewer feedback:\n{payload_2['response'][:900]}\n\n"
                    f"3. Specialist TTS & Pause Timing Constraints:\n{manus_summary}\n\n"
                    "Write the complete, production-ready 'Mar and Kuku' Chapter Script with:\n"
                    "- Title & Learning Objective\n"
                    "- Timecoded / Frame-annotated dialogue lines for MAR and KUKU\n"
                    "- Explicit Pose Swap tags ([POSE: Mar_Wave], [POSE: Kuku_Happy], etc.)\n"
                    "- Explicit 4.0-second Interactive Counting Hold ([HOLD: 16.70s - 20.70s / Frames 501..621])\n"
                    "- Prop reveal cue ([PROP_REVEAL: Red, Yellow, and Purple Garden Flowers])."
                )
                c4 = self.browser.consult_chatgpt_web(
                    prompt_4,
                    conversation_url=refs.get("chatgpt_master_url") or None,
                    conversation_id=conversation_id,
                    screenshot_name=f"{episode_id}_04_chatgpt_synthesis.png",
                )
                refs["chatgpt_master_url"] = str(c4.get("url") or refs.get("chatgpt_master_url") or "https://chatgpt.com/")
                refs["chatgpt_synthesis_url"] = refs["chatgpt_master_url"]
                out_json_4 = folder / "04_chatgpt_synthesis.json"
                script_v1_path = folder / "proposed_script_v001.md"
                script_latest_path = folder / "proposed_script.md"

                script_header = (
                    f"# Proposed Script (v001) — {episode_id}\n\n"
                    f"- **Generated At:** `{_utc_now()}`\n"
                    f"- **Master Producer (ChatGPT URL):** `{refs['chatgpt_master_url']}`\n"
                    f"- **Reviewer (Gemini URL):** `{refs['gemini_reviewer_url']}`\n"
                    f"- **Specialist (Manus URL & Status):** `{refs['manus_specialist_url']}` (`{s3['status']}`)\n\n"
                    "---\n\n"
                )
                full_script_md = script_header + c4["response"] + "\n"
                script_v1_path.write_text(full_script_md, encoding="utf-8")
                script_latest_path.write_text(full_script_md, encoding="utf-8")
                script_v1_sha = compute_sha256(script_v1_path)

                payload_4 = {
                    "step": "step_4_chatgpt_synthesis",
                    "role": "master_synthesis",
                    "provider": "chatgpt_web",
                    "url": refs["chatgpt_master_url"],
                    "prompt": prompt_4,
                    "response": c4["response"],
                    "script_path": str(script_v1_path),
                    "script_sha256": script_v1_sha,
                    "screenshot": c4["screenshot"]["path"],
                    "screenshot_sha256": c4["screenshot"]["sha256"],
                    "completed_at": _utc_now(),
                }
                out_json_4.write_text(json.dumps(payload_4, indent=2) + "\n", encoding="utf-8")
                sha_4 = compute_sha256(out_json_4)
                s4.update(
                    {
                        "status": "completed",
                        "submission_count": int(s4.get("submission_count") or 0) + 1,
                        "url": refs["chatgpt_master_url"],
                        "output_file": str(out_json_4),
                        "script_file": str(script_v1_path),
                        "script_sha256": script_v1_sha,
                        "output_sha256": sha_4,
                        "screenshot_path": c4["screenshot"]["path"],
                        "completed_at": payload_4["completed_at"],
                    }
                )
                manifest["current_script_version"] = "v001"
                manifest["current_script_path"] = str(script_latest_path)
                manifest["script_revisions"].append(
                    {
                        "version": "v001",
                        "path": str(script_v1_path),
                        "sha256": script_v1_sha,
                        "created_at": payload_4["completed_at"],
                    }
                )
                self.store.record_artifact(
                    episode_id=episode_id,
                    chapter_id="CH01",
                    role="script:proposed_v001",
                    path=str(script_v1_path),
                    sha256=script_v1_sha,
                    bytes_size=script_v1_path.stat().st_size,
                )
                self.save_manifest(manifest, episode_id)

            manifest["status"] = (
                "completed_with_manus_pending_login"
                if s3.get("status") == "partial_awaiting_owner_login"
                else "completed"
            )
            self.save_manifest(manifest, episode_id)
            return manifest

        except (BrowserCancelledError, BrowserTakeoverActiveError) as exc:
            if manifest.get("status") not in ("stopped", "awaiting_owner_login"):
                manifest["status"] = "stopped"
            manifest["last_error"] = str(exc)
            self.save_manifest(manifest, episode_id)
            raise

    def apply_followup_correction(
        self,
        correction_instruction: str,
        *,
        episode_id: str = "EP03_COUNTING_FLOWERS",
        conversation_id: str = "conv-e02-ch01-main",
    ) -> dict[str, Any]:
        """Apply an owner follow-up correction to the proposed script via ChatGPT & Gemini without repeating Steps 1-4."""
        folder = self.get_episode_folder(episode_id)
        manifest = self.load_manifest(episode_id)
        steps = manifest["steps"]
        refs = manifest["conversation_references"]

        # Ensure initial steps 1..4 are completed first (resuming if needed without repeating completed steps)
        if not self._step_already_completed(steps["step_4_chatgpt_synthesis"]):
            manifest = self.run_or_resume_consultation(
                manifest.get("initial_instruction") or "",
                episode_id=episode_id,
                conversation_id=conversation_id,
            )
            steps = manifest["steps"]
            refs = manifest["conversation_references"]

        self._check_stop(manifest, episode_id, "before_step_5_correction")
        prev_script_path = Path(steps["step_4_chatgpt_synthesis"]["script_file"])
        prev_script_text = prev_script_path.read_text(encoding="utf-8")

        prompt_corr = (
            "Owner Follow-Up Correction for the 'Mar and Kuku' script:\n"
            f"\"{correction_instruction}\"\n\n"
            "Here is the previous proposed script (v001):\n"
            f"{prev_script_text[:1800]}\n\n"
            "Produce the revised complete script (v002) applying the owner's exact correction while preserving "
            "the 30fps frame annotations, hard pose swap tags, and the 4.0-second interactive counting hold (frames 501..621)."
        )
        c5_gpt = self.browser.consult_chatgpt_web(
            prompt_corr,
            conversation_url=refs.get("chatgpt_master_url") or None,
            conversation_id=conversation_id,
            screenshot_name=f"{episode_id}_05_chatgpt_correction_v002.png",
        )
        refs["chatgpt_master_url"] = str(c5_gpt.get("url") or refs.get("chatgpt_master_url") or "https://chatgpt.com/")

        prompt_gem_confirm = (
            "Verify that this revised Mar and Kuku script (v002) accurately incorporates the owner's correction "
            f"(\"{correction_instruction}\") and preserves the 4.0-second interactive counting pause:\n\n"
            f"{c5_gpt['response'][:1600]}"
        )
        c5_gem = self.browser.consult_gemini_web(
            prompt_gem_confirm,
            conversation_url=refs.get("gemini_reviewer_url") or None,
            conversation_id=conversation_id,
            screenshot_name=f"{episode_id}_05_gemini_correction_confirm.png",
        )
        refs["gemini_reviewer_url"] = str(c5_gem.get("url") or refs.get("gemini_reviewer_url") or "https://gemini.google.com/app")

        script_v2_path = folder / "proposed_script_v002.md"
        script_latest_path = folder / "proposed_script.md"
        script_v2_header = (
            f"# Proposed Script (v002 — Revised After Owner Correction) — {episode_id}\n\n"
            f"- **Revised At:** `{_utc_now()}`\n"
            f"- **Owner Correction Applied:** `{correction_instruction}`\n"
            f"- **Master Producer (ChatGPT URL):** `{refs['chatgpt_master_url']}`\n"
            f"- **Reviewer Confirmation (Gemini URL):** `{refs['gemini_reviewer_url']}`\n\n"
            "---\n\n"
        )
        full_v2_md = script_v2_header + c5_gpt["response"] + "\n\n---\n\n## Gemini Reviewer Confirmation (v002)\n\n" + c5_gem["response"] + "\n"
        script_v2_path.write_text(full_v2_md, encoding="utf-8")
        script_latest_path.write_text(full_v2_md, encoding="utf-8")
        script_v2_sha = compute_sha256(script_v2_path)

        out_json_5 = folder / "05_followup_correction_v002.json"
        payload_5 = {
            "step": "step_5_followup_correction",
            "correction_instruction": correction_instruction,
            "chatgpt_url": refs["chatgpt_master_url"],
            "chatgpt_revised_script": c5_gpt["response"],
            "chatgpt_screenshot": c5_gpt["screenshot"]["path"],
            "gemini_url": refs["gemini_reviewer_url"],
            "gemini_confirmation": c5_gem["response"],
            "gemini_screenshot": c5_gem["screenshot"]["path"],
            "script_v002_path": str(script_v2_path),
            "script_v002_sha256": script_v2_sha,
            "completed_at": _utc_now(),
        }
        out_json_5.write_text(json.dumps(payload_5, indent=2) + "\n", encoding="utf-8")
        sha_5 = compute_sha256(out_json_5)

        s5 = steps["step_5_followup_correction"]
        refs["chatgpt_followup_url"] = refs["chatgpt_master_url"]
        s5.update(
            {
                "status": "completed",
                "submission_count": int(s5.get("submission_count") or 0) + 1,
                "correction_instruction": correction_instruction,
                "url": refs["chatgpt_master_url"],
                "chatgpt_url": refs["chatgpt_master_url"],
                "gemini_url": refs["gemini_reviewer_url"],
                "output_file": str(out_json_5),
                "script_file": str(script_v2_path),
                "script_v002_file": str(script_v2_path),
                "script_sha256": script_v2_sha,
                "output_sha256": sha_5,
                "chatgpt_screenshot_path": c5_gpt["screenshot"]["path"],
                "gemini_screenshot_path": c5_gem["screenshot"]["path"],
                "completed_at": payload_5["completed_at"],
            }
        )
        manifest["current_script_version"] = "v002"
        manifest["current_script_path"] = str(script_latest_path)
        manifest["script_revisions"].append(
            {
                "version": "v002",
                "correction": correction_instruction,
                "path": str(script_v2_path),
                "sha256": script_v2_sha,
                "created_at": payload_5["completed_at"],
            }
        )
        self.store.record_artifact(
            episode_id=episode_id,
            chapter_id="CH01",
            role="script:proposed_v002",
            path=str(script_v2_path),
            sha256=script_v2_sha,
            bytes_size=script_v2_path.stat().st_size,
        )
        manifest["status"] = (
            "completed_with_manus_pending_login"
            if steps["step_3_manus_specialist"].get("status") == "partial_awaiting_owner_login"
            else "completed"
        )
        self.save_manifest(manifest, episode_id)
        return manifest
