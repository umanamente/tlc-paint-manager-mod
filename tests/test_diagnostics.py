"""Offline contract tests for diagnostic reads; mocks never provide game mutations."""
import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
fixtureShipOrigin={X=0,Y=0,Z=0}
package.loaded["paintmanager.ship_scope"]={current=function(c,expected)
    if fixtureShipUnavailable then return nil,"Ship unavailable" end
    local scope={token="ship:"..tostring(world and world.address),center=fixtureShipOrigin,
        radiusCm=fixtureShipRadius or c.ship_radius_cm}
    if expected and expected.token~=scope.token then return nil,"Ship changed" end
    return scope
end,
prepare=function(c) return package.loaded["paintmanager.ship_scope"].current(c) end,
player_access=function(c) return package.loaded["paintmanager.ship_scope"].current(c),nil,{connected=true} end,
invalidate=function() end}
json = require("paintmanager.json")
adapter = require("paintmanager.game_adapter")
reflection = require("paintmanager.reflection")
config = require("paintmanager.config")
mutations, reads, scans, functionCalls = 0, 0, 0, 0
objects, inventories, catalog, globals, paint_inventories = {}, {}, {}, {}, {}
runtime_objects, next_address = {}, 0
local function forbidden() mutations=mutations+1; error("native mutation forbidden") end
function name(value) return {ToString=function() return value end} end
function wrapper(value) return {get=function() return value end} end
function object(path, class)
    next_address=next_address+1
    local o={path=path, class=class, valid=true,address=next_address}
    runtime_objects[path:match("^[^ ]+ (/.+)$") or path]=o
    function o:IsValid() return self.valid end
    function o:GetFullName() return self.path end
    function o:GetFName() return name(self.path:match("%.([^%.]+)$") or self.path) end
    function o:GetClass() return self.class end
    function o:GetAddress() return self.address end
    function o:GetOuter() return self.outer end
    function o:IsAnyClass() return false end
    function o:IsA() return false end
    o.AddItem, o.RemoveSlot, o.TransferSlot, o.SetPaint, o.Unlock = forbidden, forbidden, forbidden, forbidden, forbidden
    return o
end
function class(short, properties, parent, functions)
    local c=object("Class /Script/Voyage."..short)
    function c:GetFName() return name(short) end
    function c:IsAnyClass() return true end
    function c:ForEachProperty(callback)
        for _,p in ipairs(properties or {}) do if callback(p) then break end end
    end
    function c:ForEachFunction(callback)
        for _,f in ipairs(functions or {}) do if callback(f) then break end end
    end
    function c:GetSuperStruct() return parent end
    return c
end
function property(short, kind)
    local p=object(kind.." /Script/Voyage.Mock."..short, class(kind))
    function p:GetFName() return name(short) end
    return p
end
function fn(short, fields)
    local f=object("Function /Script/Voyage.Mock."..short, class("Function"))
    function f:IsA(path) return path=="/Script/CoreUObject.Function" end
    function f:ForEachProperty(callback)
        for _,p in ipairs(fields or {}) do if callback(p) then break end end
    end
    return setmetatable(f,{__call=function() functionCalls=functionCalls+1; error("must not invoke discovered function") end})
end
function actor(path, short, x)
    local a=object(path, class(short))
    local stock={BP_Inventory_PlayerBox_C="BP_Inventory_PlayerBox",BP_Inventory_PlayerBox_Small_C="BP_Inventory_PlayerBox_Small",
        BP_Inventory_PlayerBox_Large_C="BP_Inventory_PlayerBox_Large"}
    if stock[short] then a.class.path="BlueprintGeneratedClass /Game/Blueprints/Interactives/Containers/"..stock[short].."."..short end
    function a:K2_GetActorLocation() return {X=x or 0,Y=0,Z=0} end
    function a:GetWorld() return world end
    return a
end
function paint(id)
    local p=object("VoyagePaintItem /Game/Data/Assets/Items/Paints/"..id.."."..id, class("VoyagePaintItem"))
    return p
end
function slot(id,count,item)
    return {AssetName=name(id),Data={ItemCount=count},Item=wrapper(item)}
end
function inventory(path, owner, entries)
    local c=object(path,class("VoyageInventoryWeightLimitedComponent"))
    function c:GetOwner() return owner end
    c.Items={ForEach=function(_,callback)
        for index,entry in ipairs(entries or {}) do callback(wrapper(index-1),wrapper(entry)) end
    end}
    return c
