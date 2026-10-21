# Mar and Kuku Production Studio - Requirements v001

Prepared: 21 October 2026, Africa/Addis_Ababa.
Status: Proposed requirements and recommended architecture for review in local VS Code/Codex. No app, Mac installation, account connection or editor capability has been implemented or certified by writing this document.

## 1. Purpose and intended outcome

Build a Mac-first local production application that turns the owner's approved episode brief, character library and supplied audio into an organised, reproducible cutout-animation production. One producer coordinates specialist jobs, tracks durable state, validates outputs and presents a small number of meaningful decisions. Replace repeated chat copying, manual asset placement, naming and progress reconstruction with a visible production queue.

The owner remains the creative director. Version one assumes the owner generates/selects audio in Google Music/Flow and drops selected files into the designated chapter intake. The application then manages intake, transcription or transcript import, script comparison, audio-edit proposals, verified cue mapping, storyboard/timeline compilation, editor assembly, preview and final export. Routine permitted steps run without asking about every file. Uncertain teaching content, substantial repairs, new voice/style and final creative acceptance remain human decisions.

The owner wants about 80% less repetitive work. This is a target, not a promised automation rate. Measure hands-on minutes, manual interventions, retries, provider spend and usable approved chapters against a recorded baseline. Software can automate mechanical work; it cannot guarantee a compelling story, stable generated identity, perfect timing or subjective final quality.

## 2. Feasibility and hard boundaries

Feasible with conventional local software: controlled folder creation, asset catalogue, checksums/versions, persistent jobs, prompt generation, structured AI planning, transcript import, audio edits, frame math, timeline construction through supported editor interfaces, export and technical QA.

Needs an evidence gate: installed editor API/MCP operations, keyframes/nesting/pivots, project save/reopen, full export, transcription quality against music, image-provider access and account limits. A tool success response is not visual or auditory proof.

Cannot honestly promise: 80% on day one; all GUI features exposed by API; perfect generated poses or voice matching; semantic audio verification from signal energy; unlimited model/image usage through the owner's ChatGPT plan; unattended operation while the Mac sleeps or is off. Human listening is still required for music repairs and selected voice approval. A usable MP4 alone does not prove the editable project is correct.

Do not drive four ChatGPT website tabs as the production architecture. The successful four-chat experiment establishes the value of parallel independent tasks, not a reliable browser-control API. Use documented, explicitly configured integrations. Never reuse browser cookies or extract session tokens as a substitute for supported authentication.

## 3. User environment and immutable content rules

- Preferred host: MacBook Air M1, 8 GB RAM, 256 GB storage. macOS version and free space must be measured locally; do not infer them from a browser user-agent.
- Windows ZBook is a later optional render/worker host, not required for the first prototype. Core paths must be portable via root aliases, not embedded usernames throughout code.
- Development repository, proposed: `/Users/yonassayfu/projects/mar-kuku-production-studio/`.
- Existing media root, immutable name: `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/`.
- Episode documents: `04_EPISODES/EPISODE_002_Mar_Kuku_Color_Garden_Adventure/` beneath media root.
- Keep app source code separate from the production media tree. Do not clone a previous Laravel business app as the starting template. Existing media are attached data, not copied wholesale into Git.
- Public brand Mar and Kuku, tagline Learn. Play. Grow. International English preschool audience; core ages 3-5, broad 2-7. Future localisation: no English lettering baked into visual art.
- Preserve official Mar/Kuku identities. Kuku is the established golden chicken, not a generic chick. Existing official character poses are read-only references.
- EP01 assets/timelines/exports remain read-only unless the owner explicitly names a targeted fix.
- EP02 setting is the approved Scandinavian garden. This supersedes older Ethiopian-garden suggestions for this episode only.
- Six colours: Red, Yellow, Blue, then Green, Orange, Purple. Teach petal colour; yellow centres and green leaves are incidental. No pigment-mixing claims.
- Assets: six flowers, six circular swatches, one neutral basket duplicated in the editor, one background. Flowers are beside baskets unless an explicit occlusion solution is supplied. Additional owner-requested host poses are outside that 14-core-asset budget.
- PNG pose changes use hard cuts, not visual crossfades. Foot baseline about y .93-.94, Mar x .24/h .63, Kuku x .46/h .40 in the established widescreen composition; treat these as configurable starting profiles, not verified geometry for every PNG.
- Restrained foot-anchored breathing/sway, no floating. Teaching priority: active object, host reaction, background life.
- Master 1920x1080, 30 fps. Shorts require their own layout, later scope. About 10:30 is a planning target, never a padding requirement.

