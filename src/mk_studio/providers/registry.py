"""Multi-provider AI adapters (Codex CLI, Gemini CLI, Browser Web AI, Keychain API) with budget caps."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mk_studio.browser.controller import BrowserController
from mk_studio.config import RootPolicy
from mk_studio.runner import run_cancellable_subprocess
from mk_studio.state import StateStore

SECURITY_BIN = "/usr/bin/security"


class ProviderError(RuntimeError):
    """Raised when an AI provider fails or is unavailable."""


class ProviderBudgetExceededError(ProviderError):
    """Raised when a provider call would exceed its configured USD budget limit."""


def _find_cli_binary(name: str, fallback_paths: list[str]) -> str | None:
    found = shutil.which(name)
    if found and Path(found).is_file():
        return found
    for p in fallback_paths:
        if Path(p).is_file():
            return p
    # Also search fnm multishells if present for node-installed CLIs like codex
    fnm_dir = Path.home() / ".local" / "state" / "fnm_multishells"
    if fnm_dir.is_dir():
        for cand in sorted(fnm_dir.glob(f"*/bin/{name}"), reverse=True):
            if cand.is_file():
                return str(cand)
    return None


class KeychainSecretManager:
    """Stores and retrieves provider secrets using macOS Keychain (/usr/bin/security) without plaintext storage."""

    def __init__(self, account_name: str = "mk-studio") -> None:
        self.account_name = account_name

    def set_secret(self, service_name: str, secret_value: str) -> bool:
        if not Path(SECURITY_BIN).is_file():
            return False
        res = subprocess.run(
            [
                SECURITY_BIN,
                "add-generic-password",
                "-U",
                "-a",
                self.account_name,
                "-s",
                service_name,
                "-w",
                secret_value,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0

    def get_secret(self, service_name: str) -> str | None:
        if not Path(SECURITY_BIN).is_file():
            return None
        res = subprocess.run(
            [
                SECURITY_BIN,
                "find-generic-password",
                "-a",
                self.account_name,
                "-s",
                service_name,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return None
        val = (res.stdout or "").strip()
        return val or None

    def has_secret(self, service_name: str) -> bool:
        return self.get_secret(service_name) is not None


class ProviderRegistry:
    """Manages multiple AI provider connections, health checks, Keychain references, and budget limits."""

    def __init__(
        self,
        policy: RootPolicy,
        store: StateStore,
        browser: BrowserController | None = None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.policy = policy
        self.store = store
        self.browser = browser
        self.cancel_event = cancel_event or (browser.cancel_event if browser else threading.Event())
        self.keychain = KeychainSecretManager()
        self._codex_bin = _find_cli_binary(
            "codex",
            ["/opt/homebrew/bin/codex", "/usr/local/bin/codex"],
        )
        self._gemini_bin = _find_cli_binary(
            "gemini",
            ["/opt/homebrew/bin/gemini", "/usr/local/bin/gemini"],
        )
        self._ensure_default_providers()

    def set_provider_secret(self, provider_id: str, secret_value: str) -> dict[str, Any]:
        """Store a provider API key in macOS Keychain (/usr/bin/security) and refresh provider health."""
        configs = {p["provider_id"]: p for p in self.store.list_provider_configs()}
        service = (
            configs[provider_id]["keychain_service"]
            if provider_id in configs
            else f"mk-studio:{provider_id}"
        )
        ok = self.keychain.set_secret(service, secret_value)
        if not ok:
            raise ProviderError(f"Failed storing secret in macOS Keychain for service '{service}'")
        self.check_providers_health()
        return {"provider_id": provider_id, "keychain_service": service, "stored": True}

    def _ensure_default_providers(self) -> None:
        existing = {p["provider_id"]: p for p in self.store.list_provider_configs()}
        defaults = [
            {
                "provider_id": "chatgpt_web",
                "display_name": "ChatGPT Web Session (https://chatgpt.com/)",
                "provider_kind": "browser_session",
                "keychain_service": "mk-studio:chatgpt_web",
                "budget_limit_usd": 0.0,
                "status": "ready",
                "metadata": {"site_url": "https://chatgpt.com/", "role": "master"},
            },
            {
                "provider_id": "gemini_web",
                "display_name": "Google Gemini Web Session (https://gemini.google.com/app)",
                "provider_kind": "browser_session",
                "keychain_service": "mk-studio:gemini_web",
                "budget_limit_usd": 0.0,
                "status": "ready",
                "metadata": {"site_url": "https://gemini.google.com/app", "role": "reviewer"},
            },
            {
                "provider_id": "manus_web",
                "display_name": "Manus Web Session (https://manus.im/app)",
                "provider_kind": "browser_session",
                "keychain_service": "mk-studio:manus_web",
                "budget_limit_usd": 0.0,
                "status": "ready",
                "metadata": {"site_url": "https://manus.im/app", "role": "specialist"},
            },
            {
                "provider_id": "codex_cli",
                "display_name": "OpenAI Codex CLI (Local Authenticated)",
                "provider_kind": "cli_assistant",
                "keychain_service": "mk-studio:codex_cli",
                "budget_limit_usd": 5.0,
                "status": "ready" if self._codex_bin else "unavailable",
                "metadata": {"binary_path": self._codex_bin},
            },
            {
                "provider_id": "gemini_cli",
                "display_name": "Google Gemini CLI (Local Binary — Auth Blocked)",
                "provider_kind": "cli_assistant",
                "keychain_service": "mk-studio:gemini_cli",
                "budget_limit_usd": 5.0,
                "status": "blocked_auth_tier" if self._gemini_bin else "unavailable",
                "metadata": {"binary_path": self._gemini_bin},
            },
            {
                "provider_id": "browser_web_ai",
                "display_name": "Local Browser Sandbox Fixture (/browser-sandbox)",
                "provider_kind": "local_fixture",
                "keychain_service": "mk-studio:browser_web_ai",
                "budget_limit_usd": 0.0,
                "status": "ready",
                "metadata": {"cdp_port": 9222, "fixture": True},
            },
            {
                "provider_id": "ollama_local",
                "display_name": "Ollama Local (Qwen 2.5 / Llama 3 - Free)",
                "provider_kind": "local_llm",
                "keychain_service": "mk-studio:ollama_local",
                "budget_limit_usd": 0.0,
                "status": "ready",
                "metadata": {"endpoint": "http://127.0.0.1:11434", "model": "qwen2.5:latest"},
            },
            {
                "provider_id": "openai_api",
                "display_name": "OpenAI Direct REST API (Keychain Secret)",
                "provider_kind": "rest_api",
                "keychain_service": "mk-studio:openai_api",
                "budget_limit_usd": 5.0,
                "status": "ready"
                if (self.keychain.has_secret("mk-studio:openai_api") or os.environ.get("OPENAI_API_KEY"))
                else "blocked_missing_keychain_secret",
                "metadata": {"model": "gpt-4.1-mini"},
            },
        ]
        for d in defaults:
            if d["provider_id"] not in existing:
                self.store.upsert_provider_config(**d)

    def check_providers_health(self) -> list[dict[str, Any]]:
        """Inspect and update live availability of all registered providers."""
        self._ensure_default_providers()
        configs = self.store.list_provider_configs()
        updated: list[dict[str, Any]] = []
        chrome_up = bool(self.browser and self.browser.is_reachable())
        for cfg in configs:
            pid = cfg["provider_id"]
            status = cfg["status"]
            meta = dict(cfg.get("metadata") or {})
            if pid in ("chatgpt_web", "gemini_web"):
                status = "ready"
                meta["chrome_reachable"] = chrome_up
            elif pid == "manus_web":
                meta["chrome_reachable"] = chrome_up
                if status not in ("ready", "awaiting_owner_login"):
                    status = "ready"
            elif pid == "codex_cli":
                status = "ready" if self._codex_bin else "unavailable"
                meta["binary_path"] = self._codex_bin
            elif pid == "gemini_cli":
                if not self._gemini_bin:
                    status = "unavailable"
                elif status == "ready":
                    status = "blocked_auth_tier"
                meta["binary_path"] = self._gemini_bin
            elif pid == "browser_web_ai":
                status = "ready"
                meta["chrome_reachable"] = chrome_up
                meta["fixture"] = True
            elif pid == "ollama_local":
                ollama_up = False
                try:
                    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1.0) as r:
                        ollama_up = (r.status == 200)
                except Exception:
                    ollama_up = False
                status = "ready" if ollama_up else "offline"
                meta["ollama_reachable"] = ollama_up
            elif pid == "openai_api":
                has_key = self.keychain.has_secret(cfg["keychain_service"]) or bool(
                    os.environ.get("OPENAI_API_KEY")
                )
                status = "ready" if has_key else "blocked_missing_keychain_secret"
                meta["keychain_secret_present"] = has_key

            row = self.store.upsert_provider_config(
                provider_id=pid,
                display_name=cfg["display_name"],
                provider_kind=cfg["provider_kind"],
                enabled=bool(cfg["enabled"]),
                keychain_service=cfg["keychain_service"],
                budget_limit_usd=float(cfg["budget_limit_usd"]),
                status=status,
                metadata=meta,
            )
            row["metadata"] = meta
            updated.append(row)
        return updated

    def _enforce_budget(self, provider_id: str, estimated_cost_usd: float) -> dict[str, Any]:
        configs = {p["provider_id"]: p for p in self.store.list_provider_configs()}
        if provider_id not in configs:
            raise ProviderError(f"Unknown provider_id: {provider_id}")
        cfg = configs[provider_id]
        if not cfg["enabled"]:
            raise ProviderError(f"Provider '{provider_id}' is currently disabled")
        spent = float(cfg["spent_usd"])
        limit = float(cfg["budget_limit_usd"])
        if estimated_cost_usd > 0 and (spent + estimated_cost_usd) > limit:
            raise ProviderBudgetExceededError(
                f"Provider '{provider_id}' budget exceeded: spent ${spent:.4f} + ${estimated_cost_usd:.4f} > limit ${limit:.4f}"
            )
        return cfg

    def generate(
        self,
        provider_id: str,
        prompt: str,
        *,
        conversation_id: str | None = None,
        context_summary: str = "",
        browser_target_url: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """Send a prompt (with optional conversation context) to the selected AI provider and return structured output."""
        cfg = self._enforce_budget(provider_id, estimated_cost_usd=0.0)
        full_prompt = (
            f"Studio Context:\n{context_summary}\n\nUser Request:\n{prompt}"
            if context_summary
            else prompt
        )

        if provider_id == "chatgpt_web":
            if self.browser is None:
                raise ProviderError("BrowserController is not attached to ProviderRegistry")
            res = self.browser.consult_chatgpt_web(
                full_prompt,
                conversation_url=browser_target_url,
                conversation_id=conversation_id,
                timeout_seconds=timeout_seconds,
            )
            self.store.record_provider_usage(provider_id, estimated_cost_usd=0.0)
            return {
                "provider_id": provider_id,
                "provider_kind": "browser_session",
                "status": "passed" if res.get("status") == "succeeded" else res.get("status"),
                "response": res.get("response", ""),
                "screenshot": res.get("screenshot"),
                "url": res.get("url"),
            }

        if provider_id == "gemini_web":
            if self.browser is None:
                raise ProviderError("BrowserController is not attached to ProviderRegistry")
            res = self.browser.consult_gemini_web(
                full_prompt,
                conversation_url=browser_target_url,
                conversation_id=conversation_id,
                timeout_seconds=timeout_seconds,
            )
            self.store.record_provider_usage(provider_id, estimated_cost_usd=0.0)
            return {
                "provider_id": provider_id,
                "provider_kind": "browser_session",
                "status": "passed" if res.get("status") == "succeeded" else res.get("status"),
                "response": res.get("response", ""),
                "screenshot": res.get("screenshot"),
                "url": res.get("url"),
            }

        if provider_id == "manus_web":
            if self.browser is None:
                raise ProviderError("BrowserController is not attached to ProviderRegistry")
            res = self.browser.consult_manus_web(
                full_prompt,
                conversation_url=browser_target_url,
                conversation_id=conversation_id,
                timeout_seconds=timeout_seconds,
            )
            self.store.record_provider_usage(provider_id, estimated_cost_usd=0.0)
            return {
                "provider_id": provider_id,
                "provider_kind": "browser_session",
                "status": "passed" if res.get("status") == "succeeded" else res.get("status"),
                "response": res.get("response") or res.get("login_gate_message", ""),
                "screenshot": res.get("screenshot"),
                "url": res.get("url"),
                "authenticated": res.get("authenticated", False),
            }

        if provider_id == "codex_cli":
            if not self._codex_bin:
                raise ProviderError("Codex CLI binary is not installed on this machine")
            out_file = self.policy.output_root_resolved / "codex_last_message.txt"
            cmd = [
                self._codex_bin,
                "exec",
                "--skip-git-repo-check",
                "-C",
                str(self.policy.output_root_resolved),
                "-o",
                str(out_file),
                full_prompt,
            ]
            res = run_cancellable_subprocess(
                cmd,
                timeout_seconds=timeout_seconds,
                cancel_event=self.cancel_event,
                check=False,
            )
            reply_text = ""
            if out_file.is_file():
                reply_text = out_file.read_text(encoding="utf-8", errors="replace").strip()
            if not reply_text:
                reply_text = (res.stdout or "").strip()
            if res.returncode != 0 and not reply_text:
                raise ProviderError(
                    f"Codex CLI exited {res.returncode}: {(res.stderr or '').strip()[:400]}"
                )
            self.store.record_provider_usage(provider_id, estimated_cost_usd=0.0)
            return {
                "provider_id": provider_id,
                "provider_kind": "cli_assistant",
                "status": "passed",
                "response": reply_text,
                "binary": self._codex_bin,
                "returncode": res.returncode,
            }

        if provider_id == "gemini_cli":
            if not self._gemini_bin:
                raise ProviderError("Gemini CLI binary is not installed on this machine")
            cmd = [
                self._gemini_bin,
                "-p",
                full_prompt,
            ]
            res = run_cancellable_subprocess(
                cmd,
                timeout_seconds=timeout_seconds,
                cancel_event=self.cancel_event,
                check=False,
            )
            reply_text = (res.stdout or "").strip()
            if res.returncode != 0 and not reply_text:
                raise ProviderError(
                    f"Gemini CLI exited {res.returncode}: {(res.stderr or '').strip()[:400]}"
                )
            self.store.record_provider_usage(provider_id, estimated_cost_usd=0.0)
            return {
                "provider_id": provider_id,
                "provider_kind": "cli_assistant",
                "status": "passed",
                "response": reply_text,
                "binary": self._gemini_bin,
                "returncode": res.returncode,
            }

        if provider_id == "browser_web_ai":
            if self.browser is None:
                raise ProviderError("BrowserController is not attached to ProviderRegistry")
            cycle = self.browser.run_interactive_prompt_cycle(
                full_prompt,
                target_url=browser_target_url,
                conversation_id=conversation_id,
            )
            turns = cycle.get("turns") or []
            reply_text = turns[-1]["response"] if turns else ""
            self.store.record_provider_usage(provider_id, estimated_cost_usd=0.0)
            return {
                "provider_id": provider_id,
                "provider_kind": "local_fixture",
                "status": "passed",
                "response": reply_text,
                "screenshot": cycle.get("screenshot"),
                "url": (cycle.get("dom") or {}).get("url"),
            }

        if provider_id == "ollama_local":
            endpoint = "http://127.0.0.1:11434/api/generate"
            meta = dict(cfg.get("metadata") or {})
            model = str(meta.get("model") or "qwen2.5:latest")
            prompt_payload = json.dumps({
                "model": model,
                "prompt": (f"{context_summary}\n\nUser: {prompt}\n\nAssistant:" if context_summary else prompt),
                "stream": False,
            }).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=prompt_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    reply_text = str(resp_data.get("response") or "").strip()
            except Exception as exc:
                reply_text = (
                    f"Ollama local runner not detected on {endpoint} ({exc}). "
                    "To run free offline AI like in the Hermes tutorial, install Ollama: "
                    "`brew install ollama && ollama run qwen2.5` or switch to ChatGPT Web."
                )
            self.store.record_provider_usage(provider_id, estimated_cost_usd=0.0)
            return {
                "provider_id": provider_id,
                "provider_kind": "local_llm",
                "status": "passed",
                "response": reply_text,
                "model": model,
            }

        if provider_id == "openai_api":
            secret = self.keychain.get_secret(cfg["keychain_service"]) or os.environ.get(
                "OPENAI_API_KEY"
            )
            if not secret:
                raise ProviderError(
                    f"Provider '{provider_id}' blocked: no API key found in macOS Keychain service '{cfg['keychain_service']}' or OPENAI_API_KEY."
                )
            meta = dict(cfg.get("metadata") or {})
            model = str(meta.get("model") or "gpt-4.1-mini")
            req_body = json.dumps(
                {
                    "model": model,
                    "messages": [{"role": "user", "content": full_prompt}],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=req_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {secret}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")[:300]
                raise ProviderError(f"OpenAI REST API HTTP {exc.code}: {err_body}") from exc
            except Exception as exc:
                raise ProviderError(f"OpenAI REST API error: {exc}") from exc

            choices = payload.get("choices") or []
            reply_text = (
                ((choices[0].get("message") or {}).get("content") or "").strip()
                if choices
                else ""
            )
            self.store.record_provider_usage(provider_id, estimated_cost_usd=0.002)
            return {
                "provider_id": provider_id,
                "provider_kind": "rest_api",
                "status": "passed",
                "response": reply_text,
                "model": model,
            }

        raise ProviderError(f"Unsupported provider_id: {provider_id}")

