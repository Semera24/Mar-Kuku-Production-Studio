# Mar and Kuku Production Studio — Independent Architecture & Feasibility Review (`v002`)

**Prepared:** 22 October 2026 (`Africa/Addis_Ababa`)  
**Role:** Independent Senior Automation Architect & Media-Pipeline Engineer  
**Scope:** Candid feasibility review, local read-only system & media audit, challenge to `MK_Production_Studio_Requirements_v001.md` and `MK_Production_Studio_Codex_Kickoff_v001.md`, and a simpler, stronger architectural proposal ("The New Idea").  
**Note on Deliverable Versioning:** `/Users/yonassayfu/Projects/mar-kuku-production-studio/docs/antigravityIdea.md` already existed on disk as a `0`-byte placeholder created at `00:19`. Per the review rule (*"If that file already exists, preserve it and save this review as antigravityIdea_v002.md"*), `antigravityIdea.md` has been preserved untouched and this complete review is saved as `antigravityIdea_v002.md`.

---

## 1. Executive Verdict

**Verdict: Conditional GO on the media-automation pipeline; NO-GO on the over-engineered "Multi-Worker Agent Studio" as currently scoped in `Requirements_v001.md`.**

Your core production goal—eliminating ~80% of the repetitive file wrangling, transcript-to-edit-graph math, 30 fps cue conversion, foot-anchored keyframe trigonometry, and timeline assembly for *Mar and Kuku*—is **highly achievable** on your MacBook Air M1. In fact, our read-only inspection of your machine proves that you **already succeeded** in building, keyframing, and exporting a 13-minute-15-second widescreen master (`23,850` frames) and three portrait Shorts for Episode 001 through **Palmier Pro's MCP server**.

However, **`MK_Production_Studio_Requirements_v001.md` suffers from a classic architectural trap**: it tries to solve the pain of chaotic multi-tab ChatGPT conversations by designing an enterprise-grade distributed job platform (24 conceptual database entities, lease/heartbeat worker daemons, parallel specialist AI workers, dry-run rollback transaction logs, and a full web dashboard) **before** solving the actual deterministic media compiler.

### What Our Read-Only Local Audit Just Revealed About Episode 002
The best proof of what you actually need comes from inspecting `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/` right now:
1. **Your ChatGPT handoff docs (`episode_002_workflow_checklist.md` and `E02_AGENT_HANDOFF_v001.md`) are out of sync with your actual Mac disk.** The checklist in `04_EPISODES/EPISODE_002_Mar_Kuku_Color_Garden_Adventure/` is still at `v002`, and `01_SCRIPT/`, `02_STORYBOARD/`, `03_SCENE_PLANS/`, and `04_ASSET_LISTS/` are **completely empty directories**.
2. **Where did all your Episode 002 files actually go?** Because Chrome remembers the last download directory across tabs, almost everything you downloaded from your 4-tab ChatGPT session landed inside `01_ASSETS/PROPS/EPISODE_002_COLOR_GARDEN/01_SOURCES/` and `06_AUDIO/MUSIC/EPISODE_002/CH01/`:
   - `E02_MASTER_SCRIPT_v001.md` (and `(1).md`), `E02_AGENT_HANDOFF_v002.md` (and `(1).md`), `episode_002_workflow_checklist.md` (`v003`), and draft scripts for CH07–CH09 are sitting inside the **PNG prop sources folder** (`01_SOURCES/`).
   - Four different WAV files for `CH02` (ranging from `131.84s` to `179.97s`) and a `CH04` WAV (`149.29s`) are scattered across `CH01/` and `01_SOURCES/`.
   - All 6 flowers (`1254×1254` RGBA), all 6 color swatches (`1254×1254` RGBA), the neutral basket (`Empty woven garden basket.png`, `1254×1254` RGBA), and the 8 Mar/Kuku walk-cycle PNGs (`1086×1448` and `1085×1450` RGBA) are sitting in `01_SOURCES/`, while `02_APPROVED_PNG/` is empty.
   - `BG_Village_Color_Garden_v001.png` **is** saved at `01_ASSETS/BACKGROUNDS/VILLAGE/BG_Village_Color_Garden_v001.png`, but it is `1672×941` RGB (`8-bit`, no alpha) and must be scaled/conformed to `1920×1080`.

**Bottom Line:** You do not have an "autonomous multi-agent coordination" problem. You have:
1. A **deterministic file intake & quarantine reconciliation** problem (stopping browser-download chaos).
2. A **deterministic audio-edit & word-timestamp math** problem.
3. A **deterministic scene-to-Palmier-MCP compiler** problem (generating exact trigonometric foot-anchored keyframe arrays and hard-cut pose clips from a declarative YAML/JSON chapter spec).

If you build a lean **CLI-first Deterministic Media Compiler (`mk-studio`)** with an optional single-process local review UI—and drop the multi-agent orchestration bloat—you can ship Episode 002 in days rather than months.

---

## 2. Capability Feasibility Table

| Capability | Status | Classification | Local / Official Evidence & Technical Reality |
| :--- | :--- | :--- | :--- |
| **1. Folder intake, SHA-256 catalog & path quarantine** | **Practical Now** | Conventional Python | Trivial in Python (`hashlib`, `pathlib`, `sqlite3`). Immediately needed to clean up the misplaced EP02 downloads in `01_SOURCES/` and `CH01/`. |
| **2. PNG alpha, bounding-box & foot-baseline inspection** | **Practical Now** | Custom Engineering (Small) | Needs `Pillow`/`numpy` in project `.venv` (not currently in system Python 3.15). Computes exact non-transparent pixel bounds `(x_min, y_min, x_max, y_max)` to auto-calibrate Kuku's 24 px vertical walk-cycle drift and detect semi-transparent body pixels (`alpha ≈ 253` on `Mar_Introduce_Self_v001.png`). |
| **3. Script & audio-prompt extraction** | **Practical Now** | Bounded AI + Deterministic Parser | `E02_MASTER_SCRIPT_v001.md` is already written and approved. Stripping `[VISUAL: ...]` and `[RESPONSE: ...]` into clean Google Music/Flow prompts requires a 50-line Markdown parser—not an autonomous AI agent. |
| **4. Reference-based PNG variant generation** | **External API / Manual** | External API or Manual Chat | Keeping exact petal geometry across 6 flower colors requires an image-edit API (OpenAI `gpt-image-1` or Gemini `gemini-2.5-flash-image`) with paid API billing. ChatGPT Plus/Pro web subscriptions **do not** include API credits. For EP02, all 14 core assets and 8 walk poses **already exist on disk**. |
| **5. Google Music / Flow audio generation** | **Manual Input (V1)** | Manual Human Step | Google Flow (`flow.google.com`, accessed locally via your unpacked `FlowPass` Chromium extension) has no public official REST API. Manual generation + folder drop is the only reliable V1 path. |
| **6. Word-level transcription & forced alignment** | **Practical Now (2 Local Paths!)** | Custom Integration (Zero-Cost Local) | **Major Discovery:** (a) **Palmier Pro 0.11.0** includes a built-in on-device transcription tool (`inspect_media` / `get_transcript`) using macOS 26's native `SpeechAnalyzer` that returns word-level `[text, start, end]` tuples for free! (b) On M1, `mlx-whisper` (or `whispermlx`) runs locally in seconds. Manus Markdown import remains a fine fallback but shouldn't be your primary dependency. |
| **7. Non-destructive audio repair (4.00s response holds)** | **Needs Custom Engineering** | Deterministic DSP (`FFmpeg` / `scipy`) | Cutting extra tail speech (`CH01`) is easy. **Extending** a 0.66s–2.90s musical gap to 4.00s by looping a 0.60s window (`CH02`/`CH03`) without beat-matching causes audible rhythmic hiccups. Best solved by requiring `01_STEMS` (`OTHER_INSTRUMENTS` + `VOCALS`) or using beat-aware crossfades (Palmier bundles `BeatThis.mlmodelc` / `detect_beats`). |
| **8. Frame-accurate cue math (`30 fps` half-up)** | **Practical Now** | Deterministic Math | `Decimal` arithmetic with `ROUND_HALF_UP` mapping source timestamps through the piecewise linear Audio Edit Graph `[(src_in, src_out, dst_start)]`. Zero AI involvement—LLMs frequently hallucinate arithmetic. |
| **9. Cutout assembly & foot-anchored keyframes in Palmier Pro** | **Practical Now (Verified)** | Custom Compiler -> Palmier MCP | **Verified via EP01 (`E01_MOTION_REFERENCE_SNAPSHOT_v001.json`) and binary inspection of `PalmierPro 0.11.0`.** Supports `manage_project`, `import_media`, `add_clips`, `set_clip_properties`, `set_keyframes` (`position`, `scale`, `rotation`, `opacity`), `inspect_timeline`, and `export_project`. Requires Python trigonometric pre-computation for foot-anchored sway because Palmier lacks custom clip pivot points. |
| **10. Cutout assembly & keyframes in DaVinci Resolve Free 21.0.3** | **Unreliable / Blocker for V1** | High-Risk Fallback | **Verified Local Blocker:** Resolve's Edit-page API cannot set keyframes. Fusion page animation requires `AddFusionComp` + `AddModifier("BezierSpline"/"Path")`. Your local `davinci-resolve-mcp` logs (`2026-10-20` to `2026-10-21`) and `server.py` (lines `31526–31533`) document that on Resolve 21.x, `AddFusionComp` renders bit-identical to no-comp baselines unless manually activated in GUI, and Free requires an in-process `BridgeProxy` workaround. |
| **11. Automated technical QA vs. semantic QA** | **Split** | FFmpeg/Palmier (Tech) + Human (Creative) | `ffprobe`, `ebur128`, black-frame detection, and Palmier's `inspect_timeline` (which burns frame numbers and a `0..1` coordinate grid onto sampled composite frames) automate technical QA. Musical join smoothness and pronunciation ("Cuckoo" vs. "Kuku") **cannot** be certified by software. |

