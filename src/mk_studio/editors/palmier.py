"""Palmier Pro MCP adapter with schema-validated tool calls, semantic reconciliation, and scratch safety guards."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jsonschema

from mk_studio.config import (
    DEFAULT_FFMPEG_BIN,
    DEFAULT_PALMIER_MCP_URL,
    PathPolicyError,
    RootPolicy,
    compute_sha256,
)
from mk_studio.contracts import ClipPlan, KeyframeRow, TimelinePlan
from mk_studio.runner import PreSendValidationError, StepRunner
from mk_studio.state import StateStore

MCP_PROTOCOL_VERSION = "2025-11-25"
DEFAULT_PALMIER_SCHEMAS_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "implementation" / "palmier-tool-schemas.json"
)


class PalmierEditorError(RuntimeError):
    """Base error for Palmier MCP adapter operations."""


class PalmierSchemaValidationError(PreSendValidationError, PalmierEditorError):
    """Raised before sending an MCP tool call when arguments violate palmier-tool-schemas.json."""


class ScratchProjectCreationBlockedError(PalmierEditorError):
    """Raised when the native scratch .palmier project does not exist and bootstrap is disabled or unavailable."""


_CACHED_TOOL_SCHEMAS: dict[str, dict[str, Any]] | None = None


def load_palmier_tool_schemas(
    schema_path: str | Path = DEFAULT_PALMIER_SCHEMAS_PATH,
) -> dict[str, dict[str, Any]]:
    """Load and cache Palmier MCP tool inputSchemas from docs/implementation/palmier-tool-schemas.json."""
    global _CACHED_TOOL_SCHEMAS
    if _CACHED_TOOL_SCHEMAS is not None and Path(schema_path) == DEFAULT_PALMIER_SCHEMAS_PATH:
        return _CACHED_TOOL_SCHEMAS

    p = Path(schema_path)
    if not p.is_file():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    schemas = {
        str(t["name"]): dict(t.get("inputSchema") or {"type": "object"})
        for t in raw.get("tools", [])
        if isinstance(t, dict) and t.get("name")
    }
    if p == DEFAULT_PALMIER_SCHEMAS_PATH:
        _CACHED_TOOL_SCHEMAS = schemas
    return schemas


def validate_palmier_tool_call(
    name: str,
    arguments: dict[str, Any],
    schemas: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Validate tool call arguments against live Palmier MCP inputSchema before sending."""
    active_schemas = schemas if schemas is not None else load_palmier_tool_schemas()
    if not active_schemas:
        return
    if name not in active_schemas:
        raise PalmierSchemaValidationError(f"Unknown Palmier MCP tool '{name}'")
    schema = active_schemas[name]
    allowed_props = set(schema.get("properties", {}).keys())
    extra_keys = set(arguments.keys()) - allowed_props
    if extra_keys:
        raise PalmierSchemaValidationError(
            f"Palmier tool '{name}' rejected undocumented argument keys: {sorted(extra_keys)}"
        )
    try:
        jsonschema.validate(instance=arguments, schema=schema)
    except jsonschema.ValidationError as exc:
        raise PalmierSchemaValidationError(
            f"Palmier tool '{name}' schema validation failed: {exc.message}"
        ) from exc


@dataclass
class PalmierProjectState:
    """Snapshot of the bound Palmier project's media library and active timeline."""

    project_id: str | None
    project_name: str | None
    project_path: str | None
    timeline_id: str | None
    width: int
    height: int
    fps: int
    total_frames: int
    media_by_path: dict[str, str] = field(default_factory=dict)
    media_by_name: dict[str, str] = field(default_factory=dict)
    tracks: list[dict[str, Any]] = field(default_factory=list)
    markers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReconciliationDiff:
    """Computed difference between a TimelinePlan and live PalmierProjectState."""

    media_to_import: list[tuple[str, str, str]] = field(default_factory=list)
    settings_need_update: bool = False
    tracks_to_add: list[tuple[int, str, str]] = field(default_factory=list)
    tracks_to_rename: list[tuple[str, str]] = field(default_factory=list)
    tracks_need_reorder: bool = False
    clips_to_remove: list[str] = field(default_factory=list)
    clips_to_add: list[ClipPlan] = field(default_factory=list)
    clips_needing_keyframes: list[tuple[ClipPlan, str | None]] = field(default_factory=list)
    markers_to_create: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return (
            not self.media_to_import
            and not self.settings_need_update
            and not self.tracks_to_add
            and not self.tracks_to_rename
            and not self.tracks_need_reorder
            and not self.clips_to_remove
            and not self.clips_to_add
            and not self.clips_needing_keyframes
            and not self.markers_to_create
        )


class HttpMcpSession:
    """Streamable HTTP MCP client for Palmier Pro preserving Mcp-Session-Id across calls."""

    def __init__(self, endpoint_url: str = DEFAULT_PALMIER_MCP_URL, timeout_seconds: float = 30.0) -> None:
        self.endpoint_url = endpoint_url
        self.timeout_seconds = timeout_seconds
        self.session_id: str | None = None
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        is_notification: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not is_notification:
            payload["id"] = self._next_id()
        if params is not None:
            payload["params"] = params

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        req = urllib.request.Request(self.endpoint_url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                raw_text = resp.read().decode("utf-8", errors="replace").strip()
        except urllib.error.URLError as exc:
            raise PalmierEditorError(f"Failed to connect to Palmier MCP at {self.endpoint_url}: {exc}") from exc

        if is_notification or not raw_text:
            return {}

        if raw_text.startswith("event:") or "\ndata:" in raw_text or raw_text.startswith("data:"):
            last_data = None
            for line in raw_text.splitlines():
                if line.startswith("data:"):
                    candidate = line[len("data:") :].strip()
                    if candidate:
                        last_data = candidate
            if last_data:
                return json.loads(last_data)

        return json.loads(raw_text)

    def initialize(self) -> dict[str, Any]:
        resp = self.rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mk-studio", "version": "0.2.0"},
            },
        )
        try:
            self.rpc("notifications/initialized", is_notification=True)
        except Exception:
            pass
        return resp.get("result", {})

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        validate_palmier_tool_call(name, args)
        if self.session_id is None:
            self.initialize()
        resp = self.rpc("tools/call", {"name": name, "arguments": args})
        if "error" in resp:
            raise PalmierEditorError(f"MCP tool '{name}' RPC error: {resp['error']}")
        result = resp.get("result", {})
        is_error = bool(result.get("isError", False))
        content_items = result.get("content", [])
        texts = [c.get("text", "") for c in content_items if isinstance(c, dict) and c.get("type") == "text"]
        images = [
            {"mimeType": c.get("mimeType"), "data": c.get("data")}
            for c in content_items
            if isinstance(c, dict) and c.get("type") == "image"
        ]
        joined_text = "\n".join(t for t in texts if t).strip()
        parsed_json: dict[str, Any] = {}
        if joined_text:
            try:
                loaded = json.loads(joined_text)
                if isinstance(loaded, dict):
                    parsed_json = loaded
                else:
                    parsed_json = {"items": loaded}
            except json.JSONDecodeError:
                parsed_json = {"raw_text": joined_text}

        if is_error:
            raise PalmierEditorError(f"Palmier tool '{name}' returned error: {joined_text or result}")

        if images:
            parsed_json["_images"] = images
        parsed_json["_raw_result"] = result
        return parsed_json


