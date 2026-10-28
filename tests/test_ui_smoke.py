"""UI smoke tests: startup, template switching, shutdown (offscreen platform)."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CRS_GUI_TESTS", "1")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.render_service import TEMPLATES  # noqa: E402
from app.services import render_service  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def isolated_settings(monkeypatch, tmp_path):
    """Redirect UI settings persistence to a temp file."""
    from app.ui import settings_store
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "ui_settings.json")
    yield tmp_path / "ui_settings.json"


def test_window_constructs_and_closes(qapp, isolated_settings):
    from app.ui.main_window import MainWindow
    w = MainWindow()
    assert w.list_templates.count() == len(TEMPLATES)
    w.show()
    w.close()
    assert isolated_settings.exists()  # settings persisted on close


def test_selecting_templates_updates_settings(qapp, isolated_settings):
    from app.ui.main_window import MainWindow
    w = MainWindow()
    for row, tpl in enumerate(TEMPLATES):
        w.list_templates.setCurrentRow(row)
        assert w.list_templates.currentRow() == row
        cfg = w._collect_config()
        assert cfg["template_id"] == tpl["id"]
        max_v = tpl.get("max_vehicles", 6)
        assert 1 <= w.spin_count.value() <= max_v
        if tpl["vehicle_type"] == "mixed":
            assert not w.combo_vtype.isEnabled()
        else:
            assert w.combo_vtype.isEnabled()
    w.close()


def test_invalid_settings_blocked_before_worker_start(qapp, isolated_settings, monkeypatch):
    from app.ui.main_window import MainWindow
    w = MainWindow()
    called = {"n": 0}
    monkeypatch.setattr(w, "_start_worker",
                        lambda kind, cfg: called.__setitem__("n", called["n"] + 1))
    # out_dir empty -> validation failure -> no worker start
    w.lbl_outdir.setText("")
    w._on_render()
    assert called["n"] == 0
    w.close()


def test_config_collect_maps_default_name(qapp, isolated_settings):
    from app.ui.main_window import MainWindow
    w = MainWindow()
    cfg = w._collect_config()
    assert cfg["file_name"].lower().endswith(".mp4")
    assert str(cfg["out_dir"])
    w.close()


def test_open_last_mp4_disabled_initially(qapp, isolated_settings):
    from app.ui.main_window import MainWindow
    w = MainWindow()
    assert not w.btn_open_mp4.isEnabled()
    assert not w.btn_cancel.isEnabled()
    w.close()


def test_presets_loaded_and_applied(qapp, isolated_settings):
    from app.ui.main_window import MainWindow
    w = MainWindow()
    assert w.combo_preset.count() >= 3
    # applying the square preset flips the aspect radio
    for i in range(w.combo_preset.count()):
        name = w.combo_preset.itemText(i)
        if "Square" in name:
            w.combo_preset.setCurrentIndex(i)
            break
    w._apply_preset()
    assert w.radio_11.isChecked()
    assert w._resolve_aspect() == "1:1"
    w.close()


def test_batch_panel_creates_manifest(qapp, isolated_settings, monkeypatch, tmp_path):
    from app.ui.main_window import MainWindow
    from app.render_queue import jobs as batch_jobs
    monkeypatch.setattr(batch_jobs, "BATCHES_DIR", tmp_path)
    w = MainWindow()
    w.spin_variations.setValue(3)
    w._on_add_batch()
    assert w.lst_batch.count() >= 1
    man = w.active_manifest
    assert man is not None and len(man.jobs) == 3
    assert all(j.status == "pending" for j in man.jobs)
    assert "batch_id" in man.to_dict()
    # cancel pending without starting anything
    w._on_cancel_pending_jobs()
    assert all(j.status == "cancelled" for j in man.jobs)
    doc = json.loads(man.path.read_text(encoding="utf-8"))
    assert {j["status"] for j in doc["jobs"]} == {"cancelled"}
    w.close()


def test_batch_start_disabled_when_busy_and_no_pending(qapp, isolated_settings):
    from app.ui.main_window import MainWindow
    w = MainWindow()
    assert not w.btn_batch_start.isEnabled()
    assert w.btn_batch_add.isEnabled()
    w.close()


def test_collect_config_includes_camera_style(qapp, isolated_settings):
    from app.ui.main_window import MainWindow
    w = MainWindow()
    cfg = w._collect_config()
    assert cfg["camera_style"] == "cinematic_auto"
    w.combo_camera.setCurrentIndex(1)
    assert w._collect_config()["camera_style"] == "single_wide"
    w.close()


# ---------------------------------------------------- post production (M3.4)

def _render_service_ff_tools():  # ffmpeg guard helper
    from app.services.ffmpeg_service import get_tools
    t = get_tools()
    return bool(t.get("ffmpeg") and t.get("ffprobe"))


def _req_ff():  # plain runtime guard inside tests
    if not _render_service_ff_tools():
        pytest.skip("ffmpeg required")


def _make_test_clip(path, dur=1.2):
    import subprocess
    from app.services.ffmpeg_service import get_tools
    ffmpeg = get_tools()["ffmpeg"]
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-nostdin",
         "-f", "lavfi", "-i",
         f"color=red:size=320x180:rate=30:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={dur}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", str(path)], capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-300:]


def test_post_panel_constructs(qapp, isolated_settings):
    from app.ui.main_window import MainWindow
    w = MainWindow()
    assert w.btn_m_build.isEnabled() is False
    assert "0 clips" in w.lbl_m_estimate.text()
    assert w.btn_thumbs_gen.isEnabled() is False
    assert not w.btn_pkg_export.isEnabled()
    # transition toggles the fade spinbox
    w.combo_m_trans.setCurrentIndex(1)
    assert w.spin_m_fade.isEnabled()
    w.combo_m_trans.setCurrentIndex(0)
    assert not w.spin_m_fade.isEnabled()
    w.close()


def test_post_load_clips_updates_estimate(qapp, isolated_settings,
                                          monkeypatch, tmp_path):
    _req_ff()
    from app.ui.main_window import MainWindow
    from app.render_queue import jobs as batch_jobs
    clip = tmp_path / "jobclip.mp4"
    _make_test_clip(clip)
    job = batch_jobs.JobRecord(
        job_id="j1", batch_id="b_ui", name="clip_one",
        template_id="gap_jump", template_name="Gap Jump", seed=42,
        config={"resolution": "720p", "aspect": "16:9"},
        preset="", status="succeeded", output_path=str(clip),
        verify={"ok": True, "width": 320, "height": 180, "fps": 30.0})
    man = batch_jobs.BatchManifest("b_ui", "ui test batch",
                                   path=tmp_path / "manifest.json",
                                   jobs=[job])
    monkeypatch.setattr(batch_jobs, "load_all_manifests",
                        lambda directory=None: [man])
    w = MainWindow()
    assert w.combo_post_batch.count() == 1
    w._on_post_load_clips()
    assert w.lst_clips.count() == 1
    assert "1 clips" in w.lbl_m_estimate.text()
    # unchecking the clip empties the estimate
    item = w.lst_clips.item(0)
    item.setCheckState(item.checkState().__class__.Unchecked)
    assert "0 clips" in w.lbl_m_estimate.text()
    item.setCheckState(item.checkState().__class__.Checked)
    # remove button works
    w.lst_clips.setCurrentRow(0)
    w._on_clip_remove()
    assert w.lst_clips.count() == 0
    w.close()