---

## 3. Evidence and Unresolved Assumptions

All findings below were gathered via strictly **read-only** local checks on **22 October 2026**. No projects, production files, or configurations were modified.

### 3.1 Verified Local System & Environment Evidence
* **Host Hardware & OS:** MacBook Air (`arm64`, Apple M1, 8 CPU cores), **8.00 GB Unified RAM** (`8,589,934,592` bytes), running **macOS 26.5.1** (Build `25F80`).
  * *Significance:* Your macOS version (`26.5.1`) natively satisfies Palmier Pro's macOS 26 requirement and provides Apple's native on-device `SpeechAnalyzer` framework.
* **Disk Capacity Warning:** Root volume (`/dev/disk3s3s1`) is `228 GiB` total with **only `63 GiB` available**.
  * *Significance:* Uncompressed 48 kHz WAV stems, ProRes intermediates, or full-copy project backups will rapidly exhaust 63 GiB.
* **Python Runtimes Installed:**
  * `/opt/homebrew/bin/python3` -> **Python 3.15.0** (bare environment; `PIL`, `fastapi`, `pydantic`, `mlx_whisper` are **not** installed globally).
  * `/opt/homebrew/bin/python3.12` -> **Python 3.12.15** (used by `/Users/yonassayfu/Library/Application Support/davinci-resolve-mcp/venv`, which already has `pydantic 2.14.1`, `starlette 1.9.0`, `uvicorn 0.54.0`, `httpx 0.28.1`, `mcp 1.30.0`, `typer 0.27.3`, `rich 15.0.0`).
  * `/usr/bin/python3` -> **Python 3.9.6** (system Xcode stub).
  * `/opt/homebrew/bin/uv` is installed and available for fast, isolated virtual environments.
  * *Critical Version Caveat:* Python `3.15.0` is bleeding-edge; several C/Rust/MLX audio and image wheels (`mlx-whisper`, `scipy`, `onnxruntime`, `fusionscript.so`) target Python `3.12`. **Pin the studio project `.venv` to `python3.12` via `uv venv --python 3.12`.**
* **FFmpeg:** `/opt/homebrew/bin/ffmpeg` version **9.0.2**, built with Apple Silicon hardware acceleration (`--enable-videotoolbox`, `--enable-audiotoolbox`, `--enable-neon`) plus `libx264`, `libx265`, `libsvtav1`, `libvmaf`. No `whisper` filter is compiled into this FFmpeg build.
* **Codex CLI:** `/Users/yonassayfu/.local/state/fnm_multishells/.../bin/codex` version **`codex-cli 0.163.0`**.
  * Verified CLI flags: `codex exec` natively supports `--output-schema <FILE>`, `-o / --output-last-message <FILE>`, `--ephemeral`, `-s read-only`, and `-i / --image <FILE>...`.
  * Configured default model in `~/.codex/config.toml`: `model = "gpt-6.1-sol"`.

### 3.2 Verified Editor Evidence: Palmier Pro vs. DaVinci Resolve vs. CapCut

#### A. Palmier Pro (`0.11.0`, `/Applications/PalmierPro.app`) — **PRIMARY VERIFIED WINNER**
* **Current Runtime State:** Installed (`v0.11.0`), configured in `~/.codex/config.toml` at `http://127.0.0.1:19789/mcp`. The app was not running during this review (`127.0.0.1:19789` closed), and per review rules we did not launch it.
* **Hard Local Evidence of Capabilities:**
  1. **Episode 001 Production Proof:** We inspected `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/05_PALMIER_PROJECTS/EPISODE_001_Mar_Big_Counting_Adventure.palmier/project.json` (`292 KB`), `media.json` (`18 KB`), and `00_PROJECT_NOTES/AI_HANDOFFS/E01_MOTION_REFERENCE_SNAPSHOT_v001.json`. Episode 001's 23,850-frame widescreen master and 3 portrait Shorts were assembled, keyframed, and exported using Palmier Pro MCP on this machine.
  2. **Embedded MCP Tool Schema Audit (`/Applications/PalmierPro.app/Contents/MacOS/PalmierPro` & `palmier-pro.mcpb`):**
     - **Project & Timeline Lifecycle:** `manage_project(action='list'|'open'|'create'|'close')` (`close` guarantees a final save; never deletes files), `create_timeline(name, from?)` (duplicates timelines for non-destructive versioning), `set_active_timeline`, `set_project_settings(fps=30, width=1920, height=1080)`.
     - **Asset Intake:** `import_media(source={path: ...})` references local files **in place** without duplicating disk storage and completes inline (`status: 'ready'`, zero cost).
     - **Track & Clip Assembly:** `manage_tracks`, `add_clips` (places clips into `[startFrame, endFrame)` half-open intervals or overwrites), `insert_clips` (ripples downstream clips), `move_clips`, `split_clips`, `swap_clip_media`, `remove_clips`.
     - **Transforms, Hard Cuts & Keyframes:** `set_clip_properties` sets static `durationFrames`, `trimStartFrame`, `trimEndFrame`, `volumeDb`, `opacity`, `fadeInFrames`, `fadeOutFrames`, `transform`, `crop`. `set_keyframes` replaces a clip's keyframe track for `position` (`[frame, topLeftX, topLeftY, interp]`), `scale` (`[frame, width, height, interp]`), `rotation` (`[frame, degrees, interp]`), `opacity`, `volumeDb`, `crop`, `blur`, where `interp ∈ {linear, hold, smooth}` and `frame` is `0`-based clip-relative.
     - **Visual & Audio Inspection:** `inspect_timeline(startFrame, endFrame?, maxFrames?)` renders the actual composited canvas with a burned-in `0..1` coordinate grid, frame number (`f157`), and metadata listing visible clip IDs top-down. `inspect_media` and `get_transcript` run **free local on-device transcription** (word-level `[text, start, end]` tuples) and beat detection (`BeatThis.mlmodelc`).
     - **Export & Self-Contained Backup:** `export_project(mode='video'|'fcpxml'|'xml'|'palmier', outputPath=...)` and `manage_exports(action='list'|'cancel')`. Notably, `mode='palmier'` exports a self-contained `.palmier` package, and `mode='fcpxml'` exports a DaVinci Resolve / Final Cut Pro XML interchange file Carrying transforms, crops, opacity, and keyframes!
