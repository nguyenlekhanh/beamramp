"""Batch job records, state transitions and persistent batch manifests.

Pure Python + stdlib only (no Qt) so the queue model stays testable. The
runner in ``app/render_queue/runner.py`` wraps these in Qt signals.

Manifest layout: ``output/batches/<batch_id>/manifest.json`` with schema
``crs.batch/1``. All writes are atomic (temp file + os.replace).
"""

from __future__ import annotations

import datetime
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BATCHES_DIR = PROJECT_ROOT / "output" / "batches"

SCHEMA = "crs.batch/1"

STATES = ("pending", "running", "succeeded", "failed", "cancelled")

# legal transitions; anything else is a JobError
TRANSITIONS: Dict[str, tuple] = {
    "pending": ("running", "cancelled"),
    "running": ("succeeded", "failed", "cancelled"),
    "failed": ("pending",),          # retry
    "cancelled": ("pending",),       # requeue after restart or manual retry
    "succeeded": (),                 # immutable unless the user clears it
}

JOB_LOG_TAIL = 120


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def new_batch_id() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"batch_{stamp}_{uuid.uuid4().hex[:6]}"


def new_job_id() -> str:
    return "job_" + uuid.uuid4().hex[:10]


class JobError(ValueError):
    """Illegal job state transition or malformed manifest."""


def _check(cond: Any, msg: str) -> None:
    if not cond:
        raise JobError(msg)


@dataclass
class JobRecord:
    job_id: str
    batch_id: str
    name: str
    template_id: str
    template_name: str
    seed: int
    config: Dict[str, Any]
    preset: str = ""
    status: str = "pending"
    progress: float = 0.0
    output_path: Optional[str] = None
    verify: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    render_log: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id, "batch_id": self.batch_id,
            "name": self.name, "template_id": self.template_id,
            "template_name": self.template_name, "seed": int(self.seed),
            "config": dict(self.config), "preset": self.preset,
            "status": self.status, "progress": round(float(self.progress), 3),
            "output_path": self.output_path, "verify": dict(self.verify),
            "error": self.error, "render_log": list(self.render_log)[-JOB_LOG_TAIL:],
            "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobRecord":
        _check(isinstance(data, dict), "job entry must be an object")
        for key in ("job_id", "batch_id", "name", "template_id", "seed",
                    "config", "status"):
            _check(key in data, f"job entry missing field: {key}")
        status = str(data.get("status") or "pending")
        _check(status in STATES, f"unknown job status: {status!r}")
        return cls(
            job_id=str(data["job_id"]), batch_id=str(data["batch_id"]),
            name=str(data["name"]), template_id=str(data["template_id"]),
            template_name=str(data.get("template_name") or ""),
            seed=int(data["seed"]), config=dict(data["config"]),
            preset=str(data.get("preset") or ""), status=status,
            progress=float(data.get("progress") or 0.0),
            output_path=data.get("output_path"),
            verify=dict(data.get("verify") or {}),
            error=data.get("error"),
            render_log=list(data.get("render_log") or []),
            created_at=str(data.get("created_at") or _now()),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )


def transition(job: JobRecord, new_status: str, *,
               error: Optional[str] = None,
               render_log: Optional[List[str]] = None,
               verify: Optional[Dict[str, Any]] = None) -> None:
    """Apply a validated state change with timestamps."""
    _check(new_status in STATES, f"unknown status {new_status!r}")
    _check(new_status in TRANSITIONS[job.status],
           f"illegal transition {job.status} -> {new_status}")
    job.status = new_status
    if new_status == "running":
        job.started_at = _now()
        job.error = None
        job.render_log = render_log or []
        job.progress = 0.0
    elif new_status in ("succeeded", "failed", "cancelled"):
        job.finished_at = _now()
        job.progress = 1.0 if new_status == "succeeded" else job.progress
        job.error = error if new_status in ("failed", "cancelled") else None
        if render_log is not None:
            job.render_log = render_log[-JOB_LOG_TAIL:]
        if verify is not None:
            job.verify = dict(verify)