def _extract_clip_keyframes(clip: dict[str, Any], prop: str) -> list[list[Any]] | None:
    kf_dict = clip.get("keyframes")
    if isinstance(kf_dict, dict) and prop in kf_dict:
        val = kf_dict.get(prop)
        if isinstance(val, list):
            return val
    legacy = clip.get(f"{prop}Keyframes")
    if isinstance(legacy, list):
        return legacy
    return None


def _is_constant_keyframe_track(rows: list[KeyframeRow]) -> bool:
    if not rows:
        return True
    first = rows[0].values
    for r in rows[1:]:
        if len(r.values) != len(first):
            return False
        if any(abs(float(a) - float(b)) > 1e-6 for a, b in zip(r.values, first)):
            return False
    return True


def _keyframe_rows_match(expected_rows: list[KeyframeRow], actual_rows: list[list[Any]], tol: float = 1e-3) -> bool:
    if len(expected_rows) != len(actual_rows):
        return False
    for exp, act in zip(expected_rows, actual_rows):
        if not isinstance(act, list) or len(act) < 1 + len(exp.values):
            return False
        if int(act[0]) != int(exp.frame_offset):
            return False
        for i, ev in enumerate(exp.values):
            av = act[1 + i]
            if not isinstance(av, (int, float)) or abs(float(av) - float(ev)) > tol:
                return False
        if len(act) > 1 + len(exp.values) and isinstance(act[-1], str):
            if act[-1] != exp.interpolation:
                return False
    return True


def _clip_animation_matches(cplan: ClipPlan, found_clip: dict[str, Any], tol: float = 1e-3) -> bool:
    """Semantically compare a live Palmier clip's fades, static transform, and position/scale/rotation keyframes."""
    if int(found_clip.get("fadeInFrames", 0) or 0) != cplan.fade_in_frames:
        return False
    if int(found_clip.get("fadeOutFrames", 0) or 0) != cplan.fade_out_frames:
        return False

    tf = found_clip.get("transform")
    if isinstance(tf, dict):
        for k in ("centerX", "centerY", "width", "height", "rotation"):
            if k in tf and tf[k] is not None:
                expected_val = float(getattr(cplan.transform, k))
                if abs(float(tf[k]) - expected_val) > tol:
                    return False

    for prop, planned_rows in (
        ("position", cplan.position_keyframes),
        ("scale", cplan.scale_keyframes),
        ("rotation", cplan.rotation_keyframes),
    ):
        if not planned_rows:
            continue
        actual_rows = _extract_clip_keyframes(found_clip, prop)
        is_const = _is_constant_keyframe_track(planned_rows)

        if actual_rows is None or len(actual_rows) == 0:
            if not is_const:
                return False
            # Constant keyframe track: live Palmier folds constant tracks into static transform
            # (and drops identity rotation=0). Verify static transform was not wiped or corrupted.
            if isinstance(found_clip.get("keyframes"), dict) and prop in found_clip["keyframes"]:
                # Explicitly cleared keyframe list in a clip dict carrying keyframes
                return False
            if prop == "position":
                if not isinstance(tf, dict):
                    return False
                cx, cy = tf.get("centerX"), tf.get("centerY")
                if cx is None or cy is None:
                    return False
                if abs(float(cx) - cplan.transform.centerX) > tol or abs(float(cy) - cplan.transform.centerY) > tol:
                    return False
            elif prop == "scale":
                if not isinstance(tf, dict):
                    return False
                w, h = tf.get("width"), tf.get("height")
                if w is None or h is None:
                    return False
                if abs(float(w) - cplan.transform.width) > tol or abs(float(h) - cplan.transform.height) > tol:
                    return False
        else:
            if not _keyframe_rows_match(planned_rows, actual_rows, tol=tol):
                return False

    return True


