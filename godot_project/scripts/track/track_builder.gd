extends Node
## Builds every road segment, zone and obstacle from the generated layout.

signal zone_entered(zone: Dictionary, body: Node3D)

const COLORS := preload("res://scripts/util/colors.gd")
const ObstacleScripts := {
	"hammer": preload("res://scripts/obstacles/hammer_swing.gd"),
	"ball": preload("res://scripts/obstacles/ball_roller.gd"),
	"cone": preload("res://scripts/obstacles/cone.gd"),
	"barrier": preload("res://scripts/obstacles/barrier_at.gd"),
	"moving_platform": preload("res://scripts/obstacles/moving_platform.gd"),
	"rotator": preload("res://scripts/obstacles/rotator.gd"),
	"crash_wall": preload("res://scripts/obstacles/crash_wall.gd"),
}


func build_all(scenario: Dictionary) -> void:
	var layout: Dictionary = scenario.get("layout", {})
	for seg in layout.get("road", []):
		build_segment(seg)


func build_segment(seg: Dictionary) -> void:
	var kind: String = seg.get("kind", "platform")
	var at: Vector3 = _v3(seg["from"])
	var size: Vector3 = _v3(seg["size"])
	var color: Color = COLORS.mat_flat(seg.get("color", "#6c757d")).albedo_color
	var rot: Vector3 = Vector3.ZERO
	if seg.get("rot_deg") != null:
		var r: Array = seg["rot_deg"]
		rot = Vector3(r[0], r[1], r[2])

	if kind == "gap_marker":
		var mi := MeshInstance3D.new()
		var bm := BoxMesh.new()
		bm.size = size
		mi.mesh = bm
		mi.material_override = COLORS.mat_flat(seg.get("color", "#22223b"))
		mi.position = at
		mi.rotation_degrees = rot
		add_child(mi)
		return

	var body := StaticBody3D.new()
	body.collision_layer = 1
	body.collision_mask = 0
	body.position = at
	body.rotation_degrees = rot
	var shape := BoxShape3D.new()
	shape.size = size
	var cs := CollisionShape3D.new()
	cs.shape = shape
	body.add_child(cs)
	var mesh := BoxMesh.new()
	mesh.size = size
	var mesh_i := MeshInstance3D.new()
	mesh_i.mesh = mesh
	mesh_i.material_override = COLORS.mat_flat(seg.get("color", "#6c757d"))
	mesh_i.position = Vector3.ZERO
	body.add_child(mesh_i)
	if kind == "ramp":
		# side skirts for readability
		var skirt := MeshInstance3D.new()
		var bm2 := BoxMesh.new()
		bm2.size = Vector3(size.x, maxf(0.4, size.y), size.z + 1.2)
		skirt.mesh = bm2
		var m2 := StandardMaterial3D.new()
		m2.albedo_color = Color(seg.get("color", "#ffd166"))
		m2.albedo_color.a = 0.35
		m2.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		skirt.material_override = m2
		skirt.position = Vector3(0, -size.y * 0.25, 0)
		body.add_child(skirt)
	add_child(body)


func build_zone(zone: Dictionary) -> void:
	var area := Area3D.new()
	area.position = _v3(zone["at"])
	var shape := BoxShape3D.new()
	shape.size = _v3(zone["size"])
	var cs := CollisionShape3D.new()
	cs.shape = shape
	area.add_child(cs)
	if zone.get("type", "") in ["launch", "landing", "finish"]:
		var mesh := BoxMesh.new()
		mesh.size = _v3(zone["size"])
		var mi := MeshInstance3D.new()
		mi.mesh = mesh
		var m := StandardMaterial3D.new()
		m.albedo_color = Color("#ff9f1c") if zone["type"] == "launch" else (Color("#2a9d61") if zone["type"] == "landing" else Color("#ffd166"))
		m.albedo_color.a = 0.18
		m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		mi.material_override = m
		area.add_child(mi)
	area.set_meta("zone", zone)
	area.body_entered.connect(_on_zone_body.bind(zone))
	# vehicles live on collision layer 2; with the default mask (1) the zone
	# only fired when another object's layer happened to match
	area.collision_layer = 4
	area.collision_mask = 1 | 2
	add_child(area)


func _on_zone_body(body: Node3D, zone: Dictionary) -> void:
	zone_entered.emit(zone, body)


func build_obstacle(ob: Dictionary) -> void:
	var type: String = str(ob.get("type", ""))
	if not ObstacleScripts.has(type):
		push_warning("[CRS] unknown obstacle type: " + type)
		return
	var inst: Node = ObstacleScripts[type].new()
	add_child(inst)
	inst.setup(ob)


static func _v3(arr) -> Vector3:
	return Vector3(arr[0], arr[1], arr[2])
