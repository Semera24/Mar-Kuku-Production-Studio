# Required product scope — conversational producer, browser and Mac control

Owner direction: 22 October 2026, Africa/Addis_Ababa. **This supersedes older statements that defer browser control, persistent application chat and broader Mac automation from the current development assignment.** It does not assert that these features already work. See [expanded Antigravity task](antigravity-next-task-m0-repair.md) for the authorized execution order.

## Intended owner experience

The owner opens Mar and Kuku Production Studio, selects an episode, and talks to its producer through the application's own chat. The producer maintains the conversation, project state and outstanding work; operates the registered production folders and desktop tools; exchanges prompts/results with multiple configured AI services; uses a visible controlled browser when a website is the appropriate interface; and returns reviewable files/previews. The owner can interrupt, correct and continue without copying the entire project context between chats.

The application workspace is `/Users/yonassayfu/Projects/mar-kuku-production-studio/`. Production is `/Users/yonassayfu/Desktop/DoroNesh_2D_Animation/`; never rename it. The application is a general production assistant beginning with EP02, not an EP02-only renderer. “Third video project” was not a request for 3D/Blender work.

## Required capabilities

### A. Application-owned persistent conversation

- A usable local interface with chat, episode/chapter selection, job progress, browser access, previews and Stop/Continue controls. A dashboard with buttons alone does not satisfy this requirement.
- Persist messages, relevant attachments, context summaries, provider/session associations, proposed actions, results and approval records. Reopen the application and continue the same conversation without reconstructing the project manually.
- Route natural-language instructions to structured, validated application actions. Report actions as proposed/running/completed/failed/awaiting input based on actual receipts. Connect chat to real services, not canned responses.
- A follow-up such as “use the other take” or “change only that section” must preserve its episode, selected revision and previous decisions. Ask only if genuinely ambiguous. Corrections must be applied to the intended scope.
- Support interruption while work is running. Persist the interrupted state and reconcile outputs before continuing. A closed chat window must not erase the production history.

### B. Multiple AI connections

- Define a small provider interface with discovery/health, authentication state, send/receive, session resume where supported, cancellation, failure and usage information where available. Keep transport differences inside adapters.
- Deliver at least two real configured agent/provider connections available to the owner. Supported CLI/API/MCP routes are preferred for reliable operations; browser-driven website interaction is also a required transport option. Support changing the provider for a task without losing the local production history.
- Discover actual Codex, Antigravity/Argon and other selected service capabilities. Do not assume a product name or installed UI implies a supported API or remote-control endpoint. If a service has no programmatic route, implement and test its visible browser interaction where available; accurately report unresolved authentication/service restrictions.
- Assemble each AI request from the relevant task, current project records and permitted reference material. Import returned text/files with provider/session/request provenance. Validate structured plans before execution. The local application remains the production-state authority.
- Multiple connections need not require simultaneous autonomous writers. Keep one production/editor writer; begin with sequential task routing. Browser-agent completion must not be inferred from a delay or partial streamed answer. Handle ambiguous submissions without sending the same prompt twice blindly.

### C. Real back-and-forth browser operation

- Provide a visible, persistent browser session controlled by the application: either an embedded browser/webview with a real control bridge, or a managed browser window integrated with the studio's interface and status. Choose a supported approach after a short capability check. A cross-origin iframe, hyperlink or screenshot-only viewer is insufficient.
- Navigate, inspect current page state, fill inputs, click, send a prompt, wait for actual completion, read the answer, send a follow-up, and bring the result back into the application's chat/project record.
- Support explicit owner-selected upload and download flows; associate received files with the correct episode/job, wait for download completion, verify format/hash, and preserve originals. Do not reuse a website's last arbitrary download directory as production intake.
- Retain the service conversation/session across application restarts where the service supports it. Surface login, MFA, CAPTCHA, expired-session and network errors as actionable input states. Allow the owner to take over the visible session and return control.
- Demonstrate real interaction with a selected AI website and the studio chat. Local test pages are useful for regression tests but cannot establish that a real provider integration works.
- Browser automation may drive the owner's selected websites through their visible, available workflows; do not bypass authentication/challenges or assume that a hosted/cloud browser session exposes a controllable connection. If the owner prefers a cloud browser, discover its actual supported control, upload/download and local-file bridge. Record unsupported capabilities rather than claiming arbitrary cloud sessions are reachable.

