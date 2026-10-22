extends Camera3D
class_name CameraDirector
## Event-aware cinematic director: builds a shot plan from camera_hints plus
## live events (launch/land/crash), cuts between six distinct camera styles,
## tracks the primary vehicle, and never loses it outside frame.
##
## Shot styles:
##  - establish:      wide reveal at the track start
##  - rear_chase:     behind the car, looking ahead so ramps stay in frame
##  - low_side:       low side tracking shot for speed
##  - front_track:    ahead-oncoming tracking shot
##  - wide_aerial:    high overview of ramp + landing geometry (needs hints)
##  - landing:        side shot near the landing zone
##  - run_action:     mid-course action (rotates the styles above)
##  - detail:         slow orbit on a focused vehicle
##  - dramatic:       brief close shot on major crashes (event-driven only)
##  - final_wide:     pullback wide at the finish
##
## Behavior rules: cinematic_auto inserts geometry-aware shots; single_wide
## keeps the original single fixed camera. Shot switches respect a minimum
## on-screen time so the cut does not pulse. All pattern picks and shake noise
## use a scenario-seeded RNG so identical seeds produce identical cuts.

var scenario: Dictionary = {}
var mode: String = "cinematic_auto"
var transition_sec: float = 0.85
var hints: Dictionary = {}
var vehicles: Array = []
var primary: RigidBody3D = null
var shot_queue: Array = []
var active_shot: int = -1
var shot_t: float = 0.0
var elapsed_sim: float = 0.0
var blend: float = 1.0
var last_pos: Vector3 = Vector3.ZERO
var last_look: Vector3 = Vector3.ZERO
var shake: float = 0.0
var detail_focus: int = 0
var duration: float = 20.0
var portrait_scale: float = 1.0
var shot_take: int = 0
var rng: RandomNumberGenerator = RandomNumberGenerator.new()

# cutting discipline
var min_shot_time: float = 1.35
# transient event close-up ("dramatic"): sim window and decay
var override_until: float = -1.0
var override_kind: String = ""
var override_cooldown_until: float = 0.0


func setup(sc: Dictionary, vehicle_list: Array) -> void:
	scenario = sc
	vehicles = vehicle_list
	mode = str(sc.get("camera", {}).get("style", "cinematic_auto")).to_lower()
	hints = sc.get("layout", {}).get("camera_hints", {})
	duration = float(sc.get("duration_seconds", 20.0))
	# seeded determinism: identical scenario seeds produce identical cuts
	rng.seed = hash("crs-cam-%d" % int(sc.get("seed", 0)))
	# portrait crops horizontally: widen angles and push distances out
	var aspect := float(sc.get("output", {}).get("width", 1080)) / float(sc.get("output", {}).get("height", 1920))
	portrait_scale = clampf(1.15 / maxf(aspect, 0.3), 1.0, 2.0)
	min_shot_time = clampf(duration * 0.06, 1.0, 2.2)
	fov = 75.0
	primary = _pick_primary()
	_build_shot_queue()
	current = true
	last_pos = _shot_pos(active_shot)
	last_look = _shot_look()
	global_position = last_pos
	look_at(last_look, Vector3.UP)


func _pick_primary() -> RigidBody3D:
	if vehicles.is_empty():
		return null
	return vehicles[0]


func _valid_primary() -> RigidBody3D:
	if primary != null and is_instance_valid(primary):
		return primary
	# fall back to any live vehicle so one wrecked car cannot kill the cut
	for v in vehicles:
		if v != null and is_instance_valid(v):
			primary = v
			return primary
	return null


func _build_shot_queue() -> void:
	var d := duration
	var order: Array = [
		{"kind": "establish", "t0": 0.0, "t1": d * 0.14, "hint": "start"},
		{"kind": "rear_chase", "t0": d * 0.14, "t1": d * 0.34, "hint": "ramp_top"},
		{"kind": "low_side", "t0": d * 0.34, "t1": d * 0.38, "hint": "ramp_top"},
		{"kind": "airborne", "t0": d * 0.38, "t1": d * 0.56, "hint": "gap_mid"},
		{"kind": "landing", "t0": d * 0.56, "t1": d * 0.72, "hint": "landing"},
		{"kind": "wide_aerial", "t0": d * 0.72, "t1": d * 0.80, "hint": "landing"},
		{"kind": "run_action", "t0": d * 0.80, "t1": d * 0.90, "hint": "mid1"},
		{"kind": "detail", "t0": d * 0.90, "t1": d * 0.945, "hint": "mid2"},
		{"kind": "final_wide", "t0": d * 0.945, "t1": d + 1.0, "hint": "finish"},
	]
	if mode == "single_wide":
		shot_queue = [{"kind": "single_wide", "t0": 0.0, "t1": duration + 1.0, "hint": "start"}]
		return
	var trimmed: Array = []
	for s in order:
		match str(s["kind"]):
			# always runnable: car-relative, no geometry needed
			"establish", "rear_chase", "low_side", "wide_aerial", "run_action":
				trimmed.append(s)
			"wide_aerial_disabled":
				pass
			_:
				# geometry-dependent shots only if their hint exists
				if hints.has(s["hint"]):
					trimmed.append(s)
	# wide_aerial is meaningful only when we know both ends of the stunt
	var use_aerial: bool = hints.has("ramp_top") or hints.has("landing")
	trimmed = trimmed.filter(func(s): return str(s["kind"]) != "wide_aerial" or use_aerial)
	# de-duplicate back-to-back same-kind shots (low_side before a trimmed airborne etc.)
	var final_queue: Array = []
	for s in trimmed:
		if not final_queue.is_empty() and str(final_queue[-1]["kind"]) == str(s["kind"]):
			continue
		final_queue.append(s)
	shot_queue = final_queue
	active_shot = -1


