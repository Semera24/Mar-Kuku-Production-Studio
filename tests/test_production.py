"""Production acceptance checks use real media and synthetic chapter contracts."""
import json
import wave
from pathlib import Path

import pytest
from PIL import Image

from mk_studio.config import RootPolicy, compute_sha256
from mk_studio.production import ProductionBrief, ProductionService


@pytest.fixture
def brief(tmp_path):
    root = tmp_path / 'scratch'
    root.mkdir()
    script = root / 'script.md'
    script.write_text('MAR: Hello!\n[HOLD: frames 30..150)\n')
    image = root / 'background.png'
    Image.new('RGB', (1920, 1080), 'blue').save(image)
    audio = root / 'voice.wav'
    with wave.open(str(audio), 'wb') as w:
        w.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        w.writeframes(b'\x00' * 4 * 48000 * 5)
    asset = lambda key, role, path: {'asset_id': key, 'role': role, 'path': str(path), 'sha256': compute_sha256(path)}
    return dict(script_path=str(script), script_sha256=compute_sha256(script), script_approved=True,
                chapter=dict(episode_id='TEST', chapter_id='CH02', title='Hello',
                    audio_asset_id='voice', audio_duration_seconds=5, audio_total_samples=240000,
                    total_frames=150, background_asset_id='bg', pose_spans=[], prop_spans=[], cues=[]),
                assets={'assets': {'bg': asset('bg', 'background', image), 'voice': asset('voice', 'audio_master', audio)}},
                dialogue=[dict(speaker='MAR', text='Hello!', start_frame=0, end_frame=30)],
                holds=[dict(start_frame=30, end_frame=150)])


def test_reject_dialogue_inside_silent_hold(brief):
    brief['dialogue'][0]['end_frame'] = 31
    with pytest.raises(ValueError, match='(?i)hold|overlap'):
        ProductionBrief.model_validate(brief)


def test_reject_changed_script_and_source(brief, tmp_path):
    service = ProductionService(RootPolicy(output_root=tmp_path / 'scratch'))
    Path(brief['script_path']).write_text('changed')
    with pytest.raises(Exception, match='(?i)sha|hash'):
        service.prepare(ProductionBrief.model_validate(brief))


def test_real_intake_compile_and_repeated_prepare(brief, tmp_path):
    service = ProductionService(RootPolicy(output_root=tmp_path / 'scratch'))
    b = ProductionBrief.model_validate(brief)
    first = service.prepare(b)
    again = service.prepare(b)
    assert first['input_sha256'] == again['input_sha256']
    assert first['status'] == 'prepared'
    assert Path(first['plan_path']).is_file()
    plan = json.loads(Path(first['plan_path']).read_text())
    assert plan['episode_id'] == 'TEST' and plan['chapter_id'] == 'CH02'
    assert plan['total_frames'] == 150
    for asset in first['assets']['assets'].values():
        assert Path(asset['path']).is_relative_to(service.policy.output_root_resolved)
        assert compute_sha256(asset['path']) == asset['sha256']


def test_no_approval_no_render_preparation(brief, tmp_path):
    brief['script_approved'] = False
    service = ProductionService(RootPolicy(output_root=tmp_path / 'scratch'))
    with pytest.raises(ValueError, match='(?i)approv'):
        service.prepare(ProductionBrief.model_validate(brief))


def test_out_of_range_frame_rejected(brief):
    brief['holds'][0]['end_frame'] = 151
    with pytest.raises(ValueError):
        ProductionBrief.model_validate(brief)


def test_asset_missing_cannot_be_reported_ready(brief):
    del brief['assets']['assets']['voice']
    with pytest.raises(ValueError, match='(?i)asset'):
        ProductionBrief.model_validate(brief)
