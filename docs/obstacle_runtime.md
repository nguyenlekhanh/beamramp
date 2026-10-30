# Obstacle Runtime Foundation - M3.8A

First runtime activation of the previously unreachable obstacle pipeline:
`layout.obstacles` is now consumed by `track_builder.gd` inside `build_all()`.
`main.gd`, the Python layout generators and the scenario schema are untouched.

## M3.8B update: moving obstacles (rotator)

M3.8B adds the **rotator** (the first moving/interactive type) alongside the
cone. The supported spawn set is now `{cone, rotator}`; every other type is
still counted and skipped with an aggregated warning.

- `tests/test_obstacle_runtime.py` grew to 7 tests (rotator mixed-list spawn
  + skip accounting, exactly-one-rotator strike contrast/stability/repeat,
  cone+rotator+deco compatibility).
- Determinism fix in `rotator.gd`: the bar's angle is now derived from an
  accumulated **physics-time counter** (`_physics_process` ticks) and applied
  in `_process` (required for `AnimatableBody3D.sync_to_physics`). The
  original `_process`-delta integration made the angle machine/fps dependent
  (headless probes iterate faster than real time), which measured as
  *borderline strike flips between identical runs*. With physics-time
  derivation, repeated runs replayed strikes identically (3 of 4 probe pairs
  matched to ~3 mm over 4 s; the primary strike `t=2.22 / v0 / severity 7.2`
  was identical in all 13 measured probes).
- Crash events from a moving `AnimatableBody3D` record
  `other = "@StaticBody3D@..."` (Godot reports the contact via the internal
  physics body of the sync-to-physics mechanism, not the named node). Tests
  assert timing/vehicle/severity, not the `other` name. This limitation will
  also apply to hammer/moving_platform unless revisited.
- Strike aftermath is intentionally dramatic (kinematic strike from an
  effectively infinite-mass body): cars can be knocked backward, tumble, and
  the existing recovery system handles worst cases (fall/roll/stuck respawn)
  — measured: no falls inside 6 s for the single-rotator case.
- AI awareness still deferred (unchanged decision; see below). The
  `course`-template rotators sit in the center lane by design; affected runs
  stay recoverable.

## M3.8A scope

## M3.8A scope

- Spawns obstacles from `scenario.layout.obstacles` at build time using the
  pre-existing `track_builder._build_obstacles()` pass and the pre-existing
  per-type scripts in `godot_project/scripts/obstacles/` (their `setup(ob)`
  contract and collision layers were audited, not modified).
- M3.8A supported **cone only** (knockable RigidBody3D, mass 30 kg, layer 4,
  mask 1|2). Every other layout type (`barrier`, `hammer`, `ball`,
  `moving_platform`, `rotator`, `crash_wall`) was counted in a single
  aggregated line and intentionally skipped (M3.8B later adds `rotator`).
- A scenario whose layout contains `obstacles: []` (e.g. `spiral_race`) gets
  **no** `[CRS obs]` output and runs exactly as before.

## Why cone taps are eventless, and why no AI awareness yet

Cone contacts are benign by physics: a ~30 kg cone deflects a ~1250 kg car by
well under 1 m/s, so vehicles plow through them (that is the design intent of
a knockable cone). The generic vehicle event design only records `crash`
events (and applies visual damage) when velocity changes by >= 2.0 m/s within
one tick, so cone taps are typically **eventless** by construction - launch,
land and finish events are unaffected.

The AI obstacle-avoidance hook (`driver_ai._obstacle_ahead` /
`obstacle_watchlist`) remains intentionally dormant: it requires populating
`car.owner.obstacle_watchlist` with obstacle node references, but
runtime-built cars currently have no `owner`, which lives in the protected
vehicle-spawning path. Wiring it (and deciding per-type avoidance) is
deferred until the heavier stopper-type obstacles become spawnable in a
coming milestone - at that stage awareness becomes genuinely necessary.


## Collision / layer map (audited, unchanged)

| Object            | Type             | Layer | Mask    | Effect on vehicles          |
| ----------------- | ---------------- | ----- | ------- | --------------------------- |
| Vehicle           | RigidBody3D      | 2     | 1\|2\|4 | -                           |
| Road segments     | StaticBody3D     | 1     | 0       | drive surface               |
| Cone              | RigidBody3D      | 4     | 1\|2    | knocked away on contact     |
| Barrier/crash wall| StaticBody3D     | 1     | 0       | solid stopper (not spawned) |
| Hammer/rotator/mover | AnimatableBody3D | 4   | 0       | solid (not spawned)         |
| Zones             | Area3D           | 4     | 1\|2    | launch/landing/finish events|

Zones detect only layers 1|2, so knocked cones (layer 4) can never fire or
disturb zone events. Cones rest on the ground (mask includes layer 1).

## Known behavior details

- Cones are authored at `at[1] = 0.4` m while road tops are at 0.5 m, i.e.
  they spawn 0.1 m inside the road surface; the physics solver's
  depenetration settles them on the road surface within the first ticks
  (deterministic, verified visually).
- Contact evidence approach: head-to-head probes with `obstacles=[]` show
  identical trajectories before the cone and a >0.1 m divergence after
  contact; two identical cone runs agree within ~0.1 m (knocked-cone chaos
  amplifies the sim's inherent ~1 mm run-to-run jitter).
- M3.7 decorations are fully compatible (independent node paths; the
  compat test runs both together).

## Validation artifacts

- `tests/test_obstacle_runtime.py` - 4 headless-Godot tests (spawn count /
  one-cone contact contrast + stability + repeatability / obstacle-free
  unchanged / deco compatibility).
- `output/mil38a_validation/` - representative render of a gap_jump with
  exactly ONE cone placed on the center lane (and M3.7 decorations enabled
  simultaneously), with inspected frames.

## Deferred roadmap (M3.8B+ candidates)

- Barrier walls (stopper events), hammer/ball/rotator/moving_platform spawn
  set with per-type event semantics.
- AI obstacle awareness (watchlist wiring + avoidance) for stopper types.
- Python-side knob to filter/scale obstacle spawning per scenario.
