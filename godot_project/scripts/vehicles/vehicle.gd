extends RigidBody3D
## Physics-based vehicle with raycast suspension on 4 corners, per-wheel
## traction/brake, lateral grip impulses, steering, air control and reset
## recovery only (no routine teleporting). Visual parts (wheels, panels) are
## attached by car_factory.

class_name VehicleMain

signal crashed(body: Node3D, severity: float)
signal landed(impact_speed: float)
signal fell()
signal finished()
signal launch_hit()

const COLORS := preload("res://scripts/util/colors.gd")

var spec: Dictionary = {}
var damage: Node3D = null
var dims: Vector3 = Vector3(1.9, 0.9, 4.2)
var wheel_radius: float = 0.34
var wheel_width: float = 0.26
var wheel_y: float = -0.2

var rest_length: float = 0.5
var stiffness: float = 40000.0
var damping: float = 3800.0
var max_steer: float = 0.55
var max_speed: float = 55.0
var engine_accel: float = 9.0
var brake_accel: float = 12.0
var grip: float = 1.0
var side_grip: float = 0.45
var style_speed_map := {"cautious": 0.72, "balanced": 0.88, "aggressive": 1.0}

var steer_angle: float = 0.0
var target_steer: float = 0.0
var throttle: float = 0.0
var brake: float = 0.0
var wheels: Array = []
var suspension_info: Array = [false, false, false, false]
var grounded_count: int = 0
var airtime: float = 0.0
var velocity_prev: Vector3 = Vector3.ZERO
var velocity_pre_contact: Vector3 = Vector3.ZERO
var launch_flag: bool = false
var finished_flag: bool = false
var recovery_enabled: bool = true
var respawn_point: Vector3 = Vector3.ZERO
var respawn_yaw: float = -90.0
var stuck_timer: float = 0.0
var roll_timer: float = 0.0
var tick: int = 0
var mass_hint: float = 1250.0
var car_label: String = ""
var car_color: String = "#e63946"


func _ready() -> void:
	# Re-apply physics params after entering the tree so the physics server
	# sees the real mass immediately (setting mass pre-add is unreliable).
	mass = mass_hint


func configure(spec_dict: Dictionary, factory_cfg: Dictionary) -> void:
	spec = spec_dict.duplicate()
	mass = factory_cfg["mass"]
	if spec_dict.has("mass_override") and spec_dict["mass_override"] != null:
		mass = float(spec_dict["mass_override"])
	dims = factory_cfg["dims"]
	wheel_radius = factory_cfg["wheel_radius"]
	wheel_width = factory_cfg.get("wheel_width", 0.26)
	wheel_y = factory_cfg.get("wheel_y", -dims.y * 0.25)
	rest_length = factory_cfg.get("rest_length", 0.5)
	stiffness = mass * factory_cfg.get("stiffness_per_kg", 46.0) / 4.0
	damping = 2.0 * sqrt(stiffness * mass / 4.0) * factory_cfg.get("damping_ratio", 0.5)
	max_steer = factory_cfg.get("max_steer", 0.55)
	max_speed = factory_cfg["max_speed"]
	engine_accel = factory_cfg["accel"]
	brake_accel = factory_cfg["brake"]
	grip = factory_cfg.get("grip", 1.0)
	side_grip = factory_cfg.get("side_grip", 0.45)
	recovery_enabled = bool(factory_cfg.get("recovery", true))
	car_label = str(spec_dict.get("label", "Car"))
	car_color = spec_dict.get("color", "#e63946")

	collision_layer = 2
	collision_mask = 1 | 2 | 4
	contact_monitor = true
	max_contacts_reported = 8
	center_of_mass_mode = RigidBody3D.CENTER_OF_MASS_MODE_CUSTOM
	center_of_mass = Vector3(0, factory_cfg.get("com_y", -dims.y * 0.3), 0)
	angular_damp = factory_cfg.get("angular_damp", 1.4)
	linear_damp = 0.05
	can_sleep = false
	physics_material_override = PhysicsMaterial.new()
	physics_material_override.friction = factory_cfg.get("body_friction", 0.7)
	physics_material_override.bounce = 0.05

	var collision_box := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = dims
	collision_box.shape = box
	collision_box.position = Vector3(0, dims.y * 0.5, 0)
	add_child(collision_box)
	body_entered.connect(_on_body_entered)

	respawn_point = global_position
	respawn_yaw = rotation.y


func _on_body_entered(body: Node) -> void:
	if body.name == "Ground":
		_handle_ground_touch()
		return
	if body is VehicleMain and (body as VehicleMain).crash_group_self_ignored():
		pass
	var rel_speed := (velocity_pre_contact - velocity_prev).length()
	if rel_speed < 2.0:
		return
	crashed.emit(body, rel_speed)
	_apply_damage_from_collision(body, rel_speed)


