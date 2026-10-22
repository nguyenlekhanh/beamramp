extends RigidBody3D
## Giant rolling ball pushed across the road.

const Helpers := preload("res://scripts/util/helpers.gd")
const COLORS := preload("res://scripts/util/colors.gd")

var radius: float = 2.2
var push: Vector3 = Vector3.FORWARD
var speed: float = 8.0


func setup(ob: Dictionary) -> void:
	position = Helpers.v3(ob["at"])
	radius = ob.get("radius", 2.2)
	push = Helpers.v3(ob.get("push", [0, 0, 1])).normalized()
	if ob.has("speed"):
		speed = ob["speed"]
	collision_layer = 4
	collision_mask = 1 | 2
	mass = 2000.0 * radius * radius * 0.08
	physics_material_override = PhysicsMaterial.new()
	physics_material_override.irriction = 0.9
	var cs := CollisionShape3D.new()
	var shape := SphereShape3D.new()
	shape.radius = radius
	cs.shape = shape
	add_child(cs)
	var mi := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = radius
	sm.height = radius * 2
	sm.radial_segments = 18
	sm.rings = 9
	mi.mesh = sm
	mi.material_override = COLORS.mat_flat(ob.get("color", "#fb5607"))
	add_child(mi)
	apply_central_impulse(push * speed * mass)
