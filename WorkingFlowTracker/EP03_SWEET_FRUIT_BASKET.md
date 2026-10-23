# Episode Production Workflow Tracker: EP03_SWEET_FRUIT_BASKET

## Episode Profile
- **Episode ID:** `EP03_SWEET_FRUIT_BASKET`
- **Title:** Mar & Kuku: The Sweet Fruit Basket
- **Cast:** Strictly **ONLY Mar** (curious toddler host) and **Kuku** (playful chick companion). No other characters.
- **Target Audience:** Toddlers (ages 2–4).
- **Format:** CoComelon-style repetitive musical nursery song (30.00s / 900 frames @ 30fps).
- **Core Learning Objective:** Color and fruit naming (Red Apple, Yellow Banana, Blue Berries).
- **Interactive Stillness Hold:** Exact 4.0-second silence window (`[16.70s..20.70s]` / frames `501..621`) for toddlers to shout their answer before Kuku confirms.
- **Current Status:** `ready_to_start`

---

## Production Execution Plan

### Step 1: Multi-Agent Script Consultation
- **Status:** `[ ] Pending`
- **Primary AI Agent:** ChatGPT Web (`https://chatgpt.com/`) as Master Producer
- **Reviewer Agent:** Google Gemini Web (`https://gemini.google.com/app`)
- **Specialist Agent:** Manus Web (`https://manus.im/app`)
- **Prompt:**
  ```text
  Draft Episode 003 'The Sweet Fruit Basket' featuring strictly Mar and Kuku (no third characters). The episode must teach three colors through fruits: Red Apple, Yellow Banana, and Blue Berries. Structure as a catchy 30-second nursery melody with a prominent 4.0-second interactive question pause ('What color is this apple? ... [4.0s pause] ... RED!'). Ensure Kuku responds with energetic toddler chirps and giggles.
  ```
- **Expected Artifact:** `proposed_script_v001.md` saved in `scratch/ch01-proof/episodes/EP03_SWEET_FRUIT_BASKET/`

---

### Step 2: Google Flow Music Audio Generation
- **Status:** `[ ] Pending`
- **Tool:** Google Flow Music (`https://www.flowmusic.app/`)
- **Action:** Open reference session (`Nine Birds in the Sky`), click **Three Dots → Remix → Start Session**.
- **Prompt:**
  ```text
  Upbeat acoustic toddler song with Mar and Kuku, CoComelon nursery tempo 120bpm. Cheerful acoustic guitar, light bells, bright toddler female vocal for Mar, and playful chirpy vocal ad-libs for Kuku. Song: 'The Sweet Fruit Basket'. Verse 1: Red apple sweet! Verse 2: Yellow banana treat! Interactive pause hold for 4 seconds, followed by Blue berries pop! Ending on a joyful giggle. High fidelity, broadcast children's music production.
  ```
- **Expected Artifacts:**
  - `EP03_Candidate_A.wav` (48kHz 16-bit Stereo)
  - `EP03_Candidate_B.wav` (48kHz 16-bit Stereo)
  - Recorded SHA-256 hashes & session URLs in manifest

---

### Step 3: Transcription, Comparative Review & Timing Repair
- **Status:** `[ ] Pending`
- **Specialist:** Manus Web (`https://manus.im/app`) for timestamped transcription of Candidates A and B.
- **Reviewer:** ChatGPT Web (`https://chatgpt.com/`) for comparative lyric accuracy and audio quality review.
- **Human Owner Checkpoint:**
  - [ ] Owner listens to Candidates A and B in Studio dashboard.
  - [ ] Owner confirms candidate selection and wording reconciliation.
- **Timing Repair:**
  - Measure silence gap between Mar's question and Kuku's answer.
  - Apply FFmpeg trim if necessary to calibrate the interactive hold to exactly 4.00s.
- **Expected Artifact:** `EP03_Audio_Master_Approved_v001.wav`

---

### Step 4: Asset Assembly & Native Palmier MP4 Export
- **Status:** `[ ] Pending`
- **Video Editor:** Palmier Pro via local MCP server (`127.0.0.1:19789/mcp`)
- **Visual Assets:**
  - Master Mar & Kuku character transparent PNGs
  - Vector fruit props (Red Apple, Yellow Banana, Blue Berries)
  - Color Garden background
- **Timeline Specs:** 1920x1080 @ 30fps, 5 tracks, 900 total frames.
- **Render Output:** `EP03_The_Sweet_Fruit_Basket_1080p30_v001.mp4`
- **QA Gate:** Frame-by-frame RMS stillness test (frames 501..621) and audio synchronization check.

---

### Step 5: YouTube Studio Staging
- **Status:** `[ ] Pending`
- **Destination:** YouTube Studio (`https://studio.youtube.com/`)
- **Metadata Package:**
  - **Title:** Mar & Kuku: The Sweet Fruit Basket 🍎🍌 | Toddler Color Learning Song
  - **Description:** Join Mar and Kuku as they discover sweet fruits and bright colors! Can you guess the color of the apple before Kuku does?
  - **Tags:** Mar and Kuku, Kids Songs, Toddler Learning, Fruit Song, Colors for Toddlers
  - **Thumbnail:** `EP03_Thumbnail_1280x720.png`