func crash_group_self_ignored() -> bool:
	return false


func _handle_ground_touch() -> void:
	if airtime > 0.3:
		var down_speed := -velocity_pre_contact.y
		if down_speed > 2.0:
			landed.emit(down_speed)


func _physics_process(delta: float) -> void:
	velocity_pre_contact = velocity_prev
	velocity_prev = linear_velocity
	var dbg := OS.get_environment("CRS_VEH_DEBUG") == "1" and tick < 10
	if dbg:
		var srv_state := PhysicsServer3D.body_get_direct_state(get_rid())
		print("[veh %d] uniform mass=%s inv%s serverMass=%s delta=%.4f" % [tick, str(mass), str(center_of_mass), "1/inv_mass=?" if false else str(1.0 / srv_state.inverse_mass), delta])
	tick += 1
	var fwd := -global_basis.z
	var body_speed := linear_velocity.dot(fwd)
	steer_angle = lerpf(steer_angle, target_steer, clampf(4.0 * delta, 0.0, 1.0))

	var grounded_now := false
	for i in range(4):
		var hit := _resolve_wheel(i, delta, body_speed)
		suspension_info[i] = hit
		grounded_now = grounded_now or hit
	grounded_count = int(suspension_info[0]) + int(suspension_info[1]) + int(suspension_info[2]) + int(suspension_info[3])
	if dbg:
		print("[veh %d] post vel=%v succ=%s" % [tick - 1, linear_velocity, str([suspension_info[0], suspension_info[1], suspension_info[2], suspension_info[3]])])
	if dbg:
		var inv := PhysicsServer3D.body_get_direct_state(get_rid()).inverse_mass
		print("[veh %d] inv mass=%.4f" % [tick, inv])

	if grounded_now:
		if airtime > 0.3:
			_handle_ground_touch()
		airtime = 0.0
		_apply_drive(delta, body_speed, fwd)
	else:
		airtime += delta
		_apply_air_control(delta)

	_update_wheels_visual(delta, body_speed)
	_check_life_signs(delta, body_speed)


func _resolve_wheel(i: int, delta: float, body_speed: float) -> bool:
	var fwd := -global_basis.z
	var half_w: float = dims.x / 2.0 - 0.16
	var half_front: float = dims.z / 2.0 - 0.65
	var half_rear: float = dims.z / 2.0 - 0.75
	var corner_local := Vector3(
		-half_w if i in [0, 2] else half_w,
		wheel_y,
		-half_front if i in [0, 1] else half_rear
	)
	var ray_from: Vector3 = to_global(corner_local + Vector3.UP * 0.2) + global_basis.y * 1.0
	var ray_len: float = rest_length + wheel_radius
	var ray_to: Vector3 = ray_from - global_basis.y * (ray_len + 1.0)
	var query := PhysicsRayQueryParameters3D.create(ray_from, ray_to, 1 | 4, [get_rid()])
	var space := get_world_3d().direct_space_state
	var hit := space.intersect_ray(query)
	if hit.is_empty():
		return false
	var contact: Vector3 = hit["position"]
	var dist: float = (ray_from - contact).length() - 1.0
	var comp: float = (ray_len - dist) / ray_len
	if OS.get_environment("CRS_VEH_DEBUG") == "1" and tick < 8:
		print("[veh w%d] dist=%.3f comp=%.3f" % [i, (ray_from - contact).length(), comp])
	comp = clampf(comp, 0.0, 1.0)
	# warmup: physics server syncs mass one tick late; skip impulses early
	if tick < 3:
		return true
	var up_body: Vector3 = global_basis.y
	var point_vel: Vector3 = _point_velocity(to_global(corner_local))
	var comp_vel := point_vel.dot(up_body)
	var force := stiffness * comp - damping * comp_vel
	force = clampf(force, 0.0, mass * 9.8 * 2.2 / 4.0)
	if OS.get_environment("CRS_NO_WHEEL") != "1":
		apply_impulse(up_body * force * delta, contact - global_position)

	# lateral grip impulse — acts ONLY perpendicular to body-up and body-forward
	var fwd_flat: Vector3 = (-global_basis.z).normalized()
	if i < 2:
		fwd_flat = fwd_flat.rotated(up_body, steer_angle)
	fwd_flat = (fwd_flat - up_body * fwd_flat.dot(up_body)).normalized()
	var lat_dir: Vector3 = fwd_flat.cross(up_body).normalized()
	var v_lat_scalar: float = point_vel.dot(lat_dir)
	var lat_imp: Vector3 = lat_dir * (-v_lat_scalar) * (mass / 4.0) * side_grip * grip
	var max_lat := mass * 9.8 * 1.4 * 0.25 * grip
	lat_imp = lat_imp.limit_length(max_lat)
	if OS.get_environment("CRS_NO_WHEEL") != "1":
		apply_impulse(lat_imp, contact - global_position)
	return true


