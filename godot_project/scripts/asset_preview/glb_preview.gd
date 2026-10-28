extends Node3D
## Isolated external-asset preview (M3.6A).
##
## Loads ONE user GLB into a scaffold scene (floor + sun + sky) with
## configurable scale and pitch/yaw offsets, auto-frames it with a camera,
## and emits a machine-readable compatibility report in headless mode.
##
## It NEVER touches the production pipeline: main.tscn, vehicles, AI,
## camera direction, audio, recording and batch rendering are untouched.
## A successful load proves visual instantiation ONLY - it says nothing
## about production readiness, wheel placement or physics/collision JSON.
##
## Config (CRS_ASSET_PREVIEW env var -> absolute path of a JSON file):
##   { "glb": "<abs path>", "scale": 1.0, "pitch_deg": 0.0, "yaw_deg": 0.0,
##     "mode": "report" | "window", "report_path": "<abs path>" }
##
## Headless report mode prints one parseable line:
##   [CRS asset] REPORT <compact json>
## Exit codes: 0 ok, 2 bad config/asset, 3 load failure, 4 report IO error.

const SCHEMA := "crs.asset_report/1"
const STATEMENT := ("Loading a GLB in this preview proves visual instantiation "
	+ "only. It does NOT verify production readiness, wheel placement, or "
	+ "physics/collision validity.")

var cfg: Dictionary = {}
var report: Dictionary = {}
var model_root: Node3D
var place_node: Node3D
var warnings: Array = []
var world_bounds_ok: bool = false
var load_ok: bool = false


func _ready() -> void:
	if not _load_config():
		return
	_build_scaffold()
	var ok_load := _load_asset()
	var ok_metrics := _measure_model()
	_finalise_report()
	_write_report()
	_print_report()
	if not ok_load or not ok_metrics:
		get_tree().quit(3)
		return
	if str(cfg.get("mode", "report")) != "window":
		get_tree().quit(0)
		return
	print("[CRS asset] Preview window ready (close the window when done)")


# ---------------------------------------------------------------- config

func _load_config() -> bool:
	var raw := OS.get_environment("CRS_ASSET_PREVIEW")
	if raw.is_empty():
		_fail("no CRS_ASSET_PREVIEW config path set", 2)
		return false
	var f := FileAccess.open(raw, FileAccess.READ)
	if f == null:
		_fail("config file not found: " + raw, 2)
		return false
	var parsed: Variant = JSON.parse_string(f.get_as_text())
	if typeof(parsed) != TYPE_DICTIONARY:
		_fail("config file is not a JSON object: " + raw, 2)
		return false
	cfg = parsed
	var glb := str(cfg.get("glb", ""))
	if glb.is_empty():
		_fail("config missing key: glb", 2)
		return false
	if not FileAccess.file_exists(glb):
		_fail("asset file not found: " + glb, 2)
		return false
	var suffix := glb.to_lower().get_extension()
	if suffix != "glb":
		_fail("unsupported asset type ." + suffix
			+ " (this phase accepts .glb only; .gltf is unsupported)", 2)
		return false
	cfg["scale"] = clampf(_num(cfg.get("scale"), 1.0), 0.05, 20.0)
	cfg["pitch_deg"] = clampf(_num(cfg.get("pitch_deg"), 0.0), -90.0, 90.0)
	cfg["yaw_deg"] = _num(cfg.get("yaw_deg"), 0.0)
	return true


func _num(value: Variant, fallback: float) -> float:
	if value is float:
		return value
	if value is int:
		return float(value)
	return fallback


func _fail(message: String, code: int) -> void:
	var doc := {
		"schema": SCHEMA, "error": message, "verdict": "load_failed",
		"statement": STATEMENT, "warnings": warnings,
	}
	if report.has("status"):
		doc["status"] = report["status"]
	print("[CRS asset] ERROR " + JSON.stringify(doc))
	# still try to persist the error report next to nothing:
	if not str(cfg.get("report_path", "")).is_empty():
		_write_json(str(cfg["report_path"]), doc)
	push_error("[CRS asset] " + message)
	get_tree().quit(code)


# ---------------------------------------------------------------- scaffold

