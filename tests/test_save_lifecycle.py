"""Native account identity and map-completion admission for hybrid storage."""
import unittest

from lupa.lua54 import LuaRuntime

from test_save_bridge import FIXTURE, SCRIPTS


class SaveLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(r'''
        local original_find=StaticFindObject
        player=object("LocalPlayer /Local", "/Script/Engine.LocalPlayer",7)
        player.PlayerController=controller;controller.Player=player
        account=object("SaveGame /CDO", "/Script/Voyage.VoyageSaveGame",8)
        account_id="76561198000000001";account_calls=0
        function account:GetSaveGameUserId(p)
            assert(p==player);account_calls=account_calls+1
            if account_callback then return account_callback() end
            return account_id
        end
        function account:GetSaveGameRootDirectory() return "C:/Game/Saved/SaveGames/" end
        guidlib=object("Guid /CDO", "/Script/Engine.KismetGuidLibrary",9)
        guid_text="AABBCCDD0011422283330123456789AB";guid_calls=0
        function guidlib:NewGuid() guid_calls=guid_calls+1;return {A=1,B=2,C=3,D=4} end
        function guidlib:Conv_GuidToString(value)
            assert(value.A==1 and value.B==2 and value.C==3 and value.D==4)
            return {ToString=function() return guid_text end}
        end
        function StaticFindObject(path)
            if path=="/Script/Voyage.Default__VoyageSaveGame" then return account end
            if path=="/Script/Engine.Default__KismetGuidLibrary" then return guidlib end
            return original_find(path)
        end
        hooks={};registrations=0
        function RegisterLoadMapPreHook(cb) registrations=registrations+1;hooks.pre=cb end
        function RegisterLoadMapPostHook(cb) registrations=registrations+1;hooks.post=cb end
        function RegisterBeginPlayPostHook(cb) registrations=registrations+1;hooks.actor=cb end
        lifecycle=require("paintmanager.save_lifecycle")
        logs={}
        function install(build)
            return lifecycle.install({},build or "25982825",function(key,detail) logs[#logs+1]={key=key,detail=detail} end)
        end
        function wrap(value) return {get=function() return value end} end
        function load()
            hooks.pre();hooks.actor(wrap(hud));hooks.post(nil,wrap(world),nil,nil,"")
        end
        ''')

    def run_lua(self, source):
        self.lua.execute(source)

    def test_modules_are_inert_until_installed(self):
        self.run_lua('assert(registrations==0 and account_calls==0 and native_reads==0 and native_writes==0)')

    def test_stock_base_survives_vehicle_possession_without_admitting_storage(self):
        self.run_lua('''
        assert(install());load();local binding=lifecycle.consume()
        controller.Pawn=object("Vehicle /Helm", "/Script/Voyage.VoyageVehiclePawn",99)
        assert(bridge.stock_context(binding).status=="ready")
        assert(bridge.storage_context(token).status=="not_ready")
        assert(bridge.stock_context(binding).status=="ready")
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_stock_admission_at_map_completion_does_not_wait_for_helm_exit(self):
        self.run_lua('''
        assert(install())
        controller.Pawn=object("Vehicle /Helm", "/Script/Voyage.VoyageVehiclePawn",99)
        load()
        local ordinary=lifecycle.consume()
        assert(ordinary and ordinary.invalidated and ordinary.loadGeneration==1)
        local binding=lifecycle.consume_stock()
        assert(binding and binding.baseScopeId and binding.accountId==account_id)
        assert(binding.contextToken==nil and lifecycle.consume_stock()==nil)
        assert(bridge.stock_context(binding).status=="ready")
        assert(bridge.storage_context(token).status=="not_ready")
        assert(native_reads==0 and native_writes==0)
        hooks.pre()
        assert(not lifecycle.is_stock_ready(binding.baseScopeId,binding.accountId,binding.libraryPath))
        assert(bridge.stock_context(binding).status=="not_ready")
        ''')

    def test_actor_beginplay_alone_never_admits_read_only_stock(self):
        self.run_lua('''
        assert(install());hooks.pre();lifecycle.consume_stock();hooks.actor(wrap(hud))
        assert(lifecycle.consume_stock()==nil)
        assert(not lifecycle.is_stock_ready("any",account_id,"any"))
        assert(account_calls==0 and native_reads==0 and native_writes==0)
        ''')

    def test_stock_account_change_between_native_samples_rejects_admission(self):
        self.run_lua('''
        assert(install());load();local binding=lifecycle.consume()
        local initial=account_id
        local calls=0
        account_callback=function()
            calls=calls+1
            if calls==1 then account_id="76561198000000002";return initial end
            return account_id
        end
        assert(bridge.stock_context(binding).status=="not_ready" and calls==2)
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_stock_base_rejects_load_pre_and_changed_account_or_hud(self):
        for change in ('hooks.pre()', 'account_id="76561198000000002"', 'hud.address=999'):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('assert(install());load();binding=lifecycle.consume();' + change + '''
                assert(bridge.stock_context(binding).status=="not_ready")
                assert(native_reads==0 and native_writes==0)
                ''')

    def test_map_pre_revokes_lua_tasks_before_a_later_scheduler_consume(self):
        self.run_lua('''
        PaintManagerWorker=setmetatable({},{__index=function() error("Retired DLL bridge") end})
        local background=require("paintmanager.background")
        local first=assert(background.begin_task("analysis_view",{}))
        assert(install());hooks.pre()
        assert(first:step().state=="cancelled" and first:get_result()==nil)
        assert(not lifecycle.is_ready(token,'old'))
        assert(account_calls==0 and native_reads==0 and native_writes==0)
        local second=assert(background.begin_task("analysis_view",{}))
        hooks.pre();assert(second:step().state=="cancelled" and not background.is_busy())
        ''')

    def test_build_and_hook_availability_fail_closed(self):
        self.run_lua('''
        assert(not install("unknown") and registrations==0)
        RegisterBeginPlayPostHook=nil
        assert(not install() and registrations==0 and bridge.storage_context(token).status=="not_ready")
        ''')

    def test_actor_or_repeated_carrier_reads_never_establish_readiness(self):
        self.run_lua('''
        assert(install());hooks.actor(wrap(hud))
        for i=1,3 do assert(bridge.read(token).status=="missing") end
        assert(bridge.storage_context(token).status=="not_ready" and account_calls==0)
        hooks.pre();hooks.actor(wrap(hud))
        assert(bridge.storage_context(token).status=="not_ready" and account_calls==0)
        ''')

    def test_matched_native_map_completion_admits_exact_scope_and_native_account(self):
        self.run_lua('''
        assert(install());assert(install() and registrations==3)
        load();local event=lifecycle.consume()
        assert(event.status=="ready" and event.contextToken==token and event.loadGeneration==1)
        assert(lifecycle.consume()==nil)
        local value=bridge.storage_context(token)
        assert(value.status=="ready",value.error)
        assert(value.accountId==account_id and value.libraryPath=="C:/Game/Saved/SaveGames/76561198000000001/PaintManager.library.json")
        assert(value.scopeId==event.scopeId and value.loadGeneration==1)
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_map_pre_invalidates_immediately_and_failed_load_remains_closed(self):
        self.run_lua('''
        install();load();assert(bridge.storage_context(token).status=="ready")
        hooks.pre();assert(lifecycle.consume().invalidated)
        assert(bridge.storage_context(token).status=="not_ready")
        hooks.actor(wrap(hud));hooks.post(nil,wrap(world),nil,nil,"Load failed")
        assert(lifecycle.consume().invalidated and bridge.storage_context(token).status=="not_ready")
        ''')

    def test_missing_mismatched_or_replaced_hud_cannot_gain_readiness(self):
        for mutation in ('', 'hud.address=55', 'world.address=11'):
            with self.subTest(mutation=mutation):
                self.setUp()
                self.run_lua('install();hooks.pre();' +
                             ('hooks.actor(wrap(hud));' if mutation else '') + mutation +
                             ';hooks.post(nil,wrap(world),nil,nil,"");assert(bridge.storage_context(token).status=="not_ready")')

    def test_context_scope_change_after_completion_is_rejected(self):
        for mutation in ('hud.address=55', 'state.address=44', 'pawn.address=33', 'controller.address=22', 'world.address=11'):
            with self.subTest(mutation=mutation):
                self.setUp()
                self.run_lua('install();load();'+mutation+';assert(bridge.storage_context(token).status=="not_ready")')

    def test_account_identity_is_native_account_scoped_and_rechecked(self):
        self.run_lua('''
        install();load();local first=bridge.storage_context(token);assert(first.status=="ready")
        account_id="76561198000000002"
        local second=bridge.storage_context(token)
        assert(second.status=="not_ready" and lifecycle.consume().invalidated)
        account_id=first.accountId
        assert(bridge.storage_context(token).status=="not_ready")
        account_id="76561198000000002";load()
        second=bridge.storage_context(token)
        assert(second.status=="ready" and second.accountId~=first.accountId and second.libraryPath~=first.libraryPath)
        local counter=0
        account_callback=function() counter=counter+1;return counter%2==0 and "76561198000000003" or "76561198000000004" end
        assert(bridge.storage_context(token).status=="not_ready")
        ''')

    def test_unknown_account_and_nonlocal_controller_are_not_guessed(self):
        for setup in ('account_id=""', 'account_id="LocalSteamUser"', 'account_id="765123"',
                      'account_id="7656119800000000x"', 'account_id="76561198000000001/../../other"',
                      'player.PlayerController=pawn', 'controller.Player=nil', 'account.valid=false'):
            with self.subTest(setup=setup):
                self.setUp()
                self.run_lua('install();load();'+setup+';assert(bridge.storage_context(token).status=="not_ready")')

    def test_new_load_during_account_resolution_invalidates_same_token(self):
        self.run_lua('''
        install();load();local original=bridge.storage_context(token)
        local once=false
        account_callback=function()
            if not once then once=true;hooks.pre() end
            return account_id
        end
        assert(bridge.storage_context(token).status=="not_ready")
        account_callback=nil
        hooks.actor(wrap(hud));hooks.post(nil,wrap(world),nil,nil,"")
        local restored=bridge.storage_context(token)
        assert(restored.status=="ready" and restored.scopeId==original.scopeId)
        assert(restored.loadGeneration==original.loadGeneration+1)
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_native_root_change_between_account_reads_is_rejected(self):
        self.run_lua('''
        install();load();local counter=0
        function account:GetSaveGameRootDirectory()
            counter=counter+1
            return counter%2==0 and "D:/Other/Saved/SaveGames" or "C:/Game/Saved/SaveGames"
        end
        assert(bridge.storage_context(token).status=="not_ready")
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_native_root_switch_after_account_binding_requires_new_load(self):
        self.run_lua('''
        install();load();assert(bridge.storage_context(token).status=="ready")
        function account:GetSaveGameRootDirectory() return "D:/Other/Saved/SaveGames" end
        assert(bridge.storage_context(token).status=="not_ready")
        assert(lifecycle.consume().invalidated)
        assert(bridge.storage_context(token).status=="not_ready")
        load();local current=bridge.storage_context(token)
        assert(current.status=="ready" and current.libraryPath:sub(1,8)=="D:/Other")
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_account_switch_before_first_reconciliation_is_not_imported(self):
        self.run_lua('''
        install();load()
        local event=lifecycle.consume()
        assert(event.status=="ready" and event.accountId==account_id)
        account_id="76561198000000002"
        assert(bridge.storage_context(token).status=="not_ready")
        assert(lifecycle.consume().invalidated and native_reads==0 and native_writes==0)
        load();local restored=bridge.storage_context(token)
        assert(restored.status=="ready" and restored.accountId==account_id)
        ''')

    def test_consumed_event_cannot_change_private_restored_account_binding(self):
        self.run_lua('''
        install();load();local event=lifecycle.consume()
        event.accountId="76561198000000002";event.libraryPath="other-account.json"
        local original=bridge.storage_context(token)
        assert(original.status=="ready" and original.accountId==account_id)
        account_id="76561198000000002"
        assert(bridge.storage_context(token).status=="not_ready")
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_account_unavailable_at_map_post_does_not_get_late_admission(self):
        self.run_lua('''
        install();account_id="LocalSteamUser";load()
        assert(lifecycle.consume().invalidated)
        account_id="76561198000000001"
        assert(bridge.storage_context(token).status=="not_ready")
        load();assert(bridge.storage_context(token).status=="ready")
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_scope_change_in_final_account_getter_is_rejected(self):
        self.run_lua('''
        install();load();local calls=0
        function account:GetSaveGameRootDirectory()
            calls=calls+1;if calls==2 then hud.address=55 end
            return "C:/Game/Saved/SaveGames"
        end
        assert(bridge.storage_context(token).status=="not_ready")
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_post_event_replaces_pre_notice_but_retains_new_generation(self):
        self.run_lua('''
        install();load();local original=lifecycle.consume()
        -- Native LoadMap is synchronous: the scheduler normally cannot consume
        -- the invalidation notice until the post callback has replaced it.
        hooks.pre();hooks.actor(wrap(hud));hooks.post(nil,wrap(world),nil,nil,"")
        local current=lifecycle.consume()
        assert(current.status=="ready" and not current.invalidated)
        assert(current.contextToken==original.contextToken and current.scopeId==original.scopeId)
        assert(current.loadGeneration==original.loadGeneration+1)
        ''')

    def test_native_uuid_is_normalized_without_random_or_save_calls(self):
        self.run_lua('''
        local value,err=bridge.new_id(token)
        assert(value=="aabbccdd-0011-4222-8333-0123456789ab",err)
        assert(guid_calls==1 and native_reads==0 and native_writes==0)
        guid_text="AABBCCDD-0011-4222-8333-0123456789AB"
        assert(bridge.new_id(token)==value)
        ''')

    def test_uuid_invalid_or_changed_context_is_explicit_failure(self):
        for value in ('', 'x'*32, '0'*32, '1234'):
            with self.subTest(value=value):
                self.setUp()
                self.lua.globals().guid_text = value
                self.run_lua('local value,err=bridge.new_id(token);assert(value==nil and type(err)=="string")')

    def test_uuid_context_change_during_native_call_is_rejected(self):
        self.run_lua('''
        local convert=guidlib.Conv_GuidToString
        function guidlib:Conv_GuidToString(value)
            local result=convert(self,value);hud.address=55;return result
        end
        local value,err=bridge.new_id(token)
        assert(value==nil and type(err)=="string" and native_writes==0)
        ''')

    def test_late_pawn_attaches_only_to_witnessed_restored_base(self):
        self.run_lua('''
        install();controller.Pawn=nil;load()
        assert(lifecycle.consume().invalidated)
        assert(lifecycle.consume()==nil and bridge.storage_context(token).status=="not_ready")
        controller.Pawn=pawn
        local event=lifecycle.consume()
        assert(event.status=="ready" and event.contextToken==token and event.loadGeneration==1)
        assert(lifecycle.consume()==nil and bridge.storage_context(token).status=="ready")
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_pending_attachment_respects_budget_and_charges_one_bounded_unit(self):
        self.run_lua('''
        install();controller.Pawn=nil;load();assert(lifecycle.consume().invalidated)
        local b={units=0,limit=0}
        function b:expired() return self.units>=self.limit end
        function b:consume() self.units=self.units+1 end
        controller.Pawn=pawn
        assert(lifecycle.consume(b)==nil and b.units==0)
        b.limit=1;local event=lifecycle.consume(b)
        assert(event.status=="ready" and b.units==1)
        assert(lifecycle.consume(b)==nil and b.units==1)
        ''')

    def test_missing_player_state_at_hud_copy_source_cannot_be_repaired_later(self):
        self.run_lua('''
        install();hooks.pre();controller.PlayerState=nil
        hooks.actor(wrap(hud))
        controller.PlayerState=state
        hooks.post(nil,wrap(world),nil,nil,"")
        local event=lifecycle.consume();assert(event.invalidated and event.error:find("PlayerState"))
        assert(lifecycle.consume()==nil and bridge.storage_context(token).status=="not_ready")
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_hud_beginplay_uses_observed_owner_before_controller_hud_assignment(self):
        self.run_lua('''
        install();hooks.pre()
        local original=controller.GetHUD
        function controller:GetHUD() return nil end
        hooks.actor(wrap(hud))
        controller.GetHUD=original
        hooks.post(nil,wrap(world),nil,nil,"")
        assert(lifecycle.consume().status=="ready" and bridge.storage_context(token).status=="ready")
        ''')

    def test_changed_base_before_post_or_late_attachment_never_admits(self):
        for phase in ("post", "attach"):
            with self.subTest(phase=phase):
                self.setUp()
                self.run_lua('install();controller.Pawn=nil;hooks.pre();hooks.actor(wrap(hud))')
                if phase=="post": self.run_lua('state.address=44')
                self.run_lua('hooks.post(nil,wrap(world),nil,nil,"");assert(lifecycle.consume().invalidated)')
                if phase=="attach": self.run_lua('state.address=44')
                self.run_lua('controller.Pawn=pawn;local event=lifecycle.consume();assert(event==nil or event.invalidated);assert(bridge.storage_context(token).status=="not_ready")')

    def test_account_change_while_waiting_for_pawn_cannot_admit_old_carrier(self):
        self.run_lua('''
        install();controller.Pawn=nil;load();assert(lifecycle.consume().invalidated)
        account_id="76561198000000002";controller.Pawn=pawn
        local event=lifecycle.consume()
        assert(event.invalidated and bridge.storage_context(token).status=="not_ready")
        assert(lifecycle.consume()==nil and native_reads==0 and native_writes==0)
        ''')

    def test_new_load_cancels_pending_attachment_and_requires_new_source_proof(self):
        self.run_lua('''
        install();controller.Pawn=nil;load();assert(lifecycle.consume().invalidated)
        hooks.pre();controller.Pawn=pawn
        assert(lifecycle.consume().invalidated and lifecycle.consume()==nil)
        assert(bridge.storage_context(token).status=="not_ready")
        hooks.actor(wrap(hud));hooks.post(nil,wrap(world),nil,nil,"")
        local event=lifecycle.consume();assert(event.status=="ready" and event.loadGeneration==2)
        ''')

    def test_new_load_during_post_account_capture_keeps_new_generation_invalidated(self):
        self.run_lua('''
        install();local once=false
        account_callback=function()
            if not once then once=true;hooks.pre() end
            return account_id
        end
        load();local event=lifecycle.consume()
        assert(event.invalidated and event.loadGeneration==2)
        assert(bridge.storage_context(token).status=="not_ready" and native_writes==0)
        account_callback=nil
        hooks.actor(wrap(hud));hooks.post(nil,wrap(world),nil,nil,"")
        assert(lifecycle.consume().status=="ready")
        ''')


if __name__ == "__main__":
    unittest.main()