## 4. Recommended architecture and alternatives

This is an architectural project, split into testable milestones. The following stack is a recommendation for this particular local media workflow, not a claim that another language cannot work.

| Approach | Assessment |
| --- | --- |
| Local Python service + lightweight local dashboard | Recommended: media tooling, filesystem work and editor bridges fit naturally; one Mac installation with durable local state |
| VS Code extension as the primary application | Useful later for developer convenience; adds extension lifecycle/UI coupling without solving media orchestration |
| Browser extension controlling provider websites | Optional future helper for a narrowly supported action; fragile as the central worker and does not solve durable jobs/editor state |

Recommended components:

- Supported Python version compatible with the installed editor bridge; resolve exact versions during bootstrap and pin them.
- FastAPI service bound to loopback, small server-rendered dashboard using templates and minimal JavaScript. CLI and dashboard call the same application services. A standalone React frontend is optional later, not a first dependency.
- Pydantic-style validated data contracts; SQLite with migrations for durable state. One durable worker process with a small explicit job queue, leases and bounded child processes.
- FFmpeg/ffprobe for media measurements, edit execution and export checks; inspect the installed version/build/filters. PNG inspection through a normal image library. Do not require a large local generative model.
- File intake through explicit imports first; add a watcher with stable-file detection and startup reconciliation. A filesystem event alone must not imply a completed download.
- AI provider interface. Initial Codex adapter may invoke documented `codex exec` with structured output and saved authorised CLI authentication, if the installed version/account permits. Validate actual schemas/flags locally. The model receives bounded tasks and returns plans/data; application code validates and executes allowed production mutations.
- Codex app-server is an optional later adapter if persistent conversational controls are needed. Do not build the first release around experimental remote transports. Custom MCP tools may expose narrow app operations after the local engine is reliable.
- Palmier editor adapter first, conditional on local capability test. Resolve is a separate optional adapter with independent tests. Exactly one editor writer at a time.
- Editor-independent scene/timeline specification, so most production logic is not tied to one connector.

No Docker, Redis, Celery, cloud database, distributed cluster or C/C++/Go service is required for the first local release. Persistent rendering jobs must not live only in a web request or FastAPI's in-process background-task callback. Add infrastructure only when measured needs justify it.

## 5. Application workflow and human decisions

The owner opens an episode card and selects Continue. The producer evaluates actual dependency state, then schedules ready work. It must show what is running, waiting, blocked, failed and awaiting a decision. The owner should not need to reconstruct context from chat history.

Workflow:

1. Import brief/source documents; preserve provenance and extract requirements.
2. Create or adopt an episode with a validated folder plan. Adopt existing structure; do not reorganise it automatically.
3. Draft chapter/scene-labelled scripts and storyboard intent; owner approves exact words.
4. Search approved asset catalogue, select usable assets and list genuine gaps. Reuse first. Generate/import only missing assets through configured routes; perform one new-style pilot before a batch.
5. Prepare clean audio-provider prompts and performance text. Owner generates/listens in Google Music/Flow in V1 and supplies the chosen mix/stems.
6. On stable audio arrival, probe files, associate chapter/version/stems, transcribe through an explicitly configured supported provider or import Manus output, compare against the approved script, and build a proposed edit/cue map.
7. Execute eligible non-destructive edits to a new candidate, produce A/B playback and evidence, then request listening only when needed by policy. In V1 music loops/voice-tail uncertainties always require listening before final audio approval.
8. Once selected audio/cues/assets satisfy readiness, generate actual-time storyboard and compile the scene plan.
9. Build a short editor pilot in a new test project; owner approves the layout/motion template. Reuse that approved template for routine chapter assembly.
10. Assemble chapters, save project, verify readback and sampled output, render review video, run technical QA and request the owner's full watch/listen.
11. Export approved master and self-contained backup package. Publishing/upload is outside V1.

