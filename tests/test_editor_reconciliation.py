"""Tests for PalmierAdapter state-first reconciliation, idempotency, and scratch safety guards."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mk_studio.config import PathPolicyError, RootPolicy
from mk_studio.contracts import TimelinePlan
from mk_studio.editors.palmier import (
    PalmierAdapter,
    ScratchProjectCreationBlockedError,
)
from mk_studio.runner import StepRunner
from mk_studio.state import StateStore


class FakePalmierMcpServer:
    """In-memory mock of Palmier Pro's MCP server tools for deterministic adapter testing."""

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

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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
            return {"media": list(self.media)}

        if name == "get_timeline":
            return {
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
            added = []
            for src in arguments.get("sources", []):
                p = str(Path(src["path"]).resolve(strict=False))
                mid = self._alloc_id("media")
                item = {"id": mid, "name": Path(p).name, "path": p}
                self.media.append(item)
                added.append(item)
            return {"imported": added}

        if name == "set_project_settings":
            self.width = int(arguments.get("width", self.width))
            self.height = int(arguments.get("height", self.height))
            self.fps = int(arguments.get("fps", self.fps))
            return {"status": "ok"}

        if name == "manage_tracks":
            action = arguments.get("action")
            if action == "add":
                idx = int(arguments.get("atIndex", len(self.tracks)))
                tr = {
                    "id": self._alloc_id("track"),
                    "index": idx,
                    "name": arguments["name"],
                    "type": arguments["type"],
                    "clips": [],
                }
                self.tracks.append(tr)
                self.tracks.sort(key=lambda t: int(t["index"]))
                return {"track": tr}

        if name == "add_clips":
            created = []
            for entry in arguments.get("entries", []):
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
                        c["transform"] = arguments.get("transform")
                        c["fadeInFrames"] = arguments.get("fadeInFrames", 0)
                        c["fadeOutFrames"] = arguments.get("fadeOutFrames", 0)
            return {"status": "ok"}

        if name == "set_keyframes":
            cid = arguments["clipId"]
            prop = arguments["property"]
            kfs = arguments["keyframes"]
            for tr in self.tracks:
                for c in tr["clips"]:
                    if c["id"] == cid:
                        c.setdefault("keyframes", {})[prop] = kfs
                        if prop == "position":
                            c["positionKeyframes"] = kfs
            return {"status": "ok"}

        if name == "manage_markers":
            if arguments.get("action") == "create":
                for m in arguments.get("markers", []):
                    self.markers.append(
                        {
                            "id": self._alloc_id("marker"),
                            "name": m["name"],
                            "startFrame": int(m["startFrame"]),
                            "durationFrames": int(m.get("durationFrames", 0)),
                            "comment": m.get("comment", ""),
                            "color": m.get("color", "blue"),
                        }
                    )
            return {"status": "ok"}

        if name == "export_project":
            out_path = Path(arguments["outputPath"])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"fake-mp4-bytes")
            job = {
                "jobId": self._alloc_id("export"),
                "status": "completed",
                "outputPath": str(out_path),
            }
            self.exports.append(job)
            return job

        if name == "manage_exports":
            return {"exports": list(self.exports)}

        raise AssertionError(f"Unhandled tool call: {name}")


def _load_compiled_ch01_plan() -> TimelinePlan:
    plan_path = Path(
        "/Users/yonassayfu/Projects/mar-kuku-production-studio/scratch/ch01-proof/timeline-plan.json"
    )
    return TimelinePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))


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
    plan = _load_compiled_ch01_plan()

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

    # 4. Close/reopen and export inside scratch
    reopened = adapter.close_and_reopen(scratch_proj)
    assert len(reopened.tracks) == 5
    out_mp4 = scratch_root / "E02_CH01_Technical_Proof_v001.mp4"
    exp_res = adapter.export_video_mp4(out_mp4, poll_interval_seconds=0.01, timeout_seconds=2.0)
    assert exp_res["status"] == "completed"
    assert out_mp4.is_file()
