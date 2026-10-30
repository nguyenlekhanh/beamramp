extends Node3D
class_name ExternalBodyVisual
## M3.6B: opt-in external GLB body visual for the production vehicle.
##
## Enabled ONLY when the environment variable CRS_VEHICLE_GLB points at an
## imported .glb inside the Godot project. Without it, this node does nothing
## and the procedural car is used as before.
##
## Safety rules (M3.6B):
## - Purely visual: never touches the RigidBody3D transform, collision shapes,
##   physics parameters, AI, camera, audio or events.
## - Imported GLB collision geometry (CollisionShape3D / bodies inside the
##   scene) is stripped, never attached to the production physics body.
## - On any load failure the procedural body (fallback) stays in place.
## - Procedural wheels are kept by default because the model's own wheel
##   layout cannot be verified automatically; duplication is warned about.
## - Forward axis is NOT assumed: car forward is -Z; use CRS_VEHICLE_GLB_YAW_DEG
##   if the model faces a different way. Scale is not assumed either (see the
##   M3.6A asset report for the measured AABB).

const SCALE_MIN := 0.05
const SCALE_MAX := 20.0

var car: RigidBody3D = null
var glb_res_path: String = ""
var scale_factor: float = 1.0
var yaw_deg: float = 0.0
var pitch_deg: float = 0.0
var auto_ground: bool = true
var wheel_mode: String = "keep"
var size_m: Vector3 = Vector3.ZERO
var ok_applied: bool = false


## Returns true when the external visual was applied; false means "opted out"
## (env unset) or "failed to load" (procedural fallback stays visible).
func configure(car_body: RigidBody3D) -> bool:
	car = car_body
	glb_res_path = OS.get_environment("CRS_VEHICLE_GLB").strip_edges()
	if glb_res_path == "":
		return false  # opt-in disabled: default visuals, silent
	scale_factor = _env_float("CRS_VEHICLE_GLB_SCALE", 1.0)
	scale_factor = _clamp_scale(scale_factor)
	yaw_deg = _env_float("CRS_VEHICLE_GLB_YAW_DEG", 0.0)
	pitch_deg = _env_float("CRS_VEHICLE_GLB_PITCH_DEG", 0.0)
	auto_ground = OS.get_environment("CRS_VEHICLE_GLB_AUTO_GROUND") != "0"
	if OS.get_environment("CRS_VEHICLE_GLB_WHEELS") == "hide":
		wheel_mode = "hide"
	print("[CRS asset] external vehicle visual: glb=%s scale=%.3f yaw=%.2f pitch=%.2f auto_ground=%s" % [glb_res_path, scale_factor, yaw_deg, pitch_deg, "1" if auto_ground else "0"])

	if not ResourceLoader.exists(glb_res_path):
		return _fallback("file not found or not imported in the Godot project")
	var res = ResourceLoader.load(glb_res_path)
	if res == null:
		return _fallback("loader returned null (malformed or unimported GLB)")
	if not (res is PackedScene):
		return _fallback("resource is not a PackedScene")
	var inst = (res as PackedScene).instantiate()
	if inst == null:
		return _fallback("instantiate returned null")

	var stripped := _strip_physics_nodes(inst)
	if stripped > 0:
		print("[CRS asset] external visual stripped %d physics node(s) from the GLB scene (never attached to the vehicle body)" % stripped)

	var container := Node3D.new()
	container.name = "ExternalBodyScaled"
	add_child(container)
	container.scale = Vector3.ONE * scale_factor
	container.rotation_degrees = Vector3(pitch_deg, yaw_deg, 0.0)
	container.add_child(inst)

	var bounds: Array = _measure(inst, container)
	if not bounds[0]:
		print("[CRS asset] external visual: no MeshInstance3D nodes found; keeping raw origin placement")
	else:
		var mn: Vector3 = bounds[1]
		var mx: Vector3 = bounds[2]
		size_m = (mx - mn) * scale_factor
		print("[CRS asset] external visual AABB size=%s m (bounds y %.2f..%.2f local)" % [str(size_m), mn.y, mx.y])
		if size_m.x > 40.0 or size_m.y > 40.0 or size_m.z > 40.0 or size_m.y < 0.05:
			print("[CRS asset] WARNING external visual size %s m looks off; check CRS_VEHICLE_GLB_SCALE and the asset report" % str(size_m))
		if auto_ground:
			container.position.y = -mn.y * scale_factor
			print("[CRS asset] external visual auto-grounded (offset y=%.3f); car origin stays at ground contact" % container.position.y)

	var hidden := _hide_proc_body_parts()
	if hidden >= 0:
		print("[CRS asset] external visual hid %d procedural body part(s); lights and damage hooks preserved" % hidden)
	if wheel_mode == "keep":
		print("[CRS asset] model wheel layout unverified: procedural wheels kept (a visual duplicate is possible; set CRS_VEHICLE_GLB_WHEELS=hide if the model has its own wheels)")
	else:
		for wheel in car.get("wheels"):
			if wheel is Node3D:
				(wheel as Node3D).visible = false
		print("[CRS asset] procedural wheels hidden per CRS_VEHICLE_GLB_WHEELS=hide")
	if yaw_deg == 0.0:
		print("[CRS asset] note yaw offset 0.0: verify the model faces -Z in game (car forward); adjust CRS_VEHICLE_GLB_YAW_DEG if it drives backwards")
	ok_applied = true
	return true