end
function FindAllOf(short)
    scans=scans+1
    if short=="VoyageInventoryWeightLimitedComponent" then return inventories end
    if short=="VoyageItem" then return catalog end
    if short=="VoyagePaintInventoryComponent" then return paint_inventories end
    return {}
end
function FindFirstOf(short) reads=reads+1; return controller end
function StaticFindObject(path) return globals[path] or runtime_objects[path] end
function ForEachUObject(callback) for _,o in ipairs(objects) do callback(o) end end
world=object("World MockWorld",class("World"))
pawn=actor("BP_FirstPersonCharacter_New_C World.CurrentPawn","BP_FirstPersonCharacter_New_C",0)
controller=object("VoyagePlayerController World.Controller",class("VoyagePlayerController"))
controller.Pawn=pawn
function controller:GetWorld() return world end
pawn.WeightInventory=inventory("Inventory World.CurrentPawn.WeightInventory",pawn,{})
pawn.PaintInventory=inventory("PaintInventory World.CurrentPawn.PaintInventory",pawn,{})
pawn.PaintInventory.class=class("VoyagePaintInventoryComponent")
function pawn.PaintInventory:IsA(path) return path=="/Script/Voyage.VoyagePaintInventoryComponent" end
pawn.PaintInventory.MinimumItemsToUnlockSet=5
pawn.PaintInventory.bAllowSetUnlock=false
pawn.PaintInventory.PaintSetName=name("FixtureCharacter")
pawn.PaintInventory.PaintSetDisplayName=name("Character")
pawn.PaintInventory.PaintSlots={ForEach=function() end}
function capture() return json.encode(adapter.snapshot(config)) end
'''


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def run_lua(self, source):
        return self.lua.execute(source)

    def capture(self):
        return json.loads(self.lua.globals().capture())

    def test_current_backpack_vault_and_physical_stock_are_separate(self):
        self.run_lua('''
            local p=paint("DA_Paint_Red")
            pawn.WeightInventory=inventory("Inventory World.Pack",pawn,{slot("DA_Paint_Red",2,p),slot("DA_Paint_Red",3,p)})
            pawn.VaultInventory=inventory("Inventory World.Vault",pawn,{slot("DA_Paint_Red",900,p)})
            local box=actor("Box World.Small","BP_Inventory_PlayerBox_Small_C",100)
            inventories={inventory("Inventory World.Box",box,{slot("DA_Paint_Red",4,p)})}
        ''')
        result = self.capture()
        self.assertTrue(result["complete"])
        self.assertEqual(result["paintCandidates"], {"backpack": {"DA_Paint_Red": 5}, "nearbyContainers": {"DA_Paint_Red": 4}})
        self.assertEqual([x["role"] for x in result["inventories"]], ["backpack", "recipe_library_not_stock", "nearby_container"])
        self.assertEqual(self.lua.globals().mutations, 0)

    def test_exact_stock_box_classes_and_radius_boundary(self):
        self.run_lua('''
            config.ship_radius_cm=3500
            local p=paint("DA_Paint_Red")
            local cases={{"BP_Inventory_PlayerBox_C",3500},{"BP_Inventory_PlayerBox_Large_C",2000},
                         {"BP_Inventory_PlayerBox_Small_C",1000},{"BP_Inventory_PlayerBox_Small_C",3501},
                         {"BP_Inventory_PlayerBox_Custom_C",1},{"BP_FirstPersonCharacter_New_C",1},
                         {"BP_QuestContainer_C",1}}
            for i,c in ipairs(cases) do
                local a=actor("Box World."..i,c[1],c[2])
                inventories[#inventories+1]=inventory("Inventory World."..i,a,{slot("DA_Paint_Red",1,p)})
            end
        ''')
        result = self.capture()
        self.assertEqual(result["paintCandidates"]["nearbyContainers"], {"DA_Paint_Red": 3})
        self.assertEqual([i["distanceCm"] for i in result["inventories"][1:]], [1000, 2000, 3500])

    def test_duplicate_component_handles_are_only_counted_once(self):
        self.run_lua('''
            local box=actor("Box World.One","BP_Inventory_PlayerBox_C",50)
            local entries={slot("DA_Paint_Red",5,paint("DA_Paint_Red"))}
            local c=inventory("Inventory World.Same",box,entries)
            local alias=inventory("Inventory World.Same",box,entries);alias.address=c.address
            inventories={c,c,alias,pawn.WeightInventory}
        ''')
        result = self.capture()
        self.assertEqual(len(result["inventories"]), 2)
        self.assertEqual(result["paintCandidates"]["nearbyContainers"], {"DA_Paint_Red": 5})

    def test_missing_current_pack_never_substitutes_another_character(self):
        self.run_lua('''
            pawn.WeightInventory=nil
            local other=actor("Other Pawn","BP_FirstPersonCharacter_New_C",0)
            inventories={inventory("Inventory Other.Pack",other,{slot("DA_Paint_Red",5,paint("DA_Paint_Red"))})}
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertEqual(result["inventories"], [])
        self.assertEqual(result["paintCandidates"]["backpack"], {})
        self.assertIn("no substitute", result["warnings"][0])

    def test_no_valid_pawn_does_not_scan_global_inventories(self):
        self.run_lua("controller.Pawn=nil")
        result = self.capture()
        self.assertEqual(result["status"], "no_player")
        self.assertFalse(result["complete"])
        self.assertEqual(self.lua.globals().scans, 0)

    def test_unresolved_weak_item_is_explicit_and_retains_serialized_count(self):
        self.run_lua('pawn.WeightInventory=inventory("Inventory Pack",pawn,{slot("DA_Paint_Red",5,nil)})')
        result = self.capture()
        entry = result["inventories"][0]["slots"][0]
        self.assertFalse(entry["itemResolved"])
        self.assertNotIn("itemPath", entry)
        self.assertEqual(entry["paintCandidateReason"], "asset_name")
        self.assertEqual(result["paintCandidates"]["backpack"], {"DA_Paint_Red": 5})
        self.assertIn("heuristics", result["classification"])

    def test_unreadable_entry_is_not_silently_treated_as_empty(self):
        self.run_lua('pawn.WeightInventory=inventory("Inventory Pack",pawn,{{AssetName=name("DA_Paint_Red"),Data={}}})')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertFalse(result["inventories"][0]["complete"])
        self.assertEqual(result["inventories"][0]["slotsVisited"], 1)
        self.assertEqual(result["inventories"][0]["errors"], ["unreadable ItemCount"])
        self.assertEqual(result["paintCandidates"]["backpack"], {})

    def test_nonfinite_fractional_and_negative_counts_are_incomplete(self):
        for count in ("math.huge", "0/0", "-1", "1.5"):
            with self.subTest(count=count):
                self.run_lua('pawn.WeightInventory=inventory("Inventory Pack",pawn,{slot("DA_Paint_Red",' + count + ',nil)})')
                self.assertFalse(self.capture()["complete"])

    def test_processing_caps_mark_inventory_incomplete(self):
        self.run_lua('''
            config.max_slots=1
            pawn.WeightInventory=inventory("Inventory Pack",pawn,{slot("DA_Paint_Red",1,nil),slot("DA_Paint_Red",1,nil)})
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertTrue(result["inventories"][0]["slotsTruncated"])
        self.assertEqual(result["inventories"][0]["slotsVisited"], 2)
        self.assertEqual(result["paintCandidates"]["backpack"], {"DA_Paint_Red": 1})

    def test_snapshot_contains_serializable_metadata_not_uobjects(self):
        self.run_lua('''
            local p=paint("DA_Paint_Red")
            p.class=class("VoyagePaintItem",{property("PaintName","TextProperty"),property("PaintMaterial","ObjectProperty")})
            p.PaintName=name("Crimson 🎨")
            p.PaintMaterial=object("Material /Game/Paint",class("Material"))
            pawn.WeightInventory=inventory("Inventory Pack",pawn,{slot("DA_Paint_Red",5,p)})
            catalog={p,p,object("Memory /Game/CoordinatePainter.Memory",class("VoyageItem"))}
        ''')
        result = self.capture()  # Any leaked wrapper/function would make the JSON encoder reject it.
        self.assertEqual(len(result["catalog"]), 1)
        fields = {x["name"]: x for x in result["catalog"][0]["fields"]}
        self.assertEqual(fields["PaintName"]["value"], "Crimson 🎨")
        self.assertEqual(fields["PaintMaterial"]["valueNotRead"], "complex_property")
        self.assertEqual(self.lua.globals().mutations, 0)

    def test_reflection_walks_inheritance_and_never_invokes_discovered_functions(self):
        encoded = self.run_lua('''
            local f=fn("UnlockPaint",{property("Paint","ObjectProperty"),property("ReturnValue","BoolProperty")})
            local parent=class("VoyagePaintParent",{property("ResearchCount","IntProperty"),property("Other","IntProperty")},nil,{f})
            local child=class("VoyagePaintChild",{property("PaintName","StrProperty")},parent)
            local o=object("PaintObject",child); o.PaintName="Red"; o.ResearchCount=2
            objects={child,f}; globals["/Script/Voyage.VoyageItem"]=child
            return json.encode({contract=reflection.contract(child,config), observations=reflection.observations(o,config), discovered=reflection.discover(config)})
        ''')
        result = json.loads(encoded)
        self.assertEqual(len(result["contract"]["hierarchy"]), 2)
        observations = {x["name"]: x["value"] for x in result["observations"]}
        self.assertEqual(observations, {"PaintName": "Red", "ResearchCount": 2})
        self.assertEqual(result["contract"]["hierarchy"][1]["functions"][0]["fields"][1]["name"], "ReturnValue")
        self.assertEqual(len(result["discovered"]["relatedFunctions"]), 1)
        self.assertEqual(self.lua.globals().functionCalls, 0)
        self.assertEqual(self.lua.globals().mutations, 0)

    def test_catalog_lookup_failure_marks_snapshot_incomplete(self):
        self.run_lua('''
            local normal=FindAllOf
            FindAllOf=function(short)
                if short=="VoyageItem" then error("catalog lookup failed") end
                return normal(short)
            end
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertFalse(result["catalogComplete"])
        self.assertEqual(result["catalog"], [])
        self.assertTrue(any("catalog lookup failed" in warning for warning in result["warnings"]))

    def test_catalog_cap_marks_snapshot_incomplete(self):
        self.run_lua('config.max_catalog=1; catalog={paint("DA_Paint_Red"),paint("DA_Paint_Blue")}')
        result = self.capture()
        self.assertEqual(len(result["catalog"]), 1)
        self.assertTrue(result["catalogTruncated"])
        self.assertFalse(result["catalogComplete"])
        self.assertFalse(result["complete"])

    def test_reflection_iteration_failure_has_explicit_status(self):
        self.run_lua('pawn.class.ForEachProperty=function() error("properties unavailable") end')
        result = self.capture()
        observation = result["observations"][1]
        self.assertEqual(observation["fields"], [])
        self.assertFalse(observation["status"]["complete"])
        self.assertIn("properties unavailable", observation["status"]["errors"][0])
        self.assertFalse(result["complete"])

    def test_reflection_property_cap_has_explicit_status(self):
        self.run_lua('''
            config.max_properties=1
            pawn.class=class("VoyageCharacter",{property("PaintName","StrProperty"),property("ResearchCount","IntProperty")})
            pawn.PaintName="Red"; pawn.ResearchCount=2
        ''')
        result = self.capture()
        observation = result["observations"][1]
        self.assertEqual([field["name"] for field in observation["fields"]], ["PaintName"])
        self.assertTrue(observation["status"]["propertiesTruncated"])
        self.assertFalse(observation["status"]["complete"])
        self.assertFalse(result["complete"])

    def test_main_startup_and_key_callbacks_defer_scanning_to_pump(self):
        self.run_lua('''
            -- This fixture tests deferral/reporting, independently of the native
            -- character/source contracts covered by collection tests.
            package.loaded["paintmanager.collection"]={
                context=function() return "fixture-context" end,
                begin=function()
                    return {
                        cancel=function() end,
                        step=function(_,budget)
                            budget:consume()
                            return {state="done",model={contextToken="fixture-context",sources={},rows={},complete=true}}
                        end,
                    }
                end,
            }
            registered,logs,written,renamed={},{},{},{}
            print=function(message) logs[#logs+1]=message end
            Key={F6="F6",O="O",J="J"}; ModifierKey={CONTROL="ctrl",ALT="alt"}
            function IsKeyBindRegistered() return false end
            function RegisterKeyBind(key,modifiers,callback) registered[key]=callback end
            function LoopInGameThreadWithDelay(_,callback) pump=callback;return 1 end
            io.open=function(path,mode)
                if mode=="rb" then return nil,"missing",2 end
                assert(mode=="wb")
                local file={}
                function file:write(text) written[#written+1]=text; return self end
                function file:flush() return true end
                function file:close() return true end
                return file
            end
            os.rename=function(a,b) renamed[#renamed+1]={a,b}; return true end
            -- Explicitly load developer mode through the real schema/coordinator;
            -- the production default intentionally rejects diagnostic shortcuts.
            local preferences=require("paintmanager.ui_preferences")
            package.loaded["paintmanager.ui_preferences"]={new=function(callbacks)
                local coordinator=preferences.new(callbacks)
                local document=require("paintmanager.state_document").new()
                document.sections.settings.data.showDeveloperTools=true
                assert(coordinator:load(document))
                return coordinator
            end}
            assert(loadfile(scripts_path.."/main.lua"))()
        ''')
        self.assertEqual(self.lua.globals().scans, 0)
        self.assertIsNone(self.lua.globals().registered.P)
        self.run_lua('registered.F6(); registered.F6(); registered.F6()')
        self.assertEqual(self.lua.globals().scans, 0)
        self.assertEqual(len(self.lua.globals().written), 0)
        self.run_lua('for i=1,1000 do pump();if #renamed>0 then break end end')
        self.assertEqual(len(self.lua.globals().renamed), 1)
        report = json.loads(self.lua.eval('table.concat(written)'))
        self.assertTrue(report["readOnly"])
        self.assertEqual(report["kind"], "snapshot")
        self.assertEqual(self.lua.globals().mutations, 0)

    def test_native_paint_class_recognizes_nonstandard_asset_names(self):
        self.run_lua('''
            local p=object("VoyagePaintItem /Game/Custom/Finish.Finish",class("VoyagePaintItem"))
            pawn.WeightInventory=inventory("Inventory Pack",pawn,{slot("Finish",2,p)})
            catalog={p,object("Tooltip /Game/Custom/Tip.Tip",class("VoyagePaintItemToolTipWidget"))}
        ''')
        result = self.capture()
        self.assertEqual(result["paintCandidates"]["backpack"], {"Finish": 2})
        self.assertEqual(result["inventories"][0]["slots"][0]["paintCandidateReason"], "native_class")
        self.assertEqual(len(result["catalog"]), 1)

    def test_default_source_budget_covers_the_first_runtime_scene(self):
        self.run_lua('''
            pawn.VaultInventory=inventory("Inventory Vault",pawn,{})
            for i=1,65 do
                local box=actor("Box World."..i,"BP_Inventory_PlayerBox_C",i)
                inventories[#inventories+1]=inventory("Inventory Box."..i,box,{slot("DA_Paint_Red",1,paint("DA_Paint_Red"))})
            end
        ''')
        result = self.capture()
        self.assertEqual(len(result["inventories"]), 67)
        self.assertEqual(result["paintCandidates"]["nearbyContainers"], {"DA_Paint_Red": 65})
        self.assertNotIn("sourcesTruncated", result)

    def test_installed_paint_is_separate_from_backpack_containers_and_vault(self):
        self.run_lua('''
            local p=paint("DA_Paint_Red")
            pawn.WeightInventory=inventory("Inventory Pack",pawn,{slot("DA_Paint_Red",2,p)})
            pawn.VaultInventory=inventory("Inventory Vault",pawn,{slot("DA_Paint_Red",1,p)})
            pawn.PaintInventory.Items={ForEach=function(_,callback)
                local e=slot("DA_Paint_Red",1,p); e.AssetType={Name=name("FixturePaintType")}
                callback(wrapper(0),wrapper(e))
            end}
            pawn.PaintInventory.PaintSlots={ForEach=function(_,callback)
                callback(wrapper(0),wrapper({SlotDisplayName=name("Head"),MaterialSlotName=name("HeadMat"),PartName=name("Head")}))
            end}
        ''')
        result = self.capture()
        self.assertTrue(result["complete"])
        self.assertEqual(result["paintCandidates"]["backpack"], {"DA_Paint_Red": 2})
        self.assertEqual(len(result["inventories"]), 2)
        installed = result["paintState"]["character"]
        self.assertEqual(installed["minimumItemsToUnlockSet"], 5)
        self.assertIs(installed["allowSetUnlock"], False)
        self.assertEqual(installed["inventory"]["slots"][0]["assetType"], "FixturePaintType")
        self.assertEqual(installed["slots"][0]["occupancy"], "occupied")
        self.assertEqual(self.lua.globals().mutations, 0)

    def test_experimental_paint_reader_failure_preserves_physical_snapshot(self):
        self.run_lua('''
            pawn.WeightInventory=inventory("Inventory Pack",pawn,{slot("DA_Paint_Red",3,paint("DA_Paint_Red"))})
            require("paintmanager.paint_state").begin=function() error("fixture paint reader failure") end
        ''')
        result = self.capture()
        self.assertFalse(result["complete"])
        self.assertEqual(result["paintCandidates"]["backpack"], {"DA_Paint_Red": 3})
        self.assertFalse(result["paintState"]["complete"])
        self.assertEqual(result["paintState"]["status"], "error")
        self.assertIn("fixture paint reader failure", result["paintState"]["errors"][0])


if __name__ == "__main__":
    unittest.main()
