"""ColorRush Studio main window (PySide6)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget, QButtonGroup,
)

from ..models.scenario import CAMERA_STYLES
from ..services import (asset_service, ffmpeg_service, montage_service,
                        package_service, preset_service, render_service,
                        thumbnail_service)
from ..services.ffmpeg_service import get_tools
from ..render_queue import jobs as batch_jobs
from ..render_queue.runner import BatchRunner
from ..render_queue.variation import (expand_variations, DEFAULT_SEED_STEP,
                                      MAX_BATCH_JOBS)
from . import settings_store
from .render_worker import RenderWorker, ServiceWorker

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
        self.batch_runner: Optional[BatchRunner] = None
        self.post_worker: Optional[ServiceWorker] = None
        self.asset_worker: Optional[ServiceWorker] = None
        self.current_asset: Optional[str] = None
        self.manifests: List = []
        self.active_manifest = None
        self.presets: List = []
        self.last_result: Optional[dict] = None
        self.post_specs: List = []
        self.montage_result: Optional[dict] = None
        self.thumb_manifest: Optional[dict] = None
        self.last_package: Optional[dict] = None
        self.primary_color: Optional[str] = settings_store.load_settings().get("color")
        self.music_path: Optional[str] = settings_store.load_settings().get("music_path")
        self._preview_w, self._preview_h = 720, 1280
        self._selected_duration_default: Optional[int] = None
        self._selected_count_default: Optional[int] = None

        self._build_ui()
        self._load_presets()
        self._reload_settings()
        self._load_manifests()
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
        self.combo_camera = QComboBox()
        for style in CAMERA_STYLES:
            label = "Cinematic (auto-directed)" if style == "cinematic_auto" \
                else "Single wide shot"
            self.combo_camera.addItem(label, style)
        video_form.addRow("Camera", self.combo_camera)
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

        right.addWidget(self._build_assets_group(), 1)

        right.addWidget(self._build_preset_group())

        batch_group = self._build_batch_group()
        right.addWidget(batch_group, 2)

        right.addWidget(self._build_post_group(), 2)

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

    # ------------------------------------------------ asset import (M3.6A)
    def _build_assets_group(self) -> QGroupBox:
        group = QGroupBox("Assets (GLB preview)")
        v = QVBoxLayout(group)
        self.lbl_asset = QLabel("(no asset loaded)")
        self.lbl_asset.setWordWrap(True)
        self.btn_asset_import = QPushButton("Import GLB...")
        self.btn_asset_report = QPushButton("Asset Report")
        self.btn_asset_report.setEnabled(self.current_asset is not None)
        self.btn_asset_open = QPushButton("Preview Window")
        self.btn_asset_open.setEnabled(self.current_asset is not None)
        v.addWidget(self.lbl_asset)
        row = QHBoxLayout()
        row.addWidget(self.btn_asset_import)
        row.addWidget(self.btn_asset_report)
        v.addLayout(row)
        v.addWidget(self.btn_asset_open)
        self.btn_asset_import.clicked.connect(self._on_import_asset)
        self.btn_asset_report.clicked.connect(self._on_asset_report)
        self.btn_asset_open.clicked.connect(self._on_asset_preview)
        return group

    def _refresh_asset_buttons(self) -> None:
        busy = self.asset_worker is not None
        has_asset = self.current_asset is not None
        self.btn_asset_import.setEnabled(not busy)
        self.btn_asset_report.setEnabled(not busy and has_asset)
        self.btn_asset_open.setEnabled(not busy and has_asset)

    def _start_asset_worker(self, fn, args=(), kwargs=None,
                            done_slot=None, fail_slot=None) -> None:
        if self.asset_worker is not None:
            return
        worker = ServiceWorker(fn, args=args, kwargs=kwargs)
        self.asset_worker = worker
        worker.signals.log.connect(self._append_log)
        worker.signals.result.connect(done_slot)
        worker.signals.failure.connect(fail_slot)
        worker.signals.job_finished.connect(
            lambda: (worker.wait(), self._asset_finished()))
        self._refresh_asset_buttons()
        worker.start()

    def _asset_finished(self) -> None:
        self.asset_worker = None
        self._refresh_asset_buttons()

    def _on_import_asset(self) -> None:
        if self.asset_worker is not None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a GLB model",
            str(asset_service.USER_ASSETS_DIR if
                asset_service.USER_ASSETS_DIR.exists() else ""),
            "GLB models (*.glb);;All files (*.*)")
        if not path:
            return
        self._start_asset_worker(
            asset_service.prepare_asset, (path,),
            kwargs={"log_cb": self._append_log},
            done_slot=self._on_asset_imported,
            fail_slot=self._on_asset_failed)

    def _on_asset_imported(self, res: dict) -> None:
        self.current_asset = res["copied"]
        self.lbl_asset.setText(Path(res["copied"]).name)
        self.lbl_asset.setToolTip(res["copied"])
        self._refresh_asset_buttons()
        self._on_status("asset imported: " + Path(res["copied"]).name)
        self._append_log("[asset] source kept untouched: " + res["source"])

    def _on_asset_report(self) -> None:
        if self.asset_worker is not None or not self.current_asset:
            return
        self._start_asset_worker(
            asset_service.generate_report, (self.current_asset,),
            kwargs={"log_cb": self._append_log},
            done_slot=self._on_asset_report_done,
            fail_slot=self._on_asset_failed)

    def _on_asset_report_done(self, doc: dict) -> None:
        verdict = doc.get("verdict")
        path = doc.get("python_report_path", "")
        self._on_status(f"asset report: {verdict} ({doc.get('python_report_path', '')})")
        self._append_log("[asset] report verdict: " + str(verdict))
        for w in doc.get("warnings") or []:
            self._append_log("[asset] warning: " + str(w))
        self._append_log("[asset] report saved: " + path)
        if verdict == "load_failed":
            QMessageBox.warning(
                self, "Asset report",
                "The asset FAILED to load:\n" + str(doc.get("error", ""))
                + "\n\nSee docs/asset_import.md for troubleshooting.")
        else:
            QMessageBox.information(
                self, "Asset report",
                f"Verdict: {verdict}\n{len(doc.get('warnings') or [])} warning(s)\n"
                "Loading proves visual instantiation only - it does NOT verify "
                "production readiness, wheel placement, or physics/collisions.\n\n"
                + path)

    def _on_asset_preview(self) -> None:
        if self.asset_worker is not None or not self.current_asset:
            return
        self._start_asset_worker(
            asset_service.open_preview, (self.current_asset,),
            kwargs={"scale": 1.0, "pitch_deg": 0.0, "yaw_deg": 0.0,
                    "log_cb": self._append_log},
            done_slot=self._on_asset_preview_done,
            fail_slot=self._on_asset_failed)

    def _on_asset_preview_done(self, res: dict) -> None:
        self._on_status("preview window opened "
                        f"({Path(res['copied_asset_path']).name}) - close it manually")

    def _on_asset_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Asset action failed", message)
        self._append_log("[asset] error: " + message)
        self._on_status("asset action failed - see log.")

    # ------------------------------------------------ render presets
    def _build_preset_group(self) -> QGroupBox:
        group = QGroupBox("Render Presets")
        v = QVBoxLayout(group)
        row = QHBoxLayout()
        self.combo_preset = QComboBox()
        row.addWidget(self.combo_preset, 3)
        self.btn_preset_apply = QPushButton("Apply")
        self.btn_preset_save = QPushButton("Save...")
        self.btn_preset_del = QPushButton("Delete")
        row.addWidget(self.btn_preset_apply)
        row.addWidget(self.btn_preset_save)
        row.addWidget(self.btn_preset_del)
        v.addLayout(row)
        self.lbl_preset_info = QLabel()
        self.lbl_preset_info.setWordWrap(True)
        v.addWidget(self.lbl_preset_info)
        self.btn_preset_apply.clicked.connect(self._apply_preset)
        self.btn_preset_save.clicked.connect(self._save_preset)
        self.btn_preset_del.clicked.connect(self._delete_preset)
        return group

    def _load_presets(self) -> None:
        try:
            self.presets = preset_service.load_presets()
        except preset_service.PresetError as exc:
            self.presets = []
            self._note(str(exc))
        self.combo_preset.blockSignals(True)
        self.combo_preset.clear()
        for p in self.presets:
            self.combo_preset.addItem(p["name"])
        self.combo_preset.blockSignals(False)
        self._show_preset_info()

    def _show_preset_info(self) -> None:
        idx = self.combo_preset.currentIndex()
        if not 0 <= idx < len(self.presets):
            self.lbl_preset_info.setText("")
            return
        p = self.presets[idx]
        keys = [k for k in preset_service.PRESET_KEYS
                if p.get(k) is not None and k not in ("out_dir",)]
        text = ", ".join(f"{k.replace('__', ' ')}={p[k]}" for k in keys)
        self.lbl_preset_info.setText("" + text[:180])

    def _apply_preset(self) -> None:
        idx = self.combo_preset.currentIndex()
        if not 0 <= idx < len(self.presets):
            return
        preset = self.presets[idx]
        cfg = preset_service.apply_preset(preset, self._collect_config())
        self._apply_config_to_controls(cfg)
        self._note("preset '{}' applied".format(preset["name"]))

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if preset_service.find_preset(self.presets, name):
            if os.environ.get("CRS_GUI_TESTS") == "1":
                self._append_log(f"preset '{name}' already exists (overwrite declined in test mode)")
                return
            reply = QMessageBox.question(
                self, "Overwrite preset?", f"'{name}' already exists. Overwrite it?",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            self.presets = [p for p in self.presets
                            if p["name"].lower() != name.lower()]
        try:
            preset = preset_service.new_preset_from_config(name, self._collect_config())
            preset_service.save_presets(self.presets + [preset])
            self._load_presets()
            pos = self.combo_preset.findText(name)
            if pos >= 0:
                self.combo_preset.setCurrentIndex(pos)
            self._note(f"preset '{name}' saved")
        except preset_service.PresetError as exc:
            self._append_log(f"preset error: {exc}")

    def _delete_preset(self) -> None:
        idx = self.combo_preset.currentIndex()
        if not 0 <= idx < len(self.presets):
            return
        name = self.presets[idx]["name"]
        self.presets.pop(idx)
        try:
            preset_service.save_presets(self.presets)
            self._load_presets()
            self._note(f"preset '{name}' deleted")
        except preset_service.PresetError as exc:
            self._append_log(str(exc))
            self._load_presets()

    def _apply_config_to_controls(self, cfg: dict) -> None:
        aspect = str(cfg.get("aspect") or self._resolve_aspect())
        {"9:16": self.radio_916, "16:9": self.radio_169,
         "1:1": self.radio_11}[aspect].setChecked(True)
        for combo, key in ((self.combo_resolution, "resolution"),
                           (self.combo_fps, "fps"),
                           (self.combo_quality, "quality"),
                           (self.combo_camera, "camera_style")):
            idx = combo.findData(cfg.get(key))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        if cfg.get("duration"):
            self.spin_duration.setValue(int(cfg["duration"]))
        if cfg.get("out_dir"):
            self.lbl_outdir.setText(str(cfg["out_dir"]).strip())
        self.check_sfx.setChecked(bool(cfg.get("sfx_enabled", True)))
        self.check_music.setChecked(bool(cfg.get("music_enabled", True)))
        for spin, key in ((self.spin_engine, "engine_volume"),
                          (self.spin_sfx, "sfx_volume"),
                          (self.spin_music, "music_volume")):
            if cfg.get(key) is not None:
                spin.setValue(int(float(cfg[key]) * 100))
        if cfg.get("music_path"):
            self.music_path = str(cfg["music_path"])
            self.lbl_music.setText(Path(self.music_path).name)
        self._update_out_name_default()

    # ------------------------------------------------ batch queue
    def _build_batch_group(self) -> QGroupBox:
        group = QGroupBox("Batch Queue")
        v = QVBoxLayout(group)
        row = QHBoxLayout()
        self.spin_variations = QSpinBox()
        self.spin_variations.setRange(1, MAX_BATCH_JOBS)
        self.spin_variations.setValue(3)
        row.addWidget(QLabel("Variations"))
        row.addWidget(self.spin_variations)
        self.spin_seed_step = QSpinBox()
        self.spin_seed_step.setRange(1, 1_000_000)
        self.spin_seed_step.setValue(DEFAULT_SEED_STEP)
        row.addWidget(QLabel("Seed step"))
        row.addWidget(self.spin_seed_step)
        self.check_all_templates = QCheckBox()
        row.addWidget(QLabel("All templates"))
        row.addWidget(self.check_all_templates)
        row.addStretch(1)
        v.addLayout(row)

        self.lst_batch = QListWidget()
        v.addWidget(self.lst_batch, 3)

        btns = QHBoxLayout()
        self.btn_batch_add = QPushButton("Create Batch")
        self.btn_batch_start = QPushButton("Start Queue")
        self.btn_batch_start.setEnabled(False)
        self.btn_job_cancel = QPushButton("Cancel Job")
        self.btn_pending_cancel = QPushButton("Cancel Pending")
        self.btn_batch_retry = QPushButton("Retry Failed")
        for b, slot in ((self.btn_batch_add, self._on_add_batch),
                        (self.btn_batch_start, self._on_start_queue),
                        (self.btn_job_cancel, self._on_cancel_current_job),
                        (self.btn_pending_cancel, self._on_cancel_pending_jobs),
                        (self.btn_batch_retry, self._on_retry_failed)):
            btns.addWidget(b)
            b.clicked.connect(slot)
        v.addLayout(btns)

        btns2 = QHBoxLayout()
        self.btn_open_batch_folder = QPushButton("Open Output Folder")
        self.btn_open_manifest = QPushButton("Open Manifest")
        self.btn_open_manifest.setEnabled(False)
        btns2.addWidget(self.btn_open_batch_folder)
        btns2.addWidget(self.btn_open_manifest)
        btns2.addStretch(1)
        v.addLayout(btns2)

        self.lbl_batch_progress = QLabel("idle")
        v.addWidget(self.lbl_batch_progress)
        self.prog_job = QProgressBar()
        self.prog_batch = QProgressBar()
        v.addWidget(self.prog_job)
        v.addWidget(self.prog_batch)

        self.btn_open_batch_folder.clicked.connect(self._open_batch_folder)
        self.btn_open_manifest.clicked.connect(self._open_manifest)
        self.lst_batch.currentRowChanged.connect(self._on_batch_selected)
        return group

    # ------------------------------------------------ post production
    MONTAGES_DIR = Path(__file__).resolve().parents[2] / "output" / "montages"

    def _build_post_group(self) -> QGroupBox:
        group = QGroupBox("Post Production (Montage / Thumbnail / Package)")
        v = QVBoxLayout(group)

        src_row = QHBoxLayout()
        self.combo_post_batch = QComboBox()
        src_row.addWidget(QLabel("Batch"))
        src_row.addWidget(self.combo_post_batch, 3)
        self.btn_post_load = QPushButton("Load Completed Clips")
        src_row.addWidget(self.btn_post_load)
        v.addLayout(src_row)

        self.lst_clips = QListWidget()
        self.lst_clips.setMinimumHeight(80)
        v.addWidget(self.lst_clips, 3)

        arr_row = QHBoxLayout()
        self.btn_clip_up = QPushButton("Up")
        self.btn_clip_down = QPushButton("Down")
        self.btn_clip_remove = QPushButton("Remove")
        for b, slot in ((self.btn_clip_up, self._on_clip_up),
                        (self.btn_clip_down, self._on_clip_down),
                        (self.btn_clip_remove, self._on_clip_remove)):
            arr_row.addWidget(b)
            b.clicked.connect(slot)
        arr_row.addStretch(1)
        v.addLayout(arr_row)

        form = QFormLayout()
        self.combo_m_aspect = QComboBox()
        for key, label in ASPECTS:
            self.combo_m_aspect.addItem(label, key)
        self.combo_m_aspect.addItem("Portrait 4:5", "4:5")
        form.addRow("Aspect", self.combo_m_aspect)
        self.combo_m_res = QComboBox()
        for key, label in RESOLUTIONS:
            self.combo_m_res.addItem(label, key)
        form.addRow("Resolution", self.combo_m_res)
        self.combo_m_fps = QComboBox()
        for fps in (24, 30, 60):
            self.combo_m_fps.addItem(f"{fps} FPS", fps)
        form.addRow("FPS", self.combo_m_fps)
        self.combo_m_trans = QComboBox()
        self.combo_m_trans.addItem("Hard cut", "cut")
        self.combo_m_trans.addItem("Cross fade", "fade")
        self.combo_m_trans.currentIndexChanged.connect(self._update_fade_enable)
        form.addRow("Transition", self.combo_m_trans)
        self.spin_m_fade = QSpinBox()
        self.spin_m_fade.setRange(20, 200)     # tenths of a second
        self.spin_m_fade.setValue(50)
        self.spin_m_fade.setSuffix(" ms")
        form.addRow("Fade length", self.spin_m_fade)
        self.combo_m_fit = QComboBox()
        self.combo_m_fit.addItem("Pad (letterbox, no distortion)", "pad")
        self.combo_m_fit.addItem("Crop (center, fills frame)", "crop")
        form.addRow("Fit policy", self.combo_m_fit)
        self.combo_m_quality = QComboBox()
        for q in ("low", "medium", "high"):
            self.combo_m_quality.addItem(q.capitalize(), q)
        form.addRow("Quality", self.combo_m_quality)
        v.addLayout(form)

        music_row = QHBoxLayout()
        self.m_lbl_m_music = QLabel("(none)")
        self.btn_m_music = QPushButton("Music (optional)...")
        music_row.addWidget(self.m_lbl_m_music, 1)
        music_row.addWidget(self.btn_m_music)
        v.addLayout(music_row)
        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("Music volume"))
        self.spin_m_music_vol = QSpinBox()
        self.spin_m_music_vol.setRange(0, 100)
        self.spin_m_music_vol.setValue(35)
        self.spin_m_music_vol.setSuffix(" %")
        vol_row.addWidget(self.spin_m_music_vol)
        vol_row.addStretch(1)
        v.addLayout(vol_row)

        self.lbl_m_estimate = QLabel("Est. duration: -")
        self.lbl_m_estimate.setWordWrap(True)
        v.addWidget(self.lbl_m_estimate)

        self.btn_m_build = QPushButton("Build Montage")
        self.btn_m_build.clicked.connect(self._on_build_montage)
        v.addWidget(self.btn_m_build)

        thumb_row = QHBoxLayout()
        thumb_row.addWidget(QLabel("Thumbnails"))
        self.spin_thumbs = QSpinBox()
        self.spin_thumbs.setRange(1, 12)
        self.spin_thumbs.setValue(6)
        thumb_row.addWidget(self.spin_thumbs)
        self.btn_thumbs_gen = QPushButton("Generate Candidates")
        self.btn_thumbs_gen.clicked.connect(self._on_gen_thumbs)
        thumb_row.addWidget(self.btn_thumbs_gen)
        thumb_row.addStretch(1)
        v.addLayout(thumb_row)
        self.lst_thumbs = QListWidget()
        self.lst_thumbs.setMinimumHeight(84)
        self.lst_thumbs.setIconSize(QSize(96, 54))
        self.lst_thumbs.setViewMode(QListWidget.IconMode)
        v.addWidget(self.lst_thumbs, 2)
        self.btn_thumb_save = QPushButton("Save Selected Thumbnail")
        self.btn_thumb_save.clicked.connect(self._on_save_thumb)
        self.btn_thumb_save.setEnabled(False)
        v.addWidget(self.btn_thumb_save)

        self.ed_p_title = QLineEdit()
        v.addWidget(self.ed_p_title)
        self.ed_p_desc = QPlainTextEdit()
        self.ed_p_desc.setPlaceholderText("Video description (your words...")
        self.ed_p_desc.setMaximumHeight(58)
        v.addWidget(self.ed_p_desc)
        self.ed_p_tags = QLineEdit()
        self.ed_p_tags.setPlaceholderText("tags, comma separated")
        v.addWidget(self.ed_p_tags)
        pkg_row = QHBoxLayout()
        self.btn_pkg_export = QPushButton("Export Package")
        self.btn_pkg_open = QPushButton("Open Package Folder")
        self.btn_pkg_open.setEnabled(False)
        for b, slot in ((self.btn_pkg_export, self._on_export_package),
                        (self.btn_pkg_open, self._on_open_package)):
            pkg_row.addWidget(b)
            b.clicked.connect(slot)
        pkg_row.addStretch(1)
        v.addLayout(pkg_row)

        self.btn_post_load.clicked.connect(self._on_post_load_clips)
        self.btn_m_music.clicked.connect(self._on_pick_m_music)
        for w in (self.combo_m_aspect, self.combo_m_res, self.combo_m_fps,
                  self.combo_m_fit, self.combo_m_quality,
                  self.combo_m_trans):
            w.currentIndexChanged.connect(self._update_m_estimate)
        self.spin_m_fade.valueChanged.connect(self._update_m_estimate)
        self.spin_m_music_vol.valueChanged.connect(self._update_m_estimate)
        self.lst_clips.itemChanged.connect(self._update_m_estimate)
        self.lst_thumbs.currentRowChanged.connect(self._on_thumb_selected)
        self.ed_p_title.textChanged.connect(self._refresh_post_buttons)
        self._update_fade_enable()
        self._update_m_estimate()
        return group

    def _on_pick_m_music(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose music file", "",
            "Audio files (*.wav *.mp3 *.m4a *.flac *.ogg);;All files (*.*)")
        if path:
            self.m_lbl_m_music.setText(Path(path).name)
            self.m_lbl_m_music.setToolTip(path)

    def _m_music_path(self) -> Optional[str]:
        tip = self.m_lbl_m_music.toolTip()
        return tip if tip and Path(tip).exists() else None

    def _m_config(self) -> "montage_service.MontageConfig":
        music = self._m_music_path()
        return montage_service.MontageConfig(
            aspect=self.combo_m_aspect.currentData(),
            resolution=self.combo_m_res.currentData(),
            fps=int(self.combo_m_fps.currentData() or 30),
            quality=self.combo_m_quality.currentData(),
            transition=self.combo_m_trans.currentData(),
            fade_duration=self.spin_m_fade.value() / 100.0,
            fit_policy=self.combo_m_fit.currentData(),
            music_path=music,
            music_volume=self.spin_m_music_vol.value() / 100.0)

    def _checked_specs(self) -> List:
        out = []
        for row in range(self.lst_clips.count()):
            item = self.lst_clips.item(row)
            if item.checkState() == Qt.Checked:
                out.append(self.post_specs[int(item.data(Qt.UserRole))])
        return out

    def _refresh_post_buttons(self) -> None:
        busy = self.worker is not None or self.batch_runner is not None \
            or self.post_worker is not None
        specs_ok = bool(self._checked_specs())
        self.btn_post_load.setEnabled(not busy)
        self.btn_m_build.setEnabled(not busy and specs_ok)
        self.btn_thumbs_gen.setEnabled(
            not busy and self.montage_result is not None)
        self.btn_thumb_save.setEnabled(
            not busy and self.thumb_manifest is not None
            and self.lst_thumbs.currentRow() >= 0)
        self.btn_pkg_export.setEnabled(
            not busy and self.montage_result is not None
            and bool(self.ed_p_title.text().strip()))

    def _update_fade_enable(self) -> None:
        self.spin_m_fade.setEnabled(
            self.combo_m_trans.currentData() == "fade")
        self._update_m_estimate()

    def _update_m_estimate(self, *_args) -> None:
        specs = self._checked_specs()
        try:
            cfg = self._m_config()
        except montage_service.MontageError as exc:
            self.lbl_m_estimate.setText(f"Est. duration error: {exc}")
            return
        if not specs:
            self.lbl_m_estimate.setText("Est. duration: - (0 clips)")
            return
        try:
            mont_est = montage_service.estimate_duration(specs, cfg)
        except (montage_service.MontageError,
                montage_service.MontageInputError) as exc:
            self.lbl_m_estimate.setText(f"Est. duration error: {exc}")
            return
        self.lbl_m_estimate.setText(
            f"Est. duration: {mont_est:.1f} s ({len(specs)} clips, "
            f"music {'ON' if self._m_music_path() else 'OFF'})")

    def _on_post_load_clips(self) -> None:
        if self.worker is not None or self.batch_runner is not None \
                or self.post_worker is not None:
            return
        m = self.active_manifest
        if m is None:
            idx = self.combo_post_batch.currentIndex()
            if 0 <= idx < len(self.manifests):
                m = self.manifests[idx]
        if m is None:
            self._note("no batch selected (create one first)")
            return
        try:
            specs = montage_service.clips_from_manifest(m, recheck=False)
        except montage_service.MontageInputError as exc:
            self._append_log(f"montage error: {exc}")
            QMessageBox.warning(self, "No usable clips", str(exc))
            return
        if not specs:
            QMessageBox.warning(self, "No usable clips",
                                "This batch has no succeeded jobs.")
            return
        self.post_specs = specs
        self.lst_clips.blockSignals(True)
        self.lst_clips.clear()
        for i, spec in enumerate(specs):
            src = spec.source or {}
            label = (f"{spec.label or src.get('template_name', 'clip')} "
                     f"(s{src.get('seed', '?')}, {spec.duration:.1f}s"
                     f"{', no audio' if not spec.has_audio else ''})")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, i)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.lst_clips.addItem(item)
        self.lst_clips.blockSignals(False)
        self._note(f"loaded {len(specs)} completed clips from {m.name}")
        self._update_m_estimate()

    def _move_item(self, delta: int) -> None:
        row = self.lst_clips.currentRow()
        target = row + delta
        if not 0 <= row < self.lst_clips.count() or \
                not 0 <= target < self.lst_clips.count():
            return
        self.lst_clips.blockSignals(True)
        item = self.lst_clips.takeItem(row)
        self.lst_clips.insertItem(target, item)
        self.lst_clips.setCurrentRow(target)
        self.lst_clips.blockSignals(False)
        self._update_m_estimate()

    def _on_clip_up(self) -> None:
        self._move_item(-1)

    def _on_clip_down(self) -> None:
        self._move_item(+1)

    def _on_clip_remove(self) -> None:
        row = self.lst_clips.currentRow()
        if row < 0:
            return
        self.lst_clips.takeItem(row)
        self._update_m_estimate()

    def _start_post_worker(self, fn, args=(), kwargs=None,
                           done_slot=None, fail_slot=None) -> None:
        if self.post_worker is not None:
            return
        worker = ServiceWorker(fn, args=args, kwargs=kwargs)
        self.post_worker = worker
        worker.signals.log.connect(self._append_log)
        worker.signals.result.connect(done_slot)
        worker.signals.failure.connect(fail_slot)
        worker.signals.job_finished.connect(
            lambda: (worker.wait(), self._post_finished()))
        self._refresh_post_buttons()
        worker.start()

    def _post_finished(self) -> None:
        self.post_worker = None
        self._refresh_post_buttons()
        self._update_m_estimate()

    def _on_build_montage(self) -> None:
        if self.post_worker is not None:
            return
        specs = self._checked_specs()
        if not specs:
            return
        try:
            cfg = self._m_config()
        except montage_service.MontageError as exc:
            self._append_log(f"montage error: {exc}")
            return
        args = (specs, cfg)
        self._start_post_worker(
            montage_service.build_montage, args,
            kwargs={"out_dir": self.MONTAGES_DIR,
                    "name": "", "log_cb": self._append_log,
                    "progress_cb": lambda f: self._on_status(
                        f"montage {int(f*100)}%")},
            done_slot=self._on_montage_done,
            fail_slot=self._on_montage_failed)

    def _on_montage_done(self, res: dict) -> None:
        self.montage_result = res
        self.thumb_manifest = None
        self.lst_thumbs.clear()
        self.btn_thumb_save.setEnabled(False)
        self.btn_pkg_open.setEnabled(True)
        self.btn_pkg_open.setToolTip(str(Path(res["out_path"]).parent))
        self._on_status(f"montage OK: {Path(res['out_path']).name} "
                        f"({res['measured_duration']:.1f}s, RMS "
                        f"{res['audio_rms_db']} dB)")
        self._note("montage built: " + res["out_path"])

    def _on_montage_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Montage failed", message)
        self._append_log(f"montage error: {message}")
        self._on_status("montage failed — see log.")

    def _on_gen_thumbs(self) -> None:
        if self.post_worker is not None or self.montage_result is None:
            return
        out_dir = Path(self.montage_result["out_path"]).parent / "thumbs"
        self._start_post_worker(
            thumbnail_service.generate_candidates,
            (Path(self.montage_result["out_path"]),),
            kwargs={"count": self.spin_thumbs.value(), "out_dir": out_dir,
                    "log_cb": self._append_log},
            done_slot=self._on_thumbs_done,
            fail_slot=self._on_thumbs_failed)

    def _on_thumbs_done(self, man: dict) -> None:
        self.thumb_manifest = man
        self.lst_thumbs.clear()
        self.btn_thumb_save.setEnabled(True)
        for cand in man.get("candidates", []):
            icon = QIcon(str(cand["path"]))
            item = QListWidgetItem(
                icon, f"t={cand['timestamp']:.1f}s  bright={cand['brightness']:.0f}"
                f"  motion={cand['motion']:.2f}  rank={cand['rank']:.1f}")
            item.setData(Qt.UserRole, cand)
            self.lst_thumbs.addItem(item)
        self._note(f"thumbnail candidates: {man.get('manifest_path', '')}")

    def _on_thumbs_failed(self, message: str) -> None:
        self._append_log(f"thumbnail error: {message}")
        QMessageBox.critical(self, "Thumbnails failed", message)

    def _on_thumb_selected(self, row: int) -> None:
        self.btn_thumb_save.setEnabled(
            self.thumb_manifest is not None and row >= 0)

    def _selected_thumb_path(self) -> Optional[Path]:
        row = self.lst_thumbs.currentRow()
        if row < 0 or not self.thumb_manifest:
            return None
        cands = self.thumb_manifest.get("candidates") or []
        if 0 <= row < len(cands):
            return Path(cands[row]["path"])
        return None

    def _on_save_thumb(self) -> None:
        src = self._selected_thumb_path()
        if src is None or self.montage_result is None:
            return
        dest_dir = Path(self.montage_result["out_path"]).parent
        try:
            saved = thumbnail_service.save_selected(src, dest_dir)
        except thumbnail_service.ThumbnailError as exc:
            QMessageBox.critical(self, "Thumbnail save failed", str(exc))
            return
        self._note(f"thumbnail saved: {saved}")

    def _on_export_package(self) -> None:
        if self.post_worker is not None or self.montage_result is None:
            return
        title = self.ed_p_title.text().strip()
        if not title:
            QMessageBox.warning(self, "Title required",
                                "Type a video title before exporting.")
            return
        tags = [t.strip() for t in self.ed_p_tags.text().split(",") if t.strip()]
        mtree_args = dict(
            video=Path(self.montage_result["out_path"]),
            title=title,
            description=self.ed_p_desc.toPlainText().strip(),
            tags=tags,
            montage_manifest=Path(self.montage_result["manifest_path"]),
            source_jobs=[
                {"job_id": c.get("source", {}).get("job_id", ""),
                 "seed": c.get("source", {}).get("seed"),
                 "template": c.get("source", {}).get("template_id", "")}
                for c in self.montage_result.get("inputs", [])],
            settings=dict(self.montage_result.get("config") or {}),
            log_cb=self._append_log)
        self._start_post_worker(
            package_service.create_package, tuple(),
            kwargs=mtree_args,
            done_slot=self._on_package_done,
            fail_slot=self._on_package_failed)

    def _on_package_done(self, doc: dict) -> None:
        self.last_package = doc
        self.btn_pkg_open.setEnabled(True)
        self.btn_pkg_open.setToolTip(doc["package_dir"])
        self._note(f"package exported: {doc['package_dir']}")
        self._on_status("package ready (nothing is uploaded).")

    def _on_package_failed(self, message: str) -> None:
        self._append_log(f"package error: {message}")
        QMessageBox.critical(self, "Package failed", message)

    def _on_open_package(self) -> None:
        tip = self.btn_pkg_open.toolTip()
        if tip and Path(tip).exists():
            os.startfile(tip)  # noqa: S606 - by design in a GUI shell

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
        cam = str(s.get("camera_style") or "cinematic_auto")
        self.combo_camera.setCurrentIndex(max(0, self.combo_camera.findData(cam)))
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
            "camera_style": self.combo_camera.currentData(),
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
        if self.worker is not None or self.batch_runner is not None:
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
        self._refresh_post_buttons()

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
        elif self.batch_runner is not None:
            self._on_status("cancelling queue job...")
            self.batch_runner.cancel_current()

    # ------------------------------------------------ batch slots
    def _note(self, text: str) -> None:
        self._append_log(f"[batch] {text}")
        self.statusBar().showMessage(f"[batch] {text}")

    def _load_manifests(self) -> None:
        # startup recovery: interrupted jobs revert to pending; nothing auto-reruns
        self.manifests = batch_jobs.load_all_manifests()
        self._update_batch_list()

    def _update_batch_list(self) -> None:
        self.lst_batch.blockSignals(True)
        self.lst_batch.clear()
        for m in self.manifests:
            done, total = m.progress_done()
            counts = "|".join(
                f"{m.by_status(s).__len__()} {s}" for s in batch_jobs.STATES
                if m.by_status(s))
            self.lst_batch.addItem(f"{m.name} — {done}/{total} [{counts}]")
        self.lst_batch.blockSignals(False)
        self._refresh_batch_buttons()
        # keep the post-production source picker in sync
        self.combo_post_batch.blockSignals(True)
        self.combo_post_batch.clear()
        for m in self.manifests:
            self.combo_post_batch.addItem(m.name, m.batch_id)
        self.combo_post_batch.blockSignals(False)

    def _on_batch_selected(self, row: int) -> None:
        self.active_manifest = self.manifests[row] if 0 <= row < len(self.manifests) else None
        self.btn_open_manifest.setEnabled(bool(self.active_manifest))
        self._refresh_batch_buttons()

    def _refresh_batch_buttons(self) -> None:
        m = self.active_manifest
        running = self.batch_runner is not None or self.worker is not None
        pending = len(m.by_status("pending")) if m else 0
        failedish = len(m.by_status("failed")) + len(m.by_status("cancelled")) if m else 0
        self.btn_batch_add.setEnabled(not running)
        self.btn_batch_start.setEnabled(bool(m) and pending > 0 and not running)
        self.btn_job_cancel.setEnabled(self.batch_runner is not None)
        self.btn_pending_cancel.setEnabled(bool(m) and pending > 0)
        self.btn_batch_retry.setEnabled(bool(m) and failedish > 0 and not running)
        self.btn_render.setEnabled(not running)
        self.btn_preview.setEnabled(not running)
        self._refresh_post_buttons()

    def _on_add_batch(self) -> None:
        if self.batch_runner is not None or self.worker is not None:
            return
        template_ids = [t["id"] for t in render_service.TEMPLATES] \
            if self.check_all_templates.isChecked() else [self._current_template()["id"]]
        try:
            batch_id = batch_jobs.new_batch_id()
            base_cfg = dict(self._collect_config())
            base_cfg.pop("file_name", None)
            _, jobs = expand_variations(
                base_cfg, template_ids=template_ids,
                variations=self.spin_variations.value(),
                seed=self.spin_seed.value(),
                seed_step=self.spin_seed_step.value(),
                batch_id=batch_id,
                batch_name=f"{'+'.join(template_ids)} x{self.spin_variations.value()}")
            manifest = batch_jobs.create_batch(
                f"{'+'.join(template_ids)} ×{self.spin_variations.value()} "
                f"(seed {self.spin_seed.value()}, step {self.spin_seed_step.value()})",
                jobs, batch_id=batch_id,
                directory=batch_jobs.BATCHES_DIR)
        except Exception as exc:  # validation/VariationError surfaced to the user
            self._append_log(f"batch error: {exc}")
            QMessageBox.warning(self, "Batch error", str(exc))
            return
        self._load_manifests()
        row = self.manifests.index(manifest) if manifest in self.manifests else 0
        self.lst_batch.setCurrentRow(row)
        self._note(f"batch created with {len(manifest.jobs)} jobs: {manifest.name}")

    def _on_start_queue(self) -> None:
        if self.batch_runner is not None or self.worker is not None:
            return
        candidates = [m for m in self.manifests if m.by_status("pending")]
        if not candidates:
            return
        manifest = self.active_manifest \
            if self.active_manifest and self.active_manifest.by_status("pending") \
            else candidates[-1]
        self.active_manifest = manifest
        for row, m in enumerate(self.manifests):
            if m is manifest:
                self.lst_batch.setCurrentRow(row)
                break
        self._start_batch_runner(manifest)

    def _start_batch_runner(self, manifest) -> None:
        runner = BatchRunner(manifest)
        self.batch_runner = runner
        self.btn_cancel.setEnabled(True)
        self.prog_batch.setValue(0)
        self._note(f"starting queue: {manifest.name}")
        runner.signals.job_started.connect(self._on_batch_job_started)
        runner.signals.job_progress.connect(self._on_batch_job_progress)
        runner.signals.job_log.connect(self._on_batch_job_log)
        runner.signals.job_result.connect(self._on_batch_job_result)
        runner.signals.manifest_changed.connect(self._on_manifest_changed)
        runner.signals.batch_finished.connect(self._on_batch_finished)
        self._refresh_batch_buttons()
        runner.start()

    def _on_batch_job_started(self, job) -> None:
        self._note(f"job started: {job.name} (seed {job.seed}, preset {job.preset or '-'})")
        self.prog_job.setValue(0)
        self.btn_job_cancel.setEnabled(True)

    def _on_batch_job_progress(self, job, frac: float) -> None:
        self.prog_job.setValue(int(max(0.0, min(1.0, frac)) * 100))

    def _on_batch_job_log(self, job, line: str) -> None:
        self._append_log(f"[{job.template_id} s{job.seed}] {line}")

    def _on_manifest_changed(self) -> None:
        self._update_batch_list()

    def _on_batch_job_result(self, job) -> None:
        if job.status == "succeeded":
            m = self.batch_runner.manifest if self.batch_runner else self.active_manifest
            done, total = m.progress_done() if m else (0, 0)
            if total:
                self.prog_batch.setValue(int(done / total * 100))
            self._note(f"job OK: {job.name} -> {job.output_path}")
            if job.verify:
                self._note("verify: " + ", ".join(
                    f"{k}={v}" for k, v in job.verify.items()))
        else:
            self._note(f"job {job.status}: {job.name}" +
                       (f" ({job.error})" if job.error else ""))

    def _on_batch_finished(self, manifest) -> None:
        if self.batch_runner is not None:
            self.batch_runner.wait()
            self.batch_runner = None
        self._note(f"queue finished: {manifest.overall_label()} done ({manifest.name})")
        self.btn_cancel.setEnabled(False)
        btn = self.btn_open_mp4
        try:
            last = [j for j in manifest.jobs if j.status == "succeeded"][-1]
            if last and last.output_path:
                self.last_result = {"mp4_path": last.output_path}
                btn.setEnabled(True)
        except IndexError:
            pass
        self._update_batch_list()
        self._refresh_batch_buttons()

    def _on_cancel_current_job(self) -> None:
        if self.batch_runner is not None:
            self.batch_runner.cancel_current()
            self._note("cancelling current job...")

    def _on_cancel_pending_jobs(self) -> None:
        if self.batch_runner is not None:
            self.batch_runner.cancel_pending()
            self._note("pending jobs will be cancelled after the current render")
            return
        m = self.active_manifest
        if not m:
            return
        for job in m.by_status("pending"):
            batch_jobs.transition(job, "cancelled", error="cancelled by user")
        m.save()
        self._update_batch_list()

    def _on_retry_failed(self) -> None:
        if self.batch_runner is not None:
            return
        m = self.active_manifest
        if not m:
            return
        for job in m.jobs:
            if job.status in ("failed", "cancelled"):
                batch_jobs.retry(job)
        m.save()
        self._update_batch_list()
        self._note("failed/cancelled jobs re-queued")

    def _open_batch_folder(self) -> None:
        m = self.active_manifest
        if not m or not m.jobs:
            return
        out_dir = str(m.jobs[0].config.get("out_dir") or "").strip()
        if out_dir and Path(out_dir).exists():
            os.startfile(out_dir)  # noqa: S606 - by design in a GUI shell

    def _open_manifest(self) -> None:
        if self.active_manifest:
            os.startfile(str(self.active_manifest.path))  # noqa: S606

    # ------------------------------------------------ persistence
    def save_settings_now(self) -> None:
        values = {
            "template_id": self._current_template()["id"],
            "aspect": self._resolve_aspect(),
            "resolution": self.combo_resolution.currentData(),
            "fps": int(self.combo_fps.currentData() or 30),
            "quality": self.combo_quality.currentData(),
            "camera_style": self.combo_camera.currentData(),
            "seed": int(self.spin_seed.value()),
            "color": self.primary_color,
            "out_dir": self.lbl_outdir.text().strip(),
            "music_path": self.music_path,
        }
        settings_store.save_settings(values)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.batch_runner is not None:
            if os.environ.get("CRS_GUI_TESTS") == "1":
                self.batch_runner.cancel_current()
                self.batch_runner.cancel_pending()
                self.batch_runner.wait(10000)
                self.batch_runner = None
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
