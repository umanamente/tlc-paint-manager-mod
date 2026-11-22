-- Actual-call native elapsed-time samples; UObject access is game-thread only.
-- No library/object/parameter wrapper survives a call. These getters are not a
-- frame profiler and cannot preempt a native operation already in progress.
local safe = require("paintmanager.safe")
local M = {
    source = "UGameplayStatics.GetAccurateRealTime; seconds + double partial seconds; actual-call sample",
    frame_source = "UKismetSystemLibrary.GetFrameCount; GFrameCounter",
    validation = "JMAP metadata, pinned UE4SS out-parameter binding and offline fixtures verified; runtime result pending",
}

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function library(path, class_path)
    assert(type(StaticFindObject) == "function", "StaticFindObject unavailable")
    local object = StaticFindObject(path)
    assert(safe.valid(object) and object:IsA(class_path) == true, "Native clock library unavailable or unexpected")
    return object
end

function M.now_ms()
    local ok, value = pcall(function()
        local gameplay = library("/Script/Engine.Default__GameplayStatics", "/Script/Engine.GameplayStatics")
        local output = {}
        -- The reference binary exposes Seconds:int32 and PartialSeconds:double,
        -- both writable OutParm values with no return. Pinned LuaUObject.cpp
        -- writes scalar outputs under their reflected parameter names. It may
        -- reuse the first scalar-output table, so pass the same table twice.
        gameplay:GetAccurateRealTime(output, output)
        local seconds, partial = output.Seconds, output.PartialSeconds
        assert(finite(seconds) and seconds >= 0 and seconds <= 2147483647 and seconds % 1 == 0,
            "Native accurate clock Seconds is invalid")
        assert(finite(partial) and partial >= 0 and partial < 1,
            "Native accurate clock PartialSeconds is invalid")
        local milliseconds = seconds * 1000 + partial * 1000
        assert(finite(milliseconds), "Native accurate clock conversion overflowed")
        return milliseconds
    end)
    if ok then return value end
    -- Do not silently substitute frame-cached world/game time or os.clock.
    -- The caller owns monotonicity checks and an explicit count-limited fallback.
    return nil, "Native accurate time unavailable: " .. tostring(value)
end

function M.frame_count()
    local ok, value = pcall(function()
        local system = library("/Script/Engine.Default__KismetSystemLibrary", "/Script/Engine.KismetSystemLibrary")
        local frame = system:GetFrameCount()
        assert(finite(frame) and frame >= 0 and frame % 1 == 0, "Native frame counter is invalid")
        return frame
    end)
    if ok then return value end
    return nil, "Native frame count unavailable: " .. tostring(value)
end

return M