func primary_vehicle() -> RigidBody3D:
	return primary


func on_event(event: Dictionary) -> void:
	var kind := str(event.get("type", ""))
	match kind:
		"crash":
			shake = clampf(float(event.get("severity", 4.0)) * 0.03, 0.1, 0.4)
			if mode == "cinematic_auto" and _victim_is_valid(event):
				detail_focus = int(event.get("vehicle", 0))
			# major impact only: brief dramatic close-up (not on every fender bender)
			if mode == "cinematic_auto" and float(event.get("severity", 0.0)) > 9.0:
				_request_dramatic("crash")
		"land":
			shake = clampf(float(event.get("impact_speed", 3.0)) * 0.02, 0.02, 0.18)
		"launch":
			shake = 0.05
		_:
			pass


func _victim_is_valid(event: Dictionary) -> bool:
	var idx := clampi(int(event.get("vehicle", 0)), 0, vehicles.size() - 1)
	return not vehicles.is_empty() and idx >= 0


func _request_dramatic(kind: String) -> void:
	# only off the ground and not in the final second, never twice back-to-back
	if elapsed_sim >= duration - 1.2:
		return
	if elapsed_sim < override_cooldown_until:
		return
	if not _victim_is_valid({"vehicle": detail_focus}):
		return
	override_kind = kind
	override_until = elapsed_sim + clampf(duration * 0.035, 0.4, 0.8)
	override_cooldown_until = override_until + clampf(duration * 0.15, 2.5, 5.0)


func _dramatic_active() -> bool:
	return override_until > 0.0 and elapsed_sim < override_until


func _switch_shot(idx: int) -> void:
	if idx < 0 or idx >= shot_queue.size():
		return
	if idx == active_shot:
		return
	if active_shot >= 0 and shot_t < min_shot_time:
		# cutting discipline: do not pulse shots; let the current one breathe
		return
	active_shot = idx
	shot_t = 0.0
	blend = 0.0
	# each re-entry of a shot kind gets a different, deterministic pattern
	if str(shot_queue[idx]["kind"]) == "run_action":
		shot_take += 1


func _hint_pos(key: String, fallback: Vector3) -> Vector3:
	if hints.has(key):
		var arr: Array = hints[key]
		return Vector3(arr[0], arr[1], arr[2])
	return fallback


## Pull wide hint-based shots back toward the car so the subject stays
## recognizable while still showing the surrounding geometry.
func _clamp_wide(pos: Vector3, car_pos: Vector3, max_horiz: float) -> Vector3:
	var delta: Vector3 = pos - car_pos
	var flat := Vector3(delta.x, 0.0, delta.z)
	if flat.length() > max_horiz:
		flat = flat.normalized() * max_horiz
	return Vector3(car_pos.x + flat.x, maxf(pos.y, car_pos.y + 7.0), car_pos.z + flat.z)


func _car_speed() -> float:
	var car := _valid_primary()
	if car == null:
		return 0.0
	return car.linear_velocity.length()


func _look_ahead(dist: float) -> Vector3:
	var car := _valid_primary()
	if car == null:
		return Vector3.ZERO
	var fwd: Vector3 = -car.global_basis.z
	return car.global_position + fwd * dist


