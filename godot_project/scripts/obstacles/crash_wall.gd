extends StaticBody3D
## Heavy crash-test block (stacks into a wall).

const Helpers := preload("res://scripts/util/helpers.gd")
const COLORS := preload("res://scripts/util/colors.gd")


func setup(ob: Dictionary) -> void:
	position = Helpers.v3(ob["at"])
	var size: Vector3 = Helpers.v3(ob.get("size", [3.3, 1.9, 3.5]))
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
	mi.material_override = COLORS.mat_flat(ob.get("color", "#dee2e6"))
	add_child(mi)
	# hazard stripe
	var stripe := MeshInstance3D.new()
	var bm2 := BoxMesh.new()
	bm2.size = Vector3(0.15, size.y * 0.35, size.z * 0.8)
	stripe.mesh = bm2
	stripe.material_override = COLORS.mat_flat("#ffd166")
	stripe.position = Vector3(size.x / 2 + 0.08, 0, 0)
	add_child(stripe)
