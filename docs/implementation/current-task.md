# Current Task — M0 CH01 + Resumable 3-Agent Browser Consultation Workflow

## Objective
1. Correct all readiness claims, CLI secret commands, cancellation propagation, and QA reporting identified in `docs/implementation/codex-status-v002.md`.
2. Build and verify one real, resumable **"idea → three-agent consultation → saved script"** workflow through Studio's own chat (`http://127.0.0.1:8765`), using **ChatGPT** (`https://chatgpt.com/`) as Master Producer, **Google Gemini** (`https://gemini.google.com/app`) as Reviewer, and **Manus** (`https://manus.im/app`) as Specialist in the managed Chrome browser.
3. Save conversation references, responses, screenshots, proposed script revisions (`proposed_script_v001.md`, `proposed_script_v002.md`, `proposed_script.md`), and `output_manifest.json` in `scratch/ch01-proof/episodes/EP03_COUNTING_FLOWERS/`, supporting `Stop`, `Resume` without duplicate submissions (`submission_count == 1`), follow-up correction, and restart survival.

## Status
- **COMPLETED & VERIFIED** (`2026-10-22`)
- Automated tests: `44 passed` (`.venv/bin/pytest -q`)
- M0 CH01 verification: `.venv/bin/mk-studio verify-ch01` (`exit_code=0`, `279/279` production files untouched)
- Updated PDF guide: `docs/implementation/Mar_and_Kuku_Studio_MVP_Guide_and_Roadmap.pdf`
- Status summary: `docs/implementation/antigravity-summary-v003.md`
