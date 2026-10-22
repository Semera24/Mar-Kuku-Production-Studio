# Next Antigravity task — repair M0, then deliver conversational browser and Mac control

Prepared 22 October 2026, Africa/Addis_Ababa. Owner-delivered task; not sent automatically.

## Paste-ready instruction

Continue in `/Users/yonassayfu/Projects/mar-kuku-production-studio/` as the sole implementer. Read `docs/implementation/codex-review-v001.md` completely, including its reproduction evidence, and **`docs/implementation/browser-chat-mac-requirements.md`**. Your previous work provides the CLI/state/audio/compiler foundation, but M0 is **partial**, not a passed editor proof. The owner now explicitly requires persistent application chat, back-and-forth browser operation, multiple real AI connections and broad Mac control **in this development assignment**. Implement these as real features, not future placeholders. Repair M0 first, then continue through the integration stages below; do not stop at a compiler, command-line demo or another proposal.

Read the original `antigravity-task.md`, `implementation-plan.md` and `decision-record.md` for background and retained constraints. **This expanded owner task supersedes their earlier M0-only stop boundary and deferral of chat/browser/Mac control.** The owner clarified “3D” was a typo for “third video project”; no Blender scope is implied. Preserve pre-existing working-tree changes and production source media. The native M0 proof still writes only to scratch. General computer control may operate additional owner-selected apps/folders through the profiles and task authority specified in the new requirements. No production reorganization, source-art alteration, new billable generation/API use without a budget, editor/OS upgrade, unrelated global configuration change, or messages to third parties. AI-service prompt/response interaction for the requested producer is expressly part of this task. Needed OS permissions and authentication are obtained through their normal interactive flows. Codex is reviewing, not editing product code concurrently.

Start by saving a concise revised implementation plan for this expanded scope, choosing the concrete local UI/browser/AI/computer-control stack from what is actually available. Then execute it in reviewable increments under this instruction; do not ask the owner to authorize the same required feature again. Keep progress current and produce runnable evidence at each stage. Do not spend the session only rewriting documents or perform unrelated machine scans.

## 1. Reproduce before fixing

Run the existing suite, then the review script:

```bash
.venv/bin/pytest -o addopts='' --tb=short -q
.venv/bin/python scratch/codex-review/antigravity-review/reproduce_findings.py
```

The review script intentionally demonstrates the current bugs and writes only independent review outputs. Its checks are not the final regression tests. Convert confirmed defects to meaningful product regression tests that first fail, then fix their root causes. If a finding is already fixed or no longer applies, supply concrete code/result evidence rather than silently ignoring it.

## 2. Restore real editor compatibility and execution safety

- **R1:** establish a single editor-lock ownership convention. Test the full CLI/adapter/runner path so an outer lock cannot conflict with the first editor step. Continue rejecting a genuinely concurrent writer.
- **R2:** replace the hand-written transport with a compatible official MCP SDK; capture negotiated protocol and current schemas. Use singular `import_media.source`, the actual supported track creation/placement/configuration operations, and one marker per supported call with valid colour format. Do not hardcode another invented add-track operation. Validate arguments and parse actual response shapes, including import receipts, tracks, ranges, names, media references and keyframe normalization. Use fixtures derived from real readback and current schemas, not a fake server that defines the interface you wanted.
- **R4:** require exact scratch project/timeline identity before mutations, failing closed if identity is unavailable or changes. Recheck source root/hash before imports. Reconcile owned media/clips/markers with exact ranges, layer order, trim/rate, static settings and semantic animation. A nonempty keyframe dictionary is not equivalence. A changed position/scale/rotation must be detected and repaired or explicitly blocked. Preserve unrelated user content; do not delete by track-name inference alone.
- **R5:** scope idempotency/receipts to episode/chapter and actual project/timeline/output identity plus input revisions. On timeout/disconnect after a mutation may have been delivered, save an uncertain outcome and inspect external state before any retry. Wire real reconciliation into the assembly entry point. Persist lifecycle/export IDs and outcomes, and leave resumable failure/cancel state. Test both a hard crash and a lost response after remote success.
- **R6:** preserve prior exports. Use explicit scratch destinations and no-overwrite publication; verify and reuse an unchanged completed result or choose a versioned output. Persist job IDs, require actual terminal completion and inspect warnings. A growing/nonempty file or missing job entry never means export succeeded. Cancel/timeout must retain enough job state for reconciliation.
- **R7:** drain subprocess stdout and stderr while running, keeping bounded timeout/cancel and process-group termination. Regression-test output larger than pipe capacity on both streams.

## 3. Make verification truthful

- **R3:** distinguish offline preparation from a complete editor proof. Full `verify-ch01` must return nonzero or a clearly non-passing status when native project, reopen evidence, terminal export receipt, valid MP4 or required visual/rerun evidence is absent/failed. Test missing and deliberately invalid MP4 cases. Do not count cached JSON assertions as fresh verification.
- Count exactly 907 decoded video frames at 1920×1080, 30/1 fps; check stereo 48 kHz audio, content duration/start/padding, full decode and expected first/last frames. Preserve all 30.22 seconds of selected source content and explain the 13.333-ms visual overhang.
- **R8:** label existing mathematical foot checks as analytic predictions. Inspect actual PNGs and record visually checked support-foot landmarks, editor pivot/sign/scale conventions and image registration. Verify actual rendered motion, including between keyframes and before/at/after hard swaps. Do not claim that alpha bounds or the compiler's own projection establishes rendered grounding.
- Keep source artwork unchanged. The pinned red flower has usable alpha; investigate the incorrect summary before treating it as an opaque-background defect. Correct unsupported asset/transcription statements. If a real visual problem needs a derivative, report it for separate review instead of altering approved art.
- Report-derived CH01 cue times remain provisional. Technical frame-labelled animation is allowed with explicit labels. Do not certify the red-word reveal from phrase start 24.16 s or claim audio listening approval. Test no-spend Palmier ASR only if provider control is verified; otherwise retain Manus import and report the unavailable capability accurately.
- Pin/lock compatible dependencies actually tested; stop ignoring the intended dependency lockfile. Make ordinary unit tests synthetic and independent of private absolute paths/generated ignored plans. Keep explicitly marked real-media/live-editor integration tests separate.