func _build_scaffold() -> void:
	# sky + ambient like the production world (procedural, no assets)
	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	var sky := Sky.new()
	var mat := ProceduralSkyMaterial.new()
	mat.sky_top_color = Color("#8ecae6")
	sky.sky_material = mat
	env.sky = sky
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, -30, 0)
	sun.light_energy = 1.1
	sun.shadow_enabled = true
	add_child(sun)

	# ground plate (visual) with a green top like the production arena
	var ground := MeshInstance3D.new()
	var gmesh := BoxMesh.new()
	gmesh.size = Vector3(200, 0.5, 200)
	ground.mesh = gmesh
	var gmat := StandardMaterial3D.new()
	gmat.albedo_color = Color("#a7c957")
	ground.material_override = gmat
	ground.position = Vector3(0, -0.25, 0)
	add_child(ground)

	# origin marker so unusual origins/orientations are visible
	var origin := Node3D.new()
	_add_marker_box(origin, Color(1, 0.2, 0.2), Vector3(1.0, 0.06, 0.06))
	_add_marker_box(origin, Color(0.2, 1, 0.2), Vector3(0.06, 0.06, 1.0))
	add_child(origin)

	# place node (offset applied AFTER measuring) -> holder (scale+rot)
	place_node = Node3D.new()
	place_node.name = "AssetPlace"
	model_root = Node3D.new()
	model_root.name = "AssetHolder"
	place_node.add_child(model_root)
	add_child(place_node)


func _add_marker_box(parent: Node3D, color: Color, size: Vector3) -> void:
	var mi := MeshInstance3D.new()
	var mesh := BoxMesh.new()
	mesh.size = size
	mi.mesh = mesh
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mi.material_override = mat
	parent.add_child(mi)


# ---------------------------------------------------------------- load

func _load_asset() -> bool:
	var glb := str(cfg.get("glb"))
	var loaded := ResourceLoader.load(glb)
	if loaded == null:
		var exists := ResourceLoader.exists(glb)
		var detail := ("not imported by the Godot editor yet"
			if not exists else "loader returned null (malformed or unsupported GLB)")
		report.status = {
			"resource_load": "failed",
			"import_step": "present" if exists else "missing",
			"instantiate": "skipped",
		}
		report.verdict = "load_failed"
		warnings.append("resource load failed: " + detail
			+ " (run the headless import step and retry)")
		_fail("GLB could not be loaded: " + glb + " (" + detail + ")", 3)
		return false
	if not (loaded is PackedScene):
		report.status = {"resource_load": "failed", "import_step": "present",
			"instantiate": "skipped"}
		report.verdict = "load_failed"
		warnings.append("resource is a " + loaded.get_class()
			+ ", expected a scene from glTF import")
		_fail("GLB did not import as a scene: " + glb, 3)
		return false
	var node: Node = loaded.instantiate()
	if node == null:
		report.status = {"resource_load": "ok", "import_step": "present",
			"instantiate": "failed"}
		report.verdict = "load_failed"
		warnings.append("scene instantiate() returned null")
		_fail("GLB scene could not be instantiated: " + glb, 3)
		return false
	model_root.add_child(node)
	if not (node is Node3D):
		# glTF scenes are Node3D roots by spec; anything else is unexpected
		warnings.append("scene root is a " + node.get_class()
			+ " (expected Node3D); metrics may be incomplete")
	var label := glb.get_file()
	model_root.name = "AssetHolder_" + _safe_name(label)
	report.status = {"resource_load": "ok", "import_step": "present",
		"instantiate": "ok"}
	load_ok = true
	return true


func _safe_name(text: String) -> String:
	var out := ""
	for ch in text:
		var ok := (ch >= "0" and ch <= "9") or (ch >= "a" and ch <= "z") \
			or (ch >= "A" and ch <= "Z") or ch == "."
		out += ch if ok else "_"
	return out


# ---------------------------------------------------------------- metrics

