"""Telegram Remote Gateway for Mar & Kuku Production Studio.

Enables mobile notifications and remote two-way approval loops:
1. Pushes notifications to the owner's phone when a production step finishes or awaits approval.
2. Listens for incoming owner replies (e.g. 'next', 'approve', 'stop', or revisions) and dispatches them to the Studio orchestrator.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("mk_studio.telegram")


class TelegramService:
    """Manages Telegram bot configuration, push notifications, and remote mobile interaction."""

    def __init__(self, storage_dir: Path | None = None) -> None:
        self.storage_dir = storage_dir or (Path(__file__).resolve().parents[3] / "data" / "telegram")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.storage_dir / "config.json"
        self._lock = threading.RLock()
        self._polling_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._orchestrator: Any = None
        self._last_update_id = 0
        self._ensure_config()

    def _ensure_config(self) -> None:
        if not self.config_file.is_file():
            default_config = {
                "bot_token": "",
                "chat_id": "",
                "enabled": False,
                "notify_on_step_complete": True,
                "notify_on_approval_needed": True,
                "created_at": time.time(),
            }
            self.config_file.write_text(json.dumps(default_config, indent=2), encoding="utf-8")

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            if not self.config_file.is_file():
                return {}
            try:
                cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
                # Mask token for security in UI
                raw_token = cfg.get("bot_token") or ""
                cfg["is_configured"] = bool(raw_token and cfg.get("chat_id"))
                cfg["token_masked"] = (raw_token[:6] + "..." + raw_token[-4:]) if len(raw_token) > 10 else ("***" if raw_token else "")
                return cfg
            except Exception:
                return {}

    def save_config(
        self,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
        notify_on_step_complete: bool = True,
        notify_on_approval_needed: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            cfg = {
                "bot_token": bot_token.strip(),
                "chat_id": str(chat_id).strip(),
                "enabled": bool(enabled),
                "notify_on_step_complete": bool(notify_on_step_complete),
                "notify_on_approval_needed": bool(notify_on_approval_needed),
                "updated_at": time.time(),
            }
            self.config_file.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            if cfg["enabled"] and cfg["bot_token"] and cfg["chat_id"]:
                self.start_polling(self._orchestrator)
            else:
                self.stop_polling()
            return self.get_config()

    def send_message(self, text: str, parse_mode: str = "HTML") -> dict[str, Any]:
        """Send an outgoing push message to the configured owner chat."""
        with self._lock:
            if not self.config_file.is_file():
                return {"ok": False, "error": "Not configured"}
            try:
                cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

            token = cfg.get("bot_token")
            chat_id = cfg.get("chat_id")
            if not token or not chat_id:
                return {"ok": False, "error": "Missing bot_token or chat_id"}

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data
            except Exception as exc:
                logger.error("Failed to send Telegram message: %s", exc)
                return {"ok": False, "error": str(exc)}

    def notify_step(
        self,
        step_title: str,
        status: str,
        summary: str,
        requires_approval: bool = False,
        next_step_hint: str | None = None,
    ) -> None:
        """Helper to format and push workflow progress alerts to phone."""
        cfg = self.get_config()
        if not cfg.get("enabled"):
            return

        icon = "⏳" if requires_approval else ("✅" if status == "completed" else "ℹ️")
        gate_text = (
            "\n\n🛑 <b>Action Required:</b>"
            f"\nReply <code>Next</code> or <code>Approve</code> to proceed, or text your correction!"
            if requires_approval
            else (f"\n\n👉 <i>Next automated step: {next_step_hint}</i>" if next_step_hint else "")
        )

        msg = (
            f"{icon} <b>Mar & Kuku Studio Alert</b>\n\n"
            f"<b>Step:</b> <code>{step_title}</code>\n"
            f"<b>Status:</b> <code>{status}</code>\n\n"
            f"📝 <b>Details:</b>\n{summary}"
            f"{gate_text}"
        )
        self.send_message(msg)

    def test_connection(self, token: str | None = None, chat_id: str | None = None) -> dict[str, Any]:
        """Test Telegram Bot API connection and verify bot username & chat accessibility."""
        cfg = self.get_config()
        eff_token = (token or cfg.get("bot_token") or "").strip()
        eff_chat = (chat_id or cfg.get("chat_id") or "").strip()

        if not eff_token:
            return {"ok": False, "error": "Bot token is empty."}

        # 1. Verify Bot Token with getMe
        try:
            url_me = f"https://api.telegram.org/bot{eff_token}/getMe"
            with urllib.request.urlopen(url_me, timeout=8.0) as r:
                bot_info = json.loads(r.read().decode("utf-8"))
                if not bot_info.get("ok"):
                    return {"ok": False, "error": "Invalid Bot Token."}
                bot_username = (bot_info.get("result") or {}).get("username")
        except Exception as exc:
            return {"ok": False, "error": f"Failed to connect to Telegram API: {exc}"}

        # 2. If chat_id provided, send greeting test message
        sent = False
        if eff_chat:
            test_msg = (
                f"🚀 <b>Mar & Kuku Studio — Remote Telegram Gateway Connected!</b>\n\n"
                f"• <b>Bot:</b> @{bot_username}\n"
                f"• <b>Status:</b> Two-way mobile notifications & approval ready.\n\n"
                f"You will receive production alerts here and can reply directly from your phone!"
            )
            res = self.send_message(test_msg)
            sent = res.get("ok", False)

        return {
            "ok": True,
            "bot_username": bot_username,
            "message_sent": sent,
            "chat_id": eff_chat,
        }

    def start_polling(self, orchestrator: Any) -> None:
        """Start long-polling thread to receive instructions from the user's phone."""
        self._orchestrator = orchestrator
        cfg = self.get_config()
        if not cfg.get("enabled") or not cfg.get("bot_token") or not cfg.get("chat_id"):
            return

        if self._polling_thread and self._polling_thread.is_alive():
            return

        self._stop_event.clear()
        self._polling_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="TelegramBotPoller",
        )
        self._polling_thread.start()
        logger.info("Telegram mobile polling started.")

    def stop_polling(self) -> None:
        self._stop_event.set()
        if self._polling_thread and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=2.0)
        self._polling_thread = None
        logger.info("Telegram mobile polling stopped.")

    def _poll_loop(self) -> None:
        """Long-polls getUpdates from Telegram and forwards messages to the Studio orchestrator."""
        while not self._stop_event.is_set():
            cfg = self.get_config()
            token = cfg.get("bot_token")
            target_chat_id = str(cfg.get("chat_id") or "")

            if not token or not target_chat_id or not cfg.get("enabled"):
                time.sleep(3.0)
                continue

            try:
                url = f"https://api.telegram.org/bot{token}/getUpdates?offset={self._last_update_id + 1}&timeout=15"
                with urllib.request.urlopen(url, timeout=20.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("ok"):
                        updates = data.get("result") or []
                        for upd in updates:
                            upd_id = upd.get("update_id", 0)
                            if upd_id > self._last_update_id:
                                self._last_update_id = upd_id

                            msg = upd.get("message") or {}
                            chat = msg.get("chat") or {}
                            sender_chat_id = str(chat.get("id") or "")
                            text = (msg.get("text") or "").strip()

                            # Only process commands from verified owner chat_id
                            if sender_chat_id == target_chat_id and text:
                                self._handle_remote_user_message(text)
            except Exception as exc:
                # Network hiccups or timeouts are normal during long-polling
                time.sleep(2.0)

    def _handle_remote_user_message(self, user_text: str) -> None:
        """Dispatches incoming mobile message into the Studio orchestrator and replies on phone."""
        logger.info("Received mobile Telegram instruction: %s", user_text)
        if not self._orchestrator:
            self.send_message("⚠️ Studio orchestrator is not yet attached.")
            return

        # Immediate receipt ack on phone
        self.send_message(f"⚡ <i>Received instruction from phone:</i> \"{user_text}\"\n<i>Processing in Studio...</i>")

        try:
            # Dispatch to orchestrator
            conv_id = getattr(self._orchestrator, "active_conversation_id", "conv-main")
            turn = self._orchestrator.handle_chat_turn(
                conversation_id=conv_id,
                user_message=user_text,
                provider_id="codex_cli",
            )
            assistant_content = (turn.get("assistant_message") or {}).get("content") or "Command executed."
            # Truncate if Telegram max message limit (4096 chars)
            if len(assistant_content) > 3800:
                assistant_content = assistant_content[:3800] + "\n\n<i>[Truncated: see Studio browser dashboard for full log]</i>"

            self.send_message(
                f"🤖 <b>Mar & Kuku Studio:</b>\n\n{assistant_content}",
                parse_mode="Markdown" if ("**" in assistant_content or "`" in assistant_content) else "HTML",
            )
        except Exception as exc:
            self.send_message(f"❌ <b>Error executing command:</b> {exc}")

