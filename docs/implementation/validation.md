# Validation record — 22 October 2026

Scope: discovery, planning, and Milestone M0 implementation/verification (`src/mk_studio/`, `tests/`, `scratch/ch01-proof/`).

## M0 Implementation & Test Verification (Antigravity)

| Check | Observed result |
| --- | --- |
| Automated pytest suite (`.venv/bin/pytest -v`) | **28 passed in 2.02s** (`test_paths.py`: 9, `test_recovery.py`: 7, `test_timing.py`: 5, `test_scene.py`: 4, `test_editor_reconciliation.py`: 3) |
| Production root immutability (`verify_production_inventory`) | **PASS** — all 96 production content records in `docs/implementation/inventory.json` remain byte- and SHA-256-identical (`TestAIProducer.palmier/project.json` also unchanged at `9538a346...`) |
| Bit-identical CH01 audio edit reproduction | **PASS** — `scratch/ch01-proof/E02_CH01_Welcome_reproduced_v002.wav` (`1,450,560` stereo frames = `30.220000s` @ `48000 Hz`) matches `E02_CH01_Welcome_v002_EDIT_REVIEW_FULL_MIX.wav` with **`max_abs_sample_diff = 0` LSB** and **`rms_sample_diff = 0.0`** |
| Cutout registration & grounded motion QA | **PASS** — across all 907 integer and half-frames, `max_foot_drift` is `0.0081–0.0084 px` (Mar), `0.0103–0.0107 px` (Kuku), `0.0291 px` (Red Flower prop) (`<= 2.0 px` required); `head_excursion` is `41.73–53.10 px` (Mar) and `38.69–41.45 px` (Kuku) (`>= 3.0 px` required); response hold (`frames 501..621`) excursion is `0.0000 px` |
| SQLite durable state (`scratch/ch01-proof/state.db`) | **PASS** — all 7 tables created and populated (`episodes: 1`, `chapters: 1`, `approvals: 1`, `artifacts: 18`, `runs: 3`, `run_events: 2`, `file_operations: 4`) |
| Native Palmier scratch creation check | Disassembled `PalmierPro.AppState.createProject(named:)` (`0x10176eee8`); confirmed MCP `create` rejects path components and writes only to `~/Documents/Palmier Pro/`. `mk-studio assemble-ch01` cleanly blocks with exit code `2` until the owner saves an empty `.palmier` project to `scratch/ch01-proof/E02_CH01_Technical_Proof_v001.palmier` via `Cmd+N` |

## Discovery Checks (Previously Executed)

| Check | Observed result |
| --- | --- |
| Required inputs and production references | Read the three requested documents, request attachment, E02 master/checkpoints/audio reviews, master guide and both PDF references; inspected historical project/snapshot JSON |
| Environment | macOS/memory/CPU/disk via local commands; Python/uv/FFmpeg/ffprobe/Codex versions; editor Info.plist versions; relevant FFmpeg filters |
| File inventory | 55 EP02 regular files + 43 explicit references; SHA-256, exact paths, sizes, stable size/mtime during read |
| Media inspection | ffprobe for all inventoried WAV/PNG sources; PNG headers and decoded alpha planes measured without rewriting images |
| Final source integrity | Rehashed all 98 inventory files after discovery; **98 unchanged, 0 changed paths** |
| Mapping coverage | 55 TSV rows; exact set equality with inventoried EP02 paths; every row's source hash matches inventory |
| Proposed copies | 33 copy rows coalesce to 30 distinct destinations; all still absent during final verification; no conflicting planned hashes |
| Collision/path checks | Destination path components checked for symlinks and case/Unicode aliases; destination roots restricted to exact production root; no conflicts reported |
| Audio alternatives | Four distinct CH02 original hashes; CH03 duplicate candidates match byte-for-byte; no selection/archive action |
| Palmier connection | Official SDK 1.30.0 initialize/tools-list successful; 55 actual schemas; negotiated protocol 2025-11-25 |
| Palmier read-only tools | Project list and empty TestAIProducer timeline read successful; no mutation/transcription/export calls |
| Schema artifact | JSON parsed; 55 tools; SHA-256 `0c6b8e9b3a0292a11923f33adc7c174d8b65fbc7a4676698eeca2671475fea6e` matches captured evidence |

Raw final integrity/mapping results: `scratch/discovery/validation-results.json`. Inventory probe: `scratch/discovery/inventory.py`; raw run log: `scratch/discovery/inventory-run.log`. The final validation independently reread and hashed sources; it did not trust only the initial inventory. Source integrity applies to the listed 98 files, **not a claim that every file anywhere under the production root was exhaustively hashed**.

## Document self-review

Reviewed plan against the attached request: retains full producer vision, CLI/Python/SQLite/FFmpeg/layout approach, future episode contracts, short transactions, approvals, cancellation/failure/restart evidence, single writer and scratch isolation. Explicitly defers infrastructure, walking choreography, UI until proof, Resolve, automatic generation and unmeasured guarantees. Uses live schemas and keeps binary/bundle, historical, schema and execution evidence separate.

The plan contains a real CH01 proof with decode, foot-motion inspection between keys, native close/reopen and duplicate-free rerun. Source hashes/actual audio recipe govern cues. No source-art fixes, audio approvals, transcript accuracy or completed render are implied. Nine internal gates remain; previously recorded script/art approvals are preserved.

Discovery artifacts and exact collision results are reviewable before production operations. The v002/v003 checklist conflict is preserved, not overwritten. No production writes or application implementation were performed by this task. Palmier's own historical project listing remains raw evidence and is not adopted as a root configuration.

## Still unverified

- Native project creation directly inside scratch (no create-path parameter documented by live MCP).
- No-spend local Palmier transcription control and quality on actual chapter audio.
- Foot landmarks, rendered alpha edges, editor pivot/interpolation and visual motion.
- Native save/reopen with persistent animation; actual export/decode/timing; interruption-safe editor rerun.
- Listening approval for CH01–CH03, CH02 take selection, CH04 content and matching stems.
- Backup media independence after reopening; FCPXML fidelity; runtime/cost/automation targets.

Next required decision is review of [implementation-plan.md](implementation-plan.md), explicitly requested by the owner before implementation. Production mapping approval is separate and is not needed to run M0 from current source paths.
