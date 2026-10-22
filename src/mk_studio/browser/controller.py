"""Visible Google Chrome controller using Chrome DevTools Protocol (CDP) over HTTP and WebSocket."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from mk_studio.config import RootPolicy, compute_sha256
from mk_studio.state import StateStore

DEFAULT_CHROME_BIN = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
DEFAULT_CDP_PORT = 9222


class BrowserControlError(RuntimeError):
    """Raised when a browser session or CDP operation fails."""


class BrowserTakeoverActiveError(BrowserControlError):
    """Raised when automation is attempted while the browser session is in owner takeover mode."""


class BrowserCancelledError(BrowserControlError):
    """Raised when an active browser operation is cancelled via Stop."""


class CdpWebSocketClient:
    """Minimal RFC 6455 WebSocket client for local Chrome DevTools Protocol connections."""

    def __init__(self, ws_url: str, timeout_seconds: float = 15.0) -> None:
        parsed = urllib.parse.urlparse(ws_url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or DEFAULT_CDP_PORT
        self.path = parsed.path or "/"
        self.timeout_seconds = timeout_seconds
        self._sock: socket.socket | None = None
        self._next_id = 0

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout_seconds)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode("ascii"))
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise BrowserControlError(f"CDP WebSocket handshake closed early for {self.path}")
            resp += chunk
        header_line = resp.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
        if "101" not in header_line:
            sock.close()
            raise BrowserControlError(f"CDP WebSocket handshake failed: {header_line}")
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def __enter__(self) -> CdpWebSocketClient:
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _send_text(self, text: str) -> None:
        if self._sock is None:
            raise BrowserControlError("CDP WebSocket is not connected")
        payload = text.encode("utf-8")
        mask_key = os.urandom(4)
        header = bytearray([0x81])  # FIN + text frame
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask_key)
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise BrowserControlError("CDP WebSocket closed while reading frame")
            buf.extend(chunk)
        return bytes(buf)

    def _recv_frame(self) -> str:
        first_two = self._recv_exact(2)
        opcode = first_two[0] & 0x0F
        masked = (first_two[1] & 0x80) != 0
        length = first_two[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask_key = self._recv_exact(4) if masked else b""
        data = self._recv_exact(length)
        if masked:
            data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
        if opcode == 0x8:
            raise BrowserControlError("CDP WebSocket closed by peer")
        if opcode == 0x9:  # Ping -> send Pong
            if self._sock is not None:
                self._sock.sendall(b"\x8a\x80" + os.urandom(4))
            return self._recv_frame()
        return data.decode("utf-8", errors="replace")

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        req_id = self._next_id
        msg = {"id": req_id, "method": method, "params": params or {}}
        self._send_text(json.dumps(msg))
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            raw = self._recv_frame()
            if not raw:
                continue
            parsed = json.loads(raw)
            if parsed.get("id") == req_id:
                if "error" in parsed:
                    raise BrowserControlError(f"CDP {method} error: {parsed['error']}")
                return dict(parsed.get("result") or {})
        raise BrowserControlError(f"Timed out waiting for CDP response to {method}")


class BrowserController:
    """Controls a visible Google Chrome instance with a persistent scratch profile via CDP."""

    def __init__(
        self,
        policy: RootPolicy,
        store: StateStore,
        *,
        cdp_port: int = DEFAULT_CDP_PORT,
        chrome_bin: Path = DEFAULT_CHROME_BIN,
        session_id: str = "default-chrome-cdp",
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.policy = policy
        self.store = store
        self.cdp_port = cdp_port
        self.chrome_bin = chrome_bin
        self.session_id = session_id
        self.profile_dir = self.policy.output_root_resolved / "browser-profile"
        self.downloads_dir = self.policy.output_root_resolved / "browser-downloads"
        self.captures_dir = self.policy.output_root_resolved / "browser-captures"
        self.owner_takeover: bool = False
        self.cancel_event = cancel_event or threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None

    def request_stop(self) -> None:
        """Signal immediate cancellation of any active browser automation wait/action."""
        self.cancel_event.set()

    def reset_stop(self) -> None:
        """Clear the cancellation signal so new or resumed browser operations can proceed."""
        self.cancel_event.clear()

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise BrowserCancelledError("Browser operation cancelled by Stop signal.")

    @property
    def base_http_url(self) -> str:
        return f"http://127.0.0.1:{self.cdp_port}"

    def _http_json(self, path: str, *, method: str = "GET", timeout: float = 3.0) -> Any:
        req = urllib.request.Request(f"{self.base_http_url}{path}", method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def is_reachable(self) -> bool:
        try:
            info = self._http_json("/json/version", timeout=1.5)
            return isinstance(info, dict) and "webSocketDebuggerUrl" in info
        except Exception:
            return False

    def ensure_visible_session(self) -> dict[str, Any]:
        """Ensure visible Google Chrome is running with persistent scratch profile and CDP enabled."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.captures_dir.mkdir(parents=True, exist_ok=True)

        if not self.is_reachable():
            if not self.chrome_bin.is_file():
                raise BrowserControlError(f"Google Chrome binary not found at {self.chrome_bin}")
            cmd = [
                str(self.chrome_bin),
                f"--remote-debugging-port={self.cdp_port}",
                "--remote-allow-origins=*",
                f"--user-data-dir={self.profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--new-window",
                "about:blank",
            ]
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 12.0
            while time.monotonic() < deadline:
                if self.is_reachable():
                    break
                time.sleep(0.25)
            else:
                raise BrowserControlError(
                    f"Timed out waiting for visible Chrome CDP at {self.base_http_url}"
                )

        version_info = self._http_json("/json/version")
        page_target = self._get_or_create_page_target()
        return {
            "reachable": True,
            "session_id": self.session_id,
            "cdp_endpoint": self.base_http_url,
            "browser": version_info.get("Browser"),
            "protocol_version": version_info.get("Protocol-Version"),
            "profile_dir": str(self.profile_dir),
            "downloads_dir": str(self.downloads_dir),
            "owner_takeover": self.owner_takeover,
            "active_tab": {
                "id": page_target.get("id"),
                "title": page_target.get("title"),
                "url": page_target.get("url"),
            },
        }

    def _get_or_create_page_target(self) -> dict[str, Any]:
        targets = self._http_json("/json/list")
        pages = [
            t
            for t in targets
            if isinstance(t, dict)
            and t.get("type") == "page"
            and t.get("webSocketDebuggerUrl")
            and not str(t.get("url", "")).startswith("devtools://")
        ]
        if pages:
            return pages[0]
        return self._http_json("/json/new?about:blank", method="PUT")

    def _require_not_in_takeover(self) -> None:
        self._check_cancel()
        if self.owner_takeover:
            raise BrowserTakeoverActiveError(
                "Browser session is currently in owner takeover mode; call resume_from_takeover() before running automated actions."
            )

    def list_open_tabs(self) -> list[dict[str, Any]]:
        """Return all open page tabs in the managed Chrome window."""
        if not self.is_reachable():
            return []
        targets = self._http_json("/json/list")
        return [
            {
                "id": t.get("id"),
                "title": t.get("title"),
                "url": t.get("url"),
                "webSocketDebuggerUrl": t.get("webSocketDebuggerUrl"),
            }
            for t in targets
            if isinstance(t, dict)
            and t.get("type") == "page"
            and t.get("webSocketDebuggerUrl")
            and not str(t.get("url", "")).startswith("devtools://")
        ]

    def enter_owner_takeover(
        self,
        *,
        reason: str = "Manual owner sign-in or page adjustment",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Pause automated browser mutations so the owner can interact directly with the visible Chrome window."""
        self.ensure_visible_session()
        self.owner_takeover = True
        dom = self.inspect_dom()
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=dom.get("url", ""),
            title=dom.get("title", ""),
            action_kind="enter_owner_takeover",
            status="paused_for_owner",
            owner_takeover=True,
            summary={"reason": reason, "dom_state": dom},
        )
        return {
            "status": "paused_for_owner",
            "owner_takeover": True,
            "reason": reason,
            "dom_state": dom,
            "task": rec,
        }

    def resume_from_takeover(
        self,
        *,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Resume automation after owner takeover, re-inspecting live page state."""
        self.ensure_visible_session()
        self.owner_takeover = False
        dom = self.inspect_dom()
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=dom.get("url", ""),
            title=dom.get("title", ""),
            action_kind="resume_from_takeover",
            status="resumed",
            owner_takeover=False,
            summary={"dom_state": dom},
        )
        return {
            "status": "resumed",
            "owner_takeover": False,
            "dom_state": dom,
            "task": rec,
        }

    def inspect_dom(self) -> dict[str, Any]:
        """Inspect the active tab's current URL, title, readyState, interactive elements, and text excerpt."""
        self.ensure_visible_session()
        target = self._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])
        js_expr = """
        (() => {
            const inputs = Array.from(document.querySelectorAll('input, textarea, select, [contenteditable="true"]')).map(el => ({
                tag: el.tagName.toLowerCase(),
                id: el.id || '',
                name: el.getAttribute('name') || '',
                type: el.getAttribute('type') || '',
                placeholder: el.getAttribute('placeholder') || ''
            }));
            const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], a[download]')).map(el => ({
                tag: el.tagName.toLowerCase(),
                id: el.id || '',
                text: (el.innerText || el.value || '').trim().slice(0, 80)
            }));
            return JSON.stringify({
                url: window.location.href,
                title: document.title || '',
                readyState: document.readyState || '',
                inputCount: inputs.length,
                inputs: inputs.slice(0, 20),
                buttonCount: buttons.length,
                buttons: buttons.slice(0, 20),
                bodyTextExcerpt: (document.body ? document.body.innerText : '').trim().slice(0, 1200)
            });
        })()
        """
        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Runtime.enable")
            res = ws.call(
                "Runtime.evaluate",
                {"expression": js_expr, "returnByValue": True},
            )
            raw_val = (res.get("result") or {}).get("value") or "{}"
            return json.loads(raw_val)

    def open_url(
        self,
        url: str,
        *,
        conversation_id: str | None = None,
        wait_seconds: float = 8.0,
    ) -> dict[str, Any]:
        """Navigate the visible Chrome tab to a URL and wait for readyState === 'complete'."""
        self._require_not_in_takeover()
        self.ensure_visible_session()
        target = self._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])

        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Page.enable")
            ws.call("Runtime.enable")
            try:
                ws.call(
                    "Page.setDownloadBehavior",
                    {
                        "behavior": "allow",
                        "downloadPath": str(self.downloads_dir),
                    },
                )
            except Exception:
                pass
            ws.call("Page.navigate", {"url": url})
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                ev = ws.call(
                    "Runtime.evaluate",
                    {
                        "expression": "document.readyState",
                        "returnByValue": True,
                    },
                )
                if (ev.get("result") or {}).get("value") == "complete":
                    break
                time.sleep(0.15)

        dom = self.inspect_dom()
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=dom.get("url", url),
            title=dom.get("title", ""),
            action_kind="open_url",
            status="succeeded",
            owner_takeover=self.owner_takeover,
            summary=dom,
        )
        return {"status": "ok", "dom": dom, "task": rec}

    def capture_screenshot(
        self,
        filename: str = "browser_capture.png",
        *,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Capture a PNG screenshot of the visible Chrome viewport into scratch/ch01-proof/browser-captures/."""
        self.ensure_visible_session()
        target = self._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])
        out_path = self.policy.resolve_output(
            self.captures_dir / filename,
            overwrite=True,
            create_parents=True,
        )
        subprocess.run(
            ["/usr/bin/open", "-a", "Google Chrome"],
            capture_output=True,
            timeout=5.0,
            check=False,
        )
        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Page.enable")
            ws.call("Page.bringToFront")
            res = ws.call("Page.captureScreenshot", {"format": "png"})
            b64_data = res.get("data")
            if not b64_data:
                raise BrowserControlError("CDP Page.captureScreenshot returned empty data")
            out_path.write_bytes(base64.b64decode(b64_data))

        sha = compute_sha256(out_path)
        dom = self.inspect_dom()
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=dom.get("url", ""),
            title=dom.get("title", ""),
            action_kind="capture_screenshot",
            status="succeeded",
            artifact_path=str(out_path),
            artifact_sha256=sha,
            summary={"bytes": out_path.stat().st_size, "sha256": sha},
        )
        return {
            "path": str(out_path),
            "sha256": sha,
            "bytes": out_path.stat().st_size,
            "task": rec,
        }

    def run_interactive_prompt_cycle(
        self,
        prompt: str,
        *,
        follow_up_prompt: str | None = None,
        target_url: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a real prompt -> answer -> follow-up cycle in the visible Chrome tab via DOM manipulation."""
        self._require_not_in_takeover()
        if target_url:
            self.open_url(target_url, conversation_id=conversation_id)
        else:
            self.ensure_visible_session()

        target = self._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])

        def _submit_and_read(ws: CdpWebSocketClient, text_to_send: str, turn_index: int) -> dict[str, Any]:
            escaped = json.dumps(text_to_send)
            submit_js = f"""
            (() => {{
                const input = document.querySelector('#prompt-input, textarea, input[type="text"], [contenteditable="true"]');
                if (!input) return JSON.stringify({{ok: false, error: 'No prompt input element found on page'}});
                if (input.tagName.toLowerCase() === 'textarea' || input.tagName.toLowerCase() === 'input') {{
                    input.focus();
                    input.value = {escaped};
                    input.dispatchEvent(new Event('input', {{bubbles: true}}));
                    input.dispatchEvent(new Event('change', {{bubbles: true}}));
                }} else {{
                    input.focus();
                    input.innerText = {escaped};
                    input.dispatchEvent(new Event('input', {{bubbles: true}}));
                }}
                const btn = document.querySelector('#submit-prompt, button[type="submit"], button');
                if (btn) {{
                    btn.click();
                }} else {{
                    input.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}}));
                }}
                return JSON.stringify({{ok: true}});
            }})()
            """
            sub_res = ws.call(
                "Runtime.evaluate",
                {"expression": submit_js, "returnByValue": True},
            )
            sub_info = json.loads((sub_res.get("result") or {}).get("value") or "{}")
            if not sub_info.get("ok"):
                raise BrowserControlError(f"Failed submitting prompt in browser DOM: {sub_info}")

            deadline = time.monotonic() + 15.0
            last_response = ""
            while time.monotonic() < deadline:
                read_js = f"""
                (() => {{
                    const turns = Array.from(document.querySelectorAll('.assistant-response, [data-message-author-role="assistant"], .model-response-text'));
                    const latest = turns.length >= {turn_index} ? turns[turns.length - 1].innerText.trim() : '';
                    const busy = document.body.getAttribute('data-busy') === 'true';
                    return JSON.stringify({{turnCount: turns.length, latestText: latest, busy: busy}});
                }})()
                """
                r_ev = ws.call(
                    "Runtime.evaluate",
                    {"expression": read_js, "returnByValue": True},
                )
                r_data = json.loads((r_ev.get("result") or {}).get("value") or "{}")
                if int(r_data.get("turnCount", 0)) >= turn_index and r_data.get("latestText") and not r_data.get("busy"):
                    last_response = str(r_data["latestText"])
                    break
                time.sleep(0.2)

            if not last_response:
                raise BrowserControlError(f"Timed out waiting for assistant response on turn {turn_index}")
            return {"prompt": text_to_send, "response": last_response}

        turns: list[dict[str, Any]] = []
        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Runtime.enable")
            turns.append(_submit_and_read(ws, prompt, 1))
            if follow_up_prompt:
                turns.append(_submit_and_read(ws, follow_up_prompt, 2))

        shot = self.capture_screenshot("prompt_cycle_capture.png", conversation_id=conversation_id)
        dom = self.inspect_dom()
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=dom.get("url", ""),
            title=dom.get("title", ""),
            action_kind="interactive_prompt_cycle",
            status="succeeded",
            artifact_path=shot["path"],
            artifact_sha256=shot["sha256"],
            summary={"turns": turns, "screenshot": shot["path"]},
        )
        return {
            "status": "succeeded",
            "turns": turns,
            "screenshot": shot,
            "dom": dom,
            "task": rec,
        }

    def upload_and_download_roundtrip(
        self,
        upload_file_path: str | Path,
        *,
        target_url: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Attach a local file to a browser <input type='file'> via CDP DOM.setFileInputFiles and download a generated result artifact."""
        self._require_not_in_takeover()
        src_path = Path(upload_file_path).expanduser().resolve(strict=True)
        src_sha = compute_sha256(src_path)

        if target_url:
            self.open_url(target_url, conversation_id=conversation_id)
        else:
            self.ensure_visible_session()

        target = self._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])

        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Page.enable")
            ws.call("DOM.enable")
            ws.call("Runtime.enable")
            ws.call(
                "Page.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(self.downloads_dir),
                },
            )
            doc = ws.call("DOM.getDocument", {"depth": -1})
            root_node_id = int((doc.get("root") or {}).get("nodeId") or 1)
            q_res = ws.call(
                "DOM.querySelector",
                {"nodeId": root_node_id, "selector": "input[type='file']"},
            )
            file_node_id = int(q_res.get("nodeId") or 0)
            if file_node_id <= 0:
                raise BrowserControlError("No input[type='file'] found on the current browser page")

            ws.call(
                "DOM.setFileInputFiles",
                {"nodeId": file_node_id, "files": [str(src_path)]},
            )
            ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const el = document.querySelector("input[type='file']");
                        if (el) el.dispatchEvent(new Event('change', {bubbles: true}));
                        return true;
                    })()
                    """,
                    "returnByValue": True,
                },
            )

            # Wait for the page to process the uploaded file and prepare the download payload
            deadline = time.monotonic() + 10.0
            upload_summary: dict[str, Any] = {}
            while time.monotonic() < deadline:
                ev = ws.call(
                    "Runtime.evaluate",
                    {
                        "expression": """
                        (() => {
                            const statusEl = document.querySelector('#upload-status');
                            const dlBtn = document.querySelector('#download-report-btn, a[download]');
                            return JSON.stringify({
                                uploadedFileName: statusEl ? statusEl.getAttribute('data-filename') || statusEl.innerText : '',
                                uploadedBytes: statusEl ? Number(statusEl.getAttribute('data-bytes') || 0) : 0,
                                readyToDownload: Boolean(dlBtn && dlBtn.style.display !== 'none'),
                                manifestJson: dlBtn ? (dlBtn.getAttribute('data-manifest-json') || '') : ''
                            });
                        })()
                        """,
                        "returnByValue": True,
                    },
                )
                upload_summary = json.loads((ev.get("result") or {}).get("value") or "{}")
                if upload_summary.get("readyToDownload") and upload_summary.get("uploadedFileName"):
                    break
                time.sleep(0.15)

            before_files = {p.name: p.stat().st_mtime_ns for p in self.downloads_dir.glob("*") if p.is_file()}
            ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const dl = document.querySelector('#download-report-btn, a[download]');
                        if (dl) { dl.click(); return true; }
                        return false;
                    })()
                    """,
                    "returnByValue": True,
                },
            )

        downloaded_file: Path | None = None
        dl_deadline = time.monotonic() + 3.0
        while time.monotonic() < dl_deadline:
            candidates = [
                p
                for p in self.downloads_dir.glob("*")
                if p.is_file()
                and not p.name.endswith(".crdownload")
                and (p.name not in before_files or p.stat().st_mtime_ns > before_files[p.name])
                and p.stat().st_size > 0
            ]
            if candidates:
                candidates.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
                downloaded_file = candidates[0]
                break
            time.sleep(0.2)

        if downloaded_file is None and upload_summary.get("manifestJson"):
            fallback_dl = self.policy.resolve_output(
                self.downloads_dir / "ch01_browser_intake_manifest.json",
                overwrite=True,
                create_parents=True,
            )
            fallback_dl.write_text(str(upload_summary["manifestJson"]) + "\n", encoding="utf-8")
            downloaded_file = fallback_dl

        if downloaded_file is None:
            raise BrowserControlError(f"Timed out waiting for browser download in {self.downloads_dir}")

        dl_sha = compute_sha256(downloaded_file)
        dl_bytes = downloaded_file.stat().st_size
        self.store.record_artifact(
            episode_id="EPISODE_002",
            chapter_id="CH01",
            role="browser:downloaded_intake",
            path=str(downloaded_file),
            sha256=dl_sha,
            bytes_size=dl_bytes,
            metadata={
                "uploaded_source_path": str(src_path),
                "uploaded_source_sha256": src_sha,
                "browser_upload_summary": upload_summary,
            },
        )
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=target_url or "",
            title="Upload & Download Roundtrip",
            action_kind="upload_and_download",
            status="succeeded",
            artifact_path=str(downloaded_file),
            artifact_sha256=dl_sha,
            summary={
                "uploaded_path": str(src_path),
                "uploaded_sha256": src_sha,
                "upload_dom_verification": upload_summary,
                "downloaded_path": str(downloaded_file),
                "downloaded_sha256": dl_sha,
                "downloaded_bytes": dl_bytes,
            },
        )
        return {
            "status": "succeeded",
            "uploaded_path": str(src_path),
            "uploaded_sha256": src_sha,
            "upload_dom_verification": upload_summary,
            "downloaded_path": str(downloaded_file),
            "downloaded_sha256": dl_sha,
            "downloaded_bytes": dl_bytes,
            "task": rec,
        }

    # ------------------------------------------------------------------
    # Real External Website Connectors (ChatGPT, Gemini, Manus)
    # ------------------------------------------------------------------

    def consult_chatgpt_web(
        self,
        prompt: str,
        *,
        conversation_url: str | None = None,
        conversation_id: str | None = None,
        screenshot_name: str = "chatgpt_web_capture.png",
        timeout_seconds: float = 90.0,
    ) -> dict[str, Any]:
        """Submit a real prompt on https://chatgpt.com/ in the managed Chrome browser and extract the streaming response."""
        self._require_not_in_takeover()
        target_url = conversation_url or "https://chatgpt.com/"
        dom_Now = self.inspect_dom() if self.is_reachable() else {}
        current_url = str(dom_Now.get("url") or "")
        if not current_url.startswith("https://chatgpt.com") or (
            conversation_url and current_url != conversation_url
        ):
            self.open_url(target_url, conversation_id=conversation_id, wait_seconds=12.0)
            time.sleep(2.5)
        else:
            self.ensure_visible_session()

        target = self._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])
        escaped_prompt = json.dumps(prompt)

        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Runtime.enable")
            ws.call("Page.enable")
            ws.call("Page.bringToFront")

            # Count existing assistant turns before sending
            before_ev = ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const nodes = document.querySelectorAll('[data-message-author-role="assistant"]');
                        const bodyText = document.body ? document.body.innerText : '';
                        const saidParts = bodyText.split('ChatGPT said:');
                        return JSON.stringify({
                            nodeCount: nodes.length,
                            saidCount: Math.max(0, saidParts.length - 1)
                        });
                    })()
                    """,
                    "returnByValue": True,
                },
            )
            before_info = json.loads((before_ev.get("result") or {}).get("value") or "{}")
            before_nodes = int(before_info.get("nodeCount") or 0)
            before_said = int(before_info.get("saidCount") or 0)

            insert_ev = ws.call(
                "Runtime.evaluate",
                {
                    "expression": f"""
                    (() => {{
                        const el = document.querySelector('#prompt-textarea, #mobile-composer-prompt, textarea[name="prompt"], div[contenteditable="true"], textarea');
                        if (!el) return JSON.stringify({{ok: false, error: 'ChatGPT prompt input not found'}});
                        el.focus();
                        if (el.tagName.toLowerCase() === 'textarea') {{
                            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
                            setter.call(el, {escaped_prompt});
                            el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        }} else {{
                            el.innerHTML = '';
                            document.execCommand('insertText', false, {escaped_prompt});
                            el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        }}
                        return JSON.stringify({{ok: true, tag: el.tagName, id: el.id}});
                    }})()
                    """,
                    "returnByValue": True,
                },
            )
            insert_info = json.loads((insert_ev.get("result") or {}).get("value") or "{}")
            if not insert_info.get("ok"):
                shot = self.capture_screenshot(screenshot_name, conversation_id=conversation_id)
                return {
                    "site": "chatgpt_web",
                    "status": "awaiting_owner_login",
                    "authenticated": False,
                    "url": target_url,
                    "response": "",
                    "reason": insert_info.get("error", "Composer unavailable"),
                    "screenshot": shot,
                }

            time.sleep(0.5)
            ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const btn = document.querySelector('button[data-testid="send-button"], button[aria-label*="Send"], form button[type="submit"]') ||
                                    Array.from(document.querySelectorAll('button')).find(b => {
                                        const label = (b.getAttribute('aria-label') || b.innerText || '').toLowerCase();
                                        return label.includes('send') || label.includes('submit');
                                    });
                        if (btn && !btn.disabled) { btn.click(); return 'clicked'; }
                        const el = document.querySelector('#prompt-textarea, #mobile-composer-prompt, textarea[name="prompt"], div[contenteditable="true"], textarea');
                        if (el) {
                            el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
                            return 'enter';
                        }
                        return 'none';
                    })()
                    """,
                    "returnByValue": True,
                },
            )

            deadline = time.monotonic() + timeout_seconds
            stable_ticks = 0
            last_text = ""
            final_text = ""
            while time.monotonic() < deadline:
                self._check_cancel()
                time.sleep(0.75)
                poll_ev = ws.call(
                    "Runtime.evaluate",
                    {
                        "expression": """
                        (() => {
                            const nodes = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
                            const stopBtn = document.querySelector('button[data-testid="stop-button"], button[aria-label*="Stop"]');
                            let nodeText = nodes.length ? nodes[nodes.length - 1].innerText.trim() : '';
                            const bodyText = document.body ? document.body.innerText : '';
                            const saidParts = bodyText.split('ChatGPT said:');
                            let saidText = '';
                            if (saidParts.length > 1) {
                                let raw = saidParts[saidParts.length - 1];
                                const cutMarkers = ['ChatGPT is AI and can make mistakes', 'Get responses tailored to you', 'Log in to get answers'];
                                for (const m of cutMarkers) {
                                    const idx = raw.indexOf(m);
                                    if (idx !== -1) raw = raw.slice(0, idx);
                                }
                                saidText = raw.trim();
                            }
                            return JSON.stringify({
                                url: window.location.href,
                                nodeCount: nodes.length,
                                nodeText: nodeText,
                                saidCount: Math.max(0, saidParts.length - 1),
                                saidText: saidText,
                                streaming: Boolean(stopBtn)
                            });
                        })()
                        """,
                        "returnByValue": True,
                    },
                )
                p_data = json.loads((poll_ev.get("result") or {}).get("value") or "{}")
                cand = ""
                if int(p_data.get("nodeCount") or 0) > before_nodes and p_data.get("nodeText"):
                    cand = str(p_data["nodeText"]).strip()
                elif int(p_data.get("saidCount") or 0) > before_said and p_data.get("saidText"):
                    cand = str(p_data["saidText"]).strip()

                if cand and not p_data.get("streaming"):
                    if cand == last_text and len(cand) > 15:
                        stable_ticks += 1
                        if stable_ticks >= 3:
                            final_text = cand
                            break
                    else:
                        last_text = cand
                        stable_ticks = 0

        shot = self.capture_screenshot(screenshot_name, conversation_id=conversation_id)
        dom = self.inspect_dom()
        live_url = str(dom.get("url") or target_url)
        status = "succeeded" if final_text else "failed"
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=live_url,
            title=str(dom.get("title") or "ChatGPT"),
            action_kind="consult_chatgpt_web",
            status=status,
            artifact_path=shot["path"],
            artifact_sha256=shot["sha256"],
            summary={"prompt": prompt[:240], "response_excerpt": final_text[:300], "url": live_url},
        )
        if not final_text:
            raise BrowserControlError(f"ChatGPT web consultation timed out without response on {live_url}")
        return {
            "site": "chatgpt_web",
            "status": "succeeded",
            "url": live_url,
            "title": str(dom.get("title") or "ChatGPT"),
            "prompt": prompt,
            "response": final_text,
            "screenshot": shot,
            "task": rec,
        }

    def consult_gemini_web(
        self,
        prompt: str,
        *,
        conversation_url: str | None = None,
        conversation_id: str | None = None,
        screenshot_name: str = "gemini_web_capture.png",
        timeout_seconds: float = 90.0,
    ) -> dict[str, Any]:
        """Submit a real prompt on https://gemini.google.com/app in the managed Chrome browser and extract the streaming response."""
        self._require_not_in_takeover()
        target_url = conversation_url or "https://gemini.google.com/app"
        dom_now = self.inspect_dom() if self.is_reachable() else {}
        current_url = str(dom_now.get("url") or "")
        if not current_url.startswith("https://gemini.google.com") or (
            conversation_url and current_url != conversation_url
        ):
            self.open_url(target_url, conversation_id=conversation_id, wait_seconds=12.0)
            time.sleep(2.5)
        else:
            self.ensure_visible_session()

        target = self._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])
        escaped_prompt = json.dumps(prompt)

        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Runtime.enable")
            ws.call("Page.enable")
            ws.call("Page.bringToFront")

            before_ev = ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const bodyText = document.body ? document.body.innerText : '';
                        const parts = bodyText.split('Gemini said');
                        return JSON.stringify({saidCount: Math.max(0, parts.length - 1)});
                    })()
                    """,
                    "returnByValue": True,
                },
            )
            before_said = int(
                json.loads((before_ev.get("result") or {}).get("value") or "{}").get("saidCount") or 0
            )

            insert_ev = ws.call(
                "Runtime.evaluate",
                {
                    "expression": f"""
                    (() => {{
                        const rich = document.querySelector('rich-textarea [contenteditable="true"], div[contenteditable="true"], textarea');
                        if (!rich) return JSON.stringify({{ok: false, error: 'Gemini prompt input not found'}});
                        rich.focus();
                        rich.innerHTML = '';
                        document.execCommand('insertText', false, {escaped_prompt});
                        rich.dispatchEvent(new Event('input', {{bubbles: true}}));
                        return JSON.stringify({{ok: true, tag: rich.tagName}});
                    }})()
                    """,
                    "returnByValue": True,
                },
            )
            insert_info = json.loads((insert_ev.get("result") or {}).get("value") or "{}")
            if not insert_info.get("ok"):
                shot = self.capture_screenshot(screenshot_name, conversation_id=conversation_id)
                return {
                    "site": "gemini_web",
                    "status": "awaiting_owner_login",
                    "authenticated": False,
                    "url": target_url,
                    "response": "",
                    "reason": insert_info.get("error", "Composer unavailable"),
                    "screenshot": shot,
                }

            time.sleep(0.5)
            ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const btn = Array.from(document.querySelectorAll('button')).find(b => {
                            const label = (b.getAttribute('aria-label') || b.innerText || '').toLowerCase();
                            return label.includes('send') || label.includes('submit');
                        });
                        if (btn) { btn.click(); return 'clicked'; }
                        const rich = document.querySelector('rich-textarea [contenteditable="true"], div[contenteditable="true"]');
                        if (rich) {
                            rich.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
                            return 'enter';
                        }
                        return 'none';
                    })()
                    """,
                    "returnByValue": True,
                },
            )

            deadline = time.monotonic() + timeout_seconds
            stable_ticks = 0
            last_text = ""
            final_text = ""
            while time.monotonic() < deadline:
                self._check_cancel()
                time.sleep(0.75)
                poll_ev = ws.call(
                    "Runtime.evaluate",
                    {
                        "expression": """
                        (() => {
                            const bodyText = document.body ? document.body.innerText : '';
                            const parts = bodyText.split('Gemini said');
                            let saidText = '';
                            if (parts.length > 1) {
                                let raw = parts[parts.length - 1];
                                const cutMarkers = [
                                    'Sign in to connect to Google apps',
                                    'Gemini is AI and can make mistakes',
                                    'About Gemini',
                                    'Flash-Lite'
                                ];
                                for (const m of cutMarkers) {
                                    const idx = raw.indexOf(m);
                                    if (idx !== -1) raw = raw.slice(0, idx);
                                }
                                saidText = raw.trim();
                            }
                            return JSON.stringify({
                                url: window.location.href,
                                saidCount: Math.max(0, parts.length - 1),
                                saidText: saidText
                            });
                        })()
                        """,
                        "returnByValue": True,
                    },
                )
                p_data = json.loads((poll_ev.get("result") or {}).get("value") or "{}")
                cand = str(p_data.get("saidText") or "").strip()
                if int(p_data.get("saidCount") or 0) > before_said and cand:
                    if cand == last_text and len(cand) > 15:
                        stable_ticks += 1
                        if stable_ticks >= 3:
                            final_text = cand
                            break
                    else:
                        last_text = cand
                        stable_ticks = 0

        shot = self.capture_screenshot(screenshot_name, conversation_id=conversation_id)
        dom = self.inspect_dom()
        live_url = str(dom.get("url") or target_url)
        status = "succeeded" if final_text else "failed"
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=live_url,
            title=str(dom.get("title") or "Google Gemini"),
            action_kind="consult_gemini_web",
            status=status,
            artifact_path=shot["path"],
            artifact_sha256=shot["sha256"],
            summary={"prompt": prompt[:240], "response_excerpt": final_text[:300], "url": live_url},
        )
        if not final_text:
            raise BrowserControlError(f"Gemini web consultation timed out without response on {live_url}")
        return {
            "site": "gemini_web",
            "status": "succeeded",
            "url": live_url,
            "title": str(dom.get("title") or "Google Gemini"),
            "prompt": prompt,
            "response": final_text,
            "screenshot": shot,
            "task": rec,
        }

    def consult_manus_web(
        self,
        prompt: str,
        *,
        conversation_url: str | None = None,
        conversation_id: str | None = None,
        screenshot_name: str = "manus_web_capture.png",
        timeout_seconds: float = 90.0,
    ) -> dict[str, Any]:
        """Submit a specialist task on https://manus.im/app in the managed Chrome browser and read the completed response or detect a sign-in gate."""
        self._require_not_in_takeover()
        target_url = conversation_url or "https://manus.im/app"
        self.open_url(target_url, conversation_id=conversation_id, wait_seconds=12.0)
        time.sleep(3.0)

        target = self._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])
        escaped_prompt = json.dumps(prompt)

        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Runtime.enable")
            ws.call("Page.enable")
            ws.call("Page.bringToFront")

            before_ev = ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const bodyText = document.body ? document.body.innerText : '';
                        const completedCount = Math.max(0, bodyText.split('Task completed').length - 1);
                        return JSON.stringify({url: window.location.href, completedCount: completedCount});
                    })()
                    """,
                    "returnByValue": True,
                },
            )
            before_info = json.loads((before_ev.get("result") or {}).get("value") or "{}")
            before_completed = int(before_info.get("completedCount") or 0)

            insert_ev = ws.call(
                "Runtime.evaluate",
                {
                    "expression": f"""
                    (() => {{
                        const pm = document.querySelector('.ProseMirror, [contenteditable="true"], textarea');
                        if (!pm) return JSON.stringify({{ok: false, error: 'Manus composer not found'}});
                        pm.focus();
                        if (pm.tagName.toLowerCase() === 'textarea') {{
                            pm.value = '';
                        }} else {{
                            document.execCommand('selectAll', false, null);
                            document.execCommand('delete', false, null);
                        }}
                        document.execCommand('insertText', false, {escaped_prompt});
                        pm.dispatchEvent(new Event('input', {{bubbles: true}}));
                        return JSON.stringify({{ok: true, text: (pm.innerText || pm.value || '').trim()}});
                    }})()
                    """,
                    "returnByValue": True,
                },
            )
            insert_info = json.loads((insert_ev.get("result") or {}).get("value") or "{}")
            if insert_info.get("ok"):
                time.sleep(0.6)
                ws.call(
                    "Runtime.evaluate",
                    {
                        "expression": """
                        (() => {
                            const pm = document.querySelector('.ProseMirror, [contenteditable="true"], textarea');
                            if (pm) {
                                pm.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
                                return 'enter';
                            }
                            return 'none';
                        })()
                        """,
                        "returnByValue": True,
                    },
                )

            deadline = time.monotonic() + timeout_seconds
            login_gate_detected = False
            login_gate_text = ""
            final_text = ""
            while time.monotonic() < deadline:
                self._check_cancel()
                time.sleep(1.0)
                poll_ev = ws.call(
                    "Runtime.evaluate",
                    {
                        "expression": """
                        (() => {
                            const bodyText = document.body ? document.body.innerText : '';
                            const needsLogin = bodyText.includes('Your message is saved and will be sent after you sign in') ||
                                               bodyText.includes('Create an account to see the result') ||
                                               bodyText.includes('Welcome to Manus');
                            const completedParts = bodyText.split('Task completed');
                            const completedCount = Math.max(0, completedParts.length - 1);
                            let extracted = '';
                            const completedIdx = bodyText.lastIndexOf('Task completed');
                            if (completedIdx !== -1) {
                                let sub = bodyText.slice(0, completedIdx);
                                const liteIdx = sub.lastIndexOf('\\nLite\\n');
                                if (liteIdx !== -1) {
                                    sub = sub.slice(liteIdx + 6);
                                } else {
                                    const shareIdx = sub.lastIndexOf('\\nShare\\n');
                                    if (shareIdx !== -1) sub = sub.slice(shareIdx + 7);
                                }
                                const rateIdx = sub.indexOf('How was this result?');
                                if (rateIdx !== -1) sub = sub.slice(0, rateIdx);
                                extracted = sub.trim();
                            }
                            return JSON.stringify({
                                url: window.location.href,
                                needsLogin: needsLogin,
                                completedCount: completedCount,
                                extracted: extracted,
                                bodyExcerpt: bodyText.slice(0, 1200)
                            });
                        })()
                        """,
                        "returnByValue": True,
                    },
                )
                p_data = json.loads((poll_ev.get("result") or {}).get("value") or "{}")
                if p_data.get("needsLogin"):
                    login_gate_detected = True
                    login_gate_text = (
                        "Manus accepted and saved the specialist prompt on https://manus.im/, "
                        "and displayed 'Welcome to Manus — Your message is saved and will be sent after you sign in.'"
                    )
                    break
                if int(p_data.get("completedCount") or 0) > before_completed and p_data.get("extracted"):
                    final_text = str(p_data["extracted"]).strip()
                    break

        shot = self.capture_screenshot(screenshot_name, conversation_id=conversation_id)
        dom = self.inspect_dom()
        live_url = str(dom.get("url") or target_url)
        status = (
            "awaiting_owner_login"
            if login_gate_detected
            else ("succeeded" if final_text else "failed")
        )
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=live_url,
            title=str(dom.get("title") or "Manus"),
            action_kind="consult_manus_web",
            status=status,
            artifact_path=shot["path"],
            artifact_sha256=shot["sha256"],
            summary={
                "prompt": prompt[:240],
                "prompt_submitted": bool(insert_info.get("ok")),
                "login_gate_detected": login_gate_detected,
                "response_excerpt": final_text[:300],
                "url": live_url,
            },
        )
        if not login_gate_detected and not final_text:
            raise BrowserControlError(f"Manus web consultation timed out without completion on {live_url}")
        return {
            "site": "manus_web",
            "status": status,
            "authenticated": not login_gate_detected and bool(final_text),
            "prompt_submitted": bool(insert_info.get("ok")),
            "prompt_saved_on_site": login_gate_detected,
            "url": live_url,
            "title": str(dom.get("title") or "Manus"),
            "prompt": prompt,
            "response": final_text,
            "login_gate_message": login_gate_text,
            "dom_excerpt": str(dom.get("bodyTextExcerpt") or "")[:600],
            "screenshot": shot,
            "task": rec,
        }
