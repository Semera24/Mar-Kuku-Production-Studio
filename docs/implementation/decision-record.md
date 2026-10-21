# Decision record — 22 October 2026

Status: proposed design for owner review; discovery completed, implementation not started.

## Authority and intent

The current attached request supersedes conflicting first-release instructions in the older kickoff. The production root is **`/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/`**; never rename it. The application workspace is **`/Users/yonassayfu/Projects/mar-kuku-production-studio/`**. Palmier's recent-project list includes stale paths such as `Desktop/DoroNesh/`; those are returned history, not selected roots or permission to inspect them.

Preserve the Master Producer: coordinate episode brief/planning, approved script, assets, owner-generated Google Music/Flow mix/stems, transcript, edits, cues, scene assembly, QA and delivery. EP02 is the first adopted episode and CH01 the technical proof, not a permanent product limit. Existing script and artwork approvals remain evidence with provenance; technical readiness and audio listening approval are separate.

## Alternatives considered

1. **Recommended: CLI-first sequential producer with a deterministic media compiler.** Small memory footprint and inspectable runs; compact SQLite progress and one Palmier writer. Add a local review page after editor proof. Episode-neutral contracts preserve future authoring/provider routes.
2. **EP02-only script/renderer.** Fast initial output, but would discard episode planning, approval history and future episode support. Rejected as the product architecture; a bounded scratch probe is still appropriate.
3. **Service/worker platform with parallel agents and an early dashboard.** Potential later concurrency, but adds operational work before testing the editor. Deferred, alongside Resolve, extensions and automatic image generation.

## Antigravity dispositions

| Proposal / claim | Decision and reason |
| --- | --- |
| Python 3.12, CLI, FFmpeg, validated scene data, reusable layouts, one Palmier writer | Accept. Installed Python 3.12.15 and media tools verified. Dependencies get isolated and locked after approval. |
| Compact state instead of a table per conceptual entity | Accept intent; use seven small tables with versioned payloads and explicit dependencies. The original requirements describe logical entities, not a mandatory 24-table schema. |
| Treat the entire product as only a compiler | Reject narrowing. Compiler is one deterministic service coordinated by the Master Producer. |
| Synchronous operation holding `BEGIN IMMEDIATE` throughout a run | Reject. Commit intent before media/network work; record result in a second short transaction. Use process/file locks, not a long SQLite write lock. |
| Hand-written JSON-RPC client | Prefer the official MCP Python SDK. Installed SDK 1.30.0 successfully initialized and fetched 55 live schemas. Never replay uncertain mutations automatically. |
| Palmier is fully verified from binary + EP01 history | Reject evidence conflation. Live schemas/read calls now verified; edits, persistence and exports still require M0. |
| Palmier always provides free local transcription | Reject as a general claim. Live `get_transcript` may select cloud when credits are available. Verify a no-spend local route and test real chapter audio before adoption; retain Manus import. |
| MLX transcription speed/memory, sub-second audio edits, under-15-second proof, cost/session/date estimates | Not adopted: unmeasured here. No dependency/model download or runtime provider call this turn. |
| Automatically fix alpha 253 or normalize approved artwork | Reject. Measure/report only. Placement offsets may be tested; a derivative needs a demonstrated issue and separate approval. |
| Lowest nonzero alpha row proves support-foot contact | Reject. Threshold-sensitive stray pixels reach image boundaries; annotate actual foot landmarks and verify interpolated rendered frames. |
| Organize downloads before proof | Accept dry-run now; production copying is independent and can wait. M0 references current paths. Preserve all alternate takes and both originals of every duplicate. |
| Pick/archive CH02 takes based on duration | Reject. Four originals have different whole-file hashes; none selected or archived. |
| RGB background needs transparency | Reject. Full-scene RGB is suitable; host/prop cutouts require alpha. Uniform editor fitting avoids rewriting the image. |
| Illustrative cue frames are ready for assembly | Reject. Bind cues to the exact audio hash/edit graph and check word onsets. Test-only provisional events cannot become approved teaching cues. |
| Four human reviews replace nine production gates | Modify: retain all nine internal checks; group human reviews without re-asking existing approvals. |
| Beat detection/loop math certifies music repairs | Reject. Prefer aligned instrumental stems; all new musical joins/response extensions need listening. No EP02 stems found in scoped roots. |
| Native export and FCPXML are interchangeable backups | Reject. Reopen native self-contained package and verify media independence. Live export docs explicitly omit edge softness/rounding from XML/FCPXML. |
| Full walking cycle in first proof | Defer until a few poses, one prop, grounded motion, reopen/export and rerun pass. |
| Automatic system/editor upgrades, global settings, blanket sleep changes | Not part of this work. No configuration changes made. |