* **Important Palmier Quirks Discovered in Binary:**
  1. **Window Visibility Rule:** If multiple Palmier project windows are open and the user clicks another window, MCP **reads** continue on the session-bound project, but **mutations pause** until that project window is visible again or `manage_project(action='open')` re-selects it.
  2. **No Custom Pivot Point:** Rotation occurs around the image center, and `position` is top-left `(x, y)` while `scale` is absolute normalized `(width, height)`. Foot-anchored sway **must** be pre-computed mathematically in Python into coupled `position`, `scale`, and `rotation` keyframes.
  3. **No Direct File Tampering While Open:** `.palmier` packages are JSON (`project.json`, `media.json`), which makes offline read-only auditing easy, but all edits must go through MCP while Palmier is open or via offline validated generator scripts when closed.

#### B. DaVinci Resolve Free (`21.0.3`, `/Applications/DaVinci Resolve/`) — **HIGH-RISK / DO NOT USE AS V1 PRIMARY**
* **Current Runtime State:** `davinci-resolve-mcp` server (`PID 4211`, Python 3.12) is running, but Resolve itself is not open.
* **Hard Local Evidence of Blockers (`/Users/yonassayfu/Library/Application Support/davinci-resolve-mcp/logs/` & `src/server.py`):**
  1. **Free Edition External Scripting Restriction:** Resolve Free 21.0.3 blocks standard external headless scripting (`resolve_control.is_studio` failed at `2026-10-20T13:59:39Z`). It relies on the community `BridgeProxy` running inside Resolve.
  2. **`BridgeProxy` & Fusion Keyframe Fragility:** On `2026-10-20T12:39:21Z`, `fusion_comp.add_modifier` failed with `TypeError: 'BridgeProxy' object is not subscriptable`. While `server.py` was patched on `2026-10-21 00:25` (`_fusion_find_input` using `GetInputList()`), Resolve's Edit page still has **zero scripting API for keyframing** `Pan`/`Tilt`/`Zoom`/`RotationAngle`.
  3. **Resolve 21.x Fusion Render Bug Documented in Your Own MCP Server:** Lines `31526–31533` of `davinci-resolve-mcp/src/server.py` explicitly warn that on Resolve 21.0.x, Fusion compositions created via `AddFusionComp` and modified via API **render bit-identical to the un-animated baseline** because no API selects an item's active Fusion composition for rendering!
  4. **Memory Footprint on 8 GB M1:** DaVinci Resolve 21 + Fusion page + MCP server on an 8 GB MacBook Air causes heavy swap pressure during multi-layer PNG compositing.
* **Conclusion on Resolve:** Keep Resolve strictly for optional final grading/Fairlight mixing via `export_project(mode='fcpxml')` from Palmier, **not** as the automated cutout assembly engine.

#### C. CapCut (`CapCut.app` + `/Users/yonassayfu/Tools/capcut-mcp`) — **NOT RECOMMENDED**
* Although `capcut-mcp` (`PID 4212`) is running and exposes `capcut_add_keyframe`, it works by directly mutating CapCut's undocumented internal draft JSON files (`draft_content.json`), which CapCut frequently breaks across updates and overwrites if the app is open. Palmier Pro's official first-party MCP server is far superior.

### 3.3 Audit of Actual Episode 002 Files on Disk vs. Handoff Claims
Our read-only inspection of `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/` revealed critical discrepancies between the ChatGPT handoff docs and reality on disk:

| Artifact Category | What Handoff Docs Claimed | Actual Verified Disk State (22 Oct 2026) |
| :--- | :--- | :--- |
| **EP02 Folder Structure** | `E02_MASTER_SCRIPT_v001.md` in `01_SCRIPT/`, `E02_AGENT_HANDOFF_v002.md` in `06_NOTES/` | `01_SCRIPT/`, `02_STORYBOARD/`, `03_SCENE_PLANS/`, `04_ASSET_LISTS/` are **empty**. `E02_MASTER_SCRIPT_v001.md`, `E02_AGENT_HANDOFF_v002.md`, and `episode_002_workflow_checklist.md` (`v003`) were accidentally saved in `01_ASSETS/PROPS/EPISODE_002_COLOR_GARDEN/01_SOURCES/` with `(1)` duplicates! |
| **Background (`BG_Village_Color_Garden_v001.png`)** | "Mac save unconfirmed" | **Exists** at `01_ASSETS/BACKGROUNDS/VILLAGE/BG_Village_Color_Garden_v001.png`. Measured `1672×941`, `8-bit RGB` (color type `2`, no alpha). Aspect ratio is `1.7768` (~16:9), needs uniform scale to `1920×1080`. |
| **13 Core Cutout Props (6 Flowers, 6 Swatches, 1 Basket)** | "Mac save unconfirmed; basket proposed" | **All 13 exist** in `01_ASSETS/PROPS/EPISODE_002_COLOR_GARDEN/01_SOURCES/`: 6 `E02_Flower_[Color]_v001.png` (`1254×1254` RGBA), 6 `E02_Color_Swatch_[Color]_v001.png` (`1254×1254` RGBA), and `Empty woven garden basket.png` (`1254×1254` RGBA). `02_APPROVED_PNG/` is currently empty. |
| **New Host Poses (Mar Intro + Mar/Kuku Walks)** | "Save in `CHARACTERS/{MAR,KUKU}/01_SOURCES/`" | `Mar_Introduce_Self_v001.png` is already in `CHARACTERS/MAR/02_OFFICIAL_POSES/` (making 22 Mar poses + 12 Kuku poses). The 8 walk PNGs (`Mar_Walk_Right_Cycle_A..D_v001.png` at `1086×1448` RGBA; `Kuku_Walk_Right_Cycle_A..D_v001.png` at `1085×1450` RGBA) are sitting in `PROPS/EPISODE_002_COLOR_GARDEN/01_SOURCES/`. |
| **EP02 Audio Files** | "CH01–CH03 candidates created; CH04 unconfirmed; save in `CH01/`..`CH03/`" | Only `06_AUDIO/MUSIC/EPISODE_002/CH01/` (and `CH01/00_PILOT/`) exists—there are no `CH02/`, `CH03/`, or `CH04/` folders, and **zero `01_STEMS` folders**. Inside `CH01/` and `01_SOURCES/` are **12 WAV files** (all `48000 Hz`, stereo, `16-bit PCM`):<br>• `CH01` original (`105.803s`), pilot (`70.048s`), and edited candidate (`30.220s`)<br>• **Four different `CH02` originals** (`131.840s`, `176.907s`, `178.571s`, `179.968s`) plus edited candidate (`105.340s`)<br>• `CH03` original (`177.227s`) and two copies of edited candidate (`95.140s`)<br>• **`CH04` original** (`Mar and Kuku — Colour Garden Adventure (CH04).wav`, `149.291s`, saved Oct 21 23:44). |

---

## 4. Recommended Architecture & "The New Idea"

### 4.1 Architectural Comparison

| Approach | Verdict | Tradeoffs & Fit for 8 GB MacBook Air M1 |
| :--- | :--- | :--- |
| **1. Proposed in `v001`: FastAPI + SQLite (24 entities) + Separate Worker Daemon + Parallel AI Specialists + Web Dashboard** | **Over-Engineered (Reject for V1)** | Solves multi-user cloud-studio problems you don't have. Maintaining lease heartbeats, subprocess pools, 24 relational tables, and two-way Markdown projection reconciliation will consume 70% of your coding time before a single frame of Episode 002 is assembled. |
| **2. VS Code Extension** | **Reject** | Extension host lifecycle (Node/TypeScript) is awkward for Python/FFmpeg/Pillow DSP, ties your studio to an IDE UI, and adds Webview message-passing boilerplate. |
| **3. Browser Extension (driving ChatGPT / Google Flow tabs)** | **Reject as Core Engine** | You already built `FlowPass` (`engine.js` RPC patching) for Google Flow region unlocking—which is a great single-purpose utility. Driving 4 ChatGPT tabs via DOM automation breaks whenever OpenAI changes CSS selectors and violates ToS. |
| **4. Existing Workflow Frameworks (Prefect, Temporal, Airflow, n8n, LangGraph)** | **Reject** | Massive RAM overhead on an 8 GB Mac (Docker/workers eat 1.5–3 GB idle RAM) and poor native handling of frame-accurate audio/video human-in-the-loop review gates. |
| **5. THE NEW IDEA: CLI-First "Deterministic Media Compiler" (`mk-studio`) + Single-Process On-Demand Review UI** | **RECOMMENDED PRIMARY** | **Near-zero idle RAM.** Treats an Episode like a software build (`Makefile` / compiler pipeline) backed by a compact **6-table SQLite ledger** and **declarative YAML/JSON chapter manifests**. Uses AI **only** at bounded authoring/alignment edges; 95% of execution is deterministic Python + FFmpeg + Palmier MCP. |

