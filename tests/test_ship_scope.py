"""Fixed 35..50 m ship-point scope, fresh positions, and fail-closed admission."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime

SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
scope=require("paintmanager.ship_scope")
config={}
objects,boats,queries,native_reads={},{},0,0
local address=100
function object(name,class,ancestors)
    address=address+1
    local value={name=name,class=class,address=address,valid=true,template=false,ancestors=ancestors or {}}
    function value:IsValid()return self.valid end
    function value:GetFullName()return "Object "..self.name end
    function value:GetAddress()return self.address end
    function value:GetClass()
        return {IsValid=function()return true end,GetFullName=function()return "Class "..self.class end}
    end
    function value:IsA(class)return self.class==class or self.ancestors[class]==true end
    function value:HasAnyFlags()return self.template end
    function value:GetWorld()return self.world or world end
    function value:GetOwner()return self.owner end
    objects[name]=value
    return value
end
world=object("/World","/Script/Engine.World")
controller=object("/Controller","/Script/Voyage.VoyagePlayerController")
function controller:IsLocalController()return self.localController~=false end
character=object("/Character","/Script/Voyage.VoyageCharacter")
character.position={X=1000,Y=200,Z=50}
function character:K2_GetActorLocation()return self.position end
controller.Pawn=character
misc=object("/Script/Voyage.Default__VoyageMiscBlueprintFunctionLibrary","/Script/Voyage.VoyageMiscBlueprintFunctionLibrary")
function misc:GetCharacterOrDriver(pawn)return pawn.driver end
persistence=object("/Script/Voyage.Default__VoyagePersistentSubsystem","/Script/Voyage.VoyagePersistentSubsystem")
function persistence:GetPersistentActorComponent(actor)return actor.persistent end
function new_ship(name,position)
    local ship=object(name,"/Game/Blueprints/Vehicles/BP_Boat_Barge.BP_Boat_Barge_C",
        {["/Script/Voyage.VoyageBoatPawn"]=true})
    ship.position=position or {X=1000,Y=200,Z=50}
    function ship:K2_GetActorLocation()native_reads=native_reads+1;return self.position end
    ship.persistent=object(name..".Persistent","/Script/Voyage.VoyagePersistentActorComponent")
    ship.persistent.owner,ship.persistent.PersistentUniqueIdRuntime=ship,123
    boats[#boats+1]=ship
    return ship
end
ship=new_ship("/Barge")
function FindFirstOf(kind)assert(kind=="VoyagePlayerController");return controller end
function FindAllOf(kind)assert(kind=="VoyageBoatPawn");queries=queries+1;return boats end
function StaticFindObject(name)return objects[name] end
function prepare(options)
    local job=scope.begin(config,options)
    local p=job:step(1)
    assert(p.state=="done",p.error)
    captured=p.scope
    return captured
end
function no_handles(value)
    if type(value)~="table"then return true end
    for key,item in pairs(value)do
        assert(type(key)~="table" and type(item)~="function" and type(item)~="userdata")
        if type(item)=="table"then no_handles(item)end
    end
    return true
end
function budget(used)
    return {used=used or 0,expired=function(self)return self.used>=1 end,
        consume=function(self,n)self.used=self.used+(n or 1)end}
end
'''


class ShipScopeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def run_lua(self, code):
        self.lua.execute(code)

    def test_default_point_radius_is_35m_without_any_mesh_or_level_components(self):
        self.run_lua(r'''
        assert(ship.MeshComponent==nil and ship.RootComponent==nil and ship.LevelInstanceComponent==nil)
        local s=prepare()
        assert(s.radiusCm==3500 and s.sourcePolicyVersion==2)
        assert(s.center.X==1000 and s.center.Y==200 and s.center.Z==50)
        assert(s.anchorSource=="native_ship_actor_position" and no_handles(s))
        assert(s.localCenter==nil and s.hullRadiusCm==nil and s.marginCm==nil)
        assert(scope.current(config,{token=s.token}))
        ''')

    def test_no_transform_bounds_scale_or_level_api_is_touched(self):
        self.run_lua(r'''
        local function forbidden()error("Removed hull API was used")end
        ship.GetTransform,ship.GetActorBounds,ship.GetActorScale3D=forbidden,forbidden,forbidden
        ship.MeshComponent={GetLocalBounds=forbidden}
        ship.RootComponent={GetWorld=forbidden}
        ship.LevelInstanceComponent={GetOwner=forbidden}
        misc.GetComponentsBoundingBoxLocal=forbidden
        local s=prepare()
        assert(scope.current(config,s).radiusCm==3500)
        local access=scope.player_access(config,s);assert(access)
        ''')

    def test_cockpit_preset_checks_ship_identity_without_positions_or_stock_settings(self):
        self.run_lua(r'''
        ship.position,character.position=nil,nil
        config.ship_radius_cm=-1
        local selected=assert(scope.editor_ship(config))
        assert(selected.shipRef.id==ship:GetFullName().."@"..ship.address and no_handles(selected))
        assert(queries==1 and native_reads==0)
        assert(scope.editor_ship(config,selected) and queries==1 and native_reads==0)
        ship.persistent.PersistentUniqueIdRuntime=456
        assert(scope.editor_ship(config,selected)==nil and queries==1 and native_reads==0)
        new_ship("/Second")
        assert(scope.editor_ship(config)==nil and queries==2 and native_reads==0)
        ''')

    def test_50m_limit_is_inclusive_and_3d_outside_is_disconnected(self):
        self.run_lua(r'''
        config.ship_radius_cm=5000
        local s=prepare()
        character.position={X=1000,Y=200,Z=5050}
        local access,err,detail=scope.player_access(config,s)
        assert(access and detail.connected and detail.distanceCm==5000 and detail.radiusCm==5000)
        character.position.Z=5050.01
        access,err,detail=scope.player_access(config,s)
        assert(access==nil and not detail.connected and detail.code=="signal_lost")
        assert(detail.distanceCm>5000 and detail.distanceReference=="ship_center" and not detail.displayOnly)
        assert(detail.geometryDiagnostics==nil and no_handles(detail))
        ''')

    def test_default_35m_boundary_is_exact_and_contains_rejects_oversized_scopes(self):
        self.run_lua(r'''
        local s=prepare()
        local inside,distance=scope.contains(s,{X=4500,Y=200,Z=50})
        assert(inside and distance==3500)
        assert(not scope.contains(s,{X=4500.01,Y=200,Z=50}))
        s.radiusCm=5001
        local accepted,d=scope.contains(s,s.center)
        assert(not accepted and d==nil)
        ''')

    def test_radius_never_uses_legacy_radius_or_old_hull_margin_settings(self):
        self.run_lua(r'''
        config.radius_cm=999999;config.ship_scan_margin_cm=123456
        config.ship_max_radius_cm=999999;config.ship_min_radius_cm=1
        assert(prepare().radiusCm==3500)
        config.ship_radius_cm=5000
        assert(scope.current(config).radiusCm==5000)
        config.ship_radius_cm=5001
        assert(scope.current(config)==nil)
        assert(scope.begin(config):get_progress().state=="failed")
        ''')

    def test_out_of_range_nonfinite_and_fractional_settings_fail_before_discovery(self):
        for value in ("3499", "0", "-1", "5001", "math.huge", "0/0", "3500.5", '"3500"'):
            with self.subTest(value=value):
                self.setUp()
                self.run_lua("config.ship_radius_cm="+value+r'''
                config.ship_min_radius_cm=0;config.ship_max_radius_cm=999999
                local p=scope.begin(config):get_progress()
                assert(p.state=="failed" and queries==0 and native_reads==0)
                assert(scope.current(config)==nil)
                ''')

    def test_setting_increase_changes_scope_token_but_same_integer_value_does_not(self):
        self.run_lua(r'''
        local old=prepare()
        config.ship_radius_cm=4000
        assert(scope.current(config,old)==nil)
        local new=assert(scope.current(config))
        assert(new.radiusCm==4000 and new.token~=old.token and new.policyRevision~=old.policyRevision)
        config.ship_radius_cm=4000.0
        assert(scope.current(config,new).token==new.token)
        ''')

    def test_sailing_updates_canonical_center_without_changing_token_or_discovery(self):
        self.run_lua(r'''
        local old=prepare();local before=queries
        ship.position={X=10000,Y=-500,Z=25}
        local current=assert(scope.current(config,old))
        assert(current.center.X==10000 and current.center.Y==-500 and current.center.Z==25)
        assert(current.radiusCm==3500 and current.token==old.token and queries==before)
        current.center.X=0
        assert(scope.current(config).center.X==10000)
        assert(scope.describe(old).center.X==1000)
        ''')

    def test_character_distance_does_not_select_another_ship_or_move_the_center(self):
        self.run_lua(r'''
        local s=prepare();character.position={X=999999,Y=200,Z=50}
        local current=assert(scope.current(config,s))
        assert(current.center.X==1000 and current.token==s.token)
        local access,err,detail=scope.player_access(config,s)
        assert(access==nil and detail.code=="signal_lost" and detail.radiusCm==3500)
        assert(detail.distanceCm==998999)
        ''')

    def test_multiple_live_exact_barges_are_ambiguous_even_with_expected_id(self):
        self.run_lua(r'''
        local second=new_ship("/Second",{X=999999,Y=0,Z=0})
        second.persistent.PersistentUniqueIdRuntime=456
        local p=scope.begin(config,{expectedShipId="000000000000007b"}):step(1)
        assert(p.state=="failed" and p.code=="ship_ambiguous")
        local current,err,detail=scope.player_access(config)
        assert(current==nil and detail.distanceCm==nil and not detail.connected)
        ''')

    def test_templates_foreign_world_and_subclasses_are_excluded(self):
        self.run_lua(r'''
        local template=new_ship("/Template");template.template=true
        local foreign=new_ship("/Foreign");foreign.world=object("/ForeignWorld","/Script/Engine.World")
        local custom=new_ship("/Custom");custom.class="/Game/Custom/OtherBarge.OtherBarge_C"
        assert(prepare().runtimeRef.name=="Object /Barge")
        ''')

    def test_missing_barge_has_no_player_origin_or_previous_distance_fallback(self):
        self.run_lua(r'''
        boats={}
        local p=scope.begin(config):step(1)
        assert(p.state=="failed")
        local current,err,detail=scope.player_access(config)
        assert(current==nil and not detail.connected and not detail.shipLocated)
        assert(detail.distanceCm==nil and detail.radiusCm==nil and detail.token==nil)
        ''')

    def test_world_ship_class_and_persistent_identity_changes_revoke_current_scope(self):
        changes = (
            "world.address=world.address+1",
            "controller.address=controller.address+1",
            "ship.address=ship.address+1",
            "ship.valid=false",
            'ship.class="/Game/Custom/OtherBarge.OtherBarge_C"',
            "ship.persistent.PersistentUniqueIdRuntime=456",
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.run_lua("local s=prepare();"+change+r'''
                assert(scope.current(config,s)==nil)
                local access,err,detail=scope.player_access(config)
                assert(access==nil and not detail.connected and detail.radiusCm==nil)
                ''')

    def test_missing_persistent_id_allows_runtime_scope_and_high_bit_id_is_lossless(self):
        self.run_lua(r'''
        ship.persistent.PersistentUniqueIdRuntime=0
        local s=prepare();assert(s.shipId==nil and scope.current(config,s))
        ship.persistent.PersistentUniqueIdRuntime=-1
        assert(scope.current(config)==nil)
        s=prepare()
        assert(s.shipId=="ffffffffffffffff" and scope.current(config,s))
        ''')

    def test_confirmed_native_zero_is_distinct_from_unreadable_or_wrong_owner(self):
        self.run_lua(r'''
        ship.persistent.PersistentUniqueIdRuntime=0
        local id,reason,status=scope.persistent_actor_id(ship)
        assert(id==nil and reason and status=="zero")
        ship.persistent.owner=character
        id,reason,status=scope.persistent_actor_id(ship)
        assert(id==nil and reason and status==nil)
        ship.persistent.owner=ship
        ship.persistent.PersistentUniqueIdRuntime=nil
        id,reason,status=scope.persistent_actor_id(ship)
        assert(id==nil and reason and status==nil)
        ''')

    def test_expected_ship_identity_and_expected_scope_never_silently_switch(self):
        self.run_lua(r'''
        local p=scope.begin(config,{expectedShipId="00000000000001c8"}):step(1)
        assert(p.state=="failed")
        local s=prepare()
        local access,err,detail=scope.player_access(config,{token=s.token.."-stale"})
        assert(access==nil and not detail.connected and detail.code=="ship_scope_changed")
        assert(detail.radiusCm==nil and detail.distanceCm==nil)
        ''')

    def test_vehicle_driver_uses_same_point_and_range_with_stable_character_identity(self):
        self.run_lua(r'''
        local s=prepare()
        local vehicle=object("/Vehicle","/Script/Voyage.VoyageVehiclePawn")
        vehicle.driver=character
        function vehicle:GetDriver()return self.driver end
        controller.Pawn=vehicle
        local char,err,detail=scope.character(controller)
        assert(char==character and detail.inVehicle and not scope.is_character_possessed(controller))
        local access,why,state=scope.player_access(config,s)
        assert(access and access.token==s.token and state.distanceCm==0 and state.radiusCm==3500)
        vehicle.driver=nil
        access,why,state=scope.player_access(config,s)
        assert(access==nil and not state.connected and state.code=="ship_player_unavailable")
        ''')

    def test_discovery_limit_and_invalid_position_fail_closed(self):
        self.run_lua(r'''
        config.ship_max_candidates=1
        new_ship("/Second")
        local p=scope.begin(config):step(1)
        assert(p.state=="failed" and p.error:find("limit"))
        boats={ship};ship.position={X=0/0,Y=0,Z=0}
        p=scope.begin(config):step(1)
        assert(p.state=="failed" and scope.current(config)==nil)
        ''')

    def test_begin_and_expired_budget_do_no_native_work_and_invalidation_revokes_jobs(self):
        self.run_lua(r'''
        local job=scope.begin(config)
        assert(queries==0 and native_reads==0)
        assert(job:step(budget(1)).state=="running" and queries==0)
        scope.invalidate("Load started")
        assert(job:step(1).state=="cancelled" and scope.current(config)==nil)
        local current,err,p=scope.prepare(config)
        assert(current and p.state=="done" and queries==1 and no_handles(p))
        local before=queries
        assert(scope.prepare(config,{refresh=true}))
        assert(queries==before)
        ''')


if __name__ == "__main__":
    unittest.main()
