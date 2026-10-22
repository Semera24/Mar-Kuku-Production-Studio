"""Script-bound chapter production: real asset intake, voice, native editor, export QA."""
from __future__ import annotations

import json
import math
import shutil
import threading
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import Field, model_validator

from mk_studio.config import RootPolicy, compute_sha256, DEFAULT_FFMPEG_BIN, DEFAULT_FFPROBE_BIN
from mk_studio.contracts import StrictModel, ChapterSpec, AssetManifest
from mk_studio.registration import calibrate_manifest_assets
from mk_studio.scene import compile_scene
from mk_studio.media import probe_audio_stream_0a0
from mk_studio.runner import StepRunner, run_cancellable_subprocess
from mk_studio.state import StateStore
from mk_studio.editors.palmier import PalmierAdapter


class FrameRange(StrictModel):
    start_frame: int = Field(ge=0, strict=True)
    end_frame: int = Field(gt=0, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if self.end_frame <= self.start_frame:
            raise ValueError('Frame ranges are half-open [start,end) and require end > start')
        return self


class DialogueLine(FrameRange):
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ProductionBrief(StrictModel):
    script_path: str
    script_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    script_approved: bool = False
    chapter: ChapterSpec
    assets: AssetManifest
    dialogue: list[DialogueLine] = Field(default_factory=list)
    holds: list[FrameRange] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_timing_and_assets(self):
        c = self.chapter
        for ident in (c.episode_id, c.chapter_id):
            import re
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', ident):
                raise ValueError('Episode/chapter IDs must be filesystem-safe identifiers')
        if (c.fps_num, c.fps_den) != (30, 1) or c.total_frames <= 0:
            raise ValueError('This production path requires positive duration at 30/1 fps')
        if c.canvas_width != 1920 or c.canvas_height != 1080:
            raise ValueError('This production path currently exports 1920x1080')
        previous = 0
        for line in sorted(self.dialogue, key=lambda x: x.start_frame):
            if line.start_frame < previous:
                raise ValueError('Dialogue overlaps; this chapter path requires sequential speech')
            previous = line.end_frame
            for hold in self.holds:
                if line.start_frame < hold.end_frame and hold.start_frame < line.end_frame:
                    raise ValueError('Dialogue overlaps a silent audience-response hold')
        ranges = self.dialogue + self.holds + c.pose_spans + c.prop_spans + c.cues
        for span in ranges:
            if span.start_frame < 0 or span.end_frame < span.start_frame or span.end_frame > c.total_frames:
                raise ValueError('Frame range falls outside chapter duration')
        referenced = {c.background_asset_id, c.audio_asset_id} | {s.asset_id for s in c.pose_spans + c.prop_spans}
        if referenced - self.assets.assets.keys():
            raise ValueError(f'Missing assets: {sorted(referenced - self.assets.assets.keys())}')
        if self.assets.assets[c.background_asset_id].role != 'background' or self.assets.assets[c.audio_asset_id].role != 'audio_master':
            raise ValueError('Background/audio asset roles do not match chapter references')
        for key, asset in self.assets.assets.items():
            if key != asset.asset_id:
                raise ValueError('Asset map key must match asset_id')
        return self


class ProductionService:
    def __init__(self, policy: RootPolicy, *, cancel_event=None):
        self.policy = policy
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()

    def _run(self, command, timeout=120):
        return run_cancellable_subprocess([str(x) for x in command], cancel_event=self.cancel_event,
                                         timeout_seconds=timeout)

    def _write(self, path, value):
        path = self.policy.resolve_output(path, overwrite=True, create_parents=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(json.dumps(value, indent=2) + '\n')
        tmp.replace(path)
        return path

    def load_brief(self, path):
        return ProductionBrief.model_validate_json(self.policy.resolve_read(Path(path).absolute()).read_text())

    def draft(self, script_path, manifest_path, generate):
        """Ask the selected real provider for structured timing, binding paths locally."""
        script = self.policy.resolve_read(Path(script_path).absolute())
        assets = AssetManifest.model_validate_json(
            self.policy.resolve_read(Path(manifest_path).absolute()).read_text())
        for asset in assets.assets.values():
            self.policy.resolve_read(Path(asset.path), expected_sha256=asset.sha256)
        prompt = ('Convert the entire saved script into one ProductionBrief JSON object. Return JSON only. '
                  'Use only the supplied assets; report missing required artwork instead of inventing paths. '
                  'Use 30fps half-open ranges. Dialogue must not overlap silent holds. '
                  'Use the real audio duration/sample count if supplied; otherwise draft timing for later voice generation. '
                  'Set script_approved=false. Schema:\n' + json.dumps(ProductionBrief.model_json_schema()) +
                  '\nAvailable assets:\n' + assets.model_dump_json() + '\nScript:\n' + script.read_text())
        raw = generate(prompt)
        response = str(raw.get('response') or '').strip()
        if response.startswith('```'):
            response = response.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        payload = json.loads(response)
        payload.update(script_path=str(script), script_sha256=compute_sha256(script),
                       script_approved=False, assets=assets.model_dump())
        brief = ProductionBrief.model_validate(payload)
        import uuid
        folder = Path('drafts') / brief.chapter.episode_id / uuid.uuid4().hex
        path = self._write(folder / 'production-brief.json', brief.model_dump())
        self._write(path.with_name('provider-receipt.json'),
                    dict(provider=raw.get('provider_id'), url=raw.get('url'), response=response))
        return dict(status='draft_requires_review', brief_path=str(path),
                    script_path=str(script), script_sha256=brief.script_sha256)

    def approve(self, path):
        brief = self.load_brief(path)
        self.policy.resolve_read(Path(brief.script_path), expected_sha256=brief.script_sha256)
        brief.script_approved = True
        import hashlib
        digest = hashlib.sha256(brief.model_dump_json().encode()).hexdigest()
        approved = self._write(Path('approvals') / f'{digest}.json', brief.model_dump())
        return dict(status='approved', brief_path=str(approved), script_sha256=brief.script_sha256)

    def _source_script(self, brief):
        if not brief.script_approved:
            raise ValueError('Approve this exact script revision before preparing production')
        return self.policy.resolve_read(Path(brief.script_path), expected_sha256=brief.script_sha256)

    def prepare(self, brief: ProductionBrief):
        script = self._source_script(brief)
        import hashlib
        digest = hashlib.sha256(brief.model_dump_json().encode()).hexdigest()
        folder = self.policy.resolve_output(Path('production') / brief.chapter.episode_id /
                                            brief.chapter.chapter_id / digest[:20], overwrite=True)
        folder.mkdir(parents=True, exist_ok=True)
        receipt_path = folder / 'prepared.json'
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            # Verify original and copied sources on every run, including no-ops.
            for asset in brief.assets.assets.values():
                self.policy.resolve_read(Path(asset.path), expected_sha256=asset.sha256)
            for item in receipt['pinned_outputs']:
                self.policy.resolve_read(Path(item['path']), expected_sha256=item['sha256'])
            return receipt
        assets = brief.assets.model_copy(deep=True)
        provenance = []
        for key, asset in assets.assets.items():
            if self.cancel_event.is_set():
                raise RuntimeError('Production stopped before asset intake')
            source = self.policy.resolve_read(Path(asset.path), expected_sha256=asset.sha256)
            if asset.role != 'audio_master':
                with Image.open(source) as img:
                    img.verify()
                with Image.open(source) as img:
                    if asset.role in ('character_pose', 'prop') and ('A' not in img.getbands()):
                        raise ValueError(f'{key}: cutout needs an alpha channel')
            dest = self.policy.resolve_output(folder / 'assets' / f'{key}{source.suffix.lower()}',
                                              overwrite=True, create_parents=True)
            if dest.exists() and compute_sha256(dest) != asset.sha256:
                raise ValueError(f'Conflicting intake output: {dest}')
            if not dest.exists():
                shutil.copy2(source, dest)
            if compute_sha256(dest) != asset.sha256:
                raise ValueError('Asset changed during copy')
            provenance.append(dict(asset_id=key, source=str(source), source_sha256=asset.sha256, path=str(dest)))
            asset.path = str(dest)
        info = probe_audio_stream_0a0(Path(assets.assets[brief.chapter.audio_asset_id].path))
        if info.sample_rate != brief.chapter.audio_sample_rate or info.channels != 2:
            raise ValueError('Master audio must match chapter sample rate and be stereo')
        actual_samples = round(info.duration_seconds * info.sample_rate)
        if abs(actual_samples - brief.chapter.audio_total_samples) > 1:
            raise ValueError('Master audio sample count does not match chapter specification')
        if math.ceil(actual_samples / info.sample_rate * 30 - 1e-7) != brief.chapter.total_frames:
            raise ValueError('Chapter frames must cover the full master audio without truncation')
        calibration = calibrate_manifest_assets(self.policy, assets)
        # Freeze poses throughout each audience-response hold, splitting spans as needed.
        chapter = brief.chapter.model_copy(deep=True)
        from mk_studio.contracts import MotionProfileSpec
        frozen = []
        for span in chapter.pose_spans:
            boundaries = sorted({span.start_frame, span.end_frame} |
                                {n for h in brief.holds for n in (h.start_frame, h.end_frame)
                                 if span.start_frame < n < span.end_frame})
            for i, (a, b) in enumerate(zip(boundaries, boundaries[1:])):
                hold = any(h.start_frame <= a and b <= h.end_frame for h in brief.holds)
                frozen.append(span.model_copy(update=dict(span_id=f'{span.span_id}_{i}', start_frame=a,
                              end_frame=b, motion=MotionProfileSpec(mode='still_hold') if hold else span.motion)))
        chapter.pose_spans = frozen
        plan = compile_scene(chapter, assets, calibration)
        paths = [self._write(folder / 'asset-manifest.json', assets.model_dump()),
                 self._write(folder / 'registration.json', calibration.model_dump()),
                 self._write(folder / 'chapter-spec.json', chapter.model_dump()),
                 self._write(folder / 'timeline-plan.json', plan.model_dump()),
                 self._write(folder / 'production-brief.json', brief.model_dump())]
        script_copy = folder / 'approved-script.md'
        shutil.copy2(script, script_copy)
        pinned = [dict(path=str(p), sha256=compute_sha256(p)) for p in paths + [script_copy]]
        pinned.extend(dict(path=a.path, sha256=a.sha256) for a in assets.assets.values())
        result = dict(status='prepared', episode_id=chapter.episode_id, chapter_id=chapter.chapter_id,
                      input_sha256=digest, folder=str(folder), plan_path=str(paths[3]),
                      assets=assets.model_dump(), provenance=provenance, pinned_outputs=pinned,
                      audio=info.model_dump(), script_sha256=brief.script_sha256,
                      acceptance='technical_preparation_only; requires rendered and listening review')
        self._write(receipt_path, result)
        return result

    def generate_voice(self, brief: ProductionBrief, *, voices: dict[str, str] | None = None):
        """Actual local macOS TTS; never presented as Manus audio or the approved character voice."""
        self._source_script(brief)
        if not brief.dialogue:
            raise ValueError('Timecoded dialogue is required for voice generation')
        if not Path('/usr/bin/say').exists():
            raise RuntimeError('macOS local speech is unavailable; intake an external voice master instead')
        import uuid
        folder = self.policy.resolve_output(Path('voice') / brief.chapter.episode_id / brief.chapter.chapter_id /
                                            uuid.uuid4().hex, overwrite=True)
        folder.mkdir(parents=True)
        clips = []
        for i, line in enumerate(brief.dialogue):
            text_path = folder / f'{i:03d}.txt'
            text_path.write_text(line.text)
            aiff = folder / f'{i:03d}.aiff'
            command = ['/usr/bin/say', '-f', text_path, '-o', aiff, '-r', '115']
            if (voices or {}).get(line.speaker):
                command += ['-v', voices[line.speaker]]
            self._run(command)
            duration = probe_audio_stream_0a0(aiff).duration_seconds
            if duration > (line.end_frame - line.start_frame) / 30 + 0.001:
                raise ValueError(f'{line.speaker} line {i+1} exceeds its slot; revise timing instead of truncating speech')
            clips.append(aiff)
        output = folder / 'local_voice_master.wav'
        duration = brief.chapter.total_frames / 30
        cmd = [DEFAULT_FFMPEG_BIN, '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i',
               'anullsrc=r=48000:cl=stereo']
        for clip in clips:
            cmd += ['-i', clip]
        filters = []
        for i, line in enumerate(brief.dialogue):
            delay = line.start_frame * 1600
            filters.append(f'[{i+1}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={delay}S:all=1[a{i}]')
        filters.append('[0:a]' + ''.join(f'[a{i}]' for i in range(len(clips))) +
                       f'amix=inputs={len(clips)+1}:normalize=0:duration=first[out]')
        cmd += ['-filter_complex', ';'.join(filters), '-map', '[out]', '-t', str(duration),
                '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', output]
        self._run(cmd)
        result = brief.model_copy(deep=True)
        asset = result.assets.assets[result.chapter.audio_asset_id]
        asset.path, asset.sha256 = str(output), compute_sha256(output)
        result.chapter.audio_sample_rate = 48000
        result.chapter.audio_total_samples = brief.chapter.total_frames * 1600
        result.chapter.audio_duration_seconds = duration
        path = self._write(folder / 'production-brief.json', result.model_dump())
        return dict(status='generated', provider='macos_local_say', voice_approval='audition_required',
                    output_path=str(output), sha256=asset.sha256, brief_path=str(path))

    def render(self, brief: ProductionBrief):
        prepared = self.prepare(brief)
        folder = Path(prepared['folder'])
        from mk_studio.contracts import TimelinePlan
        plan = TimelinePlan.model_validate_json(Path(prepared['plan_path']).read_text())
        store = StateStore(self.policy.output_root_resolved / 'state.db')
        store.initialize_studio_schema()
        runner = StepRunner(store, self.policy.output_root_resolved / 'locks')
        adapter = PalmierAdapter(self.policy)
        project = folder / 'chapter.palmier'
        run_id = 'production-' + prepared['input_sha256'][:24]
        with runner.acquire_run_lock(), runner.acquire_editor_writer_lock():
            if self.cancel_event.is_set():
                raise RuntimeError('Production stopped before editor operation')
            store.create_run(run_id=run_id, episode_id=plan.episode_id, chapter_id=plan.chapter_id,
                             workflow_name='production-render', input_hash=prepared['input_sha256'])
            try:
                applied = adapter.apply_plan(plan, store=store, runner=runner, run_id=run_id,
                                             scratch_project_path=project, bootstrap_if_missing=True)
                if self.cancel_event.is_set():
                    raise RuntimeError('Production stopped after assembly; partial project retained')
                state = adapter.close_and_reopen(project)
                if not adapter.reconcile_plan(plan, state).is_noop:
                    raise RuntimeError('Native project differs from requested chapter after reopening')
                exported = adapter.export_video_mp4(folder / 'chapter_1080p30.mp4')
                output = self.policy.resolve_read(Path(exported.get('outputPath') or exported.get('path')))
                qa = self.verify_video(output, plan.total_frames)
                result = dict(status='exported', project_path=str(project), output_path=str(output),
                              sha256=compute_sha256(output), qa=qa, export_receipt=exported,
                              plan_id=plan.plan_id, assembly=applied,
                              creative_approval='awaiting_owner_watch_and_listen')
                self._write(folder / 'render-receipt.json', result)
                store.finish_run(run_id, status='succeeded')
                return result
            except Exception as exc:
                store.finish_run(run_id, status='failed', error_message=str(exc))
                raise

    def verify_video(self, path: Path, frames: int):
        path = self.policy.resolve_read(path)
        probe = json.loads(self._run([DEFAULT_FFPROBE_BIN, '-v', 'error', '-count_frames',
                                     '-show_streams', '-of', 'json', path]).stdout)
        video = next((s for s in probe['streams'] if s['codec_type'] == 'video'), {})
        audio = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), {})
        if (video.get('width'), video.get('height'), video.get('r_frame_rate'),
            int(video.get('nb_read_frames', 0))) != (1920, 1080, '30/1', frames):
            raise ValueError('Export resolution, frame rate or decoded frame count is incorrect')
        if audio.get('sample_rate') != '48000' or audio.get('channels') != 2:
            raise ValueError('Export must contain stereo 48kHz audio')
        self._run([DEFAULT_FFMPEG_BIN, '-v', 'error', '-xerror', '-nostdin', '-i', path,
                   '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], timeout=300)
        return dict(full_decode_passed=True, video_frames=frames, width=1920, height=1080,
                    video_duration=video.get('duration'), audio_duration=audio.get('duration'),
                    visual_and_listening_review='pending')