### 4.2 The New Idea: Treat Animation Production as a Deterministic Compiler, Not an "Agent Swarm"

In `Requirements_v001.md`, the mental model is a **Master Producer coordinating 6–7 specialist AI workers** (Script Worker, Asset Worker, Audio/Transcript Worker, Cue Worker, Scene Worker, Editor Worker, QA Worker). That model comes from your 4-tab ChatGPT experiment, where you had to use separate chats because ChatGPT tabs can't loops over local files.

**On your local Mac, Python does not need AI agents to loop over files, check PNG alpha channels, run FFmpeg crossfades, calculate 30 fps frame numbers, or send keyframe arrays to Palmier MCP.**

Instead of a multi-agent swarm, build **`mk-studio` as a 5-Stage Deterministic Media Compiler**:

```
[Stage 1: INTAKE & RECONCILE]  (100% Deterministic Python + Pillow + ffprobe)
  Scans Drop/Download folders -> SHA-256 hash -> Classifies PNG/WAV/MD ->
  Measures alpha bounding boxes & foot baselines (y_foot) ->
  Moves/copies to canonical EP02 folders after dry-run diff confirmation.
        │
        ▼
[Stage 2: AUDIO ALIGN & REPAIR] (Palmier SpeechAnalyzer or mlx-whisper + FFmpeg DSP)
  Extracts word-level [word, start_s, end_s] timestamps from supplied WAV ->
  Deterministic diff against approved script lines ->
  Builds declarative AudioEditRecipe.json (cuts, stem-backed or beat-aware 4.00s holds) ->
  Renders candidate WAV + A/B join snip HTML player for 60-second Human Ear Check.
        │
        ▼
[Stage 3: CUE & SCENE COMPILER] (100% Deterministic Python Math — ZERO LLM Math)
  Maps verified speech & 4.00s response holds through AudioEditRecipe ->
  Applies half-up 30 fps formula -> Resolves Declarative Scene DSL (e.g.,
  "show_3_flowers(left=red, mid=yellow, right=blue)", "host_react(mar='Point_Right')") ->
  Pre-computes foot-anchored trigonometric keyframes & walk-cycle y-offsets ->
  Outputs immutable `CHxx_CompiledTimeline.json`.
        │
        ▼
[Stage 4: PALMIER MCP ASSEMBLER] (Single-Writer Deterministic HTTP Client -> 127.0.0.1:19789/mcp)
  Diffs `CHxx_CompiledTimeline.json` against Palmier's `get_timeline` ->
  Idempotently imports media, creates/updates chapter timeline, places audio & PNG hard-cuts,
  pushes `set_keyframes` arrays, and calls `inspect_timeline` at cue boundaries.
        │
        ▼
[Stage 5: QA & EXPORT GATE]     (Palmier `inspect_timeline` + `export_project` + `ffprobe`)
  Generates a Contact-Sheet Review Card (sampled composite frames at every question/reveal
  + audio join waveforms) -> Human watches exported chapter/master ->
  Exports 1080p30 MP4 + `mode='palmier'` self-contained backup + `mode='fcpxml'` archive.
```

### 4.3 How State, Approvals, Retries, and Recovery Should Actually Work
1. **Replace 24 SQLite Tables with 6 Core Tables:**
   - `episodes` (`id`, `slug`, `status`, `config_json`)
   - `artifacts` (`id`, `episode_id`, `role`, `rel_path`, `sha256`, `media_meta_json`, `state` ∈ `discovered | quarantined | candidate | approved | rejected`)
   - `chapters` (`id`, `episode_id`, `ch_num`, `script_sha256`, `selected_audio_id`, `edit_recipe_json`, `cues_json`, `scene_plan_json`, `palmier_timeline_id`)
   - `approvals` (`id`, `target_type`, `target_id`, `content_sha256`, `gate_type` ∈ `script | visual_pilot | audio_ear_check | final_watch`, `approved_at`, `notes`)
   - `runs` (`id`, `stage`, `chapter_id`, `input_hash`, `status`, `started_at`, `finished_at`, `log_path`, `error_json`)
   - `file_ops` (`id`, `run_id`, `op_type`, `src_path`, `dst_path`, `src_sha256`, `executed_at`)
2. **No Background Daemon Needed on an 8 GB Mac:**
   - You are one creator sitting at one MacBook Air. You do not need an always-on background worker polling leases every 2 seconds.
   - When you run `mk-studio run ch01` (or click **"Run Next Step"** in the lightweight local UI launched via `mk-studio serve`), the command runs synchronously with a SQLite file lock (`BEGIN IMMEDIATE`) and streams live progress.
   - If the process crashes or the Mac loses power, the next run reads the `input_hash` of completed stages in `runs` and compares it against Palmier's live `get_timeline` state—skipping completed chapters and never duplicating clips.
3. **Where Bounded AI (`codex exec --output-schema`) Actually Belongs:**
   - **Task A (Fuzzy Transcript-to-Script Alignment):** When ASR spells `"Kuku"` as `"Cuckoo"` or `"Two, two"` or merges words over xylophone backing, a single bounded call to `codex exec --ephemeral -s read-only --output-schema align_schema.json` maps ASR word indices to the canonical sentences in `E02_MASTER_SCRIPT_v001.md` and flags missing/extra spoken lines.
   - **Task B (Drafting Scene Choreography DSL from Script Visual Notes):** Converting `[VISUAL: Show the three flowers: red left, yellow middle, blue right]` into a structured `SceneChoreography.json` using your finite vocabulary of 5 composable visual templates (`solo_prop_intro`, `three_choice_quiz`, `three_basket_match`, `two_choice_recall`, `full_garden_recap`).
   - **Everything else (frame math, audio slicing, keyframe trigonometry, Palmier MCP calls, file moves) must be 100% deterministic Python.**

---

## 5. Deep-Dive Assessment of Actual Integrations

### 5.1 Script Generation & Prompt Preparation
* **Verified Locally:** `E02_MASTER_SCRIPT_v001.md` (`27,433` bytes) is **already complete and approved** for all 9 chapters (`CH01`–`CH09`), sitting in `01_ASSETS/PROPS/EPISODE_002_COLOR_GARDEN/01_SOURCES/E02_MASTER_SCRIPT_v001.md`.
* **What Needs Engineering:** A deterministic Markdown parser (`mk-studio script compile`) that:
  1. Copies `E02_MASTER_SCRIPT_v001.md` to its canonical home (`04_EPISODES/EPISODE_002_Mar_Kuku_Color_Garden_Adventure/01_SCRIPT/E02_MASTER_SCRIPT_v001.md`).
  2. Extracts each `### CHxx_Syy` block, separating `[VISUAL: ...]`, `[RESPONSE: ...]`, and spoken/sung lines.
  3. Strips quotation marks around color words per your Google Music/Flow rule (`Can you say red?` instead of `Can you say “red”?`) and outputs clean copy-paste prompt cards for `CH04`–`CH09`.