func _fallback(reason: String) -> bool:
	print("[CRS asset] external visual FAILED(%s) - procedural fallback active, default vehicle in use" % reason)
	queue_free()
	ok_applied = false
	return false


func _clamp_scale(value: float) -> float:
	if value < SCALE_MIN:
		print("[CRS asset] WARNING scale %.3f below %.2f; clamped" % [value, SCALE_MIN])
		return SCALE_MIN
	if value > SCALE_MAX:
		print("[CRS asset] WARNING scale %.3f above %.2f; clamped" % [value, SCALE_MAX])
		return SCALE_MAX
	return value


func _env_float(name: String, def: float) -> float:
	var txt: String = OS.get_environment(name).strip_edges()
	if txt == "":
		return def
	var value: float = txt.to_float()
	if not is_finite(value):
		print("[CRS asset] WARNING env %s=%s is not a finite number; using %.2f" % [name, txt, def])
		return def
	return value


## Removes every collision/physics node that came with the GLB so that its
## geometry can never join the vehicle's collision surface. Visual-only.
func _strip_physics_nodes(inst: Node) -> int:
	var doomed: Array = []
	for node in inst.find_children("*", "", true, false):
		if node is CollisionObject3D or node is CollisionShape3D:
			doomed.append(node)
	for node in doomed:
		if node is CollisionObject3D:
			node.get_parent().remove_child(node)
		node.free()
	return doomed.size()


## Local-space AABB of the GLB relative to `stop_at` (the scale container).
## Works on detached nodes; only local transforms are used.
func _measure(root: Node, stop_at: Node) -> Array:
	var found := false
	var mn := Vector3.ZERO
	var mx := Vector3.ZERO
	for mi in _mesh_instances(root):
		var mesh_inst := mi as MeshInstance3D
		if mesh_inst.mesh == null:
			continue
		var xf := Transform3D.IDENTITY
		var node: Node = mesh_inst
		while node != null and node != stop_at:
			if node is Node3D:
				xf = (node as Node3D).transform * xf
			node = node.get_parent()
		var volume: AABB = mesh_inst.mesh.get_aabb()
		for corner in _corners(volume):
			var p: Vector3 = xf * corner
			if not found:
				mn = p
				mx = p
				found = true
			else:
				mn = Vector3(minf(mn.x, p.x), minf(mn.y, p.y), minf(mn.z, p.z))
				mx = Vector3(maxf(mx.x, p.x), maxf(mx.y, p.y), maxf(mx.z, p.z))
	return [found, mn, mx]


func _mesh_instances(root: Node) -> Array:
	var list: Array = []
	if root is MeshInstance3D:
		list.append(root)
	for child in root.find_children("*", "MeshInstance3D", true, false):
		list.append(child)
	return list


func _corners(box: AABB) -> Array:
	var out: Array = []
	for ex in [box.position.x, box.end.x]:
		for ey in [box.position.y, box.end.y]:
			for ez in [box.position.z, box.end.z]:
				out.append(Vector3(ex, ey, ez))
	return out


## Hides only the procedural parts that conflict with the imported model:
## shell, cabin, windows, cargo, bumpers, mirrors, hood. Lights, wheel
## visuals, damage rig hooks and everything else stay untouched. Returns the
## hidden count, or -1 when the part list is unavailable (factory too old).
func _hide_proc_body_parts() -> int:
	if car == null or not car.has_meta("proc_body_parts"):
		return -1
	var hidden := 0
	for part in car.get_meta("proc_body_parts"):
		if is_instance_valid(part) and part is Node3D:
			(part as Node3D).visible = false
			hidden += 1
	return hidden
