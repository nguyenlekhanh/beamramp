extends AnimatableBody3D
## Platform that slides back and forth across the road.

const Helpers := preload("res://scripts/util/helpers.gd")
const COLORS := preload("res://scripts/util/colors.gd")

var travel: Vector3 = Vector3.ZERO
var period_sec: float = 5.0
var base_pos: Vector3


func setup(ob: Dictionary) -> void:
	base_pos = Helpers.v3(ob["at"])
	position = base_pos
	travel = Helpers.v3(ob.get("travel", [0, 0, 8]))
	period_sec = maxf(ob.get("period_sec", 5.0), 0.5)
	collision_layer = 4
	collision_mask = 0
	sync_to_physics = true
	var size: Vector3 = Helpers.v3(ob.get("size", [6, 2, 2]))
	var cs := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = size
	cs.shape = shape
	add_child(cs)
	var mi := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = size
	mi.mesh = bm
	mi.material_override = COLORS.mat_flat(ob.get("color", "#8338ec"))
	add_child(mi)


func _process(_delta: float) -> void:
	var t := Time.get_ticks_msec() / 1000.0
	var s := sin(TAU * t / period_sec)
	position = base_pos + travel * s
