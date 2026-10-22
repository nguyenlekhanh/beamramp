extends Node
class_name AudioDirector
## Procedural/looping SFX and optional background music, with ducking.
## All sounds are original synthesized WAVs shipped in assets/audio.
## NOTE: the exported MP4 audio is mixed deterministically in post
## (app/services/audio_mixer.py); this node only affects live speaker audio.

var sfx_enabled: bool = true
var music_player: AudioStreamPlayer = null
var engine_players: Array = []
var _music_base_db: float = -9.0


func register_vehicle(car: RigidBody3D) -> void:
	if not sfx_enabled or car == null:
		return
	var stream: AudioStream = load("res://assets/audio/engine_loop.wav")
	if stream == null:
		return
	var p := AudioStreamPlayer3D.new()
	if stream is AudioStreamWAV:
		(stream as AudioStreamWAV).loop_mode = AudioStreamWAV.LOOP_FORWARD
	p.stream = stream
	p.volume_db = -18.0
	p.pitch_scale = 0.55
	p.max_distance = 120.0
	p.set_meta("car", car)
	car.add_child(p)
	p.play()
	engine_players.append(p)


func _process(_delta: float) -> void:
	if engine_players.is_empty():
		return
	for p in engine_players:
		if not is_instance_valid(p) or not p.playing:
			continue
		var car: RigidBody3D = p.get_meta("car")
		if car == null:
			continue
		var spd := car.linear_velocity.length()
		# idle hum -> strained engine as speed and throttle rise
		var tgt := 0.55 + clampf(spd / 30.0, 0.0, 1.0) * 0.55
		p.pitch_scale = lerpf(p.pitch_scale, tgt, 0.1)
		p.volume_db = lerpf(p.volume_db, -18.0 + clampf(car.throttle, 0.0, 1.0) * 5.0, 0.1)


func add_background_music(path: String, volume: float = 0.35) -> void:
	if path.is_empty():
		return
	if not FileAccess.file_exists(path):
		push_warning("[CRS] music file not found: " + path)
		return
	var apath := path if path.is_absolute_path() or FileAccess.file_exists(path) \
		else "res://" + path.replace("\\", "/")
	music_player = AudioStreamPlayer.new()
	var stream: AudioStream = load(apath)
	if stream == null:
		push_warning("[CRS] cannot load music: " + path)
		return
	stream.loop_mode = AudioStreamWAV.LOOP_FORWARD if stream is AudioStreamWAV else stream.loop_mode
	music_player.stream = stream
	_music_base_db = linear_to_db(volume)
	music_player.volume_db = _music_base_db
	add_child(music_player)
	music_player.play()


func play_sfx(kind: String, at_world_pos: Vector3) -> void:
	if not sfx_enabled:
		return
	var map := {
		"impact": "res://assets/audio/impact_hit.wav",
		"land": "res://assets/audio/land_thud.wav",
		"launch": "res://assets/audio/whoosh.wav",
		"fall": "res://assets/audio/land_thud.wav",
	}
	var path: String = map.get(kind, "")
	if path.is_empty():
		return
	var stream: AudioStream = load(path)
	if stream == null:
		return
	var p := AudioStreamPlayer3D.new()
	p.stream = stream
	p.volume_db = -6.0 if kind != "impact" else -2.0
	p.max_distance = 90.0
	add_child(p)
	p.global_position = at_world_pos
	p.play()


func duck_music(seconds: float) -> void:
	if music_player == null:
		return
	var db: float = maxf(_music_base_db - 9.0, -40.0)
	var t := create_tween()
	t.tween_property(music_player, "volume_db", db, 0.12)
	t.tween_interval(seconds)
	t.tween_property(music_player, "volume_db", _music_base_db, 0.3)