func _measure_model() -> bool:
	var meshes := []
	if model_root != null and model_root.get_child_count() > 0:
		meshes = model_root.find_children("*", "MeshInstance3D", true, false)
	var mesh_count := meshes.size()
	var surface_count := 0
	var materials := {}
	var textures := {}
	var bad_meshes := 0
	var local_min: Variant = null
	var local_max: Variant = null

	for m in meshes:
		var mesh: Mesh = m.mesh
		if mesh == null:
			bad_meshes += 1
			warnings.append("MeshInstance '%s' has no mesh data" % m.name)
			continue
		surface_count += int(mesh.get_surface_count())
		var aabb := mesh.get_aabb()
		if aabb.size.length() < 0.0001:
			bad_meshes += 1
			warnings.append("MeshInstance '%s' has degenerate bounds" % m.name)
			continue
		var t: Transform3D = m.global_transform
		for corner in _aabb_corners(aabb):
			var w: Vector3 = t * corner
			local_min = w if local_min == null else local_min.min(w)
			local_max = w if local_max == null else local_max.max(w)
		_collect_materials(m, mesh, materials, textures)

	var skeleton := {}
	var anims := []
	if model_root != null and model_root.get_child_count() > 0:
		for sk in model_root.find_children("*", "Skeleton3D", true, false):
			skeleton[str(sk.name)] = int(sk.get_bone_count())
		for ap in model_root.find_children("*", "AnimationPlayer", true, false):
			for anim_name in ap.get_animation_list():
				anims.append(str(anim_name))

	report.metrics = {
		"mesh_instances": mesh_count,
		"mesh_instances_without_data": bad_meshes,
		"surface_count": surface_count,
		"materials": materials.keys(),
		"material_count": materials.size(),
		"textures": textures.keys(),
		"texture_count": textures.size(),
		"animations": anims,
		"skeleton_bones": skeleton,
		"node_count": _count_nodes(model_root),
	}

	# ---- bounds -----------------------------------------------------------
	var scale_f: float = float(cfg.get("scale", 1.0))
	if local_min == null or local_max == null:
		warnings.append("no measurable mesh bounds (empty or mesh-less scene)")
		report.bounds = {"local_meters_unscaled": null, "size_m_scaled": null}
		world_bounds_ok = false
	else:
		var size: Vector3 = local_max - local_min
		var min_y: float = local_min.y
		var center: Vector3 = local_min + size * 0.5
		if absf(min_y) > 0.02:
			warnings.append("origin is not at ground-contact level "
				+ "(bottom of model is at y=%.3f in model space); "
				% min_y + "preview auto-grounds it for display")
		if Vector2(center.x, center.z).length() > 0.05:
			warnings.append("origin offset from volume center "
				+ "(x=%.3f, z=%.3f)" % [center.x, center.z]
				+ "; preview recenters for display")
		for i in 3:
			if size[i] > 50.0:
				warnings.append("axis %d spans %.1fin m; verify intended scale"
					% [i, size[i]])
			elif size[i] < 0.1:
				warnings.append("axis %d spans %.3f m; verify intended scale"
					% [i, size[i]])
		var local_doc := {"min": _v3(local_min), "max": _v3(local_max),
			"size": _v3(size), "center": _v3(center)}
		report.bounds = {
			"local_meters_unscaled": local_doc,
			"size_m_scaled": _v3(size * scale_f),
		}
		world_bounds_ok = true

	if material_count_zero() and surface_count > 0:
		warnings.append("meshes carry no detectable materials")
	_place_model(scale_f)
	_build_camera(scale_f)
	return true


func material_count_zero() -> bool:
	return int(report.metrics.get("material_count", 0)) == 0


func _place_model(scale_f: float) -> void:
	var holder = place_node.get_node_or_null("AssetHolder")
	if holder == null:
		return
	holder.scale = Vector3(scale_f, scale_f, scale_f)
	var pitch := deg_to_rad(float(cfg.get("pitch_deg", 0.0)))
	var yaw := deg_to_rad(float(cfg.get("yaw_deg", 0.0)))
	holder.rotation = Vector3(pitch, yaw, 0.0)

	# world AABB of the transformed tree, relative to this (identity) root
	var meshes := model_root.find_children("*", "MeshInstance3D", true, false)
	var wmin: Variant = null
	var wmax: Variant = null
	for m in meshes:
		var mesh: Mesh = m.mesh
		if mesh == null or mesh.get_aabb().size.length() < 0.0001:
			continue
		var t: Transform3D = m.global_transform
		var aabb := mesh.get_aabb()
		for corner in _aabb_corners(aabb):
			var w: Vector3 = t * corner
			wmin = w if wmin == null else wmin.min(w)
			wmax = w if wmax == null else wmax.max(w)
	if wmin == null or wmax == null:
		return
	var wcenter: Vector3 = wmin + (wmax - wmin) * 0.5
	var ground_offset: float = wmin.y
	place_node.position = Vector3(-wcenter.x, -ground_offset, -wcenter.z)


func _build_camera(scale_f: float) -> void:
	var cam := Camera3D.new()
	cam.name = "PreviewCamera"
	add_child(cam)
	cam.current = true
	cam.fov = 55.0
	var radius: float = 3.0
	if world_bounds_ok:
		var bounds_local: Dictionary = report.bounds.local_meters_unscaled
		var size := Vector3(bounds_local.size[0], bounds_local.size[1],
			bounds_local.size[2])
		var scaled_size := size * scale_f
		radius = scaled_size.length() * 0.5
		if radius < 0.3:
			warnings.append("model is very small for a real-world preview "
				+ "(fits in %.2f m) - check scale" % scaled_size.length())
	var center := Vector3(0, radius * 0.6, 0)
	var distance: float = radius / tan(deg_to_rad(cam.fov * 0.5)) * 1.25
	var dir := Vector3(0.55, 0.38, 0.75).normalized()
	cam.position = center + dir * maxf(distance, 1.2)
	cam.look_at(center, Vector3.UP)