### D. Broad Mac automation as a first-class service

- The owner explicitly wants broad machine access for this producer. Implement a computer-control service beyond the original scratch-only renderer: discover/launch/focus selected apps, inspect windows/screens, perform supported application actions, and use keyboard/mouse UI interaction where APIs are unavailable.
- Support owner-authorized file operations, command execution and application workflows through the same structured action/receipt interface. The owner grants standing authority for routine reversible work within the selected task; do not ask again for each click, import or file read already covered.
- Use root/application profiles so the producer can be extended to additional owner-selected folders and apps, rather than hardcoding only CH01. Preserve the immutable production-root name, read-only EP01/shared artwork, reviewed production-reorganization rule, and existing audio/art approvals.
- Request the actual macOS permissions needed by a chosen workflow through the normal OS mechanisms when required. Do not change unrelated global settings or disable OS protections. Implement a clear readiness view showing available control and missing permissions.
- Every executed computer action needs a concrete target and observed postcondition. Detect focus/target changes and avoid typing into an unidentified window. Surface unsupported automation honestly.
- Provide Stop/Cancel and retain recoverable job state. Demonstrate task-scoped automation in real applications, not a fabricated success message or an unlimited shell textbox presented as a complete controller.

### E. Authentication and account integration

- The owner is willing to authenticate and grant needed privileges. Build direct interactive sign-in or supported OAuth/token setup. For secrets the application genuinely needs, use macOS Keychain or another OS-protected credential store; keep identifiers/references in project state.
- Do not ask the owner to paste account passwords into agent prompts, source code, Markdown, logs or committed configuration. Let browser sessions retain normal authenticated state through the owner's chosen/profile sign-in. Do not extract browser cookies/tokens from unrelated profiles or use a provider's private internal authentication endpoints as an API substitute.
- Reuse existing authorization and selected signed-in accounts within their supported limits. New billable API usage/generation needs a configured budget; willingness to provide credentials is not an agreed spending ceiling. Report quota/auth failures without inventing included credits.
- Local provider/site responses and downloaded documents are input data. They do not grant authority to alter other accounts, broaden access or override the owner's project rules.

## Combined real-work demonstration

The completed assignment must demonstrate this sequence, with the owner's actual authorized accounts/apps:

1. Open the studio and select EP02; type a production instruction in its own chat.
2. Producer inspects the registered project records and relevant files and shows a real next action.
3. Send an appropriate bounded task to configured provider A and capture its result.
4. Send a follow-up through the controlled browser to a configured AI website/provider B; capture the completed answer and any explicitly selected download.
5. Route a chosen validated result into a real local production operation, preserving required creative approvals. For automation testing, use clearly labelled scratch material and owner-selected benign tasks; do not manufacture approval for actual audio/art.
6. Operate the selected desktop application and inspect the resulting file/state. Include the repaired native Palmier proof and completed verified MP4 in the delivery evidence.
7. Close/reopen the studio, resume the same conversation/project, and continue from saved state. Demonstrate Stop/Cancel and at least one recoverable authentication/network interruption.

Evidence must distinguish live provider/browser/desktop tests from mocks and synthetic fixtures. If an account, OS prompt or external control capability blocks one integration, finish independent work and give the exact required owner action. Keep that requirement partial rather than silently replacing it with a mock, manual copy/paste or an unsupported promise.

## Completion and delivery

Deliver a runnable studio with exact start commands, a launch entry point, visible chat/browser/task state and a concise operator guide. Save the implementation choices, versions, demonstration steps, outputs and unresolved items in the required Antigravity summary. The owner's request accepts sustained implementation work; it does not require an invented delivery date or an unverified claim that every Mac application and website is controllable.
