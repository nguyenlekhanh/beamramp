extends Node3D
## ColorRush bootstrap. Loads scenario JSON (CRS_SCENARIO env), builds the
## world/track/vehicles, wires camera director, event recorder, drivers,
## audio and slow-motion, then quits once the scenario duration elapses.
## Movie Maker output is requested externally via --write-movie CLI args.

const WorldBuilder := preload("res://scripts/environment/world_builder.gd")
const TrackBuilder := preload("res://scripts/track/track_builder.gd")
const CarFactory := preload("res://scripts/vehicles/car_factory.gd")
const EventRecorder := preload("res://scripts/render/event_recorder.gd")
const CameraDirector := preload("res://scripts/camera/camera_director.gd")
const SlowmoController := preload("res://scripts/render/slowmo_controller.gd")
const AudioDirector := preload("res://scripts/render/audio_director.gd")
const DriverAIScript := preload("res://scripts/ai/driver_ai.gd")

var scenario: Dictionary = {}
var layout: Dictionary = {}
var vehicles: Array = []
var drivers: Array = []
var recorder: EventRecorder
var director: CameraDirector
var slowmo: SlowmoController
var acast: AudioDirector
var track_builder: TrackBuilder
var sim_time: float = 0.0
var duration: float = 20.0
var movie_mode: bool = false


func _ready() -> void:
	movie_mode = OS.get_environment("CRS_MOVIE_FORMAT") != "none"
	_load_scenario()
	_apply_quality(OS.get_environment("CRS_QUALITY").to_lower())
	_add_world()
	_spawn_vehicles()
	_add_track()
	_add_camera()
	_add_systems()
	print("[CRS] Ready: vehicles=%d duration=%.1fs quality=%s audio=%s" % [
		vehicles.size(), duration, OS.get_environment("CRS_QUALITY"),
		"1" if OS.get_environment("CRS_ALLOW_AUDIO") == "1" else "0"])


func _load_scenario() -> void:
	var path := OS.get_environment("CRS_SCENARIO")
	if path.is_empty():
		path = "res://scenarios/smoke_default.json"
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		push_error("[CRS] Scenario not found: " + path)
		get_tree().quit(2)
		return
	var parsed: Variant = JSON.parse_string(f.get_as_text())
	if typeof(parsed) != TYPE_DICTIONARY:
		push_error("[CRS] Scenario JSON invalid.")
		get_tree().quit(2)
		return
	scenario = parsed
	layout = scenario.get("layout", {})
	duration = float(scenario.get("duration_seconds", 20.0))
	var out: Dictionary = scenario.get("output", {})
	var w := int(out.get("width", 1080))
	var h := int(out.get("height", 1920))
	if movie_mode:
		DisplayServer.window_set_size(Vector2i(w, h))


func _apply_quality(quality: String) -> void:
	var vp := get_viewport().get_viewport_rid()
	match quality:
		"low":
			RenderingServer.viewport_set_msaa_3d(vp, RenderingServer.VIEWPORT_MSAA_DISABLED)
			RenderingServer.directional_soft_shadow_filter_set_quality(RenderingServer.SHADOW_QUALITY_HARD)
		"high":
			RenderingServer.viewport_set_msaa_3d(vp, RenderingServer.VIEWPORT_MSAA_4X)
		_:
			RenderingServer.viewport_set_msaa_3d(vp, RenderingServer.VIEWPORT_MSAA_2X)


func _add_world() -> void:
	var wb := WorldBuilder.new()
	add_child(wb.build_environment(scenario, false))


func _add_track() -> void:
	track_builder = TrackBuilder.new()
	add_child(track_builder)
	track_builder.build_all(scenario)
	track_builder.zone_entered.connect(_on_zone_entered)
	for zone in layout.get("zones", []):
		track_builder.build_zone(zone)


