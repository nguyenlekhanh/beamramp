extends Node3D
## M3.7: modular road & environment decorations (opt-in via CRS_TRACK_DECO=1).
##
## Deterministic from the scenario seed. Builds ONLY visual meshes:
## - road shoulder strips aligned just outside each eligible road edge
## - trees on the grass beyond the shoulders, both sides
## Eligible segments: axis-aligned platforms/bridges with tops near the ground
## (skips ramps, spiral/yawed segments, elevated bridge decks, gap markers).
## No CollisionShape3D / CollisionObject3D / Area3D is ever created here, so
## vehicle physics, raycast suspension, contacts and events are untouched.

const COLORS := preload("res://scripts/util/colors.gd")

const SHOULDER_W := 1.6          # strip width (m)
const SHOULDER_H := 0.08         # strip thickness (m)
const SHOULDER_Y_OFF := 0.03     # sit-on-top z-fight guard above road top
const SHOULDER_LEN_PAD := 0.6    # slight length overhang each end (m)
const TREE_MARGIN := 2.5         # min distance tree center - shoulder outer edge (m)
const TREE_MARGIN_SPREAD := 3.5  # additional jitter spread (m)
const TREE_SPACING := [9.0, 16.0]
const TREE_H := [3.5, 7.0]
const TREE_X_INSET := 4.0        # stay inside segment ends
const SEG_MAX_TOP_Y := 1.5       # eligibility: skip decks above this
const SEG_MIN_LEN := 10.0        # skip tiny segments/markers
const ARENA_MARGIN := 2.0
const TREE_HINT_CLEAR := 8.0     # keep camera-hint anchors clear of canopies

const SHOULDER_COLOR := "#3d4453"
const TRUNK_COLOR := "#6b4f2a"
const CANOPY_COLORS := ["#2a9d61", "#57a773", "#3f7d44", "#4c9a2a", "#2f6d3f"]

var _rng := RandomNumberGenerator.new()
var _trunk_mat: StandardMaterial3D = null
var _canopy_mats: Array = []
var _shoulder_mat: StandardMaterial3D = null
var _hint_xz: Array = []   # camera anchors as [x, z] close-up view corridors


func decorate(scenario: Dictionary) -> void:
	var layout: Dictionary = scenario.get("layout", {})
	var seed_val: int = int(scenario.get("seed", 0))
	_rng.seed = seed_val * 1000193 + 7
	_hint_xz.clear()
	for key in layout.get("camera_hints", {}):
		var p = layout["camera_hints"][key]
		if p is Array and p.size() >= 2:
			_hint_xz.append([float(p[0]), float(p[2])])
	_trunk_mat = COLORS.mat_flat(TRUNK_COLOR)
	_shoulder_mat = COLORS.mat_flat(SHOULDER_COLOR)
	for hex in CANOPY_COLORS:
		_canopy_mats.append(COLORS.mat_flat(hex))
	var arena: Array = layout.get("meta", {}).get("arena", {}).get("size", [600.0, 400.0])
	var half_z: float = float(arena[1]) * 0.5 - ARENA_MARGIN
	var road: Array = layout.get("road", [])
	var segs := 0
	var strips := 0
	var trees := 0
	for i in range(road.size()):
		var seg: Dictionary = road[i]
		if not _eligible(seg):
			continue
		segs += 1
		var from := Vector3(float(seg["from"][0]), float(seg["from"][1]), float(seg["from"][2]))
		var size := Vector3(float(seg["size"][0]), float(seg["size"][1]), float(seg["size"][2]))
		var top_y: float = from.y + size.y * 0.5 + SHOULDER_Y_OFF
		var strips_done := _build_shoulders(from, size, top_y, half_z)
		strips += strips_done
		trees += _build_trees(from, size, half_z)
	print("[CRS env] deco: %d segment(s) decorated -> %d shoulder strip(s), %d tree(s) (seed %d, collision off)" % [segs, strips, trees, seed_val])


## Only ground-aligned, axle-parallel box segments: no rotation of any kind,
## low top (decks at bridge height are skipped), long enough to decorate.
func _eligible(seg: Dictionary) -> bool:
	if not (seg.has("from") and seg.has("size")):
		return false
	var f = seg.get("from")
	var s = seg.get("size")
	if not (f is Array and s is Array and f.size() >= 3 and s.size() >= 3):
		return false
	var rot = seg.get("rot_deg")
	if rot != null and rot is Array and rot.size() >= 3:
		if absf(float(rot[0])) > 0.01 or absf(float(rot[1])) > 0.01 or absf(float(rot[2])) > 0.01:
			return false
	elif rot != null:
		return false
	var size := Vector3(float(s[0]), float(s[1]), float(s[2]))
	var top_y: float = float(f[1]) + size.y * 0.5
	if top_y > SEG_MAX_TOP_Y or top_y < -0.01:
		return false
	if size.x < SEG_MIN_LEN or size.z < 4.0:
		return false
	return true


