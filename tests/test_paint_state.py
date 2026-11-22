"""Installed paint observations: native state is read, never applied or recreated."""

import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
json = require("paintmanager.json")
paint_state = require("paintmanager.paint_state")
config = {max_paint_boats=8,max_paint_slots=128,max_slots=1024}
candidates,readCalls={},{}
runtime_objects,next_address={},0
mutationCalls=0
local function forbidden()
    mutationCalls=mutationCalls+1
    error("unexpected native getter/mutation call")
end
function text(value) return {ToString=function() return value end} end
function remote(value) return {get=function() return value end} end
function object(path,type_path)
    next_address=next_address+1
    local o={path=path,valid=true,type_path=type_path,address=next_address}
    runtime_objects[path:match("^[^ ]+ (/.+)$") or path]=o
    function o:IsValid() return self.valid end
    function o:GetFullName() return self.path end
    function o:GetAddress() return self.address end
    function o:GetOuter() return self.outer end
    function o:GetFName() return text(self.path) end
    function o:GetClass()
        return object("Class "..(self.type_path or "/Script/Engine.Actor"),"/Script/CoreUObject.Class")
    end
    function o:IsA(path) return self.type_path==path end
    o.GetSetCount,o.GetSetMaxCount,o.GetPaintInSlot=forbidden,forbidden,forbidden
    o.GetNumPaintSlots,o.GetAllPaintSlotIndices,o.GetSlotInfoByIndex=forbidden,forbidden,forbidden
    o.GetPaintSlotWorldLocation,o.TransferSlot,o.AddItem,o.RemoveItem=forbidden,forbidden,forbidden,forbidden
    o.SetPaint,o.Unlock=forbidden,forbidden
    return o
end
function world(path) return object(path or "World Main","/Script/Engine.World") end
mainWorld=world()
function actor(path,boat,x,actor_world)
    local o=object(path,boat and "/Script/Voyage.VoyageBoatPawn" or "/Script/Voyage.VoyageCharacter")
    o.world=actor_world or mainWorld
    o.location={X=x or 0,Y=0,Z=0}
    function o:GetWorld() return self.world end
    function o:K2_GetActorLocation()
        if self.locationError then error("location unavailable") end
        return self.location
    end
    return o
end
function map(entries)
    return {ForEach=function(_,callback)
        for _,entry in ipairs(entries or {}) do callback(remote(entry[1]),remote(entry[2])) end
    end}
end
function slot_info(display,material,part)
    return {SlotDisplayName=text(display or "Hull"),MaterialSlotName=text(material or "HullMaterial"),
            PartName=text(part or "Hull")}
end
function raw_slot(index,id,count,resolved)
    return {slot=tostring(index),assetName=id,itemCount=count,itemResolved=resolved~=false}
end
function component(path,owner,threshold,allowed,metadata,rows)
    local c=object(path,"/Script/Voyage.VoyagePaintInventoryComponent")
    c.owner=owner
    c.MinimumItemsToUnlockSet=threshold
    c.bAllowSetUnlock=allowed
    c.PaintSetName=text("Caretaker")
    c.PaintSetDisplayName=text("Caretaker Paint")
    c.PaintSlots=map(metadata or {})
    c.rows=json.array(rows or {})
    c.inventoryComplete=true
    function c:GetOwner() return self.owner end
    return c