func _shot_pos(idx: int) -> Vector3:
	var car := _valid_primary()
	if idx < 0 or idx >= shot_queue.size():
		return Vector3(0, 40, -50)
	var shot: Dictionary = shot_queue[idx]
	var kind: String = str(shot["kind"])
	var car_pos: Vector3 = car.global_position if car else Vector3.ZERO
	var fwd: Vector3 = (-car.global_basis.z) if car else Vector3.FORWARD
	var right: Vector3 = (car.global_basis.x) if car else Vector3.RIGHT
	var pos: Vector3 = car_pos
	match kind:
		"single_wide":
			pos = _hint_pos(str(shot["hint"]), car_pos + Vector3(-30, 14, -28))
			pos.z = pos.z - 26.0 * portrait_scale
		"establish":
			pos = _hint_pos(str(shot["hint"]), car_pos + Vector3(-24, 10, -24))
			pos = _clamp_wide(pos, car_pos, 34.0 * portrait_scale)
		"rear_chase":
			pos = car_pos - fwd * 11.0 * portrait_scale - right * 3.5 * portrait_scale + Vector3(0, 3.2, 0)
		"low_side":
			# hug the ground on car-relative side; car stays offset in frame
			pos = car_pos - right * 9.0 * portrait_scale + Vector3(0, 1.4, 0)
		"front_track":
			# ahead-oncoming: offset laterally so the car sweeps past the
			# camera instead of driving through the near plane
			pos = car_pos + fwd * 12.0 * portrait_scale + right * 6.5 * portrait_scale + Vector3(0, 2.2, 0)
		"airborne":
			pos = _hint_pos(str(shot["hint"]), car_pos + Vector3(0, 4.5, -14)) if not hints.has("gap_mid") else car_pos + Vector3(-fwd.x * 0, 3.0, 0)
			if hints.has("gap_mid"):
				pos = car_pos + right * -8.5 * portrait_scale + Vector3(0, 1.6, 0)
		"landing":
			var lh := _hint_pos(str(shot["hint"]), car_pos + right * 6.0 + Vector3(0, 3.0, 0))
			var side := -1.0 if (shot_take % 2 == 0) else 1.0
			lh += right * side * 3.0 * portrait_scale
			pos = lh
		"wide_aerial":
			# overview: anchor on the stunt geometry when hints exist,
			# otherwise rig above the car with generous distance
			var a := _hint_pos("ramp_top", car_pos)
			var b := _hint_pos("landing", car_pos + fwd * 30.0)
			if hints.has("ramp_top") and hints.has("landing") and (a - b).length() > 12.0:
				pos = a.lerp(b, 0.45) + Vector3(0, 26.0, -8.0 * portrait_scale)
				pos.z = pos.z - 14.0 * portrait_scale
			else:
				pos = car_pos - fwd * 4.0 * portrait_scale - right * 12.0 * portrait_scale + Vector3(0, 20.0, 0)
		"run_action":
			# rotate through four car-relative chase styles per re-entry
			match shot_take % 4:
				1:
					pos = car_pos - fwd * 11.5 * portrait_scale + Vector3(0, 3.4, 0)
				2:
					pos = car_pos + fwd * 12.0 * portrait_scale + right * 6.0 * portrait_scale + Vector3(0, 2.2, 0)
				3:
					pos = car_pos - right * 9.0 * portrait_scale + Vector3(0, 1.4, 0)
				_:
					pos = car_pos - right * 8.0 * portrait_scale + Vector3(0, 2.6, 0)
		"detail":
			detail_focus = clampi(detail_focus, 0, maxi(vehicles.size() - 1, 0))
			var focus_car: RigidBody3D = vehicles[detail_focus] if not vehicles.is_empty() else primary
			var fc: Vector3 = focus_car.global_position if focus_car else car_pos
			var theta: float = shot_t * 0.35 + 2.2
			pos = fc + Vector3(cos(theta) * 11.0 * portrait_scale, 3.2 + sin(theta * 2.0) * 1.2, sin(theta) * 11.0 * portrait_scale)
		"final_wide":
			pos = _hint_pos(str(shot["hint"]), car_pos + Vector3(-26, 9, -24))
			pos = _clamp_wide(pos, car_pos, 30.0 * portrait_scale)
	# never dip below the car top: black frames came from cameras
	# inside elevated road decks
	pos.y = maxf(pos.y, maxf(car_pos.y + 0.6, 1.2))
	return pos


func _override_pos() -> Vector3:
	# dramatic close-up: tight framing anchored on the 'detail' hint
	# (the hazard being hit) with a three-quarter, slightly low angle
	var car := _valid_primary()
	if car == null:
		return Vector3(0, 30, -30)
	var car_pos: Vector3 = car.global_position
	var fwd: Vector3 = -car.global_basis.z
	var right: Vector3 = car.global_basis.x
	var drift := fmod(elapsed_sim, 1.0) - 0.5
	var pos: Vector3
	if hints.has("detail"):
		var d := _hint_pos("detail", car_pos)
		var dir: Vector3 = (car_pos - d).normalized()
		pos = car_pos - dir * (7.2 * portrait_scale) + Vector3(0, 2.2, 0)
	else:
		pos = car_pos - fwd * (6.4 * portrait_scale) - right * (4.2 * portrait_scale) + Vector3(0, 1.7, 0)
	pos += right * drift * 0.8
	return Vector3(pos.x, maxf(pos.y, car_pos.y + 0.4), pos.z)


