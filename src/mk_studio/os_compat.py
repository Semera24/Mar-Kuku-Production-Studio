"""Cross-Platform OS Compatibility Layer for macOS and Bare Debian 13 Linux.

Ensures Mar & Kuku Studio runs seamlessly on macOS Darwin and Linux (Debian 13, Ubuntu, headless VPS).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def get_platform_name() -> str:
    if is_macos():
        return "macOS"
    if is_linux():
        return "Linux (Debian/Ubuntu)"
    return sys.platform


def open_file_or_folder(target_path: Path | str, reveal: bool = False) -> subprocess.CompletedProcess[str]:
    """Open a file or folder in the system file explorer (Finder on macOS, Nautilus/PCManFM on Linux)."""
    p = Path(target_path).resolve()
    if is_macos():
        cmd = ["/usr/bin/open", "-R", str(p)] if reveal else ["/usr/bin/open", str(p)]
    elif is_linux():
        folder = str(p.parent if (reveal and p.is_file()) else p)
        xdg_open = shutil.which("xdg-open") or "xdg-open"
        cmd = [xdg_open, folder]
    else:
        cmd = ["explorer.exe", str(p)]

    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def focus_application(app_name: str = "Google Chrome") -> bool:
    """Brings an application window to the foreground."""
    if is_macos():
        script = f'tell application "{app_name}" to activate'
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        return res.returncode == 0
    elif is_linux():
        wmctrl = shutil.which("wmctrl")
        if wmctrl and os.environ.get("DISPLAY"):
            res = subprocess.run([wmctrl, "-a", app_name], capture_output=True, text=True, timeout=5)
            return res.returncode == 0
        return True  # Headless or Wayland/X11 without wmctrl
    return True


def capture_screen(out_path: Path) -> bool:
    """Takes a system desktop screenshot."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if is_macos():
        res = subprocess.run(["/usr/sbin/screencapture", "-x", str(out_path)], check=False)
        return res.returncode == 0 and out_path.is_file()
    elif is_linux():
        scrot = shutil.which("scrot")
        if scrot and os.environ.get("DISPLAY"):
            res = subprocess.run([scrot, str(out_path)], check=False)
            return res.returncode == 0 and out_path.is_file()
    return False

