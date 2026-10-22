"""Resumable 'idea -> three-agent consultation -> saved script' workflow orchestrated from Studio chat."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mk_studio.browser.controller import (
    BrowserCancelledError,
    BrowserControlError,
    BrowserController,
    BrowserTakeoverActiveError,
)
from mk_studio.config import RootPolicy, compute_sha256
from mk_studio.providers.registry import ProviderRegistry
from mk_studio.state import StateStore


DEFAULT_TEST_EPISODE_ID = "EP03_COUNTING_FLOWERS"
WorkflowStoppedError = BrowserCancelledError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_chat_instruction(message: str) -> str | None:
    """Classify a natural-language user message in Studio chat into an executable workflow action."""
    text = (message or "").strip().lower()
    if not text:
        return None
    if text in ("stop", "cancel", "halt", "stop workflow", "cancel workflow", "stop the workflow"):
        return "stop_workflow"
    if text in (
        "resume",
        "continue",
        "resume workflow",
        "continue workflow",
        "retry workflow",
        "resume the workflow",
        "continue the workflow",
    ):
        return "resume_workflow"
    if (
        text.startswith("correction:")
        or text.startswith("follow-up correction:")
        or text.startswith("followup correction:")
        or text.startswith("revise:")
        or text.startswith("update script:")
        or ("correction" in text and ("script" in text or "flower" in text or "kuku" in text or "mar" in text or "pause" in text))
        or ("revise" in text and "script" in text)
        or ("change" in text and "script" in text)
    ):
        return "followup_correction"
    if (
        ("research" in text or "brainstorm" in text or "idea" in text or "propose" in text or "create" in text)
        and ("episode" in text or "script" in text or "mar" in text or "kuku" in text)
        and ("gemini" in text or "manus" in text or "consult" in text or "agent" in text or "script" in text)
    ):
        return "consultation_workflow"
    if "asset" in text or "intake" in text or "voiceover" in text or "prop" in text:
        return "generate_assets"
    if "palmier" in text or "timeline" in text or "assemble" in text:
        return "assemble_palmier"
    if "export" in text or "render" in text or "mp4" in text or "video" in text:
        return "export_mp4"
    if "youtube" in text or "upload" in text or "stage" in text:
        return "stage_youtube"
    if "pipeline" in text or "produce" in text or "full" in text:
        return "full_pipeline"
    return None


class StudioWorkflowEngine:
    """Checkpoint remote submissions before sending; never guess uncertain outcomes."""

    STEP_NAMES = (
        'step_1_chatgpt_master_plan',
        'step_2_gemini_review',
        'step_3_manus_specialist',
        'step_4_chatgpt_synthesis',
        'step_5_followup_correction',
        'step_6_asset_generation_and_intake',
        'step_7_palmier_editing',
        'step_8_final_mp4_export',
        'step_9_youtube_studio_upload',
    )

    def __init__(self, policy, store, browser, providers, *, cancel_event=None):
        self.policy, self.store, self.browser, self.providers = policy, store, browser, providers
        self.cancel_event = cancel_event if cancel_event is not None else browser.cancel_event
        self._lock = threading.Lock()

    def get_episode_folder(self, episode_id=DEFAULT_TEST_EPISODE_ID):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', episode_id):
            raise ValueError('Episode ID must contain only letters, numbers, underscores or hyphens')
        folder = self.policy.resolve_output(Path('episodes') / episode_id, overwrite=True)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def manifest_path(self, episode_id=DEFAULT_TEST_EPISODE_ID):
        return self.get_episode_folder(episode_id) / 'output_manifest.json'

    def load_manifest(self, episode_id=DEFAULT_TEST_EPISODE_ID):
        path = self.manifest_path(episode_id)
        if path.exists():
            return json.loads(path.read_text())  # Corruption must not silently create a new run.
        return {'workflow_id': f'wf-{episode_id.lower()}', 'episode_id': episode_id,
                'dedicated_folder': str(path.parent), 'status': 'not_started',
                'initial_instruction': '', 'created_at': _utc_now(),
                'conversation_references': {}, 'steps': {k: {'status': 'pending', 'submission_count': 0}
                                                      for k in self.STEP_NAMES},
                'script_revisions': [], 'artifacts': []}

    def save_manifest(self, manifest, episode_id=DEFAULT_TEST_EPISODE_ID):
        path = self.manifest_path(episode_id)
        manifest['updated_at'] = _utc_now()
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(manifest, indent=2) + '\n')
        tmp.replace(path)
        self.store.record_artifact(episode_id=episode_id, chapter_id='CH01', role='workflow:output_manifest',
                                   path=str(path), sha256=compute_sha256(path), bytes_size=path.stat().st_size)
        return path

    def _step_already_completed(self, step):
        if step.get('status') != 'completed' or not step.get('output_file'):
            return False
        path = self.policy.resolve_read(Path(step['output_file']))
        if compute_sha256(path) != step.get('output_sha256'):
            raise BrowserControlError('Checkpoint output changed; review before resuming')
        payload = json.loads(path.read_text())
        return bool(str(payload.get('response') or payload.get('chatgpt_revised_script') or '').strip())

    def _check_stop(self, manifest, episode_id, point=''):
        if self.cancel_event.is_set():
            manifest.update(status='stopped', stopped_at_step=point)
            self.save_manifest(manifest, episode_id)
            raise BrowserCancelledError(f'Workflow stopped at {point}; remote generation may still be running')

    def _consult(self, manifest, key, site, prompt, conversation_id, conversation_url=None):
        episode_id = manifest['episode_id']
        step = manifest['steps'].setdefault(key, {'status': 'pending', 'submission_count': 0})
        if self._step_already_completed(step):
            return json.loads(Path(step['output_file']).read_text())
        if step.get('status') in ('submitting', 'uncertain_outcome'):
            raise BrowserControlError(f'{key}: uncertain_outcome; reconcile the recorded website conversation before retrying')
        self._check_stop(manifest, episode_id, key)
        step.update(status='submitting', submitted_at=_utc_now(), prompt=prompt,
                    submission_count=int(step.get('submission_count', 0)) + 1,
                    conversation_url=conversation_url)
        self.save_manifest(manifest, episode_id)
        try:
            result = getattr(self.browser, f'consult_{site}_web')(
                prompt, conversation_id=conversation_id, conversation_url=conversation_url,
                screenshot_name=f'{episode_id}_{key}.png')
        except BaseException:
            step['status'] = 'uncertain_outcome'
            manifest['status'] = 'needs_reconciliation'
            self.save_manifest(manifest, episode_id)
            raise
        response = str(result.get('response') or '').strip()
        if result.get('status') not in ('succeeded', 'passed', 'completed') or not response:
            step.update(status='awaiting_owner_login' if result.get('status') == 'awaiting_owner_login'
                        else 'invalid_response', provider_status=result.get('status'), url=result.get('url'))
            manifest['status'] = step['status']
            self.save_manifest(manifest, episode_id)
            raise BrowserControlError(f'{site}: {step["status"]}; a non-empty completed response is required')
        folder = self.get_episode_folder(episode_id)
        path = self.policy.resolve_output(folder / f'{key}.json', overwrite=True)
        payload = dict(result, response=response, prompt=prompt, completed_at=_utc_now())
        path.write_text(json.dumps(payload, indent=2) + '\n')
        path.with_suffix('.md').write_text(f'# {site}\n\n{response}\n')
        step.update(status='completed', output_file=str(path), output_sha256=compute_sha256(path),
                    url=result.get('url'), completed_at=_utc_now())
        self.save_manifest(manifest, episode_id)
        return payload

    def _save_script(self, manifest, response, *, correction=None):
        folder = self.get_episode_folder(manifest['episode_id'])
        revision = f'v{len(manifest["script_revisions"]) + 1:03d}'
        path = self.policy.resolve_output(folder / f'proposed_script_{revision}.md', create_parents=True)
        path.write_text(response + '\n')
        latest = self.policy.resolve_output(folder / 'proposed_script.md', overwrite=True)
        latest.write_text(path.read_text())
        manifest['script_revisions'].append(dict(version=revision, path=str(path), sha256=compute_sha256(path),
                                                  correction=correction, created_at=_utc_now()))
        manifest.update(current_script_version=revision, current_script_path=str(latest),
                        script_readiness='draft_requires_structured_timing_validation')
        self.save_manifest(manifest, manifest['episode_id'])
        return str(path)

    def run_or_resume_consultation(self, instruction, *, episode_id=DEFAULT_TEST_EPISODE_ID,
                                  conversation_id='conv-e02-ch01-main', pause_on_manus_login=True,
                                  stop_after_step=None):
        if not self._lock.acquire(blocking=False):
            raise BrowserControlError('Another consultation is running; stop or wait before starting another')
        try:
            m = self.load_manifest(episode_id)
            if not m['initial_instruction']:
                m['initial_instruction'] = instruction
            m['status'] = 'running'
            self.save_manifest(m, episode_id)
            results = []
            refs = m['conversation_references']
            prompts = [
                ('chatgpt', 'You are Master Producer for Mar and Kuku. Follow the owner topic exactly: '
                 + m['initial_instruction'] + '\nGive EPISODE_CONCEPT, QUESTION_FOR_GEMINI, QUESTION_FOR_MANUS. '
                 'Respect approved character art. State timing as half-open [start,end) frame ranges at 30fps.'),
                ('gemini', None), ('manus', None), ('chatgpt', None)]
            ref_keys = ['chatgpt_master_url', 'gemini_reviewer_url', 'manus_specialist_url', 'chatgpt_synthesis_url']
            for i, (site, prompt) in enumerate(prompts):
                key = self.STEP_NAMES[i]
                if i == 1:
                    prompt = 'Review this proposed episode for preschool suitability, poses and timing. Answer the master review question:\n' + results[0]['response']
                elif i == 2:
                    prompt = 'Answer the master specialist question with speech pacing and exact silent hold timing. Do not claim audio is generated:\n' + results[0]['response']
                elif i == 3:
                    prompt = ('Synthesize this complete consultation into a draft production script. Resolve reviewer conflicts; '
                              'include dialogue, asset requirements and non-overlapping half-open frame ranges at 30fps. '
                              'A four-second hold is 120 frames with no dialogue. Do not invent approved assets.\n' +
                              '\n\n'.join(r['response'] for r in results))
                result = self._consult(m, key, site, prompt, conversation_id,
                                       refs.get('chatgpt_master_url') if i == 3 else None)
                results.append(result)
                refs[ref_keys[i]] = result.get('url', '')
                if i == 3 and not m.get('script_revisions'):
                    script = self._save_script(m, result['response'])
                    m['steps'][key]['script_file'] = script
                self.save_manifest(m, episode_id)
                if stop_after_step == key:
                    self.cancel_event.set()
                self._check_stop(m, episode_id, f'after_step_{i+1}')
            m.update(status='completed', last_error='')
            self.save_manifest(m, episode_id)
            return m
        finally:
            self._lock.release()

    def apply_followup_correction(self, correction_instruction, *, episode_id=DEFAULT_TEST_EPISODE_ID,
                                 conversation_id='conv-e02-ch01-main'):
        if not self._lock.acquire(blocking=False):
            raise BrowserControlError('Another consultation is running')
        try:
            m = self.load_manifest(episode_id)
            if not m.get('current_script_path'):
                raise BrowserControlError('Complete the initial consultation before correcting its script')
            # Retrying the exact latest correction is a no-op.
            if m['script_revisions'][-1].get('correction') == correction_instruction:
                return m
            revision = f'v{len(m["script_revisions"]) + 1:03d}'
            prev = self.policy.resolve_read(Path(m['script_revisions'][-1]['path']))
            if compute_sha256(prev) != m['script_revisions'][-1]['sha256']:
                raise BrowserControlError('Latest script revision changed outside the workflow')
            prompt = ('Revise the complete latest script, preserving previous owner corrections. '
                      'Use half-open [start,end) timing; resolve dialogue/hold overlaps. Owner correction:\n' +
                      correction_instruction + '\nLatest script:\n' + prev.read_text())
            gpt = self._consult(m, f'correction_{revision}_chatgpt', 'chatgpt', prompt, conversation_id,
                                m['conversation_references'].get('chatgpt_synthesis_url'))
            gem = self._consult(m, f'correction_{revision}_gemini', 'gemini',
                                'Review this complete corrected script. Report any remaining errors. Correction:\n' +
                                correction_instruction + '\nScript:\n' + gpt['response'], conversation_id)
            content = gpt['response'] + '\n\n## Gemini review\n\n' + gem['response']
            script = self._save_script(m, content, correction=correction_instruction)
            s5 = m['steps']['step_5_followup_correction']
            source = m['steps'][f'correction_{revision}_chatgpt']
            s5.update(source, script_file=script, script_v002_file=script,
                      submission_count=int(s5.get('submission_count', 0)) + 1)
            m['conversation_references']['chatgpt_followup_url'] = gpt.get('url')
            m.update(status='completed', last_error='')
            self.save_manifest(m, episode_id)
            return m
        finally:
            self._lock.release()

    def run_asset_generation(self, episode_id=DEFAULT_TEST_EPISODE_ID):
        from mk_studio.production.pipeline import generate_episode_assets
        m = self.load_manifest(episode_id)
        s6 = m['steps'].setdefault('step_6_asset_generation_and_intake', {'status': 'pending', 'submission_count': 0})
        self._check_stop(m, episode_id, 'before_step_6')
        folder = self.get_episode_folder(episode_id)
        res = generate_episode_assets(episode_id, folder, self.policy, self.store)
        out_json = folder / "06_asset_intake_manifest.json"
        s6.update(status='completed', output_file=str(out_json), output_sha256=compute_sha256(out_json),
                  submission_count=int(s6.get('submission_count', 0)) + 1, completed_at=_utc_now())
        self.save_manifest(m, episode_id)
        return m

    def run_palmier_assembly(self, episode_id=DEFAULT_TEST_EPISODE_ID):
        from mk_studio.production.pipeline import assemble_episode_palmier
        m = self.load_manifest(episode_id)
        s7 = m['steps'].setdefault('step_7_palmier_editing', {'status': 'pending', 'submission_count': 0})
        self._check_stop(m, episode_id, 'before_step_7')
        folder = self.get_episode_folder(episode_id)
        res = assemble_episode_palmier(episode_id, folder, self.policy, self.store)
        out_json = folder / "07_palmier_editing_receipt.json"
        s7.update(status='completed', output_file=str(out_json), output_sha256=compute_sha256(out_json),
                  submission_count=int(s7.get('submission_count', 0)) + 1, completed_at=_utc_now())
        self.save_manifest(m, episode_id)
        return m

    def run_final_mp4_export(self, episode_id=DEFAULT_TEST_EPISODE_ID):
        from mk_studio.production.pipeline import export_episode_mp4
        m = self.load_manifest(episode_id)
        s8 = m['steps'].setdefault('step_8_final_mp4_export', {'status': 'pending', 'submission_count': 0})
        self._check_stop(m, episode_id, 'before_step_8')
        folder = self.get_episode_folder(episode_id)
        res = export_episode_mp4(episode_id, folder, self.policy, self.store)
        out_json = folder / "08_export_qa_report.json"
        s8.update(status='completed' if res['status'] == 'completed' else 'failed',
                  output_file=str(out_json), output_sha256=compute_sha256(out_json),
                  submission_count=int(s8.get('submission_count', 0)) + 1, completed_at=_utc_now())
        self.save_manifest(m, episode_id)
        return m

    def run_youtube_upload_staging(self, episode_id=DEFAULT_TEST_EPISODE_ID, conversation_id='conv-e02-ch01-main'):
        from mk_studio.production.pipeline import package_and_stage_youtube_upload
        m = self.load_manifest(episode_id)
        s9 = m['steps'].setdefault('step_9_youtube_studio_upload', {'status': 'pending', 'submission_count': 0})
        self._check_stop(m, episode_id, 'before_step_9')
        folder = self.get_episode_folder(episode_id)
        res = package_and_stage_youtube_upload(episode_id, folder, self.policy, self.browser, conversation_id=conversation_id)
        out_json = folder / "09_youtube_upload_receipt.json"
        s9.update(status=res['status'], output_file=str(out_json), output_sha256=compute_sha256(out_json),
                  submission_count=int(s9.get('submission_count', 0)) + 1, completed_at=_utc_now(),
                  screenshot_path=res['browser_result']['screenshot']['path'])
        self.save_manifest(m, episode_id)
        return m

    def run_full_production_pipeline(self, episode_id=DEFAULT_TEST_EPISODE_ID, conversation_id='conv-e02-ch01-main', stop_after_step=None):
        m = self.load_manifest(episode_id)
        if not m.get('current_script_path'):
            raise BrowserControlError('Complete consultation and save script before running production pipeline')
        
        # Step 6
        if not self._step_already_completed(m['steps'].get('step_6_asset_generation_and_intake', {})):
            self._check_stop(m, episode_id, 'before_step_6')
            m = self.run_asset_generation(episode_id)
            if stop_after_step == 'step_6_asset_generation_and_intake':
                self.cancel_event.set()
            self._check_stop(m, episode_id, 'after_step_6')

        # Step 7
        if not self._step_already_completed(m['steps'].get('step_7_palmier_editing', {})):
            self._check_stop(m, episode_id, 'before_step_7')
            m = self.run_palmier_assembly(episode_id)
            if stop_after_step == 'step_7_palmier_editing':
                self.cancel_event.set()
            self._check_stop(m, episode_id, 'after_step_7')

        # Step 8
        if not self._step_already_completed(m['steps'].get('step_8_final_mp4_export', {})):
            self._check_stop(m, episode_id, 'before_step_8')
            m = self.run_final_mp4_export(episode_id)
            if stop_after_step == 'step_8_final_mp4_export':
                self.cancel_event.set()
            self._check_stop(m, episode_id, 'after_step_8')

        # Step 9
        if not self._step_already_completed(m['steps'].get('step_9_youtube_studio_upload', {})):
            self._check_stop(m, episode_id, 'before_step_9')
            m = self.run_youtube_upload_staging(episode_id, conversation_id=conversation_id)
            if stop_after_step == 'step_9_youtube_studio_upload':
                self.cancel_event.set()
            self._check_stop(m, episode_id, 'after_step_9')

        m['status'] = 'completed'
        self.save_manifest(m, episode_id)
        return m
