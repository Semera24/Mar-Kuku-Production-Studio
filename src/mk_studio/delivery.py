"""Observed YouTube Studio browser delivery. Unknown outcomes are never auto-retried."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from pydantic import Field
from mk_studio.contracts import StrictModel
from mk_studio.config import RootPolicy, compute_sha256
from mk_studio.browser.controller import CdpWebSocketClient, BrowserCancelledError


class YouTubeMetadata(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=5000)
    made_for_kids: bool
    visibility: Literal['private', 'unlisted', 'public'] = 'private'
    tags: list[str] = Field(default_factory=list)
    thumbnail_path: str | None = None


# Return nodes through open shadow roots as well as the light DOM.
DEEP_QUERY = """function all(selector, root=document) {
 let out = Array.from(root.querySelectorAll(selector));
 for (const el of root.querySelectorAll('*')) if (el.shadowRoot) out.push(...all(selector, el.shadowRoot));
 return out;
}
function visible(el) { return el && !!el.getClientRects().length; }
"""


class YouTubeDelivery:
    def __init__(self, policy: RootPolicy, browser):
        self.policy, self.browser = policy, browser

    def _save(self, path, receipt):
        path = self.policy.resolve_output(path, overwrite=True, create_parents=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(receipt, indent=2) + '\n')
        tmp.replace(path)

    def _eval(self, ws, source):
        result = ws.call('Runtime.evaluate', {'expression': '(() => {' + DEEP_QUERY + source + '})()',
                                              'returnByValue': True})
        if result.get('exceptionDetails'):
            raise RuntimeError('YouTube Studio page action failed')
        return (result.get('result') or {}).get('value')

    def _wait(self, ws, source, seconds=60):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.browser._check_cancel()
            value = self._eval(ws, source)
            if value:
                return value
            time.sleep(0.5)
        raise RuntimeError('YouTube Studio did not reach the expected state; inspect the managed browser')

    def _file(self, ws, selector, path):
        obj = ws.call('Runtime.evaluate', {'expression': '(() => {' + DEEP_QUERY +
                      f'return all({json.dumps(selector)})[0] || null;}})()'})
        object_id = (obj.get('result') or {}).get('objectId')
        if not object_id:
            raise RuntimeError('YouTube file input unavailable; complete login or inspect the page')
        ws.call('DOM.setFileInputFiles', {'objectId': object_id, 'files': [str(path)]})

    def upload(self, video: Path, metadata: YouTubeMetadata, receipt_path: Path):
        video = self.policy.resolve_read(video.absolute())
        if not video.stat().st_size:
            raise ValueError('Cannot upload an empty video')
        receipt_path = self.policy.resolve_output(receipt_path, overwrite=True, create_parents=True)
        sha = compute_sha256(video)
        if receipt_path.exists():
            old = json.loads(receipt_path.read_text())
            if old.get('status') == 'uploaded' and old.get('video_sha256') == sha and old.get('metadata') == metadata.model_dump():
                return old
            raise RuntimeError('Upload has an uncertain or different prior outcome; reconcile YouTube Studio before retrying')
        # Independently decode and inspect the actual upload source.
        from mk_studio.production import ProductionService
        from mk_studio.config import DEFAULT_FFPROBE_BIN
        service = ProductionService(self.policy, cancel_event=self.browser.cancel_event)
        probe = json.loads(service._run([DEFAULT_FFPROBE_BIN, '-v', 'error', '-count_frames',
                                        '-select_streams', 'v:0', '-show_streams', '-of', 'json', video]).stdout)
        service.verify_video(video, int(probe['streams'][0]['nb_read_frames']))
        if metadata.tags:
            raise ValueError('Tag entry is not implemented in this browser adapter; omit tags for this delivery')
        thumbnail = self.policy.resolve_read(Path(metadata.thumbnail_path).absolute()) if metadata.thumbnail_path else None
        self.browser._require_not_in_takeover()
        self.browser.open_url('https://studio.youtube.com/', wait_seconds=12)
        target = self.browser._get_or_create_page_target()
        receipt = dict(status='preparing', video_path=str(video), video_sha256=sha,
                       metadata=metadata.model_dump(), last_observed_url='https://studio.youtube.com/')
        self._save(receipt_path, receipt)
        try:
            with CdpWebSocketClient(target['webSocketDebuggerUrl']) as ws:
                self._wait(ws, "const b=all('#create-icon, #create-button').find(visible); if(b){b.click();return true;} return false;")
                self._wait(ws, "const b=all('tp-yt-paper-item, ytcp-text-menu li, a').find(e=>visible(e)&&/upload videos/i.test(e.innerText)); if(b){b.click();return true;} return false;")
                self._wait(ws, "return all('input[type=file]').length>0;")
                # Persist intent BEFORE the first operation that can create a server-side video.
                receipt['status'] = 'submitting'
                self._save(receipt_path, receipt)
                self._file(ws, 'input[type=file]', video)
                self._wait(ws, "return all('ytcp-social-suggestions-textbox #textbox, #title-textarea #textbox').some(visible);", 180)
                fields = json.dumps([metadata.title, metadata.description])
                changed = self._eval(ws, f"""
 const fields=all('ytcp-social-suggestions-textbox #textbox').filter(visible);
 if(fields.length<2) return false;
 const values={fields};
 fields.slice(0,2).forEach((el,i)=>{{el.focus();el.textContent=values[i];el.dispatchEvent(new InputEvent('input',{{bubbles:true,inputType:'insertText',data:values[i]}}));el.dispatchEvent(new Event('change',{{bubbles:true}}));}});
 return fields[0].textContent===values[0] && fields[1].textContent===values[1];
