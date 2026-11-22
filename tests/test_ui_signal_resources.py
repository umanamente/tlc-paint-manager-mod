"""Lazy illustration demand, bounded decoding, and native brush ownership."""
import hashlib
import struct
import unittest
import zlib

from lupa.lua54 import LuaRuntime

from test_ui_icon_cache import FIXTURE, SCRIPTS


def png_fixture():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", 200, 100, 8, 6, 0, 0, 0))
    pixels = bytes(100 * (200 * 4 + 1))
    compressed = chunk(b"IDAT", zlib.compress(pixels))
    end = chunk(b"IEND", b"")
    padding = 65536 - 8 - len(header) - len(compressed) - len(end) - 12
    return b"\x89PNG\r\n\x1a\n" + header + chunk(b"pmWt", bytes(padding)) + compressed + end


PNG = png_fixture()


class UiSignalResourceTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        globals_ = self.lua.globals()
        globals_.scripts_path = SCRIPTS.as_posix()
        globals_.signal_hex = PNG.hex()
        globals_.signal_bytes = len(PNG)
        globals_.signal_digest = hashlib.sha256(PNG).hexdigest()
        self.lua.execute(r'''
        package.path=scripts_path.."/?.lua;"..package.path
        signal_loads=0
        signal_payload={hex=signal_hex,bytes=signal_bytes,width=200,height=100,sha256=signal_digest}
        package.preload["paintmanager.ui_signal_data"]=function()
            signal_loads=signal_loads+1
            return signal_payload
        end
        ''')
        self.lua.execute(FIXTURE)
        self.lua.execute(r'''
        signal_imports=0
        local original_import=library.ImportBufferAsTexture2D
        function library:ImportBufferAsTexture2D(context,bytes)
            local large=#bytes>32768
            if large then
                signal_imports=signal_imports+1
                assert(#bytes==signal_bytes,"Incomplete large PNG reached importer")
                assert(icons:is_ready(),"Illustration import delayed baseline controls")
            end
            local texture=original_import(self,context,bytes)
            if texture then texture.illustration=large end
            return texture
        end
        function drain_art()
            for _=1,10000 do
                if not icons:has_work() then return end
                local b=budget(8);local used=icons:pump(b)
                assert(used==b.used and used>0 and used<=8)
            end
            error("illustration work stalled")
        end
        function signal_image()
            local image=make("Image");image:SetVisibility(1)
            return image
        end
        ''')

    def run_lua(self, code):
        self.lua.execute(code)

    def test_startup_does_not_require_decode_or_import_the_illustration(self):
        self.run_lua(r'''
        assert(signal_loads==0 and icons:illustration_status("signal_lost")=="unrequested")
        warm()
        assert(signal_loads==0 and imports==19 and signal_imports==0 and #icons.order==19)
        assert(icons.entries.signal_lost==nil)
        ''')

    def test_first_demand_only_registers_then_decodes_bounded_slices_after_controls(self):
        self.run_lua(r'''
        local image=signal_image();local before_created=created
        local ready,target=icons:bind_illustration(image,"signal_lost")
        assert(not ready and target and signal_loads==1)
        assert(created==before_created and imports==0 and brushes==0 and image.visibility==1)
        local entry=icons.entries.signal_lost
        assert(entry.phase=="keeper" and entry.cursor==1 and #entry.bytes==0)
        assert(icons:pump(budget(0))==0)
        while not icons:is_ready() do
            icons:pump(budget(1))
            assert(entry.phase=="keeper" and signal_imports==0)
        end
        while entry.phase=="keeper" do icons:pump(budget(1))end
        assert(entry.phase=="decode" and #entry.bytes==0)
        local cursor=entry.cursor
        icons:pump(budget(1))
        assert(entry.cursor-cursor<=2048 and #entry.bytes<=1024 and signal_imports==0)
        drain_art()
        assert(signal_imports==1 and image.texture==entry.texture and image.visibility==4)
        assert(entry.bytes==nil and icons:illustration_status("signal_lost")=="ready")
        ''')

    def test_ready_texture_is_owned_before_yield_and_reused_across_window_release(self):
        self.run_lua(r'''
        warm()
        local image=signal_image()
        local _,target=icons:bind_illustration(image,"signal_lost")
        drain_art()
        local texture=image.texture;local entry=icons.entries.signal_lost
        assert(entry.keeper.texture==texture and entry.keeper.parent==icons:host())
        local original_imports=imports
        icons:release_targets();image.alive=false
        assert(target.active==false and target.image==nil and #icons.illustration_targets==0)
        assert(entry.texture==texture and entry.keeper.texture==texture and not icons:has_work())
        local reopened=signal_image()
        local ready,next_target=icons:bind_illustration(reopened,"signal_lost")
        assert(ready and next_target and reopened.texture==texture and reopened.visibility==4)
        assert(imports==original_imports and signal_loads==1 and #icons.order==20)
        ''')

    def test_pending_window_targets_are_never_touched_after_release(self):
        self.run_lua(r'''
        warm();local image=signal_image()
        local _,target=icons:bind_illustration(image,"signal_lost")
        icons:pump(budget(2))
        icons:release_targets();image.alive=false
        function image:SetBrushFromTexture()error("Retired window image touched")end
        function image:SetVisibility()error("Retired window image touched")end
        drain_art()
        assert(target.image==nil and signal_imports==1 and icons:illustration_status("signal_lost")=="ready")
        ''')

    def test_map_close_drops_pending_and_ready_target_wrappers_without_native_calls(self):
        self.run_lua(r'''
        warm();local image=signal_image()
        local _,target=icons:bind_illustration(image,"signal_lost");drain_art()
        function image:SetVisibility()error("Old map image touched")end
        function image:SetBrushFromTexture()error("Old map image touched")end
        local before=imports
        icons:close()
        assert(target.image==nil and target.bound_texture==nil and target.active==false)
        assert(#icons.illustration_targets==0 and next(icons.entries)==nil and not icons:has_work())
        assert(icons:pump(budget(8))==0 and imports==before)
        local ready,t=icons:bind_illustration(image,"signal_lost");assert(not ready and t==nil)
        ''')

    def test_failed_import_keeps_text_fallback_and_never_retries_in_same_resource_scope(self):
        self.run_lua(r'''
        warm();failImport=true
        local image,fallback=signal_image(),make("TextBlock");fallback:SetVisibility(4)
        icons:bind_illustration(image,"signal_lost",fallback);drain_art()
        assert(image.visibility==1 and fallback.visibility==4 and signal_imports==1)
        assert(icons:illustration_status("signal_lost")=="failed" and icons:is_ready())
        local ready,target=icons:bind_illustration(signal_image(),"signal_lost")
        assert(not ready and target==nil and signal_imports==1 and signal_loads==1 and not icons:has_work())
        ''')

    def test_invalid_metadata_is_isolated_from_existing_control_icon_limits(self):
        changes = (
            "signal_payload.bytes=1048577",
            "signal_payload.width=201",
            "signal_payload.height=99",
            "signal_payload.sha256='bad'",
            "signal_payload.hex='00'..signal_payload.hex:sub(3)",
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.run_lua(r'''
                warm()
                ''' + change + r'''
                local ready,target,err=icons:bind_illustration(signal_image(),"signal_lost")
                assert(not ready and target==nil and type(err)=="string")
                assert(icons:illustration_status("signal_lost")=="failed" and imports==19 and icons:is_ready())
                assert(not icons:has_work())
                ''')

    def test_malformed_body_hex_is_rejected_incrementally_before_native_import(self):
        self.run_lua(r'''
        warm()
        signal_payload.hex=signal_payload.hex:sub(1,100).." z"..signal_payload.hex:sub(103)
        local image=signal_image()
        icons:bind_illustration(image,"signal_lost");drain_art()
        assert(signal_imports==0 and imports==19 and icons:illustration_status("signal_lost")=="failed")
        assert(image.visibility==1)
        ''')

    def test_repeated_registration_is_bounded_and_optional_art_cannot_be_a_control_icon(self):
        self.run_lua(r'''
        warm();local image=signal_image()
        local _,first=icons:bind_illustration(image,"signal_lost")
        local _,second=icons:bind_illustration(image,"signal_lost")
        assert(first==second and #icons.illustration_targets==1 and #icons.entries.signal_lost.targets==1)
        assert(icons:image("signal_lost")==nil)
        local ready,target=icons:bind_illustration(image,"unapproved");assert(not ready and target==nil)
        for _=2,16 do local _,t=icons:bind_illustration(signal_image(),"signal_lost");assert(t)end
        ready,target=icons:bind_illustration(signal_image(),"signal_lost")
        assert(not ready and target==nil and #icons.illustration_targets==16)
        ''')

    def test_real_resource_host_retains_full_approved_art_and_retires_it_on_map_invalidation(self):
        from test_ui_resources import FIXTURE as RESOURCE_FIXTURE

        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().scripts_path = SCRIPTS.as_posix()
        lua.execute(RESOURCE_FIXTURE)
        lua.execute(r'''
        package.loaded["paintmanager.ui_resources"]=nil
        package.loaded["paintmanager.ui_icons"]=nil
        package.preload["paintmanager.ui_icons"]=nil
        local ordinary_find=StaticFindObject
        local rendering=object("RenderingLibrary",false)
        signal_imports=0
        function rendering:ImportBufferAsTexture2D(context,bytes)
            assert(context.kind=="Tree" and context.valid and bytes[1]==137 and bytes[2]==80)
            imports=imports+1
            if #bytes>32768 then
                assert(#bytes==118144)
                signal_imports=signal_imports+1
            end
            return object("Texture2D",true)
        end
        function StaticFindObject(path)
            if path=="/Script/Engine.Default__KismetRenderingLibrary"then return rendering end
            return ordinary_find(path)
        end
        resources=require("paintmanager.ui_resources").new({pump_ms=16})
        function ready_controls()
            for _=1,2000 do
                resources:tick(budget())
                local service=resources:get()
                if service and service:is_ready()then return service end
            end
            error("control resources stalled")
        end
        local service=ready_controls()
        assert(imports==19 and package.loaded["paintmanager.ui_signal_data"]==nil)
        local image=object("Image",true);image:SetVisibility(1)
        local ready,target=service:bind_illustration(image,"signal_lost")
        assert(not ready and target and imports==19)
        for _=1,2000 do
            resources:tick(budget())
            if service:illustration_status("signal_lost")=="ready" and image.texture then break end
        end
        local texture=assert(image.texture)
        assert(signal_imports==1 and imports==20 and service.entries.signal_lost.keeper.texture==texture)
        service:release_targets();collect_native()
        assert(not image.valid and texture.valid and resources.host.valid)
        local second=object("Image",true)
        assert(service:bind_illustration(second,"signal_lost"))
        assert(second.texture==texture and imports==20)
        resources:invalidate(true);collect_native()
        assert(not texture.valid and service.closed and target.image==nil)
        resources:resume();local next_service=ready_controls()
        assert(next_service~=service and imports==39 and signal_imports==1)
        assert(next_service:illustration_status("signal_lost")=="unrequested")
        ''')


if __name__ == "__main__":
    unittest.main()
