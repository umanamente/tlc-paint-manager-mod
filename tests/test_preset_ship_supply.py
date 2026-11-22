"""Direct preset paint sourcing across the backpack and eligible ship boxes."""
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_presets import PRESET_FIXTURE


BOX_FIXTURE = r'''
stockActors = {}
local before_find = FindAllOf
function FindAllOf(kind)
    if kind == "BP_Inventory_PlayerBox_C" then return stockActors end
    return before_find(kind)
end
local ship = require("paintmanager.ship_scope")
ship.contains = function(scope, point) return math.abs(point.X - scope.center.X) <= scope.radiusCm end
function ship_box(entries)
    local id = #stockActors + 1
    local owner = box("Source"..id, 1, {})
    local inv = inventory("Inventory /Game/Map.Map.Source"..id..".BoxInventory", owner,
        "VoyageInventoryWeightLimitedComponent", entries)
    owner.location = { X=1, Y=0, Z=0 }
    function owner:K2_GetActorLocation() return self.location end
    owner.VoyageInventoryWeightLimited, owner.IsLocked, owner.InteractEnabled = inv, false, true
    owner.HasAnyFlags, inv.HasAnyFlags = function() return false end, function() return false end
    stockActors[id] = owner
    return owner, inv
end
function plain_entry(item, count)
    local row = entry(item, count)
    row.Data.ItemData, row.Data.ItemDataMap, row.Data.Timestamp, row.Data.Flags = {}, {}, 101, 1
    return row
end
function pawn.PaintInventory:TransferSlot(source, slot, target, amount)
    assert(amount == 1)
    nativeCalls = nativeCalls+1
    for i, pair in ipairs(source.rows) do
        if pair[1] == slot then
            local row = pair[2]
            assert(row.Data.ItemCount > 0)
            self.rows[#self.rows+1] = {target, moved_can(row)}
            if row.Data.ItemCount == 1 then table.remove(source.rows,i)
            else row.Data.ItemCount = row.Data.ItemCount-1 end
            return 1
        end
    end
    error("Native source was empty")
end
'''


class PresetShipSupplyTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(PRESET_FIXTURE)
        self.lua.execute(BOX_FIXTURE)

    def test_occupied_deck_gets_box_color_and_returns_the_old_can_to_the_backpack(self):
        self.lua.execute('''
        start("boat");backpack.rows={}
        boatPaint.rows={{20,entry(paintB,1)},{30,entry(paintA,1)}}
        local _,inv=ship_box({{7,entry(paintA,1)}})
        drain(job);local r=job:get_receipt();assert(r.outcome=="applied_verified",r.error)
        assert(nativeCalls==2 and r.completed==1 and #inv.rows==0)
        assert(#backpack.rows==1 and backpack.rows[1][2].Item:get()==paintB)
        assert(#boatPaint.rows==2 and boatPaint.rows[2][1]==20 and boatPaint.rows[2][2].Item:get()==paintA)
        assert(r.transfers[1].destinationKind=="backpack" and r.transfers[2].sourceKind=="ship_box")
        ''')

    def test_full_backpack_rejects_displacement_even_with_free_space_in_the_source_box(self):
        self.lua.execute('''
        start("boat");backpack.rows={};backpack.full=true
        boatPaint.rows={{20,entry(paintB,1)},{30,entry(paintA,1)}}
        local _,inv=ship_box({{7,entry(paintA,1)}})
        drain(job);local r=job:get_receipt()
        assert(r.outcome=="rejected" and r.error:find("backpack") and not r.attempted)
        assert(nativeCalls==0 and #inv.rows==1 and boatPaint.rows[1][2].Item:get()==paintB)
        ''')

    def test_ship_box_only_stack_installs_once_per_zone(self):
        self.lua.execute('''
        require("paintmanager.ship_scope").player_access=function() error("Preset queried player distance") end
        start(); backpack.rows={}
        owner,inv=ship_box({{7,plain_entry(paintA,3)}})
        drain(job); local r=job:get_receipt(); assert(r.outcome=="applied_verified",r.error)
        assert(#backpack.rows==0 and #pawn.PaintInventory.rows==2 and inv.rows[1][2].Data.ItemCount==1)
        assert(nativeCalls==2 and r.transfers[1].sourceKind=="ship_box")
        ''')

    def test_mixed_sources_prefer_backpack_and_skip_locked_outside_foreign_and_opaque_stacks(self):
        self.lua.execute('''
        start(); backpack.rows={{1,entry(paintA,1)}}
        local locked=ship_box({{1,entry(paintA,1)}}); locked.IsLocked=true
        local far=ship_box({{1,entry(paintA,1)}}); far.location.X=config.ship_radius_cm+1
        local foreign=ship_box({{1,entry(paintA,1)}}); function foreign:GetWorld() return boat end
        local _,opaque=ship_box({{1,entry(paintA,8)}})
        local _,accessible=ship_box({{1,entry(paintA,1)}})
        drain(job); local r=job:get_receipt(); assert(r.outcome=="applied_verified",r.error)
        assert(r.transfers[1].sourceKind=="backpack" and r.transfers[2].sourceKind=="ship_box")
        assert(#accessible.rows==0 and opaque.rows[1][2].Data.ItemCount==8)
        ''')

    def test_shortage_or_changed_box_blocks_mutation(self):
        self.lua.execute('''
        start(); backpack.rows={}; owner,inv=ship_box({{9,plain_entry(paintA,1)}})
        drain(job); assert(job:get_receipt().outcome=="rejected" and nativeCalls==0)
        inv.rows[1][2]=plain_entry(paintA,2)
        job=presets.begin_apply(config,request); until_phase(job,"preset_commit")
        owner.IsLocked=true
        drain(job); assert(job:get_receipt().outcome=="rejected" and nativeCalls==0 and #inv.rows==1)
        ''')

    def test_collection_offers_backpack_only_color_and_keeps_box_evidence_separate(self):
        self.lua.execute('''
        backpack.rows={{5,plain_entry(paintA,4)},{6,plain_entry(paintB,1)}}
        box("Stock",1,{entry(paintB,2)})
        local captured=collection.scan(config)
        local view=require("paintmanager.paint_view")
        assert(captured.combinedPhysicalCount==7 and captured.physicalCount==2)
        for _,r in ipairs(captured.rows) do
            local total=view.physical_count(captured,r)
            if r.assetName=="DA_Paint_Canada" then
                assert(total==4 and r.count==0 and #r.stockLocationHints==0)
                assert(view.learn_eligible(captured,r))
            else assert(total==3 and r.count==2 and not view.learn_eligible(captured,r)) end
        end
        ''')

    def test_backpack_only_color_arriving_after_catalog_preparation_enters_the_grid(self):
        self.lua.execute('''
        backpack.rows={{5,plain_entry(paintA,3)}}
        local previous={contextToken=collection.context(),status="catalog_prepared",rows={},
            catalogComplete=true,recipeCatalogComplete=true}
        local job=collection.begin(config,{catalog=previous,openCatalog=true,background=false,presentation="paints"})
        local result=drain(job);assert(result.state=="done",result.error)
        assert(#result.model.rows==1 and result.model.rows[1].itemPath==paintA.path)
        assert(result.model.rows[1].assetType=="Item" and result.model.combinedPhysicalCount==3)
        ''')
