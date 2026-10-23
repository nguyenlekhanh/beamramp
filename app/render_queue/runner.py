"""Sequential batch runner (QThread). One Godot render at a time.

Reuses the single-render path (`render_service.run_single_render`) for every
job — no second render implementation. Failed jobs are isolated: the loop
continues with the remaining queue. Cancellation only touches the currently
running render; pending jobs can be cancelled separately from the UI thread.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from ..services import render_service
from . import jobs as jobmod
import threading


class QueueSignals(QObject):
    job_started = Signal(object)       # JobRecord
    job_progress = Signal(object, float)   # JobRecord, 0..1
    job_log = Signal(object, str)      # JobRecord, line
    job_result = Signal(object)        # final JobRecord (any terminal state)
    manifest_changed = Signal()        # anything re-saved
    batch_finished = Signal(object)    # BatchManifest


class BatchRunner(QThread):
    def __init__(self, manifest: jobmod.BatchManifest, parent=None):
        super().__init__(parent)
        self.manifest = manifest
        self.signals = QueueSignals()
        self.cancel_event = threading.Event()   # marks the current render
        self._stop_pending = False              # cancels remaining pending jobs

    # ---- thread-safe API -------------------------------------------------
    def cancel_current(self) -> None:
        """Abort the currently running render (this job becomes 'cancelled')."""
        self.cancel_event.set()

    def cancel_pending(self) -> None:
        """Prevent any further pending jobs from starting."""
        self._stop_pending = True

    # ---- loop -------------------------------------------------------------
    def run(self) -> None:  # noqa: QThread.run
        while True:
            if self._stop_pending or self.isInterruptionRequested():
                self._cancel_all_pending()
                break
            job = self.manifest.next_pending()
            if job is None:
                break
            self._run_one(job)
        self.signals.batch_finished.emit(self.manifest)

    def _cancel_all_pending(self) -> None:
        for job in self.manifest.jobs:
            if job.status == "pending":
                jobmod.transition(job, "cancelled", error="cancelled by user")
        self.manifest.save()
        self.signals.manifest_changed.emit()

    def _run_one(self, job: jobmod.JobRecord) -> None:
        log_lines: list = []

        def log_cb(line: str) -> None:
            text = str(line)
            log_lines.append(text)
            self.signals.job_log.emit(job, text)

        def progress_cb(frac) -> None:
            try:
                val = max(0.0, min(1.0, float(frac)))
            except (TypeError, ValueError):
                return
            job.progress = val
            self.signals.job_progress.emit(job, val)

        # "cancel current" only affects the render in progress:
        # each new job starts with a fresh cancel event
        self.cancel_event.clear()
        jobmod.transition(job, "running")
        self.manifest.save()
        self.signals.manifest_changed.emit()
        self.signals.job_started.emit(job)

        manifest_path = self.manifest.path
        res = None
        try:
            res = render_service.run_single_render(
                dict(job.config), log_cb=log_cb, progress_cb=progress_cb,
                cancel_event=self.cancel_event, overwrite=False)
            verify = {
                "ok": True,
                "width": res.get("width"), "height": res.get("height"),
                "fps": res.get("fps"), "duration": round(float(res.get("duration") or 0), 2),
                "has_audio": bool(res.get("has_audio")),
            }
            job.output_path = res.get("mp4_path")
            jobmod.transition(job, "succeeded", render_log=log_lines, verify=verify)
        except Exception as exc:  # noqa: BLE001 - surfaced per job
            message = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, render_service.godot_service.RenderCancelled) \
                    or "cancel" in str(exc).lower():
                jobmod.transition(job, "cancelled", error="cancelled by user",
                                  render_log=log_lines)
            else:
                jobmod.transition(job, "failed", error=message, render_log=log_lines)
        # drop the per-job working files early; keep the scenario JSON copy
        self.manifest.save()
        self.signals.job_result.emit(job)
        self.signals.manifest_changed.emit()
