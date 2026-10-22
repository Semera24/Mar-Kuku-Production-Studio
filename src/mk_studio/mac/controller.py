"""Governed, auditable, stoppable macOS desktop & application automation service."""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from mk_studio.config import PathPolicyError, RootPolicy, compute_sha256
from mk_studio.runner import run_cancellable_subprocess
from mk_studio.state import StateStore

OSASCRIPT_BIN = "/usr/bin/osascript"
SCREENCAPTURE_BIN = "/usr/sbin/screencapture"
OPEN_BIN = "/usr/bin/open"


class MacControlError(RuntimeError):
    """Base error for Mac automation operations."""


class MacProfileViolationError(MacControlError):
    """Raised when an action is not permitted by the active Mac capability profile."""


class MacApprovalRequiredError(MacControlError):
    """Raised when a high-risk Mac action is attempted without explicit owner approval."""


MAC_CAPABILITY_PROFILES: dict[str, dict[str, Any]] = {
    "read_only": {
        "description": "Inspect running apps, windows, permissions, and screen captures without modifying state.",
        "allowed_actions": {
            "inspect_permissions",
            "list_running_apps",
            "list_windows",
            "capture_screen",
        },
        "allowed_apps": {"*"},
    },
    "editor_automation": {
        "description": "Control creative editors (Palmier Pro), inspect windows/screens, and manage scratch files.",
        "allowed_actions": {
            "inspect_permissions",
            "list_running_apps",
            "list_windows",
            "capture_screen",
            "launch_or_focus_app",
            "run_applescript",
            "reveal_in_finder",
        },
        "allowed_apps": {
            "PalmierPro",
            "Palmier Pro",
            "Google Chrome",
            "Finder",
            "Preview",
            "QuickTime Player",
        },
    },
    "browser_automation": {
        "description": "Launch, focus, and inspect desktop web browsers and scratch download folders.",
        "allowed_actions": {
            "inspect_permissions",
            "list_running_apps",
            "list_windows",
            "capture_screen",
            "launch_or_focus_app",
            "reveal_in_finder",
        },
        "allowed_apps": {"Google Chrome", "Safari", "Finder"},
    },
    "general_mac_control": {
        "description": "Broad governed Mac automation across apps, AppleScript/JXA, scratch workflows, and UI fallback.",
        "allowed_actions": {
            "inspect_permissions",
            "list_running_apps",
            "list_windows",
            "capture_screen",
            "launch_or_focus_app",
            "run_applescript",
            "reveal_in_finder",
            "ui_keystroke_or_click",
            "quit_app",
        },
        "allowed_apps": {"*"},
    },
}

HIGH_RISK_ACTIONS = {"quit_app", "delete_file", "system_setting"}


