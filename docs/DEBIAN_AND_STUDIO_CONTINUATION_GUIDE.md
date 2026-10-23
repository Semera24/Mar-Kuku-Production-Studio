# Mar & Kuku Production Studio — Master Continuation Guide (Debian 13 Linux & Universal Multi-Platform)

> **Document Version:** `v1.0.0-master`  
> **Target Audience:** Next-Session AI Coding Assistant (Google Gemini Pro), Studio Owner, and Linux DevOps Engineers.  
> **Purpose:** Serves as the single authoritative operational manual and codebase map to resume development, maintenance, and autonomous operation of **Mar & Kuku Production Studio** on Bare Debian 13 (Trixie) Linux or any host OS without losing context.

---

## 1. Executive Mission & Core Standards

**Mar & Kuku Production Studio** is an autonomous, multi-agent 2D animation and educational channel production engine. It transforms raw concepts into broadcast-ready, fully staged YouTube episodes with **Zero-Touch Automation** and remote **Human-in-the-Loop** approval via mobile Telegram.

### Non-Negotiable Standards & Hard Rules:
1. **Aspect Ratio & Frame Rate:** Strict **16:9 Widescreen (1920x1080 @ 30.00 fps)**. This is a primary YouTube broadcast pipeline; never revert to 9:16 Shorts unless an explicit separate Shorts branch is requested.
2. **Audio Specifications:** 48kHz, 16-bit PCM stereo WAV.
3. **Interactive Response Hold Gap:** Exact **4.00-second stillness hold** (`frames 501..621` in 30s clips) calibrated with sub-frame FFmpeg precision for toddler call-and-response engagement.
4. **Visual Aesthetics:** High-contrast, vibrant, color-saturated 2D character and prop PNGs with clean alpha transparency (`1254x1254` standard).
5. **Multi-Editor Decoupling:** The studio supports **both** Palmier Pro (`.palmier` native timelines) and **CapCut Desktop** (`draft_content.json` / `draft_meta_info.json`). Never tie the pipeline exclusively to macOS-only software.
6. **OS Independence:** Full Tier 1 support for **Bare Debian 13 Linux** and macOS Darwin. All OS-specific operations (browser launching, window focus, file managers) run via `src/mk_studio/os_compat.py`.
7. **Safe Gate Policy:** Never guess uncertain outcomes or publish unapproved media. If a step requires owner sign-off, freeze execution, dispatch an interactive alert to Telegram, and wait for `/approve` or UI confirmation.

---

## 2. Handover Instructions for the Incoming Gemini Pro Assistant

When opening this codebase in a fresh session with a new Gemini Pro subscription on Debian 13, paste the following prompt to initialize the agent:

```markdown
Read `docs/DEBIAN_AND_STUDIO_CONTINUATION_GUIDE.md` completely. 
You are acting as the Lead Production Engineer and Autonomous Workflow Architect for Mar & Kuku Production Studio on Bare Debian 13 Linux.
All production standards are strict 16:9 (1920x1080 @ 30fps). Follow all architectural patterns, OS compatibility abstractions in `src/mk_studio/os_compat.py`, and Telegram gateway routines in `src/mk_studio/telegram/bot.py`.
Report your understanding of the current system state, confirm you have read the guide, and await my instructions.
```

---

## 3. Quick-Start Guide: Setting Up on Bare Debian 13 Linux

Follow these exact steps after setting up your fresh Debian 13 installation:

### Step 3.1: Install System Dependencies
Open a root/sudo terminal:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    git \
    python3 \
    python3-venv \
    python3-pip \
    ffmpeg \
    chromium \
    curl \
    jq \
    wmctrl \
    xdotool \
    libnss3 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2
```

### Step 3.2: Clone Repository & Setup Virtual Environment
```bash
cd ~
git clone https://github.com/up421/mar-kuku-production-studio.git
cd mar-kuku-production-studio

# Create Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install package in editable mode with development dependencies
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

### Step 3.3: Configure Production & Media Paths
On Linux, create your dedicated production workspace:
```bash
mkdir -p ~/ProductionStudio/episodes ~/ProductionStudio/output ~/ProductionStudio/drafts
```
You can set environment variables in `~/.bashrc` or `~/.profile`:
```bash
export MK_STUDIO_OUTPUT_DIR="$HOME/ProductionStudio/output"
export MK_STUDIO_EPISODES_DIR="$HOME/ProductionStudio/episodes"
export CHROME_BIN="/usr/bin/chromium"
```

### Step 3.4: Configure CapCut Desktop on Debian Linux
CapCut Desktop runs on Linux seamlessly via Wine/Proton, Bottles, or native Chromium web app.
By default, the Studio checks:
- Linux Drafts Directory: `~/.local/share/CapCut/User Data/Projects/com.lveditor.draft/`
- Or an environment variable: `export CAPCUT_DRAFTS_DIR="/path/to/your/drafts"`