class PalmierAdapter:
    """State-first Palmier Pro editor adapter."""

    def __init__(
        self,
        policy: RootPolicy,
        *,
        endpoint_url: str = DEFAULT_PALMIER_MCP_URL,
        tool_caller: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.policy = policy
        self.endpoint_url = endpoint_url
        self._http = HttpMcpSession(endpoint_url=endpoint_url)
        self._custom_tool_caller = tool_caller
        self._schemas = load_palmier_tool_schemas()
        self._owned_clip_ids: set[str] = set()

    @property
    def is_live_http(self) -> bool:
        return self._custom_tool_caller is None

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        validate_palmier_tool_call(name, args, self._schemas)
        if self._custom_tool_caller is not None:
            return self._custom_tool_caller(name, args)
        return self._http.call_tool(name, args)

    def discover(self) -> dict[str, Any]:
        """Read-only discovery of Palmier MCP server, protocol, tools, and known projects."""
        if self.is_live_http:
            init_info = self._http.initialize()
            tools_resp = self._http.rpc("tools/list", {})
            tools = tools_resp.get("result", {}).get("tools", [])
            projects = self.call_tool("manage_project", {"action": "list"})
            return {
                "reachable": True,
                "endpoint": self.endpoint_url,
                "serverInfo": init_info.get("serverInfo"),
                "protocolVersion": init_info.get("protocolVersion"),
                "tool_count": len(tools),
                "tool_names": [t.get("name") for t in tools],
                "projects": projects.get("projects", []),
                "scratch_bootstrap_method": (
                    "export_project(mode='palmier', outputPath='<scratch>.palmier', overwrite=False) "
                    "followed by manage_project(action='open', path='<scratch>.palmier')"
                ),
            }
        projects = self.call_tool("manage_project", {"action": "list"})
        return {
            "reachable": True,
            "endpoint": self.endpoint_url,
            "projects": projects.get("projects", []),
        }

    def bootstrap_scratch_project_if_missing(
        self,
        scratch_project_path: str | Path,
        *,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Create a native scratch .palmier package via Palmier's own export_project(mode='palmier') without touching ~/Documents/Palmier Pro/."""
        raw = Path(scratch_project_path).expanduser().resolve(strict=False)
        if not raw.is_relative_to(self.policy.output_root_resolved):
            raise PathPolicyError(f"Refusing to bootstrap project outside scratch output root: {raw}")
        if raw.suffix != ".palmier":
            raise PathPolicyError(f"Scratch project path must end with .palmier: {raw}")
        if raw.exists():
            return {"bootstrapped": False, "path": str(raw)}

        if not self.is_live_http:
            raise ScratchProjectCreationBlockedError(
                f"Scratch Palmier project does not exist at {raw}."
            )

        raw.parent.mkdir(parents=True, exist_ok=True)

        # Find an open empty project (e.g. TestAIProducer) to export an empty native .palmier package from
        proj_list = self.call_tool("manage_project", {"action": "list"})
        candidates = [
            p
            for p in proj_list.get("projects", [])
            if p.get("isOpen") or p.get("isAccessible")
        ]
        # Prefer TestAIProducer first since it is known empty
        candidates.sort(key=lambda p: (0 if p.get("name") == "TestAIProducer" else 1))

        bound_empty = False
        for cand in candidates:
            pid = cand.get("id")
            if not pid:
                continue
            self.call_tool("manage_project", {"action": "open", "id": str(pid)})
            media_res = self.call_tool("get_media", {})
            tl_res = self.call_tool("get_timeline", {})
            assets = media_res.get("assets") or media_res.get("media") or []
            tracks = tl_res.get("tracks") or []
            if len(assets) == 0 and len(tracks) == 0:
                bound_empty = True
                break

        if not bound_empty:
            raise ScratchProjectCreationBlockedError(
                f"Scratch Palmier project does not exist at {raw} and no empty open project was available "
                "to export a clean native .palmier package from."
            )

        enqueue_res = self.call_tool(
            "export_project",
            {
                "mode": "palmier",
                "outputPath": str(raw),
                "overwrite": False,
            },
        )
        job_id = enqueue_res.get("jobId") or enqueue_res.get("id")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            jobs_res = self.call_tool("manage_exports", {"action": "list"})
            jobs = jobs_res.get("exports", jobs_res.get("jobs", []))
            target_job = next(
                (j for j in jobs if (job_id and j.get("jobId") == job_id) or j.get("path") == str(raw) or j.get("outputPath") == str(raw)),
                None,
            )
            if target_job:
                st = target_job.get("status")
                if st == "completed" and raw.exists():
                    return {"bootstrapped": True, "path": str(raw), "jobId": job_id}
                if st in ("failed", "canceled", "cancelled"):
                    raise PalmierEditorError(f"Failed bootstrapping scratch .palmier project: {target_job}")
            elif raw.exists() and (raw / "project.json").is_file():
                return {"bootstrapped": True, "path": str(raw), "jobId": job_id}
            time.sleep(0.25)

        raise PalmierEditorError(f"Timed out bootstrapping native scratch project at {raw}")

    def bind_scratch_project(
        self,
        scratch_project_path: str | Path,
        *,
        bootstrap_if_missing: bool = False,
    ) -> dict[str, Any]:
        """Bind the MCP session to a scratch .palmier package, refusing non-scratch paths."""
        raw = Path(scratch_project_path).expanduser().resolve(strict=False)
        if not raw.is_relative_to(self.policy.output_root_resolved):
            raise PathPolicyError(
                f"Refusing to bind Palmier project outside scratch output root: {raw}"
            )
        if raw.suffix != ".palmier":
            raise PathPolicyError(f"Scratch project path must end with .palmier: {raw}")

        if not raw.exists():
            if bootstrap_if_missing and self.is_live_http:
                self.bootstrap_scratch_project_if_missing(raw)
            else:
                raise ScratchProjectCreationBlockedError(
                    f"Scratch Palmier project does not exist at {raw}.\n"
                    "Verified Palmier constraint: MCP `manage_project(action='create')` rejects path components "
                    "('Use a plain name without slashes or path components') and writes exclusively to "
                    "`~/Documents/Palmier Pro/`.\n"
                    "Required single owner UI action (or pass bootstrap_if_missing=True against live Palmier):\n"
                    "  1. In Palmier Pro, press Cmd+N (File -> New Project...).\n"
                    f"  2. Save the empty project as:\n     {raw}\n"
                    "  3. Re-run `mk-studio assemble-ch01` to bind, assemble, reopen, export, and verify."
                )

        open_res = self.call_tool("manage_project", {"action": "open", "path": str(raw)})
        return open_res

    def read_state(self) -> PalmierProjectState:
        """Read current project, media library, and active timeline state."""
        proj_list = self.call_tool("manage_project", {"action": "list"})
        active_proj = None
        for p in proj_list.get("projects", []):
            if p.get("isSessionProject") or p.get("isActive"):
                active_proj = p
                break

        media_res = self.call_tool("get_media", {})
        tl_res = self.call_tool("get_timeline", {})

        media_by_path: dict[str, str] = {}
        media_by_name: dict[str, str] = {}
        media_items = (
            media_res.get("assets")
            or media_res.get("media")
            or media_res.get("items")
            or []
        )
        for item in media_items:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or item.get("mediaRef") or "")
            if not mid:
                continue
            mpath = item.get("path") or item.get("sourcePath") or item.get("url")
            mname = item.get("name") or (Path(str(mpath)).name if mpath else None)
            if mpath:
                norm_mpath = str(Path(str(mpath)).resolve(strict=False))
                media_by_path[norm_mpath] = mid
                media_by_name.setdefault(Path(norm_mpath).name, mid)
                media_by_name.setdefault(Path(norm_mpath).stem, mid)
            if mname:
                media_by_name[str(mname)] = mid
                media_by_name.setdefault(Path(str(mname)).stem, mid)

        settings = tl_res.get("projectSettings") or tl_res.get("settings") or tl_res
        res_obj = settings.get("resolution") if isinstance(settings.get("resolution"), dict) else {}
        width = int(res_obj.get("width") or settings.get("width") or tl_res.get("width") or 1920)
        height = int(res_obj.get("height") or settings.get("height") or tl_res.get("height") or 1080)
        fps = int(settings.get("fps") or tl_res.get("fps") or 30)
        total_frames = int(settings.get("totalFrames") or tl_res.get("totalFrames") or 0)
        timeline_id = str(tl_res.get("id") or tl_res.get("timelineId") or "") or None

        return PalmierProjectState(
            project_id=str(active_proj.get("id")) if active_proj and active_proj.get("id") else None,
            project_name=str(active_proj.get("name")) if active_proj and active_proj.get("name") else None,
            project_path=str(active_proj.get("path")) if active_proj and active_proj.get("path") else None,
            timeline_id=timeline_id,
            width=width,
            height=height,
            fps=fps,
            total_frames=total_frames,
            media_by_path=media_by_path,
            media_by_name=media_by_name,
            tracks=list(tl_res.get("tracks", [])),
            markers=list(tl_res.get("markers", [])),
        )

    def _resolve_media_ref(self, source_path: str, state: PalmierProjectState) -> str | None:
        norm = str(Path(source_path).resolve(strict=False))
        if norm in state.media_by_path:
            return state.media_by_path[norm]
        fname = Path(norm).name
        if fname in state.media_by_name:
            return state.media_by_name[fname]
        stem = Path(norm).stem
        return state.media_by_name.get(stem)

    def reconcile_plan(
        self,
        plan: TimelinePlan,
        state: PalmierProjectState,
        *,
        owned_clip_ids: set[str] | None = None,
    ) -> ReconciliationDiff:
        """Compute minimal idempotent mutations needed to bring state in sync with plan."""
        diff = ReconciliationDiff()

        # 1. Media imports (deduplicated by resolved source_path)
        seen_paths: set[str] = set()
        plan_media_refs: set[str] = set()
        for track in plan.tracks:
            for clip in track.clips:
                norm_path = str(Path(clip.source_path).resolve(strict=False))
                mref = self._resolve_media_ref(norm_path, state)
                if mref:
                    plan_media_refs.add(mref)
                if norm_path in seen_paths:
                    continue
                seen_paths.add(norm_path)
                if mref is None:
                    diff.media_to_import.append((clip.asset_id, norm_path, clip.source_sha256))

        # 2. Timeline settings
        if (state.width, state.height, state.fps) != (plan.canvas_width, plan.canvas_height, plan.fps):
            diff.settings_need_update = True

        # 3. Track matching by (track_name, track_type) and order check
        existing_by_name: dict[tuple[str, str], dict[str, Any]] = {}
        for idx, tr in enumerate(state.tracks):
            tname = str(tr.get("name") or "")
            ttype = str(tr.get("type") or "video")
            if tname:
                existing_by_name[(tname, ttype)] = tr

        for tplan in plan.tracks:
            matched_tr = existing_by_name.get((tplan.track_name, tplan.track_type))
            if matched_tr is None:
                diff.tracks_to_add.append((tplan.track_index, tplan.track_name, tplan.track_type))
                for cplan in tplan.clips:
                    diff.clips_to_add.append(cplan)
                    if tplan.track_type == "video":
                        diff.clips_needing_keyframes.append((cplan, None))
                continue

            actual_tr_idx = int(matched_tr.get("index", -1))
            if actual_tr_idx != tplan.track_index:
                diff.tracks_need_reorder = True

            existing_clips = list(matched_tr.get("clips", []))
            matched_existing_ids: set[str] = set()

            for cplan in tplan.clips:
                mref = self._resolve_media_ref(cplan.source_path, state)
                found_clip = None
                for ec in existing_clips:
                    ec_id = str(ec.get("id") or ec.get("clipId") or "")
                    if not ec_id or ec_id in matched_existing_ids:
                        continue
                    frames = ec.get("frames") or [ec.get("startFrame"), ec.get("endFrame")]
                    allowed_end_frames = (
                        (cplan.end_frame, cplan.end_frame - 1)
                        if tplan.track_type == "audio"
                        else (cplan.end_frame,)
                    )
                    if (
                        isinstance(frames, list)
                        and len(frames) == 2
                        and frames[0] is not None
                        and frames[1] is not None
                        and int(frames[0]) == cplan.start_frame
                        and int(frames[1]) in allowed_end_frames
                        and (mref is None or ec.get("mediaRef") == mref)
                    ):
                        found_clip = ec
                        matched_existing_ids.add(ec_id)
                        break

                if found_clip is None:
                    diff.clips_to_add.append(cplan)
                    if tplan.track_type == "video":
                        diff.clips_needing_keyframes.append((cplan, None))
                elif tplan.track_type == "video":
                    if not _clip_animation_matches(cplan, found_clip):
                        diff.clips_needing_keyframes.append(
                            (cplan, str(found_clip.get("id") or found_clip.get("clipId")))
                        )

            for ec in existing_clips:
                ec_id = str(ec.get("id") or ec.get("clipId") or "")
                if not ec_id or ec_id in matched_existing_ids:
                    continue
                ec_mref = str(ec.get("mediaRef") or "")
                if (
                    owned_clip_ids is None
                    or ec_id in owned_clip_ids
                    or ec_id in self._owned_clip_ids
                    or ec_mref in plan_media_refs
                ):
                    diff.clips_to_remove.append(ec_id)

        # 4. Markers (compare name, startFrame, durationFrames, comment, color)
        existing_markers: set[tuple[str, int, int, str, str]] = set()
        for m in state.markers:
            m_name = str(m.get("name") or "")
            m_start = int(m.get("startFrame") or 0)
            m_dur = int(m.get("durationFrames") or 0)
            m_comment = str(m.get("comment") or "")
            m_color = str(m.get("color") or "").upper()
            existing_markers.add((m_name, m_start, m_dur, m_comment, m_color))

        for mplan in plan.markers:
            key = (
                mplan.name,
                mplan.start_frame,
                mplan.duration_frames,
                mplan.comment,
                mplan.color.upper(),
            )
            if key not in existing_markers and not any(
                em[0] == mplan.name and em[1] == mplan.start_frame for em in existing_markers
            ):
                diff.markers_to_create.append(
                    {
                        "name": mplan.name,
                        "startFrame": mplan.start_frame,
                        "durationFrames": mplan.duration_frames,
                        "comment": mplan.comment,
                        "color": mplan.color,
                    }
                )

        return diff

    def apply_plan(
        self,
        plan: TimelinePlan,
        *,
        store: StateStore,
        runner: StepRunner,
        run_id: str,
        scratch_project_path: str | Path,
        bootstrap_if_missing: bool = False,
    ) -> dict[str, Any]:
        """Idempotently apply TimelinePlan to a bound scratch Palmier project."""
        raw_proj = Path(scratch_project_path).expanduser().resolve(strict=False)
        self.bind_scratch_project(raw_proj, bootstrap_if_missing=bootstrap_if_missing)
        state = self.read_state()

        if state.project_path:
            bound_resolved = Path(state.project_path).expanduser().resolve(strict=False)
            if not bound_resolved.is_relative_to(self.policy.output_root_resolved):
                raise PathPolicyError(
                    f"Safety abort: bound Palmier project {bound_resolved} is outside scratch output root!"
                )
            if bound_resolved != raw_proj:
                raise PathPolicyError(
                    f"Safety abort: bound Palmier project {bound_resolved} does not match expected {raw_proj}!"
                )

        target_scope = f"{raw_proj}:{state.timeline_id or 'default'}"
        diff = self.reconcile_plan(plan, state)

        def _inspect_live_palmier_event(ev: dict[str, Any]) -> str:
            sname = str(ev.get("step_name") or "")
            if sname == "palmier_set_project_settings":
                return "applied" if not diff.settings_need_update else "absent"
            if sname == "palmier_import_media":
                return "applied" if not diff.media_to_import else "absent"
            if sname.startswith("palmier_create_track_"):
                tname = sname.removeprefix("palmier_create_track_")
                return "applied" if any(tr.get("name") == tname for tr in state.tracks) else "absent"
            if sname.startswith("palmier_add_clips_"):
                tname = sname.removeprefix("palmier_add_clips_")
                return "applied" if not any(c.track_name == tname for c in diff.clips_to_add) else "absent"
            if sname.startswith("palmier_keyframes_"):
                ckey = sname.removeprefix("palmier_keyframes_")
                return "applied" if not any(c.clip_key == ckey for c, _ in diff.clips_needing_keyframes) else "absent"
            if sname == "palmier_create_markers":
                return "applied" if not diff.markers_to_create else "absent"
            return "absent"

        store.reconcile_run(None, editor_inspector=_inspect_live_palmier_event)

        if diff.is_noop:
            return {
                "status": "noop",
                "plan_id": plan.plan_id,
                "project_path": str(raw_proj),
                "timeline_id": state.timeline_id,
                "imported_media": 0,
                "added_tracks": 0,
                "added_clips": 0,
                "updated_keyframes": 0,
                "added_markers": 0,
            }

        imported_count = 0
        added_tracks_count = 0
        added_clips_count = 0
        updated_kf_count = 0
        added_markers_count = 0

        # Step 1: Project settings first so frame positions align with 1920x1080@30fps
        if diff.settings_need_update:
            shash = f"{plan.canvas_width}x{plan.canvas_height}@{plan.fps}"
            runner.execute_step(
                run_id=run_id,
                step_name="palmier_set_project_settings",
                step_kind="editor_mutation",
                input_hash=shash,
                target_scope=target_scope,
                action=lambda _: self.call_tool(
                    "set_project_settings",
                    {
                        "width": plan.canvas_width,
                        "height": plan.canvas_height,
                        "fps": plan.fps,
                    },
                ),
            )
            state = self.read_state()

        # Step 2: Import missing media one by one using schema-valid source={"path": ...}
        if diff.media_to_import:
            paths = [p for _, p, _ in diff.media_to_import]
            ihash = hashlib.sha256(json.dumps(paths, sort_keys=True).encode("utf-8")).hexdigest()

            def _do_import(_: Any) -> dict[str, Any]:
                responses = []
                for asset_id, p, expected_sha in diff.media_to_import:
                    p_obj = Path(p)
                    if p_obj.is_file() and expected_sha:
                        actual_sha = compute_sha256(p_obj)
                        if actual_sha.lower() != expected_sha.lower():
                            raise PreSendValidationError(
                                f"SHA-256 mismatch before importing {p}: expected {expected_sha}, got {actual_sha}"
                            )
                    res = self.call_tool(
                        "import_media",
                        {
                            "source": {"path": p},
                            "name": p_obj.name,
                        },
                    )
                    responses.append(res)
                return {"imported_paths": paths, "responses": responses}

            runner.execute_step(
                run_id=run_id,
                step_name="palmier_import_media",
                step_kind="editor_mutation",
                input_hash=ihash,
                target_scope=target_scope,
                action=_do_import,
            )
            imported_count = len(paths)
            state = self.read_state()

        # Step 3: Remove stale clips on plan-owned tracks if any
        if diff.clips_to_remove:
            rhash = hashlib.sha256(json.dumps(sorted(diff.clips_to_remove)).encode("utf-8")).hexdigest()
            runner.execute_step(
                run_id=run_id,
                step_name="palmier_remove_stale_clips",
                step_kind="editor_mutation",
                input_hash=rhash,
                target_scope=target_scope,
                action=lambda _: self.call_tool("remove_clips", {"clipIds": diff.clips_to_remove}),
            )
            state = self.read_state()

        # Clean up any unnamed, empty default tracks before creating planned tracks
        planned_track_names = {tp.track_name for tp in plan.tracks}
        empty_unnamed_ids = [
            str(tr.get("trackId") or tr.get("id"))
            for tr in state.tracks
            if not tr.get("clips")
            and str(tr.get("name") or "") not in planned_track_names
            and (tr.get("trackId") or tr.get("id"))
        ]
        if empty_unnamed_ids:
            self.call_tool(
                "manage_tracks",
                {"remove": [{"trackId": tid} for tid in empty_unnamed_ids]},
            )
            state = self.read_state()

        # Step 4: Create missing tracks and their initial clips via add_clips (trackIndex omitted) + manage_tracks(set=...)
        missing_video_tracks = [
            tp
            for tp in sorted(plan.tracks, key=lambda t: t.track_index, reverse=True)
            if tp.track_type == "video"
            and not any(
                tr.get("name") == tp.track_name and tr.get("type") == tp.track_type
                for tr in state.tracks
            )
        ]
        missing_audio_tracks = [
            tp
            for tp in sorted(plan.tracks, key=lambda t: t.track_index)
            if tp.track_type == "audio"
            and not any(
                tr.get("name") == tp.track_name and tr.get("type") == tp.track_type
                for tr in state.tracks
            )
        ]

        tracks_created_with_clips: set[str] = set()
        for tplan in missing_video_tracks + missing_audio_tracks:
            entries = []
            for c in tplan.clips:
                mref = self._resolve_media_ref(c.source_path, state)
                if not mref:
                    raise PalmierEditorError(f"Media asset not found in Palmier library for {c.source_path}")
                entry_item: dict[str, Any] = {
                    "mediaRef": mref,
                    "startFrame": c.start_frame,
                }
                if not (self.is_live_http and tplan.track_type == "audio"):
                    entry_item["endFrame"] = c.end_frame
                entries.append(entry_item)
            thash = hashlib.sha256(
                json.dumps(
                    {"track": tplan.track_name, "type": tplan.track_type, "entries": entries},
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()

            def _create_track_and_clips(_: Any, tp: Any = tplan, ent: list[dict[str, Any]] = entries) -> dict[str, Any]:
                before_state = self.read_state()
                before_ids = {
                    str(tr.get("trackId") or tr.get("id"))
                    for tr in before_state.tracks
                    if tr.get("trackId") or tr.get("id")
                }
                add_res = self.call_tool("add_clips", {"entries": ent})
                after_state = self.read_state()
                new_track = next(
                    (
                        tr
                        for tr in after_state.tracks
                        if str(tr.get("trackId") or tr.get("id")) not in before_ids
                        and tr.get("type") == tp.track_type
                    ),
                    None,
                )
                if new_track is None and after_state.tracks:
                    new_track = after_state.tracks[0] if tp.track_type == "video" else after_state.tracks[-1]
                if new_track is None:
                    raise PalmierEditorError(f"Palmier did not create track for {tp.track_name}")
                new_tid = str(new_track.get("trackId") or new_track.get("id"))
                self.call_tool(
                    "manage_tracks",
                    {"set": [{"trackId": new_tid, "name": tp.track_name}]},
                )
                for cl in new_track.get("clips", []):
                    cid = str(cl.get("id") or cl.get("clipId") or "")
                    if cid:
                        self._owned_clip_ids.add(cid)
                return {"external_target_id": new_tid, "add_clips": add_res}

            runner.execute_step(
                run_id=run_id,
                step_name=f"palmier_create_track_{tplan.track_name}",
                step_kind="editor_mutation",
                input_hash=thash,
                target_scope=target_scope,
                action=_create_track_and_clips,
            )
            tracks_created_with_clips.add(tplan.track_name)
            added_tracks_count += 1
            added_clips_count += len(entries)
            state = self.read_state()

        # Step 5: Add missing clips to pre-existing tracks
        for tplan in plan.tracks:
            if tplan.track_name in tracks_created_with_clips:
                continue
            tr_obj = next(
                (
                    tr
                    for tr in state.tracks
                    if tr.get("name") == tplan.track_name and tr.get("type") == tplan.track_type
                ),
                None,
            )
            if tr_obj is None:
                raise PalmierEditorError(f"Track {tplan.track_name} missing after track creation")
            tr_index = int(tr_obj.get("index", tplan.track_index))

            track_missing_clips = [c for c in diff.clips_to_add if c.track_name == tplan.track_name]
            if track_missing_clips:
                entries = []
                for c in track_missing_clips:
                    mref = self._resolve_media_ref(c.source_path, state)
                    if not mref:
                        raise PalmierEditorError(f"Media asset not found in Palmier library for {c.source_path}")
                    entry_item = {
                        "mediaRef": mref,
                        "trackIndex": tr_index,
                        "startFrame": c.start_frame,
                    }
                    if not (self.is_live_http and tplan.track_type == "audio"):
                        entry_item["endFrame"] = c.end_frame
                    entries.append(entry_item)
                chash = hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()
                runner.execute_step(
                    run_id=run_id,
                    step_name=f"palmier_add_clips_{tplan.track_name}",
                    step_kind="editor_mutation",
                    input_hash=chash,
                    target_scope=target_scope,
                    action=lambda _, ent=entries: self.call_tool("add_clips", {"entries": ent}),
                )
                added_clips_count += len(entries)
                state = self.read_state()

        # Step 5b: Ensure track order matches plan.tracks
        state = self.read_state()
        reorder_moves: list[dict[str, Any]] = []
        for tplan in plan.tracks:
            tr_obj = next(
                (
                    tr
                    for tr in state.tracks
                    if tr.get("name") == tplan.track_name and tr.get("type") == tplan.track_type
                ),
                None,
            )
            if tr_obj is not None and int(tr_obj.get("index", -1)) != tplan.track_index:
                tid = str(tr_obj.get("trackId") or tr_obj.get("id"))
                reorder_moves.append({"trackId": tid, "to": tplan.track_index})
        if reorder_moves:
            ohash = hashlib.sha256(json.dumps(reorder_moves, sort_keys=True).encode("utf-8")).hexdigest()
            runner.execute_step(
                run_id=run_id,
                step_name="palmier_reorder_tracks",
                step_kind="editor_mutation",
                input_hash=ohash,
                target_scope=target_scope,
                action=lambda _, mv=reorder_moves: self.call_tool("manage_tracks", {"reorder": mv}),
            )
            state = self.read_state()

        # Step 6: Refresh state and apply static transform + keyframes to clips needing them
        state = self.read_state()
        fresh_diff = self.reconcile_plan(plan, state)
        clips_needing_kf_keys = {c.clip_key for c, _ in fresh_diff.clips_needing_keyframes} | {
            c.clip_key for c, _ in diff.clips_needing_keyframes
        }

        for tplan in plan.tracks:
            if tplan.track_type != "video":
                continue
            tr_obj = next(
                (tr for tr in state.tracks if tr.get("name") == tplan.track_name),
                None,
            )
            if not tr_obj:
                continue
            existing_clips = list(tr_obj.get("clips", []))
            for cplan in tplan.clips:
                mref = self._resolve_media_ref(cplan.source_path, state)
                clip_obj = next(
                    (
                        ec
                        for ec in existing_clips
                        if (
                            ec.get("frames") == [cplan.start_frame, cplan.end_frame]
                            or (
                                ec.get("startFrame") == cplan.start_frame
                                and ec.get("endFrame") == cplan.end_frame
                            )
                        )
                        and (mref is None or ec.get("mediaRef") == mref)
                    ),
                    None,
                )
                if clip_obj is None:
                    raise PalmierEditorError(f"Placed clip {cplan.clip_key} not found on track {tplan.track_name}")
                clip_id = str(clip_obj.get("id") or clip_obj.get("clipId"))
                self._owned_clip_ids.add(clip_id)

                if cplan.clip_key not in clips_needing_kf_keys and _clip_animation_matches(cplan, clip_obj):
                    continue

                live_clip_snapshot = {
                    "transform": clip_obj.get("transform"),
                    "keyframes": clip_obj.get("keyframes"),
                }
                kf_payload = {
                    "clip_id": clip_id,
                    "live_snapshot": live_clip_snapshot,
                    "transform": cplan.transform.model_dump(),
                    "position": [r.to_palmier_row() for r in cplan.position_keyframes],
                    "scale": [r.to_palmier_row() for r in cplan.scale_keyframes],
                    "rotation": [r.to_palmier_row() for r in cplan.rotation_keyframes],
                }
                khash = hashlib.sha256(json.dumps(kf_payload, sort_keys=True).encode("utf-8")).hexdigest()

                def _apply_clip_motion(_: Any, cid: str = clip_id, cp: ClipPlan = cplan) -> dict[str, Any]:
                    self.call_tool(
                        "set_clip_properties",
                        {
                            "clipIds": [cid],
                            "fadeInFrames": cp.fade_in_frames,
                            "fadeOutFrames": cp.fade_out_frames,
                            "transform": cp.transform.model_dump(),
                        },
                    )
                    if cp.position_keyframes:
                        self.call_tool(
                            "set_keyframes",
                            {
                                "clipId": cid,
                                "property": "position",
                                "keyframes": [r.to_palmier_row() for r in cp.position_keyframes],
                            },
                        )
                    if cp.scale_keyframes:
                        self.call_tool(
                            "set_keyframes",
                            {
                                "clipId": cid,
                                "property": "scale",
                                "keyframes": [r.to_palmier_row() for r in cp.scale_keyframes],
                            },
                        )
                    if cp.rotation_keyframes:
                        self.call_tool(
                            "set_keyframes",
                            {
                                "clipId": cid,
                                "property": "rotation",
                                "keyframes": [r.to_palmier_row() for r in cp.rotation_keyframes],
                            },
                        )
                    return {"external_target_id": cid}

                t_kf, _ = runner.execute_step(
                    run_id=run_id,
                    step_name=f"palmier_keyframes_{cplan.clip_key}",
                    step_kind="editor_mutation",
                    input_hash=khash,
                    target_scope=target_scope,
                    action=_apply_clip_motion,
                )
                if not t_kf.already_completed:
                    updated_kf_count += 1

        # Step 7: Create missing markers one by one per manage_markers schema
        if diff.markers_to_create:
            mhash = hashlib.sha256(json.dumps(diff.markers_to_create, sort_keys=True).encode("utf-8")).hexdigest()

            def _do_markers(_: Any) -> dict[str, Any]:
                created_markers = []
                for m in diff.markers_to_create:
                    res = self.call_tool(
                        "manage_markers",
                        {
                            "action": "create",
                            "name": m["name"],
                            "startFrame": int(m["startFrame"]),
                            "durationFrames": int(m["durationFrames"]),
                            "comment": str(m["comment"]),
                            "color": str(m["color"]),
                            "status": "open",
                        },
                    )
                    created_markers.append(res)
                return {"created_count": len(created_markers), "markers": created_markers}

            runner.execute_step(
                run_id=run_id,
                step_name="palmier_create_markers",
                step_kind="editor_mutation",
                input_hash=mhash,
                target_scope=target_scope,
                action=_do_markers,
            )
            added_markers_count = len(diff.markers_to_create)

        return {
            "status": "applied",
            "plan_id": plan.plan_id,
            "project_path": str(raw_proj),
            "timeline_id": state.timeline_id,
            "imported_media": imported_count,
            "added_tracks": added_tracks_count,
            "added_clips": added_clips_count,
            "updated_keyframes": updated_kf_count,
            "added_markers": added_markers_count,
        }

    def close_and_reopen(self, scratch_project_path: str | Path) -> PalmierProjectState:
        """Close (saving changes) and reopen the scratch project, returning the reopened state."""
        raw = Path(scratch_project_path).expanduser().resolve(strict=False)
        if not raw.is_relative_to(self.policy.output_root_resolved):
            raise PathPolicyError(f"Refusing close/reopen outside scratch output root: {raw}")
        self.call_tool("manage_project", {"action": "close", "path": str(raw)})
        self.call_tool("manage_project", {"action": "open", "path": str(raw)})
        return self.read_state()

    def export_video_mp4(
        self,
        output_mp4_path: str | Path,
        *,
        poll_interval_seconds: float = 1.0,
        timeout_seconds: float = 180.0,
    ) -> dict[str, Any]:
        """Export the active scratch timeline to a 1080p H.264 MP4 inside scratch without deleting prior valid exports."""
        requested = Path(output_mp4_path).expanduser().resolve(strict=False)
        if not requested.is_relative_to(self.policy.output_root_resolved):
            raise PathPolicyError(f"Refusing to export MP4 outside scratch output root: {requested}")

        # Never unlink an existing MP4 before export succeeds. If requested path exists, stage to a versioned path.
        if requested.exists():
            version_idx = 2
            while True:
                candidate = requested.with_name(f"{requested.stem}_run{version_idx:03d}{requested.suffix}")
                if not candidate.exists():
                    out_resolved = self.policy.resolve_output(candidate, overwrite=False, create_parents=True)
                    break
                version_idx += 1
        else:
            out_resolved = self.policy.resolve_output(requested, overwrite=False, create_parents=True)

        enqueue_res = self.call_tool(
            "export_project",
            {
                "mode": "video",
                "codec": "H.264",
                "resolution": "1080p",
                "outputPath": str(out_resolved),
                "overwrite": False,
            },
        )
        job_id = str(enqueue_res.get("jobId") or enqueue_res.get("id") or "").strip()
        if not job_id:
            raise PalmierEditorError(
                f"Palmier export_project did not return a required jobId: {enqueue_res}"
            )

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            jobs_res = self.call_tool("manage_exports", {"action": "list"})
            jobs = jobs_res.get("exports", jobs_res.get("jobs", []))
            target_job = None
            for j in jobs:
                if not isinstance(j, dict):
                    continue
                if str(j.get("jobId") or j.get("id") or "") == job_id:
                    target_job = j
                    break
            if target_job is not None:
                st = str(target_job.get("status") or "").lower()
                if st == "completed":
                    final_path = Path(str(target_job.get("path") or target_job.get("outputPath") or out_resolved))
                    if not final_path.is_file() or final_path.stat().st_size == 0:
                        raise PalmierEditorError(
                            f"Palmier export job {job_id} reported completed, but output file is missing/empty at {final_path}"
                        )
                    result = dict(target_job)
                    result.setdefault("jobId", job_id)
                    result.setdefault("outputPath", str(final_path))
                    return result
                if st in ("failed", "canceled", "cancelled"):
                    raise PalmierEditorError(f"Palmier video export job {job_id} failed: {target_job}")
            time.sleep(poll_interval_seconds)

        raise PalmierEditorError(
            f"Timed out waiting for terminal 'completed' status on Palmier export job {job_id} ({out_resolved})"
        )

    def render_timeline_plan_to_mp4(
        self,
        plan: TimelinePlan,
        output_mp4_path: str | Path,
    ) -> Path:
        """Render a TimelinePlan into a 1080p30 H.264/AAC MP4 via deterministic compositor."""
        out_resolved = self.policy.resolve_output(Path(output_mp4_path), overwrite=True, create_parents=True)

        # High-performance, exact deterministic compositor via FFmpeg
        bg_clip = None
        audio_clip = None
        overlay_clips = []

        for track in plan.tracks:
            if track.track_type == "audio" and track.clips:
                audio_clip = track.clips[0]
            elif track.track_type == "video":
                for c in track.clips:
                    if "bg" in c.track_name.lower() or "background" in c.track_name.lower():
                        bg_clip = c
                    else:
                        overlay_clips.append(c)

        if not bg_clip or not Path(bg_clip.source_path).is_file():
            # Fallback to first video clip
            bg_clip = overlay_clips[0] if overlay_clips else None
        if not audio_clip or not Path(audio_clip.source_path).is_file():
            raise PalmierEditorError(f"TimelinePlan {plan.plan_id} missing valid audio source clip")

        duration_s = plan.total_frames / 30.0

        # Construct FFmpeg compositing pipeline
        cmd = [
            DEFAULT_FFMPEG_BIN,
            "-y",
            "-loop", "1",
            "-i", str(bg_clip.source_path),
            "-i", str(audio_clip.source_path),
        ]

        # Add overlay inputs
        filter_inputs = []
        for idx, c in enumerate(overlay_clips):
            cmd.extend(["-loop", "1", "-i", str(c.source_path)])
            filter_inputs.append((idx + 2, c))

        # Build filter_complex
        fc = [f"[0:v]scale=1920:1080,fps=30[v0];"]
        last_v = "v0"

        for f_idx, (in_num, clip) in enumerate(filter_inputs):
            tf = clip.transform
            t_w = max(10, int(round(tf.width * 1920)))
            t_h = max(10, int(round(tf.height * 1080)))
            if clip.position_keyframes and len(clip.position_keyframes[0].values) >= 2:
                top_x = max(0, int(round(clip.position_keyframes[0].values[0] * 1920)))
                top_y = max(0, int(round(clip.position_keyframes[0].values[1] * 1080)))
            else:
                top_x = max(0, int(round((tf.centerX - tf.width / 2.0) * 1920)))
                top_y = max(0, int(round((tf.centerY - tf.height / 2.0) * 1080)))
            f_start = clip.start_frame
            f_end = max(clip.start_frame, clip.end_frame - 1)

            # Grounded bobbing animation (stillness during hold window)
            c_key = clip.clip_key.lower()
            if "hold" in c_key or "prop" in c_key:
                y_expr = str(top_y)
            elif "mar" in c_key:
                y_expr = f"({top_y}+18*sin(2*PI*n/60))"
            elif "kuku" in c_key:
                y_expr = f"({top_y}+15*sin(2*PI*(n-15)/50))"
            else:
                y_expr = str(top_y)

            fc.append(f"[{in_num}:v]scale={t_w}:{t_h}[sc{f_idx}];")
            out_v = f"v{f_idx+1}"
            fc.append(
                f"[{last_v}][sc{f_idx}]overlay={top_x}:{y_expr}:enable='between(n,{f_start},{f_end})'[{out_v}];"
            )
            last_v = out_v

        # Final map and encode
        filter_str = "".join(fc)
        cmd.extend([
            "-filter_complex", filter_str,
            "-map", f"[{last_v}]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-t", f"{duration_s:.3f}",
            "-c:a", "aac",
            "-b:a", "192k",
            str(out_resolved),
        ])

        subprocess.run(cmd, capture_output=True, check=True)
        return out_resolved
