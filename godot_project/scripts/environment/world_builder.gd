extends Node3D
## Builds sky, sun, ambient environment, and endless ground plate.

class_name WorldBuilder

const COLORS := preload("res://scripts/util/colors.gd")


func build_environment(scenario: Dictionary, movie_mode: bool = false) -> Node3D:
	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.ambient_light_sky_contribution = 1.0
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	env.tonemap_white = 4.0
	env.glow_enabled = false
	var meta: Dictionary = scenario.get("layout", {}).get("meta", {})
	var sky_colors: Dictionary = meta.get("sky", {})
	var top := Color(sky_colors.get("top_color", "#8ecae6"))
	var ground := Color(sky_colors.get("ground_color", "#f1faee"))
	var sky := Sky.new()
	var mat := ProceduralSkyMaterial.new()
	mat.sky_top_color = top
	mat.sky_horizon_color = top.lerp(Color.WHITE, 0.35)
	mat.ground_bottom_color = Color("#b5c99a")
	mat.ground_horizon_color = ground
	sky.sky_material = mat
	env.sky = sky
	env.fog_enabled = false

	var wel := WorldEnvironment.new()
	wel.environment = env
	add_child(wel)

	var sun := DirectionalLight3D.new()
	var sun_meta: Dictionary = meta.get("sun", {})
	var rot: Array = sun_meta.get("rotation_deg", [-38.0, -25.0])
	sun.rotation_degrees = Vector3(rot[0], rot[1], 0.0)
	sun.light_energy = float(sun_meta.get("energy", 1.2))
	sun.light_color = Color("#fff8f0")
	sun.shadow_enabled = true
	add_child(sun)

	# Endless-feel world: a large base ground slab far beyond the arena
	# so wide shots never fall off into the void, plus the arena plate on top.
	var arena: Array = meta.get("arena", {}).get("size", [260.0, 120.0])
	var size_x: float = maxf(arena[0] * 2.2, 600.0)
	var size_z: float = maxf(arena[1] * 2.2, 400.0)
	# wide base slab (visual only, top flush under the arena plate)
	var base_body := StaticBody3D.new()
	base_body.name = "GroundBase"
	base_body.collision_layer = 1
	base_body.collision_mask = 0
	var base_mesh := BoxMesh.new()
	base_mesh.size = Vector3(size_x, 1.0, size_z)
	var base_mi := MeshInstance3D.new()
	base_mi.mesh = base_mesh
	base_mi.material_override = COLORS.mat_flat("#a7c957")
	base_mi.position = Vector3(0, -0.51, 0)
	base_body.add_child(base_mi)
	add_child(base_body)
	# arena collision plate on top
	var ground_body := StaticBody3D.new()
	ground_body.name = "Ground"
	ground_body.collision_layer = 1
	ground_body.collision_mask = 0
	var shape := BoxShape3D.new()
	shape.size = Vector3(size_x, 1.0, size_z)
	var cs := CollisionShape3D.new()
	cs.shape = shape
	cs.position = Vector3(0, -0.5, 0)
	ground_body.add_child(cs)
	var mesh := BoxMesh.new()
	mesh.size = Vector3(size_x, 1.0, size_z)
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = COLORS.mat_flat("#a7c957")
	mi.position = Vector3(0, -0.5, 0)
	ground_body.add_child(mi)
	add_child(ground_body)
	return self
