"""ColorRush Studio main window (PySide6)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QSpinBox, QVBoxLayout, QWidget, QButtonGroup,
)

from ..services import render_service
from ..services.ffmpeg_service import get_tools
from . import settings_store
from .render_worker import RenderWorker

ASPECTS = [
    ("9:16", "Shorts 9:16"),
    ("16:9", "YouTube 16:9"),
    ("1:1", "Square 1:1"),
]
RESOLUTIONS = [("720p", "720p"), ("1080p", "1080p")]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ColorRush Studio")
        self.resize(1180, 820)
        self.worker: Optional[RenderWorker] = None
        self.last_result: Optional[dict] = None
        self.primary_color: Optional[str] = settings_store.load_settings().get("color")
        self.music_path: Optional[str] = settings_store.load_settings().get("music_path")
        self._preview_w, self._preview_h = 720, 1280
        self._selected_duration_default: Optional[int] = None
        self._selected_count_default: Optional[int] = None

        self._build_ui()
        self._reload_settings()
        self._on_template_selected(self.list_templates.currentRow())

        if get_tools().get("ffmpeg") is None or get_tools().get("ffprobe") is None:
            self._append_log("[setup] WARNING: ffmpeg/ffprobe not found on PATH. "
                             "Install FFmpeg (winget install Gyan.FFmpeg) and restart.")

    # ------------------------------------------------ UI construction
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        top_row = QHBoxLayout()
        root.addLayout(top_row, 1)

        # ---- Scenario library (left)
        lib_group = QGroupBox("Scenario Library")
        lib_v = QVBoxLayout(lib_group)
        self.list_templates = QListWidget()
        for tpl in render_service.TEMPLATES:
            QListWidgetItem(f'{tpl["icon"]} {tpl["name"]}', self.list_templates)
        self.lbl_description = QLabel()
        self.lbl_description.setWordWrap(True)
        self.lbl_description.setFrameStyle(QFrame.StyledPanel)
        self.lbl_description.setMinimumHeight(70)
        self.lbl_description.setAlignment(Qt.AlignTop)
        lib_v.addWidget(self.list_templates, 2)
        lib_v.addWidget(self.lbl_description, 1)
        top_row.addWidget(lib_group, 3)

        # ---- right column: settings groups
        right = QVBoxLayout()
        top_row.addLayout(right, 4)

        veh_group = QGroupBox("Vehicles")
        veh_form = QFormLayout(veh_group)
        self.spin_count = QSpinBox()
        self.spin_count.setRange(1, 12)
        veh_form.addRow("Number of vehicles", self.spin_count)
        self.combo_vtype = QComboBox()
        for vtype in render_service.VEHICLE_TYPES:
            self.combo_vtype.addItem(vtype.replace("_", " ").title(), vtype)
        veh_form.addRow("Vehicle type", self.combo_vtype)
        color_row = QHBoxLayout()
        self.btn_color = QPushButton("Pick primary color")
        color_row.addWidget(self.btn_color)
        self.lbl_color = QLabel("(seeded palette)")
        color_row.addWidget(self.lbl_color, 1)
        color_row.addStretch(1)
        veh_form.addRow("Color", color_row)
        self.spin_seed = QSpinBox()
        self.spin_seed.setRange(0, 10_000_000)
        veh_form.addRow("Seed", self.spin_seed)
        right.addWidget(veh_group)

        video_group = QGroupBox("Video Settings")
        video_form = QFormLayout(video_group)
        asp_row = QHBoxLayout()
        self.radio_916 = QRadioButton("Shorts 9:16")
        self.radio_169 = QRadioButton("YouTube 16:9")
        self.radio_11 = QRadioButton("Square 1:1")
        self.aspect_group = QButtonGroup(self)
        for i, r in enumerate((self.radio_916, self.radio_169, self.radio_11)):
            self.aspect_group.addButton(r, i)
            asp_row.addWidget(r)
        asp_row.addStretch(1)
        video_form.addRow("Aspect", asp_row)
        self.combo_resolution = QComboBox()
        for _key, label in RESOLUTIONS:
            self.combo_resolution.addItem(label, _key)
        video_form.addRow("Resolution", self.combo_resolution)
        self.combo_fps = QComboBox()
        for fps in render_service.SUPPORTED_FPS:
            self.combo_fps.addItem(f"{fps} FPS", fps)
        video_form.addRow("FPS", self.combo_fps)
        self.spin_duration = QSpinBox()
        self.spin_duration.setRange(2, 300)
        self.spin_duration.setSuffix(" s")
        video_form.addRow("Duration", self.spin_duration)
        self.combo_quality = QComboBox()
        for q in render_service.SUPPORTED_QUALITIES:
            self.combo_quality.addItem(q.capitalize(), q)
        video_form.addRow("Quality", self.combo_quality)
        right.addWidget(video_group)

        # ---- audio settings
        audio_group = QGroupBox("Audio")
        audio_form = QFormLayout(audio_group)
        self.check_sfx = QCheckBox()
        self.check_sfx.setChecked(True)
        audio_form.addRow("Sound FX & engine", self.check_sfx)
        self.spin_engine = QSpinBox()
        self.spin_engine.setRange(0, 100)
        self.spin_engine.setValue(50)
        self.spin_engine.setSuffix(" %")
        audio_form.addRow("Engine volume", self.spin_engine)
        self.spin_sfx = QSpinBox()
        self.spin_sfx.setRange(0, 100)
        self.spin_sfx.setValue(80)
        self.spin_sfx.setSuffix(" %")
        audio_form.addRow("SFX volume", self.spin_sfx)
        self.check_music = QCheckBox()
        self.check_music.setChecked(True)
        audio_form.addRow("Background music", self.check_music)
        music_row = QHBoxLayout()
        self.lbl_music = QLabel("(none)")
        self.btn_music = QPushButton("Pick file...")
        self.btn_music.clicked.connect(self._pick_music)
        music_row.addWidget(self.lbl_music, 1)
        music_row.addWidget(self.btn_music)
        audio_form.addRow("Music file", music_row)
        self.spin_music = QSpinBox()
        self.spin_music.setRange(0, 100)
        self.spin_music.setValue(35)
        self.spin_music.setSuffix(" %")
        audio_form.addRow("Music volume", self.spin_music)
        right.addWidget(audio_group)
        right.addStretch(1)

        out_group = QGroupBox("Output")
        out_form = QFormLayout(out_group)
        out_form.addRow("Output folder", self._make_out_dir_row())
        out_form.addRow("File name (auto)", self.lbl_file_hint)
        right.addWidget(out_group, 1)

        # ---- progress area
        prog_group = QGroupBox("Preview & Render")
        prog_v = QVBoxLayout(prog_group)
        btn_row = QHBoxLayout()
        self.btn_preview = QPushButton("Preview")
        self.btn_render = QPushButton("Render")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        btn_row.addWidget(self.btn_preview)
        btn_row.addWidget(self.btn_render)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)
        self.btn_open_folder = QPushButton("Open Output Folder")
        self.btn_open_mp4 = QPushButton("Open Last MP4")
        self.btn_open_mp4.setEnabled(False)
        btn_row.addWidget(self.btn_open_folder)
        btn_row.addWidget(self.btn_open_mp4)
        prog_v.addLayout(btn_row)
        self.lbl_status = QLabel("Ready.")
        self.statusBar().showMessage("Ready.")
        prog_v.addWidget(self.lbl_status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        prog_v.addWidget(self.progress)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("Render log will appear here...")
        prog_v.addWidget(self.log_text, 1)
        root.addWidget(prog_group, 2)

        # wiring
        self.list_templates.currentRowChanged.connect(self._on_template_selected)
        self.radio_916.toggled.connect(self._update_out_name_default)
        self.radio_169.toggled.connect(self._update_out_name_default)
        self.radio_11.toggled.connect(self._update_out_name_default)
        self.combo_resolution.currentIndexChanged.connect(self._update_out_name_default)
        self.btn_color.clicked.connect(self._pick_color)
        self.btn_render.clicked.connect(self._on_render)
        self.btn_preview.clicked.connect(self._on_preview)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_open_folder.clicked.connect(self._open_out_dir)
        self.btn_open_mp4.clicked.connect(self._open_last_mp4)

    def _make_out_dir_row(self):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_outdir = QLabel()
        self.btn_choose_dir = QPushButton("Choose...")
        self.btn_choose_dir.clicked.connect(self._choose_out_dir)
        lay.addWidget(self.lbl_outdir, 2)
        lay.addWidget(self.btn_choose_dir)
        self.lbl_file_hint = QLabel()
        self.lbl_file_hint.setWordWrap(False)
        return row

    # ------------------------------------------------ helpers
    def _reload_settings(self) -> None:
        s = settings_store.load_settings()
        idx = next((i for i, t in enumerate(render_service.TEMPLATES)
                    if t["id"] == s.get("template_id")), 0)
        self.list_templates.blockSignals(True)
        self.list_templates.setCurrentRow(idx)
        self.list_templates.blockSignals(False)
        self.spin_seed.setValue(int(s.get("seed") or 12345))
        aspect = str(s.get("aspect") or "9:16")
        {"9:16": self.radio_916, "16:9": self.radio_169, "1:1": self.radio_11}[aspect].setChecked(True)
        res = str(s.get("resolution") or "1080p")
        self.combo_resolution.setCurrentIndex(max(0, self.combo_resolution.findData(res)))
        fps = int(s.get("fps") or 30)
        self.combo_fps.setCurrentIndex(max(0, self.combo_fps.findData(fps)))
        qual = str(s.get("quality") or "medium")
        self.combo_quality.setCurrentIndex(max(0, self.combo_quality.findData(qual)))
        out_dir = s.get("out_dir") or render_service.DEFAULT_OUT_DIR
        self.lbl_outdir.setText(str(out_dir))
        self.music_path = s.get("music_path")
        self.lbl_music.setText(Path(self.music_path).name if self.music_path else "(none)")
        self._update_color_label()
        self.lbl_file_hint.setText("")  # filled on template selection
        self._selected_duration_default: Optional[int] = None
        self._selected_count_default: Optional[int] = None

    def _current_template(self) -> dict:
        row = self.list_templates.currentRow()
        if 0 <= row < len(render_service.TEMPLATES):
            return render_service.TEMPLATES[row]
        return render_service.TEMPLATES[0]

    def _resolve_aspect(self) -> str:
        if self.radio_169.isChecked():
            return "16:9"
        if self.radio_11.isChecked():
            return "1:1"
        return "9:16"

    def _pick_music(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick background music", self.music_path or "",
            "Audio (*.mp3 *.wav *.flac *.ogg *.m4a *.aac)")
        if path:
            self.music_path = path
            self.lbl_music.setText(Path(path).name)

    def _collect_config(self) -> dict:
        tpl = self._current_template()
        count = self.spin_count.value()
        # user satellites: equal to template default means "template default"
        vehicle_type = self.combo_vtype.currentData() if self.combo_vtype.isEnabled() else None
        if vehicle_type == tpl["vehicle_type"]:
            vehicle_type = None
        duration = self.spin_duration.value()
        if self._selected_duration_default and duration == self._selected_duration_default:
            duration = None
        return {
            "template_id": tpl["id"],
            "vehicle_count": count,
            "vehicle_type": vehicle_type,
            "duration": duration,
            "aspect": self._resolve_aspect(),
            "resolution": self.combo_resolution.currentData(),
            "fps": self.combo_fps.currentData(),
            "quality": self.combo_quality.currentData(),
            "seed": self.spin_seed.value(),
            "color": self.primary_color,
            "out_dir": self.lbl_outdir.text().strip(),
            "file_name": self.lbl_file_hint.text().strip(),
            "sfx_enabled": self.check_sfx.isChecked(),
            "engine_volume": self.spin_engine.value() / 100.0,
            "sfx_volume": self.spin_sfx.value() / 100.0,
            "music_enabled": self.check_music.isChecked(),
            "music_path": self.music_path,
            "music_volume": self.spin_music.value() / 100.0,
        }

    # ------------------------------------------------ slots
    def _on_template_selected(self, row: int) -> None:
        if not 0 <= row < len(render_service.TEMPLATES):
            return
        tpl = render_service.TEMPLATES[row]
        self.lbl_description.setText(
            f"<b>{tpl['icon']} {tpl['name']}</b><br>{tpl['description']}<br>"
            f"<i>Settings: vehicles 1..{tpl.get('max_vehicles', 6)}, "
            f"{'mixed vehicle lineup' if tpl['vehicle_type'] == 'mixed' else tpl['vehicle_type'] + ' cars'}, "
            f"duration ~{tpl['duration']}s</i>")
        max_v = int(tpl.get("max_vehicles", 6))
        self.spin_count.setRange(1, max_v)
        if self.spin_count.value() > max_v:
            self.spin_count.setValue(max_v)
        was_type_enabled = self.combo_vtype.isEnabled()
        mixed = tpl["vehicle_type"] == "mixed"
        self.combo_vtype.setEnabled(not mixed)
        if mixed:
            self.combo_vtype.setToolTip("Mixed lineup template; vehicle type is fixed to the mix.")
        else:
            pos = self.combo_vtype.findData(tpl["vehicle_type"])
            self.combo_vtype.setCurrentIndex(pos if pos >= 0 else 0)
            self.combo_vtype.setToolTip("Choose the vehicle type for all cars.")
        self._selected_duration_default = int(tpl["duration"])
        self.spin_duration.setValue(self._selected_duration_default)
        if self.spin_count.value() == self._selected_count_default:
            self.spin_count.setValue(int(tpl["vehicles"]))
        self._selected_count_default = int(tpl["vehicles"])
        self._update_out_name_default()

    def _update_out_name_default(self) -> None:
        self.lbl_file_hint.setText(render_service.default_output_name(self._collect_config()))

    def _pick_color(self) -> None:
        initial = QColor(self.primary_color or "#e63946")
        color = QColorDialog.getColor(initial, self, "Pick primary vehicle color")
        if color.isValid():
            self.primary_color = color.name()
            self._update_color_label()

    def _update_color_label(self) -> None:
        if self.primary_color:
            self.lbl_color.setText(self.primary_color)
            pal = self.lbl_color.palette()
            pal.setColor(QPalette.WindowText, QColor(self.primary_color))
            self.lbl_color.setPalette(pal)
        else:
            self.lbl_color.setText("(seeded palette)")

    def _choose_out_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose output folder",
                                                  self.lbl_outdir.text().strip() or str(render_service.DEFAULT_OUT_DIR))
        if chosen:
            self.lbl_outdir.setText(chosen)
            self._update_out_name_default()

    def _open_out_dir(self) -> None:
        d = self.lbl_outdir.text().strip()
        if d:
            os.startfile(d)  # noqa: S606 - by design in a GUI shell

    def _open_last_mp4(self) -> None:
        if self.last_result and self.last_result.get("mp4_path"):
            os.startfile(self.last_result["mp4_path"])  # noqa: S606

    def _append_log(self, line: str) -> None:
        self.log_text.appendPlainText(line)

    # ------------------------------------------------ job control
    def _start_worker(self, kind: str, config: dict) -> None:
        if self.worker is not None:
            return
        self.save_settings_now()
        worker = RenderWorker(config, self.lbl_outdir.text().strip(), kind,
                              preview_w=self._preview_w, preview_h=self._preview_h)
        self.worker = worker
        self.btn_render.setEnabled(False)
        self.btn_preview.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.lbl_status.setText("starting...")

        worker.signals.log.connect(self._append_log)
        if kind == "render":
            worker.signals.progress.connect(self._on_progress)
            worker.signals.result.connect(self._on_render_result)
        else:
            worker.signals.result.connect(self._on_preview_result)
        worker.signals.failure.connect(self._on_job_failed)
        worker.signals.status.connect(self._on_status)
        worker.signals.job_finished.connect(self._on_job_finished)
        worker.start()

    def _on_progress(self, frac: float) -> None:
        val = int(max(0, min(1.0, frac)) * 100)
        self.progress.setValue(val)

    def _on_status(self, text: str) -> None:
        self.lbl_status.setText(text)
        self.statusBar().showMessage(text)

    def _on_job_finished(self) -> None:
        if self.worker is not None:
            self.worker.wait()
            self.worker = None
        self._set_state(False)
        self.save_settings_now()

    def _on_render_result(self, res: dict) -> None:
        self.last_result = res
        self.progress.setValue(100)
        self.btn_open_mp4.setEnabled(True)
        path = Path(res["mp4_path"])
        self.lbl_status.setText(
            f"Render OK: {path.name} — {res['width']}x{res['height']} @ {res['fps']} FPS, "
            f"{res['duration']:.1f}s, elapsed {res['elapsed']:.0f}s")

    def _on_preview_result(self, res: dict) -> None:
        self.lbl_status.setText(f"Preview closed (exit code {res.get('preview_exit_code', 0)}).")

    def _on_job_failed(self, message: str) -> None:
        self._append_log(f"ERROR: {message}")
        QMessageBox.critical(self, "Job failed", message)
        self.lbl_status.setText("Job failed — see log.")

    def _set_state(self, busy: bool) -> None:
        self.btn_render.setEnabled(not busy)
        self.btn_preview.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)

    def _on_render(self) -> None:
        if self.worker is not None:
            return
        try:
            render_service.validate_config(self._collect_config())
        except Exception as exc:
            if os.environ.get("CRS_GUI_TESTS") == "1":
                self._append_log(f"Validation error: {exc}")
                return
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return
        out_dir = Path(self.lbl_outdir.text().strip())
        target = out_dir / render_service.default_output_name(self._collect_config())
        overwrite = False
        if target.exists():
            if os.environ.get("CRS_GUI_TESTS") == "1":
                # tests ask for cancel-free flow: never overwrite silently
                self._append_log("Validation error: output already exists (overwrite declined in test mode)")
                return
            reply = QMessageBox.question(
                self, "Overwrite existing video?",
                f"{target.name} already exists. Overwrite it?",
                QMessageBox.Yes | QMessageBox.No)
            overwrite = reply == QMessageBox.Yes
            if not overwrite:
                return
        self._start_worker("render", {**self._collect_config(), "_overwrite": overwrite})

    def _on_preview(self) -> None:
        if self.worker is not None:
            return
        try:
            render_service.validate_config(self._collect_config())
        except Exception as exc:
            if os.environ.get("CRS_GUI_TESTS") == "1":
                self._append_log(f"Validation error: {exc}")
                return
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return
        w, h = render_service.resolve_output_size("9:16", "720p")
        self._preview_w, self._preview_h = w, h
        self._start_worker("preview", self._collect_config())

    def _on_cancel(self) -> None:
        if self.worker is not None:
            self._on_status("cancelling...")
            self.worker.cancel()

    # ------------------------------------------------ persistence
    def save_settings_now(self) -> None:
        values = {
            "template_id": self._current_template()["id"],
            "aspect": self._resolve_aspect(),
            "resolution": self.combo_resolution.currentData(),
            "fps": int(self.combo_fps.currentData() or 30),
            "quality": self.combo_quality.currentData(),
            "seed": int(self.spin_seed.value()),
            "color": self.primary_color,
            "out_dir": self.lbl_outdir.text().strip(),
            "music_path": self.music_path,
        }
        settings_store.save_settings(values)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.worker is not None:
            if os.environ.get("CRS_GUI_TESTS") == "1":
                self.worker.cancel()
                self.worker.wait(10000)
            else:
                reply = QMessageBox.question(
                    self, "Quit ColorRush Studio?",
                    "A render is still running. Cancel it and quit?",
                    QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.No:
                    event.ignore()
                    return
                self.worker.cancel()
                self.worker.wait(15000)
        self.save_settings_now()
        super().closeEvent(event)
