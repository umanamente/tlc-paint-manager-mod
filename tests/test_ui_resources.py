"""Independent native ownership model for the startup control-icon cache.

Viewport UserWidgets root native trees; Lua wrappers and Outer alone do not.
These fixtures exercise lifecycle/cleanup and do not render real Slate.
"""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"

FIXTURE = r'''
package.path=scripts_path.."/?.lua;"..package.path
objects,viewport,hosts,logs={}, {}, {}, {}
created,imports,finds=0,0,0
function object(kind,collectable)
    local value={kind=kind,valid=true,children={},collectable=collectable,addr=#objects+1,touches=0}
    objects[#objects+1]=value
    local function touch(self)
        self.touches=self.touches+1
        assert(not self.forbid_touch,"Old-scope native object was touched")
    end
    function value:IsValid() touch(self);return self.valid end
    function value:GetAddress() touch(self);return self.addr end
    function value:GetFullName() touch(self);return self.fullname or self.kind end
    function value:GetOuter() touch(self);return self.outer end
    function value:GetSuperStruct() touch(self);return self.super end
    function value:IsA(path)
        touch(self)
        return self.kind=="HostClass" and path=="/Script/CoreUObject.Class"
            or self.kind=="CanvasPanel" and path=="/Script/UMG.CanvasPanel"
            or self.kind=="Texture2D" and path=="/Script/Engine.Texture2D"
    end
    function value:SetVisibility(state) touch(self);self.visibility=state end
    function value:AddChild(child)
        touch(self);self.children[#self.children+1]=child
        local slot=object("Slot",true);slot.Content=child;slot.Parent=self;child.slot=slot
        return slot
    end
    value.AddChildToCanvas=value.AddChild
    function value:SetBrushFromTexture(texture) touch(self);self.texture=texture end
    function value:AddToViewport(z)
        touch(self);assert(z==9799 and self.visibility==1)
        viewport[self]=true
    end
    function value:IsInViewport() touch(self);return viewport[self]==true end
    function value:RemoveFromParent()
        touch(self);if fail_remove then error("remove failed") end
        viewport[self]=nil
    end
    return value
end
world=object("World",false)
pc=object("Controller",false)
function pc:IsLocalController() return local_controller~=false end
function pc:GetWorld() return world end
-- Startup warmup must work while no gameplay Pawn exists.
assert(pc.Pawn==nil)
class=object("HostClass",false)
class.fullname="WidgetBlueprintGeneratedClass /Game/UI/Game/HUD/BP_VoyageIngameEmpty.BP_VoyageIngameEmpty_C"
class.super=object("UserWidgetClass",false)
class.super.fullname="Class /Script/UMG.UserWidget"
input=object("Input",false)
function input:Create(context,cls,owner)
    assert(context==pc and owner==pc and cls==class)
    created=created+1
    local host=object("Host",true)
    local tree=object("Tree",true);tree.outer=host;host.WidgetTree=tree
    local root=object(wrong_root and "Border" or "CanvasPanel",true);root.outer=tree;tree.RootWidget=root
    host.default_root=root;hosts[#hosts+1]=host
    return host
end
function FindFirstOf(name)
    finds=finds+1;assert(name=="VoyagePlayerController")
    return available~=false and pc or nil
end
function StaticFindObject(path)
    if path=="/Script/UMG.Default__WidgetBlueprintLibrary" then return input end
    if path=="/Game/UI/Game/HUD/BP_VoyageIngameEmpty.BP_VoyageIngameEmpty_C" then return class end
    if path:find("^/Script/UMG%.") then return object("WidgetClass",false) end
end
function FName(value) return value end
EObjectFlags={RF_Transient=64}
function StaticConstructObject(cls,tree,name,flags)
    assert(tree.kind=="Tree" and flags==64)
    local kind=name:match("IconCache_(.-)_%d+$")
    local value=object(kind,true);value.outer=tree
    return value
end
-- Resource ownership is exercised independently from PNG decoding, which has
-- its own actual-module tests. The imported texture's native keeper is real
-- in this fixture's graph; Lua cache/Outer are deliberately ignored by GC.
package.preload["paintmanager.ui_icons"]=function()
    return {new=function(tree,config,make)
        local icons={bank=make("HorizontalBox"),tree=tree,make=make,working=false}
        icons.bank:SetVisibility(1)
        function icons:host() return self.bank end
        function icons:preload_all() self.working=true;self.preloaded=true;return true end
        function icons:has_work() return self.working and not self.closed end
        function icons:pump(budget)
            if self.closed or not self.working or budget and budget:expired() then return end
            imports=imports+1
            self.texture=object("Texture",true);self.texture.outer=self.tree
            self.keeper=self.make("Image");self.bank:AddChild(self.keeper)
            self.keeper:SetBrushFromTexture(self.texture)
            self.working=false
            if budget then budget:consume(1) end
        end
        function icons:close() self.closed=true;self.working=false end
        return icons
    end}
end
function budget(limit)
    return {used=0,consume=function(self,n) self.used=self.used+(n or 1) end,
        expired=function(self) return self.used>=(limit or 8) end}
end
function collect_native()
    local marked={}
    local function mark(value)
        if not value or marked[value] or not value.valid then return end
        marked[value]=true
        mark(value.WidgetTree);mark(value.RootWidget);mark(value.texture);mark(value.Content)
        for _,child in ipairs(value.children or {}) do mark(child) end
    end
    for host in pairs(viewport) do mark(host) end
    for _,value in ipairs(objects) do
        if value.collectable and not marked[value] then value.valid=false end
    end
end
resources=require("paintmanager.ui_resources").new({pump_ms=16},{log=function(message) logs[#logs+1]=message end})
function warm()
    for _=1,100 do
        resources:tick(budget())
        if resources:get() and resources:get().texture then return resources:get() end
    end
    error("warmup did not finish")
end
'''


class UIResourceTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute(FIXTURE)

    def test_stock_box_requests_only_its_icon_before_first_window_and_cancels_safely(self):
        self.lua.execute(r'''
        package.loaded["paintmanager.ui_resources"]=nil
        package.loaded["paintmanager.ui_icons"]=nil
        package.preload["paintmanager.ui_icons"]=nil
        local processing=require("paintmanager.processing")
        local get=processing.get
        processing.get=function(id,default)
            if id=="automatic_preparation" or id=="preload_icons" then return false end
            return get(id,default)
        end
        local find=StaticFindObject
        local library=object("RenderingLibrary",false)
        function library:ImportBufferAsTexture2D(tree,bytes)
            local expected=require("paintmanager.ui_icon_data").deposit_bucket
            assert(tree.kind=="Tree" and #bytes==#expected/2)
            for index,byte in ipairs(bytes) do
                assert(byte==tonumber(expected:sub(index*2-1,index*2),16))
            end
            imports=imports+1
            return object("Texture2D",true)
        end
        function StaticFindObject(path)
            if path=="/Script/Engine.Default__KismetRenderingLibrary" then return library end
            return find(path)
        end
        resources=require("paintmanager.ui_resources").new({pump_ms=16})
        local function tick()
            local b=budget(3)
            resources:tick(b,false,nil,false)
            assert(b.used<=3)
        end
        tick();assert(created==0 and imports==0)
        local retired=object("Image",false)
        local release=resources:bind_deposit_icon(retired)
        tick();assert(created==1 and imports==0)
        release();retired.forbid_touch=true
        local image=object("Image",false)
        release=resources:bind_deposit_icon(image)
        local steps=0
        while not image.texture do
            tick();steps=steps+1
            assert(steps<100)
        end
        local texture=image.texture
        assert(imports==1 and steps>2 and image.visibility==4)
        assert(not resources:processing_busy() and #resources.icons.order==1)
        release();image.forbid_touch=true
        local reopened=object("Image",false)
        resources:bind_deposit_icon(reopened)
        tick();assert(reopened.texture==texture and imports==1)
        resources:invalidate(true);reopened.forbid_touch=true
        resources:resume();tick();assert(not resources.deposit and imports==1)
        ''')

    def test_open_resource_retry_uses_elapsed_deadline_across_short_frames_and_drains(self):
        self.lua.execute('''
        local probes=0
        FindFirstOf=function() probes=probes+1 end
        resources:tick(budget(),true,1000,true)
        for _=1,80 do resources:tick(budget(),true,1000,true) end
        for t=1001,1499 do resources:tick(budget(),true,t,true) end
        assert(probes==1)
        resources:tick(budget(),true,1500,true);assert(probes==2)
        ''')

    def test_preloads_without_pawn_and_keeps_texture_alive_without_a_window(self):
        self.lua.execute('''
        local icons=warm()
        assert(pc.Pawn==nil and created==1 and imports==1 and icons.preloaded)
        local host=resources.host
        assert(host.visibility==1 and viewport[host] and host.WidgetTree.RootWidget==host.default_root)
        assert(#host.default_root.children==1 and host.default_root.children[1]==icons.bank)
        local texture=icons.texture
        collect_native()
        assert(host.valid and icons.bank.valid and texture.valid)
        -- No visible window is part of the resource host or its lifetime.
        for _=1,100 do resources:tick(budget()) end
        assert(resources:get()==icons and icons.texture==texture and imports==1 and created==1)
        ''')

    def test_map_invalidation_suspends_and_resume_recreates_one_owned_bank(self):
        self.lua.execute('''
        local old_icons=warm();local old_host=resources.host
        resources:invalidate(true)
        assert(old_icons.closed and not viewport[old_host] and resources:get()==nil)
        for _=1,100 do resources:tick(budget()) end
        assert(created==1 and imports==1)
        collect_native();assert(not old_host.valid and not old_icons.texture.valid)
        resources:resume();local icons=warm()
        assert(icons~=old_icons and created==2 and imports==2)
        collect_native();assert(icons.texture.valid)
        ''')

    def test_scope_change_does_not_touch_old_native_graph(self):
        self.lua.execute('''
        local old=warm();local host=resources.host
        host.forbid_touch=true;local touches=host.touches
        world=object("NewWorld",false)
        -- Force an ownership check even if the cache is already idle.
        assert(resources:get()==nil)
        assert(old.closed and host.touches==touches)
        local next_icons=warm()
        assert(next_icons~=old and created==2 and next_icons.texture.valid)
        ''')

    def test_failed_cleanup_is_retained_and_retried_before_replacement(self):
        self.lua.execute('''
        local old=warm();local host=resources.host
        fail_remove=true;resources:invalidate()
        assert(resources.cleanup_host==host and viewport[host] and old.closed)
        resources:tick(budget());assert(created==1 and resources:get()==nil)
        fail_remove=false;resources:tick(budget())
        assert(resources.cleanup_host==nil and not viewport[host] and created==2)
        assert(resources:get()~=old and #logs>=1)
        ''')

    def test_startup_probe_waits_for_native_services_and_respects_budget(self):
        self.lua.execute('''
        available=false
        resources:tick(budget(0));assert(finds==0 and created==0)
        resources:tick(budget());assert(finds==1 and created==0)
        for _=1,10 do resources:tick(budget()) end
        assert(finds==1 and created==0)
        available=true
        warm();assert(created==1 and imports==1)
        ''')

    def test_temporary_missing_controller_retains_cleanup_without_old_object_calls(self):
        self.lua.execute('''
        warm();local host=resources.host
        available=false;local touches=host.touches
        resources:invalidate()
        assert(resources.cleanup_host==host and viewport[host])
        resources:tick(budget())
        assert(resources.cleanup_host==host and host.touches==touches and created==1)
        available=true;resources:tick(budget())
        assert(resources.cleanup_host==nil and not viewport[host] and created==2)
        ''')

    def test_bad_native_root_cleans_partial_host_and_retries_later(self):
        self.lua.execute('''
        wrong_root=true;resources:tick(budget())
        assert(created==1 and resources.host==nil and resources:get()==nil)
        assert(not viewport[hosts[1]] and #logs==1)
        wrong_root=false
        warm();assert(created==2 and imports==1)
        ''')

    def test_actual_icon_module_preloads_all_textures_and_reuses_owned_bank(self):
        self.lua.execute('''
        package.loaded["paintmanager.ui_resources"]=nil
        package.loaded["paintmanager.ui_icons"]=nil
        package.preload["paintmanager.ui_icons"]=nil
        local original_find=StaticFindObject
        local library=object("RenderingLibrary",false)
        function library:ImportBufferAsTexture2D(context,bytes)
            assert(context.kind=="Tree" and context.valid)
            assert(bytes[1]==137 and bytes[2]==80 and bytes[3]==78 and bytes[4]==71)
            imports=imports+1
            return object("Texture2D",true)
        end
        function StaticFindObject(path)
            if path=="/Script/Engine.Default__KismetRenderingLibrary" then return library end
            return original_find(path)
        end
        resources=require("paintmanager.ui_resources").new({pump_ms=16})
        local icons
        for _=1,200 do
            resources:tick(budget())
            icons=resources:get()
            if icons and icons:is_ready() then break end
        end
        assert(icons and icons:is_ready() and imports==19 and created==1)
        collect_native()
        for _,entry in pairs(icons.entries) do
            assert(entry.texture.valid and entry.keeper.texture==entry.texture)
        end
        -- Ready images bind immediately; detaching a window's targets does
        -- not retire the independently owned keepers or import again.
        local image=icons:image("star")
        assert(image.kind=="Image" and image.texture==icons.entries.star.texture)
        icons:release_targets();collect_native()
        assert(icons.entries.star.texture.valid)
        image=icons:image("star")
        assert(image.texture==icons.entries.star.texture and imports==19)
        ''')

    def test_actual_window_close_reopen_keeps_preloaded_textures_through_gc(self):
        from test_ui import FIXTURE as UI_FIXTURE
        from test_ui_player import PLAYER_FIXTURE

        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().scripts_path = SCRIPTS.as_posix()
        lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        lua.execute(UI_FIXTURE)
        lua.execute(PLAYER_FIXTURE)
        lua.execute('''
        local ordinary_object=object
        native_viewport_hosts={};imports=0
        function object(kind)
            local value=ordinary_object(kind)
            if kind=="Host" then
                function value:AddToViewport(z)
                    assert(z==9799 or z==9800)
                    assert(self.WidgetTree.RootWidget.kind=="CanvasPanel")
                    self.slate_root=self.slate_root or self.WidgetTree.RootWidget
                    native_viewport_hosts[self]=true
                    if z==9800 then viewport.added=self end
                    event("add")
                end
                function value:IsInViewport() return native_viewport_hosts[self]==true end
                local ordinary_remove=value.RemoveFromParent
                function value:RemoveFromParent()
                    ordinary_remove(self)
                    native_viewport_hosts[self]=nil
                end
            end
            return value
        end
        local rendering=object("RenderingLibrary")
        function rendering:ImportBufferAsTexture2D(context,bytes)
            assert(context.kind=="WidgetTree" and bytes[1]==137 and bytes[2]==80)
            imports=imports+1
            local texture=object("Texture");texture.collectable=true;texture.outer=context
            return texture
        end
        local original_find=StaticFindObject
        function StaticFindObject(path)
            if path=="/Script/Engine.Default__KismetRenderingLibrary" then return rendering end
            return original_find(path)
        end
        function collect_native()
            local marked={}
            local function mark(o)
                if not o or marked[o] or not o.valid then return end
                marked[o]=true
                mark(o.WidgetTree);mark(o.RootWidget);mark(o.child);mark(o.Content)
                mark(o.Parent);mark(o.slot);mark(o.texture);mark(o.tooltip)
                for _,slot in ipairs(o.slots or {}) do mark(slot) end
            end
            for host in pairs(native_viewport_hosts) do mark(host) end
            for _,o in ipairs(all_objects) do
                if o.collectable and not marked[o] then o.valid=false end
            end
        end
        local resources=require("paintmanager.ui_resources").new({pump_ms=16})
        for _=1,200 do
            resources:tick(work_budget(8))
            if resources:get() and resources:get():is_ready() then break end
        end
        local icons=assert(resources:get())
        assert(icons:is_ready() and imports==19)
        local cache_host=resources.host
        ui.callbacks.icons=function() return resources:get() end
        open_player()
        assert(ui.icons==icons and ui.icons_shared and imports==19)
        local first_window=ui.host
        assert(first_window~=cache_host and native_viewport_hosts[first_window] and native_viewport_hosts[cache_host])
        collect_native()
        assert(first_window.valid and cache_host.valid and icons.entries.star.texture.valid)
        assert(ui:close("user"))
        collect_native()
        assert(not first_window.valid and cache_host.valid and icons.entries.star.texture.valid)
        assert(native_viewport_hosts[cache_host] and not icons.closed)
        assert(ui:toggle());drain_render()
        assert(ui.host~=first_window and ui.icons==icons and imports==19)
        collect_native()
        assert(ui.host.valid and cache_host.valid and icons.entries.star.texture.valid)
        ''')


if __name__ == "__main__":
    unittest.main()
