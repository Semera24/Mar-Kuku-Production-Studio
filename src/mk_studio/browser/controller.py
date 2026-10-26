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

import shutil
from mk_studio.config import RootPolicy, compute_sha256
from mk_studio.os_compat import focus_application
from mk_studio.state import StateStore

_CHROME_CANDIDATES = (
    os.environ.get("CHROME_BIN", ""),
    shutil.which("google-chrome") or "",
    shutil.which("google-chrome-stable") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
DEFAULT_CHROME_BIN = next(
    (Path(c) for c in _CHROME_CANDIDATES if c and Path(c).is_file()),
    Path("/usr/bin/google-chrome"),
)
DEFAULT_CDP_PORT = 9222


class BrowserControlError(RuntimeError):
    """Raised when a browser session or CDP operation fails."""


class BrowserTakeoverActiveError(BrowserControlError):
    """Raised when automation is attempted while the browser session is in owner takeover mode."""


class BrowserCancelledError(BrowserControlError):
    """Raised when an active browser operation is cancelled via Stop."""


class CdpWebSocketClient:
    """Minimal RFC 6455 WebSocket client for local Chrome DevTools Protocol connections."""

    def __init__(self, ws_url: str, timeout_seconds: float = 420.0) -> None:
        parsed = urllib.parse.urlparse(ws_url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or DEFAULT_CDP_PORT
        self.path = parsed.path or "/"
        self.timeout_seconds = timeout_seconds
        self._sock: socket.socket | None = None
        self._next_id = 0
        self.received_events: list[dict[str, Any]] = []

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
            if "method" in parsed:
                self.received_events.append(parsed)
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
        self.session_urls_file = self.policy.output_root_resolved / "active_project_sessions.json"
        self.owner_takeover: bool = False
        self.cancel_event = cancel_event or threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None
        self._active_target_id: str | None = None

    @staticmethod
    def _domain_to_service_key(url_or_domain: str) -> str:
        low = (url_or_domain or "").lower()
        if "chatgpt.com" in low:
            return "chatgpt"
        if "gemini.google.com" in low:
            return "gemini"
        if "manus.im" in low:
            return "manus"
        if "flowmusic.app" in low:
            return "flowmusic"
        if "capcut.com" in low:
            return "capcut"
        if "youtube.com" in low:
            return "youtube"
        return low

    @classmethod
    def _is_specific_session_url(cls, service_key: str, url: str) -> bool:
        """Return True if url points to a specific active chat/session rather than the bare service home page."""
        u = (url or "").strip()
        if not u:
            return False
        sk = cls._domain_to_service_key(service_key)
        if sk == "chatgpt":
            return "chatgpt.com" in u.lower() and ("/c/" in u or "/g/" in u)
        if sk == "gemini":
            if "gemini.google.com/app/" in u:
                tail = u.split("gemini.google.com/app/", 1)[1].strip("/?")
                return len(tail) > 2
            return False
        if sk == "manus":
            if "manus.im/app/" in u:
                tail = u.split("manus.im/app/", 1)[1].strip("/?")
                return len(tail) > 2
            return False
        if sk == "flowmusic":
            return "flowmusic.app/session/" in u.lower()
        return False

    def load_session_urls(self) -> dict[str, str]:
        if self.session_urls_file.is_file():
            try:
                data = json.loads(self.session_urls_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items() if v}
            except Exception:
                pass
        return {}

    def get_session_url(self, service_key: str) -> str | None:
        sk = self._domain_to_service_key(service_key)
        return self.load_session_urls().get(sk)

    def set_session_url(self, service_key: str, url: str) -> None:
        """Persist the active project conversation/session URL for a service so subsequent steps return to the same chat."""
        u = (url or "").strip()
        if not u:
            return
        sk = self._domain_to_service_key(service_key)
        current_map = self.load_session_urls()
        existing = current_map.get(sk, "")
        # Never overwrite a specific conversation URL (e.g. /c/123) with a generic home URL (e.g. https://chatgpt.com/)
        if existing and self._is_specific_session_url(sk, existing) and not self._is_specific_session_url(sk, u):
            return
        current_map[sk] = u
        try:
            self.session_urls_file.parent.mkdir(parents=True, exist_ok=True)
            self.session_urls_file.write_text(json.dumps(current_map, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    def clear_session_urls(self) -> None:
        """Clear pinned project conversation URLs when starting a brand-new project."""
        try:
            if self.session_urls_file.is_file():
                self.session_urls_file.unlink()
        except Exception:
            pass

    def request_stop(self) -> None:
        """Signal immediate cancellation of any active browser automation wait/action."""
        self.cancel_event.set()

    def reset_stop(self) -> None:
        """Clear the cancellation signal and resume from takeover so new or resumed browser operations can proceed."""
        self.cancel_event.clear()
        self.owner_takeover = False

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
                "https://chatgpt.com/",
            ]
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
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

    def _close_target(self, target_id: str) -> None:
        if not target_id:
            return
        try:
            req = urllib.request.Request(f"{self.base_http_url}/json/close/{target_id}", method="GET")
            with urllib.request.urlopen(req, timeout=2.0):
                pass
        except Exception:
            pass

    @staticmethod
    def _default_url_for_service(service_key: str, fallback_pattern: str) -> str:
        defaults = {
            "chatgpt": "https://chatgpt.com/",
            "gemini": "https://gemini.google.com/app",
            "manus": "https://manus.im/app",
            "flowmusic": "https://www.flowmusic.app/",
            "capcut": "https://www.capcut.com/my-edit?start_tab=video",
            "youtube": "https://studio.youtube.com/",
        }
        if service_key in defaults:
            return defaults[service_key]
        if fallback_pattern.startswith("http://") or fallback_pattern.startswith("https://"):
            return fallback_pattern
        return f"https://{fallback_pattern}/"

    def _get_or_create_page_target(self, preferred_url_pattern: str | None = None) -> dict[str, Any]:
        targets = self._http_json("/json/list")
        pages = [
            t
            for t in targets
            if isinstance(t, dict)
            and t.get("type") == "page"
            and t.get("webSocketDebuggerUrl")
            and not str(t.get("url", "")).startswith("devtools://")
        ]
        if preferred_url_pattern:
            pat = preferred_url_pattern.lower()
            sk = self._domain_to_service_key(pat)
            saved_url = self.get_session_url(sk)
            matching = [p for p in pages if pat in str(p.get("url", "")).lower()]
            if matching:
                def _rank_tab(p: dict[str, Any]) -> tuple[int, int]:
                    u = str(p.get("url", "")).strip()
                    exact_saved = 1 if (saved_url and u.rstrip("/") == saved_url.rstrip("/")) else 0
                    is_specific = 1 if self._is_specific_session_url(sk, u) else 0
                    return (exact_saved, is_specific)

                matching.sort(key=_rank_tab, reverse=True)
                chosen = matching[0]
                # Close any duplicate tabs for the same service so Chrome strictly keeps 1 tab per service
                for dup in matching[1:]:
                    dup_id = str(dup.get("id") or "")
                    if dup_id and dup_id != str(chosen.get("id") or ""):
                        self._close_target(dup_id)

                chosen_url = str(chosen.get("url") or "")
                if self._is_specific_session_url(sk, chosen_url):
                    self.set_session_url(sk, chosen_url)

                tid = chosen.get("id")
                if tid:
                    self._active_target_id = str(tid)
                    try:
                        with CdpWebSocketClient(str(chosen["webSocketDebuggerUrl"]), timeout_seconds=3.0) as ws:
                            ws.call("Page.bringToFront")
                    except Exception:
                        pass
                return chosen

            # No tab for this service exists yet: create a dedicated tab instead of overwriting another service's tab
            initial_url = saved_url or self._default_url_for_service(sk, preferred_url_pattern)
            if pages:
                try:
                    with CdpWebSocketClient(str(pages[0]["webSocketDebuggerUrl"]), timeout_seconds=4.0) as ws:
                        res = ws.call("Target.createTarget", {"url": initial_url})
                        new_tid = str(res.get("targetId") or "")
                    time.sleep(0.5)
                    refreshed = self._http_json("/json/list")
                    for t in refreshed:
                        if isinstance(t, dict) and (str(t.get("id")) == new_tid or pat in str(t.get("url", "")).lower()):
                            self._active_target_id = str(t.get("id") or "")
                            return t
                except Exception:
                    pass

        # Find whichever tab is currently visible in the Chrome window so we never yank the user to another tab
        for p in pages:
            if str(p.get("url", "")).strip() in ("", "about:blank", "chrome://newtab/"):
                continue
            try:
                with CdpWebSocketClient(str(p["webSocketDebuggerUrl"]), timeout_seconds=1.5) as ws:
                    ev = ws.call(
                        "Runtime.evaluate",
                        {"expression": "document.visibilityState === 'visible'", "returnByValue": True},
                    )
                    if (ev.get("result") or {}).get("value") is True:
                        if p.get("id"):
                            self._active_target_id = str(p["id"])
                        return p
            except Exception:
                pass
        if self._active_target_id:
            for p in pages:
                if str(p.get("id")) == self._active_target_id:
                    return p
        non_blank_pages = [
            p
            for p in pages
            if str(p.get("url", "")).strip() not in ("", "about:blank", "chrome://newtab/")
        ]
        if non_blank_pages:
            chosen = non_blank_pages[0]
            if chosen.get("id"):
                self._active_target_id = str(chosen["id"])
            return chosen
        if pages:
            return pages[0]
        return self._http_json("/json/new?https://chatgpt.com/", method="PUT")

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

    def open_or_focus_tab(self, url: str, *, capture_preview: bool = True) -> dict[str, Any]:
        """Open a URL in a new Chrome tab (or activate the existing tab for that domain without reloading it)."""
        self.ensure_visible_session()
        parsed = urllib.parse.urlparse(url)
        domain = (parsed.netloc or url).lower()
        sk = self._domain_to_service_key(domain)
        saved_url = self.get_session_url(sk)
        target_url = saved_url if (saved_url and not self._is_specific_session_url(sk, url)) else url

        tabs = self.list_open_tabs()
        matching = [t for t in tabs if domain and domain in str(t.get("url", "")).lower()]
        if matching:
            # Prefer the tab that already has an active project session URL
            matching.sort(
                key=lambda t: (
                    1 if (saved_url and str(t.get("url", "")).rstrip("/") == saved_url.rstrip("/")) else 0,
                    1 if self._is_specific_session_url(sk, str(t.get("url", ""))) else 0,
                ),
                reverse=True,
            )
            chosen = matching[0]
            for dup in matching[1:]:
                if dup.get("id") and dup.get("id") != chosen.get("id"):
                    self._close_target(str(dup["id"]))
            tid = chosen.get("id")
            if tid:
                self._active_target_id = str(tid)
            try:
                with CdpWebSocketClient(str(chosen["webSocketDebuggerUrl"]), timeout_seconds=4.0) as ws:
                    ws.call("Page.bringToFront")
                    ev = ws.call("Runtime.evaluate", {"expression": "window.location.href", "returnByValue": True})
                    live_href = str((ev.get("result") or {}).get("value") or chosen.get("url") or "")
                    if self._is_specific_session_url(sk, live_href):
                        self.set_session_url(sk, live_href)
                        chosen["url"] = live_href
                    elif saved_url and self._is_specific_session_url(sk, saved_url) and live_href.rstrip("/") != saved_url.rstrip("/"):
                        ws.call("Page.enable")
                        ws.call("Page.navigate", {"url": saved_url})
                        chosen["url"] = saved_url
            except Exception:
                pass
            focus_application("Google Chrome")
            if capture_preview:
                try:
                    time.sleep(0.3)
                    self.capture_screenshot("studio_live_preview.png", bring_to_front=False, record_task=False)
                except Exception:
                    pass
            return {"status": "activated", "tab": chosen, "pinned_session_url": self.get_session_url(sk)}

        # Create a new tab via Target.createTarget over CDP WebSocket
        base_target = self._get_or_create_page_target()
        ws_url = str(base_target["webSocketDebuggerUrl"])
        # If the only open tab is about:blank, navigate it directly instead of leaving an extra blank tab
        if len(tabs) == 1 and str(tabs[0].get("url", "")) in ("about:blank", "chrome://newtab/"):
            with CdpWebSocketClient(ws_url, timeout_seconds=420.0) as ws:
                ws.call("Page.enable")
                ws.call("Page.navigate", {"url": target_url})
            if tabs[0].get("id"):
                self._active_target_id = str(tabs[0]["id"])
            focus_application("Google Chrome")
            if capture_preview:
                try:
                    time.sleep(1.0)
                    self.capture_screenshot("studio_live_preview.png", bring_to_front=False, record_task=False)
                except Exception:
                    pass
            return {"status": "navigated_blank_tab", "url": target_url}

        with CdpWebSocketClient(ws_url, timeout_seconds=420.0) as ws:
            res = ws.call("Target.createTarget", {"url": target_url})
            new_tid = res.get("targetId")
            if new_tid:
                self._active_target_id = str(new_tid)
                try:
                    self._http_json(f"/json/activate/{new_tid}")
                except Exception:
                    pass
        focus_application("Google Chrome")
        if capture_preview:
            try:
                time.sleep(1.0)
                self.capture_screenshot("studio_live_preview.png", bring_to_front=False, record_task=False)
            except Exception:
                pass
        return {"status": "created_tab", "url": target_url, "targetId": new_tid}

    def open_login_workspace_tabs(self) -> dict[str, Any]:
        """Open all standard AI Brain & Editor tabs in the persistent Chrome profile for user login."""
        workspace_urls = [
            "https://chatgpt.com/",
            "https://gemini.google.com/app",
            "https://manus.im/app",
            "https://www.flowmusic.app/",
            "https://www.capcut.com/my-edit?start_tab=video",
            "https://studio.youtube.com/",
        ]
        results = []
        for u in workspace_urls:
            try:
                results.append(self.open_or_focus_tab(u, capture_preview=False))
            except Exception as exc:
                results.append({"status": "error", "url": u, "error": str(exc)})
        # Focus ChatGPT tab first as the Master Brain and enable Owner Takeover so user can type undisturbed
        self.owner_takeover = True
        try:
            self.open_or_focus_tab("https://chatgpt.com/", capture_preview=True)
        except Exception:
            pass
        return {"status": "ok", "owner_takeover": True, "tabs_opened": results}

    def check_workspace_logins(self) -> dict[str, Any]:
        """Passively inspect open tabs in the managed Chrome window to verify login status without stealing focus."""
        self.ensure_visible_session()
        tabs = self.list_open_tabs()
        inspect_js = """
        (() => {
          const url = location.href || '';
          const title = document.title || '';
          const bodyText = (document.body ? document.body.innerText : '').slice(0, 1200);
          const hasPromptTextarea = Boolean(
            document.querySelector('#prompt-textarea, textarea, [contenteditable="true"], rich-textarea')
          );
          const buttons = Array.from(document.querySelectorAll('a, button')).map(
            el => (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase()
          );
          const hasLoginButton = buttons.some(
            t => t === 'log in' || t === 'sign in' || t === 'login' || t.startsWith('sign in ') || t.startsWith('log in ')
          );
          const isAuthPage = /accounts\\.google\\.com|auth0|login|signin|auth\\./i.test(url);
          return {
            url,
            title,
            hasPromptTextarea,
            hasLoginButton,
            isAuthPage,
            excerpt: bodyText.replace(/\\s+/g, ' ').slice(0, 260)
          };
        })()
        """
        results = []
        for t in tabs:
            ws_url = t.get("webSocketDebuggerUrl")
            if not ws_url:
                continue
            try:
                with CdpWebSocketClient(str(ws_url), timeout_seconds=5.0) as ws:
                    ev = ws.call("Runtime.evaluate", {"expression": inspect_js, "returnByValue": True})
                    val = (ev.get("result") or {}).get("value") or {}
                    url_str = str(val.get("url") or t.get("url") or "")
                    has_login_btn = bool(val.get("hasLoginButton"))
                    is_auth_page = bool(val.get("isAuthPage"))
                    has_prompt = bool(val.get("hasPromptTextarea"))
                    logged_in = (not is_auth_page) and (not has_login_btn or has_prompt)
                    # Special check for ChatGPT guest mode ("Log in" button visible in top header)
                    if "chatgpt.com" in url_str.lower() and has_login_btn:
                        logged_in = False
                    results.append({
                        "id": t.get("id"),
                        "url": url_str,
                        "title": val.get("title") or t.get("title") or "",
                        "logged_in": logged_in,
                        "has_prompt_input": has_prompt,
                        "has_login_button": has_login_btn,
                        "is_auth_redirect": is_auth_page,
                        "excerpt": val.get("excerpt", ""),
                    })
            except Exception as exc:
                results.append({
                    "id": t.get("id"),
                    "url": t.get("url"),
                    "title": t.get("title"),
                    "logged_in": False,
                    "error": str(exc),
                })
        return {"status": "ok", "tabs": results}

    def enter_owner_takeover(
        self,
        *,
        reason: str = "Manual owner sign-in or page adjustment",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Pause automated browser mutations so the owner can interact directly with the visible Chrome window."""
        self.ensure_visible_session()
        self.owner_takeover = True
        focus_application("Google Chrome")
        tabs = self.list_open_tabs()
        active_tab = tabs[0] if tabs else {}
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=active_tab.get("url", ""),
            title=active_tab.get("title", ""),
            action_kind="enter_owner_takeover",
            status="paused_for_owner",
            owner_takeover=True,
            summary={"reason": reason},
        )
        return {
            "status": "paused_for_owner",
            "owner_takeover": True,
            "reason": reason,
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

    def inspect_dom(self, *, preferred_url_pattern: str | None = None) -> dict[str, Any]:
        """Inspect the active tab's current URL, title, readyState, interactive elements, and text excerpt."""
        self.ensure_visible_session()
        target = self._get_or_create_page_target(preferred_url_pattern=preferred_url_pattern)
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
        with CdpWebSocketClient(ws_url, timeout_seconds=420.0) as ws:
            ws.call("Runtime.enable")
            res = ws.call(
                "Runtime.evaluate",
                {"expression": js_expr, "returnByValue": True},
            )
            raw_val = (res.get("result") or {}).get("value") or "{}"
            data = json.loads(raw_val)
            live_url = str(data.get("url") or "")
            if live_url:
                sk = self._domain_to_service_key(live_url)
                if self._is_specific_session_url(sk, live_url):
                    self.set_session_url(sk, live_url)
            return data

    def open_url(
        self,
        url: str,
        *,
        conversation_id: str | None = None,
        wait_seconds: float = 8.0,
        force_navigate: bool = False,
    ) -> dict[str, Any]:
        """Activate the dedicated Chrome tab for a URL's domain and navigate only if not already in the active project session."""
        self._require_not_in_takeover()
        self.ensure_visible_session()
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc or url
        sk = self._domain_to_service_key(domain)
        saved_session_url = self.get_session_url(sk)
        desired_url = (
            saved_session_url
            if (saved_session_url and not self._is_specific_session_url(sk, url))
            else url
        )

        target = self._get_or_create_page_target(preferred_url_pattern=domain)
        ws_url = str(target["webSocketDebuggerUrl"])

        with CdpWebSocketClient(ws_url, timeout_seconds=420.0) as ws:
            ws.call("Page.enable")
            ws.call("Runtime.enable")
            ws.call("Page.bringToFront")
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
            href_ev = ws.call(
                "Runtime.evaluate",
                {"expression": "window.location.href", "returnByValue": True},
            )
            live_href = str((href_ev.get("result") or {}).get("value") or target.get("url") or "")
            if self._is_specific_session_url(sk, live_href):
                self.set_session_url(sk, live_href)
                if not self._is_specific_session_url(sk, desired_url):
                    desired_url = live_href

            should_navigate = force_navigate
            if not should_navigate:
                if not live_href or domain.lower() not in live_href.lower():
                    should_navigate = True
                elif self._is_specific_session_url(sk, desired_url) and live_href.rstrip("/") != desired_url.rstrip("/"):
                    should_navigate = True

            if should_navigate:
                ws.call("Page.navigate", {"url": desired_url})
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

        dom = self.inspect_dom(preferred_url_pattern=domain)
        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=dom.get("url", desired_url),
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
        bring_to_front: bool = False,
        record_task: bool = True,
        preferred_url_pattern: str | None = None,
    ) -> dict[str, Any]:
        """Capture a PNG screenshot of the visible Chrome viewport into scratch/ch01-proof/browser-captures/."""
        self.ensure_visible_session()
        target = self._get_or_create_page_target(preferred_url_pattern=preferred_url_pattern)
        ws_url = str(target["webSocketDebuggerUrl"])
        out_path = self.policy.resolve_output(
            self.captures_dir / filename,
            overwrite=True,
            create_parents=True,
        )
        if bring_to_front:
            focus_application("Google Chrome")
        with CdpWebSocketClient(ws_url, timeout_seconds=6.0) as ws:
            ws.call("Page.enable")
            ws.call("Page.bringToFront")
            res = ws.call("Page.captureScreenshot", {"format": "png"})
            b64_data = res.get("data")
            if not b64_data:
                raise BrowserControlError("CDP Page.captureScreenshot returned empty data")
            out_path.write_bytes(base64.b64decode(b64_data))

        sha = compute_sha256(out_path)
        rec = None
        if record_task:
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
        with CdpWebSocketClient(ws_url, timeout_seconds=420.0) as ws:
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

        with CdpWebSocketClient(ws_url, timeout_seconds=420.0) as ws:
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
        timeout_seconds: float = 180.0,
    ) -> dict[str, Any]:
        """Submit a real prompt on https://chatgpt.com/ in the managed Chrome browser and extract the streaming response."""
        self._require_not_in_takeover()
        saved_url = self.get_session_url("chatgpt")
        target_url = (
            conversation_url
            if self._is_specific_session_url("chatgpt", str(conversation_url or ""))
            else (saved_url or "https://chatgpt.com/")
        )
        self.open_url(target_url, conversation_id=conversation_id, wait_seconds=12.0)
        target = self._get_or_create_page_target(preferred_url_pattern="chatgpt.com")

        ws_url = str(target["webSocketDebuggerUrl"])
        escaped_prompt = json.dumps(prompt)

        with CdpWebSocketClient(ws_url, timeout_seconds=420.0) as ws:
            ws.call("Runtime.enable")
            ws.call("Page.enable")
            ws.call("Page.bringToFront")

            # Count existing assistant turns before sending
            before_ev = ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const nodes = document.querySelectorAll('[class*="DilResponseRoot"], [class*="ResponseRoot"], [data-message-author-role="assistant"], [class*="agent-turn"], .markdown');
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

            # Dismiss any promo/onboarding modal dialog (e.g. "Connect finances") if present
            ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const dialogs = document.querySelectorAll('[role="dialog"]');
                        dialogs.forEach(d => {
                            const closeBtn = d.querySelector('button[aria-label*="Close"], button[aria-label*="close"], button:has(svg)');
                            if (closeBtn) closeBtn.click();
                        });
                        document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true}));
                        return dialogs.length;
                    })()
                    """,
                    "returnByValue": True,
                },
            )
            time.sleep(0.3)

            rate_limit_detected = False
            final_text = ""
            insert_ev = ws.call(
                "Runtime.evaluate",
                {
                    "expression": f"""
                    (() => {{
                        const el = document.querySelector('#prompt-textarea, #mobile-composer-prompt, textarea[name="prompt"], div[contenteditable="true"], textarea');
                        if (!el) return JSON.stringify({{ok: false, error: 'ChatGPT prompt input not found'}});
                        const textVal = {escaped_prompt};
                        el.focus();
                        if (el.tagName.toLowerCase() === 'textarea') {{
                            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
                            setter.call(el, textVal);
                            el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        }} else {{
                            document.execCommand('selectAll', false, null);
                            document.execCommand('insertText', false, textVal);
                            if ((el.innerText || '').trim().length < 5) {{
                                el.innerHTML = textVal.split('\\n').map(l => '<p>' + (l.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') || '<br>') + '</p>').join('');
                                el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: textVal}}));
                            }} else {{
                                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                            }}
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
            while time.monotonic() < deadline:
                self._check_cancel()
                time.sleep(2.5)
                poll_ev = ws.call(
                    "Runtime.evaluate",
                    {
                        "expression": """
                        (() => {
                            const nodes = Array.from(document.querySelectorAll('[class*="DilResponseRoot"], [class*="ResponseRoot"], [data-message-author-role="assistant"], [class*="agent-turn"], .markdown'));
                            const stopBtn = document.querySelector('button[data-testid="stop-button"], button[aria-label*="Stop"], button:has(rect), button:has(svg rect)');
                            let nodeText = '';
                            if (nodes.length > 0) {
                                const last = nodes[nodes.length - 1];
                                nodeText = (last ? last.innerText : '').trim();
                            }
                            const bodyText = document.body ? document.body.innerText : '';
                            const saidParts = bodyText.split('ChatGPT said:');
                            let saidText = '';
                            if (saidParts.length > 1) {
                                let raw = saidParts[saidParts.length - 1];
                                const cutMarkers = ['ChatGPT can make mistakes', 'ChatGPT is AI and can make mistakes', 'Get responses tailored to you', 'Log in to get answers'];
                                for (const m of cutMarkers) {
                                    const idx = raw.indexOf(m);
                                    if (idx !== -1) raw = raw.slice(0, idx);
                                }
                                saidText = raw.trim();
                            }
                            const isRateLimited = /you've reached (your|our) (limit|usage limit)|too many requests|try again in \\d+|rate limit reached|plan limit|limit reached for gpt/i.test(bodyText) ||
                                Boolean(document.querySelector('[data-testid="error-banner"], .text-token-text-error, .alert-error'));
                            return JSON.stringify({
                                url: window.location.href,
                                nodeCount: nodes.length,
                                nodeText: nodeText,
                                saidCount: Math.max(0, saidParts.length - 1),
                                saidText: saidText,
                                streaming: Boolean(stopBtn),
                                rateLimited: isRateLimited
                            });
                        })()
                        """,
                        "returnByValue": True,
                    },
                )
                p_data = json.loads((poll_ev.get("result") or {}).get("value") or "{}")
                if p_data.get("rateLimited"):
                    rate_limit_detected = True
                    break

                cand = ""
                if p_data.get("nodeText"):
                    cand = str(p_data["nodeText"]).strip()
                elif p_data.get("saidText"):
                    cand = str(p_data["saidText"]).strip()

                if cand and not p_data.get("streaming"):
                    if cand == last_text and len(cand) > 15:
                        stable_ticks += 1
                        if stable_ticks >= 2:
                            final_text = cand
                            break
                    else:
                        last_text = cand
                        stable_ticks = 0

        shot = self.capture_screenshot(
            screenshot_name,
            conversation_id=conversation_id,
            preferred_url_pattern="chatgpt.com",
        )
        dom = self.inspect_dom(preferred_url_pattern="chatgpt.com")
        live_url = str(dom.get("url") or target_url)
        self.set_session_url("chatgpt", live_url)
        if rate_limit_detected:
            rec = self.store.record_browser_task(
                conversation_id=conversation_id,
                session_id=self.session_id,
                url=live_url,
                title=str(dom.get("title") or "ChatGPT (Rate Limited)"),
                action_kind="consult_chatgpt_web",
                status="rate_limited",
                artifact_path=shot["path"],
                artifact_sha256=shot["sha256"],
                summary={"prompt": prompt[:240], "reason": "Rate limit / tab limit encountered", "url": live_url},
            )
            return {
                "site": "chatgpt_web",
                "status": "rate_limited",
                "url": live_url,
                "title": str(dom.get("title") or "ChatGPT"),
                "prompt": prompt,
                "response": "",
                "reason": "ChatGPT rate limit or message limit reached",
                "screenshot": shot,
                "task": rec,
            }

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
        saved_url = self.get_session_url("gemini")
        target_url = (
            conversation_url
            if self._is_specific_session_url("gemini", str(conversation_url or ""))
            else (saved_url or "https://gemini.google.com/app")
        )
        self.open_url(target_url, conversation_id=conversation_id, wait_seconds=12.0)
        target = self._get_or_create_page_target(preferred_url_pattern="gemini.google.com")
        ws_url = str(target["webSocketDebuggerUrl"])
        escaped_prompt = json.dumps(prompt)

        with CdpWebSocketClient(ws_url, timeout_seconds=420.0) as ws:
            ws.call("Runtime.enable")
            ws.call("Page.enable")
            ws.call("Page.bringToFront")

            before_ev = ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const models = Array.from(document.querySelectorAll("model-response"));
                        const count = models.length || Math.max(0, (document.body ? document.body.innerText : "").split('Gemini said').length - 1);
                        return JSON.stringify({saidCount: count});
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
                time.sleep(2.5)
                poll_ev = ws.call(
                    "Runtime.evaluate",
                    {
                        "expression": """
                        (() => {
                            const models = Array.from(document.querySelectorAll("model-response"));
                            const saidCount = models.length;
                            let saidText = "";
                            if (models.length > 0) {
                                const last = models[models.length - 1];
                                const content = last.querySelector("message-content") || last.querySelector(".model-response-text") || last;
                                saidText = (content ? content.innerText : "").trim();
                            }
                            if (!saidText) {
                                const bodyText = document.body ? document.body.innerText : '';
                                const parts = bodyText.split('Gemini said');
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
                            }
                            return JSON.stringify({
                                url: window.location.href,
                                saidCount: saidCount || Math.max(0, (document.body ? document.body.innerText : '').split('Gemini said').length - 1),
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

        shot = self.capture_screenshot(
            screenshot_name,
            conversation_id=conversation_id,
            preferred_url_pattern="gemini.google.com",
        )
        dom = self.inspect_dom(preferred_url_pattern="gemini.google.com")
        live_url = str(dom.get("url") or target_url)
        self.set_session_url("gemini", live_url)
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
        file_path: str | Path | None = None,
        conversation_url: str | None = None,
        conversation_id: str | None = None,
        screenshot_name: str = "manus_web_capture.png",
        timeout_seconds: float = 90.0,
    ) -> dict[str, Any]:
        """Submit a specialist task on https://manus.im/app in the managed Chrome browser, optionally attaching a file, and read the response."""
        self._require_not_in_takeover()
        saved_url = self.get_session_url("manus")
        target_url = (
            conversation_url
            if self._is_specific_session_url("manus", str(conversation_url or ""))
            else (saved_url or "https://manus.im/app")
        )
        self.open_url(target_url, conversation_id=conversation_id, wait_seconds=12.0)
        target = self._get_or_create_page_target(preferred_url_pattern="manus.im")

        attached_file_str = None
        file_attached = False
        if file_path:
            p = Path(file_path).expanduser().resolve(strict=True)
            attached_file_str = str(p)

        ws_url = str(target["webSocketDebuggerUrl"])
        escaped_prompt = json.dumps(prompt)

        with CdpWebSocketClient(ws_url, timeout_seconds=420.0) as ws:
            ws.call("Runtime.enable")
            ws.call("Page.enable")
            ws.call("Page.bringToFront")

            if attached_file_str:
                try:
                    ws.call("DOM.enable")
                    # Step 1: Install click interceptor so dynamic file input is attached to DOM
                    ws.call(
                        "Runtime.evaluate",
                        {
                            "expression": """
                            (() => {
                                window.__manus_captured_file_input = null;
                                const origClick = HTMLInputElement.prototype.click;
                                HTMLInputElement.prototype.click = function() {
                                    if (this.type === 'file') {
                                        this.id = '__manus_file_input__';
                                        document.body.appendChild(this);
                                        window.__manus_captured_file_input = this;
                                        return;
                                    }
                                    return origClick.apply(this, arguments);
                                };
                                return true;
                            })()
                            """
                        },
                    )

                    # Step 2: Open attachment menu via plus button
                    ws.call(
                        "Runtime.evaluate",
                        {
                            "expression": """
                            (() => {
                                const btn = document.querySelector("button:has(svg.lucide-plus), button:has(svg.lucide-paperclip)");
                                if (btn) {
                                    btn.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true}));
                                    btn.dispatchEvent(new MouseEvent("mousedown", {bubbles: true}));
                                    btn.click();
                                    return true;
                                }
                                return false;
                            })()
                            """
                        },
                    )
                    time.sleep(0.5)

                    # Step 3: Click 'Local files'
                    ws.call(
                        "Runtime.evaluate",
                        {
                            "expression": """
                            (() => {
                                const localEl = Array.from(document.querySelectorAll("*")).find(el => el.innerText && el.innerText.trim() === "Local files" && el.children.length === 0);
                                if (localEl) {
                                    localEl.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true}));
                                    localEl.dispatchEvent(new MouseEvent("mousedown", {bubbles: true}));
                                    localEl.click();
                                    return true;
                                }
                                return false;
                            })()
                            """
                        },
                    )
                    time.sleep(0.5)

                    # Step 4: Set file input files via CDP DOM
                    doc = ws.call("DOM.getDocument", {"depth": -1})
                    root_node_id = int((doc.get("root") or {}).get("nodeId") or 1)
                    q_res = ws.call(
                        "DOM.querySelector",
                        {"nodeId": root_node_id, "selector": "#__manus_file_input__, input[type='file']"},
                    )
                    file_node_id = int(q_res.get("nodeId") or 0)
                    if file_node_id > 0:
                        ws.call("DOM.setFileInputFiles", {"nodeId": file_node_id, "files": [attached_file_str]})
                        file_attached = True

                        # Step 5: Wait for upload to complete
                        for _ in range(35):
                            time.sleep(1.0)
                            up_res = ws.call(
                                "Runtime.evaluate",
                                {
                                    "expression": """
                                    (() => {
                                        const body = document.body ? document.body.innerText : '';
                                        return !body.includes('Uploading');
                                    })()
                                    """,
                                    "returnByValue": True,
                                },
                            )
                            if (up_res.get("result") or {}).get("value") is True:
                                break
                except Exception:
                    pass

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
                            const buttons = Array.from(document.querySelectorAll('button'));
                            const sendBtn = buttons.find(b => b.className.includes('accent-send-button') || (b.type === 'submit' && !b.innerText.includes('Upgrade') && b.querySelector('svg')));
                            if (sendBtn && !sendBtn.disabled) {
                                sendBtn.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
                                sendBtn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                                sendBtn.click();
                                return 'clicked_button';
                            }
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
                            // Also check preview view lines
                            const viewLines = Array.from(document.querySelectorAll('.view-line')).map(l => l.innerText).filter(Boolean);
                            if (viewLines.length > 0) {
                                extracted = viewLines.join('\\n');
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
                if (int(p_data.get("completedCount") or 0) > before_completed or 'Task completed' in p_data.get('bodyExcerpt', '')) and p_data.get("extracted"):
                    final_text = str(p_data["extracted"]).strip()
                    break

        shot = self.capture_screenshot(
            screenshot_name,
            conversation_id=conversation_id,
            preferred_url_pattern="manus.im",
        )
        dom = self.inspect_dom(preferred_url_pattern="manus.im")
        live_url = str(dom.get("url") or target_url)
        self.set_session_url("manus", live_url)
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
            "file_attached": file_attached,
            "attached_file": attached_file_str,
            "url": live_url,
            "title": str(dom.get("title") or "Manus"),
            "prompt": prompt,
            "response": final_text,
            "login_gate_message": login_gate_text,
            "dom_excerpt": str(dom.get("bodyTextExcerpt") or "")[:600],
            "screenshot": shot,
            "task": rec,
        }

    def inspect_youtube_studio_and_stage_upload(
        self,
        *,
        video_path: str | Path,
        title: str,
        description: str,
        tags: list[str] | None = None,
        thumbnail_path: str | Path | None = None,
        made_for_kids: bool = True,
        conversation_id: str | None = None,
        screenshot_name: str = "youtube_studio_stage_capture.png",
        target_url: str = "https://studio.youtube.com/",
    ) -> dict[str, Any]:
        """Navigate to YouTube Studio in the managed Chrome browser, check authentication, and stage or prepare video upload."""
        self._require_not_in_takeover()
        vid = Path(video_path).expanduser().resolve(strict=True)
        vid_sha = compute_sha256(vid)

        self.open_url(target_url, conversation_id=conversation_id, wait_seconds=10.0)
        time.sleep(2.0)

        dom = self.inspect_dom()
        live_url = str(dom.get("url") or target_url)
        body_text = str(dom.get("bodyTextExcerpt") or "")

        login_gate = (
            "accounts.google.com" in live_url
            or "Sign in to YouTube" in body_text
            or "Sign in" in str(dom.get("title") or "")
            or "Choose an account" in body_text
        )

        shot = self.capture_screenshot(screenshot_name, conversation_id=conversation_id)

        if login_gate:
            status = "awaiting_owner_youtube_login"
            summary_msg = (
                f"YouTube Studio opened at {live_url}. Google authentication is required to access your YouTube channel. "
                "Use Owner Takeover in Studio to sign into your channel once in this Chrome session."
            )
        else:
            status = "ready_for_upload"
            summary_msg = (
                f"YouTube Studio channel dashboard active at {live_url}. Video {vid.name} ({vid.stat().st_size} bytes) "
                f"ready to upload with title: '{title[:50]}...'"
            )

        rec = self.store.record_browser_task(
            conversation_id=conversation_id,
            session_id=self.session_id,
            url=live_url,
            title=str(dom.get("title") or "YouTube Studio"),
            action_kind="inspect_youtube_studio_and_stage_upload",
            status=status,
            artifact_path=shot["path"],
            artifact_sha256=shot["sha256"],
            summary={
                "video_path": str(vid),
                "video_bytes": vid.stat().st_size,
                "video_sha256": vid_sha,
                "title": title,
                "login_gate": login_gate,
                "status": status,
                "url": live_url,
                "message": summary_msg,
            },
        )

        return {
            "status": status,
            "authenticated": not login_gate,
            "login_gate": login_gate,
            "url": live_url,
            "title": str(dom.get("title") or "YouTube Studio"),
            "video_path": str(vid),
            "video_sha256": vid_sha,
            "metadata_package": {
                "title": title,
                "description": description,
                "tags": tags or [],
                "made_for_kids": made_for_kids,
                "thumbnail_path": str(thumbnail_path) if thumbnail_path else None,
            },
            "screenshot": shot,
            "message": summary_msg,
            "task": rec,
        }
