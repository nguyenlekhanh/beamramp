"""Milestone 3.6A: asset_service — validation, collision-safe copy, report."""
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CRS_GUI_TESTS", "1")

from app.services import asset_service  # noqa: E402
from app.services import godot_service  # noqa: E402
from app.services.asset_service import (ASSET_SCHEMA,  # noqa: E402
                                        AssetError)


# ---------------------------------------------------------------------------
# minimal GLB builder (a real, spec-compliant single-mesh model)
# ---------------------------------------------------------------------------


def _cube_positions(x0, y0, z0, x1, y1, z1):
    """36 non-indexed vertices (12 triangles), CCW faces."""
    def quad(p0, p1, p2, p3):
        return [[p0, p1, p2], [p0, p2, p3]]
    A, B = (x0, y0, z0), (x1, y1, z1)
    x0v, y0v, z0v = A
    x1v, y1v, z1v = B
    tris = []
    tris += quad((x0v, y0v, z0v), (x0v, y1v, z0v), (x1v, y1v, z0v), (x1v, y0v, z0v))
    tris += quad((x0v, y0v, z1v), (x1v, y0v, z1v), (x1v, y1v, z1v), (x0v, y1v, z1v))
    tris += quad((x0v, y0v, z0v), (x0v, y0v, z1v), (x0v, y1v, z1v), (x0v, y1v, z0v))
    tris += quad((x1v, y0v, z0v), (x1v, y1v, z0v), (x1v, y1v, z1v), (x1v, y0v, z1v))
    tris += quad((x0v, y0v, z0v), (x1v, y0v, z0v), (x1v, y0v, z1v), (x0v, y0v, z1v))
    tris += quad((x0v, y1v, z0v), (x0v, y1v, z1v), (x1v, y1v, z1v), (x1v, y1v, z0v))
    out = []
    for tri in tris:
        for p in tri:
            out += [p[0], p[1], p[2]]
    return out


def build_minimal_glb(path: Path, mn=(2.0, 2.0, 1.0), mx=(4.0, 3.0, 5.0)):
    """Box model with unusual origin (min y=2m, off-center) -> origin warnings."""
    verts = _cube_positions(*mn, *mx)
    bin_data = struct.pack(f"<{len(verts)}f", *verts)
    doc = {
        "asset": {"version": "2.0", "generator": "crs-test"},
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "mode": 4}]}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 36,
                       "type": "VEC3", "min": list(mn), "max": list(mx)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(bin_data)}],
        "buffers": [{"byteLength": len(bin_data)}],
    }
    js = json.dumps(doc).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    bin_data += b"\x00" * ((-len(bin_data)) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_data)
    glb = (b"glTF" + struct.pack("<I", 2) + struct.pack("<I", total) +
           struct.pack("<2I", len(js), 0x4E4F534A) + js +
           struct.pack("<2I", len(bin_data), 0x004E4942) + bin_data)
    path.write_bytes(glb)
    return path


@pytest.fixture
def user_assets_dir(tmp_path, monkeypatch):
    d = tmp_path / "user_assets"
    monkeypatch.setattr(asset_service, "USER_ASSETS_DIR", d)
    return d


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    d = tmp_path / "asset_reports"
    monkeypatch.setattr(asset_service, "REPORTS_DIR", d)
    return d


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_validate_rejects_missing_and_bad_files(tmp_path):
    with pytest.raises(AssetError, match="not found"):
        asset_service.validate_asset(str(tmp_path / "missing.glb"))
    with pytest.raises(AssetError, match="unsupported asset type"):
        asset_service.validate_asset(str(tmp_path / "photo.jpg"))
    model = build_minimal_glb(tmp_path / "model.glb")
    out = asset_service.validate_asset(str(model))
    assert out.is_file()
    uppercase = tmp_path / "MODEL.GLB"
    uppercase.write_bytes(model.read_bytes())
    assert asset_service.validate_asset(str(uppercase)).suffix == ".GLB"


def test_validate_rejects_gltf_empty_and_fake(tmp_path):
    gltf = tmp_path / "model.gltf"
    gltf.write_text('{"asset": {"version": "2.0"}}', encoding="utf-8")
    with pytest.raises(AssetError, match="gltf"):
        asset_service.validate_asset(str(gltf))
    empty = tmp_path / "empty.glb"
    empty.write_bytes(b"")
    with pytest.raises(AssetError, match="empty"):
        asset_service.validate_asset(str(empty))
    fake = tmp_path / "fake.glb"
    fake.write_bytes(b"PK\x03\x04 not a glTF container")
    with pytest.raises(AssetError, match="magic"):
        asset_service.validate_asset(str(fake))


