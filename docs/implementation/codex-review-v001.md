# Codex review of Antigravity M0

Date: 22 October 2026, Africa/Addis_Ababa.
Reviewed baseline: local commit `4b54790` plus the current working tree. The 17 pre-existing modified files differ from that commit by added trailing blank lines; they were preserved. Review scope: submitted summary, code, tests, saved artifacts and live read-only tool schemas. No product-code fixes or Palmier mutations were made by Codex.

**Verdict: useful CLI foundation; M0 is partial and is not accepted as an end-to-end editor proof.** Saving an empty project alone will not make the submitted pipeline work: blocking implementation defects remain. Do not move to full-episode assembly, dashboard or conversational automation before these are addressed.

## Findings, ordered by impact

### R1 — P1: the normal assembly command conflicts with its own editor lock

[`cli.py:290`](../../src/mk_studio/cli.py#L290) holds the editor-writer lock for the full assembly. [`runner.py:186`](../../src/mk_studio/runner.py#L186) opens and attempts to acquire the same non-reentrant lock for every editor step. A minimal reproduction with the same runner raised `ConcurrentRunError` before its mutation action ran. The missing scratch project currently masks this next failure. Establish one consistent lock owner and test the CLI-to-runner path, not just adapter calls without the outer lock.

### R2 — P1: adapter calls conflict with actual Palmier schemas; the fake server accepts the invented interface

Fresh official-SDK initialization and `tools/list` in this review returned the same 55 tools/protocol 2025-11-25. No editor mutation was invoked.

| Adapter location / payload | Actual schema |
| --- | --- |
| [`palmier.py:439`](../../src/mk_studio/editors/palmier.py#L439): `import_media(sources=[...])` | Requires singular `source`; `sources` is undocumented |
| [`palmier.py:504`](../../src/mk_studio/editors/palmier.py#L504): `manage_tracks(action='add',type,name,atIndex)` | Only `reorder`, `set`, `remove`; this tool does not expose the invented add operation |
| [`palmier.py:647`](../../src/mk_studio/editors/palmier.py#L647): batched `manage_markers(markers=[...])` | Creates one marker through top-level fields; colours are hex strings, not the plan's `blue`/`green` names |

The adapter tests implement those same invented calls in `FakePalmierMcpServer`, so they pass without proving compatibility. Track creation/ordering must follow supported operations, with actual returned IDs and readback. The hand-written `HttpMcpSession` is used even though an official SDK is installed; it does not implement the plan's SDK-based, schema-validated client. Recheck real response shapes, session identity and media mappings as well as request schemas.

### R3 — P1: verification reports success without the required project or video

[`cli.py:343`](../../src/mk_studio/cli.py#L343) checks inventory, calculated motion and cached PCM comparison but ignores `palmier_project_exists` and `mp4_qa.passed`. Running the real verifier against independent review copies of its four metadata files returned **exit 0 with neither a native project nor an MP4 present**. It would also ignore an MP4 QA failure in its final exit status. Offline preparation and complete M0 verification need distinct outcomes.

[`qa.py:79`](../../src/mk_studio/qa.py#L79) does not count the required 907 decoded frames or check stereo channels, project persistence, sampled visual output or rerun evidence. A close duration and nominal frame rate are insufficient. Audio PCM evidence is read from saved JSON rather than freshly checked during verification.

### R4 — P1: animation and project reconciliation can falsely certify a changed project

[`palmier.py:365`](../../src/mk_studio/editors/palmier.py#L365) checks whether some keyframe data exists rather than comparing the planned position/scale/rotation values, static properties, track order and relevant timing. In the submitted fake-server harness, Codex moved Mar to normalized position `(100,100)`, deleted scale/rotation animation and changed its static transform. `reconcile_plan` still returned **`is_noop=True`**. Therefore its save/reopen comparison cannot establish persistent correct animation.

Project identity validation also allows missing identity and any path under scratch, rather than requiring the exact intended package/timeline before each mutation batch. Media name fallback can confuse same-named files. Import paths/hashes need revalidation at the execution boundary, not only during an earlier bind/compile command. Only recorded owned clips should be reconciled/removed; a matching track name alone is insufficient ownership evidence.

### R5 — P1: unknown editor outcomes can be repeated; cached steps are not scoped to their target

[`runner.py:204`](../../src/mk_studio/runner.py#L204) records an editor timeout as ordinary `failed`, enabling another call with the same inputs. Reproduction: a fake remote operation performed its side effect and then raised a timeout; invoking the runner twice produced **two side effects**. The test for a hard-crashed `running` row does not cover this common lost-response failure. Assembly also lacks a real external-state reconciliation path before retry and durable lifecycle/export step receipts.

[`state.py:390`](../../src/mk_studio/state.py#L390) searches/cache-invalidates by step name globally, with an idempotency key of only step name/kind/input hash. Reproduction: the same import step in EP003 was skipped using EP002's receipt. Include episode/chapter, exact editor project/timeline or output target and relevant input revisions in cache/reconciliation identity. Do not trust a previous success when current external state differs.

### R6 — P1: export can erase the previous result and declare a partial file complete

[`palmier.py:684`](../../src/mk_studio/editors/palmier.py#L684) unlinks an existing MP4 before requesting a replacement and omits `overwrite=false`. In a scratch-only reproduction, rejected submission left the old file deleted. At line 717 it treats any nonempty file as completed if the job is absent from the list: a **13-byte partial header** was returned as `status='completed'` without terminal job evidence. Persist and reconcile job IDs; preserve verified previous outputs; handle cancel/timeout without losing job state. Completion requires both a terminal success receipt and file verification.

### R7 — P1: subprocess output pipes can deadlock

[`runner.py:109`](../../src/mk_studio/runner.py#L109) polls for exit before draining piped stdout/stderr. A child that writes more than pipe capacity blocks while the parent waits for it to exit. A one-megabyte stdout child that should exit immediately instead timed out in the review reproduction. ffprobe/FFmpeg diagnostics can encounter this. Drain both streams while preserving timeout and process-group cancellation.

### R8 — P1 acceptance gap: calculated motion and automatic alpha landmarks do not prove rendered grounding

[`registration.py:57`](../../src/mk_studio/registration.py#L57) estimates landmarks from alpha geometry, including the average of two foot candidates. There is no recorded visual support-foot annotation or editor transform calibration. [`scene.py:601`](../../src/mk_studio/scene.py#L601) evaluates its own transform model; reported subpixel foot drift is a **mathematical prediction**, not a measurement of Palmier's rendered frames. No native package, exported MP4 or visual sample exists in the proof directory. Genuine rendered proof remains outstanding.

The template hardcodes report-derived cue frames, including a flower reveal at frame 725, and changes the proposed composition/motion values without rendered calibration. Labels retain `report_derived`, which is useful; they must remain provisional. Phrase start 24.16 s must not become a verified onset for the word “red.” Freeze response-hold teaching visuals as specified; do not silently convert a technical animation test into production timing approval.

## Additional corrections before the next handoff

- The summary's red-flower transparency claim is incorrect. Fresh inspection of the exact pinned file finds RGBA, alpha range 0–255 and 1,020,840 fully transparent pixels; its hash matches discovery. The current registration code reports alpha-channel presence, which itself is not full edge QA. Do not create a derivative to repair a falsely reported opaque source.
- `media.py:483` hardcodes that `inspect_media` cannot transcribe. Fresh tool descriptions explicitly expose audio transcription and `wordTimestamps`. Local availability/cost/accuracy remain unproven; preserve this uncertainty rather than assert either free operation or impossibility. No transcription call was made in this review.
- State contains a technical-use approval naming an audio review document as the approving authority. The owner's explicit M0 authorization is the relevant authority; the review document is recipe evidence, not listening approval.
- Dependencies are broad minimum versions, no dependency lockfile is present, and `.gitignore` ignores all `*.lock`. The actual project environment has MCP 2.3.0, not the 1.30.0 used for discovery. Record/lock the versions actually tested without changing installed editors.
- Some unit tests require private source media and write/regenerate `scratch/ch01-proof` outputs, while the editor test loads a hardcoded ignored generated plan. Separate synthetic unit fixtures from explicitly invoked local-media integration tests so a clean checkout can test without those artifacts. Running the submitted suite in this review regenerated its scratch registration/spec/plan; originals were not modified.
- Tracking documents contradict themselves: M0 complete versus no code created, waiting for old plan approval versus implementation already committed. Replace the current status with truthful partial/blocked evidence and retain earlier records as history.

## Independently verified progress

| Area | Result |
| --- | --- |
| Existing tests | `.venv/bin/pytest -o addopts='' --tb=short -q`: **28 passed in 1.85 s** |
| Audio reproduction | Fresh comparison: 1,450,560 stereo sample frames, max sample difference 0, RMS difference 0.0; WAV containers differ |
| Input integrity | 98 files checked: 92 non-Finder content files unchanged; no missing files/content mismatches. One of six `.DS_Store` records differs; no cause attributed |
| Live tool discovery | Official SDK fetched 55 current schemas; actual request mismatches confirmed |
| Native project / MP4 | Neither exists at the promised scratch destination |
| Real animation, reopen, export, idempotent editor rerun | Not demonstrated |
| Product status | CLI/media/state/compiler code exists. No studio dashboard or application-owned chat exists yet |

## Reproduction evidence and review limits

Review-only script: `scratch/codex-review/antigravity-review/reproduce_findings.py`.
Run: `.venv/bin/python scratch/codex-review/antigravity-review/reproduce_findings.py`.
Outputs: `reproductions.json`, `reproductions.log`, `pytest.txt`, `live-schemas.json`, `source-integrity.json` in the same review directory. Editor mutation cases use the submitted fake harness; that reproduces software defects without claiming live editor behavior. Live access was initialization and schema listing only. No application code was fixed, no commit was made, and no editor project was created/changed.

**Next task:** [repair M0 and complete the real proof](antigravity-next-task-m0-repair.md). After M0 acceptance, scope the durable chapter workflow and a local interface with the owner's explicitly requested back-and-forth chat. That product requirement should remain visible; it is not delivered by this CLI foundation.