## 4. Complete the real scratch-only proof

After the regression tests pass, use supported Palmier UI/API operations to create/open the empty native package **directly** at:

`/Users/yonassayfu/Projects/mar-kuku-production-studio/scratch/ch01-proof/E02_CH01_Technical_Proof_v001.palmier`

If the save panel needs owner interaction and you cannot perform it through available authorized controls, give one precise action with this path. Do all independent repairs/tests first. Do not create elsewhere, silently move a project or hand-write native project JSON.

Then execute actual import/assembly/keyframes, save/close/reopen and compare semantic state, export to a versioned scratch MP4, fully decode it, inspect sampled output and continuous motion, and run the same plan again without duplicate clips/media or unnecessary mutation/export. Demonstrate interruption recovery against the scratch project. Capture receipts, readbacks, counts and frames. Mocked editor responses and a flattened FFmpeg preview cannot pass this stage.

## 5. Build the persistent conversational studio

After the M0 repairs, build the application's own usable local chat/dashboard, backed by the existing services and durable project state. Implement the full requirements document sections A and B: conversation history and resumable context, episode selection, job progress/results, natural-language action routing, follow-up corrections, provider selection and Stop/Continue controls. Add the small schema migrations and application modules actually needed; the earlier seven-table count is not a restriction against storing conversations/session mappings correctly.

Implement real provider adapters for at least two available agent/provider connections. Discover their supported CLI/API/MCP/browser routes and authenticate through owner-selected accounts. Codex and Antigravity/Argon are candidates, not proof of an integration. Keep local structured project history independent of provider chat memory. Sequential provider routing satisfies multiple-agent support; do not add distributed worker infrastructure or competing editor writers.

## 6. Deliver browser back-and-forth and Mac control

Implement sections C–E of `browser-chat-mac-requirements.md`. A visible controlled browser is mandatory, either embedded through a working bridge or a managed browser integrated with the studio. It must perform real navigation, prompt/answer/follow-up cycles, state inspection and selected upload/download handling. Neither an iframe nor a button that opens a website satisfies this feature. A hosted/cloud browser is usable only if its actual control and local-file bridge are available and demonstrated.

Implement the shared Mac-control service for selected apps, windows/screens, supported scripting/commands, file workflows and keyboard/mouse fallback, with observed postconditions. Integrate it with chat and durable tasks. Use normal macOS permission prompts where needed; the owner is willing to grant access. Preserve the specific EP01/shared-art/source rules and avoid repeated prompts for routine already-authorized work.

Provide direct account sign-in and OS-protected credential/session handling. Do not solicit passwords in prompts or write them into source/docs/logs. Use authenticated sessions established by the owner, supported account connections and a configurable budget for any new billable use. Authentication challenges are owner-input states; do not bypass them. Keep one editor writer and accurate interrupted/failed/completed status across these new transports.

Exercise the **combined real-work demonstration** in the requirements document: studio chat → project inspection → provider A → browser follow-up with provider B → validated local operation → selected desktop app → verified result → restart/resume. Include the repaired Palmier proof. Test real Stop/Cancel and recoverable auth/network failures. Do not substitute mocked providers for the delivered feature.

If an external dependency blocks one route, finish independent repairs/UI/services and precisely identify the required owner action or unavailable capability. Do not label the feature complete, indefinitely repeat a failing operation, or silently defer it out of scope. Work can continue across sessions using saved checkpoints; a context limit is a handoff boundary, not completion.

## 7. Save an honest handoff

Preserve your original summary as historical evidence. Write **`docs/implementation/antigravity-summary-v002.md`** and update current task, validation and handoff to one consistent current status. Include:

- Each R1–R8 disposition, code locations and regression-test results.
- Exact environment/commands, Git baseline/new commit if created, and preserved user changes.
- Native project/timeline, MP4, schema/receipt/readback/sample paths, and input/output hashes.
- Actual live assembly, reopen, decoded frame/audio results, rendered grounding and duplicate-free rerun evidence.
- Accurate source-integrity counts, distinguishing Finder metadata from production content.
- Separate technical-use authorization, recipe provenance, provisional cues, ASR status and pending listening approval.
- The revised architecture and exact start/launch commands for the studio; which browser is controlled and how the owner enters chat and takes over a session.
- Each required chat/browser/multiple-provider/Mac-control acceptance result, with actual provider/transport/version, persistent session/context behavior, task receipts and demonstration artifacts. Keep secrets out of evidence.
- Which OS permissions/accounts were connected, described by non-secret identifiers, and any exact remaining sign-in/MFA/permission action.
- Unfinished work, exact blocker/owner action if any, and remaining external job/process IDs.

Use **passed**, **partial** or **blocked** for M0 and each subsequent feature separately. Do not call M0 complete merely because code exists or unit tests pass, and do not call the overall assignment complete until the required conversational/browser/provider/Mac demonstration works. No runtime/date/cost guarantee is requested. Preserve a clear checkpoint and continue until the authorized scope is complete or a concrete external blocker requires owner input.
