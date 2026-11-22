"""Regression for the reference UE4SS iterator's consuming boolean result path.

The binding reads a boolean callback result through get_bool (which removes it),
then discards the same stack index again for false. On its next iteration the
callback is lost. This fixture models that observed failure, unlike a normal Lua
for-loop which treats nil and false as interchangeable continuation values.
"""

import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
json = require("paintmanager.json")
reflection = require("paintmanager.reflection")
config = require("paintmanager.config")
function text(s) return {ToString=function() return s end} end
function object(path, class)
    return {
        IsValid=function() return true end,
        GetFullName=function() return path end,
        GetClass=function() return class end,
    }
end
function reference_iteration(values, callback)
    local callable=callback
    for _,value in ipairs(values) do
        if callable==nil then error("attempt to call a nil value: reference iterator lost callback") end
        local decision=callable(value)
        if type(decision)=="boolean" then
            if decision then return end
            -- Model the consumed false followed by the binding's second discard.
            callable=nil
        end
    end
end
function class(short, properties, functions, parent)
    local c=object("Class /Script/Voyage."..short)
    function c:GetFName() return text(short) end
    function c:GetSuperStruct() return parent end
    function c:ForEachProperty(callback) reference_iteration(properties or {},callback) end
    function c:ForEachFunction(callback) reference_iteration(functions or {},callback) end
    return c
end
function property(short, kind)
    local p=object(kind.." /Script/Voyage.Mock:"..short,class(kind))
    function p:GetFName() return text(short) end
    return p
end
function fn(short, properties)
    local f=object("Function /Script/Voyage.Mock:"..short)
    function f:ForEachProperty(callback) reference_iteration(properties or {},callback) end
    return setmetatable(f,{__call=function() error("native function must not be invoked") end})
end
'''


class ReflectionBindingTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def result(self, source):
        return json.loads(self.lua.execute(source))

    def test_fixture_reproduces_false_failure_but_nil_continues_and_true_stops(self):
        result = self.result('''
            local false_count,nil_count,true_count=0,0,0
            local ok,err=pcall(function()
                reference_iteration({1,2},function() false_count=false_count+1; return false end)
            end)
            reference_iteration({1,2},function() nil_count=nil_count+1 end)
            reference_iteration({1,2},function() true_count=true_count+1; return true end)
            return json.encode({falseSucceeded=ok,error=err,falseCount=false_count,
                                nilCount=nil_count,trueCount=true_count})
        ''')
        self.assertFalse(result["falseSucceeded"])
        self.assertIn("nil value", result["error"])
        self.assertEqual((result["falseCount"], result["nilCount"], result["trueCount"]), (1, 2, 1))

    def test_contract_keeps_multiple_properties_functions_and_parameter_descriptors(self):
        result = self.result('''
            local first=fn("GetPaintInSlot",{property("SlotIndex","IntProperty"),property("ReturnValue","ObjectProperty")})
            local second=fn("GetSetCount",{property("Item","ObjectProperty"),property("Count","IntProperty")})
            local c=class("VoyagePaintInventoryComponent",
                {property("PaintSetName","NameProperty"),property("PaintSetMaxCount","IntProperty")},{first,second})
            return json.encode(reflection.contract(c,config))
        ''')
        level = result["hierarchy"][0]
        self.assertTrue(result["complete"])
        self.assertNotIn("propertyError", level)
        self.assertNotIn("functionError", level)
        self.assertEqual([p["name"] for p in level["properties"]], ["PaintSetName", "PaintSetMaxCount"])
        self.assertEqual(len(level["functions"]), 2)
        self.assertEqual([p["name"] for p in level["functions"][0]["fields"]], ["SlotIndex", "ReturnValue"])
        self.assertTrue(all("fieldError" not in fn for fn in level["functions"]))

    def test_contract_iterates_each_inherited_level_without_losing_callback(self):
        result = self.result('''
            local parent=class("VoyageItem",
                {property("DesiredInventory","EnumProperty"),property("UnlockCount","IntProperty")},
                {fn("First",{}),fn("Second",{})})
            local child=class("VoyagePaintItem",{property("PaintSet","MapProperty")},{},parent)
            return json.encode(reflection.contract(child,config))
        ''')
        self.assertEqual(len(result["hierarchy"]), 2)
        parent = result["hierarchy"][1]
        self.assertEqual(len(parent["properties"]), 2)
        self.assertEqual(len(parent["functions"]), 2)
        self.assertNotIn("propertyError", parent)
        self.assertNotIn("functionError", parent)

    def test_scalar_observations_continue_past_irrelevant_fields_and_read_parent(self):
        result = self.result('''
            local parent=class("Parent",{property("ResearchUnlocked","BoolProperty")},{})
            local child=class("Child",{property("Ignored","IntProperty"),
                property("PaintProgress","IntProperty"),property("PaintSetCount","IntProperty")},{},parent)
            local o=object("Character Mock",child)
            o.Ignored=999; o.PaintProgress=2; o.PaintSetCount=4; o.ResearchUnlocked=true
            local fields,status=reflection.observations(o,config)
            return json.encode({fields=fields,status=status})
        ''')
        self.assertTrue(result["status"]["complete"])
        self.assertEqual(result["status"]["errors"], [])
        self.assertEqual({p["name"]: p["value"] for p in result["fields"]},
                         {"PaintProgress": 2, "PaintSetCount": 4, "ResearchUnlocked": True})

    def test_true_limit_result_stops_before_reading_more_properties(self):
        result = self.result('''
            config.max_properties=1
            local c=class("Limited",{property("PaintOne","IntProperty"),property("PaintTwo","IntProperty")},{})
            return json.encode(reflection.contract(c,config))
        ''')
        level = result["hierarchy"][0]
        self.assertEqual(len(level["properties"]), 1)
        self.assertTrue(level["propertiesTruncated"])
        self.assertNotIn("propertyError", level)
        self.assertFalse(result["complete"])

    def test_contract_propagates_iterator_failure_to_complete(self):
        result = self.result('''
            local c=class("Broken",{},{})
            function c:ForEachProperty() error("fixture metadata unreadable") end
            return json.encode(reflection.contract(c,config))
        ''')
        self.assertFalse(result["complete"])
        self.assertIn("fixture metadata unreadable", result["hierarchy"][0]["propertyError"])

    def test_discovery_includes_observed_native_paint_types_without_global_matches(self):
        result = self.result('''
            local item=class("VoyagePaintItem",{},{})
            local inventory=class("VoyagePaintInventoryComponent",{},{})
            function ForEachUObject() end
            function StaticFindObject(path)
                if path=="/Script/Voyage.VoyagePaintItem" then return item end
                if path=="/Script/Voyage.VoyagePaintInventoryComponent" then return inventory end
            end
            return json.encode(reflection.discover(config))
        ''')
        self.assertTrue(result["complete"])
        self.assertEqual({c["class"] for c in result["classes"]},
                         {"Class /Script/Voyage.VoyagePaintItem", "Class /Script/Voyage.VoyagePaintInventoryComponent"})


if __name__ == "__main__":
    unittest.main()
