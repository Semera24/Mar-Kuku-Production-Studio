"""Browser generation and real-file intake, separate from text consultation success."""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from PIL import Image
from mk_studio.config import RootPolicy, compute_sha256
from mk_studio.browser.controller import CdpWebSocketClient


class BrowserAssetGenerator:
    URLS = {'chatgpt': 'https://chatgpt.com/', 'gemini': 'https://gemini.google.com/app',
            'manus': 'https://manus.im/app'}

    def __init__(self, policy: RootPolicy, browser):
        self.policy, self.browser = policy, browser

    def collect(self, path, *, role, provider, prompt, conversation_url=''):
        path = self.policy.resolve_read(Path(path).absolute())
        if not path.stat().st_size:
            raise ValueError('Generated asset is empty')
        if role in ('background', 'prop', 'character_pose'):
            with Image.open(path) as img:
                img.verify()
            with Image.open(path) as img:
                if role in ('prop', 'character_pose'):
                    if 'A' not in img.getbands() or img.getchannel('A').getextrema()[0] == 255:
                        raise ValueError('Cutout needs actual transparent alpha pixels')
                    if img.getchannel('A').getextrema()[1] == 0:
                        raise ValueError('Cutout is empty; no visible pixels')
        elif role == 'audio_master':
            from mk_studio.media import probe_audio_stream_0a0
            probe_audio_stream_0a0(path)
        else:
            raise ValueError('Unsupported asset role')
        return dict(status='downloaded_requires_review', path=str(path), sha256=compute_sha256(path),
                    bytes_size=path.stat().st_size, role=role, provider=provider, prompt=prompt,
                    conversation_url=conversation_url)

    def generate(self, *, provider, role, prompt, episode_id, timeout_seconds=180):
        if provider not in self.URLS:
            raise ValueError('Select chatgpt, gemini or manus browser generation')
        if role not in ('background', 'prop', 'character_pose', 'audio_master'):
            raise ValueError('Unsupported generated asset role')
        import re
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', episode_id):
            raise ValueError('Invalid episode ID')
        request_hash = hashlib.sha256(json.dumps([episode_id, provider, role, prompt]).encode()).hexdigest()
        folder = self.policy.resolve_output(Path('generation') / episode_id / request_hash[:20], overwrite=True)
        folder.mkdir(parents=True, exist_ok=True)
        receipt_path = folder / 'request.json'
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            # Collect late downloads without asking the provider to generate again.
            found = self._collect_downloads(folder, receipt)
            if found:
                return self._save(receipt_path, receipt | {'status': 'downloaded_requires_review', 'assets': found})
            return receipt | {'instruction': 'Generation already submitted; inspect its browser conversation and download the result, then collect. No resubmission was made.'}
        self.browser._require_not_in_takeover()
        self.browser.open_url(self.URLS[provider], wait_seconds=12)
        target = self.browser._get_or_create_page_target()
        receipt = dict(status='preparing', provider=provider, role=role, prompt=prompt,
                       request_sha256=request_hash, download_folder=str(folder), conversation_url=self.URLS[provider])
        with CdpWebSocketClient(target['webSocketDebuggerUrl']) as ws:
            ws.call('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(folder)})
            instruction = prompt + (
                '\nGenerate an actual downloadable audio file, not written TTS advice.' if role == 'audio_master'
                else (
                    '\nGenerate an actual downloadable PNG image.'
                    '\nSTRICT PRODUCTION SPECIFICATION:'
                    '\n- Output Format: High-resolution PNG with transparent background (or pure solid white background for clean alpha cutout).'
                    '\n- Single-Subject Isolation: Exactly ONE animal, character, or prop centered. NEVER include multiple animals or multiple different species in one image.'
                    '\n- Strict Biological Purity & Anti-Chimera Rule: 100% anatomical accuracy for toddler education. Absolutely NO hybrid, fantasy, or cross-blended animals (e.g. NO sheep with duck bills, NO duck-sheep chimeras, NO feathered mammals, NO wool on birds, NO human hands on animals). A sheep must be 100% sheep; a duck must be 100% duck.'
                    '\n- Visual Style: 2.5D children\'s storybook cartoon matching Mar & Kuku official poses, bold clean line art, bright friendly colors, gentle soft shading, expressive cute eyes.'
                    + (' Use a transparent background for this cutout.' if role in ('prop', 'character_pose') else '')
                )
            )
            result = ws.call('Runtime.evaluate', {'expression': """(() => {
 const el=document.querySelector('#prompt-textarea,rich-textarea [contenteditable=true],.ProseMirror,[contenteditable=true],textarea');
 if(!el)return false;el.focus();
 const text=__PROMPT__;
 if(el.tagName==='TEXTAREA'){Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(el,text);}
 else{document.execCommand('selectAll',false,null);document.execCommand('insertText',false,text);}
 el.dispatchEvent(new Event('input',{bubbles:true}));return true;
})()""".replace('__PROMPT__', json.dumps(instruction)), 'returnByValue': True})
            if not result.get('result', {}).get('value'):
                raise RuntimeError('Browser generation composer unavailable; sign in with Owner Takeover')
            # Persist intent before the irreversible remote generation submission.
            receipt['status'] = 'submitted_awaiting_download'
            self._save(receipt_path, receipt)
            ws.call('Runtime.evaluate', {'expression': """(() => {
 const buttons=Array.from(document.querySelectorAll('button'));
 const b=buttons.find(e=>!e.disabled&&(/send|submit/i.test(e.getAttribute('aria-label')||'')||e.dataset.testid==='send-button'));
 if(b){b.click();return 'clicked';}
 const el=document.activeElement;el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));return 'enter';
})()"""})
            deadline = time.monotonic() + timeout_seconds
            clicked = False
            while time.monotonic() < deadline:
                self.browser._check_cancel()
                time.sleep(1)
                dom = ws.call('Runtime.evaluate', {'expression': """(() => {
 const turns=Array.from(document.querySelectorAll('[data-message-author-role="assistant"],model-response'));
 const latest=turns[turns.length-1];
 const streaming=!!document.querySelector('button[data-testid="stop-button"],button[aria-label*="Stop"]');
 return {url:location.href,ready:!!latest&&!streaming};
})()""", 'returnByValue': True}).get('result', {}).get('value') or {}
                receipt['conversation_url'] = dom.get('url', receipt['conversation_url'])
                # Only use the most recent response's download control, not a page-wide old result.
                if dom.get('ready') and not clicked:
                    action = ws.call('Runtime.evaluate', {'expression': """(() => {
 const turns=Array.from(document.querySelectorAll('[data-message-author-role="assistant"],model-response'));
 const last=turns[turns.length-1];if(!last)return false;
 const b=Array.from(last.querySelectorAll('a[download],button')).find(e=>e.hasAttribute('download')||/download/i.test(e.getAttribute('aria-label')||e.innerText||''));
 if(b){b.click();return true;}return false;
})()""", 'returnByValue': True})
                    clicked = bool(action.get('result', {}).get('value'))
                found = self._collect_downloads(folder, receipt)
                if found:
                    return self._save(receipt_path, receipt | {'status': 'downloaded_requires_review', 'assets': found})
            receipt.update(status='awaiting_owner_download', instruction='No valid completed download observed. Use Owner Takeover to inspect/download into this request folder, then collect; do not regenerate.')
            return self._save(receipt_path, receipt)

    def _save(self, path, value):
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(value, indent=2) + '\n')
        tmp.replace(path)
        return value

    def _collect_downloads(self, folder, receipt):
        found = []
        for path in folder.iterdir():
            if path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.wav','.mp3','.m4a','.aiff'):
                continue
            if path.with_suffix(path.suffix + '.crdownload').exists() or time.time() - path.stat().st_mtime < 2:
                continue
            try:
                found.append(self.collect(path, role=receipt['role'], provider=receipt['provider'],
                                          prompt=receipt['prompt'], conversation_url=receipt.get('conversation_url','')))
            except (ValueError, OSError):
                continue
        return found
