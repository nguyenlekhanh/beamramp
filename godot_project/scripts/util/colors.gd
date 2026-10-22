extends Object
## Shared flat-material / paint helpers with cached instances.

static var _cache: Dictionary = {}


static func mat_flat(color_hex: String) -> StandardMaterial3D:
	return mat(color_hex, false, false)


static func mat(color_hex: String, metallic: bool = false, unshaded: bool = false) -> StandardMaterial3D:
	var key := color_hex + "|" + str(metallic) + "|" + str(unshaded)
	if _cache.has(key):
		return _cache[key]
	var m := StandardMaterial3D.new()
	m.albedo_color = Color(color_hex)
	m.roughness = 0.55 if not metallic else 0.25
	m.metallic = 0.8 if metallic else 0.0
	if unshaded:
		m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	if not _cache.has(key):
		_cache[key] = m
	return m


static func palette() -> Array:
	return ["#e63946", "#1d71f2", "#ffd166", "#2a9d61", "#8338ec", "#ff8c1a"]