The Studio automatically generates compliant 16:9 `draft_content.json` and `draft_meta_info.json` project structures that load directly in CapCut!

### Step 3.5: Run Local AI with Ollama (100% Free Offline Option)
If you do not want to use paid cloud API tokens:
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b
# Ollama runs in background on port 11434. The Studio's provider dropdown can immediately connect to it!
```

### Step 3.6: Launch Chromium CDP Session (Browser Automation)
For YouTube Studio management and web-based AI orchestration:
```bash
chromium --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --no-first-run --user-data-dir=$HOME/.chrome-debug &
```

### Step 3.7: Launch Mar & Kuku Production Studio
```bash
source .venv/bin/activate
mk-studio serve --port 8767
```
Open your browser to: **`http://127.0.0.1:8767/`**

---

## 4. Mobile Telegram Two-Way Gateway Guide

You can run the entire studio from your mobile phone while away from your desk.

### Active Configuration:
- **Bot Username:** `@mar_kuku_studio_bot`
- **Config Storage:** `data/telegram/config.json`
- **Owner Chat ID:** Stored securely upon connection (configured via UI modal or `/telegram` command).

### Available Mobile Commands (Send to `@mar_kuku_studio_bot`):
| Telegram Command | Action |
| :--- | :--- |
| `/status` | Returns active production episode, running jobs, and system health. |
| `/approve` or `approve` | Approves pending gate (e.g. script wordings, video render, comment replies). |
| `/resume` or `resume` | Resumes a blocked or paused pipeline from its last checkpoint. |
| `/stop` or `stop` | Emergency halt: cancels all background rendering and tasks immediately. |
| `/comments` | Dispatches pending YouTube comments and draft AI replies to your phone. |
| `/analytics` | Delivers an executive daily digest of YouTube views, watch time, and CTR. |
| Any custom text | Forwarded directly into Studio Production Chat for AI multi-agent processing. |

---

## 5. Chronological Summary: Everything Completed to Date

Here is the complete inventory of features, bug fixes, and infrastructure completed across the project:

### Phase 1: Core Engine, Chapter 01 Verification & Technical Proof
- **Audio Verification Pipeline (`cmd_verify_ch01`):** Verified 48kHz audio duration (`30.220000s`, `1,450,560` samples), response hold stillness gap (`frames 501..621`), native 5-track Palmier project structure, and 907-frame MP4 decode verification.
- **FFmpeg Sub-frame Calibration:** Precision trimming and audio repair algorithms (`apply_audio_repair`) maintaining pedagogical rhythm.
- **Isolated Protected Workspace:** Set strict root policies isolating production assets from system directories.

### Phase 2: Web Studio Dashboard (FastHTML / Starlette)
- **Unified 3-Pane Interface:**
  - **Left Sidebar:** Active Workflow status, step-by-step progress checklist, AI Providers monitor, and Historical Workflows archive.
  - **Center Stage:** 6-Tab workspace featuring:
    1. *Production Chat:* Live conversation with model switching and expandible prompt composer.
    2. *Live Workflow Tracker:* Step-by-step status indicators with instant "Run" triggers.
    3. *Files & Asset Explorer:* Dual-view explorer for episode directories and assets with image preview modal.
    4. *Live Browser View:* Chrome CDP live screenshot inspector with automated polling.
    5. *Niche Blueprints & Memory:* Multi-niche blueprint creator (Toddler learning, STEM, Bedtime stories) and persistent memory ledger.
    6. *Scheduled Tasks (Cron):* Background cron scheduler interface for automated weekly/daily jobs.
  - **Right Sidebar:** Live Browser & Activity logs, background job monitor, and Mac/Linux control audit stream.

### Phase 3: Multi-Agent Collaboration Engine
- **3-Agent Live Panel:** Clickable sidebar inspector detailing the exact roles and prompts of:
  1. *ChatGPT Web Session:* Master Creative Director & Script Author.
  2. *Google Gemini Web Session:* Pedagogical QA, Toddler Developmental Reviewer & Safety Gate.
  3. *Manus Web Session:* Precision Timing, Phonetics & Rhythm Specialist.
- **Provider Switching:** Instant runtime toggle between ChatGPT Web, Gemini Web, Manus Web, Ollama Local (Free offline), OpenAI Codex CLI, and REST APIs.

