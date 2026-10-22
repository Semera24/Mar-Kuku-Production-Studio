"""Google Flow Music (Lyria 3.5) browser adapter for Mar and Kuku Production Studio.

Automates:
1. Navigation and authentication verification on https://www.flowmusic.app/ (dancingPatina PLUS).
2. Selecting reference sessions (e.g. Five Flowers in the Garden).
3. Clicking 3-dot menu or direct Remix button (button[aria-label="Remix <track>"]).
4. Injecting approved chapter dialogue prompt into the Producer composer (textarea[placeholder="Ask Producer..."]).
5. Monitoring generation progress via CDP until complete.
6. Triggering track download and verifying the saved audio master/stems in chapter folders.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from mk_studio.browser.controller import BrowserControlError, BrowserController, CdpWebSocketClient
from mk_studio.config import RootPolicy
from mk_studio.media import compute_sha256, probe_audio_stream_0a0
from mk_studio.state import StateStore


class GoogleFlowMusicAdapter:
    """Automates Google Flow Music / Lyria 3.5 browser workflows."""

    FLOW_BASE_URL = "https://www.flowmusic.app/"
    APPROVED_FIVE_FLOWERS_SESSION_URL = "https://www.flowmusic.app/session/c6d54460-584e-457b-97e2-5a9ee8189e74"

    def __init__(self, browser: BrowserController) -> None:
        self.browser = browser

    def check_connection_and_auth(self) -> dict[str, Any]:
        """Check if Flow Music is open and verify if dancingPatina PLUS is authenticated."""
        dom = self.browser.inspect_dom() if self.browser.is_reachable() else {}
        url = str(dom.get("url") or "")
        if not url.startswith(self.FLOW_BASE_URL):
            self.browser.open_url(self.FLOW_BASE_URL, wait_seconds=6.0)
            time.sleep(2.0)
            dom = self.browser.inspect_dom()

        body_text = str(dom.get("bodyTextExcerpt") or "")
        buttons = [str(b.get("text") or "") for b in dom.get("buttons") or []]
        button_joined = " ".join(buttons)

        is_logged_in = "dancingPatina" in body_text or "dancingPatina" in button_joined or "New session" in button_joined
        login_button_present = "Log in" in button_joined or "Sign up" in button_joined

        return {
            "service_name": "Google Flow Music (Lyria 3.5)",
            "url": str(dom.get("url") or self.FLOW_BASE_URL),
            "authenticated": bool(is_logged_in and not login_button_present),
            "account_handle": "dancingPatina PLUS" if is_logged_in else None,
            "login_required": bool(login_button_present and not is_logged_in),
            "has_reference_sessions": "Five Flowers" in body_text or "Colour Garden" in body_text,
        }

    def open_reference_session(self, session_url: str | None = None) -> dict[str, Any]:
        """Open the approved reference session (defaults to Five Flowers Remix)."""
        target_url = session_url or self.APPROVED_FIVE_FLOWERS_SESSION_URL
        self.browser.open_url(target_url, wait_seconds=6.0)
        time.sleep(2.0)
        dom = self.browser.inspect_dom()
        shot = self.browser.capture_screenshot("flowmusic_session_opened.png")

        return {
            "session_url": target_url,
            "title": str(dom.get("title") or ""),
            "screenshot": shot["path"],
            "has_five_flowers": "Five Flowers" in str(dom.get("bodyTextExcerpt") or ""),
        }

    def trigger_remix_for_track(self, track_name: str = "Five Flowers in the Garden") -> dict[str, Any]:
        """Click the Remix button for the specified track."""
        target = self.browser._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])
        escaped_track = json.dumps(track_name)

        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Runtime.enable")
            res = ws.call(
                "Runtime.evaluate",
                {
                    "expression": f"""
                    (() => {{
                        const track = {escaped_track};
                        // Priority 1: Direct remix button with aria-label
                        let btn = document.querySelector(`button[aria-label="Remix ${{track}}"]`);
                        if (!btn) {{
                            btn = Array.from(document.querySelectorAll("button")).find(b => {{
                                const a = b.getAttribute("aria-label") || "";
                                return a.toLowerCase().includes("remix") && a.toLowerCase().includes(track.toLowerCase());
                            }});
                        }}
                        if (btn) {{
                            btn.click();
                            return {{clicked: true, method: "aria-label", aria: btn.getAttribute("aria-label")}};
                        }}
                        // Priority 2: 3-dot options menu
                        const moreBtn = document.querySelector(`button[aria-label="More options for ${{track}}"]`);
                        if (moreBtn) {{
                            moreBtn.click();
                            return {{clicked: true, method: "more-options-menu", aria: moreBtn.getAttribute("aria-label")}};
                        }}
                        return {{clicked: false, error: "Remix button not found for track: " + track}};
                    }})()
                    """,
                    "returnByValue": True,
                },
            )
            val = (res.get("result") or {}).get("value") or {}
            if not val.get("clicked"):
                raise BrowserControlError(f"Failed to click Remix for track '{track_name}': {val.get('error')}")

        time.sleep(2.0)
        shot = self.browser.capture_screenshot("flowmusic_remix_triggered.png")
        return {
            "track_name": track_name,
            "remix_clicked": True,
            "details": val,
            "screenshot": shot["path"],
        }

    def submit_approved_prompt(
        self,
        prompt_text: str,
        *,
        conversation_id: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """Type the approved chapter script prompt into the Producer composer and click Send.
        
        Strictly requires button[aria-label="Send message"] to be enabled and clicked.
        Never marks filled-but-unsent input as submitted.
        """
        if not prompt_text or not prompt_text.strip():
            raise BrowserControlError("Cannot submit empty prompt to Flow Music Producer")

        target = self.browser._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])
        escaped_prompt = json.dumps(prompt_text)

        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Runtime.enable")
            insert_res = ws.call(
                "Runtime.evaluate",
                {
                    "expression": f"""
                    (() => {{
                        const composer = document.querySelector('textarea[placeholder="Ask Producer..."], textarea[placeholder="Describe your song..."], textarea[placeholder*="create"], textarea');
                        if (!composer) return {{ok: false, error: "Composer textarea not found"}};
                        composer.focus();
                        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
                        if (nativeSetter) {{
                            nativeSetter.call(composer, {escaped_prompt});
                        }} else {{
                            composer.value = {escaped_prompt};
                        }}
                        composer.dispatchEvent(new Event("input", {{bubbles: true}}));
                        composer.dispatchEvent(new Event("change", {{bubbles: true}}));

                        // Find send button specifically
                        const sendBtn = document.querySelector('button[aria-label="Send message"]');
                        if (!sendBtn) {{
                            return {{ok: false, error: "Send button with aria-label='Send message' not found"}};
                        }}
                        if (sendBtn.disabled) {{
                            return {{ok: false, error: "Send button is disabled after inserting prompt into composer"}};
                        }}
                        sendBtn.click();
                        return {{ok: true, buttonClicked: "Send message"}};
                    }})()
                    """,
                    "returnByValue": True,
                },
            )
            info = (insert_res.get("result") or {}).get("value") or {}
            if not info.get("ok"):
                raise BrowserControlError(f"Failed to submit prompt to Flow Music Producer: {info.get('error')}")

        time.sleep(3.0)
        shot = self.browser.capture_screenshot("flowmusic_prompt_submitted.png", conversation_id=conversation_id)
        return {
            "prompt_length": len(prompt_text),
            "submitted": True,
            "details": info,
            "screenshot": shot["path"],
        }

    def list_session_tracks(self) -> list[dict[str, Any]]:
        """List all tracks present in the current Flow Music session."""
        target = self.browser._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])

        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Runtime.enable")
            res = ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const playBtns = Array.from(document.querySelectorAll('button[aria-label^="Play "]'));
                        const tracks = [];
                        for (const b of playBtns) {
                            const label = b.getAttribute("aria-label") || "";
                            const name = label.replace(/^Play\\s+/, "").trim();
                            if (name && !tracks.some(t => t.name === name)) {
                                tracks.push({
                                    name: name,
                                    play_aria: label,
                                    has_more_options: !!document.querySelector(`button[aria-label="More options for ${name}"]`),
                                    has_remix: !!document.querySelector(`button[aria-label="Remix ${name}"]`),
                                });
                            }
                        }
                        return tracks;
                    })()
                    """,
                    "returnByValue": True,
                },
            )
            return list((res.get("result") or {}).get("value") or [])

    def download_track_audio(
        self,
        track_name: str,
        *,
        track_index: int = 0,
        download_format: str = "WAV",
        destination_dir: Path | str | None = None,
        timeout_seconds: float = 60.0,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Automate opening track 3-dot menu, expanding Download, selecting format (WAV/MP3/stems), and collecting downloaded audio."""
        import shutil

        downloads_dir = Path.home() / "Downloads"
        dest = Path(destination_dir) if destination_dir else (self.browser.policy.output_root_resolved / "downloads")
        dest.mkdir(parents=True, exist_ok=True)

        target = self.browser._get_or_create_page_target()
        ws_url = str(target["webSocketDebuggerUrl"])

        before_files = set(downloads_dir.glob("*.wav")) | set(downloads_dir.glob("*.mp3")) | set(downloads_dir.glob("*.zip"))
        escaped_track = json.dumps(track_name)
        escaped_fmt = json.dumps(download_format.upper())
        idx_val = int(track_index)

        with CdpWebSocketClient(ws_url) as ws:
            ws.call("Runtime.enable")
            # 1. Close any open menus with Escape
            ws.call("Runtime.evaluate", {"expression": 'document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}))'})
            time.sleep(0.3)

            # 2. Click More options for track
            open_menu_res = ws.call(
                "Runtime.evaluate",
                {
                    "expression": f"""
                    (() => {{
                        const track = {escaped_track};
                        const btns = Array.from(document.querySelectorAll(`button[aria-label="More options for ${{track}}"]`));
                        if (btns.length === 0) return {{ok: false, error: "More options button not found for track: " + track}};
                        const idx = {idx_val} < btns.length ? {idx_val} : 0;
                        const btn = btns[idx];
                        btn.scrollIntoView({{behavior: "instant", block: "center"}});
                        btn.dispatchEvent(new PointerEvent("pointerdown", {{bubbles: true, cancelable: true}}));
                        btn.dispatchEvent(new MouseEvent("mousedown", {{bubbles: true, cancelable: true}}));
                        btn.dispatchEvent(new MouseEvent("mouseup", {{bubbles: true, cancelable: true}}));
                        btn.dispatchEvent(new MouseEvent("click", {{bubbles: true, cancelable: true}}));
                        return {{ok: true, buttonAria: btn.getAttribute("aria-label"), chosenIndex: idx, totalCount: btns.length}};
                    }})()
                    """,
                    "returnByValue": True,
                },
            )
            menu_info = (open_menu_res.get("result") or {}).get("value") or {}
            if not menu_info.get("ok"):
                raise BrowserControlError(f"Failed to open More options menu for '{track_name}': {menu_info.get('error')}")

            time.sleep(0.5)

            # 3. Focus Download menuitem and dispatch native CDP ArrowRight to expand submenu
            ws.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => {
                        const dl = Array.from(document.querySelectorAll("[role=menuitem]")).find(el => el.innerText.trim().startsWith("Download"));
                        if (dl) dl.focus();
                    })()
                    """
                },
            )
            time.sleep(0.2)
            ws.call("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": "ArrowRight",
                "code": "ArrowRight",
                "windowsVirtualKeyCode": 39,
                "nativeVirtualKeyCode": 39,
            })
            ws.call("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": "ArrowRight",
                "code": "ArrowRight",
                "windowsVirtualKeyCode": 39,
                "nativeVirtualKeyCode": 39,
            })
            time.sleep(0.5)

            # 4. Click target format (e.g. WAV, MP3, Get stems)
            click_fmt_res = ws.call(
                "Runtime.evaluate",
                {
                    "expression": f"""
                    (() => {{
                        const targetFmt = {escaped_fmt};
                        const items = Array.from(document.querySelectorAll("[role=menuitem]"));
                        const match = items.find(el => el.innerText.trim().toUpperCase() === targetFmt || el.innerText.trim().toUpperCase().includes(targetFmt));
                        if (!match) return {{ok: false, available: items.map(i => i.innerText.trim())}};
                        match.dispatchEvent(new PointerEvent("pointerdown", {{bubbles: true, cancelable: true}}));
                        match.dispatchEvent(new MouseEvent("mousedown", {{bubbles: true, cancelable: true}}));
                        match.dispatchEvent(new MouseEvent("mouseup", {{bubbles: true, cancelable: true}}));
                        match.dispatchEvent(new MouseEvent("click", {{bubbles: true, cancelable: true}}));
                        return {{ok: true, clicked: match.innerText.trim()}};
                    }})()
                    """,
                    "returnByValue": True,
                },
            )
            fmt_info = (click_fmt_res.get("result") or {}).get("value") or {}
            if not fmt_info.get("ok"):
                raise BrowserControlError(f"Failed to click format '{download_format}': available options: {fmt_info.get('available')}")

        clean_track = re.sub(r"[^A-Za-z0-9]", "", track_name.lower())

        # 5. Poll for completed file in ~/Downloads
        deadline = time.time() + timeout_seconds
        downloaded_file: Path | None = None
        while time.time() < deadline:
            time.sleep(1.0)
            current_files = (
                set(downloads_dir.glob("*.wav"))
                | set(downloads_dir.glob("*.mp3"))
                | set(downloads_dir.glob("*.zip"))
            )
            diff = current_files - before_files
            crdownloads = list(downloads_dir.glob("*.crdownload"))
            if diff and not crdownloads:
                downloaded_file = sorted(diff, key=lambda f: f.stat().st_mtime, reverse=True)[0]
                break

        # Fallback: Check if a matching track was recently completed in ~/Downloads
        if not downloaded_file:
            matching_recent = [
                f
                for f in downloads_dir.glob(f"*.{download_format.lower()}")
                if clean_track[:8] in re.sub(r"[^A-Za-z0-9]", "", f.stem.lower())
                and (time.time() - f.stat().st_mtime) < 300
            ]
            if matching_recent:
                downloaded_file = sorted(matching_recent, key=lambda f: f.stat().st_mtime, reverse=True)[0]

        if not downloaded_file or not downloaded_file.is_file():
            raise BrowserControlError(f"Download timed out after {timeout_seconds}s waiting for {download_format} of '{track_name}'")

        # 6. Copy to destination and strictly verify probe specs
        target_file = dest / downloaded_file.name
        shutil.copy2(downloaded_file, target_file)
        sha256 = compute_sha256(target_file)

        # Verify track name match (avoid accepting unrelated downloads)
        clean_track = re.sub(r"[^A-Za-z0-9]", "", track_name.lower())
        clean_stem = re.sub(r"[^A-Za-z0-9]", "", target_file.stem.lower())
        # Track stem should match or overlap with requested track name
        if clean_track[:10] not in clean_stem and clean_stem[:10] not in clean_track:
            raise BrowserControlError(
                f"Downloaded file name mismatch: expected '{track_name}', got '{target_file.name}'"
            )

        try:
            audio_probe = probe_audio_stream_0a0(target_file)
            if audio_probe.duration_seconds <= 0 or audio_probe.sample_rate <= 0:
                raise BrowserControlError(f"Invalid audio stream metrics for {target_file.name}: {audio_probe}")
            probe_dict = {
                "duration_seconds": round(audio_probe.duration_seconds, 3),
                "sample_rate": audio_probe.sample_rate,
                "channels": audio_probe.channels,
                "codec_name": audio_probe.codec_name,
            }
        except Exception as e:
            raise BrowserControlError(f"Audio probe failed for downloaded file {target_file.name}: {e}")

        shot = self.browser.capture_screenshot("flowmusic_track_downloaded.png", conversation_id=conversation_id)

        return {
            "track_name": track_name,
            "format": download_format,
            "original_download_path": str(downloaded_file),
            "saved_path": str(target_file),
            "size_bytes": target_file.stat().st_size,
            "sha256": sha256,
            "audio_specs": probe_dict,
            "screenshot": shot["path"],
        }
