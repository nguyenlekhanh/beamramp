extends Node3D
class_name VehicleWheelVisual
## Visual wheel wrapper: steering (yaw) on wrapper, roll on inner spinner.

const COLORS := preload("res://scripts/util/colors.gd")

var spin_node: Node3D
var mesh_node: MeshInstance3D
var spin: float = 0.0
var wheel_color: String = "#22223b"


func build(radius: float, width: float, color_hex: String) -> void:
	wheel_color = color_hex
	spin_node = Node3D.new()
	add_child(spin_node)
	mesh_node = MeshInstance3D.new()
	var cm := CylinderMesh.new()
	cm.top_radius = radius
	cm.bottom_radius = radius
	cm.height = width
	cm.radial_segments = 16
	mesh_node.mesh = cm
	mesh_node.rotation = Vector3(0, 0, PI / 2.0)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(color_hex)
	mat.roughness = 0.85
	mesh_node.material_override = mat
	spin_node.add_child(mesh_node)
	# rim detail (small contrasting hub)
	var hub := MeshInstance3D.new()
	var hm := CylinderMesh.new()
	hm.top_radius = radius * 0.45
	hm.bottom_radius = radius * 0.45
	hm.height = width * 1.1
	hub.mesh = hm
	hub.rotation = Vector3(0, 0, PI / 2.0)
	var hubmat := StandardMaterial3D.new()
	hubmat.albedo_color = Color("#c8c8c8")
	hubmat.roughness = 0.3
	hub.material_override = hubmat
	spin_node.add_child(hub)


func update_state(steer_angle: float, body_speed: float, delta: float) -> void:
	rotation.y = steer_angle
	spin += (body_speed / maxf(0.05, 0.34)) * delta
	spin_node.rotation.x = spin
