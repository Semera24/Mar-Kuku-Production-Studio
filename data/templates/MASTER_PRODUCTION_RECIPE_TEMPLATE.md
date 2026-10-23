# 📋 Universal Master Production Recipe & Pipeline Template

> **Mar & Kuku Production Studio — Project Initialization Standard**  
> **Applicable Environment:** Bare Debian 13 Linux, macOS, and Multi-Platform Studio.  
> **Rule:** Every new episode project automatically inherits this master recipe as `00_PRODUCTION_RECIPE.md` and `00_production_recipe.json`.

---

## 1. Technical Standards
- **Aspect Ratio:** `16:9` Widescreen (`1920x1080` @ `30` fps)
- **Audio Master:** `48,000` Hz, 16-bit PCM Stereo WAV
- **Interactive Response Stillness Hold:** `4.00` seconds (Sub-frame FFmpeg precision)

---

## 2. Feature Pipeline Toggles (`if` Conditions)
Before executing any step in `run_full_production_pipeline` or background cron, the engine reads these switches. Toggle to `false` in `00_production_recipe.json` to omit any feature for a specific project:

| Feature Toggle | Default | Step Evaluated | Behavior When `False` |
| :--- | :--- | :--- | :--- |
| `enable_flow_music` | `true` | Step 2 (Make Music) | Skips Google Flow Music remix; expects external WAV intake. |
| `enable_lipsync` | `true` | Step 3 (Prepare Props) | Skips 30fps RMS audio viseme analysis; uses static character idle pose. |
| `enable_karaoke_subtitles` | `true` | Step 3 & Step 4 (Assembly) | Skips `.ass` `{\k...}` and `.srt` generation; omits text track in CapCut. |
| `enable_thumbnail_ab_variants` | `true` | Step 3 & Step 6 (YouTube Stage) | Skips generating 3 variants; uses single standard thumbnail. |
| `enable_capcut_export` | `true` | Step 4 (Assemble Video) | Skips generating CapCut `draft_content.json` project. |
| `enable_palmier_export` | `true` | Step 4 (Assemble Video) | Skips compiling native `.palmier` multi-track timeline. |
| `enable_telegram_notifications` | `true` | All Steps & Gates | Suppresses remote push alerts to `@mar_kuku_studio_bot`. |
| `enable_youtube_staging` | `true` | Step 6 (YouTube Stage) | Marks Step 6 as skipped; renders MP4 for local archive only. |
| `burn_subtitles_into_mp4` | `false` | Step 5 (Export MP4) | Leaves video clean without burned-in captions (YouTube CC used instead). |
| `require_owner_approval_before_upload` | `true` | Step 6 (YouTube Stage) | Enforces Telegram `/approve` or UI confirmation before release. |

---

## 3. How to Override Toggles for a Specific Episode
In your episode directory (e.g. `episodes/EP03_SWEET_FRUIT_BASKET/`):
1. Open `00_production_recipe.json`.
2. Change any toggle, for example:
   ```json
   {
     "enable_lipsync": false,
     "enable_karaoke_subtitles": true,
     "interactive_hold_duration_s": 5.0
   }
   ```
3. Run or resume the pipeline — the Studio will automatically obey your new rules!