Do not compress unrelated decisions into one vague Approve all button. Permit previously authorised safe batches and provide precise change/reject controls. Approval must identify the file/script revision and content hash; changes invalidate affected downstream approvals, without discarding unaffected approved work.

## 6. Core requirements

### R01 - Episode and document management

Create/read/update/archive channel projects, episodes, chapters, scenes and tasks. Archive is the normal delete action. Hard deletion of production files is not a normal CRUD operation. Import Markdown/PDF plans with provenance and preserve originals. Display one current script, checklist, manifest, cue sheet and handoff per episode. Future E03 etc must be new episodes, not modifications to E01/E02 state.

SQLite is the operational state authority after adoption. Existing Markdown files are importable inputs; generated current checklist/manifest/handoff files are projections, not competing writable databases. Detect external document changes and explicitly reconcile them before overwriting a projection. Approved script content is stored as an immutable revision and exported to the existing 01_SCRIPT folder. No silent two-way sync.

### R02 - Controlled asset catalogue and intake

Catalogue authorised folders only. Store checksum, exact relative path/root alias, file type determined from bytes, dimensions, colour role, character/pose, alpha statistics, source/prompt/reference, version, approval and usage. Detect renamed JPGs, fake checkerboards where possible, clipped bounds, missing references, duplicates, filename collisions and licensing metadata gaps. Alpha presence is not proof of perfect edges; previews and uncertainty flags are required.

Support manual imports and optional documented image-provider API adapters. Batch workers receive the same approved geometry/identity reference, never another worker's unrelated output by accident. Preserve downloaded originals and matching generation metadata. AI similarity ranking can propose a source; deterministic path/format validation must still run. Do not generate optional props solely to keep workers busy.

### R03 - File operations and versions

Plan operations with actual old/new paths, operation IDs, collisions, content hashes and rollback notes. Dry-run first. Preserve originals; copy new derivatives with versioned names; use atomic writes and check for incomplete copies. Root path validation must account for symlinks and directory traversal. Approved media paths cannot be moved while editor references depend on them without a tested relink operation. Never fix formats by changing extensions.

Use existing save map: shared backgrounds in 01_ASSETS/BACKGROUNDS/VILLAGE; props in 01_ASSETS/PROPS/EPISODE_002_COLOR_GARDEN/{01_SOURCES,02_APPROVED_PNG,99_REJECTED}; chapter music in 06_AUDIO/MUSIC/EPISODE_002/CHxx with 01_STEMS; episode documents in their existing six folders; project files in the selected editor's project folder; exports in 07_EXPORTS. Test jobs use a dedicated sandbox, not these live destinations.

### R04 - Script and prompt preparation

Represent chapter/scene IDs, speech/lyrics, unspoken directions, response hold duration, target object and answer onset separately. Validate colour order, question/reveal pairing, no early pointing cues, and no unsupported prop dependencies. Export scene-labelled master and clean per-chapter performance text. Avoid quotation marks around colour words in Google prompts per owner preference. Keep the approved youthful female voice reference and allow modest musical-rhythm variation. Generation is not proof of exact words or pauses.

### R05 - Durable orchestration and parallel specialists

Producer evaluates a dependency graph; specialist jobs handle script drafting, asset inventory/selection, audio/transcript analysis, cue planning, scene planning, editor work and QA. These are bounded roles sharing durable structured context, not autonomous agents with unrestricted shell access.

Default limits for the 8 GB Mac: up to two cloud planning jobs, one local media-processing job, one editor writer. Optional four cloud jobs only after memory/account-limit checks. No simultaneous editing by competing agents. Outputs are staged and validated before producer integration. Every job has episode/input revision IDs, input hashes, expected output schema, permitted paths/tools, timeout, retry policy and evidence.

