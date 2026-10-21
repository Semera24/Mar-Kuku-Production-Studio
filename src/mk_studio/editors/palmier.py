"""Palmier Pro MCP adapter with state-first reconciliation and scratch safety guards."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mk_studio.config import (
    DEFAULT_PALMIER_MCP_URL,
    PathPolicyError,
    RootPolicy,
)
from mk_studio.contracts import ClipPlan, TimelinePlan
from mk_studio.runner import StepRunner
from mk_studio.state import StateStore

MCP_PROTOCOL_VERSION = "2025-11-25"


class PalmierEditorError(RuntimeError):
    """Base error for Palmier MCP adapter operations."""


class ScratchProjectCreationBlockedError(PalmierEditorError):
    """Raised when the native scratch .palmier project does not yet exist and MCP create would write to ~/Documents/Palmier Pro/."""


@dataclass
class PalmierProjectState:
    """Snapshot of the bound Palmier project's media library and active timeline."""

    project_id: str | None
    project_name: str | None
    project_path: str | None
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

    media_to_import: list[tuple[str, str]] = field(default_factory=list)
    settings_need_update: bool = False
    tracks_to_add: list[tuple[int, str, str]] = field(default_factory=list)
    tracks_to_rename: list[tuple[str, str]] = field(default_factory=list)
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
            and not self.clips_to_remove
            and not self.clips_to_add
            and not self.clips_needing_keyframes
            and not self.markers_to_create
        )