end
function read_inventory(c,role,owner,distance,limits)
    readCalls[#readCalls+1]={component=c.path,role=role}
    if c.inventoryError then error("inventory read failed") end
    return {component=c.path,role=role,owner=owner.path,distanceCm=distance,
            slots=c.rows,complete=c.inventoryComplete,errors=json.array(c.inventoryComplete and {} or {"partial inventory"})}
end
function FindAllOf(class)
    assert(class=="VoyagePaintInventoryComponent","unexpected discovery type")
    if discoveryError then error("paint discovery failed") end
    return candidates
end
function StaticFindObject(path) return runtime_objects[path] end
pawn=actor("Character Current",false,0)
pawn.PaintInventory=component("Paint Character",pawn,5,true,{}, {})
function capture() return json.encode(paint_state.capture(pawn,config,read_inventory)) end
'''


class PaintStateTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def run_lua(self, source):
        return self.lua.execute(source)

    def capture(self):
        return json.loads(self.lua.globals().capture())

    def test_false_unlock_permission_and_signed_threshold_are_preserved(self):
        for threshold in (5, 0, -1, -(2**31), 2**31 - 1):
            with self.subTest(threshold=threshold):
                self.run_lua(f"pawn.PaintInventory.MinimumItemsToUnlockSet={threshold}; pawn.PaintInventory.bAllowSetUnlock=false")
                result = self.capture()
                self.assertTrue(result["complete"])
                self.assertEqual(result["character"]["minimumItemsToUnlockSet"], threshold)
                self.assertIs(result["character"]["allowSetUnlock"], False)
                self.assertEqual(result["character"]["role"], "installed_character_paint")
                self.assertEqual(self.lua.globals().mutationCalls, 0)

    def test_installed_items_join_slot_identity_and_empty_slots_stay_empty(self):
        self.run_lua('''
            pawn.PaintInventory=component("Paint Character",pawn,4,true,
                {{7,slot_info("Head 🎨","HeadMaterial","Head")},{2,slot_info("Body","BodyMaterial","Torso")}},
                {raw_slot(7,"DA_Paint_Canada",1)})
        ''')
        result = self.capture()
        self.assertTrue(result["complete"])
        slots = {slot["index"]: slot for slot in result["character"]["slots"]}
        self.assertEqual(slots[7]["displayName"], "Head 🎨")
        self.assertEqual(slots[7]["materialSlotName"], "HeadMaterial")
        self.assertEqual(slots[7]["partName"], "Head")
        self.assertEqual(slots[7]["occupancy"], "occupied")
        self.assertEqual(slots[7]["item"]["assetName"], "DA_Paint_Canada")
        self.assertEqual(slots[2]["occupancy"], "empty")
        self.assertIsNone(slots[2]["item"])
        self.assertEqual(result["character"]["unconfiguredItems"], [])

    def test_zero_quantity_is_not_an_installed_can(self):
        self.run_lua('''
            pawn.PaintInventory=component("Paint Character",pawn,5,true,
                {{0,slot_info()}},{raw_slot(0,"DA_Paint_Red",0)})
        ''')
        result = self.capture()
        self.assertTrue(result["complete"])
        self.assertEqual(result["character"]["slots"][0]["occupancy"], "empty")

    def test_unresolved_item_reference_preserves_serialized_identity(self):
        self.run_lua('''
            pawn.PaintInventory=component("Paint Character",pawn,5,true,
                {{0,slot_info()}},{raw_slot(0,"DA_Paint_Red",1,false)})
        ''')
        slot = self.capture()["character"]["slots"][0]
        self.assertEqual(slot["occupancy"], "occupied")
        self.assertEqual(slot["item"]["assetName"], "DA_Paint_Red")
        self.assertIs(slot["item"]["itemResolved"], False)

    def test_missing_or_wrong_character_component_is_explicitly_incomplete(self):
        self.run_lua("pawn.PaintInventory=nil")
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertFalse(result["character"]["complete"])
        self.assertFalse(result["character"]["slotConfigurationComplete"])
        self.assertTrue(result["character"]["errors"])
        self.assertEqual(len(self.lua.globals().readCalls), 0)
        self.run_lua('pawn.PaintInventory=object("Not paint","/Script/Voyage.VoyageBaseInventoryComponent")')
        result = self.capture()
        self.assertFalse(result["character"]["complete"])
        self.assertEqual(len(self.lua.globals().readCalls), 0)

    def test_character_component_owned_by_another_actor_is_not_substituted(self):
        self.run_lua('pawn.PaintInventory.owner=actor("Other character",false,0)')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertFalse(result["character"]["complete"])
        self.assertEqual(len(self.lua.globals().readCalls), 0)

    def test_missing_invalid_threshold_and_permission_are_not_defaulted(self):
        for expression in ("nil", "1.5", "math.huge", "2147483648", '"5"'):
            with self.subTest(threshold=expression):
                self.run_lua(f"pawn.PaintInventory.MinimumItemsToUnlockSet={expression}")
                result = self.capture()
                self.assertFalse(result["complete"])
                self.assertIsNone(result["character"]["minimumItemsToUnlockSet"])
        self.run_lua("pawn.PaintInventory.MinimumItemsToUnlockSet=5; pawn.PaintInventory.bAllowSetUnlock=nil")
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertIsNone(result["character"]["allowSetUnlock"])

    def test_bad_metadata_key_does_not_discard_the_other_slot(self):
        self.run_lua('''
            pawn.PaintInventory=component("Paint Character",pawn,5,true,
                {{"wrong",slot_info()},{1,slot_info("Valid","ValidMaterial","ValidPart")}}, {})
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertEqual([slot["index"] for slot in result["character"]["slots"]], [1])
        self.assertTrue(result["character"]["errors"])

    def test_unreadable_metadata_row_is_not_reported_as_empty_inventory(self):
        self.run_lua('''
            pawn.PaintInventory.PaintSlots=map({{0,setmetatable({}, {__index=function() error("slot metadata unavailable") end})}})
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertFalse(result["character"]["complete"])
        self.assertFalse(result["character"]["slotConfigurationComplete"])
        self.assertTrue(result["character"]["errors"])

    def test_missing_required_slot_text_is_explicit_without_losing_slot_or_item(self):
        self.run_lua('''
            local info=slot_info("Known label","Known material","Known part")
            info.PartName=nil
            pawn.PaintInventory=component("Paint Character",pawn,4,false,
                {{2,info}},{raw_slot(2,"DA_Paint_Red",1)})
        ''')
        result = self.capture()
        target = result["character"]
        self.assertFalse(target["complete"])
        self.assertFalse(target["slotConfigurationComplete"])
        self.assertEqual(target["slots"][0]["index"], 2)
        self.assertEqual(target["slots"][0]["displayName"], "Known label")
        self.assertIsNone(target["slots"][0]["partName"])
        self.assertEqual(target["slots"][0]["item"]["assetName"], "DA_Paint_Red")
        self.assertIs(target["allowSetUnlock"], False)

    def test_unconfigured_positive_item_is_retained_and_flagged(self):
        self.run_lua('''
            pawn.PaintInventory=component("Paint Character",pawn,5,true,
                {{0,slot_info()}},{raw_slot(9,"DA_Paint_Red",1)})
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertEqual(result["character"]["unconfiguredItems"][0]["slot"], "9")
        self.assertEqual(result["character"]["unconfiguredItems"][0]["assetName"], "DA_Paint_Red")

    def test_partial_inventory_cannot_prove_that_a_configured_slot_is_empty(self):
        self.run_lua('''
            pawn.PaintInventory.PaintSlots=map({{0,slot_info()}})
            pawn.PaintInventory.inventoryComplete=false
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertEqual(result["character"]["slots"][0]["occupancy"], "unknown")

    def test_inventory_callback_failure_is_captured(self):
        self.run_lua("pawn.PaintInventory.inventoryError=true")
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertTrue(result["character"]["errors"])

    def test_boats_require_native_type_and_same_world_at_any_distance(self):
        self.run_lua('''
            local near=actor("Boat Near",true,100)
            local edge=actor("Boat Boundary",true,3000)
            local far=actor("Boat Far",true,1000000)
            local otherWorld=actor("Boat Preview",true,0,world("World Preview"))
            local nonBoat=actor("Actor NamedBoat",false,0)
            candidates={component("Paint Far",far,5,true),component("Paint Preview",otherWorld,5,true),
                        component("Paint NonBoat",nonBoat,5,true),component("Paint Edge",edge,5,true),
                        component("Paint Near",near,4,false)}
        ''')
        result = self.capture()
        self.assertTrue(result["complete"])
        self.assertEqual([boat["owner"] for boat in result["boats"]], ["Boat Boundary", "Boat Far", "Boat Near"])
        self.assertEqual([boat["role"] for boat in result["boats"]], ["installed_boat_paint"] * 3)
        self.assertEqual(result["scope"]["boatOwnership"], "not established")
        self.assertIs(result["boats"][2]["allowSetUnlock"], False)

    def test_duplicate_component_handles_and_character_discovery_are_not_recounted(self):
        self.run_lua('''
            local boat=actor("Boat One",true,10)
            local c=component("Paint Same",boat,4,true)
            local alias=component("Paint Same",boat,4,true)
            candidates={pawn.PaintInventory,c,c,alias}
        ''')
        result = self.capture()
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["boats"]), 1)
        self.assertEqual(len(self.lua.globals().readCalls), 2)

    def test_boat_cap_is_deterministic_and_reports_incompleteness(self):
        self.run_lua('''
            config.max_paint_boats=1
            local far=actor("Boat Far",true,200)
            local near=actor("Boat Near",true,100)
            candidates={component("Paint Near",near,5,true),component("Paint Far",far,5,true)}
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertTrue(result["boatsTruncated"])
        self.assertEqual([boat["owner"] for boat in result["boats"]], ["Boat Far"])

    def test_metadata_cap_is_explicit(self):
        self.run_lua('''
            config.max_paint_slots=1
            pawn.PaintInventory.PaintSlots=map({{0,slot_info()},{1,slot_info()}})
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertTrue(result["character"]["slotsTruncated"])
        self.assertEqual(len(result["character"]["slots"]), 1)

    def test_boats_need_a_known_world_but_no_actor_location(self):
        self.run_lua('''
            local boat=actor("Boat Loaded",true,100)
            pawn.locationError,boat.locationError=true,true
            candidates={component("Paint Boat",boat,5,true)}
        ''')
        result = self.capture()
        self.assertTrue(result["complete"])
        self.assertEqual([boat["owner"] for boat in result["boats"]], ["Boat Loaded"])
        self.run_lua('pawn.world=nil')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertEqual(result["boats"], [])

    def test_unknown_boat_world_is_an_observation_failure(self):
        self.run_lua('''
            local boat=actor("Boat MissingWorld",true,100); boat.world=nil
            candidates={component("Paint Boat",boat,5,true)}
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertEqual(result["boats"], [])

    def test_discovery_failure_does_not_hide_the_character_observation(self):
        self.run_lua("discoveryError=true")
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertTrue(result["character"]["complete"])
        self.assertTrue(result["errors"])

    def test_export_is_json_safe_and_does_not_call_native_queries_or_mutations(self):
        self.run_lua('''
            local boat=actor("Boat ReadOnly",true,100)
            candidates={component("Paint Boat",boat,4,true,
                {{3,slot_info()}},{raw_slot(3,"DA_Paint_Canada",1)})}
        ''')
        result = self.capture()  # Wrappers have functions; leaking one would fail encoding.
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(result["boats"][0]["slots"][0]["item"]["assetName"], "DA_Paint_Canada")
        self.assertEqual(self.lua.globals().mutationCalls, 0)


if __name__ == "__main__":
    unittest.main()