func _shot_look() -> Vector3:
	var car := _valid_primary()
	if car == null:
		return Vector3.ZERO
	match _active_kind():
		"rear_chase":
			# keep the upcoming ramp/obstacle in frame behind the car
			return _look_ahead(clampf(_car_speed() * 0.32, 3.0, 9.0))
		"low_side":
			return _look_ahead(4.5)
		"front_track":
			# car approaching the camera: look slightly before the car nose
			return car.global_position - car.global_basis.z * 1.5
		"airborne":
			return _look_ahead(3.0)
		_:
			return car.global_position


func _active_kind() -> String:
	if _dramatic_active():
		return "dramatic"
	if active_shot < 0 or active_shot >= shot_queue.size():
		return ""
	return str(shot_queue[active_shot]["kind"])


func _override_look() -> Vector3:
	var car := _valid_primary()
	return car.global_position + Vector3(0, 0.4, 0) if car else Vector3.ZERO


func on_vehicle_indexes_changed(_list: Array) -> void:
	pass


func _physics_process(delta: float) -> void:
	elapsed_sim += delta
	if shot_queue.is_empty() or _valid_primary() == null:
		return
	var still_valid := active_shot >= 0 and active_shot < shot_queue.size()
	if not still_valid:
		_switch_shot(clampi(active_shot, 0, shot_queue.size() - 1))
		if active_shot < 0:
			active_shot = 0
	shot_t = clampf(elapsed_sim - shot_queue[active_shot]["t0"], 0.0, 1e9)
	var next_threshold: float = shot_queue[active_shot]["t1"]
	if elapsed_sim >= next_threshold:
		_switch_shot(mini(active_shot + 1, shot_queue.size() - 1))

	blend = clampf(blend + delta / maxf(transition_sec * 0.35, 0.05), 0.0, 1.0)
	var solid := smoothstep(0.0, 1.0, blend)
	var dramatic := _dramatic_active()
	var goal_pos: Vector3 = _override_pos() if dramatic else _shot_pos(active_shot)
	var goal_look: Vector3 = _override_look() if dramatic else _shot_look()
	if not dramatic and override_until > 0.0 and elapsed_sim >= override_until:
		# leaving override: hand back smoothly from the close-up position
		override_until = -1.0
		override_kind = ""
		shot_t = 0.0
	# ease into position from previous shot
	var pos: Vector3 = last_pos.lerp(goal_pos, solid)
	var look: Vector3 = last_look.lerp(goal_look, solid)
	last_pos = pos
	last_look = look

	# subtle seeded shake decay (deterministic per scenario seed)
	if shake > 0.001:
		var n := Vector3(rng.randf_range(-1, 1), rng.randf_range(-1, 1), rng.randf_range(-1, 1)) * shake * 0.55
		pos += n
		shake = lerpf(shake, 0.0, minf(6.0 * delta, 1.0))

	# never lose the subject: enforce minimal camera distance
	if (look - pos).length() < 7.0 * portrait_scale:
		var to_cam := (pos - look).normalized() * 7.0 * portrait_scale
		pos = look + to_cam
	# keep non-primary vehicles out of the near plane: a passing truck at
	# spawn-lane distance otherwise fills the whole frame for a second
	for v in vehicles:
		if v == null or not is_instance_valid(v) or v == primary:
			continue
		var away: Vector3 = pos - v.global_position
		var vdist := away.length()
		if vdist < 3.4 and vdist > 0.01:
			pos += away.normalized() * (3.4 - vdist)
			pos.y = maxf(pos.y, v.global_position.y + 0.8)
	if (look - pos).length() > 0.5:
		look_at(look, Vector3.UP)
	# pull camera in front of geometry between it and the subject
	var cam_query := PhysicsRayQueryParameters3D.create(look, pos, 1 | 4, [])
	var c_hit := get_world_3d().direct_space_state.intersect_ray(cam_query)
	if not c_hit.is_empty() and (c_hit["position"] - look).length() > 2.0:
		var dir := (pos - look).normalized()
		global_position = c_hit["position"] - dir * 0.8
		return
	global_position = pos
	_debug_probe(pos)


func _debug_probe(pos: Vector3) -> void:
	if OS.get_environment("CRS_CAM_DEBUG") != "1":
		return
	var frame_n := Engine.get_physics_frames()
	if frame_n % 15 != 0:
		return
	var parts := ["[cam t=%.2f kind=%s pos=%v look=%v]" % [elapsed_sim, _active_kind(), global_position, last_look]]
	for v in vehicles:
		if v == null or not is_instance_valid(v):
			continue
		var d: float = (v.global_position - global_position).length()
		parts.append("v%d(%s)=%s d=%.2f" % [v.get_meta("crs_index"), v.name, str(v.global_position), d])
	print(" ".join(parts))