## Two strips per segment, inner face exactly at the road edge (no overlap):
## strip spans [edge, edge + SHOULDER_W] on each side.
func _build_shoulders(from: Vector3, size: Vector3, top_y: float, half_z: float) -> int:
	var done := 0
	for side_sign in [-1.0, 1.0]:
		var edge_z: float = from.z + side_sign * size.z * 0.5
		var strip_z: float = edge_z + side_sign * SHOULDER_W * 0.5
		if strip_z * side_sign >= half_z:
			continue  # never decorate past the arena bounds
		var mi := MeshInstance3D.new()
		var bm := BoxMesh.new()
		bm.size = Vector3(size.x + SHOULDER_LEN_PAD, SHOULDER_H, SHOULDER_W)
		mi.mesh = bm
		mi.material_override = _shoulder_mat
		mi.position = Vector3(from.x, top_y + SHOULDER_H * 0.5, strip_z)
		add_child(mi)
		done += 1
	return done


## Trees walk each side from the segment start to its end with deterministic
## spacing/jitter; they stand on the ground slab (grass at y=0), never on
## the road, and never past the arena margin.
func _build_trees(from: Vector3, size: Vector3, half_z: float) -> int:
	var count := 0
	for side_sign in [-1.0, 1.0]:
		var edge_z: float = from.z + side_sign * size.z * 0.5
		var base_z: float = edge_z + side_sign * (SHOULDER_W + TREE_MARGIN)
		var x: float = from.x - size.x * 0.5 + TREE_X_INSET
		var x_end: float = from.x + size.x * 0.5 - TREE_X_INSET
		while x <= x_end:
			var z: float = base_z + side_sign * _rng.randf_range(0.0, TREE_MARGIN_SPREAD)
			z = clampf(z, -half_z, half_z)
			# never let a canopy block a dramatic camera anchor (the wide /
			# close-up 'detail' shots live beside the track at these points)
			if not _blocks_hint(x, z):
				_make_tree(Vector3(x + _rng.randf_range(-1.0, 1.0), 0.0, z))
				count += 1
			x += _rng.randf_range(TREE_SPACING[0], TREE_SPACING[1])
	return count


func _blocks_hint(x: float, z: float) -> bool:
	var r2: float = TREE_HINT_CLEAR * TREE_HINT_CLEAR
	for hp in _hint_xz:
		var dx: float = x - float(hp[0])
		var dz: float = z - float(hp[1])
		if dx * dx + dz * dz <= r2:
			return true
	return false


## Pure visual tree: trunk + canopy (conifer cone / 2-ball / 3-ball cluster).
func _make_tree(base: Vector3) -> void:
	var total_h: float = _rng.randf_range(TREE_H[0], TREE_H[1])
	var trunk_r: float = _rng.randf_range(0.12, 0.28)
	var trunk_h: float = total_h * _rng.randf_range(0.35, 0.5)
	var style: int = _rng.randi() % 3
	var canopy_mat: StandardMaterial3D = _canopy_mats[_rng.randi() % _canopy_mats.size()]

	var trunk := MeshInstance3D.new()
	var tm := CylinderMesh.new()
	tm.top_radius = trunk_r * 0.7
	tm.bottom_radius = trunk_r
	tm.height = trunk_h
	tm.radial_segments = 8
	trunk.mesh = tm
	trunk.material_override = _trunk_mat
	trunk.position = base + Vector3(0, trunk_h * 0.5, 0)
	add_child(trunk)

	var canopy_r: float = total_h * _rng.randf_range(0.3, 0.4)
	if style == 0:
		_cone_canopy(base, trunk_h, canopy_r, total_h * 0.65, canopy_mat)
	else:
		var balls: int = 0
		if style == 1:
			balls = 2
		else:
			balls = 3
		_ball_canopy(base, trunk_h, canopy_r, balls, canopy_mat)


func _cone_canopy(base: Vector3, trunk_h: float, radius: float, cone_h: float,
		mat: StandardMaterial3D) -> void:
	var mi := MeshInstance3D.new()
	var cm := CylinderMesh.new()
	cm.top_radius = 0.02
	cm.bottom_radius = radius
	cm.height = cone_h
	cm.radial_segments = 9
	mi.mesh = cm
	mi.material_override = mat
	mi.position = base + Vector3(_rng.randf_range(-0.3, 0.3),
			trunk_h + cone_h * 0.42, _rng.randf_range(-0.3, 0.3))
	add_child(mi)


func _ball_canopy(base: Vector3, trunk_h: float, radius: float, balls: int,
		mat: StandardMaterial3D) -> void:
	for b in range(balls):
		var mi := MeshInstance3D.new()
		var sm := SphereMesh.new()
		var r: float = radius * (1.0 - 0.18 * b)
		sm.radius = r
		sm.height = r * 2.0
		sm.radial_segments = 12
		sm.rings = 8
		mi.mesh = sm
		mi.material_override = mat
		var off := Vector3(_rng.randf_range(-0.6, 0.6), trunk_h * (0.85 + 0.25 * b),
				_rng.randf_range(-0.6, 0.6))
		mi.position = base + off
		add_child(mi)
