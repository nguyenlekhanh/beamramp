extends Node
class_name SlowmoController
## Controlled slow-motion on selected events with consistent time mapping.
##
## Slow motion "borrows" wall time from the rest of the cut. The simulation
## advances slower while Engine.time_scale < 1, so after each slowmo window a
## catch-up phase at a bounded, gently-eased boost (>1x) repays the borrowed
## sim time. The total debt is budgeted against the remaining simulation
## horizon, which guarantees the simulation still reaches its full duration by
## the end of the movie (video length stays within ffmpeg tolerance).
##
## Tuning knobs all live in the scenario: ``sim.slowmo = {
##   "enabled": bool,          # master switch
##   "factor": 0.2..0.8,       # slow-motion speed (1 = off)
##   "dur":    seconds,        # per-trigger duration multiplier
##   "max_boost": 1.2..2.0,    # catch-up speed cap (never visible snap)
##   "min_gap": seconds,       # cooldown between triggers of the same kind
##   "budget_fraction": 0..1,  # max share of remaining sim time that may be used
## }
## Physics is never modified here; only Engine.time_scale is used.

var scenario_config: Dictionary = {}
var factor: float = 0.45
var dur_scale: float = 1.0
var enabled: bool = true
var max_boost: float = 1.5
var budget_fraction: float = 0.4
var horizon: float = 20.0
var boost: float = 1.0
var active_t: float = 0.0
var active_dur: float = 0.0
var catchup_credit: float = 0.0
var triggers: Dictionary = {"land": true, "launch": true, "crash": true}
var min_interval: Dictionary = {"land": 2.2, "launch": 2.2, "crash": 2.2}
var last_kind_t: Dictionary = {}


func setup(sc: Dictionary) -> void:
	scenario_config = sc
	horizon = float(sc.get("duration_seconds", 20.0))
	var slowmo: Dictionary = sc.get("sim", {}).get("slowmo", {})
	enabled = bool(slowmo.get("enabled", true))
	factor = clampf(float(slowmo.get("factor", 0.45)), 0.1, 0.8)
	dur_scale = clampf(float(slowmo.get("dur", 1.0)), 0.4, 1.6)
	max_boost = clampf(float(slowmo.get("max_boost", 1.5)), 1.05, 2.0)
	budget_fraction = clampf(float(slowmo.get("budget_fraction", 0.4)), 0.05, 1.0)
	var gap: float = float(slowmo.get("min_gap", 2.2))
	for key in triggers.keys():
		if slowmo.has(key):
			triggers[key] = bool(slowmo[key])
	for key in min_interval.keys():
		if slowmo.has("min_gap_" + str(key)):
			min_interval[key] = float(slowmo["min_gap_" + str(key)])
		else:
			min_interval[key] = gap
	Engine.time_scale = 1.0


func remaining_time() -> float:
	return maxf(horizon - elapsed_sim_total(), 0.0)


func elapsed_sim_total() -> float:
	var mn := get_parent()
	if mn and mn.has_method("get_sim_time"):
		return float(mn.get_sim_time())
	return 0.0


## Base trigger window; widened when the camera wants a longer beat.
func window_for(kind: String) -> float:
	match kind:
		"launch":
			return clampf(0.9 * dur_scale, 0.35, 1.6)
		"land":
			return clampf(0.75 * dur_scale, 0.3, 1.4)
		"crash":
			return clampf(0.85 * dur_scale, 0.35, 1.5)
	return 0.7


func _allowed(kind: String, dur: float) -> bool:
	if not enabled or not triggers.get(kind, true):
		return false
	if active_dur > 0.0 or catchup_credit > 0.0:
		# never stack windows and never fight the catch-up phase
		return false
	if remaining_time() < dur + 1.5:  # keep the final second at normal speed
		return false
	var min_gap: float = float(min_interval.get(kind, 2.2))
	var last: float = float(last_kind_t.get(kind, -999.0))
	if elapsed_sim_total() - last < min_gap:
		return false
	# debt budget: borrowed time must fit within the remaining sim horizon
	var debt := dur * (1.0 - factor)
	if debt > remaining_time() * budget_fraction:
		return false
	return true


func trigger(kind: String, dur: float = 1.1) -> void:
	var window := clampf(dur * dur_scale, 0.2, 1.8)
	if not _allowed(kind, window):
		return
	Engine.time_scale = factor
	active_t = 0.0
	active_dur = window
	catchup_credit = window * (1.0 - factor)
	last_kind_t[kind] = elapsed_sim_total()
	print("[CRS slowmo] kind=%s t=%.2f dur=%.2f credit=%.2f" % [kind, elapsed_sim_total(), window, catchup_credit])


func _physics_process(delta_time: float) -> void:
	if active_dur > 0.0:
		active_t += delta_time
		if active_t >= active_dur:
			active_dur = 0.0
			if catchup_credit > 0.02:
				boost = 1.0  # ramp up gently below
			else:
				Engine.time_scale = 1.0
				catchup_credit = 0.0
		return
	if catchup_credit > 0.0:
		# ramp: ease away from 1.0x (no speed snap), hold max_boost,
		# ease back out as credit approaches zero
		var let_boost: float = max_boost if catchup_credit > 0.75 else 1.0 + (max_boost - 1.0) * (catchup_credit / 0.75)
		var target := minf(let_boost, max_boost)
		boost = lerpf(boost, target, clampf(2.5 * delta_time, 0.0, 1.0))
		Engine.time_scale = clampf(boost, 1.0, max_boost)
		catchup_credit -= delta_time * (Engine.time_scale - 1.0)
		if catchup_credit <= 0.0:
			catchup_credit = 0.0
			print("[CRS slowmo] catchup done t=%.2f (debt repaid)" % elapsed_sim_total())
		elif remaining_time() <= 0.1:
			catchup_credit = 0.0
			Engine.time_scale = 1.0
