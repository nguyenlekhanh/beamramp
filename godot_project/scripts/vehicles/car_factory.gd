extends Node
## Car factory: creates complete vehicle nodes (RigidBody + visuals + wheels)
## from spec dicts. Types: sports, compact, bus, truck, monster.

const COLORS := preload("res://scripts/util/colors.gd")
const VehicleScript := preload("res://scripts/vehicles/vehicle.gd")
const WheelScript := preload("res://scripts/vehicles/vehicle_wheel_visual.gd")
const VehicleDamage := preload("res://scripts/vehicles/vehicle_damage.gd")

const TYPE_SPECS := {
	"sports": {
		"dims": [1.9, 0.85, 4.2], "wheel_radius": 0.34, "wheel_width": 0.26,
		"mass": 1250.0, "max_speed": 46.0, "accel": 10.5, "brake": 14.0,
		"max_steer": 0.55, "com_y": -0.35, "stiffness_per_kg": 52.0, "damping_ratio": 0.55,
		"grip": 1.0, "side_grip": 0.42, "cabin": [0.8, 1.7, 2.2],
	},
	"compact": {
		"dims": [1.75, 0.9, 3.7], "wheel_radius": 0.31, "wheel_width": 0.24,
		"mass": 1050.0, "max_speed": 38.0, "accel": 8.0, "brake": 13.0,
		"max_steer": 0.58, "com_y": -0.32, "stiffness_per_kg": 50.0, "damping_ratio": 0.55,
		"grip": 0.95, "side_grip": 0.45, "cabin": [0.85, 1.45, 1.7],
	},
	"bus": {
		"dims": [2.5, 2.0, 9.0], "wheel_radius": 0.5, "wheel_width": 0.32,
		"mass": 9000.0, "max_speed": 24.0, "accel": 4.5, "brake": 8.0,
		"max_steer": 0.45, "com_y": -0.5, "stiffness_per_kg": 42.0, "damping_ratio": 0.62,
		"grip": 0.8, "side_grip": 0.36, "cabin": null,
	},
	"truck": {
		"dims": [2.3, 1.6, 7.2], "wheel_radius": 0.45, "wheel_width": 0.3,
		"mass": 6000.0, "max_speed": 30.0, "accel": 5.5, "brake": 9.0,
		"max_steer": 0.48, "com_y": -0.4, "stiffness_per_kg": 44.0, "damping_ratio": 0.62,
		"grip": 0.85, "side_grip": 0.38, "cabin": [0.9, 1.55, 2.4],
	},
	"monster": {
		"dims": [2.7, 1.5, 4.8], "wheel_radius": 0.92, "wheel_width": 0.5,
		"mass": 3200.0, "max_speed": 40.0, "accel": 9.0, "brake": 15.0,
		"max_steer": 0.55, "com_y": -0.3, "stiffness_per_kg": 58.0, "damping_ratio": 0.6,
		"grip": 0.95, "side_grip": 0.4, "cabin": [0.9, 1.05, 1.7],
		"wheel_y": -0.4, "rest_length": 0.75,
	},
}