Job states: queued, running, awaiting_input, awaiting_approval, blocked, succeeded, failed, cancelled. Include lease/heartbeat, bounded retries, cancellation that terminates child processes, and restart reconciliation. Expired leases must not blindly replay a possibly completed editor mutation or paid request. Reconcile external job IDs/output evidence first. Use idempotency keys and output checks to prevent duplicate renders, duplicate clips and duplicate charges.

### R06 - Audio acquisition, transcription and script comparison

Owner audio intake is the V1 default. Require association with episode/chapter/script version and selected reference; distinguish candidate from approved. Match stems to the generation ID, source duration and sample alignment, not filename similarity alone.

An automated transcription adapter is needed for the drop-audio-and-continue path. Choose a supported provider only after capability/auth/cost checks; require start/end timestamps, exact observed text and uncertainty flags, with word-level evidence where available. A manual Manus Markdown/JSON importer remains a supported fallback, clearly shown as awaiting_input when no automatic provider is configured. Do not promise that a text-only Codex invocation hears audio or that this chat's tools automatically exist in the local app.

Compare observed transcript against approved words: missing/repeated/extra speech, colour order and uncertain names. Forced alignment alone cannot detect all hallucinated extra speech; semantic comparison and source review are separate. Music, low-level signal and digital silence are distinct. Energy/silence detection informs analysis but cannot certify spoken onsets or good music. Preserve candidate versus human-verified timestamps.

### R07 - Non-destructive audio repair and evidence

Create a versioned edit graph: source segments, trims, inserts, fades, gain, source-to-edited mapping and provenance. Never overwrite a selected original. Default rules can shorten excessive instrumental openings/tails within authorised policy, but uncertain speech, clipped tails, changed teaching wording, music loops and questionable joins require listening approval. Exact four-second mathematics does not prove an audible vocal-free gap.

Prefer aligned instrumental stems for answer-gap reconstruction. If no suitable stem exists, the application may propose source-music loops/crossfades as review candidates; never silently label them seamless. Preserve speech speed/pitch unless explicitly approved otherwise. Output PCM WAV with the selected quality settings and log transformations. Original stems cease to align after edits; either derive all stems through the same edit graph or mark them as source-only/unmatched. Never layer FULL_MIX and matching stems accidentally.

### R08 - Cues, timing and storyboard compilation

Maintain original-source, selected-edited-source and master timebases explicitly. Source-relative decimal seconds and frame indices must be distinct typed fields. Store sample counts where possible. The actual placed clip supplies start/trim/rate. At normal playback and 30 fps:

`masterFrame = placedStartFrame + round((sourceSeconds - sourceInSeconds) * 30)`

Define rounding explicitly as half-up for nonnegative offsets; do not rely on Python's default ties-to-even round in production. For playback-rate changes use a separately tested rational mapping; ordinary formula applies at rate 1.0 only. Transform source cues through the edit graph before master placement. A cue falling inside removed audio is invalid, not clamped silently.

Cue record: source asset/hash/version, chapter/scene/pass IDs, source in/out, spoken event, visual action, target/pose asset IDs, question end, reveal onset, answer-hold duration, verification/evidence, placed clip ID and master frame. Label repeated passes P01/P02. Master markers alone are not visible graphics.

### R09 - Editor-neutral scene plan

Validated scene plan includes resolution/frame rate, tracks/layers, selected audio, duration in integer frames, asset references, alpha/scale rules, foot anchors, pose hard swaps, shared-container motion profile, teaching-target layout, holds and markers. Round boundaries deliberately and document any subframe audio versus visual offset.

Walking: alternate poses while the shared character container moves; normalise canvas/pivots/support-foot baseline non-destructively or use placement offsets. Fixed motion must not imply a skeletal rig. Known Kuku opposite-leg ambiguity cannot be solved by vertical alignment alone; test playback and flag remaining stride defects. No floating, automatic bobbing or visual crossfades.

### R10 - Editor adapter and capability evidence

