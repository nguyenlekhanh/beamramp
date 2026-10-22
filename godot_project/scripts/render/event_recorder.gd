extends Node
class_name EventRecorder
## Collects simulation events (launch/land/crash/fall/finish) from vehicles
## and zones, streams them to the camera director, and writes events.json.

signal vehicle_launch(index: int, vehicle: Node3D)
signal zone_signal(type: String, index: int)
signal event_logged(ev: Dictionary)

var events: Array = []
var vehicles: Array = []
var vehicle_labels: Array = []
var vehicle_index_map: Dictionary = {}
var events_output_path: String = ""
var cooldowns: Dictionary = {}


func register_vehicle(car: RigidBody3D, label: String) -> void:
	if car == null:
		return
	var idx: int = vehicles.size()
	vehicles.append(car)
	vehicles[idx].set_meta("crs_index", idx)
	vehicle_labels.append(label)
	vehicle_index_map[car.get_instance_id()] = idx
	car.landed.connect(_on_land.bind(idx))
	car.crashed.connect(_on_crash.bind(idx))
	car.fell.connect(_on_fall.bind(idx))
	car.finished.connect(_on_finished.bind(idx))


func on_zone_entered(zone: Dictionary, body: Node3D) -> void:
	var ztype: String = str(zone.get("type", ""))
	if not (body is RigidBody3D and vehicle_index_map.has(body.get_instance_id())):
		return
	var idx: int = int(vehicle_index_map[body.get_instance_id()])
	_log_event({"type": "zone_" + ztype, "t": _sim_time(), "vehicle": idx})
	zone_signal.emit(ztype, idx)
	if ztype == "finish":
		pass


func write_out() -> void:
	if events_output_path.is_empty():
		return
	var f := FileAccess.open(events_output_path, FileAccess.WRITE)
	if f == null:
		push_warning("[CRS] cannot write events file: " + events_output_path)
		return
	f.store_string(JSON.stringify({
		"schema": "crs.events/1",
		"vehicles": vehicle_labels,
		"events": events,
	}, "  "))


func _on_land(impact_speed: float, idx: int) -> void:
	_log_event({"type": "land", "t": _sim_time(), "vehicle": idx, "impact_speed": impact_speed}, "land:%d" % idx, 0.4)


func _on_crash(body: Node3D, severity: float, idx: int) -> void:
	_log_event({"type": "crash", "t": _sim_time(), "vehicle": idx,
		"other": body.name if body else "", "severity": severity}, "crash:%d" % idx, 0.9)


func _on_fall(idx: int) -> void:
	_log_event({"type": "fall", "t": _sim_time(), "vehicle": idx})


func _on_finished(idx: int) -> void:
	_log_event({"type": "finish", "t": _sim_time(), "vehicle": idx})


func log_launch(idx: int) -> void:
	_log_event({"type": "launch", "t": _sim_time(), "vehicle": idx})


func _sim_time() -> float:
	var mn := get_parent()
	if mn and mn.has_method("get_sim_time"):
		return mn.get_sim_time()
	return 0.0


func _log_event(ev: Dictionary, cd_key: String = "", cd: float = 0.0) -> void:
	if not cd_key.is_empty():
		var cdok: bool = not cooldowns.has(cd_key) \
			or float(ev["t"]) - float(cooldowns[cd_key]) >= cd
		if not cdok:
			return
		cooldowns[cd_key] = float(ev["t"])
	events.append(ev)
	event_logged.emit(ev)
