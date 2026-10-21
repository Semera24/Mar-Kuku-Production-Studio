# Discovery and capability evidence — 22 October 2026

Read-only production discovery, with reports written only inside the application workspace. This document does not certify an editor proof or audio approval.

## Roots and inspected context

- **Production:** `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/`. This exact spelling/name is authoritative and remains unchanged.
- **Application:** `/Users/yonassayfu/Projects/mar-kuku-production-studio/`. Initially three Markdown files plus Finder metadata; no Git repository, application code or environment. The IDE's older `antigravityIdea.md` tab had no corresponding file on disk at inspection; it was not recreated.
- No applicable `AGENTS.md` found in workspace/ancestors or the authorized production tree/ancestors checked. No credentials/config tokens read. No unrelated personal directories scanned.
- Fully read the three requested documents and the current attached request. Read the E02 master script, downloaded v003 checklist, handoff v002 and three audio-edit reports, the master production guide, 23-page master workflow PDF and 12-page roadmap PDF. PDF text extracted with system PDFKit because Poppler/Python PDF packages were unavailable; no layout/visual QA claim. Historical EP01 JSON was inspected read-only.
- Scratch contains one-off inventory/PDF probes and raw MCP evidence, not an implemented studio application. Existing Resolve-bridge Python environment was used only to import its installed MCP client dependency, with bytecode writes disabled; no Resolve tool, application or bridge mutation was invoked. Future application uses its own environment.

## Measured local environment

| Item | Verified value / evidence |
| --- | --- |
| macOS | 26.5.1, build 25F80 (`sw_vers`) |
| Architecture/model | arm64, MacBookAir10,1, 8 physical CPUs |
| Memory | 8,589,934,592 bytes (8 GiB) |
| Available filesystem space at discovery | 64,913,204 KiB, about 61.9 GiB; changes over time |
| Homebrew default Python | 3.15.0 |
| Preferred Python | `/opt/homebrew/bin/python3.12`, 3.12.15 |
| uv | 0.13.1 |
| FFmpeg / ffprobe | 9.0.2; VideoToolbox, AudioToolbox, libx264 enabled |
| Useful available filters | acrossfade, atrim, asetpts, ebur128, loudnorm, silencedetect, blackdetect; no listed whisper filter |
| Codex CLI | 0.163.0; runtime authoring/authentication not exercised |
| Palmier installed application | 0.11.0 from bundle Info.plist |
| Palmier bundled MCP shim | 0.2.0; source points at `http://127.0.0.1:19789/mcp`, default protocol 2025-06-18 |
| Resolve application | 21.0.3 from bundle Info.plist; edition, bridge operation and render bugs not re-tested |
| Available official MCP Python SDK | 1.30.0, with Pydantic 2.14.1 and httpx 0.28.1 in existing environment |

No packages, models, OS/editor updates or global settings were installed/changed. The PDFKit probe produced a compiler module cache inside scratch; it is disposable discovery output.

## EP02 file findings

Machine-readable inventory: [inventory.json](inventory.json). Exact proposed operations: [reconciliation.md](reconciliation.md) and [reconciliation.tsv](reconciliation.tsv). Detailed audio measurements: [audio-inventory.md](audio-inventory.md).

- 55 regular files in the three EP02 roots: 21 PNG, 15 Markdown, 12 WAV, one PDF and six `.DS_Store` files. This is 49 content files, not Antigravity's ambiguous “36 misplaced files.” A further 43 explicit references were hashed. No symlinks encountered; all files remained stable in size/mtime while read.
- Canonical `01_SCRIPT`, `02_STORYBOARD`, `03_SCENE_PLANS`, `04_ASSET_LISTS` and approved-prop folder are empty. Master script, latest handoff and v003 checklist are in prop sources. Their `(1)` copies are byte-identical. Canonical v002 checklist differs; do not overwrite automatically.
- All 14 core image files exist: RGB background 1672x941, plus six flowers, six swatches and basket, all 1254x1254 RGBA. Existence/alpha measurements do not establish edge quality, colour-role correctness or compositing approval.
- 22 Mar and 12 Kuku official PNGs found. Eight additional walk PNGs remain in EP02 prop sources. No character source was moved or altered.
- Antigravity's example names `Kuku_Happy_WingUp_v001.png` and `Kuku_Listen_v001.png` do not exist in the official Kuku directory. `Kuku_Happy_v001.png`, `Kuku_Think_HeadTilt_v001.png` and `Kuku_Wave_v001.png` do exist; any use must respect their actual visual content.
- Twelve WAV paths represent eleven whole-file hashes: the two CH03 95.14-second candidates are identical. Four CH02 originals have distinct hashes and durations 131.840000, 176.906667, 178.570667 and 179.968000 seconds. No choice, approval or archive decision made.
- CH04 exists in CH01's directory: 149.290667 seconds, 7,165,952 sample frames. Its words, completeness and quality are unverified. CH05–CH09 audio and EP02 stems were not found in the inventoried episode roots. EP02 voiceover/export/publishing directories are empty; no EP02 native project found in the designated Palmier-project directory.
- The permitted CH01 candidate is exactly 30.220000 seconds / 1,450,560 sample frames at 48 kHz, stereo, 16-bit PCM. SHA-256 `93a261d5984d3285c903934939b90bf1691a7fd3fe4981f518c2007976f9cb69`. Full content requires 907 video frames at 30 fps; that visual duration is 30.233333 seconds, **not** exactly 30.22 seconds.
- Eight WAVs also expose an MJPEG image stream. Explicit audio stream selection is required; “has a video stream” must not misclassify a full mix as video footage.
- CH01–CH03 review reports explicitly require owner listening. Their cue maps derive from supplied Manus intervals, not new word measurements. In particular 24.16 seconds in CH01 denotes a phrase onset, not established onset of the word “red.”