func _spawn_vehicles() -> void:
	var factory := CarFactory.new()
	factory.name = "CarFactory"
	add_child(factory)
	var specs: Array = scenario.get("vehicles", {}).get("specs", [])
	var spawns: Array = layout.get("spawns", [])
	var count: int = layout.get("vehicles_count", specs.size())
	count = mini(count, 12)
	for i in range(mini(specs.size(), spawns.size())):
		var spec: Dictionary = specs[i].duplicate()
		var spawn: Array = spawns[i]
		var car: RigidBody3D = factory.build(spec)
		add_child(car)
		var pos := Vector3(float(spawn[0]), 0.5 + car.preferred_spawn_y() + 0.05, float(spawn[2]))
		car.global_position = pos
		var yaw := -PI / 2.0 + deg_to_rad(float(spec.get("spawn_yaw_deg", 0.0)))
		car.rotation = Vector3(0, yaw, 0)
		car.set_respawn(pos, yaw)
		vehicles.append(car)


func _add_camera() -> void:
	director = CameraDirector.new()
	director.name = "CameraDirector"
	add_child(director)
	director.setup(scenario, vehicles)


func _add_systems() -> void:
	recorder = EventRecorder.new()
	recorder.name = "EventRecorder"
	recorder.events_output_path = OS.get_environment("CRS_EVENTS")
	add_child(recorder)
	for car in vehicles:
		recorder.register_vehicle(car, str(car.get("car_label")))
	recorder.event_logged.connect(_on_recorder_event)
	slowmo = SlowmoController.new()
	slowmo.name = "Slowmo"
	add_child(slowmo)
	slowmo.setup(scenario)
	acast = AudioDirector.new()
	acast.name = "Audio"
	add_child(acast)
	acast.sfx_enabled = OS.get_environment("CRS_ALLOW_AUDIO") == "1"
	for car in vehicles:
		acast.register_vehicle(car)
	var music_path := str(scenario.get("output", {}).get("music_path", ""))
	if not music_path.is_empty():
		acast.add_background_music(music_path, float(scenario["output"].get("music_volume", 0.35)))

	# Driver AIs: racing line is derived from road geometry, not camera hints.
	var line := _build_racing_line()
	for i in range(vehicles.size()):
		var car: RigidBody3D = vehicles[i]
		var ai := DriverAIScript.new()
		add_child(ai)
		ai.setup(car, line, i, vehicles.size(),
			str(car.spec.get("style", "balanced")),
			bool(scenario.get("sim", {}).get("recovery", true)))
		drivers.append(ai)


func _build_racing_line() -> Array:
	var points: Array = []
	var is_spiral: bool = scenario.get("track", {}).get("type", "") == "spiral_race"
	# collect (order_key, point) pairs; sort by forward X for straight tracks
	var items: Array = []
	for seg in layout.get("road", []):
		var at := Vector3(seg["from"][0], seg["from"][1], seg["from"][2])
		var kind: String = seg.get("kind", "platform")
		if kind == "ramp":
			var s: Array = seg["size"]
			var a_deg: float = seg["rot_deg"][2] if seg.get("rot_deg") != null else 0.0
			var dir := Vector3(cos(deg_to_rad(a_deg)), sin(deg_to_rad(a_deg)), 0.0)
			var half: float = s[0] / 2.0
			items.append([at.x - dir.x * half, at - dir * half])
			items.append([at.x + dir.x * half, at + dir * half])
		else:
			items.append([at.x, at])
	for zone in layout.get("zones", []):
		if zone.get("type", "") in ["launch", "landing", "finish", "checkpoint"]:
			var p := Vector3(zone["at"][0], zone["at"][1], zone["at"][2])
			items.append([p.x, p])
	if not is_spiral:
		items.sort_custom(func(a, b): return float(a[0]) < float(b[0]))
	for item in items:
		var pt: Vector3 = item[1]
		if points.is_empty() or (pt - points[points.size() - 1]).length() > 3.0:
			points.append(pt)
	# start with spawn center so cars begin on the line
	if not vehicles.is_empty():
		var start: Vector3 = vehicles[0].global_position
		if (start - points[0]).length() > 3.0:
			points.push_front(start)
	return points