class HttpMcpSession:
    """Minimal Streamable HTTP MCP client for Palmier Pro."""

    def __init__(self, endpoint_url: str = DEFAULT_PALMIER_MCP_URL, timeout_seconds: float = 15.0) -> None:
        self.endpoint_url = endpoint_url
        self.timeout_seconds = timeout_seconds
        self.session_id: str | None = None
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def rpc(self, method: str, params: dict[str, Any] | None = None, *, is_notification: bool = False) -> dict[str, Any]:
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

        # Handle either plain JSON or SSE data lines
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
                "clientInfo": {"name": "mk-studio", "version": "0.1.0"},
            },
        )
        try:
            self.rpc("notifications/initialized", is_notification=True)
        except Exception:
            pass
        return resp.get("result", {})

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session_id is None:
            self.initialize()
        resp = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if "error" in resp:
            raise PalmierEditorError(f"MCP tool '{name}' RPC error: {resp['error']}")
        result = resp.get("result", {})
        is_error = bool(result.get("isError", False))
        content_items = result.get("content", [])
        texts = [c.get("text", "") for c in content_items if isinstance(c, dict) and c.get("type") == "text"]
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

        parsed_json["_raw_result"] = result
        return parsed_json


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
        self._tool_caller = tool_caller or self._http.call_tool

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._tool_caller(name, arguments or {})

    def discover(self) -> dict[str, Any]:
        """Read-only discovery of Palmier MCP server, protocol, tools, and known projects."""
        if self._tool_caller == self._http.call_tool:
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
                "native_create_supports_custom_path": False,
                "native_create_constraint": (
                    "Palmier manage_project(action='create') calls AppState.createProject(named:), which rejects "
                    "slashes/path components and always writes to ~/Documents/Palmier Pro/<name>.palmier. "
                    "Direct scratch creation requires either File -> New Project... (Cmd+N) NSSavePanel in Palmier Pro "
                    "to save scratch/ch01-proof/E02_CH01_Technical_Proof_v001.palmier, or explicit owner approval."
                ),
            }
        else:
            projects = self.call_tool("manage_project", {"action": "list"})
            return {
                "reachable": True,
                "endpoint": self.endpoint_url,
                "projects": projects.get("projects", []),
            }

    def bind_scratch_project(self, scratch_project_path: str | Path) -> dict[str, Any]:
        """Bind the MCP session to an existing scratch .palmier package, refusing non-scratch paths."""
        raw = Path(scratch_project_path).expanduser().resolve(strict=False)
        if not raw.is_relative_to(self.policy.output_root_resolved):
            raise PathPolicyError(
                f"Refusing to bind Palmier project outside scratch output root: {raw}"
            )
        if raw.suffix != ".palmier":
            raise PathPolicyError(f"Scratch project path must end with .palmier: {raw}")

        if not raw.exists():
            raise ScratchProjectCreationBlockedError(
                f"Scratch Palmier project does not exist at {raw}.\n"
                "Verified Palmier constraint: MCP `manage_project(action='create')` rejects path components "
                "('Use a plain name without slashes or path components') and writes exclusively to "
                "`~/Documents/Palmier Pro/`.\n"
                "Required single owner UI action:\n"
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
        for item in media_res.get("media", media_res.get("items", [])):
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or item.get("mediaRef") or "")
            if not mid:
                continue
            mpath = item.get("path") or item.get("sourcePath") or item.get("url")
            mname = item.get("name") or (Path(str(mpath)).name if mpath else None)
            if mpath:
                media_by_path[str(Path(str(mpath)).resolve(strict=False))] = mid
            if mname:
                media_by_name[str(mname)] = mid

        settings = tl_res.get("projectSettings") or tl_res.get("settings") or tl_res
        res_obj = settings.get("resolution") or {}
        width = int(res_obj.get("width") or settings.get("width") or 1920)
        height = int(res_obj.get("height") or settings.get("height") or 1080)
        fps = int(settings.get("fps") or 30)
        total_frames = int(settings.get("totalFrames") or tl_res.get("totalFrames") or 0)

        return PalmierProjectState(
            project_id=str(active_proj.get("id")) if active_proj else None,
            project_name=str(active_proj.get("name")) if active_proj else None,
            project_path=str(active_proj.get("path")) if active_proj else None,
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
        return state.media_by_name.get(fname)

    def reconcile_plan(self, plan: TimelinePlan, state: PalmierProjectState) -> ReconciliationDiff:
        """Compute minimal idempotent mutations needed to bring state in sync with plan."""
        diff = ReconciliationDiff()

        # 1. Media imports (deduplicated by resolved source_path)
        seen_paths: set[str] = set()
        for track in plan.tracks:
            for clip in track.clips:
                norm_path = str(Path(clip.source_path).resolve(strict=False))
                if norm_path in seen_paths:
                    continue
                seen_paths.add(norm_path)
                if self._resolve_media_ref(norm_path, state) is None:
                    diff.media_to_import.append((clip.asset_id, norm_path))

        # 2. Timeline settings
        if (state.width, state.height, state.fps) != (plan.canvas_width, plan.canvas_height, plan.fps):
            diff.settings_need_update = True

        # 3. Track matching by (track_name, track_type) or fallback index
        existing_by_name: dict[tuple[str, str], dict[str, Any]] = {}
        for tr in state.tracks:
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
                    diff.clips_needing_keyframes.append((cplan, None))
                continue

            existing_clips = list(matched_tr.get("clips", []))
            matched_existing_ids: set[str] = set()

            for cplan in tplan.clips:
                mref = self._resolve_media_ref(cplan.source_path, state)
                found_clip = None
                for ec in existing_clips:
                    ec_id = str(ec.get("id") or "")
                    frames = ec.get("frames") or [ec.get("startFrame"), ec.get("endFrame")]
                    if (
                        isinstance(frames, list)
                        and len(frames) == 2
                        and int(frames[0]) == cplan.start_frame
                        and int(frames[1]) == cplan.end_frame
                        and (mref is None or ec.get("mediaRef") == mref)
                    ):
                        found_clip = ec
                        matched_existing_ids.add(ec_id)
                        break

                if found_clip is None:
                    diff.clips_to_add.append(cplan)
                    diff.clips_needing_keyframes.append((cplan, None))
                else:
                    # Check whether keyframes/transforms are already present on found_clip
                    has_expected_kf = True
                    if cplan.position_keyframes and not found_clip.get("positionKeyframes") and not found_clip.get("keyframes"):
                        has_expected_kf = False
                    if not has_expected_kf:
                        diff.clips_needing_keyframes.append((cplan, str(found_clip["id"])))

            for ec in existing_clips:
                ec_id = str(ec.get("id") or "")
                if ec_id and ec_id not in matched_existing_ids:
                    diff.clips_to_remove.append(ec_id)

        # 4. Markers
        existing_markers = {
            (str(m.get("name") or ""), int(m.get("startFrame") or 0))
            for m in state.markers
        }
        for mplan in plan.markers:
            if (mplan.name, mplan.start_frame) not in existing_markers:
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
    ) -> dict[str, Any]:
        """Idempotently apply TimelinePlan to a bound scratch Palmier project."""
        self.bind_scratch_project(scratch_project_path)
        state = self.read_state()

        if state.project_path:
            bound_resolved = Path(state.project_path).expanduser().resolve(strict=False)
            if not bound_resolved.is_relative_to(self.policy.output_root_resolved):
                raise PathPolicyError(
                    f"Safety abort: bound Palmier project {bound_resolved} is outside scratch output root!"
                )

        diff = self.reconcile_plan(plan, state)
        if diff.is_noop:
            return {
                "status": "noop",
                "plan_id": plan.plan_id,
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

        # Step 1: Import missing media
        if diff.media_to_import:
            paths = [p for _, p in diff.media_to_import]
            ihash = hashlib.sha256(json.dumps(paths, sort_keys=True).encode("utf-8")).hexdigest()

            def _do_import(_: Any) -> dict[str, Any]:
                res = self.call_tool(
                    "import_media",
                    {"sources": [{"path": p} for p in paths]},
                )
                return {"imported_paths": paths, "response": res}

            runner.execute_step(
                run_id=run_id,
                step_name="palmier_import_media",
                step_kind="editor_mutation",
                input_hash=ihash,
                action=_do_import,
            )
            imported_count = len(paths)
            state = self.read_state()

        # Step 2: Project settings
        if diff.settings_need_update:
            shash = f"{plan.canvas_width}x{plan.canvas_height}@{plan.fps}"
            runner.execute_step(
                run_id=run_id,
                step_name="palmier_set_project_settings",
                step_kind="editor_mutation",
                input_hash=shash,
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

        # Step 3: Remove stale clips if any
        if diff.clips_to_remove:
            rhash = hashlib.sha256(json.dumps(diff.clips_to_remove).encode("utf-8")).hexdigest()
            runner.execute_step(
                run_id=run_id,
                step_name="palmier_remove_stale_clips",
                step_kind="editor_mutation",
                input_hash=rhash,
                action=lambda _: self.call_tool("remove_clips", {"clipIds": diff.clips_to_remove}),
            )
            state = self.read_state()

        # Step 4: Ensure tracks exist in exact order
        for tplan in plan.tracks:
            existing_match = next(
                (
                    tr
                    for tr in state.tracks
                    if tr.get("name") == tplan.track_name and tr.get("type") == tplan.track_type
                ),
                None,
            )
            if existing_match is None:
                thash = f"{tplan.track_index}:{tplan.track_name}:{tplan.track_type}"
                runner.execute_step(
                    run_id=run_id,
                    step_name=f"palmier_add_track_{tplan.track_name}",
                    step_kind="editor_mutation",
                    input_hash=thash,
                    action=lambda _, tp=tplan: self.call_tool(
                        "manage_tracks",
                        {
                            "action": "add",
                            "type": tp.track_type,
                            "name": tp.track_name,
                            "atIndex": tp.track_index,
                        },
                    ),
                )
                added_tracks_count += 1
                state = self.read_state()

        # Step 5: Add missing clips per track and apply transforms/keyframes
        for tplan in plan.tracks:
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
                    entries.append(
                        {
                            "mediaRef": mref,
                            "trackIndex": tr_index,
                            "startFrame": c.start_frame,
                            "endFrame": c.end_frame,
                        }
                    )
                chash = hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()
                runner.execute_step(
                    run_id=run_id,
                    step_name=f"palmier_add_clips_{tplan.track_name}",
                    step_kind="editor_mutation",
                    input_hash=chash,
                    action=lambda _, ent=entries: self.call_tool("add_clips", {"entries": ent}),
                )
                added_clips_count += len(entries)
                state = self.read_state()

        # Refresh track clips and apply static transform + keyframes
        state = self.read_state()
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
                        if (ec.get("frames") == [cplan.start_frame, cplan.end_frame] or ec.get("startFrame") == cplan.start_frame)
                        and (mref is None or ec.get("mediaRef") == mref)
                    ),
                    None,
                )
                if clip_obj is None:
                    raise PalmierEditorError(f"Placed clip {cplan.clip_key} not found on track {tplan.track_name}")
                clip_id = str(clip_obj["id"])

                kf_payload = {
                    "clip_id": clip_id,
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
                    action=_apply_clip_motion,
                )
                if not t_kf.already_completed:
                    updated_kf_count += 1

        # Step 6: Create missing markers
        if diff.markers_to_create:
            mhash = hashlib.sha256(json.dumps(diff.markers_to_create, sort_keys=True).encode("utf-8")).hexdigest()
            runner.execute_step(
                run_id=run_id,
                step_name="palmier_create_markers",
                step_kind="editor_mutation",
                input_hash=mhash,
                action=lambda _: self.call_tool(
                    "manage_markers",
                    {"action": "create", "markers": diff.markers_to_create},
                ),
            )
            added_markers_count = len(diff.markers_to_create)

        return {
            "status": "applied",
            "plan_id": plan.plan_id,
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
        """Export the active scratch timeline to a 1080p H.264 MP4 inside scratch and wait for completion."""
        out_resolved = self.policy.resolve_output(output_mp4_path, overwrite=True, create_parents=True)
        if out_resolved.exists():
            out_resolved.unlink()

        enqueue_res = self.call_tool(
            "export_project",
            {
                "mode": "video",
                "codec": "H.264",
                "resolution": "1080p",
                "outputPath": str(out_resolved),
            },
        )
        job_id = enqueue_res.get("jobId") or enqueue_res.get("id")
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            jobs_res = self.call_tool("manage_exports", {"action": "list"})
            jobs = jobs_res.get("exports", jobs_res.get("jobs", []))
            target_job = None
            for j in jobs:
                if job_id and j.get("jobId") == job_id:
                    target_job = j
                    break
                if j.get("outputPath") == str(out_resolved):
                    target_job = j
                    break
            if target_job:
                st = target_job.get("status")
                if st == "completed":
                    return target_job
                if st in ("failed", "canceled", "cancelled"):
                    raise PalmierEditorError(f"Palmier video export failed: {target_job}")
            elif out_resolved.is_file() and out_resolved.stat().st_size > 0:
                return {"status": "completed", "outputPath": str(out_resolved)}
            time.sleep(poll_interval_seconds)

        raise PalmierEditorError(f"Timed out waiting for Palmier export at {out_resolved}")
