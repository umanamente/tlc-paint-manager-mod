"""Read-only UI collection contracts; native UMG rendering needs an in-game test."""
import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
fixtureShipOrigin={X=0,Y=0,Z=0}
package.loaded["paintmanager.ship_scope"]={
    prepare=function(c) return package.loaded["paintmanager.ship_scope"].current(c) end,
    invalidate=function() end,
    current=function(c,expected)
        if fixtureShipUnavailable then return nil,"Ship unavailable" end
        local radius=fixtureShipRadius or c.ship_radius_cm
        local scope={token="ship:"..tostring(world and world.address)..":"..tostring(radius),
            worldId=world and world.path.."@"..tostring(world.address),center=fixtureShipOrigin,
            radiusCm=radius,geometryRevision="fixture",shipId="0000000000000001"}
        if expected and expected.token~=scope.token then return nil,"Ship scope changed" end
        return scope
    end,
    player_access=function(c,expected)
        local scope,err=package.loaded["paintmanager.ship_scope"].current(c,expected)
        if not scope then return nil,err end
        local d=require("paintmanager.safe").distance_cm(pawn:K2_GetActorLocation(),scope.center)
        if not d or d>scope.radiusCm then return nil,"Signal lost" end
        return scope,nil,{connected=true,distanceCm=d,radiusCm=scope.radiusCm}
    end,
    persistent_actor_id=function(actor) return actor.persistentId end,
}
json=require("paintmanager.json")
collection=require("paintmanager.collection")
config=require("paintmanager.config")
scans,mutations,inventory_reads,queries,vault_reads=0,0,0,0,0
paints,containers,runtime_objects={},{},{}
local address=100
function text(s) return {ToString=function() return s end} end
function wrap(v) return {get=function() return v end} end
function obj(path,kind)
    address=address+1
    local o={path=path,kind=kind,valid=true,address=address}
    function o:IsValid() return self.valid end
    function o:GetFullName() return self.path end
    function o:GetFName() return text(self.path:match("%.([^%.]+)$") or self.path) end
    function o:GetAddress() return self.address end
    function o:GetClass()
        local stock={BP_Inventory_PlayerBox_Small_C="BP_Inventory_PlayerBox_Small",
            BP_Inventory_PlayerBox_C="BP_Inventory_PlayerBox",BP_Inventory_PlayerBox_Large_C="BP_Inventory_PlayerBox_Large"}
        local asset=stock[self.kind]
        local path=self.class_path or (asset and ("BlueprintGeneratedClass /Game/Blueprints/Interactives/Containers/"..asset.."."..self.kind))
            or ("Class /Script/Voyage."..self.kind)
        return obj(path,"Class")
    end
    function o:IsA(path)
        return path=="/Script/Voyage."..self.kind
            or (self.kind=="VoyageInventoryWeightLimitedComponent" and path=="/Script/Voyage.VoyageBaseInventoryComponent")
    end
    function o:AddItem() mutations=mutations+1;error("forbidden") end
    function o:RemoveSlot() mutations=mutations+1;error("forbidden") end
    runtime_objects[path]=o
    runtime_objects[path:match("^[^ ]+ (/.+)$") or path]=o
    return o
end
world=obj("World /Game/Map.Map","World")
pawn=obj("Character /Game/Map.Map.Pawn","VoyageCharacter")
function pawn:GetWorld() return world end
function pawn:K2_GetActorLocation() return {X=0,Y=0,Z=0} end
controller=obj("Controller /Game/Map.Map.Controller","VoyagePlayerController")
controller.Pawn=pawn
function controller:GetWorld() return world end
vault=obj("Vault /Game/Map.Map.Vault","VoyageBaseInventoryComponent")
vault.members={}
function recipe_entries(entries)
    vault.Items={ForEach=function(_,callback)
        vault_reads=vault_reads+1
        for i,e in ipairs(entries or {}) do
            local decision=callback(wrap(i),wrap(e))
            assert(decision==nil or decision==true)
            if decision==true then break end
        end
    end}
