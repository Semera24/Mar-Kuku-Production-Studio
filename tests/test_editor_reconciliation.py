"""Tests for PalmierAdapter state-first reconciliation, schema validation, idempotency, and scratch safety guards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mk_studio.config import PathPolicyError, RootPolicy, compute_sha256
from mk_studio.contracts import (
    ClipPlan,
    KeyframeRow,
    MarkerPlan,
    StaticTransformPlan,
    TimelinePlan,
    TrackPlan,
)
from mk_studio.editors.palmier import (
    PalmierAdapter,
    PalmierEditorError,
    PalmierSchemaValidationError,
    ScratchProjectCreationBlockedError,
    validate_palmier_tool_call,
)
from mk_studio.runner import StepRunner
from mk_studio.state import StateStore


class FakePalmierMcpServer:
    """In-memory mock of Palmier Pro's MCP server tools enforcing exact live schemas."""

    def __init__(self, scratch_project_path: Path) -> None:
        self.scratch_project_path = str(scratch_project_path)
        self.width = 1920
        self.height = 1080
        self.fps = 30
        self.media: list[dict[str, Any]] = []
        self.tracks: list[dict[str, Any]] = []
        self.markers: list[dict[str, Any]] = []
        self.exports: list[dict[str, Any]] = []
        self.mutation_calls: list[tuple[str, dict[str, Any]]] = []
        self._next_id = 1

    def _alloc_id(self, prefix: str) -> str:
        val = f"{prefix}-{self._next_id:04d}"
        self._next_id += 1
        return val

    def _reindex_tracks(self) -> None:
        for idx, tr in enumerate(self.tracks):
            tr["index"] = idx

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        validate_palmier_tool_call(name, arguments)

        if name == "manage_project":
            action = arguments.get("action")
            if action == "list":
                return {
                    "projects": [
                        {
                            "id": "proj-scratch-001",
                            "name": Path(self.scratch_project_path).stem,
                            "path": self.scratch_project_path,
                            "isActive": True,
                            "isSessionProject": True,
                        }
                    ]
                }
            if action in ("open", "close"):
                return {"status": "ok", "path": arguments.get("path")}
            raise AssertionError(f"Unexpected manage_project action: {action}")

        if name == "get_media":
            return {"assets": list(self.media), "media": list(self.media)}

        if name == "get_timeline":
            self._reindex_tracks()
            return {
                "id": "tl-scratch-001",
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "totalFrames": 907,
                "projectSettings": {
                    "resolution": {"width": self.width, "height": self.height},
                    "fps": self.fps,
                    "totalFrames": 907,
                },
                "tracks": list(self.tracks),
                "markers": list(self.markers),
            }

        # Record mutations from here on
        self.mutation_calls.append((name, arguments))

        if name == "import_media":
            src = arguments["source"]
            p = str(Path(src["path"]).resolve(strict=False))
            mid = self._alloc_id("media")
            ext = Path(p).suffix.lower()
            mtype = "audio" if ext in (".wav", ".mp3", ".aac", ".m4a") else "image"
            item = {
                "id": mid,
                "name": arguments.get("name") or Path(p).name,
                "path": p,
                "type": mtype,
            }
            self.media.append(item)
            return {"asset": item, "imported": [item]}

        if name == "set_project_settings":
            self.width = int(arguments.get("width", self.width))
            self.height = int(arguments.get("height", self.height))
            self.fps = int(arguments.get("fps", self.fps))
            return {"status": "ok"}

        if name == "manage_tracks":
            for move in arguments.get("reorder", []):
                tid = move.get("trackId")
                cur_idx = move.get("index")
                to_idx = int(move["to"])
                target_tr = next(
                    (
                        t
                        for t in self.tracks
                        if (tid and (t.get("trackId") == tid or t.get("id") == tid))
                        or (cur_idx is not None and int(t.get("index", -1)) == int(cur_idx))
                    ),
                    None,
                )
                if target_tr is not None:
                    self.tracks.remove(target_tr)
                    self.tracks.insert(max(0, min(to_idx, len(self.tracks))), target_tr)
                    self._reindex_tracks()

            for upd in arguments.get("set", []):
                tid = upd.get("trackId")
                cur_idx = upd.get("index")
                target_tr = next(
                    (
                        t
                        for t in self.tracks
                        if (tid and (t.get("trackId") == tid or t.get("id") == tid))
                        or (cur_idx is not None and int(t.get("index", -1)) == int(cur_idx))
                    ),
                    None,
                )
                if target_tr is not None and "name" in upd:
                    target_tr["name"] = upd["name"]

            for rem in arguments.get("remove", []):
                tid = rem.get("trackId") if isinstance(rem, dict) else None
                rem_idx = rem if isinstance(rem, int) else None
                self.tracks = [
                    t
                    for t in self.tracks
                    if not (
                        (tid and (t.get("trackId") == tid or t.get("id") == tid))
                        or (rem_idx is not None and int(t.get("index", -1)) == rem_idx)
                    )
                ]
                self._reindex_tracks()

            return {"tracks": list(self.tracks)}

        if name == "add_clips":
            entries = arguments.get("entries", [])
            created = []
            if entries and all("trackIndex" not in e for e in entries):
                first_mref = entries[0]["mediaRef"]
                m_item = next((m for m in self.media if m["id"] == first_mref), None)
                ttype = "audio" if (m_item and m_item.get("type") == "audio") else "video"
                tid = self._alloc_id("track")
                new_tr: dict[str, Any] = {
                    "id": tid,
                    "trackId": tid,
                    "index": 0,
                    "name": "",
                    "type": ttype,
                    "clips": [],
                }
                if ttype == "video":
                    self.tracks.insert(0, new_tr)
                else:
                    self.tracks.append(new_tr)
                self._reindex_tracks()
                for entry in entries:
                    cid = self._alloc_id("clip")
                    clip = {
                        "id": cid,
                        "mediaRef": entry["mediaRef"],
                        "frames": [int(entry["startFrame"]), int(entry["endFrame"])],
                        "startFrame": int(entry["startFrame"]),
                        "endFrame": int(entry["endFrame"]),
                    }
                    new_tr["clips"].append(clip)
                    created.append(clip)
                return {"clips": created, "trackId": tid}

            for entry in entries:
                t_idx = int(entry["trackIndex"])
                tr = next(t for t in self.tracks if int(t["index"]) == t_idx)
                cid = self._alloc_id("clip")
                clip = {
                    "id": cid,
                    "mediaRef": entry["mediaRef"],
                    "frames": [int(entry["startFrame"]), int(entry["endFrame"])],
                    "startFrame": int(entry["startFrame"]),
                    "endFrame": int(entry["endFrame"]),
                }
                tr["clips"].append(clip)
                created.append(clip)
            return {"clips": created}

        if name == "remove_clips":
            to_remove = set(arguments.get("clipIds", []))
            for tr in self.tracks:
                tr["clips"] = [c for c in tr["clips"] if c["id"] not in to_remove]
            return {"removed": list(to_remove)}

        if name == "set_clip_properties":
            cids = set(arguments.get("clipIds", []))
            for tr in self.tracks:
                for c in tr["clips"]:
                    if c["id"] in cids:
                        c["transform"] = dict(arguments.get("transform") or {})
                        c["fadeInFrames"] = int(arguments.get("fadeInFrames", 0))
                        c["fadeOutFrames"] = int(arguments.get("fadeOutFrames", 0))
            return {"status": "ok"}

        if name == "set_keyframes":
            cid = arguments["clipId"]
            prop = arguments["property"]
            kfs = arguments["keyframes"]
            for tr in self.tracks:
                for c in tr["clips"]:
                    if c["id"] == cid:
                        c.setdefault("keyframes", {})[prop] = [list(r) for r in kfs]
                        if prop == "position":
                            c["positionKeyframes"] = [list(r) for r in kfs]
            return {"status": "ok"}

        if name == "manage_markers":
            if arguments.get("action") == "create":
                mid = self._alloc_id("marker")
                marker = {
                    "id": mid,
                    "markerId": mid,
                    "name": arguments.get("name", ""),
                    "startFrame": int(arguments.get("startFrame", 0)),
                    "durationFrames": int(arguments.get("durationFrames", 0)),
                    "comment": arguments.get("comment", ""),
                    "color": arguments.get("color", "#3B82F6"),
                    "status": arguments.get("status", "open"),
                }
                self.markers.append(marker)
                return {"marker": marker}
            return {"status": "ok"}

        if name == "export_project":
            out_path = Path(arguments["outputPath"])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"fake-mp4-bytes")
            job = {
                "jobId": self._alloc_id("export"),
                "status": "completed",
                "path": str(out_path),
                "outputPath": str(out_path),
            }
            self.exports.append(job)
            return job

        if name == "manage_exports":
            return {"exports": list(self.exports)}

        raise AssertionError(f"Unhandled tool call: {name}")


