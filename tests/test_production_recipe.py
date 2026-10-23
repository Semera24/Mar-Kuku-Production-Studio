"""Unit tests for ProductionRecipe and conditional feature pipeline toggles."""

import json
from pathlib import Path

from mk_studio.config import RootPolicy
from mk_studio.production.pipeline import generate_episode_assets
from mk_studio.production.recipe import (
    ProductionRecipe,
    create_default_recipe,
    load_recipe,
    save_recipe,
)


def test_recipe_lifecycle(tmp_path: Path):
    recipe = create_default_recipe(
        episode_id="EP_TEST_RECIPE",
        title="Test Song",
        overrides={"enable_lipsync": False, "interactive_hold_duration_s": 5.0},
    )
    assert recipe.enable_lipsync is False
    assert recipe.interactive_hold_duration_s == 5.0
    assert recipe.enable_karaoke_subtitles is True

    json_path, md_path = save_recipe(recipe, tmp_path)
    assert json_path.is_file()
    assert md_path.is_file()

    md_content = md_path.read_text(encoding="utf-8")
    assert "[ ] **Audio-Driven 2D Lip-Sync:**" in md_content
    assert "[x] **Bouncing Karaoke Subtitles:**" in md_content
    assert "`5.0` seconds" in md_content

    loaded = load_recipe(tmp_path)
    assert loaded.enable_lipsync is False
    assert loaded.interactive_hold_duration_s == 5.0


def test_pipeline_conditional_skipping(tmp_path: Path):
    policy = RootPolicy(output_root=tmp_path)
    episode_dir = tmp_path / "episodes" / "EP_SKIP_TEST"
    episode_dir.mkdir(parents=True)

    # Disable lip-sync and karaoke in the recipe
    recipe = create_default_recipe(
        episode_id="EP_SKIP_TEST",
        overrides={
            "enable_lipsync": False,
            "enable_karaoke_subtitles": False,
            "enable_thumbnail_ab_variants": False,
        },
    )
    save_recipe(recipe, episode_dir)

    receipt = generate_episode_assets("EP_SKIP_TEST", episode_dir, policy)
    assert receipt["status"] == "completed"

    # Verify that skipped features are reported as skipped in the receipt
    assert receipt["lipsync_manifest"] is None
    assert receipt["karaoke_subtitles"]["status"] == "skipped"
    assert receipt["thumbnail_variants"]["status"] == "skipped"
