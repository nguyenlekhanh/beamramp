# Road & Environment Decorations - M3.7

Modular, opt-in scenery for the existing procedural tracks: road **shoulders**
(strips aligned to the road edges) and **trees** on the grass beyond them.
It is a pure-visual layer on top of the working track builder — the vehicle
physics, AI, camera, events, audio and rendering pipeline are untouched.

## Turning it on / off

| Environment variable | Effect                                                     |
| -------------------- | ---------------------------------------------------------- |
| `CRS_TRACK_DECO` unset or `0` | Default. Track renders exactly as in M3.6 (no deco nodes at all). |
| `CRS_TRACK_DECO` = anything else (e.g. `1`, `modular`) | Decorations are built. |

The check happens once in `track_builder.gd` `build_all()`:

```gdscript
var deco_flag := OS.get_environment("CRS_TRACK_DECO").strip_edges()
if deco_flag != "" and deco_flag != "0":
    ...instantiate environment/road_deco.gd and call decorate(scenario)...
```

No Python pipeline changes are involved (Godot inherits the UI process
environment), and `main.gd` is untouched — the deco node lives under the
TrackBuilder node. To revert the whole feature, delete
`godot_project/scripts/environment/road_deco.gd`, the two hook spots in
`godot_project/scripts/track/track_builder.gd`, and
`tests/test_road_deco.py`.

## What gets built

`godot_project/scripts/environment/road_deco.gd` builds, per *eligible* road
segment:

- **Shoulders** — two `BoxMesh` strips (1.6 m wide, 0.08 m thick) whose inner
  face starts exactly at the road edge (`edge_z`, extending outward). They sit
  `0.03 m` above the road top as a z-fighting guard, and slightly overhang the
  segment ends. They never cover the drivable surface.
- **Trees** — deterministic spacing walk (9–16 m + jitter), starting at least
  2.5 m beyond the shoulder outer edge, on the ground plane (y=0, matching the
  world `Ground` slab), clamped inside the arena bounds. Each tree is a trunk
  cylinder plus either a conifer cone or a 2–3 ball canopy cluster; heights
  3.5–7 m; radius, trunk proportions and canopy color vary per tree. Trees are
  additionally **skipped within 8 m (XZ distance) of every
  `layout.camera_hints` anchor**, so dramatic close-up shots keep a clear
  sightline (`TREE_HINT_CLEAR`).

All placement derives from `RandomNumberGenerator` seeded with
`seed * 1000193 + 7` (scenario seed), so the same scenario seed always renders
the same scenery. One summary line is logged:

```
[CRS env] deco: N segment(s) decorated -> S shoulder strip(s), T tree(s) (seed X, collision off)
```

## Guarantees

- **Zero physics interaction.** Only `MeshInstance3D` nodes are created — no
  `CollisionShape3D`, `CollisionObject3D`, `Area3D`, no collision layers or
  masks, ever. Vehicle suspension raycasts (mask 1|4), contacts, launch /
  landing / crash / finish events cannot see the decorations.
- **Deterministic.** Seeded from the scenario seed (see above).
- **Default is today's pixels.** With the variable unset the decos are not
  even instantiated.

## Segment eligibility (what gets skipped)

A segment is decorated only when ALL hold:

- has `from` + `size` arrays with ≥3 components;
- `rot_deg` absent or `[0, 0, 0]` (skips rotated **ramps** and yawed
  **spiral** segments — 2-side edge math is only valid on axis-aligned boxes);
- top height `from.y + size.y/2` in `[-0.01, 1.5]` m (skips elevated
  **bridge decks**; decorations belong at ground level; ramps' high ends are
  automatically excluded here too);
- length ≥ 10 m and width ≥ 4 m (skips tiny markers/short zone-ish slabs);
- shoulder positions stay inside the arena half-size minus a 2 m margin.

Gaps and pits between segments are naturally skipped because decoration is
per-segment.

## Knobs

All constants are at the top of `road_deco.gd` (`SHOULDER_W`, `TREE_MARGIN`,
`TREE_SPACING`, `TREE_H`, `TREE_HINT_CLEAR`, eligibility limits, palette).
They are verified by `tests/test_road_deco.py`.

## Test evidence & the physics-identity tolerance

`pytest tests/test_road_deco.py -q` runs four real headless Godot probes:

1. default OFF → no `[CRS env]` lines, clean exit;
2. `CRS_TRACK_DECO=1` → exactly one deco summary line, clean exit;
3. OFF vs ON physics identity on the same scenario/seed — trace timestamps
   match exactly and per-vehicle positions match within **0.01 m**, speeds
   within 0.5 m/s, grounded-wheel counts exact, AI controls within 0.05;
   event counts equal;
4. ON on a long straight template (`obstacle_course`) decorates cleanly.

Note on the tolerance: two *identical* deco-OFF runs of the same scenario were
compared in `temp/mil37_ctrl.py` — 3 of 16 trace lines already differ by
~0.0007 m, so the simulation has inherent run-to-run float jitter and exact
trace equality is not a meaningful assertion. 0.01 m is ~10x that floor and
still orders of magnitude below any contact-scale deflection (which would be
meters). Grounded-wheel count is compared exactly because decorations are
built collider-free.

## Known limitations

- Rotated segments (ramps, spiral), elevated bridge decks and very short
  slabs are never decorated by design.
- The 8 m hint-clear only guards camera **anchors**; a camera looking along
  the road can still catch trees farther along the corridor in the corner of
  the frame (adds depth, mostly harmless).
- Vehicles can visually overlap trees after losing control (trees are
  pass-through, collider-free by design).
- Configuration is env-var-only; there is no UI checkbox yet.
- The pre-existing gap where `layout.obstacles` never spawns at runtime
  (`build_obstacle` is not called by `main.gd`) is documented and deliberately
  untouched — obstacle spawning is out of scope for M3.7.

## Deferred roadmap

- **Python passthrough** — `scenario_service` could copy a
  `track_params.environment` dict into `layout.environment`, which
  `road_deco.gd` would then honor (density, shoulder width, disabled trees).
- **UI knob** — an "Environment decorations" checkbox in the render tab.
- Per-template decoration profiles (e.g. denser trees on `spiral_race`).