func _point_velocity(world_point: Vector3) -> Vector3:
	var rel := world_point - global_position
	return linear_velocity + angular_velocity.cross(rel)


func _apply_drive(delta: float, body_speed: float, fwd: Vector3) -> void:
	var max_eff_speed := max_speed * float(style_speed_map.get(_style(), 0.9))
	var desired_accel := throttle * engine_accel - brake * brake_accel
	if body_speed > max_eff_speed:
		desired_accel = minf(desired_accel, -0.5)
	var force_length := absf(desired_accel) * mass
	for i in range(4):
		if not suspension_info[i]:
			continue
		var push_dir: Vector3
		if desired_accel >= 0.0:
			# throttle has no meaning without forward grip direction
			push_dir = fwd
			if i < 2:
				push_dir = fwd.rotated(global_basis.y, steer_angle)
		else:
			# braking must oppose the actual velocity, not the body axis:
			# a spun-around car would otherwise be "braked" as reverse thrust
			var vel_flat := Vector3(linear_velocity.x, 0.0, linear_velocity.z)
			if vel_flat.length() < 0.4:
				push_dir = Vector3.ZERO
			else:
				push_dir = -vel_flat.normalized()
		if push_dir != Vector3.ZERO:
			apply_central_force(push_dir * force_length * 0.25)


func _apply_air_control(delta: float) -> void:
	# gentle stabilization so jumps do not nosedive unnaturally
	var pitch_rate := clampf(-angular_velocity.x, -0.35, 0.35)
	apply_torque(-global_basis.x * pitch_rate * mass * 0.03)


func _check_life_signs(delta: float, body_speed: float) -> void:
	if finished_flag:
		# parked on purpose at the end of the run: never "rescued" out of it
		stuck_timer = 0.0
		roll_timer = 0.0
		if global_position.y < -12.0:
			fell.emit()
		return
	if global_position.y < -8.0:
		fell.emit()
		if recovery_enabled:
			_teleport_to_respawn()
		return
	var up_y := global_basis.y.y
	if up_y < 0.15 and body_speed < 3.0:
		roll_timer += delta
		if roll_timer > 2.2 and recovery_enabled:
			_teleport_to_respawn()
	else:
		roll_timer = 0.0
	if grounded_count >= 3 and body_speed < 1.4 and absf(throttle) < 0.05:
		stuck_timer += delta
		if stuck_timer > 2.8 and recovery_enabled:
			_teleport_to_respawn()
	else:
		stuck_timer = 0.0


func _teleport_to_respawn() -> void:
	linear_velocity = Vector3.ZERO
	angular_velocity = Vector3.ZERO
	global_position = respawn_point + Vector3(0, 0.8, 0)
	rotation = Vector3(0, respawn_yaw, 0)
	stuck_timer = 0.0
	roll_timer = 0.0


## Recovery hook for externally detected failures (AI reports it lost
## off-road far from the racing line); same reset path as fall/roll/stuck.
func recover_to_spawn() -> void:
	_teleport_to_respawn()


func set_respawn(pos: Vector3, yaw: float) -> void:
	respawn_point = pos
	respawn_yaw = yaw


## World-space Y at which the car rests with equilibrium suspension compression.
func preferred_spawn_y() -> float:
	var ray_len: float = rest_length + wheel_radius
	var eq_compression: float = (mass * 9.8 / 4.0) / stiffness
	return ray_len - eq_compression - (wheel_y + 0.2)


func _update_wheels_visual(delta: float, body_speed: float) -> void:
	for wheel in wheels:
		if wheel.has_method("update_state"):
			wheel.update_state(steer_angle, body_speed, delta)


func _apply_damage_from_collision(body: Node, severity: float) -> void:
	if damage == null or severity < 4.0:
		return
	var local_point := to_local(global_position)
	if body is Node3D:
		local_point = to_local((body as Node3D).global_position)
	damage.impact(severity, local_point)


func set_target_steer(value: float) -> void:
	target_steer = clampf(value, -max_steer, max_steer)


func _style() -> String:
	return str(spec.get("style", "balanced"))


func _flag_launch() -> void:
	launch_flag = true
	launch_hit.emit()
