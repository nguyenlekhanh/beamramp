extends RigidBody3D
## Traffic cone (knockable).

const Helpers := preload("res://scripts/util/helpers.gd")
const COLORS := preload("res://scripts/util/colors.gd")

const CONE_H = 1.2
const CONE_R = 0.5


func setup(ob: Dictionary) -> void:
	position = Helpers.v3(ob["at"])
	collision_layer = 4
	collision_mask = 1 | 2
	mass = 30.0
	var cs := CollisionShape3D.new()
	var shape := CylinderShape3D.new()
	shape.height = CONE_H
	shape.radius = CONE_R
	cs.shape = shape
	cs.position = Vector3(0, CONE_H / 2.0, 0)
	add_child(cs)
	var mi := MeshInstance3D.new()
	var cm := CylinderMesh.new()
	cm.top_radius = 0.12
	cm.bottom_radius = CONE_R
	cm.height = CONE_H
	cm.radial_segments = 12
	mi.mesh = cm
	mi.material_override = COLORS.mat_flat("#ff6b35")
	mi.position = Vector3(0, CONE_H / 2.0, 0)
	add_child(mi)
