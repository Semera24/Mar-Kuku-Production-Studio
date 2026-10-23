"""Autonomous Background Cron Scheduler inspired by Nous Research Hermes Agent.

Supports relative delays, recurring intervals, daily schedules, and skill-attached execution.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


@dataclass
class ScheduledJob:
    job_id: str
    name: str
    schedule: str  # e.g., "daily 09:00", "every 2h", "every 30m"
    prompt: str
    skill_name: str | None = None
    is_active: bool = True
    last_run: str | None = None
    next_run: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StudioScheduler:
    """Manages scheduled recurring production and channel maintenance jobs."""

    def __init__(self, storage_dir: Path | None = None) -> None:
        self.storage_dir = storage_dir or (Path(__file__).resolve().parents[2] / "data" / "cron")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_file = self.storage_dir / "jobs.json"
        self._lock = threading.RLock()
        self._ensure_default_jobs()

    def _ensure_default_jobs(self) -> None:
        if not self.jobs_file.is_file():
            default_jobs = [
                {
                    "job_id": "cron-weekly-full-production",
                    "name": "Weekly Full Episode Auto-Production (16:9)",
                    "schedule": "weekly mon 09:00",
                    "prompt": "Run full automated sequence from script planning to staged broadcast MP4 for active channel.",
                    "skill_name": "mar-kuku-kids",
                    "is_active": True,
                    "last_run": None,
                    "next_run": "Monday at 09:00 AM",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "job_id": "cron-daily-yt-review",
                    "name": "Daily YouTube Comment Review",
                    "schedule": "daily 09:00",
                    "prompt": "Inspect latest YouTube Studio comments, draft on-brand responses, and prepare for owner approval.",
                    "skill_name": "youtube-community-manager",
                    "is_active": True,
                    "last_run": None,
                    "next_run": "Tomorrow at 09:00 AM",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "job_id": "cron-daily-analytics-digest",
                    "name": "Daily YouTube Studio Analytics Digest",
                    "schedule": "daily 20:00",
                    "prompt": "Scan YouTube Studio performance metrics (views, retention, CTR) and push executive card to Telegram.",
                    "skill_name": "youtube-community-manager",
                    "is_active": True,
                    "last_run": None,
                    "next_run": "Today at 08:00 PM",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "job_id": "cron-weekly-tech-research",
                    "name": "Weekly 16:9 Topic Research",
                    "schedule": "weekly mon 10:00",
                    "prompt": "Research trending developments in the active 16:9 niche and draft 3 episode concepts.",
                    "skill_name": "tech-explainer",
                    "is_active": True,
                    "last_run": None,
                    "next_run": "Monday at 10:00 AM",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            ]
            self.jobs_file.write_text(json.dumps(default_jobs, indent=2), encoding="utf-8")

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.jobs_file.is_file():
                return []
            try:
                return json.loads(self.jobs_file.read_text(encoding="utf-8"))
            except Exception:
                return []

    def add_job(
        self,
        name: str,
        schedule: str,
        prompt: str,
        skill_name: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            jobs = self.list_jobs()
            job_id = f"cron-{int(time.time())}"
            new_job = {
                "job_id": job_id,
                "name": name,
                "schedule": schedule,
                "prompt": prompt,
                "skill_name": skill_name,
                "is_active": True,
                "last_run": None,
                "next_run": f"Scheduled ({schedule})",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            jobs.append(new_job)
            self.jobs_file.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
            return new_job

    def toggle_job(self, job_id: str, is_active: bool) -> dict[str, Any] | None:
        with self._lock:
            jobs = self.list_jobs()
            target = None
            for j in jobs:
                if j["job_id"] == job_id:
                    j["is_active"] = is_active
                    target = j
                    break
            if target:
                self.jobs_file.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
            return target

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            jobs = self.list_jobs()
            initial_count = len(jobs)
            jobs = [j for j in jobs if j["job_id"] != job_id]
            if len(jobs) < initial_count:
                self.jobs_file.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
                return True
            return False

    def mark_executed(self, job_id: str) -> None:
        with self._lock:
            jobs = self.list_jobs()
            now_iso = datetime.now(timezone.utc).isoformat()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["last_run"] = now_iso
                    break
            self.jobs_file.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
