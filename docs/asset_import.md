# Asset Import (GLB preview) - M3.6A

This milestone adds an *isolated* GLB preview sandbox. It does **not** change
the production vehicle in any way: `main.tscn`, `main.gd`, `car_factory.gd`,
vehicle physics, AI, camera, audio, recording and the batch queue are
untouched. All behavior lives in `godot_project/scenes/asset_preview.tscn` +
`godot_project/scripts/asset_preview/glb_preview.gd`, wrapped by
`app/services/asset_service.py` and an "Assets (GLB preview)" group in the UI.

## Scope, and what a successful load does NOT prove

A verdict of `loaded_with_warnings` / `loaded_visual_review_required` means the
Godot glTF importer could produce a scene and instantiate it. It does **not**
verify production readiness, wheel placement, center of mass, physics
behavior, or collisions. Visual/manual inspection (and later phases) is always
required before an asset is used as an actual vehicle.

## What the UI does

"Assets (GLB preview)" group in the render tab, right column:

1. **Import GLB...** - pick a `.glb` file. It is validated (correct extension,
   `glTF` magic bytes), **copied** (never moved/overwritten - a `_1`
   suffix is added on collisions) into
   `godot_project/assets/user_assets/`, then a headless Godot import is run.
2. **Asset Report** - runs the full isolated check and writes a timestamped
   JSON report under `output/asset_reports/`. The verdict and warnings are
   shown in a dialog and logged.
3. **Preview Window** - opens the same scene in a real window (800x600) with
   the auto-grounded, auto-framed camera; close it manually when done.

## What the report contains

Schema `crs.asset_report/1` with verdicts:

| Verdict                        | Meaning                                   |
| ------------------------------ | ----------------------------------------- |
| `load_failed`                  | Godot could not import/load/instantiate it |
| `loaded_with_warnings`         | loaded, but review the warning list       |
| `loaded_visual_review_required`| loaded cleanly - still inspect visually    |

Fields of interest: `bounds.local_meters_unscaled` (model-space AABB),
`size_m_scaled`, `metrics` (mesh/surface/material/texture/animation counts,
node count, skeleton bones), `status` (`resource_load`/`import_step`/
`instantiate`), `warnings`, and `statement` (anti-overpromise disclaimer).
Typical warnings: origin not at ground-contact level, origin offset from the
volume center, axis spans that look wrong (over 50 m or under 0.1 m), no
measurable bounds (empty scene), meshes without materials.

## Windows command line (no UI)

Headless verify of a GLB at `C:\abs\path\model.glb`:

```bat
python -c "from app.services.asset_service import generate_report; print(generate_report(r'C:\abs\path\model.glb')['verdict'])"
```

Or open the interactive preview window:

```bat
python -c "from app.services.asset_service import open_preview; open_preview(r'C:\abs\path\model.glb')"
```

(Service layer requires the `app` package importable: run from the project
root. Godot is auto-located under `tools/Godot*win64*.exe`; override with the
`CRS_GODOT_EXE` environment variable.)

Under the hood the service: validates + copies the file into
`godot_project/assets/user_assets/`, runs
`tools\Godot_v4.4.1-stable_win64_console.exe --headless --import --path
godot_project`, then runs `godot --headless --path godot_project
<abs path to scenes/asset_preview.tscn>` with a `CRS_ASSET_PREVIEW` config
env pointing at a JSON config ({glb, scale, pitch_deg, yaw_deg, mode,
report_path}) and parses the `[CRS asset] REPORT {json}` stdout line. Exit
codes: 0 ok, 2 bad config, 3 load failure (a full load-failed report is still
written in that case).

## Troubleshooting

| Symptom                                      | Fix                                                                                |
| -------------------------------------------- | ---------------------------------------------------------------------------------- |
| "GLB could not be loaded ... not imported"   | The import step did not run (Godot missing / asset imported once already). Re-run the UI report button or the command above. |
| "loader returned null (malformed...)"        | The file is not a valid GLB (e.g. gfx-bailey-unity mesh exports, corrupted download). Re-export as binary glTF 2.0 (`*.glb`). |
| Report says loaded but looks wrong           | Importing only proves instantiation. Check warnings, open the Preview Window, and inspect visually. |
| Import step fails with a log tail            | Usually a Godot path problem - ensure `tools\Godot_v4.4.1-stable_win64.exe` exists (or set `CRS_GODOT_EXE`). |
| Wrong scale/orientation                      | Use the report's unscaled bounds as a ruler. Scale/pitch/yaw parameters exist on the service functions (`scale` clamped 0.05-20) - watch the origin markers (red=x, green=z) in the preview window. |

## Using image-to-3D generated assets

Exporters like Meshy, Tripo, Rodin, or Luma produce `*.glb` files that import
here, but their UV maps are frequently broken (overlapping islands, split
shells), so a successful import does NOT mean a clean texture bake. Before
using them in production:

1. Inspect in Blender (`File > Import > glTF 2.0`), check the UV editor for
   overlaps, and re-bake textures if needed.
2. Remesh/decimate if the polycount is very high (these tools often emit huge
   triangle counts).
3. Confirm the origin sits at ground-contact level (or accept/adjust the
   preview's origin-offset warnings).
4. Scale should be real-world meters after export; the report's
   `local_meters_unscaled` shows what Godot measured.