def check_ax_is_process_trusted() -> bool:
    """Query macOS ApplicationServices AXIsProcessTrusted() via ctypes."""
    try:
        lib_path = (
            ctypes.util.find_library("ApplicationServices")
            or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        app_services = ctypes.cdll.LoadLibrary(lib_path)
        app_services.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(app_services.AXIsProcessTrusted())
    except Exception:
        return False


class MacController:
    """Executes profile-governed macOS actions with pre/postcondition verification and SQLite audit logging."""

    def __init__(
        self,
        policy: RootPolicy,
        store: StateStore,
        *,
        default_profile: str = "editor_automation",
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.policy = policy
        self.store = store
        self.default_profile = default_profile
        self.captures_dir = self.policy.output_root_resolved / "mac-captures"
        self.cancel_event = cancel_event or threading.Event()

    @property
    def stop_requested(self) -> bool:
        return self.cancel_event.is_set()

    def request_stop(self) -> None:
        """Signal immediate cancellation for any running Mac automation subprocess."""
        self.cancel_event.set()

    def clear_stop(self) -> None:
        self.cancel_event.clear()

    def reset_stop(self) -> None:
        self.cancel_event.clear()

    def _check_profile_and_approval(
        self,
        profile_name: str,
        action_kind: str,
        target_app: str = "",
        approved_by: str | None = None,
    ) -> None:
        if self.cancel_event.is_set():
            raise MacControlError("Mac automation is currently stopped; call clear_stop() to resume.")
        if profile_name not in MAC_CAPABILITY_PROFILES:
            raise MacProfileViolationError(f"Unknown Mac capability profile: {profile_name}")
        prof = MAC_CAPABILITY_PROFILES[profile_name]
        if action_kind not in prof["allowed_actions"]:
            raise MacProfileViolationError(
                f"Action '{action_kind}' is not permitted under profile '{profile_name}'"
            )
        allowed_apps = prof["allowed_apps"]
        if target_app and "*" not in allowed_apps and target_app not in allowed_apps:
            raise MacProfileViolationError(
                f"Target application '{target_app}' is not in allowlist for profile '{profile_name}'"
            )
        if action_kind in HIGH_RISK_ACTIONS and not approved_by:
            raise MacApprovalRequiredError(
                f"High-risk Mac action '{action_kind}' on '{target_app}' requires explicit owner approval."
            )

    def inspect_permissions(
        self,
        *,
        profile_name: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Inspect live macOS Accessibility, System Events Automation, and Screen Capture readiness."""
        prof = profile_name or self.default_profile
        ax_trusted = check_ax_is_process_trusted()

        sys_events_ok = False
        frontmost_app = ""
        try:
            res = run_cancellable_subprocess(
                [
                    OSASCRIPT_BIN,
                    "-e",
                    'tell application "System Events" to get name of first process whose frontmost is true',
                ],
                timeout_seconds=5.0,
                cancel_event=self.cancel_event,
                check=False,
            )
            if res.returncode == 0:
                sys_events_ok = True
                frontmost_app = (res.stdout or "").strip()
        except Exception:
            sys_events_ok = False

        probe_img = self.policy.output_root_resolved / "mac-captures" / "_perm_probe.png"
        probe_img.parent.mkdir(parents=True, exist_ok=True)
        screen_recording_ok = False
        try:
            sc_res = run_cancellable_subprocess(
                [SCREENCAPTURE_BIN, "-x", str(probe_img)],
                timeout_seconds=5.0,
                cancel_event=self.cancel_event,
                check=False,
            )
            screen_recording_ok = sc_res.returncode == 0 and probe_img.is_file() and probe_img.stat().st_size > 0
        finally:
            if probe_img.is_file():
                probe_img.unlink()

        summary = {
            "accessibility_trusted": ax_trusted,
            "system_events_automation_ready": sys_events_ok,
            "screen_capture_ready": screen_recording_ok,
            "frontmost_app": frontmost_app,
            "ui_click_keystroke_fallback_available": ax_trusted,
            "active_profile": prof,
            "profiles": {
                k: {
                    "description": v["description"],
                    "allowed_actions": sorted(v["allowed_actions"]),
                    "allowed_apps": sorted(v["allowed_apps"]),
                }
                for k, v in MAC_CAPABILITY_PROFILES.items()
            },
        }
        self.store.record_mac_action(
            conversation_id=conversation_id,
            profile_name=prof,
            action_kind="inspect_permissions",
            target_app="System Events",
            command_or_script="AXIsProcessTrusted + System Events frontmost query + screencapture probe",
            expected_postcondition="Permission readiness determined",
            observed_postcondition=json.dumps(
                {
                    "ax": ax_trusted,
                    "system_events": sys_events_ok,
                    "screen_capture": screen_recording_ok,
                    "frontmost": frontmost_app,
                }
            ),
            status="succeeded",
        )
        return summary

    def list_running_apps(
        self,
        *,
        profile_name: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """List visible background/foreground applications via System Events."""
        prof = profile_name or self.default_profile
        self._check_profile_and_approval(prof, "list_running_apps")

        script = (
            'tell application "System Events"\n'
            '  set appNames to name of every process whose background only is false\n'
            '  set frontName to name of first process whose frontmost is true\n'
            '  return {frontName, appNames}\n'
            'end tell'
        )
        res = run_cancellable_subprocess(
            [OSASCRIPT_BIN, "-e", script],
            timeout_seconds=8.0,
            cancel_event=self.cancel_event,
            check=False,
        )
        raw = (res.stdout or "").strip()
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        frontmost = parts[0] if parts else ""
        apps = parts[1:] if len(parts) > 1 else parts

        rec = self.store.record_mac_action(
            conversation_id=conversation_id,
            profile_name=prof,
            action_kind="list_running_apps",
            target_app="System Events",
            command_or_script="System Events non-background process list",
            expected_postcondition="Non-empty running app list",
            observed_postcondition=f"frontmost={frontmost}, count={len(apps)}",
            status="succeeded" if res.returncode == 0 else "failed",
        )
        return {
            "status": "succeeded" if res.returncode == 0 else "failed",
            "frontmost_app": frontmost,
            "running_apps": apps,
            "action_record": rec,
        }

    def list_windows(
        self,
        *,
        owner_filter: str | None = None,
        profile_name: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """List on-screen macOS windows with owner name, window title, and bounds via System Events / JXA."""
        prof = profile_name or self.default_profile
        self._check_profile_and_approval(prof, "list_windows", target_app=owner_filter or "")

        jxa = """
        ObjC.import('CoreGraphics');
        const opts = $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements;
        const winList = ObjC.castRefToObject($.CGWindowListCopyWindowInfo(opts, $.kCGNullWindowID));
        const out = [];
        const count = winList.count;
        for (let i = 0; i < count; i++) {
            const w = ObjC.deepUnwrap(winList.objectAtIndex(i));
            if (w && w.kCGWindowLayer === 0) {
                out.push({
                    owner: w.kCGWindowOwnerName || '',
                    name: w.kCGWindowName || '',
                    pid: w.kCGWindowOwnerPID || 0,
                    windowNumber: w.kCGWindowNumber || 0,
                    bounds: w.kCGWindowBounds || {}
                });
            }
        }
        JSON.stringify(out);
        """
        res = run_cancellable_subprocess(
            [OSASCRIPT_BIN, "-l", "JavaScript", "-e", jxa],
            timeout_seconds=8.0,
            cancel_event=self.cancel_event,
            check=False,
        )
        windows: list[dict[str, Any]] = []
        if res.returncode == 0 and (res.stdout or "").strip():
            try:
                windows = json.loads(res.stdout.strip())
            except json.JSONDecodeError:
                windows = []

        if owner_filter:
            filt_norm = owner_filter.lower().replace(" ", "")
            windows = [
                w
                for w in windows
                if filt_norm in str(w.get("owner", "")).lower().replace(" ", "")
            ]

        rec = self.store.record_mac_action(
            conversation_id=conversation_id,
            profile_name=prof,
            action_kind="list_windows",
            target_app=owner_filter or "*",
            command_or_script="CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly)",
            expected_postcondition="On-screen window metadata list",
            observed_postcondition=f"found {len(windows)} windows",
            status="succeeded" if res.returncode == 0 else "failed",
        )
        return {
            "status": "succeeded" if res.returncode == 0 else "failed",
            "window_count": len(windows),
            "windows": windows,
            "action_record": rec,
        }

    def capture_screen(
        self,
        filename: str = "mac_desktop_capture.png",
        *,
        window_id: int | None = None,
        profile_name: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Capture the full desktop or a specific macOS window ID into scratch/ch01-proof/mac-captures/."""
        prof = profile_name or self.default_profile
        self._check_profile_and_approval(prof, "capture_screen")

        out_path = self.policy.resolve_output(
            self.captures_dir / filename,
            overwrite=True,
            create_parents=True,
        )
        cmd = [SCREENCAPTURE_BIN, "-x"]
        if window_id is not None:
            cmd.extend(["-l", str(window_id)])
        cmd.append(str(out_path))

        res = run_cancellable_subprocess(
            cmd,
            timeout_seconds=10.0,
            cancel_event=self.cancel_event,
            check=False,
        )
        ok = res.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 0
        sha = compute_sha256(out_path) if ok else ""
        size = out_path.stat().st_size if ok else 0

        if ok:
            self.store.record_artifact(
                episode_id="EPISODE_002",
                chapter_id="CH01",
                role="mac:screen_capture",
                path=str(out_path),
                sha256=sha,
                bytes_size=size,
                metadata={"window_id": window_id, "profile": prof},
            )

        rec = self.store.record_mac_action(
            conversation_id=conversation_id,
            profile_name=prof,
            action_kind="capture_screen",
            target_app=f"window:{window_id}" if window_id else "desktop",
            command_or_script=" ".join(cmd),
            expected_postcondition=f"Non-empty PNG at {out_path}",
            observed_postcondition=f"exists={ok}, bytes={size}, sha256={sha[:16]}",
            status="succeeded" if ok else "failed",
        )
        return {
            "status": "succeeded" if ok else "failed",
            "path": str(out_path),
            "bytes": size,
            "sha256": sha,
            "action_record": rec,
        }

    def launch_or_focus_app(
        self,
        app_name: str,
        *,
        profile_name: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Launch or bring an application to the foreground and verify its running/window state."""
        prof = profile_name or self.default_profile
        self._check_profile_and_approval(prof, "launch_or_focus_app", target_app=app_name)

        res = run_cancellable_subprocess(
            [OPEN_BIN, "-a", app_name],
            timeout_seconds=10.0,
            cancel_event=self.cancel_event,
            check=False,
        )
        win_info = self.list_windows(
            owner_filter=app_name.replace(" ", ""),
            profile_name=prof,
            conversation_id=conversation_id,
        )
        if not win_info["windows"] and " " in app_name:
            win_info = self.list_windows(
                owner_filter=app_name,
                profile_name=prof,
                conversation_id=conversation_id,
            )

        ok = res.returncode == 0
        observed = f"open_rc={res.returncode}, visible_windows={win_info['window_count']}"
        rec = self.store.record_mac_action(
            conversation_id=conversation_id,
            profile_name=prof,
            action_kind="launch_or_focus_app",
            target_app=app_name,
            command_or_script=f"/usr/bin/open -a {app_name}",
            expected_postcondition=f"{app_name} running with accessible window",
            observed_postcondition=observed,
            status="succeeded" if ok else "failed",
        )
        return {
            "status": "succeeded" if ok else "failed",
            "app_name": app_name,
            "windows": win_info["windows"],
            "observed_postcondition": observed,
            "action_record": rec,
        }

    def reveal_in_finder(
        self,
        target_path: str | Path,
        *,
        profile_name: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Verify a scratch artifact exists on disk and inspect/reveal its metadata."""
        prof = profile_name or self.default_profile
        self._check_profile_and_approval(prof, "reveal_in_finder", target_app="Finder")

        resolved = Path(target_path).expanduser().resolve(strict=False)
        if not resolved.is_relative_to(self.policy.output_root_resolved):
            raise PathPolicyError(f"Refusing Finder action outside scratch output root: {resolved}")
        exists = resolved.exists()
        observed = (
            f"exists={exists}, is_dir={resolved.is_dir()}, bytes={resolved.stat().st_size if resolved.is_file() else 0}"
            if exists
            else "exists=False"
        )
        rec = self.store.record_mac_action(
            conversation_id=conversation_id,
            profile_name=prof,
            action_kind="reveal_in_finder",
            target_app="Finder",
            command_or_script=f"stat {resolved}",
            expected_postcondition=f"Path {resolved} exists inside scratch",
            observed_postcondition=observed,
            status="succeeded" if exists else "failed",
        )
        return {
            "status": "succeeded" if exists else "failed",
            "path": str(resolved),
            "observed_postcondition": observed,
            "action_record": rec,
        }

    def quit_app(
        self,
        app_name: str,
        *,
        profile_name: str = "general_mac_control",
        approved_by: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """High-risk action: quit an application only when explicit owner approval is supplied."""
        self._check_profile_and_approval(
            profile_name,
            "quit_app",
            target_app=app_name,
            approved_by=approved_by,
        )
        res = run_cancellable_subprocess(
            [OSASCRIPT_BIN, "-e", f'tell application "{app_name}" to quit'],
            timeout_seconds=8.0,
            cancel_event=self.cancel_event,
            check=False,
        )
        rec = self.store.record_mac_action(
            conversation_id=conversation_id,
            profile_name=profile_name,
            action_kind="quit_app",
            target_app=app_name,
            command_or_script=f'tell application "{app_name}" to quit',
            expected_postcondition=f"{app_name} terminated",
            observed_postcondition=f"rc={res.returncode}",
            status="succeeded" if res.returncode == 0 else "failed",
            requires_approval=True,
            approved_by=approved_by,
        )
        return {"status": "succeeded" if res.returncode == 0 else "failed", "action_record": rec}
