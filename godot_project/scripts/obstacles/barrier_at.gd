extends StaticBody3D
## Concrete-style barrier block.

const Helpers := preload("res://scripts/util/helpers.gd")
const COLORS := preload("res://scripts/util/colors.gd")


func setup(ob: Dictionary) -> void:
	position = Helpers.v3(ob["at"])
	if ob.has("rot_deg"):
		rotation_degrees = Helpers.v3(ob["rot_deg"])
	var size: Vector3 = Helpers.v3(ob.get("size", [1.0, 1.0, 3.0]))
	collision_layer = 1
	collision_mask = 0
	var cs := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = size
	cs.shape = shape
	add_child(cs)
	var mi := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = size
	mi.mesh = bm
	mi.material_override = COLORS.mat_flat(ob.get("color", "#adb5bd"))
	add_child(mi)
