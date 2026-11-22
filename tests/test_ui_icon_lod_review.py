"""Independent icon LOD, native ownership, retirement and decode-budget checks.

The importer fixture validates ownership and scheduling, not shipping PNG
decoding or measured Unreal frame time.
"""
import unittest

from lupa.lua54 import LuaRuntime

import test_ui_icon_cache as icon_fixture


class IconLodReviewTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = icon_fixture.SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        # Variants use deterministic owned bytes here. The package tests check
        # the approved PNG/manifest/embedded twins independently.
        self.lua.execute('''
        local data=require("paintmanager.ui_icon_data")
        data.learned_detailed=data.learned
        data.ready_detailed=data.ready
        ''')
        self.lua.execute(icon_fixture.FIXTURE)
        self.lua.execute('''
        icon_module=require("paintmanager.ui_icons")
        function drain_icons()
            for _=1,10000 do
                if not icons:has_work() then return end
                local b=budget(8);local used=icons:pump(b)
                assert(used<=8 and b.used==used)
            end
            error("icon work stalled")
        end
        ''')

    def test_lod_requires_both_logical_and_device_pixel_thresholds(self):
        self.lua.execute('''
        local choose=icon_module.variant_id
        assert(choose("learned",18,10)=="learned")
        assert(choose("ready",31.999,10)=="ready")
        assert(choose("ready",32,1.499)=="ready")
        assert(choose("ready",32,1.5)=="ready_detailed")
        assert(choose("learned",48,1)=="learned_detailed")
        assert(choose("learn",32,1.5)=="ready_detailed")
        assert(choose("refresh",128,2)=="refresh")
        assert(choose("ready",nil,nil)=="ready")
        ''')

    def test_startup_preloads_only_base_icons_and_detailed_is_lazy(self):
        self.lua.execute('''
        assert(icons:preload_all());drain_icons()
        assert(imports==19 and #icons.order==19 and icons:is_ready())
        assert(icons.entries.learned_detailed==nil and icons.entries.ready_detailed==nil)
        icons:set_scale(1.481)
        local small=icons:image("learned",18)
        assert(small.texture==icons.entries.learned.texture and imports==19)
        icons:set_scale(1.5)
        local large,target=icons:image("learned",32)
        assert(icons.entries.learned_detailed and imports==19)
        drain_icons()
        assert(target.image.texture==icons.entries.learned_detailed.texture and imports==20)
        assert(icons.entries.ready_detailed==nil)
        ''')

    def test_resize_is_plain_and_rebinds_existing_images_under_pump_budget(self):
        self.lua.execute('''
        icons:preload_all();drain_icons()
        local image,target=icons:image("ready",32)
        local first=target.image;local before_created,before_imports,before_brushes=created,imports,brushes
        icons:set_scale(2)
        assert(created==before_created and imports==before_imports and brushes==before_brushes)
        drain_icons()
        assert(target.image==first and first.texture==icons.entries.ready_detailed.texture)
        local warmed_created,warmed_imports=created,imports
        icons.make=function() error("LOD swap constructed a widget") end
        icons:set_scale(1);drain_icons()
        assert(target.image==first and first.texture==icons.entries.ready.texture)
        assert(created==warmed_created and imports==warmed_imports)
        ''')

    def test_repeated_scale_and_semantic_changes_cannot_publish_old_variant(self):
        self.lua.execute('''
        icons:preload_all();drain_icons()
        local image,target=icons:image("ready",32)
        local original=target.image
        icons:set_scale(2);icons:pump(budget(1))
        assert(icons:rebind(target,"learned"))
        icons:set_scale(1);icons:set_scale(2)
        drain_icons()
        assert(target.image==original and original.texture==icons.entries.learned_detailed.texture)
        assert(not icons.entries.ready_detailed or original.texture~=icons.entries.ready_detailed.texture)
        ''')

    def test_retired_target_is_never_touched_by_pending_variant_or_resize(self):
        self.lua.execute('''
        icons:preload_all();drain_icons()
        local image,target=icons:image("ready",32)
        icons:set_scale(2);icons:pump(budget(1))
        local retired_image=target.image
        icons:retire(target)
        local touches=0
        function retired_image:SetBrushFromTexture() touches=touches+1;error("retired target was rebound") end
        function retired_image:SetVisibility() touches=touches+1;error("retired target visibility was changed") end
        icons:set_scale(1);icons:set_scale(2);drain_icons()
        assert(icons:is_ready() and touches==0)
        ''')

    def test_maximum_png_decodes_fixed_byte_slices_without_runtime_filesystem_work(self):
        self.lua.execute('''
        local data=require("paintmanager.ui_icon_data")
        data.learned_detailed="89504e470d0a1a0a"..string.rep("00",32768-8)
        io.open=function() error("icon loader accessed filesystem") end
        icons:set_scale(2)
        local image,target=icons:image("learned",32)
        local entry=assert(icons.entries.learned_detailed)
        for _=1,1000 do
            if entry.phase=="decode" then break end
            icons:pump(budget(1))
        end
        assert(entry.phase=="decode")
        while entry.phase=="decode" do
            local cursor,bytes=entry.cursor,#entry.bytes
            icons:pump(budget(1))
            assert(entry.cursor-cursor<=512 and #entry.bytes-bytes<=256)
        end
        assert(#entry.bytes==32768 and entry.phase=="import")
        drain_icons()
        assert(imports==1 and target.image.texture==entry.texture)
        ''')

    def test_failed_optional_detail_keeps_existing_compact_icon_visible(self):
        self.lua.execute('''
        icons:preload_all();drain_icons()
        local image,target=icons:image("ready",32)
        local compact=icons.entries.ready.texture
        assert(target.image.texture==compact and target.image.visibility==4)
        failImport=true;icons:set_scale(2);drain_icons()
        assert(icons.entries.ready_detailed.phase=="failed")
        assert(target.image.texture==compact and target.image.visibility==4)
        assert(compact.alive)
        ''')

    def test_window_target_release_ignores_late_detail_and_reuses_keeper_on_reopen(self):
        self.lua.execute('''
        icons:preload_all();drain_icons()
        local image,target=icons:image("learned",32)
        local old_image=target.image;local touched=0
        icons:set_scale(2);icons:pump(budget(1))
        icons:release_targets()
        function old_image:SetBrushFromTexture() touched=touched+1;error("closed window rebound") end
        function old_image:SetVisibility() touched=touched+1;error("closed window changed") end
        drain_icons();assert(touched==0 and #icons.lod_targets==0)
        local before=imports
        local reopened,new_target=icons:image("learned",32)
        assert(new_target.image.texture==icons.entries.learned_detailed.texture)
        assert(imports==before and touched==0 and icons:is_ready())
        ''')


if __name__ == "__main__":
    unittest.main()