def _build_synthetic_ch01_plan(tmp_path: Path) -> TimelinePlan:
    assets_dir = tmp_path / "synthetic_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    def _make_asset(filename: str) -> tuple[str, str]:
        p = assets_dir / filename
        p.write_bytes(f"synthetic-bytes-for-{filename}".encode("utf-8"))
        return str(p), compute_sha256(p)

    prop_path, prop_sha = _make_asset("E02_Flower_Red_v001.png")
    mar_paths = [_make_asset(f"Mar_Pose_{i:02d}.png") for i in range(5)]
    kuku_paths = [_make_asset(f"Kuku_Pose_{i:02d}.png") for i in range(4)]
    bg_path, bg_sha = _make_asset("BG_Village_Color_Garden_v001.png")
    aud_path, aud_sha = _make_asset("E02_CH01_Welcome_v002.wav")

    def _video_clip(
        key: str,
        track_name: str,
        asset_id: str,
        spath: str,
        ssha: str,
        start: int,
        end: int,
        cx: float,
        cy: float,
    ) -> ClipPlan:
        dur = end - start
        return ClipPlan(
            clip_key=key,
            track_name=track_name,
            track_type="video",
            asset_id=asset_id,
            source_path=spath,
            source_sha256=ssha,
            start_frame=start,
            end_frame=end,
            duration_frames=dur,
            transform=StaticTransformPlan(centerX=cx, centerY=cy, width=0.35, height=0.62, rotation=0.0),
            position_keyframes=[
                KeyframeRow(frame_offset=0, values=[round(cx - 0.175, 6), round(cy - 0.31, 6)], interpolation="linear"),
                KeyframeRow(frame_offset=max(1, dur - 1), values=[round(cx - 0.17, 6), round(cy - 0.305, 6)], interpolation="linear"),
            ],
            scale_keyframes=[
                KeyframeRow(frame_offset=0, values=[0.35, 0.62], interpolation="linear"),
                KeyframeRow(frame_offset=max(1, dur - 1), values=[0.36, 0.638], interpolation="linear"),
            ],
            rotation_keyframes=[
                KeyframeRow(frame_offset=0, values=[0.0], interpolation="linear"),
                KeyframeRow(frame_offset=max(1, dur - 1), values=[1.2], interpolation="linear"),
            ],
            motion_mode="grounded_bob",
        )

    mar_spans = [(0, 62), (62, 180), (180, 354), (354, 501), (501, 621), (621, 725), (725, 907)]
    kuku_spans = [(0, 127), (127, 354), (354, 501), (501, 621), (621, 725), (725, 907)]

    tracks = [
        TrackPlan(
            track_index=0,
            track_name="Props",
            track_type="video",
            clips=[
                _video_clip("prop_01", "Props", "prop_flower_red", prop_path, prop_sha, 725, 907, 0.81, 0.88)
            ],
        ),
        TrackPlan(
            track_index=1,
            track_name="Mar",
            track_type="video",
            clips=[
                _video_clip(
                    f"mar_{i:02d}",
                    "Mar",
                    f"mar_pose_{i % 5}",
                    mar_paths[i % 5][0],
                    mar_paths[i % 5][1],
                    s,
                    e,
                    0.24,
                    0.62,
                )
                for i, (s, e) in enumerate(mar_spans)
            ],
        ),
        TrackPlan(
            track_index=2,
            track_name="Kuku",
            track_type="video",
            clips=[
                _video_clip(
                    f"kuku_{i:02d}",
                    "Kuku",
                    f"kuku_pose_{i % 4}",
                    kuku_paths[i % 4][0],
                    kuku_paths[i % 4][1],
                    s,
                    e,
                    0.46,
                    0.73,
                )
                for i, (s, e) in enumerate(kuku_spans)
            ],
        ),
        TrackPlan(
            track_index=3,
            track_name="Background",
            track_type="video",
            clips=[
                _video_clip("bg_01", "Background", "bg_color_garden", bg_path, bg_sha, 0, 907, 0.5, 0.5)
            ],
        ),
        TrackPlan(
            track_index=4,
            track_name="Audio_Master",
            track_type="audio",
            clips=[
                ClipPlan(
                    clip_key="audio_01",
                    track_name="Audio_Master",
                    track_type="audio",
                    asset_id="ch01_candidate_wav",
                    source_path=aud_path,
                    source_sha256=aud_sha,
                    start_frame=0,
                    end_frame=907,
                    duration_frames=907,
                    volume_db=0.0,
                    motion_mode="audio_locked",
                )
            ],
        ),
    ]

    markers = [
        MarkerPlan(
            marker_key=f"CUE_{i:02d}",
            name=f"Cue {i:02d}",
            start_frame=i * 80,
            duration_frames=0,
            comment=f"Synthetic cue {i:02d}",
            color="#3B82F6",
        )
        for i in range(10)
    ]

    return TimelinePlan(
        plan_id="EPISODE_002_CH01_synthetic",
        episode_id="EPISODE_002",
        chapter_id="CH01",
        timeline_name="EPISODE_002_CH01_Proof",
        canvas_width=1920,
        canvas_height=1080,
        fps=30,
        total_frames=907,
        audio_duration_seconds=30.22,
        audio_total_samples=1450560,
        video_duration_seconds=30.233333,
        visual_overhang_ms=13.333333,
        visual_overhang_samples=640,
        tracks=tracks,
        markers=markers,
    )