### Phase 4: CapCut Desktop Multi-Platform Video Engine
- **Decoupled Architecture:** Created `src/mk_studio/editors/capcut.py` (`CapCutAdapter`) enabling native 16:9 timeline assembly for CapCut Desktop without requiring Palmier Pro.
- **Project Structure Generation:** Programmatically builds `draft_content.json` (canvas 16:9, 1920x1080, 30fps, video/audio tracks) and `draft_meta_info.json`.
- **Automatic Multi-OS Draft Path Discovery:** Autodetects CapCut project directories on macOS Darwin, Debian 13 Linux, and Windows.

### Phase 5: Autonomous Channel Automation & Cron Scheduler
- **Background Cron Engine (`src/mk_studio/scheduler.py`):**
  1. `cron-weekly-full-production`: Weekly end-to-end 16:9 episode generation (Script -> Flow Music -> Props -> Timeline -> MP4 -> YouTube Stage).
  2. `cron-daily-yt-review`: Daily YouTube comments manager and sentiment analyzer.
  3. `cron-daily-analytics-digest`: Daily YouTube Studio metrics (views, retention, CTR, subscriber delta) pushed to Telegram.
  4. `cron-weekly-tech-research`: Weekly topic and educational trend discovery.

### Phase 6: Telegram Mobile Gateway & Remote Approval
- **Two-Way Gateway (`src/mk_studio/telegram/bot.py`):**
  - Instant mobile push alerts on step completion or gate block.
  - Background polling thread with zero third-party dependencies (`urllib.request`).
  - Remote `/approve`, `/resume`, `/stop`, `/comments`, and `/analytics` command support.
  - In-app configuration modal and `/telegram <message>` studio chat command.

### Phase 7: UI Polish & Ergonomics
- Cleaned up top header navigation bar.
- Added dedicated `📊 Channel Analytics` tool button and `💬 Review Comments` button.
- Restored full reactivity to all dropdowns, action buttons, and modal dialogs.
- Fixed layout collapse issues on narrow viewports.

---

## 6. Codebase Architecture & Key Files

```
mar-kuku-production-studio/
├── data/
│   ├── cron/
│   │   └── jobs.json               # Persistent background cron task definitions
│   ├── memory/                     # Long-term studio memory & channel preferences
│   ├── skills/                     # Niche blueprints (Toddler, STEM, Stories)
│   └── telegram/
│       └── config.json             # Telegram Bot credentials and chat settings
├── docs/
│   ├── DEBIAN_AND_STUDIO_CONTINUATION_GUIDE.md  # THIS MASTER GUIDE
│   ├── implementation/             # Granular architectural reports & proofs
│   └── MK_Production_Studio_Requirements_v001.md
├── src/mk_studio/
│   ├── cli.py / cli_app.py         # Terminal commands (serve, verify, assemble)
│   ├── config.py                   # Central paths, timeouts, and video dimensions
│   ├── delivery.py                 # YouTube Studio CDP browser automation
│   ├── os_compat.py                # Cross-platform OS abstraction (Linux/macOS/Win)
│   ├── production/                 # Audio calibration, asset intake, FFmpeg QA
│   │   ├── pipeline.py             # Full episode staging orchestrator
│   │   └── verification.py         # Sub-frame RMS stillness QA
│   ├── editors/
│   │   ├── capcut.py               # CapCut Desktop 16:9 project compiler
│   │   └── palmier.py              # Palmier Pro timeline compiler
│   ├── scheduler.py                # Cron daemon engine
│   ├── skills/
│   │   └── manager.py              # Multi-channel niche blueprints & memory
│   ├── studio/
│   │   ├── server.py               # Starlette/FastHTML web app, API & WebSockets
│   │   └── workflow.py             # 9-step production state machine & manifests
│   └── telegram/
│       └── bot.py                  # Mobile Telegram 2-way bot gateway
└── tests/                          # Automated unit and integration test suite
```

---

## 7. Recommended Next Steps for the Future

When you are ready to expand the studio further on Debian 13, here are the most impactful enhancements:
1. **Direct YouTube Data API v3 Fallback:** While the CDP browser automation currently manages YouTube Studio directly, adding direct API key/OAuth support for YouTube Data API v3 will provide a secondary headless fallback for analytics queries.
2. **Local TTS/Voiceover Engine via Coqui / Kokoro:** Add local neural voiceover generation for character voices (Mar and Kuku) running 100% on CPU/GPU without cloud voice tokens.
3. **Automated Subtitle Styler (SRT / ASS):** Automatically generate bouncing animated karaoke word-by-word subtitles onto 16:9 videos.
4. **Cloud / VPS Headless Deployment:** With Debian 13 compatibility verified, the studio can easily run inside a Docker container or cheap Hetzner/Linode VPS with a systemd service.

---
*End of Master Continuation Guide. Happy Creating with Mar & Kuku Studio!*