end
recipe_entries({})
function vault:HasItem(item) queries=queries+1;return self.members[item.path]==true end
pawn.VaultInventory=vault
function paint(id,label,path)
    local p=obj(path or ("VoyagePaintItem /Game/Data/Assets/Items/Paints/"..id.."."..id),"VoyagePaintItem")
    p.Name=label and text(label) or nil
    p.Icon=obj("Texture2D /Game/Icons/"..id.."."..id,"Texture2D")
    paints[#paints+1]=p
    return p
end
function entry(item,count,asset)
    return {Item=wrap(item),AssetName=text(asset or (item and item:GetFName():ToString()) or "DA_Paint_Missing"),
        AssetType={Name=text("Item")},Data={ItemCount=count}}
end
function FName(s) return s end
EFindName={FNAME_Add=1}
function box(label,distance,entries,kind,other_world)
    local a=obj("Box /Game/Map.Map."..label,kind or "BP_Inventory_PlayerBox_C")
    function a:GetWorld() return other_world or world end
    function a:K2_GetActorLocation() return {X=distance,Y=0,Z=0} end
    local c=obj("Inventory /Game/Map.Map."..label..".WeightInventory","VoyageInventoryWeightLimitedComponent")
    function c:GetOwner() return a end
    c.Items={ForEach=function(_,callback)
        inventory_reads=inventory_reads+1
        for i,e in ipairs(entries or {}) do callback(wrap(i),wrap(e)) end
    end}
    containers[#containers+1]=c
    return a,c
end
function FindFirstOf() return controller end
function StaticFindObject(path) return runtime_objects[path] end
function FindAllOf(kind)
    scans=scans+1
    if kind=="VoyagePaintItem" then return paints end
    if kind=="VoyageInventoryWeightLimitedComponent" then return containers end
    error("unexpected global scan")
end
function scan() return json.encode(collection.scan(config)) end
'''


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def run_lua(self, code):
        return self.lua.execute(code)

    def scan(self):
        return json.loads(self.run_lua('return scan()'))

    def test_three_exact_box_sizes_are_aggregated_automatically_on_first_scan(self):
        self.run_lua('''
            local p=paint("DA_Paint_Canada","Last Leaf")
            box("Small",1,{entry(p,2)},"BP_Inventory_PlayerBox_Small_C")
            box("Medium",2,{entry(p,4)},"BP_Inventory_PlayerBox_C")
            box("Large",3,{entry(p,6)},"BP_Inventory_PlayerBox_Large_C")
        ''')
        result = self.scan()
        self.assertEqual(result['schemaVersion'], 2)
        self.assertEqual(result['sourceInclusion'], 'automatic_all_nearby_stock_boxes')
        self.assertEqual(result['physicalCount'], 12)
        self.assertEqual(result['rows'][0]['count'], 12)
        self.assertEqual(result['includedSourceCount'], 3)
        self.assertTrue(all(source['included'] for source in result['sources']))
        self.assertTrue(all('selected' not in source for source in result['sources']))
        self.assertNotIn('selectedSourceCount', result)
        self.assertEqual(self.lua.globals().inventory_reads, 3)

    def test_automatic_box_count_excludes_backpack_vault_installed_and_loose(self):
        self.run_lua('''
            local p=paint("DA_Paint_Canada","Last Leaf")
            box("First",1,{entry(p,12)}); box("Second",2,{entry(p,7)})
            pawn.WeightInventory={Items={entry(p,100)}}
            pawn.PaintInventory={Items={entry(p,200)}}
            recipe_entries({entry(p,1000)})
            loose={entry(p,9000)}
        ''')
        result = self.scan()
        self.assertEqual(result['physicalCount'], 19)
        self.assertEqual(result['rows'][0]['count'], 19)
        self.assertEqual(result['includedSourceCount'], 2)
        self.assertEqual(self.lua.globals().mutations, 0)

    def test_names_and_icons_come_from_native_fields(self):
        self.run_lua('paint("DA_Paint_Canada","Last Leaf")')
        row = self.scan()['rows'][0]
        self.assertEqual(row['displayName'], 'Last Leaf')
        self.assertEqual(row['assetName'], 'DA_Paint_Canada')
        self.assertEqual(row['iconPath'], 'Texture2D /Game/Icons/DA_Paint_Canada.DA_Paint_Canada')

    def test_missing_native_name_is_not_generated_from_internal_id(self):
        self.run_lua('paint("DA_Paint_Canada",nil)')
        row = self.scan()['rows'][0]
        self.assertNotIn('displayName', row)
        self.assertFalse(row['nameAvailable'])
        self.assertFalse(row['metadataComplete'])

    def test_recipe_true_and_false_are_distinct_native_membership_results(self):
        self.run_lua('''
            local p=paint("DA_Paint_Canada","Last Leaf")
            local q=paint("DA_Paint_Suomi","Suomi")
            vault.members[p.path]=true
        ''')
        result = self.scan()
        states = {r['assetName']: r['recipeState'] for r in result['rows']}
        self.assertEqual(states, {'DA_Paint_Canada': 'unlocked', 'DA_Paint_Suomi': 'locked'})
        self.assertEqual(self.lua.globals().queries, 2)

    def test_missing_vault_never_marks_recipes_locked(self):
        self.run_lua('paint("DA_Paint_Canada","Last Leaf");pawn.VaultInventory=nil')
        result = self.scan()
        self.assertEqual(result['rows'][0]['recipeState'], 'unknown')
        self.assertFalse(result['complete'])

    def test_failed_or_nonboolean_recipe_query_is_unknown(self):
        for query in ('error("bad query")', 'return nil', 'return 0'):
            with self.subTest(query=query):
                self.setUp()
                self.run_lua('paint("DA_Paint_Canada","Last Leaf");vault.HasItem=function() ' + query + ' end')
                result = self.scan()
                self.assertEqual(result['rows'][0]['recipeState'], 'unknown')
                self.assertFalse(result['complete'])

    def test_exact_stock_classes_exclude_reward_jetski_quest_and_other_world(self):
        self.run_lua('''
            local p=paint("DA_Paint_Canada","Last Leaf")
            for _,k in ipairs({"BP_Inventory_PlayerBox_Small_C","BP_Inventory_PlayerBox_C","BP_Inventory_PlayerBox_Large_C",
                "BP_RocketReward_InventoryBox_C","BP_Inventory_JetSkiBox_C","BP_QuestContainer_C"}) do
                box(k,1,{entry(p,1)},k)
            end
            box("OtherWorld",1,{entry(p,500)},nil,obj("World Other","World"))
        ''')
        result = self.scan()
        self.assertEqual(len(result['sources']), 3)
        self.assertEqual(result['physicalCount'], 3)
        self.assertTrue(all(s['ownership'] == 'assumed_player_built_by_user_policy' for s in result['sources']))

    def test_radius_boundary_and_location_are_reported(self):
        self.run_lua('config.ship_radius_cm=3500;box("AtBoundary",3500,{});box("Outside",3501,{})')
        result = self.scan()
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['sources'][0]['distanceCm'], 3500)
        self.assertEqual(result['sources'][0]['location'], {'X': 3500, 'Y': 0, 'Z': 0})

    def test_same_short_class_name_from_a_custom_package_is_excluded(self):
        self.run_lua('''
            local a,c=box("Custom",1,{entry(paint("DA_Paint_Canada","Last Leaf"),900)})
            a.class_path="BlueprintGeneratedClass /Game/Mods/Custom/BP_Inventory_PlayerBox.BP_Inventory_PlayerBox_C"
        ''')
        result = self.scan()
        self.assertEqual(result['sourceCount'], 0)
        self.assertEqual(result['includedSourceCount'], 0)
        self.assertEqual(result['physicalCount'], 0)

    def test_source_ids_change_when_actor_or_component_address_changes(self):
        self.run_lua('a,c=box("One",1,{})')
        first = self.scan()['sources'][0]['id']
        self.run_lua('a.address=a.address+100')
        second = self.scan()['sources'][0]['id']
        self.assertNotEqual(first, second)
        self.run_lua('c.address=c.address+100')
        self.assertNotEqual(second, self.scan()['sources'][0]['id'])

    def test_missing_address_excludes_unreliable_source_identity(self):
        self.run_lua('local a,c=box("One",1,{});c.GetAddress=nil')
        result = self.scan()
        self.assertEqual(result['sources'], [])
        self.assertFalse(result['complete'])

    def test_context_check_is_cheap_and_changes_with_pawn_and_world_instances(self):
        first = self.run_lua('return collection.context()')[0]
        self.assertEqual(self.lua.globals().scans, 0)
        self.run_lua('pawn.address=pawn.address+100')
        second = self.run_lua('return collection.context()')[0]
        self.assertNotEqual(first, second)
        self.run_lua('world.address=world.address+100')
        self.assertNotEqual(second, self.run_lua('return collection.context()')[0])

    def test_noncharacter_pawn_does_not_enumerate_sources(self):
        self.run_lua('pawn.kind="VoyageBoatPawn"')
        result = self.scan()
        self.assertEqual(result['status'], 'no_player')
        self.assertFalse(result['complete'])
        self.assertEqual(self.lua.globals().scans, 0)

    def test_source_and_inventory_caps_are_explicit_and_counts_partial(self):
        self.run_lua('''
            config.max_sources=1;config.max_slots=1
            local p=paint("DA_Paint_Canada","Last Leaf")
            box("One",1,{entry(p,2),entry(p,500)});box("Two",2,{entry(p,900)})
        ''')
        result = self.scan()
        self.assertTrue(result['sourcesTruncated'])
        self.assertTrue(result['sources'][0]['slotsTruncated'])
        self.assertEqual(result['sourceCount'], 2)
        self.assertEqual(result['includedSourceCount'], 1)
        self.assertEqual(result['physicalCount'], 2)
        self.assertFalse(result['complete'])

    def test_catalog_cap_is_explicit(self):
        self.run_lua('config.max_catalog=1;paint("DA_Paint_A","A");paint("DA_Paint_B","B")')
        result = self.scan()
        self.assertTrue(result['catalogTruncated'])
        self.assertFalse(result['catalogComplete'])
        self.assertFalse(result['complete'])

    def test_test_assets_are_excluded_from_rows_and_stock(self):
        self.run_lua('''
            local p=paint("DA_Test_Paint_1","Test","VoyagePaintItem /Game/Data/Assets/Items/Paints/Test/DA_Test_Paint_1.DA_Test_Paint_1")
            box("One",1,{entry(p,99)})
        ''')
        result = self.scan()
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['physicalCount'], 0)
        self.assertEqual(result['excludedTestAssets'], 1)

    def test_unresolved_entry_resolves_only_against_unique_native_catalog_asset(self):
        self.run_lua('paint("DA_Paint_Canada","Last Leaf");box("One",1,{entry(nil,12,"DA_Paint_Canada")})')
        self.assertEqual(self.scan()['physicalCount'], 12)
        self.run_lua('paint("DA_Paint_Canada","Other","VoyagePaintItem /Game/Other/DA_Paint_Canada.DA_Paint_Canada")')
        result = self.scan()
        self.assertEqual(result['physicalCount'], 0)
        self.assertFalse(result['complete'])
        self.assertEqual(len(result['unresolvedItems']), 1)

    def test_invalid_and_unreadable_counts_are_incomplete(self):
        for count in ('-1', '1.5', 'math.huge', '0/0', 'nil'):
            with self.subTest(count=count):
                self.setUp()
                self.run_lua('box("One",1,{entry(paint("DA_Paint_Canada","Last Leaf"),' + count + ')})')
                result = self.scan()
                self.assertFalse(result['complete'])
                self.assertEqual(result['physicalCount'], 0)

    def test_unresolved_unknown_item_type_is_not_assumed_to_be_nonpaint(self):
        self.run_lua('box("One",1,{entry(nil,12,"CustomAssetWithoutPaintName")})')
        result = self.scan()
        self.assertFalse(result['complete'])
        self.assertEqual(result['physicalCount'], 0)
        self.assertFalse(result['unresolvedItems'][0]['paintCandidate'])

    def test_duplicate_component_is_not_counted_twice(self):
        self.run_lua('local a,c=box("One",1,{entry(paint("DA_Paint_Canada","Last Leaf"),4)});containers[#containers+1]=c')
        result = self.scan()
        self.assertEqual(result['physicalCount'], 4)
        self.assertEqual(len(result['sources']), 1)

    def test_reappearing_box_is_automatically_counted_with_a_new_runtime_id(self):
        self.run_lua('a,c=box("One",1,{entry(paint("DA_Paint_Canada","Last Leaf"),4)})')
        first = self.scan()
        self.run_lua('containers={}')
        self.assertEqual(self.scan()['physicalCount'], 0)
        self.run_lua('a.address=a.address+100;c.address=c.address+100;containers={c}')
        reappeared = self.scan()
        self.assertNotEqual(first['sources'][0]['id'], reappeared['sources'][0]['id'])
        self.assertEqual(reappeared['physicalCount'], 4)
        self.assertEqual(reappeared['includedSourceCount'], 1)

    def test_radius_changes_automatically_include_newly_eligible_boxes(self):
        self.run_lua('''
            config.ship_radius_cm=4000
            local p=paint("DA_Paint_Canada","Last Leaf")
            box("Near",4000,{entry(p,2)});box("Far",4500,{entry(p,3)})
        ''')
        self.assertEqual(self.scan()['physicalCount'], 2)
        self.run_lua('config.ship_radius_cm=5000')
        self.assertEqual(self.scan()['physicalCount'], 5)
        self.run_lua('config.ship_radius_cm=3500')
        self.assertEqual(self.scan()['physicalCount'], 0)

    def test_new_world_automatically_counts_its_boxes_without_replaying_old_ids(self):
        self.run_lua('''
            p=paint("DA_Paint_Canada","Last Leaf")
            old_world=world
            box("Old",1,{entry(p,4)},nil,old_world)
        ''')
        first = self.scan()
        self.run_lua('''
            world=obj("World /Game/Other.Other","World")
            pawn.address=pawn.address+100
            box("New",1,{entry(p,7)})
        ''')
        result = self.scan()
        self.assertNotEqual(first['contextToken'], result['contextToken'])
        self.assertEqual(result['sourceCount'], 1)
        self.assertEqual(result['includedSourceCount'], 1)
        self.assertEqual(result['physicalCount'], 7)
        self.assertIn('.New', result['sources'][0]['ownerPath'])

    def test_context_change_during_scan_discards_the_stock_view(self):
        self.run_lua('''
            local p=paint("DA_Paint_Canada","Last Leaf")
            vault.HasItem=function() pawn.address=pawn.address+1;return false end
        ''')
        result = self.scan()
        self.assertEqual(result['status'], 'context_changed')
        self.assertFalse(result['complete'])
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['physicalCount'], 0)

    def test_discovery_failures_are_not_reported_as_empty_complete_stock(self):
        self.run_lua('FindAllOf=function() error("not available") end')
        result = self.scan()
        self.assertFalse(result['complete'])
        self.assertFalse(result['catalogComplete'])
        self.assertTrue(any('not available' in error for error in result['errors']))


if __name__ == '__main__':
    unittest.main()
