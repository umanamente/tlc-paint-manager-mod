"""Use cached ship-box locations only to find live, admissible preset supplies."""
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_preset_ship_supply import BOX_FIXTURE
from test_presets import PRESET_FIXTURE


CACHED_SUPPLY = r'''
local supply = require("paintmanager.preset_supply")
local ship = require("paintmanager.ship_scope")
ship.same_scope = function(a,b) return a.token==b.token and a.worldId==b.worldId and a.radiusCm==b.radiusCm end
local original_read, find = supply.read_box,FindAllOf
planning_reads,stock_discoveries={},0
function supply.read_box(config,context,scope,box)
    if job and job.phase=="preset_boxes" then
        local id=box.ownerRef.id
        planning_reads[id]=(planning_reads[id] or 0)+1
    end
    return original_read(config,context,scope,box)
end
function FindAllOf(kind)
    if kind=="BP_Inventory_PlayerBox_C" then stock_discoveries=stock_discoveries+1 end
    return find(kind)
end
function visits(owner) return planning_reads[collection.runtime_ref(owner).id] or 0 end
function install_hints(owners,first,second)
    local bank={}
    for _,o in ipairs(owners) do bank[#bank+1]={ownerRef=collection.runtime_ref(o),
        componentRef=collection.runtime_ref(o.VoyageInventoryWeightLimited),kind=collection.box_kind(o)} end
    request.stockModel={status="captured",contextToken=collection.context(),stockScope=ship.current(config),
        stockComplete=true,stockLocationSources=bank,rows={
            {assetType="Item",assetName=paintA:GetFName():ToString(),stockLocationHints=first},
            {assetType="Item",assetName=paintB:GetFName():ToString(),stockLocationHints=second or {}}}}
end
function different_colors()
    request.preset.zones[2].paint={assetType="Item",assetName=paintB:GetFName():ToString()}
end
function verified()
    drain(job)
    local r=job:get_receipt();assert(r.outcome=="applied_verified",r.error)
end
'''


class PresetCachedSupplyTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + PRESET_FIXTURE + BOX_FIXTURE + CACHED_SUPPLY)

    def test_two_hinted_sources_among_many_boxes_skip_all_unneeded_boxes(self):
        self.lua.execute('''
        start();backpack.rows={};different_colors()
        local idle={};for i=1,48 do idle[i]=ship_box({}) end
        local a=ship_box({{4,plain_entry(paintA,2)}})
        local extra=ship_box({{5,plain_entry(paintA,2)}})
        local b=ship_box({{3,plain_entry(paintB,2)}})
        install_hints({a,extra,b},{1,2},{3})
        verified()
        for _,owner in ipairs(idle) do assert(visits(owner)==0) end
        assert(visits(a)==1 and visits(b)==1 and visits(extra)==0 and stock_discoveries==0 and nativeCalls==2)
        local search=job.receipt.boxSearch
        assert(search.hintedCandidates==2 and search.fallbackCandidates==0 and not search.usedDiscovery)
        ''')

    def test_backpack_reservation_removes_that_color_from_the_box_search(self):
        self.lua.execute('''
        start();backpack.rows={{7,plain_entry(paintA,1)}};different_colors()
        local a=ship_box({{1,plain_entry(paintA,1)}});local b=ship_box({{1,plain_entry(paintB,1)}})
        install_hints({a,b},{1},{2})
        verified()
        assert(stock_discoveries==0 and visits(a)==0 and visits(b)==1)
        assert(job.receipt.transfers[1].sourceKind=="backpack" and job.receipt.transfers[2].sourceKind=="ship_box")
        ''')

    def test_partial_stale_and_locked_hints_fall_back_without_rereading_an_owner(self):
        self.lua.execute('''
        start();backpack.rows={};different_colors()
        local a=ship_box({{1,plain_entry(paintA,1)}});local empty=ship_box({})
        local locked=ship_box({{1,plain_entry(paintB,2)}});locked.IsLocked=true
        local b=ship_box({{1,plain_entry(paintB,1)}})
        install_hints({a,empty,locked},{1,1},{2,2,3,99})
        request.stockModel.stockComplete=false
        verified()
        assert(stock_discoveries==1 and visits(a)==1 and visits(empty)==1 and visits(locked)==1 and visits(b)==1)
        ''')

    def test_skipped_hint_can_still_supply_a_different_color_during_fallback(self):
        self.lua.execute('''
        start();backpack.rows={};different_colors()
        local a=ship_box({{1,plain_entry(paintA,1)}});local moved=ship_box({{1,plain_entry(paintB,1)}})
        install_hints({a,moved},{1,2},{})
        verified()
        assert(stock_discoveries==1 and visits(a)==1 and visits(moved)==1)
        ''')

    def test_absent_foreign_or_preview_capture_uses_normal_box_discovery(self):
        for change in ('request.stockModel=nil', 'request.stockModel.contextToken="other"',
                       'request.stockModel.stockScope.shipId="other"', 'request.stockModel.stockScope.token="other"',
                       'request.stockModel.cachePreview=true'):
            with self.subTest(change=change):
                self.setUp()
                self.lua.execute('''
                start();backpack.rows={}
                local idle=ship_box({});local a=ship_box({{0,plain_entry(paintA,2)}})
                install_hints({a},{1})
                ''' + change + '''
                verified();assert(stock_discoveries==1 and visits(idle)==1 and visits(a)==1)
                ''')

    def test_reserved_box_must_still_pass_commit_checks(self):
        self.lua.execute('''
        start();backpack.rows={}
        local a=ship_box({{9,plain_entry(paintA,2)}})
        install_hints({a},{1});until_phase(job,"preset_commit")
        a.IsLocked=true
        drain(job);assert(job.receipt.outcome=="rejected" and nativeCalls==0 and stock_discoveries==0)
        ''')
