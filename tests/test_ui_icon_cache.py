"""Bounded startup preparation and retained native keeper/cache contracts.

These tests execute the Lua icon cache with a native ownership model; they do
not qualify shipping texture decoding, real GC, or frame-time performance.
"""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"


FIXTURE = r'''
package.path=scripts_path.."/?.lua;"..package.path
function FText(value) return value end
imports,brushes,created=0,0,0
textures={}
function make(kind)
    created=created+1
    local widget={kind=kind,children={},Font={},alive=true}
    function widget:IsValid() return self.alive end
    function widget:IsA(class) return self.kind=="Texture2D" and class=="/Script/Engine.Texture2D" end
    function widget:SetText(value) self.text=value end
    function widget:SetAutoWrapText(value) self.wrap=value end
    function widget:SetJustification(value) self.justification=value end
    function widget:SetFont(value) self.Font=value end
    function widget:SetVisibility(value) self.visibility=value end
    function widget:AddChild(child) self.children[#self.children+1]=child;child.parent=self end
    function widget:SetBrushFromTexture(texture)
        assert(texture.alive)
        if failBrush then error("Brush failed") end
        brushes=brushes+1;self.texture=texture
    end
    return widget
end
tree=make("WidgetTree")
library=make("KismetRenderingLibrary")
function library:ImportBufferAsTexture2D(context,bytes)
    assert(context==tree and bytes[1]==137 and bytes[2]==80 and bytes[3]==78 and bytes[4]==71)
    imports=imports+1
    if failImport then return nil end
    local texture=make("Texture2D")
    textures[#textures+1]=texture
    return texture
end
function StaticFindObject(path)
    assert(path=="/Script/Engine.Default__KismetRenderingLibrary")
    return library
end
icons=require("paintmanager.ui_icons").new(tree,{},make)
function budget(units)
    return {used=0,expired=function(self) return self.used>=units end,
        consume=function(self,count)
            self.used=self.used+count
            -- A successful native import is already retained before the first
            -- scheduler yield/consume following it.
            for _,texture in ipairs(textures) do
                if not failBrush then
                    local found=false
                    for _,keeper in ipairs(icons:host().children) do
                        if keeper.texture==texture then found=true end
                    end
                    assert(found)
                end
            end
        end}
end
function warm()
    assert(icons:preload_all())
    local callbacks=0
    while icons:has_work() do
        local b=budget(8)
        local used=icons:pump(b)
        assert(used==b.used and used>0 and used<=8)
        callbacks=callbacks+1;assert(callbacks<1000)
    end
    return callbacks
end
'''


class UiIconCacheTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute(FIXTURE)

    def test_preload_queues_without_native_work_and_warms_all_once(self):
        self.lua.execute('''
        local before=created
        assert(icons:preload_all())
        assert(created==before and imports==0 and #icons.order==19)
        assert(#icons:host().children==0 and not icons:is_ready())
        local units=0
        for _,entry in ipairs(icons.order) do units=units+2+math.ceil(#entry.hex/512) end
        assert(warm()<=math.ceil(units/8)+1)
        assert(icons:is_ready() and imports==19 and #icons:host().children==19)
        assert(brushes==19 and not icons:has_work())
        icons:preload_all();assert(icons:pump(budget(8))==0 and imports==19)
        ''')

    def test_shared_budget_bounds_units_and_decodes_only_a_fixed_slice(self):
        self.lua.execute('''
        icons:image("refresh")
        local entry=icons.entries.refresh
        assert(icons:pump(budget(0))==0 and entry.phase=="keeper")
        assert(icons:pump(budget(1))==1 and entry.phase=="decode")
        local cursor=entry.cursor
        assert(icons:pump(budget(1))==1)
        assert(entry.cursor-cursor<=512 and #entry.bytes<=256 and imports==0)
        local used=icons:pump(budget(2));assert(used<=2)
        ''')

    def test_nonexpiring_or_missing_budget_still_has_an_operation_cap(self):
        self.lua.execute('''
        icons:preload_all()
        local b={used=0,expired=function() return false end,
            consume=function(self,count) self.used=self.used+count end}
        assert(icons:pump(b)==8 and b.used==8)
        assert(icons:pump(nil)==8)
        ''')

    def test_loading_does_not_flash_text_and_ready_hits_use_one_widget(self):
        self.lua.execute('''
        local pending=icons:image("star")
        assert(pending.kind=="Overlay" and pending.children[1].visibility==1)
        assert(pending.children[2].visibility==1 and imports==0)
        warm()
        assert(pending.children[1].texture==icons.entries.star.texture)
        assert(pending.children[1].visibility==4 and pending.children[2].visibility==1)
        local before=created
        local ready=icons:image("star")
        assert(ready.kind=="Image" and created-before==1)
        assert(ready.texture==icons.entries.star.texture and ready.visibility==4)
        assert(#icons.entries.star.targets==0 and not icons:has_work() and imports==19)
        ''')

    def test_window_target_release_preserves_warm_cache_and_reopen_is_immediate(self):
        self.lua.execute('''
        local old=icons:image("close")
        icons:pump(budget(1))
        icons:release_targets();old.alive=false
        assert(#icons.entries.close.targets==0 and not icons.closed)
        warm()
        local texture=icons.entries.close.texture
        local image=icons:image("close")
        assert(image.kind=="Image" and image.texture==texture)
        local imported=imports
        icons:release_targets();image.alive=false
        local reopened=icons:image("close")
        assert(reopened.texture==texture and imports==imported and icons:is_ready())
        ''')

    def test_failed_import_only_then_shows_symbol_and_stops_retrying(self):
        self.lua.execute('''
        failImport=true
        local image=icons:image("close")
        assert(image.children[2].visibility==1)
        while icons:has_work() do icons:pump(budget(8)) end
        assert(imports==1 and image.children[1].visibility==1)
        assert(image.children[2].visibility==4 and not icons:is_ready())
        local next_image=icons:image("close")
        assert(next_image.children[2].visibility==4 and imports==1)
        assert(icons:image("unknown_id")==nil)
        ''')

    def test_scope_close_discards_cache_and_rejects_future_work(self):
        self.lua.execute('''
        warm();local imported=imports
        icons:close()
        assert(not icons:preload_all() and not icons:has_work() and not icons:is_ready())
        assert(icons:pump(budget(8))==0 and icons:image("close")==nil)
        assert(next(icons.entries)==nil and imports==imported and icons:host()==nil)
        ''')


if __name__ == "__main__":
    unittest.main()
