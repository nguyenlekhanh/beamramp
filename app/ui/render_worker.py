"""Background job runner for the UI (runs in its own QThread)."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from ..services import godot_service, render_service


class JobSignals(QObject):
    progress = Signal(float)          # 0..1
    status = Signal(str)
    log = Signal(str)
    result = Signal(object)           # dict on success
    failure = Signal(str)             # error message
    job_finished = Signal()           # always emitted at the end


class RenderWorker(QThread):
    """Runs one job (render or preview) off the UI thread."""

    def __init__(self, config: dict, out_dir: str, job_kind: str = "render",
                 preview_w: int = 720, preview_h: int = 1280, parent=None):
        super().__init__(parent)
        self.signals = JobSignals()
        self.config = dict(config)
        self.out_dir = out_dir
        self.job_kind = job_kind
        self.preview_w = preview_w
        self.preview_h = preview_h
        self.cancel_event = None
        import threading
        self.cancel_event = threading.Event()

    # ---- public (thread-safe) API -------------------------------------
    def cancel(self) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()

    def _log(self, line: str) -> None:
        self.signals.log.emit(str(line))

    def _progress(self, frac) -> None:
        try:
            self.signals.progress.emit(float(frac))
        except Exception:
            pass

    def _status(self, text: str) -> None:
        self.signals.status.emit(text)

    # ---- thread entry ---------------------------------------------
    def run(self) -> None:
        try:
            self.signals.status.emit("preparing scenario...")
            self.signals.progress.emit(0.0)
            if self.job_kind == "render":
                cfg = {k: v for k, v in self.config.items() if not k.startswith("_")}
                overwrite = bool(self.config.get("_overwrite", False))
                res = render_service.run_single_render(
                    {**cfg, "out_dir": self.out_dir},
                    log_cb=self._log,
                    progress_cb=self._progress,
                    cancel_event=self.cancel_event,
                    overwrite=overwrite,
                )
                self.signals.result.emit(res)
            else:
                from pathlib import Path
                from ..services.render_service import build_scenario_from_config
                self.signals.status.emit("opening preview window (close it to continue)...")
                sc = build_scenario_from_config({**self.config})
                preview_path = Path(self.out_dir) / "preview.scenario.json"
                sc.save(preview_path)
                code = godot_service.run_preview(
                    preview_path, self.preview_w, self.preview_h,
                    quality="low", allow_audio=True,
                    on_line=self._log, cancel_event=self.cancel_event,
                )
                self.signals.result.emit({"preview_exit_code": code})
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            kind = "preview" if self.job_kind != "render" else "render"
            if "cancelled by user" in str(exc).lower() or "closed by user" in str(exc).lower():
                self.signals.status.emit(f"{kind} cancelled.")
            else:
                self.signals.failure.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.signals.job_finished.emit()