## PNG measurements and their limits

PNG signature/IHDR and ffprobe verified dimensions/format. Alpha planes were decoded read-only by FFmpeg and measured at thresholds 1, 128 and 254; the originals were never rewritten. Bounds use exclusive right/bottom coordinates.

| Image | Bounds at alpha >= 1 | Bounds at alpha >= 128 |
| --- | --- | --- |
| Mar introduction | (0,15)-(1021,1451) | (187,20)-(915,1422) |
| Kuku walk A | (0,0)-(1071,1450) | (184,71)-(868,1355) |
| Kuku walk B | (0,25)-(1071,1430) | (189,74)-(882,1362) |
| Kuku walk C | (0,37)-(1067,1450) | (179,62)-(899,1379) |
| Kuku walk D | (0,42)-(1063,1410) | (194,73)-(908,1373) |

Kuku's 24-pixel bottom variation is reproduced at threshold 128, but threshold 1 yields a different 40-pixel span. Mar introduction contains 485,733 pixels with alpha 253. These are pixel statistics, not a demonstrated visual defect or support-foot identification. Do not clamp alpha. Physical landmark registration and visible interpolation remain M0 tasks.

## Live Palmier inspection

Initially the port had no listener. The owner opened Palmier on request; the official SDK then completed `initialize` and paginated `tools/list`. Negotiated protocol **2025-11-25**, server identity **palmier-pro 1.0.0**, 55 tools. Server identity is separate from installed app version 0.11.0 and bundled shim version 0.2.0.

Complete schemas/descriptions/initial instructions: [palmier-tool-schemas.json](palmier-tool-schemas.json). Fingerprint SHA-256 `0c6b8e9b3a0292a11923f33adc7c174d8b65fbc7a4676698eeca2671475fea6e`.

Only application tools called: `manage_project(action="list")` and `get_timeline({})`. The current project was `TestAIProducer` at `/Users/yonassayfu/Documents/Palmier Pro/TestAIProducer.palmier`; returned timeline was empty, 1920x1080, 30 fps, zero frames, `canGenerate=false`. It was **read only**, not adopted as the proof project. No open/create/close/import/edit/transcription/export tool was invoked.

`scratch/discovery/palmier_projects.json` is the raw recent-project list returned by Palmier. Its legacy `Desktop/DoroNesh/` entry is not the production root and was not opened or scanned. It is intentionally retained as evidence of what the server returned, rather than silently edited to look current.

| Capability | Actual current evidence | Remaining proof |
| --- | --- | --- |
| Connection, project listing, empty timeline read | Live execution succeeded | Revalidate identity before future writes |
| Create/open/close | Live schema; close documents final save | Scratch-only destination: `path` is documented only for open/close, not create |
| Exact canvas | Schema accepts width+height+fps | Read back after mutation; set before adding content |
| Individual local imports | Schema says path imports reference in place | Actual ready receipt and usable IDs |
| Layered clips | `add_clips.entries`, exclusive endFrame; same-track overlaps overwrite | Separate tracks and correct hard-cut output |
| Animated transforms | Clip-relative keyframes; position is normalized top-left, scale is absolute normalized size, rotation clockwise | Real pivot/rotation centre, interpolation, support foot and visible motion |
| Readback | Constant/identity key tracks may be collapsed/omitted | Compare semantic animation before/after reopen |
| Composite inspection | Schema returns gridded composite frames | Actual render inspected; no playback claim from samples alone |
| Transcription | `inspect_media` is sentence-level by default; `wordTimestamps=true` requests words. `get_transcript` is timeline-relative and may choose cloud if credits suffice | Enforce no-spend local mode and test actual audio before adoption |
| MP4 / native package export | Queued job contract with explicit path and overwrite flag; progress via `manage_exports` | Completed file, full decode, visual/timing QA and native reopen |
| FCPXML | Carries supported transforms/keys but explicitly omits edge softness/rounding | Optional interchange fidelity test; never a native-backup substitute |

No transcript provider reliability, cost, latency, persistent animation, export correctness, idempotent replay or self-contained backup has been certified by this discovery.

## Historical evidence is separate

The E01 motion snapshot records 23,850 frames for `E01_Branded_Master_v002` in **`EPISODE_001_Mar_Big_Counting_Adventure_CH05_Working_v001.palmier`**. Read-only inspection of that working package confirms master/branded timelines with end frame 23,850.

The differently named original **`EPISODE_001_Mar_Big_Counting_Adventure.palmier`** cited prominently by Antigravity has an `E01_Audio_Master` with maximum clip end **27,741** frames (191 clips, 160 containing key tracks). Therefore these packages must not be conflated. Stored keys prove historical data, not current rendered motion. No EP01 package was reopened in the editor or modified, and no historical export was newly decoded here.

## Recommended next boundary

Review [implementation-plan.md](implementation-plan.md). After approval, first establish the native scratch project destination, then run the small CH01 proof. Keep production reconciliation optional and separately reviewed. No current blocker requires selecting CH02, regenerating artwork, or upgrading software.
