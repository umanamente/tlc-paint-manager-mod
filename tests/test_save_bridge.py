"""Native carrier contract: exact marshalling, scope/CAS guards and no retry."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"
FIXTURE = r'''
native_reads,native_writes,lookups,name_calls=0,0,0,0
function object(name,class,address)
    local value={name=name,class=class,address=address,valid=true}
    function value:IsValid() return self.valid end
    function value:IsA(path) return path==self.class end
    function value:GetFullName() return self.name end
    function value:GetAddress() return self.address end
    function value:GetWorld() return world end
    return value
end
world=object("World /World", "/Script/Engine.World",1)
controller=object("Controller /Controller", "/Script/Voyage.VoyagePlayerController",2)
pawn=object("Pawn /Pawn", "/Script/Voyage.VoyageCharacter",3)
state=object("State /State", "/Script/Voyage.VoyagePlayerState",4)
hud=object("HUD /HUD", "/Script/Voyage.VoyageHUD",5)
controller.Pawn=pawn;controller.PlayerState=state
function controller:GetHUD() return hud end
function controller:IsLocalController() return true end
function pawn:GetController() return controller end
function state:GetOwner() return controller end
function hud:GetOwningPlayerController() return controller end
library=object("Library /Library", "/Script/Voyage.VoyageBlueprintUIFunctionLibrary",6)
function FindFirstOf(class) assert(class=="VoyagePlayerController");return controller end
function StaticFindObject(path)
    lookups=lookups+1
    assert(path=="/Script/Voyage.Default__VoyageBlueprintUIFunctionLibrary")
    return library
end
EFindName={FNAME_Add=1}
local function construct_fname(name,mode)
    name_calls=name_calls+1
    assert(name=="PaintManager.State" and mode==EFindName.FNAME_Add)
    return {ToString=function() return name end}
end
FName=setmetatable({}, {__call=function(_,...) return construct_fname(...) end})
function library:GetPersistentHudData(pc,key,out)
    native_reads=native_reads+1
    assert(pc==controller and key:ToString()=="PaintManager.State" and type(out)=="table" and next(out)==nil)
    if read_callback then return read_callback(out) end
    if raw==nil then return false end
    out.IntValue=0;out.FloatValue=0;out.bIsString=true
    out.StringValue={ToString=function() return raw end}
    return true
end
function library:SetPersistentHudData(pc,key,input)
    native_writes=native_writes+1
    assert(pc==controller and key:ToString()=="PaintManager.State")
    assert(input.IntValue==0 and input.FloatValue==0 and input.bIsString==true and type(input.StringValue)=="string")
    local fields=0;for _ in pairs(input) do fields=fields+1 end;assert(fields==4)
    if write_callback then return write_callback(input) end
    raw=input.StringValue
    return true
end
token="World /World@1|Controller /Controller@2|Pawn /Pawn@3"
bridge=require("paintmanager.save_bridge")
'''


class SaveBridgeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)

    def run_lua(self, source):
        return self.lua.execute(source)

    def test_import_is_inert(self):
        self.assertEqual(self.lua.eval('native_reads+native_writes+lookups+name_calls'), 0)

    def test_callable_fname_class_supports_read_and_write(self):
        self.run_lua('''
        local construct=FName
        FName=setmetatable({}, {__call=function(_,...) return construct(...) end})
        assert(type(FName)=="table")
        local loaded=bridge.read(token)
        assert(loaded.status=="missing",loaded.error)
        local written=bridge.write(token,nil,'{"revision":1}',loaded.scopeId)
        assert(written.status=="staged",written.error)
        assert(bridge.read(token).raw=='{"revision":1}' and native_writes==1)
        ''')

    def test_callable_userdata_constructor_supports_read_and_write(self):
        construct = self.lua.globals().FName
        self.lua.globals().FName = lambda name, mode: construct(name, mode)
        self.run_lua('''
        assert(type(FName)=="userdata")
        local loaded=bridge.read(token);assert(loaded.status=="missing",loaded.error)
        local result=bridge.write(token,nil,"{}",loaded.scopeId)
        assert(result.status=="staged",result.error)
        assert(native_writes==1 and bridge.read(token).raw=="{}")
        ''')

    def test_plain_function_constructor_remains_supported(self):
        self.run_lua('''
        local construct=FName
        FName=function(...) return construct(...) end
        assert(type(FName)=="function" and bridge.read(token).status=="missing")
        ''')

    def test_missing_key_requires_valid_hud_and_returns_scope(self):
        self.run_lua('local r=bridge.read(token);assert(r.status=="missing" and r.raw==nil and r.scopeId:find("State /State@4",1,true))')

    def test_direct_out_struct_fstring_is_copied_and_plain_strings_are_accepted(self):
        self.run_lua('''
        raw='{"value":1}'
        local r=bridge.read(token);assert(r.status=="present" and r.raw==raw)
        raw="new";assert(r.raw=='{"value":1}')
        read_callback=function(out) out.bIsString=true;out.StringValue="plain";return true end
        assert(bridge.read(token).raw=="plain")
        ''')

    def test_empty_string_is_present_instead_of_missing(self):
        self.run_lua('raw="";local r=bridge.read(token);assert(r.status=="present" and r.raw=="")')

    def test_invalid_native_values_never_become_missing(self):
        for body in ('return nil', 'return 0', 'return "false"',
                     'out.OutValue={bIsString=true,StringValue="bad nesting"};return true',
                     'out.bIsString=false;out.StringValue="x";return true',
                     'out.bIsString=1;out.StringValue="x";return true',
                     'out.bIsString=true;out.StringValue=3;return true',
                     'out.bIsString=true;out.StringValue={ToString=function() return false end};return true',
                     'error("read failed")'):
            with self.subTest(body=body):
                self.setUp()
                self.run_lua('read_callback=function(out) '+body+' end;assert(bridge.read(token).status=="error")')

    def test_native_and_requested_byte_limits_and_utf8_are_enforced(self):
        self.run_lua('''
        raw=string.rep("a",32768);assert(bridge.read(token).status=="present")
        assert(bridge.read(token,32767).status=="error")
        raw=raw.."a";assert(bridge.read(token,999999).status=="error")
        raw=string.char(255);assert(bridge.read(token).status=="error")
        raw="a\0b";assert(bridge.read(token).status=="error")
        ''')

    def test_invalid_limits_fail_before_native_reads(self):
        self.run_lua('''
        for _,limit in ipairs({0,-1,1.5,"100",false,0/0,math.huge}) do
            assert(bridge.read(token,limit).status=="error")
        end
        assert(native_reads==0)
        ''')

    def test_absent_or_wrong_objects_fail_before_lookup(self):
        for setup in ('controller.valid=false', 'controller.class="Other"', 'controller.Pawn=nil',
                      'controller.IsLocalController=function() return false end',
                      'controller.IsLocalController=function() return 1 end',
                      'pawn.class="/Script/Voyage.VoyageBoat"', 'controller.PlayerState=nil',
                      'state.class="Other"', 'hud=nil', 'hud.class="Other"',
                      'pawn.GetController=function() return pawn end',
                      'state.GetOwner=function() return pawn end',
                      'hud.GetOwningPlayerController=function() return pawn end',
                      'pawn.GetWorld=function() return pawn end',
                      'state.GetWorld=function() return state end',
                      'hud.GetWorld=function() return hud end'):
            with self.subTest(setup=setup):
                self.setUp()
                self.run_lua(setup+';assert(bridge.read(token).status=="error" and native_reads==0)')

    def test_missing_or_changed_expected_context_is_rejected(self):
        self.run_lua('''
        assert(bridge.read(nil).status=="error")
        assert(bridge.read("old context").status=="error")
        assert(bridge.write("old context",nil,"{}").status=="error")
        assert(native_reads==0 and native_writes==0)
        ''')

    def test_library_and_key_failures_are_explicit(self):
        for setup in ('library.valid=false', 'library.class="Other"', 'FName=nil',
                      'FName={}', 'FName=false', 'FName=function() error("constructor failed") end',
                      'EFindName=nil', 'FName=function() return {ToString=function() return "None" end} end'):
            with self.subTest(setup=setup):
                self.setUp()
                self.run_lua(setup+';assert(bridge.read(token).status=="error" and native_writes==0)')

    def test_scope_change_during_read_rejects_old_value(self):
        for body in ('hud.address=50', 'state.address=40', 'controller.address=20', 'pawn.address=30'):
            with self.subTest(body=body):
                self.setUp()
                self.run_lua('read_callback=function(out) '+body+';return false end;assert(bridge.read(token).status=="error")')

    def test_cas_stages_exact_native_string_with_readback_and_allows_next_change(self):
        self.run_lua('''
        local scope=bridge.read(token).scopeId
        local r=bridge.write(token,nil,'{"revision":1}',scope)
        assert(r.status=="staged" and raw=='{"revision":1}' and native_writes==1)
        assert(bridge.write(token,raw,'{"revision":2}',r.scopeId).status=="staged")
        assert(native_writes==2 and bridge.read(token).raw=='{"revision":2}')
        ''')

    def test_changed_raw_or_expected_scope_is_a_nonwriting_conflict(self):
        self.run_lua('''
        local scope=bridge.read(token).scopeId
        raw="external"
        assert(bridge.write(token,nil,"{}",scope).status=="conflict")
        assert(bridge.write(token,"old","{}",scope).status=="conflict")
        state.address=44
        assert(bridge.write(token,"external","{}",scope).status=="conflict")
        assert(native_writes==0)
        ''')

    def test_scope_change_during_cas_blocks_setter(self):
        self.run_lua('''
        read_callback=function(out) hud.address=55;return false end
        assert(bridge.write(token,nil,"{}").status=="error" and native_writes==0)
        ''')

    def test_invalid_document_or_old_raw_never_calls_native_setter(self):
        self.run_lua('''
        for _,value in ipairs({false,1,"",string.rep("x",32769),string.char(255),"a\0b"}) do
            assert(bridge.write(token,nil,value).status=="error")
        end
        assert(bridge.write(token,false,"{}").status=="error")
        assert(native_writes==0)
        ''')

    def test_setter_false_exception_or_wrong_return_blocks_retries(self):
        for body in ('return false', 'error("setter exception")', 'return 1', 'return nil'):
            with self.subTest(body=body):
                self.setUp()
                self.run_lua('''
                write_callback=function(input) raw=input.StringValue;'''+body+''' end
                assert(bridge.write(token,nil,"{}").status=="uncertain")
                write_callback=nil
                assert(bridge.write(token,"{}","new").status=="uncertain")
                assert(native_writes==1 and bridge.read(token).status=="present")
                ''')

    def test_postwrite_scope_change_or_different_readback_is_uncertain(self):
        for body in ('raw="different"', 'raw=nil', 'raw=input.StringValue;hud.address=99',
                     'raw=input.StringValue;state.address=99',
                     'raw=input.StringValue;read_callback=function() error("readback failure") end'):
            with self.subTest(body=body):
                self.setUp()
                self.run_lua('''
                write_callback=function(input) '''+body+''';return true end
                assert(bridge.write(token,nil,"{}").status=="uncertain")
                assert(bridge.write(token,raw,"new").status=="uncertain" and native_writes==1)
                ''')

    def test_reentrant_notification_cannot_attempt_another_write(self):
        self.run_lua('''
        write_callback=function(input)
            assert(bridge.write(token,nil,"reentered").status=="uncertain")
            raw=input.StringValue;return true
        end
        assert(bridge.write(token,nil,"{}").status=="staged" and native_writes==1)
        ''')

    def test_new_runtime_context_has_its_own_attempt_guard(self):
        self.run_lua('''
        write_callback=function() error("uncertain") end
        assert(bridge.write(token,nil,"{}").status=="uncertain")
        world.address=9;token="World /World@9|Controller /Controller@2|Pawn /Pawn@3"
        write_callback=nil
        assert(bridge.write(token,nil,"{}").status=="staged" and native_writes==2)
        ''')

    def test_objects_and_fixed_fname_are_resolved_again_for_every_call(self):
        self.run_lua('''
        bridge.read(token);bridge.read(token)
        assert(lookups==2 and name_calls==2)
        library.valid=false
        assert(bridge.read(token).status=="error")
        ''')


if __name__ == "__main__":
    unittest.main()
