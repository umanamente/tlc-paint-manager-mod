"""Compare warm editor lookups with the layout rules used by guarded storage."""
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import SCRIPTS


class PresetIndexTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute("""
            package.path = scripts_path .. "/?.lua;" .. package.path
            module = require("paintmanager.preset_index")
            layout = require("paintmanager.preset_layout")
            json = require("paintmanager.json")
            budget = require("paintmanager.work_budget")
            target = { kind="boat", className="/Game/Ship", paintSetName="Hull" }
            function zone(slot, part, color)
                return { materialSlotName=slot, partName=part,
                    paint=color and { assetType="Paint", assetName=color } or json.null }
            end
            function record(name, zones)
                return { name=name, target=target, zones=zones }
            end
            first = record("pReSet 1", {zone("h:1", "舷", "red"), zone("a", "bc")})
            second = record("Other name", {zone("a", "bc"), zone("h:1", "舷", "red")})
            third = record("Preset 3", {zone("a", "bc", "red"), zone("h:1", "舷")})
            library = { presets={first, second, third} }
            function capture(zones, kind, class, paintSet)
                return { complete=true, logicalZones=zones,
                    compatibility={kind=kind or "boat", className=class or "/Game/Ship", paintSetName=paintSet or "Hull"} }
            end
            function build()
                local job = module.begin(library)
                for step = 1, 10 do
                    if job:step(budget.new(100, 1)).state == "done" then return job:get_result() end
                end
                error("index did not complete")
            end
        """)

    def test_lookup_uses_only_captured_zones_and_first_saved_match(self):
        self.lua.execute("""
            local index = build()
            assert(index.nextName == "Preset 2")
            library.presets = nil
            local edited = capture({zone("a", "bc"), zone("h:1", "舷", "red")})
            edited.logicalZones[2].displayName = "Localized text"
            edited.logicalZones[2].paint.displayName = "New display name"
            assert(layout.compare_layout(edited, first) == "match")
            assert(index:match(edited) == first)
            assert(index:match(capture(third.zones)) == third)
            for _, different in ipairs({
                capture(first.zones, "character"),
                capture(first.zones, "boat", "/Game/Other"),
                capture(first.zones, "boat", "/Game/Ship", "Other"),
                capture({zone("ab", "c"), zone("h:1", "舷", "red")}),
                capture({zone("a", "bc", "red"), zone("h:1", "舷", "red")}),
                capture({zone("a", "bc")}),
            }) do
                assert(layout.compare_layout(different, first) ~= "match")
                assert(index:match(different) == nil)
            end
        """)

    def test_build_respects_work_budget_and_unknown_layout_is_distinct_from_a_miss(self):
        self.lua.execute("""
            local job = module.begin(library)
            assert(job:step(budget.new(100, 1)).state == "running" and not job:get_result())
            library.presets = {}
            local index = build()
            assert(index.nextName == "Preset 1")
            local edited = capture(first.zones)
            local record, err = index:match(edited)
            assert(not record and not err)
            edited.complete = false
            assert(select(2, index:match(edited)))
            edited = capture({zone("a", "bc"), zone("a", "bc", "red")})
            assert(select(2, index:match(edited)))
            edited = capture({{materialSlotName="a", partName="bc"}})
            assert(select(2, index:match(edited)))
        """)
