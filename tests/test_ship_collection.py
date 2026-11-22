"""Actual collection/proof flows use the fixed ship point and selected 35..50 m range."""
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_collection_jobs import JOB_FIXTURE
from test_learning_proof import PROOF_FIXTURE


SHIP_FIXTURE = r'''
ship_module=package.loaded["paintmanager.ship_scope"]
fixtureShipRadius=3500
config.ship_radius_cm=3500
ship_module.current=function(c,expected)
    if fixtureShipUnavailable then return nil,"Ship unavailable"end
    local radius=fixtureShipRadius or c.ship_radius_cm or 3500
    local revision="ship_point_v2:"..tostring(math.tointeger(radius))
    local scope={token="ship:"..tostring(world.address)..":"..revision,
        worldId=world.path.."@"..tostring(world.address),center=fixtureShipOrigin,
        radiusCm=radius,policyRevision=revision,sourcePolicyVersion=2,shipId="0000000000000001"}
    if expected and expected.token~=scope.token then return nil,"Ship scope changed"end
    return scope
end
player_position={X=0,Y=0,Z=0}
function pawn:K2_GetActorLocation()return player_position end
container_queries,paint_queries=0,0
local find_all=FindAllOf
function FindAllOf(kind)
    if kind=="VoyageInventoryWeightLimitedComponent"then container_queries=container_queries+1 end
    if kind=="VoyagePaintItem"then paint_queries=paint_queries+1 end
    return find_all(kind)
end
function stock_job(options)
    options=options or {}; options.background=false
    job=collection.begin(config,options)
    result=drain()
    return result
end
function proof_scene(box_position)
    p=paint("DA_Paint_A","Actual paint")
    actor,component=box("Witness",box_position or 100,{entry(p,4)})
    capture_request()
    assert(model.stockRadiusCm==fixtureShipRadius and model.stockComplete)
end
'''


class ShipCollectionTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + JOB_FIXTURE + PROOF_FIXTURE + SHIP_FIXTURE)

    def run_lua(self, code):
        self.lua.execute(code)

    def test_background_scan_uses_ship_center_when_character_is_far_away(self):
        self.run_lua(r'''
        fixtureShipOrigin={X=10000,Y=0,Z=0}
        player_position={X=999999,Y=0,Z=0}
        fixtureShipRadius=3500
        local paint=paint("DA_Paint_A","A")
        box("ShipBoundary",13500,{entry(paint,4)})
        box("OutsideShip",13500.01,{entry(paint,100)})
        box("BesideCharacter",1000000,{entry(paint,1000)})
        stock_job()
        assert(result.state=="done" and result.model.stockComplete)
        assert(result.model.physicalCount==4 and result.model.includedSourceCount==1)
        assert(result.model.capture.origin.X==10000 and result.model.stockRadiusCm==3500)
        assert(result.model.stockScope.sourcePolicyVersion==2)
        assert(result.model.radiusCm==3500 and inventory_reads==1 and mutations==0)
        assert(no_native_handles(result.model))
        ''')

    def test_selected_50m_radius_is_a_3d_boundary(self):
        self.run_lua(r'''
        fixtureShipRadius=5000;config.ship_radius_cm=5000
        local paint=paint("DA_Paint_A","A")
        local boundary=box("UpperDeck",0,{entry(paint,4)})
        function boundary:K2_GetActorLocation()return {X=0,Y=0,Z=5000}end
        local outside=box("AboveSphere",0,{entry(paint,100)})
        function outside:K2_GetActorLocation()return {X=0,Y=0,Z=5000.01}end
        stock_job()
        assert(result.state=="done" and result.model.physicalCount==4)
        assert(result.model.stockRadiusCm==5000 and result.model.radiusCm==5000)
        assert(inventory_reads==1)
        ''')

    def test_missing_ship_never_falls_back_to_character_position(self):
        self.run_lua(r'''
        local paint=paint("DA_Paint_A","A");box("AtPlayer",0,{entry(paint,4)})
        fixtureShipUnavailable=true
        stock_job()
        assert(result.state~="done" and result.model==nil)
        assert(job.model.stockComplete==false and inventory_reads==0 and container_queries==0)
        assert(mutations==0)
        ''')

    def test_candidate_filter_samples_current_ship_position_after_sailing(self):
        self.run_lua(r'''
        local paint=paint("DA_Paint_A","A");box("NewShipPosition",10000,{entry(paint,4)})
        job=collection.begin(config,{background=false})
        until_phase("source_filter")
        fixtureShipOrigin={X=10000,Y=0,Z=0}
        result=drain()
        assert(result.state=="done" and result.model.stockComplete and result.model.physicalCount==4)
        assert(result.model.sources[1].distanceCm==0 and inventory_reads==1)
        ''')

    def test_reread_excludes_a_box_left_behind_by_moving_ship(self):
        self.run_lua(r'''
        local paint=paint("DA_Paint_A","A");box("ShoreBox",100,{entry(paint,4)})
        job=collection.begin(config,{background=false})
        until_phase("container_read")
        fixtureShipOrigin={X=10000,Y=0,Z=0}
        result=drain()
        assert(result.state=="done" and not result.model.stockComplete)
        assert(result.model.physicalCount==0 and inventory_reads==0)
        ''')

    def test_reread_includes_a_box_moving_with_ship_without_frozen_origin(self):
        self.run_lua(r'''
        local paint=paint("DA_Paint_A","A")
        local box_actor=box("Aboard",100,{entry(paint,4)})
        local current_position={X=100,Y=0,Z=0}
        function box_actor:K2_GetActorLocation()return current_position end
        job=collection.begin(config,{background=false});until_phase("container_read")
        fixtureShipOrigin={X=10000,Y=0,Z=0};current_position={X=10100,Y=0,Z=0}
        result=drain()
        assert(result.state=="done" and result.model.stockComplete and result.model.physicalCount==4)
        assert(result.model.sources[1].distanceCm==100 and result.model.sources[1].location.X==10100)
        ''')

    def test_final_proof_rereads_source_radius_after_native_inventory_callback(self):
        self.run_lua(r'''
        proof_scene(-3000)
        set_entries(component,{entry(p,4)},function()fixtureShipOrigin={X=1000,Y=0,Z=0}end)
        prove()
        assert(not proof_ok and proof_calls==0 and mutations==0)
        assert(tostring(proof_result):find("radius"))
        ''')

    def test_final_proof_uses_center_returned_by_fresh_connection_check(self):
        self.run_lua(r'''
        proof_scene(-3000)
        local original_access=ship_module.player_access
        local access_calls=0
        ship_module.player_access=function(c,expected)
            access_calls=access_calls+1
            if access_calls==2 then fixtureShipOrigin={X=1000,Y=0,Z=0}end
            return original_access(c,expected)
        end
        prove()
        assert(access_calls==2 and not proof_ok and proof_calls==0 and mutations==0)
        assert(tostring(proof_result):find("radius"))
        ''')

    def test_ship_motion_does_not_reject_still_eligible_fresh_witnesses(self):
        self.run_lua(r'''
        proof_scene(100)
        local source_position={X=100,Y=0,Z=0}
        function actor:K2_GetActorLocation()return source_position end
        set_entries(component,{entry(p,4)},function()
            fixtureShipOrigin={X=1000,Y=0,Z=0};source_position={X=1100,Y=0,Z=0}
        end)
        prove()
        assert(proof_ok and proof_calls==1 and mutations==0)
        assert(last_proof.radiusCm==3500 and last_proof.count==4)
        ''')

    def test_registry_reuse_keeps_negative_boxes_and_full_discovery_finds_new_empty_box(self):
        self.run_lua(r'''
        local paint=paint("DA_Paint_A","A")
        box("Positive",10,{entry(paint,4)})
        local _,negative=box("Negative",20,{})
        stock_job();local first=result.model;local registry=job:get_registry()
        assert(#registry==2 and no_native_handles(registry))
        set_entries(negative,{entry(paint,2)})
        local _,new_empty=box("NewEmpty",30,{})
        local before_queries=container_queries+paint_queries
        stock_job({catalog=first,sourceRefs=registry,shipScope=first.stockScope})
        assert(result.state=="done" and result.model.reusedBoxRegistry and result.model.physicalCount==6)
        assert(container_queries+paint_queries==before_queries)
        local second=result.model
        stock_job({catalog=second,shipScope=second.stockScope})
        assert(result.state=="done" and container_queries+paint_queries==before_queries+1)
        registry=job:get_registry();assert(#registry==3)
        local third=result.model
        set_entries(new_empty,{entry(paint,3)})
        before_queries=container_queries+paint_queries
        stock_job({catalog=third,sourceRefs=registry,shipScope=third.stockScope})
        assert(result.model.physicalCount==9 and container_queries+paint_queries==before_queries)
        assert(mutations==0)
        ''')

    def test_changed_expected_scope_rejects_before_any_box_read(self):
        self.run_lua(r'''
        local paint=paint("DA_Paint_A","A");box("Witness",100,{entry(paint,4)})
        local expected=ship_module.current(config)
        fixtureShipRadius=5000
        stock_job({shipScope=expected})
        assert(result.state~="done" and result.model==nil and inventory_reads==0)
        assert(not job.model.stockComplete and mutations==0)
        ''')

    def test_scope_change_after_last_read_cannot_publish_completed_old_stock(self):
        self.run_lua(r'''
        local paint=paint("DA_Paint_A","A");box("Witness",100,{entry(paint,4)})
        job=collection.begin(config,{background=false})
        until_phase("aggregate")
        assert(inventory_reads==1)
        fixtureShipRadius=5000
        result=drain()
        assert(result.state~="done" and result.model==nil)
        assert(not job.model.stockComplete and mutations==0)
        ''')


if __name__ == "__main__":
    unittest.main()
