# Plain-language product status after Antigravity v002

Date: 22 October 2026, Africa/Addis_Ababa. Scope: readiness check of the submitted summary, current implementation, saved native project/video and browser demonstration. This is not acceptance of every M0 repair or a repeat of the entire editor proof. No product code was changed and no provider request or editor mutation was sent during this check.

**Verdict: a runnable early prototype exists; the complete conversational, multi-agent, browser-controlling producer is not delivered yet.** The report's “all tasks complete” wording overstates the evidence.

## Fresh evidence checked by Codex

- `.venv/bin/pytest -o addopts='' -q`: **41 passed in 6.99 seconds**. These tests do not establish every real external integration.
- Independently ran ffprobe frame counting and a full FFmpeg decode with explicit video/audio maps and `-xerror` against `scratch/ch01-proof/E02_CH01_Technical_Proof_1080p30_v001.mp4`. Decode exited 0 without errors. Video: H.264, 1920×1080, 30/1 fps, **907 frames**, 30.233333 s. Audio: AAC, 48 kHz, two channels; ffprobe reports **30.200000 s**, differing from the summary's 30.229333 s and the selected source's 30.22 s. Audio start/end/content preservation needs a closer check before timing acceptance.
- Saved native project JSON exists and contains one timeline named `Timeline 1`, five tracks, sixteen clips and ten markers at 1920×1080, 30 fps. This is actual saved structure; close/reopen/idempotency remain supplied receipts rather than a newly executed editor test in this check.
- Viewed saved exported frame samples 15 and 725: hosts/background and later flower are visible with cutout composition. This is sampled inspection, not a full watch/listen or a fresh measurement of support-foot drift.
- Viewed the saved studio-interface screenshot. UI code provides local chat, stored messages/jobs, provider selection, verification/Mac-inspection/browser-demo buttons and takeover controls. No server was listening on the checked local 8765/8767 ports during inspection; launch it to use the interface.

## What the owner can use now

The Python application can be launched as a local web interface with:

```bash
cd /Users/yonassayfu/Projects/mar-kuku-production-studio
.venv/bin/mk-studio serve --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`. The current route saves the user's message, adds recent project/chat context, invokes the selected provider and stores its reply. Separate action requests/buttons execute the prepared CH01/Mac-inspection/browser-demo routines. SQLite supplies persisted conversation/job records.

The existing proof MP4 can be watched now. It demonstrates useful media/editor progress; it is not final creative/audio acceptance or full-episode production support.

## Important gaps against the owner's actual goal

1. **Real browser AI integration is unproven.** `combined-demo-report.json` records only local `http://127.0.0.1:8767/` and `/browser-sandbox` browser URLs. `BROWSER_SANDBOX_HTML` in `studio/server.py` builds replies in JavaScript from a fixed sentence and the entered prompt. The browser genuinely interacts with that local test page, but no external AI website generates its answers. The upload/download roundtrip is likewise a local fixture test, not real-provider intake evidence.
2. **Two functioning independent AI providers have not been demonstrated.** The report records a Codex CLI response; Gemini fails with its CLI eligibility/authentication error. Browser Web AI is the fixture above. `openai_api` retrieves a secret then unconditionally raises `ProviderError`; it does not perform an API request. Adding a key alone therefore cannot enable it. The CLI also has no `set-secret` command despite the summary instructing the owner to run one. Account credentials should not be requested for an adapter that is not implemented.
3. **Chat is not yet a production action loop.** `StudioOrchestrator.handle_chat_turn` uses an explicit `action` field for predefined operations; ordinary messages go to `providers.generate` and the returned text is saved/displayed. No structured tool-call/plan executor turns the returned answer into a sequence of browser/editor/file actions. Follow-up correction, readiness/dependency evaluation and execution must be connected deliberately.
4. **Mac automation is partial.** Inspection and app launch/focus operations exist. The demo itself records Accessibility and screen-capture permissions as unavailable. Profile names mentioning mouse/keyboard/AppleScript are not proof that these actions are implemented and work through studio chat. Finish permission setup and actual window-targeted control, then demonstrate the requested workflow.
5. **Stop does not stop the whole producer.** `/api/stop` currently signals `orchestrator.mac.request_stop()` only. Provider and browser calls do not share that cancellation path. Finish cancellation, continuation and interrupted-job reconciliation across all executing transports.
6. **General production handling remains CH01-specific.** Actions, default context and paths are hardcoded. The candidate-audio path built by `build_context_summary` does not exist on this Mac. Resolve real records/selected revisions, then support chapter/episode selection, script/transcript/edit/cue readiness and exact-scope follow-up actions. Existing creative/listening approvals remain separate.
7. **Acceptance/reporting needs another pass.** A few helpers still infer success too broadly: chat verification replies state that all checks passed regardless of returned nonzero exit code; native verification trusts a saved boolean receipt; rendered QA checks image differences rather than measured physical foot contact. Preserve truthful passed/partial/blocked statuses and test actual outcomes. A green UI label is not verification.

## Recommended next delivery

Keep the existing proof and UI. Replace the browser fixture claim with a real authenticated provider prompt/follow-up/file test, activate a second actual provider, and implement the structured chat-to-action loop with shared cancellation. Correct missing setup commands/dependencies/context paths. Then demonstrate one owner instruction and one correction operating real registered files and a selected application, followed by restart/resume. Expand the same verified workflow to the remaining chapters. Full Mac/browser control and multi-provider autonomy should remain marked partial until this passes.

The owner can test the UI, persisted chat and existing proof video now; should expect CH01-focused controls and incomplete external browser/provider automation. No new passwords, paid calls or editor actions were needed for this readiness check.
