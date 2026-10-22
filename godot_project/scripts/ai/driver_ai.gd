extends Node3D
class_name DriverAI
## Pure-pursuit racing-line follower with style-based throttle, pre-brake
## for obstacles, recovery resets, and mid-air mild corrections.

const Helpers := preload("res://scripts/util/helpers.gd")

var car: RigidBody3D
var line_points: Array = []     # Vector3 world waypoints
var line_offsets: Array = []    # lateral per-line progress (unused yet)
var lane_lateral: float = 0.0
var index: int = 0
var style: String = "balanced"
var target_speed_scale: float
var brake_distance: float
var recovery_enabled: bool = true
var finished: bool = false
var current_segment: int = 0
var steer_nudge: float = 0.0
var offroad_timer: float = 0.0
const OFFROAD_LAT := 14.0        # farther than this from the racing line counts as lost
const OFFROAD_RECOVERY_SEC := 3.5


func setup(car_body: RigidBody3D, line: Array, lane_index: int, lane_count: int,
		car_style: String, recovery: bool) -> void:
	car = car_body
	line_points = []
	for p in line:
		line_points.append(Vector3(p[0], p[1], p[2]))
	style = car_style
	recovery_enabled = recovery
	match style:
		"cautious":
			target_speed_scale = 0.72
			brake_distance = 16.0
		"aggressive":
			target_speed_scale = 1.0
			brake_distance = 7.0
		_:
			target_speed_scale = 0.88
			brake_distance = 11.0
	if lane_count <= 1:
		lane_lateral = 0.0
	else:
		lane_lateral = ((float(lane_index) - (lane_count - 1.0) / 2.0)) * 3.2


func drive(delta: float) -> void:
	if car == null or line_points.size() < 2:
		return
	var local_speed: float = car.linear_velocity.length()
	# find target point ahead on the line
	var target := _pursuit_target()
	var to_target_local := car.to_local(target)
	# local +X is world-left for a +X-facing car; negative steer turns left
	var steer_err := atan2(to_target_local.x + steer_nudge, -to_target_local.z)
	steer_nudge = lerpf(steer_nudge, 0.0, minf(2.0 * delta, 1.0))

	var speed_cap: float = car.max_speed * target_speed_scale
	car.throttle = 1.0
	car.brake = 0.0
	if not car.finished_flag:
		# pre-brake for slow obstacles ahead
		if _obstacle_ahead(local_speed) and local_speed > 9.0:
			car.brake = 0.85
			car.throttle = 0.0
		if local_speed > speed_cap:
			car.throttle = 0.0
			car.brake = 0.25
	else:
		# finished: brake to a straight stop on the line; obstacle dodging
		# after the finish caused whip-steer spins and off-road falls
		steer_nudge = 0.0
		car.throttle = 0.0
		car.brake = 1.0

	var steer_limit: float = car.max_steer if not car.finished_flag else 0.25
	car.target_steer = clampf(-steer_err * 1.25, -steer_limit, steer_limit)
	# mid-air stability: slight pitch forward to match landing
	if car.airtime > 0.6 and not car.finished_flag:
		car.apply_torque(-car.global_basis.x * 60.0 * delta)
	_update_offroad_recovery(delta)


## Lateral distance from the car to its projection on the racing line
## segment nearest (by forward order) the car.
func lateral_error() -> float:
	if line_points.size() < 2:
		return 0.0
	var i := clampi(current_segment, 0, line_points.size() - 2)
	var a: Vector3 = line_points[i]
	var b: Vector3 = line_points[i + 1]
	var seg: Vector3 = b - a
	var seg_len2 := seg.length_squared()
	if seg_len2 < 0.01:
		return 0.0
	var t := clampf((car.global_position - a).dot(seg) / seg_len2, 0.0, 1.0)
	var proj: Vector3 = a + seg * t
	return (car.global_position - proj).length()


func _update_offroad_recovery(delta: float) -> void:
	if not recovery_enabled or car.finished_flag:
		offroad_timer = 0.0
		return
	# the timer needs the full window; real jumps stay well under 14 m from
	# the line (straight chord vs ballistic arc) and nowhere near 3.5 s, so
	# intentional stunts never trigger recovery
	if lateral_error() > OFFROAD_LAT:
		offroad_timer += delta
		if car.owner != null and car.owner.has_method("get_sim_time"):
			print("[CRS offroad] v%d lat=%.1f t=%.1f (timer %.1fs)" % [
				index, lateral_error(), car.owner.get_sim_time(), offroad_timer])
		if offroad_timer >= OFFROAD_RECOVERY_SEC:
			print("[CRS offroad] v%d recovered to spawn" % index)
			car.recover_to_spawn()
			offroad_timer = 0.0
	else:
		offroad_timer = 0.0


