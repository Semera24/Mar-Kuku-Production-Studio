# Antigravity implementation task — M0 CH01 proof

Prepared 22 October 2026, Africa/Addis_Ababa. This is a task for the owner to give Antigravity; it has not been sent automatically. When the owner pastes the kickoff below, proceed with the bounded M0 implementation. Production reorganization and later milestones remain outside this task.

## Paste-ready kickoff

You are the sole implementation engineer for Mar and Kuku Production Studio in:

`/Users/yonassayfu/Projects/mar-kuku-production-studio/`

Read this entire task and its referenced plan, then implement M0 through actual verification. This instruction authorizes the M0 work and scratch export described here. Do not stop after another architecture proposal. Codex has prepared discovery and will review your completed work afterward; it will not edit concurrently. Use the model the owner selects in Antigravity; do not start additional paid agents or model calls.

The owner's phrase “3D video project” was a typo for “third video project.” This task does not add a 3D/Blender milestone. Keep future episode support and the full Master Producer vision.

## Read in this order

1. Applicable `AGENTS.md`, if any now exists.
2. `docs/implementation/decision-record.md`.
3. `docs/implementation/implementation-plan.md` — execute **M0 tasks 1–6**, not M1–M3.
4. `docs/implementation/discovery.md` and `docs/implementation/validation.md`.
5. `docs/implementation/handoff.md`, `inventory.json`, `audio-inventory.md` and `palmier-tool-schemas.json` as needed for exact inputs/contracts.
6. `docs/MK_Production_Studio_Requirements_v001.md` for the product vision. The current decision record/plan supersede conflicting first-release scope in the older kickoff and your earlier `docs/antigravityIdea_v002.md` report.

The broader product is a local production assistant: episode planning/scripts, asset reuse, owner-generated audio, transcripts, audio edits, cues, editable assembly, QA and delivery. First release is CLI-first Python with compact SQLite state and one Palmier writer; a local review interface follows the editor proof. Preserve episode-neutral contracts. Future AI authoring, image providers and broader Mac automation remain possible, but are not required for this proof.

## First response and execution

Give a short capability check: whether you can access the workspace, run Python/FFmpeg, connect to Palmier through a compatible official MCP SDK, and inspect its current tools. Say which capability is unavailable if any. Then continue with every unblocked M0 task. Do not rerun the entire media audit or ask the owner to reconfirm known script/art approvals.

Inspect current workspace changes before editing and preserve them. At preparation there was no Git repository, application source or project environment; recheck rather than assuming. Establish local version control and the isolated Python 3.12 environment as the plan specifies. Do not push or create a remote repository.

## Paths and boundaries

- Production root: **`/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/`**. Never rename it. Read existing inputs in place.
- Proof artifacts, native projects, state, derived audio, renders and runtime reports: **`/Users/yonassayfu/Projects/mar-kuku-production-studio/scratch/ch01-proof/`** only.
- Application code/tests/config: inside the application workspace. Tracking/handoff documentation: `docs/implementation/`.
- EP01, official/shared sources and all production originals remain read-only. No production copies/moves/renames/deletions; `reconciliation.tsv` is a proposal, not an execution authorization.
- No artwork alpha clamping, recolouring, normalization, regeneration or paid generation. No OS/editor upgrade, installed bridge patch or unrelated configuration/permission change.
- Do not assume the wider-Mac-control aspiration authorizes unrelated access. This task is confined to the production proof and its tools.

## Known findings to use, not rediscover blindly

- Python 3.12.15, FFmpeg/ffprobe 9.0.2 and Palmier app 0.11.0 were verified. Official Python MCP SDK 1.30.0 successfully connected. Pin compatible dependencies in this application's own environment; do not modify or depend operationally on Resolve's environment.
- Palmier exposed 55 tools and negotiated protocol 2025-11-25. Refresh current schemas before relying on them. Binary strings, historical project keys, live schemas and actual execution are different evidence levels.
- `scratch/discovery/palmier_projects.json` is raw recent-project history. Old `Desktop/DoroNesh/` paths are not production roots. The previously active `TestAIProducer` project is outside scratch and is not your mutation target.
- `manage_project.create` had no documented destination parameter. Verify a supported workflow that saves the native project directly inside scratch before creating it. Use supported app/UI operations if available; if one owner action is required, give its exact save path. Do not invent a create argument, hand-write native JSON or create elsewhere and silently move it.
- `get_transcript` may automatically choose cloud transcription when credits suffice. Do not invoke a potentially paid route. Establish a supported local/no-spend path before testing real CH01 audio; otherwise retain Manus import and record automatic ASR as unproven. No unrequested model download.
- Several original WAVs have an attached JPEG stream: explicitly select audio. Four CH02 originals are distinct takes; none is selected or archived.
- Alpha bounds vary substantially with threshold. Lowest nonzero alpha is not proof of foot contact. Approved art remains unchanged; measure actual support-foot landmarks and use placement/motion compensation.

