# Mar & Kuku Production Studio — Antigravity Status Summary (v003)

- **Generated At:** `2026-10-22`
- **Workspace:** `/Users/yonassayfu/Projects/mar-kuku-production-studio`
- **Production Root Integrity:** `279 / 279` files in `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/` verified byte-identical (`inventory_passed=true`).
- **Automated Test Suite:** `44 passed` (`.venv/bin/pytest -q`) + `.venv/bin/mk-studio verify-ch01` (`exit_code=0`).

---

## 1. Completed, Partial, and Remaining Work

### A. Completed & Verified
1. **Corrected Readiness Claims, CLI Secrets, Cancellation, and QA (`codex-status-v002.md` items 1–7):**
   - **Keychain Secret CLI (`mk-studio set-secret`):** Implemented `.venv/bin/mk-studio set-secret <provider_id> [--value ...]` in `src/mk_studio/cli.py` and `ProviderRegistry.set_provider_secret()` in `src/mk_studio/providers/registry.py`, storing credentials in macOS Keychain (`/usr/bin/security`) and wiring real HTTPS requests to `https://api.openai.com/v1/responses` for `openai_api`.
   - **Shared Cooperative Stop Signal:** Wired a single shared `threading.Event` across `StudioOrchestrator`, `BrowserController`, `ProviderRegistry`, `MacController`, and `StudioWorkflowEngine` so `POST /api/stop` (or typing `Stop` in Studio Chat) halts in-flight Browser CDP polling, CLI provider subprocesses, Mac commands, and workflow step boundaries.
   - **Local Sandbox Fixture Labeling:** Relabeled `/browser-sandbox` and `browser_web_ai` as `provider_kind="local_fixture"` (`Local Browser Sandbox Fixture (/browser-sandbox)`), clearly distinguishing it from real external AI web sessions (`chatgpt_web`, `gemini_web`, `manus_web`).
   - **Context Path & Exit Code Accuracy:** Updated `StudioOrchestrator.build_context_summary` to reference the actual candidate WAV path (`CH01_PINNED_INPUTS["ch01_candidate_wav"]["path"]`) and updated `verify_ch01` / `assemble_ch01` chat handlers so nonzero exit codes mark the job `failed`.
   - **Exact MP4 Stream Durations & Native Project QA:** Updated `verify_exported_mp4` and `verify_native_palmier_project` in `src/mk_studio/qa.py` to parse `project.json` directly inside the `.palmier` bundle on disk (`5` tracks, `16` clips, `10` markers) and report both `video_stream_duration_seconds=30.233333` (`907` frames @ `30fps`) and `audio_stream_duration_seconds=30.200000` (vs `30.220000s` source WAV, `audio_duration_diff_seconds=0.020`).