# ---------------------------------------------------------------------------
# collision-safe copy
# ---------------------------------------------------------------------------


def test_copy_never_overwrites_and_bump_names(user_assets_dir):
    src_dir = Path(tempfile.mkdtemp(prefix="crs_asset_src_"))
    original = build_minimal_glb(src_dir / "car.glb")
    first = original.read_bytes()
    dest1 = asset_service.copy_asset(original)
    assert dest1.name == "car.glb" and dest1.read_bytes() == first
    dest2 = asset_service.copy_asset(original)
    assert dest2.name == "car_1.glb"
    # _1 is taken (treat like an existing file) -> never overwritten
    (asset_service.USER_ASSETS_DIR / "car_1.glb").write_bytes(b"existing user file")
    dest3 = asset_service.copy_asset(original)
    assert dest3.name == "car_2.glb"
    assert dest3.read_bytes() == first
    assert (asset_service.USER_ASSETS_DIR / "car.glb").read_bytes() == first
    assert original.read_bytes() == first          # user's original untouched
    assert [p.name for p in asset_service.user_assets()] == \
        ["car.glb", "car_1.glb", "car_2.glb"]


def test_user_assets_empty_when_no_dir(user_assets_dir):
    assert asset_service.user_assets() == []


# ---------------------------------------------------------------------------
# report parsing / validation (pure python)
# ---------------------------------------------------------------------------


def _sample_report(verdict="loaded_with_warnings"):
    return {
        "schema": ASSET_SCHEMA, "created_at": "2026-10-23T12:00:00",
        "mode": "report", "source_asset_path": "C:/x/car.glb",
        "copied_asset_path": "C:/y/car.glb",
        "config": {"scale": 1.0, "pitch_deg": 0.0, "yaw_deg": 0.0},
        "status": {"resource_load": "ok", "import_step": "present",
                   "instantiate": "ok"},
        "metrics": {"mesh_instances": 1, "surface_count": 6},
        "bounds": {"size_m_scaled": [2, 1, 4]},
        "warnings": ["origin is not at ground-contact level"],
        "verdict": verdict,
        "statement": "Loading a GLB in this preview proves visual instantiation "
                     "only.",
    }


def test_parse_report_line_ok():
    doc = _sample_report()
    line = "[CRS asset] REPORT " + json.dumps(doc)
    parsed = asset_service.parse_report_line(
        "Godot v4.4.1\nsome noise\n" + line + "\n")
    assert parsed and parsed["verdict"] == doc["verdict"]


def test_parse_report_line_none_when_absent():
    assert asset_service.parse_report_line("no report here\n") is None


def test_parse_report_line_malformed_raises():
    with pytest.raises(AssetError, match="malformed"):
        asset_service.parse_report_line("[CRS asset] REPORT {not valid json}")


def test_validate_report_dict_rejects_bad_schema_and_verdict():
    doc = _sample_report()
    doc.pop("verdict")
    with pytest.raises(AssetError, match="verdict"):
        asset_service.validate_report_dict(doc)
    doc = _sample_report("unexpected_verdict")
    with pytest.raises(AssetError, match="verdict"):
        asset_service.validate_report_dict(doc)
    doc = _sample_report()
    doc["schema"] = "crs.other/9"
    with pytest.raises(AssetError, match="schema"):
        asset_service.validate_report_dict(doc)


# ---------------------------------------------------------------------------
# godot integration (skipped cleanly when Godot is unavailable)
# ---------------------------------------------------------------------------

skip_no_godot = pytest.mark.skipif(
    godot_service.find_godot() is None,
    reason="Godot binary not found under tools/ (real integration test)")


@pytest.fixture
def project_assets_dir(monkeypatch):
    """Throwaway dir INSIDE the godot project so the headless importer sees it."""
    d = godot_service.GODOT_PROJECT / "assets" / "user_assets_test"
    monkeypatch.setattr(asset_service, "USER_ASSETS_DIR", d)
    yield d
    if d.exists():
        for p in sorted(d.glob("*"), reverse=True):
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
        try:
            d.rmdir()
        except OSError:
            pass


