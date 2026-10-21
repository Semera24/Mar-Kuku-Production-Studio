# Mar and Kuku CH01 proof implementation plan

> Execution after owner review: inline in this local workspace using `superpowers:executing-plans`. No parallel agents, cloud projects, or application implementation in the discovery turn.

**Goal:** Prove an editable, repeatable CH01 assembly using existing sources, then grow the same producer workflow to EP02 and future episodes.

**Architecture:** Sequential CLI producer, compact SQLite state, typed revision contracts and a deterministic scene compiler. One Palmier writer uses the official MCP SDK and current schemas. Add a small loopback review interface only after the editor proof passes.

**Stack:** Python 3.12.15 in an isolated environment; standard-library SQLite/path/hash/process tools; Pydantic and Pillow; official `mcp`; installed FFmpeg/ffprobe. Start with argparse; no NumPy, FastAPI, MLX or AI provider dependency until needed.

**Design:** [decision-record.md](decision-record.md). **Evidence:** [discovery.md](discovery.md). **Status:** awaiting plan review, not implementation authorization.

## Constraints

- Production root: `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/`; immutable name. EP01 and official/shared source assets read-only.
- Application root: `/Users/yonassayfu/Projects/mar-kuku-production-studio/`. All proof projects, exports, state and evidence: `scratch/ch01-proof/` beneath it. Planning/tracking documents: `docs/implementation/`.
- No production reorganization until exact mapping reviewed. No paid generation, transcription spending, editor/OS upgrade or unrelated configuration change.
- Preserve existing script/art approvals. CH01's 30.22-second review audio is authorized only as a labelled technical input; no inferred listening approval.
- Canvas `1920x1080`, fps `30/1`; full-body pose hard cuts, no visual crossfades; genuine visible foot-anchored motion. Few poses and one red flower first.
- No automatic alpha clamping, recolouring, regeneration or source normalization. Background may remain RGB.
- Source seconds/samples, edited seconds/samples, master frames and clip-local keyframes are distinct types/timebases. Use half-up rounding for cues, reject removed cues; no illustrative report timestamps copied as verified.

## M0 — approved implementation boundary

### 1. Bootstrap and scratch containment

Files: `pyproject.toml`, `.python-version`, `.gitignore`, `src/mk_studio/{cli,config,contracts}.py`, `tests/test_paths.py`.

- [ ] Create isolated Python 3.12 environment and lock compatible dependencies; initialize local Git because this directory currently has no repository. Exclude scratch, environments, media, runtime databases and credentials; no remote/push.
- [ ] Define `RootPolicy.resolve_read(alias, relative_path)` and `resolve_output(relative_path)` returning validated Paths; reject traversal, symlink escape and output roots outside scratch. Test synthetic symlink escapes, missing/changed sources and existing output refusal.
- [ ] `mk-studio doctor` records versions, available disk and dependency/schema fingerprints; no app launch or install hidden in doctor.
- [ ] Verify a supported native method to create/save the empty project **directly inside scratch** before any creation. Live MCP `manage_project.create` has no documented destination parameter. Inspect the app's supported save workflow; if necessary request one precise owner save action to `scratch/ch01-proof/E02_CH01_Technical_Proof_v001.palmier`. Do not create in default Documents storage and silently move it, or write native JSON by hand.

Acceptance: isolated environment imports; path tests pass; project destination is verified. If scratch-only creation cannot be achieved, record the exact blocker; do not relax the boundary.

### 2. Durable execution and no duplicate writes

Files: `src/mk_studio/{state,runner}.py`, `src/mk_studio/migrations/001.sql`, `tests/test_recovery.py`.

- [ ] Implement the seven tables in the decision record. `begin_step(run_id, step_key, input_hash, intent)` commits intent; `finish_step(step_id, receipt, evidence)` commits outcome after external work. Immutable versioned artifacts preserve approval provenance.
- [ ] Implement `reconcile_run(run_id)` from persisted evidence and external readback, not elapsed time. Use one owned run lock and one editor-writer lock; do not hold a SQLite write transaction across an editor call or FFmpeg process.
- [ ] Test interruption after external output but before receipt commit, changed input invalidation, unknown editor outcome, concurrent run rejection and cancellation of an owned subprocess. A second DB connection must remain able to commit during a deliberately stalled external step.