func _on_zone_entered(zone: Dictionary, body: Node3D) -> void:
	var ztype: String = str(zone.get("type", ""))
	if not is_instance_valid(body):
		return
	var idx: int = -1
	if body.get("crs_index") != null:
		pass
	if body.has_meta("crs_index"):
		idx = int(body.get_meta("crs_index"))
	if idx < 0:
		return
	match ztype:
		"launch":
			recorder.log_launch(idx)
			slowmo.trigger("launch", 0.7)
			acast.play_sfx("launch", body.global_position)
			if idx == 0 and director:
				director.on_event({"type": "launch", "vehicle": idx})
		"finish":
			var car: RigidBody3D = vehicles[idx]
			car.finished_flag = true
			car.finished.emit()
			acast.play_sfx("land", body.global_position)
		"landing":
			pass
		_:
			pass


func _on_recorder_event(ev: Dictionary) -> void:
	# forward sim events to the camera director + slow motion, with thresholds
	# so noisy contacts don't hijack the cut
	var etype: String = str(ev.get("type", ""))
	var idx: int = int(ev.get("vehicle", 0))
	# live-position SFX follow the event stream too (export mixes later;
	# this only affects what speakers hear during a manual godot run)
	if acast and idx >= 0 and idx < vehicles.size():
		match etype:
			"crash":
				if float(ev.get("severity", 0.0)) > 6.0:
					acast.play_sfx("impact", vehicles[idx].global_position)
			"fall":
				acast.play_sfx("fall", vehicles[idx].global_position)
	if director == null:
		return
	match etype:
		"land":
			if float(ev.get("impact_speed", 0.0)) > 11.0:
				director.on_event({"type": "land", "vehicle": idx})
				slowmo.trigger("land", 0.8)
		"crash":
			if float(ev.get("severity", 0.0)) > 6.0:
				director.on_event({"type": "crash", "vehicle": idx})
				slowmo.trigger("crash", 0.9)
		"fall":
			director.on_event({"type": "fall", "vehicle": idx})
		_:
			pass


func _physics_process(delta: float) -> void:
	sim_time += delta
	var dbg := int(sim_time * 2.0)
	if dbg != int((sim_time - delta) * 2.0) and not vehicles.is_empty():
		if OS.get_environment("CRS_TRACK_TRACE") == "1":
			var trace_parts := []
			for i in range(drivers.size()):
				var drv = drivers[i]
				trace_parts.append("v%d lat=%.1f seg=%d spd=%.1f fgnd=%d pos=%v" % [
					i, drv.lateral_error(), drv.current_segment,
					drv.car.linear_velocity.length(),
					drv.car.grounded_count, drv.car.global_position])
			print("[CRS trace t=%.2f] %s" % [sim_time, " | ".join(trace_parts)])
		if OS.get_environment("CRS_DBG_ALL") == "1":
			var parts := []
			for i in range(vehicles.size()):
				var v: RigidBody3D = vehicles[i]
				parts.append("v%d pos=%v spd=%.1f fgnd=%d thr=%.1f brk=%.1f steer=%.2f" % [
					i, v.global_position, v.linear_velocity.length(),
					v.grounded_count, v.throttle, v.brake, v.target_steer])
			print("[CRS all t=%.2f] %s" % [sim_time, " | ".join(parts)])
		else:
			var c: RigidBody3D = vehicles[0]
			print("[CRS dbg] t=%.2f pos=%v spd=%.2f thr=%.2f steer=%.2f fgnd=%d air=%.2f" % [
				sim_time, c.global_position, c.linear_velocity.length(),
				c.throttle, c.target_steer, c.grounded_count, c.airtime])
	for ai in drivers:
		ai.drive(delta)
	if sim_time >= duration:
		recorder.write_out()
		print("[CRS] Done sim_time=%.2fs events=%d" % [sim_time, recorder.events.size()])
		get_tree().quit(0)


func get_sim_time() -> float:
	return sim_time
