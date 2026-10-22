extends AnimatableBody3D
## Rotating flat barrier.

const Helpers := preload("res://scripts/util/helpers.gd")
const COLORS := preload("res://scripts/util/colors.gd")

var rpm: float = 20.0


func setup(ob: Dictionary) -> void:
	position = Helpers.v3(ob["at"])
	rpm = ob.get("rpm", 20.0)
	collision_layer = 4
	collision_mask = 0
	sync_to_physics = true
	var size: Vector3 = Helpers.v3(ob.get("size", [3, 0.6, 12]))
	var cs := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = size
	cs.shape = shape
	add_child(cs)
	var mi := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = size
	mi.mesh = bm
	mi.material_override = COLORS.mat_flat(ob.get("color", "#ff595e"))
	add_child(mi)


func _process(delta: float) -> void:
	rotation.y += deg_to_rad(rpm * 6.0) * delta