Acceptance: rerun resolves one logical output; uncertain state is blocked with evidence; no blind mutation retry.

### 3. Bind actual CH01 inputs and timing

Files: `src/mk_studio/{media,timing}.py`, `scratch/ch01-proof/inputs.json`, `tests/test_timing.py`.

- [ ] Rehash the exact CH01 candidate, original and edit-report inputs. Reference background, Mar Wave/Introduce Self, Kuku Wave/Happy and one red flower at the current paths in the inventory. `Kuku_Happy_WingUp_v001.png` and `Kuku_Listen_v001.png` from Antigravity's example are absent; use the actual approved library, without fabricating aliases or files.
- [ ] Probe audio stream explicitly (`0:a:0`); several originals have an attached JPEG stream. Candidate has 1,450,560 audio sample frames at 48,000 Hz: 30.22 s. Cover its full duration with 907 video frames (30.233333 s); record the 640-sample/13.333-ms visual overhang. No stretching or truncating source audio to fit.
- [ ] Bind an edit graph to original and candidate hashes, including overlaps/fades from the actual CH01 edit report. Reproduce/compare the recipe in scratch before claiming it describes the candidate; discrepancy leaves provenance unverified. Imported mapped cues remain provisional.
- [ ] Test Palmier transcription on this real audio only after confirming a supported no-spend local path; inspect `inspect_media` with word timestamps and explicit English language where applicable. Record resolved provider, actual words, units, timing and uncertainties. Do not call auto-cloud `get_transcript` without spending authority. If local control is unavailable, mark Palmier ASR unadopted and use Manus import; do not install another model silently.
- [ ] Verify the relevant word onsets against this exact candidate/edit graph. For technical motion tests, synthetic frame-labelled events may be used and explicitly labelled; they are not teaching cues. The flower's teaching reveal follows the audible **red** onset, not automatically the report's 24.16-second phrase start.
- [ ] Test half-up half-frame boundaries (0.05 s -> 2 frames), source trim/placement, removed segments, overlapping crossfade source ranges, and invalid frame bounds. Reject unsupported speed changes in M0.

Acceptance: manifest records exact hashes/timebases and audio-review status; any unresolved teaching onsets are clearly held/provisional, never declared approved.

### 4. Calibrate registration and compile the scene

Files: `src/mk_studio/{registration,scene}.py`, `src/mk_studio/templates/welcome.py`, `tests/test_scene.py`.

- [ ] Inspect the actual PNGs; record alpha bounds at several thresholds and manually verified support-foot landmark(s) per pose. Measure editor rotation centre, sign, top-left position, absolute normalized scale and clip-relative timing using a small calibration inside the scratch project.
- [ ] Implement `compile_scene(chapter_spec, asset_manifest, calibration) -> TimelinePlan`: stable logical clip IDs, non-overlapping half-open ranges, preserved pixel aspect, hard cuts and per-pose placement compensation. Start near Mar x .24/h .63, Kuku x .46/h .40 and feet y .935, adjusting only from measured registration.
- [ ] Implement compensated motion around the verified landmark; shared phase across pose swaps, explicit still holds. Test both anchors and interpolated midframes, not merely analytic endpoints. Use denser/per-frame keys if editor interpolation causes foot drift.
- [ ] Test differently padded poses, no ghost overlap, clipping bounds, wrong-alpha cutouts, no early answer highlighting and a scene with an episode ID other than EP02 to prevent episode-hardcoded contracts.

Acceptance: deterministic plan with one background, both hosts, at least one pose swap and one red flower; no walking-cycle choreography or artwork derivatives.

### 5. Assemble through the actual editor and reopen

Files: `src/mk_studio/editors/palmier.py`, `tests/test_editor_reconciliation.py`, `scratch/ch01-proof/evidence/`.