Required adapter operations, implemented only when supported: health/version/active-project read; create/select separate project; import exact assets; set 1080p30; create audio master; add tracks/clips with exact ranges; transforms/alpha; hard pose swaps; motion keyframes; markers; save/export snapshot; inspect readback; render/export status and output verification.

Record actual tool names/schemas rather than inventing calls. Confirm active project/timeline before each mutation batch; lock one writer. Snapshot supported project state before edits. On failure leave a precise resumable record, not an optimistic success state. GUI automation may be an explicitly bounded fallback; repeated blind clicking is not the primary adapter.

Palmier current official repository advertises MCP, a loopback endpoint and macOS 26/Apple Silicon requirements. Installed version/tools may differ. No automatic OS upgrade. Test one short scene before relying on any animation/export function. Do not assume binary source availability or current licences from old notes.

Resolve: inspect installed edition/build, scripting docs, local bridge schema and running listener. Prior project evidence reported Mac Free 21.0.3 via Samuel Gursky community bridge and an indexing-incompatible BridgeProxy; this is historical, not proof of the current environment. Timed SetInput alone was not proof of persistent animation. Test actual splines/keyframes, values at two times, save/reopen and rendered motion. Do not assume Free equals Studio scripting support, or MCP presence equals working feature support. A .drp is an export, not the live project database. Never patch installed bridge code silently.

Use one primary editor. If neither adapter passes, record the blocker and offer a clearly labelled FFmpeg preview/manual editing route. That fallback can prove compositing logic, but it does not satisfy the editable-project/editor-automation acceptance criterion.

### R11 - Dashboard and authorisation

Dashboard: episode overview, jobs/progress, required decisions, asset browser, script/scene view, audio A/B playback, cue uncertainty list, render previews and latest handoff. Controls: Continue, pause/resume, cancel, retry eligible job, approve/reject exact revision, show evidence and open output location.

Use local-only binding and an unpredictable local token plus origin checks for write operations; do not expose unauthenticated editor/filesystem endpoints. Keep provider secrets in OS keychain or uncommitted local config, never scripts/prompts/Git. Restrict file-serving routes to registered artefacts. Treat imported documents/transcripts as content, not executable instructions. AI tasks propose data; deterministic service methods enforce authorised roots and operations.

Routine reversible actions inside the test workspace, previously approved asset selections and rendering an approved preview template can run under a standing episode policy. New paid generation has an explicit budget/ceiling and bounded retry rules. Publishing, destructive production changes and content/voice changes require explicit owner authority. Do not prompt repeatedly for actions already authorised in the current policy.

### R12 - Export, QA, provenance and backup

Check expected resolution/frame rate/duration, stream decode, missing assets, invalid alpha, clipped/offscreen bounds, unsupported fonts, unexpected chapter gaps/overlaps, audio peaks and black frames as aids. Black-frame detection is not semantic QA. Generate sampled frames and join snippets. Human review certifies teaching clarity, voice/music and complete video experience.

Save editable project, selected media mapping, cue/edit graphs, source references, provider terms/source licence evidence and final master. Verify a self-contained backup by reopen/relink test where possible; a copied project file alone is insufficient. Backups must be incremental and estimate free space. No blanket copying all media on a 256 GB Mac. No automatic YouTube upload or account creation in V1.

## 7. Minimal data model

Entities: ChannelProfile, Episode, Chapter, Scene, ScriptRevision, Asset, AssetVersion, AudioTake, StemSet, TranscriptRevision, AudioEditGraph, Cue, StoryboardRevision, TimelinePlan, Job, JobDependency, Approval, ProviderRun, EditorSession, Render, FileOperation, Evidence, RightsRecord and AuditEvent. These are logical concepts; combine tables where that simplifies the implementation without losing identity/history.

Statuses must not collapse generated, approved, placed and verified into one boolean. A human-accepted asset can still have technical caveats. A stored timeline can still fail rendered QA. Use derived freshness from input hashes so stale renders/cues cannot silently remain current.

## 8. Milestones and acceptance

### M0 - Local discovery and disposable editor proof