## Authorized technical input

Use this existing candidate solely for the labelled technical proof:

`/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/06_AUDIO/MUSIC/EPISODE_002/CH01/00_PILOT/E02_CH01_Welcome_v002_EDIT_REVIEW_FULL_MIX.wav`

Expected SHA-256: `93a261d5984d3285c903934939b90bf1691a7fd3fe4981f518c2007976f9cb69`.

Measured duration: 30.22 seconds, 1,450,560 sample frames at 48 kHz. Full duration requires 907 video frames at 30 fps; record the 13.333-ms visual overhang. This authorization does not establish owner listening approval. Verify current bytes before use.

Reference the background, actual Mar/Kuku poses and one red flower from their current inventory paths. Start with a few poses. Walking choreography, extra props and a review dashboard wait. Derive teaching cues from the selected audio and actual edit recipe; Antigravity's illustrative cue times are not verified. Technical frame-labelled events may be used only with an explicit technical/provisional label.

## Definition of done

Implement the plan's durable CLI/services with input hashes, approval provenance, failure logs, cancellation and restart reconciliation. Use short SQLite transactions; no write transaction held across rendering/network work. Single studio/editor writer. Unknown editor outcomes require readback before retry.

Demonstrate with **real Palmier execution**:

1. 1920×1080 at 30 fps, background, transparent hosts and teaching prop.
2. At least one hard pose swap without visual crossfade or double exposure.
3. Actual visible grounded motion; inspect both keyframes and interpolated frames. Use the plan's measured motion/foot tolerance checks and report any failures.
4. Native project saved and reopened with persistent editable animation.
5. MP4 export completed, then full decode and output-frame/timing inspection.
6. Same input rerun creates no duplicate clips or unnecessary repeated imports/mutations; interruption recovery is tested.
7. Referenced original hashes remain unchanged.

Tests should cover the plan's meaningful path, timing, state and recovery failures. Mock tests can verify failure handling; they cannot certify editor animation, native persistence or export. A flattened FFmpeg preview is not an editable-project proof.

Continue fixing failures within scope. If an external capability blocks progress, finish independent work and report the exact failing operation, error and evidence. Avoid repeated blind retries, silently relaxing acceptance, or declaring a partial proof complete. Do not turn M0 into a full application build.

## Required saved summary and handoff

Before ending, save **`docs/implementation/antigravity-summary.md`** and update `current-task.md`, `validation.md` and `handoff.md` to reflect actual results, preserving earlier evidence rather than erasing it.

The summary must contain:

- Overall status: passed / partial / blocked, and each acceptance criterion's actual result.
- Exact code/config/test files changed; local Git branch and commit if created; any pre-existing changes preserved.
- Exact environment setup and runnable CLI commands, without credentials.
- Native project path, timeline identity, MP4 path, evidence/report paths and relevant input/output hashes.
- Tests and verification commands executed with their observed results; identify mocked versus live checks.
- Save/reopen evidence, decoded video/audio timing, visible motion/foot checks and duplicate-free rerun counts.
- Whether local transcription ran, resolved provider/cost evidence, and unresolved transcript/cue/audio approval status.
- Source integrity results and whether any action departed from the plan.
- Remaining defects/blockers and the smallest concrete task for Codex to complete next.
- Any external jobs/processes still running, their IDs and how to reconcile/stop them. Never imply a background job succeeded just because your chat is ending.

In your final chat response, link the summary and actual deliverables and state what the owner can test now. Do not claim readiness beyond the evidence. Codex will read this summary and inspect the implementation before deciding what remains.


