extends Node3D

var tick := 0
var bodies: Array[RigidBody3D] = []
var snapshot := PackedByteArray()
var log_file: FileAccess
var passes := 1

func _ready() -> void:
	var setting := OS.get_environment("TICK_GODOT_BODIES")
	var count := 128 if setting.is_empty() else setting.to_int()
	if count < 1 or count > 2048:
		push_error("TICK_GODOT_BODIES: ожидается число от 1 до 2048")
		get_tree().quit(2)
		return
	var passes_setting := OS.get_environment("TICK_GODOT_PASSES")
	passes = 1 if passes_setting.is_empty() else passes_setting.to_int()
	if passes < 1 or passes > 4096:
		push_error("TICK_GODOT_PASSES: ожидается число от 1 до 4096")
		get_tree().quit(2)
		return
	var shape := BoxShape3D.new()
	for i in range(count):
		var body := RigidBody3D.new()
		body.gravity_scale = 0.0
		body.can_sleep = false
		body.position = Vector3(i % 8 * 1.1, (i >> 3) % 8 * 1.1, (i >> 6) * 1.1)
		var collision := CollisionShape3D.new()
		collision.shape = shape
		body.add_child(collision)
		add_child(body)
		bodies.append(body)
	# У страниц одинаковое содержимое между контейнерами и разные номера внутри снимка.
	snapshot.resize(8 * 1024 * 1024)
	snapshot.fill(90)
	for page in range(2048):
		snapshot.encode_u32(page * 4096, page)
	log_file = FileAccess.open("/out/ticks.csv", FileAccess.WRITE)
	if log_file == null:
		push_error("Не удалось открыть /out/ticks.csv")
		get_tree().quit(1)
		return
	log_file.store_line("tick,start_ns,end_ns,engine_delta_s,realtime_ns")

func _physics_process(delta: float) -> void:
	if log_file == null:
		return
	tick += 1
	# Пара часов Godot нужна для привязки относительных меток к часам хоста.
	var realtime_ns := 0
	var before := Time.get_ticks_usec()
	if tick == 1 or tick % 60 == 0:
		realtime_ns = int(Time.get_unix_time_from_system() * 1e9)
	var start := Time.get_ticks_usec()
	if start - before > 100:
		realtime_ns = 0
	for i in range(bodies.size()):
		var steering := tick
		for step in range(passes):
			var page := (tick + i + step) & 2047
			steering = (steering * 3 + snapshot[page << 12]) & 65535
		var direction := 1.0 if steering % 2 == 0 else -1.0
		bodies[i].apply_central_force(Vector3(direction * 2.0, 0.0, 0.0))
	var done := Time.get_ticks_usec()
	# Привязка относится к середине интервала чтения настенных часов.
	if realtime_ns != 0:
		realtime_ns += (start - before) * 500
	log_file.store_line("%d,%d,%d,%.9f,%d" % [tick, start * 1000, done * 1000, delta, realtime_ns])
	if tick == 1 or tick % 60 == 0:
		log_file.flush()