def retry(job: JobRecord) -> None:
    """Re-queue a failed or cancelled job."""
    _check(job.status in ("failed", "cancelled"),
           f"only failed/cancelled jobs can be retried (got {job.status})")
    job.output_path = None
    job.verify = {}
    job.error = None
    job.render_log = []
    transition(job, "pending")


class BatchManifest:
    """One batch = one manifest.json with ordered job entries."""

    def __init__(self, batch_id: str, name: str, *,
                 path: Optional[Path] = None,
                 jobs: Optional[List[JobRecord]] = None):
        self.batch_id = batch_id
        self.name = name
        self.path = Path(path) if path else (
            BATCHES_DIR / batch_id / "manifest.json")
        self.jobs: List[JobRecord] = list(jobs or [])

    # ---- serialization -------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "batch_id": self.batch_id,
            "name": self.name,
            "created_at": self.jobs[0].created_at if self.jobs else _now(),
            "jobs": [j.to_dict() for j in self.jobs],
        }

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.path)
        return self.path

    @classmethod
    def load(cls, path: Path) -> "BatchManifest":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        _check(isinstance(doc, dict) and doc.get("schema") == SCHEMA,
               "unsupported batch manifest schema")
        _check(isinstance(doc.get("batch_id"), str), "manifest missing batch_id")
        _check(isinstance(doc.get("jobs"), list), "manifest missing jobs list")
        jobs = [JobRecord.from_dict(j) for j in doc["jobs"]]
        return cls(doc["batch_id"], str(doc.get("name") or doc["batch_id"]),
                   path=Path(path), jobs=jobs)

    # ---- queries -------------------------------------------------------
    def by_status(self, status: str) -> List[JobRecord]:
        return [j for j in self.jobs if j.status == status]

    def next_pending(self) -> Optional[JobRecord]:
        for job in self.jobs:
            if job.status == "pending":
                return job
        return None

    def progress_done(self) -> Tuple[int, int]:
        done = sum(1 for j in self.jobs if j.status in ("succeeded", "failed", "cancelled"))
        return done, len(self.jobs)

    def overall_label(self) -> str:
        done, total = self.progress_done()
        return f"{done}/{total}"

    def get(self, job_id: str) -> Optional[JobRecord]:
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        return None


def load_all_manifests(directory: Optional[Path] = None) -> List[BatchManifest]:
    """Load every saved batch (restart inspection) with recovery rules.

    Recovery (no automatic re-run):
      running|pending  -> pending   (queue stays paused until the user starts)
      succeeded        -> keep succeeded only if the output file still exists
      failed|cancelled -> keep as-is (retryable)
    """
    directory = Path(directory) if directory else BATCHES_DIR
    out: List[BatchManifest] = []
    if not directory.exists():
        return out
    for mf in sorted(directory.glob("*/manifest.json")):
        try:
            batch = BatchManifest.load(mf)
        except (JobError, json.JSONDecodeError, OSError):
            continue  # unreadable manifest stays out of the queue view
        changed = False
        for job in batch.jobs:
            prev = job.status
            if prev == "running":
                job.status = "pending"
                changed = True
            elif prev == "succeeded" and job.output_path \
                    and not Path(job.output_path).exists():
                job.status = "pending"
                changed = True
        if changed:
            batch.save()
        out.append(batch)
    return out


def create_batch(name: str, jobs: List[JobRecord], *,
                 batch_id: Optional[str] = None,
                 directory: Optional[Path] = None) -> BatchManifest:
    batch_id = batch_id or new_batch_id()
    manifest = BatchManifest(batch_id, name, jobs=list(jobs))
    if directory:
        manifest.path = Path(directory) / batch_id / "manifest.json"
    manifest.save()
    return manifest