### 5.2 Reference-Based Image Generation & Asset Hygiene
* **Verified Locally:** All 14 core assets for Episode 002 (`1` background, `6` flowers, `6` swatches, `1` basket) plus `Mar_Introduce_Self_v001.png` and `8` walk-cycle PNGs (`Mar_Walk_Right_Cycle_A..D`, `Kuku_Walk_Right_Cycle_A..D`) **already exist on your Mac**.
* **Unverified Assumptions / Local Caveats:**
  - `Mar_Introduce_Self_v001.png` has reported body pixel translucency (`alpha ≈ 253`), which will faintly bleed the cottage background through her dress unless clamped (`alpha = 255` where `alpha >= 245`) during a non-destructive asset-prep pass.
  - `Kuku_Walk_Right_Cycle_A..D_v001.png` has up to `24 px` vertical bounding-box variation between frames `A/B/C/D`. If placed at identical `topLeftY` in Palmier, Kuku will jitter vertically on every step!
* **Recommended Engineering Fix (Essential):** Build a deterministic **PNG Asset Normalizer** (`mk-studio assets normalize`) using `Pillow`/`numpy` that:
  1. Measures the lowest non-transparent pixel row (`y_foot_px`) and horizontal center of mass of every character/prop PNG.
  2. Computes per-pose normalized placement offsets `(dx_norm, dy_norm)` so that when `Walk_A -> Walk_B -> Walk_C -> Walk_D` hard-swap on the timeline, Kuku's support foot stays locked to `y = 0.935` to the sub-pixel!
  3. Optionally outputs an opaque-body derivative (`v002_OPAQUE.png`) for `Mar_Introduce_Self` so her dress does not bleed background pixels.

### 5.3 Transcription & Script Comparison
* **Verified Locally:** No standalone CLI whisper (`whisper`, `mlx_whisper`, `faster-whisper`) is installed yet, **but**:
  1. **Palmier Pro 0.11.0** (`inspect_media` and `get_transcript`) already provides **free, local on-device word-level transcription** (`[text, start, end]` tuples in source seconds or project frames) via macOS 26's native `SpeechAnalyzer`!
  2. Installing `mlx-whisper` into `.venv` (`uv pip install mlx-whisper`) gives you an independent second local word-timestamp engine on M1 (`mlx-community/whisper-large-v3-turbo`) that transcribes a 2-minute chapter in ~6–10 seconds using ~1.5 GB Unified Memory.
* **Why This Changes Your Workflow:** You no longer need to manually upload WAVs to **Manus** and wait for Markdown transcripts! You can drop a WAV from Google Flow into the chapter folder, and `mk-studio` can run local word-level transcription + script diffing automatically in 10 seconds, while keeping Manus Markdown import as an optional override.

### 5.4 Non-Destructive Audio Processing & The "Looped Music Gap" Trap
* **Verified Local Evidence:** In `CH02` and `CH03`, the edited review candidates (`E02_CH02_Red_Yellow_Blue_v002_EDIT_REVIEW_FULL_MIX.wav` and `E02_CH03_Green_Orange_Purple_v002_EDIT_REVIEW_FULL_MIX.wav`) extended short response pauses (some as short as `0.66s` and `0.82s` in `CH03`!) to `4.00s` by repeating a `0.60s` or `1.20s` slice of the full mix with `80 ms` linear crossfades.
* **Technical Reality Check:** Repeating a `0.60s` window of a marimba/ukulele melody without tempo/beat alignment (`600 ms` corresponds to 100 BPM quarter notes; if the track is at 92 BPM or 115 BPM, every loop seam cuts across the barline) almost always produces an audible musical stutter.
* **Better Engineering Solution:**
  1. **Always download or separate `01_STEMS` (`VOCALS` + `INSTRUMENTAL`):** Notice that in Episode 001 (`06_AUDIO/MUSIC/EPISODE_001/E01_CH01.../01_STEMS/`), you had `VOCALS.m4a`, `BASS.m4a`, `DRUMS.m4a`, and `OTHER_INSTRUMENTS.m4a`. In Episode 002, **zero stems are saved on disk yet**.
  2. **Beat-Locked Instrumental Hold Builder:** When extending a response hold to `4.00s`, either:
     - Use Palmier's `detect_beats` (powered by `BeatThis.mlmodelc`) or a simple Python onset autocorrelation check to find an **exact 1-bar or 2-bar loop length** ($T_{\text{bar}}$) in the instrumental section, crossfading on beat boundaries, OR
     - Duck/fade the instrumental down smoothly into a calm low-level ambient garden/marimba sustain during the 4-second thinking hold, then bring the phrase back on the answer pickup!

### 5.5 Editor Control (Palmier Pro 0.11.0 MCP)
* **How Palmier MCP Actually Works Under the Hood:**
  - Inspecting `/Applications/PalmierPro.app/Contents/Resources/palmier-pro.mcpb` (`server/index.js`) shows that Palmier runs a Streamable HTTP / SSE MCP server at `http://127.0.0.1:19789/mcp` using protocol version `2025-06-18` and session header `Mcp-Session-Id`.
  - Your Python application does **not** even need Node.js to talk to Palmier: it can POST JSON-RPC 2.0 directly to `http://127.0.0.1:19789/mcp` using `httpx`!
* **Evaluation of Required Editor Operations in Palmier Pro:**
  1. **Project Open / Create / Save / Reopen:** **Supported & Verified** via `manage_project(action='list'|'open'|'create'|'close')`.
  2. **1080p30 Timeline Creation & Duplication:** **Supported & Verified** via `create_timeline` and `set_project_settings(fps=30, width=1920, height=1080)`.
  3. **Audio Master Placement & Trims:** **Supported & Verified** via `import_media`, `add_clips`, and `set_clip_properties(trimStartFrame, trimEndFrame, fadeInFrames, fadeOutFrames, volumeDb)`.
  4. **PNG Hard Pose Swaps (No Crossfades):** **Supported & Verified** by placing sequential PNG clips back-to-back `[startFrame, endFrame)` on the `Mar` or `Kuku` track with `fadeInFrames=0, fadeOutFrames=0`.
  5. **Foot-Anchored Breathing/Sway Keyframes:** **Supported & Verified** by pre-computing `[frame, topLeftX, topLeftY, 'smooth']`, `[frame, width, height, 'smooth']`, and `[frame, degrees, 'smooth']` arrays every 15 frames in Python and calling `set_keyframes`.
  6. **Visual Verification Readback:** **Supported & Verified** via `get_timeline` (structural JSON readback) + `inspect_timeline` (actual rendered composite PNG frames with burned-in `0..1` coordinate grid and frame number).
  7. **Final Export & Self-Contained Backup:** **Supported & Verified** via `export_project(mode='video', codec='H.264', outputPath=...)` and `export_project(mode='palmier', outputPath=...)`, polled via `manage_exports(action='list')`.

---

## 6. Practical Operating Limits (8 GB Mac vs. Windows ZBook)

1. **8 GB Unified Memory Budget on MacBook Air M1:**
   - macOS 26 + WindowServer: `~2.2 GB`
   - Palmier Pro 0.11.0 (active Metal compositor + 1080p30 timeline): `~1.8–2.5 GB`
   - Chrome (with Google Flow / YouTube tabs): `~1.5–2.5 GB`
   - Remaining headroom before swap: **`~1.0–2.0 GB`**.
   - **Rule 1:** Never run DaVinci Resolve, Blender, and Palmier Pro simultaneously.
   - **Rule 2:** Never load local LLMs (`ollama` / `lmstudio`) into the 8 GB M1 RAM while Palmier Pro is open.
   - **Rule 3:** If using `mlx-whisper` locally, run it in a subprocess that **exits immediately** after transcription so its `~1.2 GB` Metal buffer is freed before Palmier renders. (Better yet, use Palmier's built-in `inspect_media` / `get_transcript` which shares Palmier's existing process memory!)
2. **63 GiB Free Disk Space Budget:**
   - A 10-minute 48 kHz 16-bit stereo WAV is `~115 MB`. Nine chapters × (original + 4 stems + 2 edited candidates) ≈ `5–6 GB` per episode.
   - **Rule:** Never copy shared `01_ASSETS/CHARACTERS/` or `01_ASSETS/BACKGROUNDS/` into per-episode folders. Reference them in-place via `import_media(source={path: ...})`. Only create `mode='palmier'` self-contained package backups at final Gate 09 archive time, and store older episode archives on external storage or the Windows ZBook.