Read macOS/Python/FFmpeg/Codex/editor versions and MCP capabilities. Inventory only explicitly provided paths. Produce capability report with verified/unsupported/unknown states. In an isolated scratch test project, import one background, two approved host PNGs, one colour object and a supplied audio excerpt. Produce a 10-20 second 1080p30 test with layers, hard pose swap, one actual keyframed transform and an exported MP4. Save, reopen and inspect state/output. If local user action is necessary, request one precise action. Never touch EP01. Budget editor investigation initially to a bounded session; on failure report actual blocker rather than building a dashboard around an unproven adapter.

Acceptance: output file decodes, expected format/ranges, visible alpha/layers, motion demonstrated by sampled frames/playback, editable project reopens and chosen adapter/tool versions are recorded. Do not count a mock adapter as this proof.

### M1 - Local reliable engine

Implement config/root policy, migrations, jobs, explicit asset import/catalogue, operation dry-runs, transcript importer, edit/cue schemas and CLI. Synthetic fixtures exercise timing and filesystem rules. Restart during a job and reconcile without duplicate outputs. Missing credentials/editor yield awaiting_input/blocked, not false success. Provide quickstart and meaningful tests.

### M2 - One complete chapter end to end

Use CH01 as a vertical slice. Import approved script/assets and supplied audio; automate transcription if configured, otherwise import Manus; compare words, propose/execute versioned repairs; obtain required listening acceptance; generate cues and timeline; create editable editor project and chapter preview/export. Show a usable local dashboard. Resume after restart. Human interventions are measured. Do not implement all episodes before this passes.

### M3 - Full episode

Expand the same pipeline to CH01-CH09, reusable motion/layout profiles, limited parallel planning, chapter audio master placement, joins, complete QA and backup. Re-run unchanged plan without duplicating clips or paid jobs. Target low intervention under approved policy; report actual measured automation level.

### M4 - Optional extensions

Direct supported image generation with budget policy; automatic audio-generation provider only if a suitable supported interface exists and owner chooses it; optional transcription alternatives; richer UI; Windows render worker; Shorts layouts; localisation; packaging a desktop app/VS Code extension/MCP plugin. None blocks M0-M2. Public publishing and multi-user hosting need separate requirements.

## 9. Acceptance tests that matter

1. Original checksums unchanged after adoption/edit; EP01 and shared official assets cannot be mutated by an EP02 job.
2. A file still being downloaded is not processed. Repeated watcher events create one ingestion job.
3. Crash/restart resumes with one current output and no duplicated editor clips.
4. Unverified transcript times remain unverified; music energy never becomes a listened flag.
5. Removed/extra words are flagged; six intended four-second holds map through the exact edit graph. Actual audio listening status remains separate.
6. Editing a source/script invalidates dependent cue/timeline/render revisions, preserving history.
7. Cue math matches known 30 fps cases including half-frame boundaries, source trims and chapter placements; rate changes use separate tested logic.
8. Missing assets, invalid alpha or wrong pose orientation block production readiness rather than generating replacements silently.
9. Editor save/reopen retains keyframes; sampled output shows motion; hard pose swaps do not create double images.
10. Final export has expected streams/duration and opens for owner review. Backup opens/relinks from the recorded package.
11. API timeout with unknown outcome does not blindly repeat a paid generation. Limits/cancel work without leaking secrets.
12. No broad filesystem access, shell fragments from model text or path traversal can bypass the root/operation policy.

## 10. Current EP02 checkpoint for migration

This summary is conversation evidence, not a local filesystem inspection:

