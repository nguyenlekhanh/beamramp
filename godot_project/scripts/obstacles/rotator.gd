extends AnimatableBody3D
## Rotating flat barrier.
##
## M3.8B: rotation is derived from an accumulated PHYSICS-time counter
## (_physics_process ticks) rather than _process wall-clock delta, so the
## bar angle at any sim time is deterministic under both headless probes
## (which iterate faster than real time) and Movie Maker --fixed-fps
## renders, and stays in lockstep with vehicle physics during
## Engine.time_scale slowmo windows. The transform is still written in
## _process only, as required for AnimatableBody3D with sync_to_physics.

const Helpers := preload("res://scripts/util/helpers.gd")
const COLORS := preload("res://scripts/util/colors.gd")

var rpm: float = 20.0
var _phys_time: float = 0.0


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


func _physics_process(delta: float) -> void:
	# delta follows the physics timeline (constant tick * time_scale),
	# so _phys_time equals simulation time regardless of frame pacing.
	_phys_time += delta


func _process(_delta: float) -> void:
	rotation.y = deg_to_rad(rpm * 6.0) * _phys_time