@skip_no_godot
def test_real_import_then_report_valid_glb(project_assets_dir, reports_dir,
                                           tmp_path):
    src_dir = Path(tempfile.mkdtemp(prefix="crs_asset_src_"))
    model = build_minimal_glb(src_dir / "test_car.glb")

    def log(msg):
        logs.append(msg)
    logs = []
    doc = asset_service.generate_report(str(model), log_cb=log)
    assert doc["schema"] == ASSET_SCHEMA
    assert doc["verdict"] in ("loaded_with_warnings",
                              "loaded_visual_review_required")
    assert "NOT" in doc["statement"] or "not" in doc["statement"].lower()
    assert doc["copied_asset_path"].startswith(str(project_assets_dir))
    assert doc["source_asset_path"] == str(model)
    # this test model has its bottom 2m above origin -> origin warning expected
    assert any("origin" in w.lower() for w in doc["warnings"]), doc["warnings"]
    assert doc["metrics"]["mesh_instances"] >= 1
    # box is 2 x 1 x 4 m at scale 1.0 -> bounds match real geometry
    assert len(doc["bounds"]["size_m_scaled"]) == 3
    assert all(abs(doc["bounds"]["size_m_scaled"][i] - exp) < 0.05
               for i, exp in enumerate((2.0, 1.0, 4.0))), doc["bounds"]
    # parseable report file was persisted (never simple stdout claim)
    assert any(reports_dir.glob("*.json"))
    assert any(any("report" in ln for ln in logs) for _ in [0])


@skip_no_godot
def test_real_report_broken_glb_reports_load_failed(project_assets_dir,
                                                    reports_dir, tmp_path):
    broken = tmp_path / "broken.glb"
    broken.write_bytes(b"glTF" + struct.pack("<I", 2) + struct.pack("<I", 999) +
                       struct.pack("<I", 0))
    doc = asset_service.generate_report(str(broken), skip_import=True)
    # a declared failure must never be reported as a success
    assert doc["verdict"] == "load_failed"
    assert doc["status"]["resource_load"] in ("failed", "loader_missing")


@skip_no_godot
def test_generate_report_without_godot_raises(project_assets_dir, reports_dir,
                                              monkeypatch, tmp_path):
    model = build_minimal_glb(tmp_path / "car.glb")
    monkeypatch.setattr(godot_service, "find_godot", lambda: None)
    with pytest.raises(AssetError, match="Godot not found"):
        asset_service.generate_report(str(model))


def test_generate_report_missing_asset_raises(user_assets_dir, monkeypatch,
                                              tmp_path):
    monkeypatch.setattr(godot_service, "find_godot", lambda: None)
    with pytest.raises(AssetError, match="not found"):
        asset_service.generate_report(str(tmp_path / "nope.glb"))


@skip_no_godot
@pytest.mark.parametrize("kw", [dict(scale=2.0), dict(yaw_deg=45.0)])
def test_report_respects_scale_and_rotation(project_assets_dir, reports_dir,
                                            tmp_path, kw):
    src_dir = Path(tempfile.mkdtemp(prefix="crs_asset_src_"))
    model = build_minimal_glb(src_dir / "test_car.glb")
    doc = asset_service.generate_report(str(model), **kw)
    size = doc["bounds"]["size_m_scaled"]
    expected_scale = kw.get("scale", 1.0)
    assert all(abs(size[i] - exp * expected_scale) < 0.1
               for i, exp in enumerate((2.0, 1.0, 4.0))), size
    assert doc["config"]["yaw_deg"] == kw.get("yaw_deg", 0.0)
    assert all(abs(size[i] - exp * expected_scale) < 0.1
               for i, exp in enumerate((2.0, 1.0, 4.0))), size
    assert doc["config"]["yaw_deg"] == kw.get("yaw_deg", 0.0)


@skip_no_godot
@pytest.mark.skipif(
    os.environ.get("CRS_ASSET_WINDOW_TESTS") != "1",
    reason="manual check: opens a live Godot window (set CRS_ASSET_WINDOW_TESTS=1)")
def test_open_preview_real_window(project_assets_dir, tmp_path):
    model = build_minimal_glb(tmp_path / "preview_car.glb")
    res = asset_service.open_preview(str(model), scale=1.0, yaw_deg=30.0)
    assert res["copied_asset_path"].startswith(str(project_assets_dir))
    proc = res["proc"]
    import time
    time.sleep(3.0)
    # user closes the window manually; we only verify the process launched
    assert proc.poll() is None or proc.poll() in (0, 3)
    proc.terminate()