def test_refuses_binding_outside_scratch(tmp_path: Path) -> None:
    policy = RootPolicy(output_root=tmp_path / "scratch")
    adapter = PalmierAdapter(policy, tool_caller=lambda n, a: {})

    with pytest.raises(PathPolicyError, match="outside scratch output root"):
        adapter.bind_scratch_project(
            "/Users/yonassayfu/Documents/Palmier Pro/TestAIProducer.palmier"
        )


def test_blocks_when_scratch_project_does_not_exist(tmp_path: Path) -> None:
    policy = RootPolicy(output_root=tmp_path / "scratch")
    adapter = PalmierAdapter(policy, tool_caller=lambda n, a: {})
    missing_proj = policy.output_root_resolved / "E02_CH01_Technical_Proof_v001.palmier"

    with pytest.raises(ScratchProjectCreationBlockedError, match="Required single owner UI action"):
        adapter.bind_scratch_project(missing_proj)


def test_schema_validation_rejects_invalid_tool_payloads(tmp_path: Path) -> None:
    policy = RootPolicy(output_root=tmp_path / "scratch")
    adapter = PalmierAdapter(policy, tool_caller=lambda n, a: {})

    with pytest.raises(PalmierSchemaValidationError):
        adapter.call_tool("import_media", {"sources": [{"path": "/tmp/foo.png"}]})

    with pytest.raises(PalmierSchemaValidationError):
        adapter.call_tool("manage_tracks", {"action": "add", "name": "Mar", "type": "video"})

    with pytest.raises(PalmierSchemaValidationError):
        adapter.call_tool("manage_markers", {"action": "create", "markers": []})