- [ ] `PalmierAdapter.discover()` captures schemas/server info each run; `read_state()` verifies scratch project path and timeline; `apply_plan(plan, ledger)` reconciles logical clips to returned IDs. Validate exact current schemas, including exclusive ranges and explicit track indexes for layered placement.
- [ ] Open/bind only the verified scratch project. Set canvas before assembly, import individual existing files by path, create tracks and add clips; apply static properties and clip-relative keyframes. Never mutate the currently open `TestAIProducer` project merely because it is active.
- [ ] Save via supported lifecycle, capture readback, close/reopen the scratch project, and compare semantic ranges/media/keyframe tracks. Readback may omit constant/identity tracks; compare animated meaning, not raw JSON formatting.
- [ ] Run the identical plan again: unchanged owned clip count, zero duplicate media/clip additions and zero unneeded mutation calls. Repeat after a controlled interruption around an editor step; reconcile live state before continuing.

Acceptance: actual reopened editable project retains animation and external source references. Mock responses may test failure handling, but cannot pass the editor proof.

### 6. Export and inspect completed output

Files: `src/mk_studio/qa.py`, `scratch/ch01-proof/E02_CH01_Technical_Proof_1080p30_v001.mp4`, `scratch/ch01-proof/proof-report.md`.

- [ ] Submit video export to an explicit scratch path with `overwrite=false`, H.264 and matching 1080p timeline. Persist job ID; poll to a terminal success status and inspect warnings. Submission alone is not completion.
- [ ] Run ffprobe with frame counting: expect 1920x1080, 30/1 fps, 907 decoded video frames and stereo 48 kHz audio. Decode the full file with FFmpeg (`-v error -i ... -map 0:v:0 -map 0:a:0 -f null -`). Inspect audio start/end/encoder padding separately; preserve all 30.22 seconds of source content and explain any codec padding.
- [ ] Inspect decoded first/last frames; before/at/after each hard swap and reveal; motion extrema and between-key frames. Quantify visible body motion (at least 3 pixels at a non-anchor landmark in the calibration), while intended support-foot contact remains within a proposed 2-pixel tolerance at 1080p, including interpolated frames. If that tolerance fails, report and adjust the motion, not the artwork.
- [ ] Review continuous playback for visual motion/grounding and hard cuts. Do not label sampled frames or ASR as full listening. Produce owner review evidence; technical pass and creative/audio approval remain separate.
- [ ] Rehash all referenced inputs and compare with the before snapshot. Save exact commands, versions, receipts, samples, timing caveats and rerun counts; update task/validation/handoff.

Acceptance: all requested technical proof criteria pass with actual native project and decoded MP4 evidence. A flattened FFmpeg preview cannot substitute. Failed criteria remain open.

## Subsequent increments (separate from M0 approval)

- **M1 — reusable adoption and audio workflow:** script parsing covering CH07 quoted lyrics and CH08/09 exact-speech sections; stable explicit imports; catalogue/version history; reviewed production-copy executor; Manus importer and validated Palmier transcription if proven; sample-accurate edit graphs, matched stems, bounded retries/cancel/restart. Test the four CH02 takes stay distinct and changing source/script invalidates only affected downstream work.
- **M2 — one production-ready chapter and review interface:** after M0, small loopback UI sharing CLI services, registered artifact serving, origin/token protection, audio A/B/join audition, exact-revision approval and cue uncertainty resolution. CH01 final audio/cues must be reviewed before a production chapter is declared ready. Preserve existing creative approvals.
- **M3 — EP02 CH01–CH09:** add the six reusable layout families as needed, supplied chapter audio and verified joins; full watch/listen, technical QA, native self-contained package reopened with references verified inside the backup. FCPXML optional interchange only. Continue sequentially; no concurrency infrastructure assumed.
- **Later:** episode planning/authoring adapters, optional supported image providers with budget controls, richer UI, Windows/Resolve/extension routes, Shorts/localisation. Preserve interfaces now, implement when separately scoped.

## Review focus and stop conditions

Covered explicitly above: path escape or wrong active project (tasks 1/5); mixed WAV+cover-art streams (3); changed audio/edit timebase (3); unknown-outcome retry and DB blocking (2/5); interpolation drift between correct keys (4/6). Technical capability blocks stop only the dependent step, with a precise record. Existing audio approval is not fabricated to unblock production.

Plan approval authorizes the M0 work described here; it does **not** authorize execution of the production reconciliation table. Exact mappings remain separately reviewable. No delivery-date, runtime or API-cost guarantee is made.