func _count_nodes(node: Node) -> int:
	var total := 1
	for child in node.get_children():
		total += _count_nodes(child)
	return total


func _collect_materials(m: MeshInstance3D, mesh: Mesh,
		materials: Dictionary, textures: Dictionary) -> void:
	var override_mat: Material = m.material_override
	if override_mat != null:
		_note_material(override_mat, "(surface override)", materials, textures)
	for i in int(mesh.get_surface_count()):
		var mat: Material = mesh.surface_get_material(i)
		_note_material(mat, str(mesh.resource_name) + "/" + str(i),
			materials, textures)


func _note_material(mat: Material, _where: String, materials: Dictionary,
		textures: Dictionary) -> void:
	if mat == null:
		return
	var key := str(mat.resource_name)
	if key.is_empty():
		key = mat.get_class()
	var src := _where + " [" + key + "]"
	if not materials.has(src):
		materials[src] = true
	if mat is BaseMaterial3D:
		var tex: Texture2D = mat.albedo_texture
		if tex != null:
			var tpath := str(tex.resource_path)
			if tpath.is_empty():
				tpath = "embedded;" + str(tex.get_class())
			if not textures.has(tpath):
				textures[tpath] = true
	elif mat is ShaderMaterial:
		var skey := "shader:" + str(mat.shader.resource_path if mat.shader else "")
		if not textures.has(skey):
			textures[skey] = true


# ---------------------------------------------------------------- report

func _finalise_report() -> void:
	var glb := str(cfg.get("glb", ""))
	report.schema = SCHEMA
	report.created_at = Time.get_datetime_string_from_system(true)
	report.mode = str(cfg.get("mode", "report"))
	report.loaded_asset_path = glb
	report.copied_asset_path = str(cfg.get("copied_asset_path", ""))
	report.config = {
		"scale": _num(cfg.get("scale"), 1.0),
		"pitch_deg": _num(cfg.get("pitch_deg"), 0.0),
		"yaw_deg": _num(cfg.get("yaw_deg"), 0.0),
	}
	report.warnings = warnings
	if report.status == null:
		report.status = {"resource_load": "unknown", "import_step": "unknown",
			"instantiate": "unknown"}
	# load-failed paths (load_ok==false) must never look like a success
	report.verdict = ("load_failed" if not load_ok
		else ("loaded_with_warnings" if warnings.size() > 0
			else "loaded_visual_review_required"))
	report.statement = STATEMENT


func _v3(v: Vector3) -> Array:
	return [_round(v.x), _round(v.y), _round(v.z)]


func _round(f: float) -> float:
	return snappedf(f, 0.0001)


func _aabb_corners(aabb: AABB) -> Array:
	var p0 := aabb.position
	var s := aabb.size
	var p1 := p0 + s
	var pts := []
	pts.append(Vector3(p0.x, p0.y, p0.z))
	pts.append(Vector3(p1.x, p0.y, p0.z))
	pts.append(Vector3(p0.x, p1.y, p0.z))
	pts.append(Vector3(p1.x, p1.y, p0.z))
	pts.append(Vector3(p0.x, p0.y, p1.z))
	pts.append(Vector3(p1.x, p0.y, p1.z))
	pts.append(Vector3(p0.x, p1.y, p1.z))
	pts.append(Vector3(p1.x, p1.y, p1.z))
	return pts


func _write_report() -> void:
	var path := str(cfg.get("report_path", ""))
	if path.is_empty():
		return
	report.warnings = warnings
	if not _write_json(path, report):
		push_error("[CRS asset] report could not be written: " + path)
		print("[CRS asset] ERROR report write failed: " + path)


func _print_report() -> void:
	print("[CRS asset] REPORT " + JSON.stringify(report))


func _write_json(path: String, doc: Variant) -> bool:
	var text: String = doc if doc is String else JSON.stringify(doc, "\t")
	var tmp := path + ".tmp"
	var f := FileAccess.open(tmp, FileAccess.WRITE)
	if f == null:
		return false
	f.store_string(text)
	f.close()
	var err := DirAccess.rename_absolute(tmp, path)
	if err == OK and FileAccess.file_exists(path):
		return true
	# non-atomic fallback keeps the data when rename is refused
	if FileAccess.file_exists(path):
		return true
	push_error("[CRS asset] report rename failed (code %d): %s" % [err, path])
	return false