def test_idempotent_assembly_and_stale_clip_cleanup(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    policy = RootPolicy(output_root=scratch_root)
    scratch_proj = policy.output_root_resolved / "E02_CH01_Technical_Proof_v001.palmier"
    scratch_proj.mkdir(parents=True, exist_ok=True)

    store = StateStore(scratch_root / "state.db")
    runner = StepRunner(store, scratch_root / "locks")
    store.upsert_episode(
        episode_id="E02",
        title="Colors & Comparisons",
        production_root=str(policy.production_root_resolved),
        status="approved",
    )
    store.upsert_chapter(
        episode_id="E02",
        chapter_id="CH01",
        title="Welcome",
        status="ready",
    )
    store.create_run(
        run_id="run-palmier-01",
        episode_id="E02",
        chapter_id="CH01",
        workflow_name="assemble_ch01",
        input_hash="plan-hash-01",
    )

    fake_server = FakePalmierMcpServer(scratch_proj)
    adapter = PalmierAdapter(policy, tool_caller=fake_server.call_tool)
    plan = _build_synthetic_ch01_plan(tmp_path)

    # 1. First apply_plan populates media, tracks, clips, keyframes, and markers
    res1 = adapter.apply_plan(
        plan,
        store=store,
        runner=runner,
        run_id="run-palmier-01",
        scratch_project_path=scratch_proj,
    )
    assert res1["status"] == "applied"
    assert res1["imported_media"] == 12
    assert res1["added_tracks"] == 5
    assert res1["added_clips"] == 16
    assert res1["updated_keyframes"] == 15
    assert res1["added_markers"] == 10

    mutation_count_after_first = len(fake_server.mutation_calls)
    assert mutation_count_after_first > 0

    # 2. Second apply_plan against the populated state is a pure no-op with zero mutations
    res2 = adapter.apply_plan(
        plan,
        store=store,
        runner=runner,
        run_id="run-palmier-01",
        scratch_project_path=scratch_proj,
    )
    assert res2["status"] == "noop"
    assert len(fake_server.mutation_calls) == mutation_count_after_first

    # 3. Inject an unplanned stale clip on Mar and verify reconcile_plan + apply_plan removes it
    v2_track = next(t for t in fake_server.tracks if t["name"] == "Mar")
    v2_track["clips"].append(
        {
            "id": "stale-clip-999",
            "mediaRef": "media-0001",
            "frames": [10, 20],
            "startFrame": 10,
            "endFrame": 20,
        }
    )
    diff = adapter.reconcile_plan(plan, adapter.read_state())
    assert diff.clips_to_remove == ["stale-clip-999"]

    res3 = adapter.apply_plan(
        plan,
        store=store,
        runner=runner,
        run_id="run-palmier-01",
        scratch_project_path=scratch_proj,
    )
    assert res3["status"] == "applied"
    assert all(c["id"] != "stale-clip-999" for c in v2_track["clips"])

    # 4. Corrupt Mar clip 0 keyframes/transform and verify reconcile_plan detects and repairs it
    corrupt_clip = v2_track["clips"][0]
    corrupt_clip["keyframes"]["position"] = [[0, 100.0, 100.0, "linear"]]
    corrupt_clip["keyframes"]["scale"] = []
    corrupt_clip["keyframes"]["rotation"] = []
    corrupt_clip["transform"] = {"centerX": 100.0, "centerY": 100.0}
    corrupt_diff = adapter.reconcile_plan(plan, adapter.read_state())
    assert not corrupt_diff.is_noop
    assert len(corrupt_diff.clips_needing_keyframes) == 1

    res4 = adapter.apply_plan(
        plan,
        store=store,
        runner=runner,
        run_id="run-palmier-01",
        scratch_project_path=scratch_proj,
    )
    assert res4["status"] == "applied"
    assert adapter.reconcile_plan(plan, adapter.read_state()).is_noop

    # 5. Close/reopen and export inside scratch
    reopened = adapter.close_and_reopen(scratch_proj)
    assert len(reopened.tracks) == 5
    out_mp4 = scratch_root / "E02_CH01_Technical_Proof_v001.mp4"
    exp_res = adapter.export_video_mp4(out_mp4, poll_interval_seconds=0.01, timeout_seconds=2.0)
    assert exp_res["status"] == "completed"
    assert out_mp4.is_file()