func _pursuit_target() -> Vector3:
	# advance along the line, project onto segment
	var pos := car.global_position
	if current_segment >= line_points.size() - 1:
		current_segment = line_points.size() - 2
	for i in range(current_segment, line_points.size() - 1):
		var a: Vector3 = line_points[i]
		var b: Vector3 = line_points[i + 1]
		var seg_len := (b - a).length()
		if seg_len < 1.0:
			continue
		var t: float = clampf((pos - a).dot(b - a) / (seg_len * seg_len), 0.0, 1.0)
		var proj := a + (b - a) * t
		if (proj - pos).length() < 4.0 or (b - pos).length() < (b - proj).length():
			if i > current_segment:
				current_segment = i
	var lookahead: float = clampf(car.linear_velocity.length() * 0.55, 6.0, 34.0)
	# ---- line-anchored pure pursuit ------------------------------------------
	# The target must sit ON the racing line (projection + lookahead SLID
	# along the polyline), never on a parallel line through the car.
	# Car-anchored targets never correct lateral offset, which let cars
	# drift off-road indefinitely (bug: gap_jump seed 4242 vehicle 2).
	var a0: Vector3 = line_points[current_segment]
	var i_seg := current_segment
	var s: float = 0.0
	var dist_to_end: float = 0.0
	if i_seg < line_points.size() - 1:
		var seg_full: Vector3 = line_points[i_seg + 1] - line_points[i_seg]
		var seg_len2 := seg_full.dot(seg_full)
		if seg_len2 > 0.01:
			s = clampf((pos - a0).dot(seg_full) / seg_len2, 0.0, 1.0)
		# distance still to travel along the line from the projection
		dist_to_end = (1.0 - s) * seg_full.length()
		for j in range(i_seg + 1, line_points.size() - 1):
			dist_to_end += (line_points[j + 1] - line_points[j]).length()
	# short heuristic lookahead near the line end so the car glides to a
	# stop on the line instead of whipping around an end point behind it
	lookahead = minf(lookahead, maxf(dist_to_end + 2.0, 4.0))
	var target: Vector3 = a0
	var remaining := lookahead
	while true:
		if i_seg >= line_points.size() - 1:
			target = line_points[line_points.size() - 1]
			break
		var a_i: Vector3 = line_points[i_seg]
		var b_i: Vector3 = line_points[i_seg + 1]
		var seg_i: Vector3 = b_i - a_i
		var have: float = (1.0 - s) * seg_i.length()
		if have >= remaining:
			var dir_i := seg_i / maxf(seg_i.length(), 0.01)
			target = a_i + dir_i * (s * seg_i.length() + remaining)
			break
		remaining -= have
		s = 0.0
		i_seg += 1
	# overshoot: driving past the line end used to put the target BEHIND the
	# car (projection clamps), producing whip-steer circles and off-road spins
	var end_pt: Vector3 = line_points[line_points.size() - 1]
	var neck_seg: Vector3 = line_points[line_points.size() - 1] - line_points[line_points.size() - 2]
	var run_dir := neck_seg
	run_dir.y = 0.0
	run_dir = run_dir.normalized()
	var past := (pos - end_pt).dot(run_dir)
	if past > 0.5:
		return end_pt + run_dir * (past + lookahead)
	# lateral lane offset is applied perpendicular to the reached segment,
	# so lane intent survives the line-anchored projection
	if i_seg >= line_points.size() - 1:
		i_seg = line_points.size() - 2
	var seg_dir: Vector3 = line_points[i_seg + 1] - line_points[i_seg]
	seg_dir.y = 0.0
	seg_dir = seg_dir.normalized()
	var lateral := Vector3(-seg_dir.z, 0.0, seg_dir.x) * lane_lateral
	return target + lateral


func _obstacle_ahead(speed: float) -> bool:
	if car.owner == null:
		return false
	var obs: Array = car.owner.get("obstacle_watchlist")
	if obs == null:
		return false
	var fwd := -car.global_basis.z
	for ob in obs:
		if not is_instance_valid(ob):
			continue
		var rel: Vector3 = ob.global_position - car.global_position
		var along := rel.dot(fwd)
		if along < 2.0 or along > speed * 0.9 + brake_distance * 1.4:
			continue
		var side_err: float = absf(rel.cross(fwd).length())
		if side_err < 3.0:
			steer_nudge = -signf(rel.dot(car.global_basis.x)) * 2.2
			return true
	return false
