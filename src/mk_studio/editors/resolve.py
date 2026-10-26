"""DaVinci Resolve Video Editor Adapter & MCP Client for Mar & Kuku Studio.

Supports DaVinci Resolve on Linux, macOS, and Windows via local CursorBridge HTTP API (port 9876)
and Model Context Protocol (MCP) server.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from mk_studio.config import RootPolicy

logger = logging.getLogger("mk_studio.resolve")

DEFAULT_RESOLVE_BRIDGE_URL = "http://127.0.0.1:9876"


class ResolveAdapter:
    """Manages DaVinci Resolve timeline assembly, media intake, and project control."""

    def __init__(
        self,
        policy: RootPolicy | None = None,
        bridge_url: str = DEFAULT_RESOLVE_BRIDGE_URL,
    ) -> None:
        self.policy = policy or RootPolicy()
        self.bridge_url = bridge_url.rstrip("/")

    def is_installed(self) -> bool:
        """Returns True if DaVinci Resolve is installed on this machine."""
        candidates = [
            Path("/opt/resolve/bin/resolve"),
            Path("/Applications/DaVinci Resolve/DaVinci Resolve.app"),
            Path("C:/Program Files/Blackmagic Design/DaVinci Resolve/Resolve.exe"),
            Path.home() / ".local" / "share" / "DaVinciResolve",
        ]
        return any(c.exists() for c in candidates)

    def is_bridge_connected(self) -> bool:
        """Returns True if the internal CursorBridge HTTP listener (port 9876) is reachable."""
        try:
            req = urllib.request.Request(f"{self.bridge_url}/ping", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return resp.status == 200
        except Exception:
            # Fall back to root or /project
            try:
                req = urllib.request.Request(f"{self.bridge_url}/project", headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    return resp.status in (200, 404)
            except Exception:
                return False

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.bridge_url}{endpoint}"
        if params:
            qs = urllib.parse.urlencode(params)
            url = f"{url}?{qs}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def _post(self, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.bridge_url}{endpoint}"
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def get_status(self) -> dict[str, Any]:
        """Returns connection and project status from DaVinci Resolve."""
        installed = self.is_installed()
        connected = self.is_bridge_connected()
        info: dict[str, Any] = {
            "editor": "davinci_resolve",
            "installed": installed,
            "bridge_connected": connected,
            "bridge_url": self.bridge_url,
            "current_project": None,
            "active_timeline": None,
        }
        if connected:
            try:
                proj = self._get("/project")
                info["current_project"] = proj.get("name") or proj.get("project_name")
            except Exception:
                pass
            try:
                tl = self._get("/timeline")
                info["active_timeline"] = tl.get("name") or tl.get("timeline_name")
            except Exception:
                pass
        return info

    def import_media(self, file_paths: list[str | Path]) -> dict[str, Any]:
        """Imports audio, image, and video files into DaVinci Resolve Media Pool."""
        if not self.is_bridge_connected():
            raise RuntimeError("DaVinci Resolve bridge is not connected on port 9876. Launch Workspace > Scripts > CursorBridge in Resolve.")
        clean_paths = [str(Path(p).resolve()) for p in file_paths if Path(p).is_file()]
        return self._post("/mediapool/import", {"file_paths": clean_paths})

    def insert_timeline_clip(
        self,
        media_path: str | Path,
        *,
        track_index: int = 1,
        track_type: str = "video",
        start_frame: int = 0,
        end_frame: int | None = None,
    ) -> dict[str, Any]:
        """Inserts a media asset clip into the active timeline."""
        if not self.is_bridge_connected():
            raise RuntimeError("DaVinci Resolve bridge is not connected on port 9876.")
        return self._post(
            "/timeline/clip",
            {
                "file_path": str(Path(media_path).resolve()),
                "track_index": track_index,
                "track_type": track_type,
                "start_frame": start_frame,
                "end_frame": end_frame,
            },
        )

    def add_marker(
        self,
        frame_id: int,
        color: str = "Cyan",
        name: str = "Cue",
        note: str = "",
        duration: int = 1,
    ) -> dict[str, Any]:
        """Places a colored marker on the active timeline."""
        if not self.is_bridge_connected():
            raise RuntimeError("DaVinci Resolve bridge is not connected on port 9876.")
        return self._post(
            "/timeline/marker",
            {
                "frame_id": frame_id,
                "color": color,
                "name": name,
                "note": note,
                "duration": duration,
            },
        )

