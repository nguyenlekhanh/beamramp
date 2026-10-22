extends AnimatableBody3D
## Swinging hammer on a pivot, animated kinematically (solid to vehicles).

const Helpers := preload("res://scripts/util/helpers.gd")
const COLORS := preload("res://scripts/util/colors.gd")

var pivot: Node3D
var swing_sec: float = 2.6
var phase_deg: float = 0.0


func setup(ob: Dictionary) -> void:
	position = Helpers.v3(ob["at"])
	var s: float = ob.get("swing_sec", 2.6)
	swing_sec = maxf(s, 0.5)
	phase_deg = ob.get("phase_deg", 0.0)
	collision_layer = 4
	collision_mask = 0
	sync_to_physics = true
	pivot = Node3D.new()
	add_child(pivot)
	var arm_len: float = ob.get("arm_len", 9.0)
	var head_size: float = ob.get("head_size", 3.4)
	var arm := MeshInstance3D.new()
	var arm_mesh := BoxMesh.new()
	arm_mesh.size = Vector3(0.35, arm_len, 0.35)
	arm.mesh = arm_mesh
	arm.position = Vector3(0, -arm_len / 2.0, 0)
	arm.material_override = COLORS.mat_flat("#6c757d")
	pivot.add_child(arm)
	var head := MeshInstance3D.new()
	var head_mesh := BoxMesh.new()
	head_mesh.size = Vector3(head_size, head_size, head_size)
	head.mesh = head_mesh
	head.position = Vector3(0, -arm_len - head_size * 0.4, 0)
	head.material_override = COLORS.mat_flat("#e63946")
	pivot.add_child(head)
	var arm_shape := CollisionShape3D.new()
	var arm_box := BoxShape3D.new()
	arm_box.size = Vector3(0.4, arm_len, 0.4)
	arm_shape.shape = arm_box
	arm_shape.position = Vector3(0, -arm_len / 2.0, 0)
	pivot.add_child(arm_shape)
	var head_shape := CollisionShape3D.new()
	var head_box := BoxShape3D.new()
	head_box.size = Vector3(head_size, head_size, head_size)
	head_shape.shape = head_box
	head_shape.position = Vector3(0, -arm_len - head_size * 0.4, 0)
	pivot.add_child(head_shape)


func _process(_delta: float) -> void:
	var t := Time.get_ticks_msec() / 1000.0
	var ang := sin((t * TAU / swing_sec) + deg_to_rad(phase_deg)) * 1.15
	pivot.rotation.z = ang
