"""Failure-oriented consultation tests; no external website calls."""
import json
from pathlib import Path

import pytest

from mk_studio.config import RootPolicy
from mk_studio.studio.server import StudioOrchestrator


@pytest.fixture
def studio(tmp_path, monkeypatch):
    orch = StudioOrchestrator(RootPolicy(output_root=tmp_path / 'scratch'))
    prompts = []
    shot = tmp_path / 'shot.png'
    shot.write_bytes(b'test screenshot')
    def reply(prompt, **kwargs):
        prompts.append(prompt)
        return {'status': 'succeeded', 'response': f'Complete output {len(prompts)}: {prompt}',
                'url': 'https://example.test/conversation/one', 'authenticated': True,
                'screenshot': {'path': str(shot), 'sha256': 'test'}}
    for provider in ('chatgpt', 'gemini', 'manus'):
        monkeypatch.setattr(orch.browser, f'consult_{provider}_web', reply)
    return orch, prompts


def test_empty_reviewer_never_completes(studio, monkeypatch):
    orch, _ = studio
    monkeypatch.setattr(orch.browser, 'consult_gemini_web', lambda *a, **k: {
        'status': 'awaiting_owner_login', 'response': '', 'url': 'https://gemini.google.com/app'})
    with pytest.raises(Exception, match='(?i)gemini|login|empty'):
        orch.workflow.run_or_resume_consultation('Research animals', episode_id='ANIMALS')
    m = orch.workflow.load_manifest('ANIMALS')
    assert m['status'] != 'completed'
    assert m['steps']['step_2_gemini_review']['status'] != 'completed'


def test_successive_corrections_preserve_latest_and_history(studio):
    orch, prompts = studio
    orch.workflow.run_or_resume_consultation('Animals', episode_id='ANIMALS')
    a = orch.workflow.apply_followup_correction('Add a cat', episode_id='ANIMALS')
    old = Path(a['current_script_path']).read_text()
    rev = Path(a['steps']['step_5_followup_correction']['script_file'])
    b = orch.workflow.apply_followup_correction('Add a dog too', episode_id='ANIMALS')
    assert b['current_script_version'] == 'v003'
    assert rev.read_text() == old
    assert any(old in p for p in prompts[-2:])
    assert [r['version'] for r in b['script_revisions']] == ['v001', 'v002', 'v003']


def test_interrupted_submission_not_blindly_repeated(studio, monkeypatch):
    orch, _ = studio
    calls = []
    def uncertain(*a, **k):
        calls.append(1)
        raise TimeoutError('response lost after submission')
    monkeypatch.setattr(orch.browser, 'consult_chatgpt_web', uncertain)
    for _ in range(2):
        with pytest.raises(Exception):
            orch.workflow.run_or_resume_consultation('Animals', episode_id='ANIMALS')
    assert len(calls) == 1
    m = orch.workflow.load_manifest('ANIMALS')
    assert m['steps']['step_1_chatgpt_master_plan']['status'] == 'uncertain_outcome'


def test_different_episode_preserves_owner_subject(studio):
    orch, prompts = studio
    orch.workflow.run_or_resume_consultation('Learn ocean animals', episode_id='OCEAN')
    assert 'counting 1 to 3 garden flowers' not in prompts[0]
    assert 'ocean animals' in prompts[0]
    assert not orch.workflow.manifest_path('SPACE').read_text() if orch.workflow.manifest_path('SPACE').exists() else True