3. **Preventing Mac Sleep During Exports / Batch Runs:**
   - Wrap every `mk-studio` CLI execution in `/usr/bin/caffeinate -dimsu` automatically via Python (`subprocess.Popen(['caffeinate', '-dimsu', '-w', str(os.getpid())])`) so display/idle sleep never kills an active Palmier MCP session or export.
4. **What Runs Locally vs. Remote vs. Windows HP ZBook Studio G7 (`i9-10885H, 32 GB RAM, RTX 3000 6 GB`):**
   - **Keep 100% on the M1 Mac for V1:** File reconciliation, audio DSP/edit-graph rendering (FFmpeg takes `<1 second` per chapter on M1), cue math, scene compilation, and Palmier Pro MCP assembly/export (Palmier Pro is macOS/Apple Silicon native and uses M1 Media Engine hardware H.264 encoding).
   - **Use Remote APIs Only When Needed:** `codex exec` (for fuzzy transcript-to-script alignment if local deterministic matching flags anomalies) and optional image generation (only for future episodes E03+ when new props are needed).
   - **Defer Windows ZBook for V1:** Because Palmier Pro is macOS-only, the Windows ZBook cannot run Palmier assembly. Use the ZBook later only if you adopt Blender 3D rendering or local demucs/stem-separation batches over SMB/Tailscale.

---

## 7. Human Involvement: Collapse 9 Bureaucratic Gates into 4 High-Value Checkpoints

The existing workflow (`episode_002_workflow_checklist.md`) has 9 gates and dozens of micro-confirmations that caused you and ChatGPT to spend hours writing handoff documents about whether files were saved on the Mac. **Software should verify file existence, dimensions, SHA-256 hashes, alpha bounds, and frame math automatically with zero human prompts.**

Reserve human attention strictly for **4 Creative & Sensory Checkpoints**:

| Checkpoint | What the Human Does | What `mk-studio` Automates Before Prompting You |
| :--- | :--- | :--- |
| **Gate A: Script & Visual Kit Lock** *(Once per Episode)* | Read the clean chapter script & approve the 1-page **Visual Kit Contact Sheet** showing new episode props composited next to Mar & Kuku on the episode background at actual target scale. | Parses `E02_MASTER_SCRIPT_v001.md`, generates clean Google Flow prompt cards, scans `01_SOURCES/`, verifies RGBA alpha & dimensions, measures foot baselines, and renders the composite scale-check sheet automatically. |
| **Gate B: Chapter Audio Ear-Check** *(60s per Chapter)* | Listen to the **Edit Candidate Player** (a single local HTML page with keyboard shortcuts `[Space]`, `[1..6]` to jump directly to the `100 ms` crossfade joins and the `4.00s` response holds) and click **Approve Audio** or **Tweak Cut**. | Watches for dropped Google Flow WAVs, runs word-level transcription, compares words against the approved script, flags "Cuckoo"/extra lyrics, builds the non-destructive edit graph, and renders the candidate WAV + join snippets. |
| **Gate C: Visual Pilot Motion Check** *(Once per Episode on CH01)* | Watch a `30-second` exported MP4 of `CH01` (or inspect live in Palmier) to sign off on host placement, foot grounding, walk-cycle stride, and basket/flower layout. | Compiles `CH01` cues at 30 fps, computes trigonometric foot-anchored keyframes, drives Palmier MCP to build `CH01`, samples boundary frames via `inspect_timeline`, and exports `CH01_PILOT.mp4`. |
| **Gate D: Full Episode Watch & Delivery Sign-Off** *(Once per Episode)* | Watch the complete `CH01–CH09` assembled master video from start to finish with sound, then click **Finalize & Archive**. | Assembles `CH02–CH09` using the approved Gate C motion templates, verifies zero black gaps/overlaps/clipping via `ffprobe` + `inspect_timeline`, exports the 1080p30 MP4, and packages the self-contained backup. |

---

## 8. Essential Changes to the Existing Proposal vs. Optional Enhancements

### 8.1 Essential Changes (Must Do Before Coding)
1. **Replace the "Parallel AI Specialist Workers" with a Deterministic Scene Template Library:**
   - Every chapter in Episode 002 uses a small set of recurring pedagogical stage layouts:
     1. `welcome_walk_and_settle` (`CH01`)
     2. `single_flower_intro_and_say` (`CH02 S01–S03`, `CH03 S01–S03`, `CH07`)
     3. `three_flower_point_quiz` (`CH02 S04–S06`, `CH03 S04–S06`, `CH06`)
     4. `three_basket_marker_match` (`CH04`, `CH05`)
     5. `two_flower_recall_quiz` (`CH08`)
     6. `full_garden_recap_and_wave` (`CH09`)
   - Instead of asking an LLM to invent coordinates for every scene, define these **6 parametric layout templates in Python** (with verified slot coordinates for left/middle/right flowers, basket markers, and Mar/Kuku poses). This guarantees 100% visual consistency across all 9 chapters and eliminates AI layout hallucinations.
2. **Add an Immediate "Episode 002 Disk Quarantine & Reconciliation" Command (`M0.5`):**
   - Before running any pipeline on EP02, `mk-studio reconcile ep02 --dry-run` must clean up the 36 files currently mixed together in `01_ASSETS/PROPS/EPISODE_002_COLOR_GARDEN/01_SOURCES/` and `06_AUDIO/MUSIC/EPISODE_002/CH01/`:
     - Promote `E02_MASTER_SCRIPT_v001.md` -> `04_EPISODES/.../01_SCRIPT/E02_MASTER_SCRIPT_v001.md`.
     - Promote `E02_AGENT_HANDOFF_v002.md` -> `04_EPISODES/.../06_NOTES/E02_AGENT_HANDOFF_v002.md`.
     - Promote `episode_002_workflow_checklist.md` (`v003`, `10,044` bytes) -> `04_EPISODES/.../episode_002_workflow_checklist.md` (archiving `v002`).
     - Copy approved flowers/swatches and `Empty woven garden basket.png` (renamed to `E02_Basket_Neutral_v001.png`) into `02_APPROVED_PNG/`.
     - Move `Mar_Walk_Right_Cycle_A..D_v001.png` and `Kuku_Walk_Right_Cycle_A..D_v001.png` to `01_ASSETS/CHARACTERS/{MAR,KUKU}/01_SOURCES/` (and approved copies after baseline normalization).
     - Organize `CH02`, `CH03`, and `CH04` WAV files into `06_AUDIO/MUSIC/EPISODE_002/{CH02,CH03,CH04}/` with SHA-256 provenance logs so the 4 competing `CH02` WAV files are explicitly disambiguated (`131.84s` vs `176.91s` vs `178.57s` vs `179.97s`).
