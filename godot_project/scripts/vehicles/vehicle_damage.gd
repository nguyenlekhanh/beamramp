extends Node3D
## Modular damage visuals: dents (paint darkening), bumper displacement,
## hood lift, wheel misalignment indicator on strong impacts. Physics body
## stays untouched; heavy impacts also detach a bumper as a visual-only chunk.

const VehicleScriptRef := preload("res://scripts/vehicles/vehicle.gd")

var car: RigidBody3D
var dims: Vector3
var bumper_front: Node3D = null
var bumper_rear: Node3D = null
var hood: Node3D = null
var dent_count: int = 0
var total_severity: float = 0.0


func wire(car_body: RigidBody3D, dims_vec: Vector3,
		front_bumper: Node3D = null, rear_bumper: Node3D = null, hood_ref: Node3D = null) -> void:
	car = car_body
	dims = dims_vec
	bumper_front = front_bumper
	bumper_rear = rear_bumper
	hood = hood_ref





func impact(severity: float, local_point: Vector3) -> void:
	total_severity += severity
	dent_count += 1
	if total_severity < 5.0:
		return
	var displaced: float = clampf(severity * 0.04, 0.02, 0.22)
	var side: float = signf(local_point.z)

	if side <= 0.0 and bumper_front != null:
		bumper_front.position.z -= displaced * 0.5
		bumper_front.rotation.x = -displaced * 0.5
		if severity > 11.0:
			_detach_visual_chunk(bumper_front, severity)
			bumper_front = null
	if side > 0.0 and bumper_rear != null:
		bumper_rear.position.z += displaced * 0.5
		bumper_rear.rotation.x = displaced * 0.5
		if severity > 11.0:
			_detach_visual_chunk(bumper_rear, severity)
			bumper_rear = null

	if severity > 7.0 and side <= 0.0 and hood != null:
		hood.rotation.x = -clampf(severity * 0.03, 0.05, 0.4)

	if dent_count == 4 and total_severity > 25.0:
		if car is VehicleScriptRef and car.wheels.size() > 0:
			var wheel: Node3D = car.wheels[0]
			wheel.rotation_degrees.y += 3.0


func _detach_visual_chunk(panel: Node3D, severity: float) -> void:
	if panel == null or not (panel is MeshInstance3D):
		return
	var mesh_mi := panel as MeshInstance3D
	var world_xform := mesh_mi.global_transform
	var chunk := RigidBody3D.new()
	chunk.collision_layer = 4
	chunk.collision_mask = 1 | 2
	chunk.mass = 12.0
	var cs := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(0.3, 0.3, 0.3)
	cs.shape = shape
	chunk.add_child(cs)
	var mi := MeshInstance3D.new()
	mi.mesh = mesh_mi.mesh
	mi.material_override = mesh_mi.material_override
	chunk.add_child(mi)
	chunk.global_position = world_xform.origin
	chunk.linear_velocity = (car.linear_velocity if car else Vector3.ZERO) * 0.5
	chunk.angular_velocity = Vector3(randf_range(-4, 4), 0, randf_range(-3, 3))
	var root := car.get_parent()
	if root:
		root.add_child(chunk)
	panel.queue_free()
