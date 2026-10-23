"""Master Production Recipe & Conditional Feature Pipeline Configuration.

Defines the project blueprint and feature toggles for each episode:
- Aspect Ratio, Resolution, and FPS standards
- Interactive stillness hold duration
- Feature toggles (Lip-sync, Karaoke Subtitles, A/B Thumbnails, CapCut, Palmier, Telegram, YouTube)
- Human-readable Markdown and machine-readable JSON formats
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ProductionRecipe(BaseModel):
    """Configurable master blueprint and feature toggles for an episode project."""

    recipe_version: str = "1.0.0"
    episode_id: str = "EP03_SWEET_FRUIT_BASKET"
    title: str = "Mar & Kuku: Sweet Fruit Basket!"
    target_audience: str = "Toddlers & Preschoolers (Ages 2-5)"
    theme: str = "Fruits, Colors & Counting Sing-Along"

    # Core Technical Specifications (Strict 16:9 Broadcast Standard)
    aspect_ratio: str = "16:9"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    audio_sample_rate: int = 48000
    interactive_hold_duration_s: float = 4.0

    # Conditional Feature Toggles (if-conditions evaluated at each pipeline step)
    enable_flow_music: bool = Field(default=True, description="Generate acoustic children song via Google Flow Music")
    enable_lipsync: bool = Field(default=True, description="Analyze 48kHz audio and generate 2D character mouth visemes")
    enable_karaoke_subtitles: bool = Field(default=True, description="Generate word-by-word bouncing karaoke subtitles (.ass/.srt)")
    enable_thumbnail_ab_variants: bool = Field(default=True, description="Generate 3 high-CTR 16:9 thumbnail variants with predicted CTR")
    enable_capcut_export: bool = Field(default=True, description="Assemble native CapCut Desktop 16:9 draft project")
    enable_palmier_export: bool = Field(default=True, description="Assemble native Palmier Pro 16:9 timeline")
    enable_telegram_notifications: bool = Field(default=True, description="Send mobile push alerts to Telegram gateway")
    enable_youtube_staging: bool = Field(default=True, description="Package video, metadata, and stage in YouTube Studio")
    burn_subtitles_into_mp4: bool = Field(default=False, description="Burn ASS subtitles directly into MP4 render")
    require_owner_approval_before_upload: bool = Field(default=True, description="Human-in-the-loop gate before publishing")

    def to_markdown(self) -> str:
        """Renders human-readable master instruction markdown document."""
        chk = lambda val: "[x]" if val else "[ ]"
        return f"""# 📋 Master Production Recipe: {self.title}

> **Episode ID:** `{self.episode_id}`  
> **Target Audience:** {self.target_audience}  
> **Theme:** {self.theme}  
> **Status:** Active Blueprint

---

## 1. Technical Standards (Non-Negotiable)
- **Format:** `{self.aspect_ratio}` Widescreen (`{self.width}x{self.height}` @ `{self.fps}` fps)
- **Audio Master:** `{self.audio_sample_rate}`Hz 16-bit PCM Stereo WAV
- **Interactive Call-and-Response Hold:** `{self.interactive_hold_duration_s}` seconds (Frames 501..621)

---

## 2. Feature Pipeline Toggles (`if` conditions)
Every automated run evaluates these flags before executing each step. Uncheck or set to `false` to skip:

- {chk(self.enable_flow_music)} **Google Flow Music Audio:** Acoustic toddler music generation with Lyria 3.5.
- {chk(self.enable_lipsync)} **Audio-Driven 2D Lip-Sync:** 30fps RMS energy waveform analysis & viseme mouth flaps.
- {chk(self.enable_karaoke_subtitles)} **Bouncing Karaoke Subtitles:** Synchronized word-by-word highlight (.ass/.srt) + CapCut text track.
- {chk(self.enable_thumbnail_ab_variants)} **16:9 Thumbnail A/B Studio:** 3 High-CTR variants (Sunshine, Sky Play, Royal Violet).
- {chk(self.enable_capcut_export)} **CapCut Desktop Assembly:** Native 16:9 `draft_content.json` multi-track project.
- {chk(self.enable_palmier_export)} **Palmier Pro Timeline:** Native `.palmier` project structure and markers.
- {chk(self.enable_telegram_notifications)} **Mobile Telegram Push:** Instant step notifications & interactive approval gates.
- {chk(self.enable_youtube_staging)} **YouTube Studio Staging:** Metadata, tags, thumbnail, and caption staging via CDP.
- {chk(self.burn_subtitles_into_mp4)} **Burn Subtitles to MP4:** Hardcode karaoke subtitles into final video render.
- {chk(self.require_owner_approval_before_upload)} **Owner Approval Gate:** Require `/approve` or UI click before final delivery.

---

## 3. Step-by-Step Production Checklist
1. [ ] **Step 1:** Multi-Agent Script Consultation (ChatGPT Master, Gemini QA, Manus Specialist)
2. [ ] **Step 2:** Music Generation (Flow Music Acoustic candidate WAVs)
3. [ ] **Step 3:** Asset Intake, 2D Lip-Sync Visemes & Teaching Props
4. [ ] **Step 4:** Multi-Track Timeline Assembly (CapCut Desktop & Palmier Pro)
5. [ ] **Step 5:** 1080p30 Native MP4 Export & Sub-frame Stillness QA
6. [ ] **Step 6:** 16:9 Thumbnail A/B Variants & YouTube Studio Staging
"""


def create_default_recipe(
    episode_id: str,
    title: str = "",
    overrides: dict[str, Any] | None = None,
) -> ProductionRecipe:
    """Creates a new ProductionRecipe with default studio standards and optional overrides."""
    data: dict[str, Any] = {
        "episode_id": episode_id,
        "title": title or f"Mar & Kuku: {episode_id}",
    }
    if overrides:
        data.update(overrides)
    return ProductionRecipe.model_validate(data)


def load_recipe(episode_folder: Path, episode_id: str = "") -> ProductionRecipe:
    """Loads 00_production_recipe.json if present; otherwise creates and saves default."""
    json_path = episode_folder / "00_production_recipe.json"
    if json_path.is_file():
        try:
            return ProductionRecipe.model_validate_json(json_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Fallback to default
    recipe = create_default_recipe(episode_id or episode_folder.name)
    save_recipe(recipe, episode_folder)
    return recipe


def save_recipe(recipe: ProductionRecipe, episode_folder: Path) -> tuple[Path, Path]:
    """Saves both 00_production_recipe.json and 00_PRODUCTION_RECIPE.md in the episode folder."""
    episode_folder.mkdir(parents=True, exist_ok=True)
    json_path = episode_folder / "00_production_recipe.json"
    md_path = episode_folder / "00_PRODUCTION_RECIPE.md"

    json_path.write_text(recipe.model_dump_json(indent=2) + "\n", encoding="utf-8")
    md_path.write_text(recipe.to_markdown(), encoding="utf-8")
    return json_path, md_path
