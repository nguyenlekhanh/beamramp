"""UI smoke tests: startup, template switching, shutdown (offscreen platform)."""
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
