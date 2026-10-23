"""Unit tests for CapCut Desktop multi-platform adapter."""

from pathlib import Path
from mk_studio.config import RootPolicy
from mk_studio.editors.capcut import CapCutAdapter


def test_capcut_draft_generation(tmp_path: Path):
    policy = RootPolicy(output_root=tmp_path / "out")
    adapter = CapCutAdapter(policy=policy)
    
    # Fake assets
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir(parents=True)
    audio_file = asset_dir / "candidate_a.wav"
    audio_file.write_bytes(b"RIFFdummydataWAVE")
    prop_file = asset_dir / "prop_apple.png"
    prop_file.write_bytes(b"\x89PNGdummy")
    
    output_dir = tmp_path / "out" / "draft_test"
    output_dir.mkdir(parents=True)
    
    receipt = adapter.assemble_episode_capcut(
        episode_id="EP_TEST_CAPCUT",
        output_dir=output_dir,
        audio_master_path=audio_file,
        props_dir=asset_dir,
    )
    
    assert receipt["status"] == "assembled"
    assert receipt["editor"] == "CapCut Desktop"
    assert "16:9" in receipt["aspect_ratio"]
    assert receipt["frame_rate"] == 30
    assert (output_dir / "07_capcut_editing_receipt.json").is_file()
    assert Path(receipt["project_path"]).is_dir()
    assert (Path(receipt["project_path"]) / "draft_content.json").is_file()
    assert (Path(receipt["project_path"]) / "draft_meta_info.json").is_file()
