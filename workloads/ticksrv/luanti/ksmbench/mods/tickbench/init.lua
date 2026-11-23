core.register_node("tickbench:a", {description = "Плитка A"})
core.register_node("tickbench:b", {description = "Плитка Б"})
core.register_entity("tickbench:mover", {
    initial_properties = {physical = false, pointable = false, static_save = false},
})

local count = tonumber(core.settings:get("tickbench_entities") or "64")
local edits = tonumber(core.settings:get("tickbench_edits") or "32")
assert(count and count >= 1 and count <= 1024 and count % 1 == 0)
assert(edits and edits >= 1 and edits <= 65536 and edits % 1 == 0)
local origin = {x = 0, y = 0, z = 0}
local log, tick, ready = nil, 0, false
local movers, positions, snapshot = {}, {}, nil

core.after(0, function()
    core.emerge_area(origin, {x = 15, y = 15, z = 15}, function(_, action, remaining)
        assert(action ~= core.EMERGE_CANCELLED and action ~= core.EMERGE_ERRORED)
        if remaining ~= 0 then return end
        assert(core.forceload_block(origin, true))
        for z = 0, 15 do
            for x = 0, 15 do
                local pos = {x = x, y = 0, z = z}
                core.set_node(pos, {name = "tickbench:a"})
                positions[#positions + 1] = pos
            end
        end
        for i = 1, count do
            movers[i] = assert(core.add_entity({x = i % 16, y = 2, z = math.floor(i / 16) % 16},
                "tickbench:mover"))
        end
        -- Общий снимок состояния удерживается в анонимной памяти, а не в page cache.
        local pages = {}
        for i = 1, 2048 do
            pages[i] = string.format("%04x", i) .. string.rep(string.char(65 + i % 23), 4092)
        end
        snapshot = table.concat(pages)
        log = assert(io.open(core.get_worldpath() .. "/ticks.csv", "w"))
        assert(log:write("tick,start_ns,end_ns,engine_delta_s,realtime_ns\n"))
        ready = true
    end)
end)

core.register_globalstep(function(dtime)
    if not ready then return end
    local start = core.get_us_time()
    tick = tick + 1
    for i, obj in ipairs(movers) do
        obj:set_pos({x = (i % 16) + (tick % 20) * 0.02, y = 2, z = math.floor(i / 16) % 16})
    end
    for i = 1, edits do
        local j = (tick + i - 2) % #positions + 1
        local byte = snapshot:byte((tick * 4096 + j) % #snapshot + 1)
        -- При обходе блока несколько раз каждая запись по-прежнему меняет узел.
        local parity = (tick + byte + math.floor((i - 1) / #positions)) % 2
        core.swap_node(positions[j], {name = parity == 0 and "tickbench:a" or "tickbench:b"})
    end
    local done = core.get_us_time()
    assert(log:write(string.format("%d,%.0f,%.0f,%.9f,\n", tick, start * 1000, done * 1000, dtime)))
    if tick == 1 or tick % 20 == 0 then assert(log:flush()) end
end)

core.register_on_shutdown(function()
    if log then assert(log:close()) end
end)