## Small application design

The CLI calls application services; the producer computes the next ready action from revision dependencies and gate state. Services cover intake, script/performance extraction, transcripts, edit graphs, cues, layout compilation, Palmier assembly and QA. Ordinary math, naming, hashes, file operations and editor calls are deterministic. Later bounded authoring/interpretation adapters return validated proposals; they never execute shell text or bypass root/approval policy. No AI runtime adapter is required for M0.

SQLite tables: `episodes`, `chapters`, `artifacts`, `approvals`, `runs`, `run_events`, `file_operations`. Immutable artifact revisions include schema version, root alias/relative path, content hash, typed role, provenance, dependency hashes and status. Store transcript/edit/cue/scene payloads as versioned validated JSON; distinguish candidate, approved, placed and verified. `run_events` holds ordered step intent, external IDs, receipts, failures and reconciliation evidence. Approval records bind exact subject hash, scope, actor, provenance and decision; imported approval evidence is labelled imported, not a new listening act.

Use short transactions for intent and outcome. A single process executes ready steps; an advisory OS lock prevents concurrent studio writers, and a separate editor lock serializes the endpoint. Locks do not prevent manual editor changes: recheck project/timeline identity and expected state before every mutation batch. Capture a completed export/job ID before recording success. Hash changes invalidate only dependent results and approvals; keep history and unaffected approvals.

On restart, mark incomplete runs for reconciliation. Inspect known output paths/hashes, external job IDs and live clip signatures before deciding to skip, resume or block. Ambiguous editor outcomes block automatic replay. Cancel stops new work, terminates owned FFmpeg subprocess groups, requests cancellation of recorded export jobs, and retains evidence until terminal state is reconciled. No worker leases or parallel infrastructure.

Roots are registered aliases (`production`, `scratch`), with resolved-path/symlink checks. M0 permits writes only under application scratch; EP01 and shared artwork are always read-only. Production adoption is a separately reviewed exact operation manifest with precondition hashes, no-overwrite copy, atomic publication and post-copy checks. Never update external Markdown merely because a newer projection exists.

## Nine internal checks retained

1. Brief/scope; 2. exact script/pacing; 3. visual pilot/style; 4. asset inventory/technical readiness; 5. selected audio and listening; 6. transcript/cue verification; 7. storyboard/readiness; 8. editable assembly/pilot; 9. audiovisual QA/delivery/backup.

Group owner reviews around new words/artwork, audio joins/onsets, visual pilot, and final watch/delivery. Technical checks run automatically. M0 is explicitly a technical-test exception using unapproved review audio, not a pass for production gates 5–9.

## Evidence sources

- [Discovery and capability findings](discovery.md), [inventory](inventory.json), [exact reconciliation](reconciliation.tsv).
- Local requirements, kickoff, Antigravity v002, production guide, 23-page master workflow and 12-page roadmap, full E02 master/checklist/handoff and CH01–CH03 audio reports. Extracted PDF text is in `scratch/discovery/production-pdfs.txt`; no layout QA claim.
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) identifies the SDK used for discovery. [Palmier official repository](https://github.com/palmier-io/palmier-pro) is supplementary; installed live schemas govern this adapter.