func build(spec_dict: Dictionary) -> RigidBody3D:
	var type_name: String = str(spec_dict.get("type", "sports"))
	var cfg: Dictionary = TYPE_SPECS.get(type_name, TYPE_SPECS["sports"])
	var dims := Vector3(cfg["dims"][0], cfg["dims"][1], cfg["dims"][2])
	var paint: String = spec_dict.get("color", "#e63946")
	var wheel_color: String = spec_dict.get("wheel_color", "#22223b")

	var car := VehicleScript.new()
	car.name = "CarBody"
	car.car_label = str(spec_dict.get("label", "Car"))
	car.car_color = paint
	car.spec = spec_dict.duplicate()
	car.dims = dims
	car.wheel_radius = cfg["wheel_radius"]
	car.wheel_width = cfg["wheel_width"]
	car.wheel_y = cfg.get("wheel_y", -dims.y * 0.28)
	car.max_speed = cfg["max_speed"]
	car.engine_accel = cfg["accel"]
	car.brake_accel = cfg["brake"]
	car.max_steer = cfg["max_steer"]
	car.rest_length = cfg.get("rest_length", 0.5)
	car.stiffness = cfg["mass"] * cfg["stiffness_per_kg"] / 4.0
	car.mass_hint = cfg["mass"]
	if spec_dict.has("mass_override") and spec_dict["mass_override"] != null:
		car.mass_hint = float(spec_dict["mass_override"])
	car.mass = car.mass_hint
	car.damping = 2.0 * sqrt(car.stiffness * car.mass / 4.0) * cfg["damping_ratio"]
	car.grip = cfg["grip"]
	car.side_grip = cfg["side_grip"]

	_apply_body_visuals(car, cfg, dims, paint)

	car.collision_layer = 2
	car.collision_mask = 1 | 2 | 4
	car.contact_monitor = true
	car.max_contacts_reported = 8
	car.center_of_mass_mode = RigidBody3D.CENTER_OF_MASS_MODE_CUSTOM
	car.center_of_mass = Vector3(0, cfg["com_y"], 0)
	car.angular_damp = 1.4
	car.linear_damp = 0.05
	car.physics_material_override = PhysicsMaterial.new()
	car.physics_material_override.friction = 0.7
	car.physics_material_override.bounce = 0.05

	var shape := CollisionShape3D.new()
	var cs := BoxShape3D.new()
	cs.size = dims
	shape.shape = cs
	shape.position = Vector3(0, dims.y * 0.5, 0)
	car.add_child(shape)

	var wheels: Array = []
	var half_w: float = dims.x / 2.0 + 0.02
	var hfront: float = dims.z / 2.0 - cfg["wheel_radius"] * 1.25
	var hrear: float = dims.z / 2.0 - cfg["wheel_radius"] * 1.25
	var tmp_vis := WheelScript.new()
	car.add_child(tmp_vis)
	_build_wheel(tmp_vis, cfg, wheel_color)
	tmp_vis.position = Vector3(-half_w, car.wheel_y, -hfront)
	wheels.append(tmp_vis)
	var vis2 := WheelScript.new()
	car.add_child(vis2)
	_build_wheel(vis2, cfg, wheel_color)
	vis2.position = Vector3(half_w, car.wheel_y, -hfront)
	wheels.append(vis2)
	var vis3 := WheelScript.new()
	car.add_child(vis3)
	_build_wheel(vis3, cfg, wheel_color)
	vis3.position = Vector3(-half_w, car.wheel_y, hrear)
	wheels.append(vis3)
	var vis4 := WheelScript.new()
	car.add_child(vis4)
	_build_wheel(vis4, cfg, wheel_color)
	vis4.position = Vector3(half_w, car.wheel_y, hrear)
	wheels.append(vis4)
	car.wheels = wheels

	var dmg := VehicleDamage.new()
	car.add_child(dmg)
	dmg.wire(car, dims, car.get_meta("bumper_front"), car.get_meta("bumper_rear"), car.get_meta("hood"))
	car.damage = dmg

	car.body_entered.connect(car._on_body_entered)
	return car


func _build_wheel(wheel: Node3D, cfg: Dictionary, wheel_color: String) -> void:
	wheel.build(cfg["wheel_radius"], cfg["wheel_width"], wheel_color)