- Scripts CH01-CH09 approved and consolidated in E02_MASTER_SCRIPT_v001.md. CH08 targets Red/Yellow/Orange, not the older draft's Blue/Green/Purple. Main earlier handoff v002 and checklist internal v003 predate subsequent audio work.
- Core images prepared; owner reports colour assets available and approves flowers/templates. Mar walking A-D and Kuku walking A-D accepted by owner. New Mar introduction pose has minor identity/embroidery drift and reported slight translucency. Kuku sequence has reported vertical bounds differences up to 24 px and unclear A/C leg contact. No automatic regeneration requested.
- Original CH01/CH02/CH03 audio was supplied with Manus timestamps. Shorter derivative review candidates were created: CH01 30.22 s, CH02 105.34 s, CH03 95.14 s. These are review candidates, NOT confirmed final audio. CH01-CH03 human listening approval is not recorded. The original transcripts do not directly match edited timebases.
- CH01 extra post-script speech was cut. CH02/CH03 response music was extended in some gaps; joins need listening. Audio edit reports document provenance and provisional cue mappings. This experience motivates the proposed edit graph and evidence model.
- CH04 audio generation prompt has been supplied; no CH04 audio is confirmed in this conversation. CH05-CH09 audio generation is not confirmed. No EP02 editor assembly has started here.
- Existing source filenames/paths and saved stems must be verified locally. Do not mark all gates complete or claim that ChatGPT has already checked the Mac.

## 11. Reference documents to attach or copy into docs/reference

Required context:

1. This requirements document and MK_Production_Studio_Codex_Kickoff_v001.md.
2. Mar_Kuku_Master_Production_Workflow_v002.pdf (download names may contain duplicate suffixes; preserve them as source provenance).
3. E02_MASTER_SCRIPT_v001.md.
4. Current episode_002_workflow_checklist.md and E02_AGENT_HANDOFF_v002.md, marked as earlier checkpoints; section 10/audio reports supply later status.
5. E02_Mar_Kuku_Color_Garden_Agent_Roadmap_v001.pdf, planning reference subordinate to newer approvals.
6. E02_CH01/CH02/CH03_AUDIO_EDIT_REVIEW_v001.md reports for known audio defects and timing examples.

For M0/M2, supply or point to one approved background, actual Mar/Kuku PNGs, one colour prop, selected audio/originals and corresponding transcripts. A screenshot of filenames is not image source data. A tree list is not existence proof. Media stays out of Git; use root aliases/fixture references. Optional owner-supplied Resolve bridge diagnostics can clarify prior tests but are not proof that installed code is fixed.

## 12. Official technical references checked during preparation

- Codex non-interactive mode, structured output and auth: https://developers.openai.com/codex/noninteractive/ (redirects to ChatGPT Learn documentation). Validate installed version and account access; do not assume unlimited use or included third-party/image APIs.
- Codex app-server lifecycle/protocol: https://developers.openai.com/codex/app-server/ (redirects to ChatGPT Learn). Optional future integration; remote WebSocket support is described as experimental in the fetched docs.
- Palmier official repository/MCP instructions and platform requirements: https://github.com/palmier-io/palmier-pro . Current page describes loopback HTTP MCP at 127.0.0.1:19789/mcp and macOS 26/Apple Silicon; local discovery remains authoritative for the installed build.
- FastAPI background tasks and heavy-computation caveat: https://fastapi.tiangolo.com/tutorial/background-tasks/ . The dedicated durable worker is our design choice, not a claim FastAPI supplies crash recovery.
- FFmpeg documentation index: https://ffmpeg.org/documentation.html . Direct online command/filter pages timed out during preparation; inspect installed CLI help/build/filter availability locally.
- Blackmagic official product reference: https://www.blackmagicdesign.com/products/davinciresolve/studio . It does not establish the exact capabilities of the owner's installed Free/community-bridge setup; inspect bundled scripting documentation and test locally.

## 13. Decisions to validate in local kickoff

Proposed defaults: Python local application, SQLite queue, lightweight browser dashboard, CLI shared services, Palmier first if supported, owner-generated audio, supported transcription adapter with Manus fallback, Codex bounded planning adapter, no custom browser extension in V1.

Resolve actual macOS/editor versions, editor MCP access, provider/account authentication and per-run spend ceiling locally. Until spend ceiling is configured, paid generation is disabled; imports and local processing still work. Until an editor passes M0, call its automation support unverified. Treat these as discovery tasks, not excuses to restart the entire discussion or ask the owner a long questionnaire.