2. **Real, Resumable “Idea → Three-Agent Consultation → Saved Script” Workflow via Studio Chat:**
   - Implemented `StudioWorkflowEngine` and `classify_chat_instruction` in `src/mk_studio/studio/workflow.py` and wired natural-language chat routing into `StudioOrchestrator.handle_chat_turn` (`src/mk_studio/studio/server.py`).
   - Verified sequentially against the three live websites in the managed visible Chrome session (`127.0.0.1:9222`, persistent profile `scratch/ch01-proof/browser-profile`):
     1. **Step 1 — ChatGPT Master Plan (`https://chatgpt.com/uc/6ada036c-7d6c-83ea-93d8-22f8a413d90c`):** Drafted the episode concept (`Mar and Kuku’s Colorful Flower Garden`) and specialist prompts for Gemini and Manus; saved `01_chatgpt_master_plan.json` & `.md` (`sha256=d0453fb8eea6e47e1e40f7569229624f5882806d802d80a4dea8193e4d2d62db`).
     2. **Step 2 — Google Gemini Reviewer (`https://gemini.google.com/app`):** Critiqued preschool counting clarity, hard pose swaps, and interactive hold framing; saved `02_gemini_review.json` & `.md` (`sha256=1db62d55b81600c39d3a08bb995708a771a6e08d388e8e8b6a5e8af886a5b9a9`).
     3. **Step 3 — Manus Specialist Task (`https://manus.im/app/xVApR3Hc7uyqZAMjvXx48B`):** Submitted the TTS pacing and frame-boundary prompt to the authenticated Manus session (`Guangut Semera`), waited for `Task completed`, and extracted Manus's `110–120 WPM`, `0.4s (12-frame)` counting pause, and `120-frame` hold specification into `03_manus_specialist.json` & `.md` (`sha256=2d7e9cfdeefa0800ebdd2ef255f041c8498ab105421d217c15918818fbfdbe32`).
     4. **Step 4 — ChatGPT Master Synthesis (`https://chatgpt.com/uc/6ada03a5-9488-83ea-8e73-827efbd63964`):** Synthesized all three agents' outputs into `proposed_script_v001.md` (`sha256=bffd59a3a004f33f3c63be208da5c5f604ed5b7c86931e377f6a98c6646ecefb`).
     5. **Step 5 — Natural-Language Follow-Up Correction (`https://chatgpt.com/uc/6ada03a5-9488-83ea-8e73-827efbd63964` + `https://gemini.google.com/app`):** Applied the owner follow-up correction (`"Revise the proposed script so Kuku giggles 'One! Two! Three purple petals!' right after the 4-second response hold at frame 621"`) and saved `proposed_script_v002.md` (`sha256=69d89a38466335201dc367eb9c6274da4a5240d5aa57889c4e8d6571ff54973a`) and canonical pointer `proposed_script.md`.
   - **Stop / Resume Idempotency Verified:** Stopping after Step 1 and sending `Resume the workflow` in Studio Chat resumed directly at Step 2 with `submission_count == 1` across all steps (`step_1` through `step_5`).
   - **Live Browser Preview & Takeover Controls in Studio UI:** Added `/api/browser/preview` and updated `http://127.0.0.1:8765` with a live Chrome preview card, `Owner Takeover` / `Resume Auto` controls, and a real-time 3-Agent Workflow progress panel.

### B. Partial (Working Foundations with Explicit Boundaries)
1. **Mac Desktop & Application Control (`MacController`):**
   - Inspecting macOS permissions (`AXIsProcessTrusted=true`, `system_events_automation_ready=true`, `screen_capture_ready=true`), listing on-screen windows, capturing screenshots (`screencapture`), focusing allowed apps, and executing basic AppleScript keystrokes/clicks with capability profiles (`read_only`, `editor_automation`, `browser_automation`, `general_mac_control`) work today.
   - Full coordinate-level mouse control, menu bar hierarchy traversal, arbitrary window targeting, and rich UI postcondition verification are not yet built.
2. **Additional AI Providers:**
   - `chatgpt_web`, `gemini_web`, `manus_web`, and `codex_cli` work live.
   - `openai_api` has a real HTTPS client and `mk-studio set-secret` command, awaiting an owner OpenAI API key in macOS Keychain.
   - `gemini_cli` is blocked by local Google account tier (`ValidationRequiredError`); use `gemini_web` instead.
   - DeepSeek web session adapter is not yet implemented.

### C. Remaining Roadmap Milestones
1. **Script-to-Asset & Palmier Timeline Bridge:** Parse approved episode scripts (`proposed_script.md`) into structured `ChapterSpec` JSON, generate/intake voiceover and cutout assets, and compile directly into a native `.palmier` timeline.
2. **Episode 002 Chapters CH02–CH07 Scene Templates:** Extend parameterized scene templates across the remaining Episode 002 chapters.
3. **YouTube Studio Upload & Metadata Automation:** Drive YouTube Studio in the managed browser with owner approval gates to upload the verified MP4 export and populate title, description, tags, and made-for-kids settings.