func _apply_body_visuals(car: RigidBody3D, cfg: Dictionary, dims: Vector3, paint: String) -> void:
	var paint_mat := StandardMaterial3D.new()
	paint_mat.albedo_color = Color(paint)
	paint_mat.roughness = 0.3
	paint_mat.metallic = 0.08
	var dark_mat := StandardMaterial3D.new()
	dark_mat.albedo_color = Color(0.16, 0.17, 0.2)
	dark_mat.roughness = 0.7

	var shell := MeshInstance3D.new()
	var shell_mesh := BoxMesh.new()
	# monster sits high: shell lifted above wheels; others low
	var shell_lift: float = absf(car.wheel_y) + cfg["wheel_radius"] * 0.7 if str(cfg.get("wheel_y", "")) != "" else dims.y * 0.35
	if type_of_shell_low(cfg):
		shell_lift = dims.y * 0.32
	shell_mesh.size = Vector3(dims.x, dims.y * 0.72, dims.z * 0.98)
	shell.mesh = shell_mesh
	shell.material_override = paint_mat
	shell.position = Vector3(0, shell_lift, 0)
	car.add_child(shell)

	var cabin: Array = cfg["cabin"]
	if cabin != null:
		# cabin height scales with body height (specs stored wrong scale)
		var cabin_h: float = dims.y * 0.6
		var cabin_len: float = clampf(cabin[2], dims.z * 0.3, dims.z * 0.45)
		var cabin_top: float = shell_lift + dims.y * 0.36
		var cabin_y: float = cabin_top + cabin_h * 0.5 - 0.03
		var cabin_mi := MeshInstance3D.new()
		var cabin_mesh := BoxMesh.new()
		cabin_mesh.size = Vector3(dims.x * 0.92, cabin_h, cabin_len)
		cabin_mi.mesh = cabin_mesh
		cabin_mi.material_override = paint_mat
		cabin_mi.position = Vector3(0, cabin_y, -dims.z * 0.12)
		car.add_child(cabin_mi)
		var glass := StandardMaterial3D.new()
		glass.albedo_color = Color(0.08, 0.11, 0.14)
		glass.metallic = 0.5
		glass.roughness = 0.12
		var window_i := MeshInstance3D.new()
		var window_mesh := BoxMesh.new()
		window_mesh.size = Vector3(dims.x * 0.86, cabin_h * 0.6, 0.06)
		window_i.mesh = window_mesh
		window_i.material_override = glass
		window_i.position = Vector3(0, cabin_y, cabin_mi.position.z - cabin_len * 0.5)
		car.add_child(window_i)
		car.set_meta("cabin_mi", cabin_mi)

	if type_of_cargo(cfg):
		var cargo := MeshInstance3D.new()
		var cargo_mesh := BoxMesh.new()
		var cargo_len: float = dims.z * 0.42
		cargo_mesh.size = Vector3(dims.x * 0.98, dims.y * 0.85, cargo_len)
		cargo.mesh = cargo_mesh
		cargo.material_override = paint_mat
		cargo.position = Vector3(0, shell_lift + dims.y * 0.3, dims.z * 0.26)
		car.add_child(cargo)

	# bumpers
	var bumper_mesh := BoxMesh.new()
	bumper_mesh.size = Vector3(dims.x * 0.94, 0.3, 0.24)
	var bumper_front := MeshInstance3D.new()
	bumper_front.mesh = bumper_mesh
	bumper_front.material_override = dark_mat
	bumper_front.position = Vector3(0, shell_lift, -dims.z * 0.5)
	car.add_child(bumper_front)
	var bumper_rear := MeshInstance3D.new()
	bumper_rear.mesh = bumper_mesh
	bumper_rear.material_override = dark_mat
	bumper_rear.position = Vector3(0, shell_lift, dims.z * 0.5)
	car.add_child(bumper_rear)

	# mirrors (small wing blades) for readability
	for side in [-1.0, 1.0]:
		var mirror := MeshInstance3D.new()
		var mm := BoxMesh.new()
		mm.size = Vector3(0.1, 0.1, 0.34)
		mirror.mesh = mm
		mirror.material_override = dark_mat
		mirror.position = Vector3(side * dims.x * 0.55, shell_lift + dims.y * 0.35, -dims.z * 0.18)
		car.add_child(mirror)

	# hood
	var hood := MeshInstance3D.new()
	var hood_mesh := BoxMesh.new()
	hood_mesh.size = Vector3(dims.x * 0.88, 0.12, dims.z * 0.22)
	hood.mesh = hood_mesh
	hood.material_override = paint_mat
	hood.position = Vector3(0, shell_lift + dims.y * 0.2, -dims.z * 0.32)
	car.add_child(hood)

	# lights
	_add_light_pair(car, Vector3(0, shell_lift + dims.y * 0.05, -dims.z * 0.49), dims, Color(1, 1, 0.85))
	_add_light_pair(car, Vector3(0, shell_lift + dims.y * 0.05, dims.z * 0.49), dims, Color(1, 0.18, 0.18))

	car.set_meta("bumper_front", bumper_front)
	car.set_meta("bumper_rear", bumper_rear)
	car.set_meta("hood", hood)


func type_of_shell_low(cfg: Dictionary) -> bool:
	return not cfg.has("wheel_y")


func type_of_cargo(cfg: Dictionary) -> bool:
	return not cfg["cabin"] == null and cfg["dims"][2] > 5.5


func _add_light_pair(car: RigidBody3D, pos: Vector3, dims: Vector3, color: Color) -> void:
	for side in [-1.0, 1.0]:
		var lamp := MeshInstance3D.new()
		var lm := BoxMesh.new()
		lm.size = Vector3(0.26, 0.13, 0.08)
		lamp.mesh = lm
		var mat := StandardMaterial3D.new()
		mat.albedo_color = color
		mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		mat.emission_enabled = true
		mat.emission = color
		mat.emission_energy_multiplier = 2.0
		lamp.material_override = mat
		lamp.position = Vector3(pos.x + side * dims.x * 0.38, pos.y, pos.z)
		car.add_child(lamp)


func _wheel_ring_visual(car: RigidBody3D, _d: Vector3) -> void:
	pass