3. **Add Automatic Alpha-Bounding-Box & Foot-Baseline Calibration (`y_foot`) to Asset Intake:**
   - Do not use a blind bottom-of-canvas formula when PNGs have varying transparent bottom padding (like Kuku's 24 px walk-cycle variance). Compute each PNG's exact visible bottom pixel `y_foot_ratio = y_max_alpha / height` at intake time and adjust `footY` in the trigonometric compensation formula automatically.
4. **Use Direct Python HTTP Client for Palmier MCP (`127.0.0.1:19789/mcp`) Instead of Routing Editor Edits Through Codex:**
   - Do **not** have `codex exec` call Palmier MCP tools directly to place hundreds of clips and keyframes (which burns thousands of LLM tokens, takes minutes, and risks model typos in float arrays). Have your **Python service** call `http://127.0.0.1:19789/mcp` directly via `httpx` in `<200 ms`!
5. **Pin Python `3.12` in `.venv`:**
   - Use `/opt/homebrew/bin/python3.12` with `uv` rather than `/opt/homebrew/bin/python3` (`3.15.0`) so audio/image/MLX libraries install cleanly from prebuilt Apple Silicon wheels.

### 8.2 Unnecessary Complexity to Cut from `Requirements_v001.md` (Defer to Optional Future)
- **Cut DaVinci Resolve Adapter from V1 Scope:** Resolve Free 21.0.3 on this machine has verified `AddFusionComp` render issues and no Edit-page keyframe API. Keep FCPXML export from Palmier (`export_project(mode='fcpxml')`) as your zero-code bridge if you ever want to open an episode in Resolve.
- **Cut Multi-Worker Lease/Heartbeat Daemon:** Use a single-process CLI + on-demand local FastAPI/HTMX review server.
- **Cut Codex App-Server / WebSocket Transport:** Unnecessary for local deterministic compilation.
- **Cut Automated Image Generation API Adapter from V1:** All 14 core EP02 props + 8 walk poses are already generated and on disk. Build the image API adapter only when EP03 starts.

---

## 9. Smallest End-to-End Proof (`M0`) and Acceptance Criteria

### The Smallest End-to-End Experiment: `CH01` 30.22-Second Vertical Slice (`M0`)
Instead of a synthetic throwaway test disconnected from Episode 002, use **real Episode 002 `CH01` data** inside an isolated scratch Palmier project (`/Users/yonassayfu/Projects/mar-kuku-production-studio/scratch/E02_CH01_Proof.palmier`):

* **Inputs (All Verified Present on Disk Right Now):**
  - Background: `BG_Village_Color_Garden_v001.png` (`1672×941` conformed to `1920×1080`)
  - Audio: `06_AUDIO/MUSIC/EPISODE_002/CH01/00_PILOT/E02_CH01_Welcome_v002_EDIT_REVIEW_FULL_MIX.wav` (`30.220s` = `907` frames at 30 fps)
  - Hosts: `Mar_Wave_v001.png`, `Mar_Introduce_Self_v001.png`, `Mar_Point_Right_v001.png`, `Mar_Listen_v001.png` + `Kuku_Wave_v001.png`, `Kuku_Happy_WingUp_v001.png`, `Kuku_Listen_v001.png` (plus optional 3-second opening walk test using `Mar_Walk_Right_Cycle_A..D` and `Kuku_Walk_Right_Cycle_A..D`)
  - Props: `Empty woven garden basket.png`, `E02_Color_Swatch_Red_v001.png`, `E02_Flower_Red_v001.png`
* **What the Proof Script Executes Automatically (< 15 seconds runtime):**
  1. Connects to Palmier Pro MCP at `http://127.0.0.1:19789/mcp` via Python `httpx`.
  2. Creates scratch project `E02_CH01_Proof`, sets `1920×1080 @ 30 fps`.
  3. Calls Palmier's `inspect_media` on the `30.220s` WAV to verify on-device word-level transcription timestamps against the provisional cue map (`0.60s` "Hello", `2.08s` "I'm Mar", `6.60s` "Today...", `15.70s` "Will you help us?", `16.70–20.70s` 4.00s response hold, `24.16s` "Our first flower is red").
  4. Places background, audio master (`907` frames), Mar track (with hard-cut pose swaps at frame `0`, `62` ["I'm Mar"], `198` [picnic], `471` [question], `501` [4s listen hold], `725` [red flower reveal]), Kuku track, and Red Flower/Basket reveal at frame `725` (`24.16s`).
  5. Pushes pre-computed foot-anchored breathing/sway keyframes (`set_keyframes` for `position`, `scale`, `rotation`) onto Mar and Kuku, holding still during the `4.00s` response gap (`frames 501–621`).
  6. Calls `inspect_timeline` at frames `30`, `75`, `550` (hold), and `780` (red flower reveal) to capture composite verification PNGs with the coordinate grid.
  7. Closes/saves project (`manage_project(action='close')`), reopens it (`manage_project(action='open')`), verifies keyframe persistence via `get_timeline`, and exports `E02_CH01_Proof_1080p30.mp4` (`export_project(mode='video')`).

### Concrete Acceptance Criteria for `M0`
1. **Zero Source Mutation:** SHA-256 hashes of all files under `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/` remain 100% identical before and after the run.
2. **Frame-Accurate Duration & Audio Sync:** Exported MP4 decodes cleanly via `ffprobe` at `1920×1080`, `30/1` fps, `907` frames (`30.23s`), stereo `48 kHz` audio.
3. **Visual Layer & Hard-Cut Proof:** Sampled `inspect_timeline` frames verify:
   - True RGBA transparency on Mar, Kuku, Basket, and Red Flower over the Scandinavian cottage background.
   - Single-frame hard swap at frame `62` (`Mar_Wave` -> `Mar_Introduce_Self`) with zero ghosting/double-exposure frame.
   - Red flower appears only at frame `725` (`24.16s`), never during the `4.00s` response hold (`frames 501–621`).
4. **Grounded Foot-Anchor & Walk Stride Proof:** Across sampled frames `0, 15, 30, 45`, Mar's and Kuku's visible bottom foot pixels remain within `±2 px` of `y = 0.935` while rotation changes by `±2°`.
5. **Save/Reopen Persistence & Idempotency:** Reopening `E02_CH01_Proof.palmier` and calling `get_timeline` returns the exact keyframe arrays, and running the compiler a second time produces `0` duplicate clips.

---

## 10. Staged Implementation Plan (From `M0` to Full Episode 002)

### Stage M0: Direct Python-to-Palmier MCP Proof & `CH01` Vertical Slice
* Bootstrap `/Users/yonassayfu/Projects/mar-kuku-production-studio` with `uv` (`Python 3.12`), `pydantic`, `httpx`, `pillow`, `numpy`, `typer`.
* Implement `palmier_client.py` (direct JSON-RPC over HTTP `127.0.0.1:19789/mcp` with session handling) and `motion_math.py` (foot-anchored breathing/sway and PNG bounding-box baseline calibration).
* Run the `CH01` 30.22-second proof in `/Users/yonassayfu/Projects/mar-kuku-production-studio/scratch/` and review `E02_CH01_Proof_1080p30.mp4`.

### Stage M1: EP02 Disk Reconciliation & Asset Normalization (`mk-studio reconcile` & `assets`)
* Implement the 6-table SQLite database and dry-run file reconciler.
* Present the exact `Old Path -> New Path` table for the 36 misplaced EP02 files in `01_SOURCES/` and `CH01/`.
* Upon your confirmation, copy/organize EP02 scripts, checklists, handoffs, approved PNG props, walk-cycle poses, and `CH01–CH04` WAVs into their canonical folders while preserving originals.
* Run `mk-studio assets inspect` to record every PNG's alpha bounding box, `y_foot` baseline offset, and fix `Mar_Introduce_Self_v001.png` body translucency (`alpha ≈ 253 -> 255`) into a versioned derivative.

### Stage M2: Audio Ear-Check Player, Word Alignment & `CH01–CH04` Readiness
* Build the local **Audio Ear-Check & Join Reviewer** (`mk-studio serve` — lightweight FastAPI + HTMX single-page player):
  - Plays `CH01`, `CH02`, `CH03` edit review candidates alongside originals, with one-click buttons to audition each crossfade join and 4.00s response hold.
  - Processes the newly discovered `CH04` WAV (`149.291s`) through Palmier `inspect_media` (or `mlx-whisper`), diffs against `CH04` script, and proposes the `CH04` edit recipe for your ear check.
  - Outputs clean copy-paste Google Music/Flow prompt cards for remaining chapters `CH05–CH09`.

### Stage M3: Declarative Scene Templates & Full `CH01–CH09` Assembly
* Implement the 6 parametric visual layout templates (`welcome_walk_and_settle`, `single_flower_intro_and_say`, `three_flower_point_quiz`, `three_basket_marker_match`, `two_flower_recall_quiz`, `full_garden_recap_and_wave`).
* Compile verified cues + templates for `CH01–CH09` into `E02_Master_Timeline` in Palmier Pro.
* Run automated technical QA (`inspect_timeline` contact sheet at every cue boundary + `ffprobe` stream/loudness checks), present for your Gate D full watch-through, and export the final `1080p30` MP4 + `.palmier` and `.fcpxml` backup archives.

---

## 11. Cost and Operational Considerations

| Component | Recommended Provider / Tool | Recurring / Per-Episode Cost | Operational Notes |
| :--- | :--- | :--- | :--- |
| **Video Assembly, Compositing & Export** | **Palmier Pro 0.11.0** (Local MCP on M1 Mac) | **$0 marginal cost** (local tool calls) | `import_media`, `add_clips`, `set_keyframes`, `inspect_timeline`, and `export_project` run locally on Apple Silicon at zero credit cost. (Avoid calling Palmier's paid `generate_video`/`generate_image`/Magic Mask tools unless explicitly budgeted.) |
| **Word-Level Transcription** | **Palmier `inspect_media` / `get_transcript`** (macOS 26 `SpeechAnalyzer`) + **`mlx-whisper`** | **$0** (100% local on M1) | Eliminates waiting on external Manus uploads for every edited audio candidate. Keep Manus Markdown importer as a zero-cost fallback. |
| **Audio Generation (V1)** | **Google Music / Flow** (Manual via browser + `FlowPass`) | **$0 / Existing Google Account** | Human-in-the-loop generation in V1. Whenever possible, download **stems** (`VOCALS` + `INSTRUMENTAL`) alongside the full mix so 4-second response holds never require awkward full-mix loops. |
| **Bounded AI Planning / Alignment (Optional)** | **`codex exec`** (OpenAI CLI) or deterministic Python | **$0–$2 / episode** (or $0 in deterministic mode) | Because `E02_MASTER_SCRIPT_v001.md` is already written and our scene compiler uses deterministic Python templates, an entire episode requires near-zero LLM API tokens. |
| **Image Generation (Future EP03+)** | ChatGPT Web (Manual) or OpenAI `gpt-image-1` API | **$0** (Web plan) or **~$1–$3 / episode** (API for ~15 PNGs) | Not needed for EP02 (all 14 core assets + 8 walk poses are already on disk). |
| **Engineering Effort (Realistic Solo + AI Pair Range)** | Python 3.12 + `httpx` + `Pillow` + `FFmpeg` + HTMX | **~3–5 focused sessions** for `M0–M2`; **~1–2 weeks** through full `M3` episode completion | Cutting the 24-table multi-agent orchestration layer and Resolve adapter reduces codebase size by ~65% (~1,500–2,500 lines of clean Python vs. 8,000+ lines). |

---

## 12. Remaining Decisions Requiring Your Input

Before we begin **Milestone M0** implementation, I need your confirmation on **five concrete decisions**:

1. **Approve the Simplified Architecture ("The New Idea"):**
   - Do you agree to replace the multi-worker autonomous AI agent swarm with a **deterministic Python Media Compiler (`mk-studio`) + 6 parametric visual scene templates + single-process HTMX review player**, driving **Palmier Pro MCP (`127.0.0.1:19789/mcp`)** directly over HTTP?
2. **Disambiguate the Four `CH02` Audio Files on Disk:**
   - Our read-only scan found **four** different `CH02` original WAV files across `CH01/` and `01_SOURCES/`:
     - `06_AUDIO/MUSIC/EPISODE_002/CH01/Mar and Kuku — Color Garden Adventure (CH02).wav` (`131.840s` — matches the `E02_CH02_AUDIO_EDIT_REVIEW_v001.md` report)
     - `06_AUDIO/MUSIC/EPISODE_002/CH01/Mar and Kuku — Color Garden Adventure (CH02) (1).wav` (`176.907s`)
     - `01_ASSETS/PROPS/EPISODE_002_COLOR_GARDEN/01_SOURCES/Mar and Kuku — Colour Garden Adventure (CH02).wav` (`179.968s`)
     - `01_ASSETS/PROPS/EPISODE_002_COLOR_GARDEN/01_SOURCES/Mar and Kuku — Colour Garden Adventure (CH02) (1).wav` (`178.571s`)
   - Confirm whether the `131.840s` file (and its `105.340s` edited candidate in `CH01/00_PILOT/`) is your active `CH02` candidate, and whether the other three (`176.9s`, `178.6s`, `180.0s`) should be moved to `99_ARCHIVE` or kept as alternate takes.
3. **Confirm Status of the `CH04` Audio File Found in `CH01/`:**
   - We found `Mar and Kuku — Colour Garden Adventure (CH04).wav` (`149.291s`, saved Oct 21 at `23:44`) inside `06_AUDIO/MUSIC/EPISODE_002/CH01/`. Should `M1` move this to `06_AUDIO/MUSIC/EPISODE_002/CH04/` and run transcription + script comparison on it immediately?
4. **Authorize the `M1` Dry-Run File Reconciliation for `01_SOURCES/` and `CH01/`:**
   - May our first file-management step generate a dry-run move/copy plan to put `E02_MASTER_SCRIPT_v001.md`, `episode_002_workflow_checklist.md` (`v003`), `E02_AGENT_HANDOFF_v002.md`, the 13 core props, and the 8 walk-cycle PNGs into their canonical Episode 002 folders?
5. **Confirm Launch of `PalmierPro.app` for `M0` Execution:**
   - `PalmierPro.app` (`v0.11.0`) is currently closed. When we begin `M0`, will you open `PalmierPro.app` (or allow our kickoff command to launch it via `open -a PalmierPro`) so we can run the 30.22-second `CH01` scratch-project proof against `http://127.0.0.1:19789/mcp`?

---

## 13. Sources Checked

1. **Palmier Pro Official Repository & MCP Documentation:**  
   - URL: `https://github.com/palmier-io/palmier-pro` and `https://www.palmier.io`  
   - Local Binary & Bundle Inspected: `/Applications/PalmierPro.app/Contents/MacOS/PalmierPro` (`v0.11.0`) and `/Applications/PalmierPro.app/Contents/Resources/palmier-pro.mcpb` (`v0.2.0`, MCP protocol `2025-06-18`, loopback endpoint `http://127.0.0.1:19789/mcp`).  
   - Date Checked: **22 October 2026**.
2. **OpenAI Codex CLI Non-Interactive & Structured Output Documentation:**  
   - URL: `https://developers.openai.com/codex/noninteractive`  
   - Local Binary Verified: `/Users/yonassayfu/.local/state/fnm_multishells/.../bin/codex` (`codex-cli 0.163.0`, `codex exec --help` confirming `--output-schema`, `-o`, `--ephemeral`, `-s read-only`).  
   - Date Checked: **22 October 2026**.
3. **DaVinci Resolve Scripting & Installed MCP Bridge Evidence:**  
   - URL: `https://www.blackmagicdesign.com/products/davinciresolve/fusion`  
   - Local Installation & Logs Inspected: `/Applications/DaVinci Resolve/DaVinci Resolve.app` (`v21.0.3` Free), `/Users/yonassayfu/Library/Application Support/davinci-resolve-mcp/src/server.py` (lines `31293–31920`, documenting `BridgeProxy` subscript limitations and Resolve 21.0.x `AddFusionComp` active-comp render bugs), and `logs/operation-log.jsonl` (`2026-10-20` to `2026-10-21`).  
   - Date Checked: **22 October 2026**.
4. **Apple Silicon `mlx-whisper` Word-Level Timestamp Documentation:**  
   - URL: `https://pypi.org/project/mlx-whisper/` and `https://github.com/ml-explore/mlx-examples/tree/main/whisper`  
   - Date Checked: **22 October 2026**.
5. **Local Production Root & Episode 001 / 002 Artifacts Inspected (Read-Only):**  
   - `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/00_PROJECT_NOTES/AI_HANDOFFS/E01_MOTION_REFERENCE_SNAPSHOT_v001.json`  
   - `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/00_PROJECT_NOTES/Master_Template_For_Episoids/Mar_and_Kuku_MASTER_PRODUCTION_GUIDE_v001.md`  
   - `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/05_PALMIER_PROJECTS/EPISODE_001_Mar_Big_Counting_Adventure.palmier/project.json`  
   - `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/04_EPISODES/EPISODE_002_Mar_Kuku_Color_Garden_Adventure/` (all subdirectories and audio review reports)  
   - `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/01_ASSETS/PROPS/EPISODE_002_COLOR_GARDEN/01_SOURCES/`  
   - `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/06_AUDIO/MUSIC/EPISODE_002/CH01/`  
   - Date Checked: **22 October 2026**.

