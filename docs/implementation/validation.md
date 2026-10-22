# Validation Log — M0 CH01 & Live 3-Agent Browser Consultation Workflow (v003)

## 1. Automated Test Suite & M0 CH01 Verification
- Command: `.venv/bin/pytest -q && .venv/bin/mk-studio verify-ch01`
- Result: `44 passed` (`exit_code=0`)
- Production Root Integrity: `279 / 279` files in `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/` byte-identical (`inventory_passed=true`).
- Native Palmier Project (`scratch/ch01-proof/E02_CH01_Technical_Proof_v001.palmier`): `structure_valid=true`, `tracks_on_disk=5`, `clips_on_disk=16`, `markers_on_disk=10`, `reopen_verified=true`.
- Exported 1080p30 MP4 (`scratch/ch01-proof/E02_CH01_Technical_Proof_1080p30_v001.mp4`): `nb_read_frames=907`, `video_stream_duration_seconds=30.233333`, `audio_stream_duration_seconds=30.200000` (source WAV `30.220000s`, `audio_duration_diff_seconds=0.020`), `decode_returncode=0`.

## 2. Live 3-Agent Browser Consultation Workflow Acceptance Test (`EP03_COUNTING_FLOWERS`)
Executed through Studio's `/api/chat` endpoint against the managed visible Chrome session (`127.0.0.1:9222`, persistent profile `scratch/ch01-proof/browser-profile`):
1. **Natural-Language Start + Stop After Step 1:**
   - Instruction: `"Research the next Mar and Kuku episode, consult Gemini and Manus, and save the proposed script."`
   - Completed `step_1_chatgpt_master_plan` on `https://chatgpt.com/uc/6ada036c-7d6c-83ea-93d8-22f8a413d90c` (`submission_count=1`, `sha256=d0453fb8eea6e47e1e40f7569229624f5882806d802d80a4dea8193e4d2d62db`) and halted cleanly at checkpoint `after_step_1` (`status="stopped"`).
2. **Natural-Language Resume (`"Resume the workflow"`):**
   - Skipped `step_1_chatgpt_master_plan` (`submission_count` remained `1`).
   - Completed `step_2_gemini_review` on `https://gemini.google.com/app` (`submission_count=1`, `sha256=1db62d55b81600c39d3a08bb995708a771a6e08d388e8e8b6a5e8af886a5b9a9`).
   - Completed `step_3_manus_specialist` on authenticated `https://manus.im/app/xVApR3Hc7uyqZAMjvXx48B` (`submission_count=1`, `status="completed"`, `sha256=2d7e9cfdeefa0800ebdd2ef255f041c8498ab105421d217c15918818fbfdbe32`).
   - Completed `step_4_chatgpt_synthesis` on `https://chatgpt.com/uc/6ada03a5-9488-83ea-8e73-827efbd63964` (`submission_count=1`), writing `proposed_script_v001.md` (`sha256=bffd59a3a004f33f3c63be208da5c5f604ed5b7c86931e377f6a98c6646ecefb`).
3. **Natural-Language Follow-Up Correction:**
   - Instruction: `"Follow-up correction: Revise the proposed script so Kuku giggles 'One! Two! Three purple petals!' right after the 4-second response hold at frame 621."`
   - Completed `step_5_followup_correction` (`submission_count=1`) via ChatGPT (`https://chatgpt.com/uc/6ada03a5-9488-83ea-8e73-827efbd63964`) + Gemini Reviewer confirmation (`https://gemini.google.com/app`), writing `proposed_script_v002.md` (`sha256=69d89a38466335201dc367eb9c6274da4a5240d5aa57889c4e8d6571ff54973a`) and updating `proposed_script.md` while keeping `submission_count=1` on Steps 1–4.
4. **Application Restart / Reload Survival:**
   - Re-instantiated `create_studio_app(policy)` and verified `/api/state` returned `workflow.status == "completed"`, `current_script_version == "v002"`, and all persisted chat messages and jobs in `scratch/ch01-proof/state.db`.