""")
                if not changed:
                    raise RuntimeError('Could not verify title and description entry')
                kid_name = 'VIDEO_MADE_FOR_KIDS_MFK' if metadata.made_for_kids else 'VIDEO_MADE_FOR_KIDS_NOT_MFK'
                self._wait(ws, f"const el=all('tp-yt-paper-radio-button[name={kid_name}]').find(visible); if(el){{el.click();return true;}}return false;")
                if thumbnail:
                    self._file(ws, 'input[type=file][accept*="image"]', thumbnail)
                for _ in range(3):
                    self._wait(ws, "const b=all('#next-button').find(e=>visible(e)&&!e.hasAttribute('disabled')); if(b){b.click();return true;} return false;", 180)
                    time.sleep(1)
                self._wait(ws, f"const el=all('tp-yt-paper-radio-button[name={metadata.visibility.upper()}]').find(visible); if(el){{el.click();return true;}}return false;")
                self._wait(ws, f"return all('tp-yt-paper-radio-button[name={metadata.visibility.upper()}]').some(e=>e.hasAttribute('checked')||e.getAttribute('aria-checked')==='true');")
                # The owner explicitly chooses visibility; public is never selected by default.
                self._wait(ws, "const b=all('#done-button').find(e=>visible(e)&&!e.hasAttribute('disabled')); if(b){b.click();return true;}return false;", 300)
                url = self._wait(ws, """
 const dialog=all('ytcp-video-share-dialog').find(visible);
 if(!dialog) return false;
 const a=all('a[href]',dialog).find(e=>/youtu\\.be\\/|youtube\\.com\\/watch\\?v=/.test(e.href));
 return a ? a.href : false;
""", 300)
                receipt.update(status='uploaded', video_url=url, visibility=metadata.visibility)
                self._save(receipt_path, receipt)
                return receipt
        except Exception as exc:
            receipt.update(status='needs_reconciliation', error=str(exc),
                           instruction='Inspect YouTube Studio drafts/uploads; do not resubmit blindly.')
            self._save(receipt_path, receipt)
            raise
