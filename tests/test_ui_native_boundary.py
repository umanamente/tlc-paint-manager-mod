"""Pinned-loader parameter limits and native-reference style regressions.

These fixtures model the 512-byte UFunction argument buffer, not native Slate
rendering. The same-build FButtonStyle argument is 912 bytes and cannot cross
that buffer safely, even though the reflected SetStyle method exists.
"""
import unittest

from lupa.lua54 import LuaRuntime

from test_ui import FIXTURE, SCRIPTS
from test_ui_player import PLAYER_FIXTURE


NATIVE_BOUNDARY = r'''
native_parameter_capacity=0x200
native_parameter_bytes={Button_SetStyle=0x390}
native_style_calls=0
whole_style_assignments=0
local ordinary_object=object
function object(kind)
    local value=ordinary_object(kind)
    -- UE4SS returns a view of the existing native style, rather than a detached
    -- Lua value which needs to be passed back to a whole-style setter.
    local native_style=value.WidgetStyle
    if kind=="EditableTextBox" then
        native_style.Padding={Left=7,Top=9,Right=11,Bottom=13}
        native_style.TextStyle.OtherNativeField="preserve"
    end
    rawset(value,"WidgetStyle",nil)
    setmetatable(value,{
        __index=function(_,key)
            if key=="WidgetStyle" then return native_style end
        end,
        __newindex=function(target,key,item)
            if key=="WidgetStyle" then
                whole_style_assignments=whole_style_assignments+1
                error("Whole native style assignment is unnecessary; update reflected fields")
            end
            rawset(target,key,item)
        end,
    })
    function value:SetStyle(style)
        native_style_calls=native_style_calls+1
        local bytes=native_parameter_bytes.Button_SetStyle
        assert(bytes<=native_parameter_capacity,
            "Button_SetStyle: 912-byte parameters exceed the pinned 512-byte buffer")
        native_style=style
    end
    local ordinary_content=value.SetContent
    function value:SetContent(child)
        if child.kind=="EditableTextBox" then
            local style=child.WidgetStyle
            local function luminance(color)
                assert(color.ColorUseRule==0 and color.SpecifiedColor.A==1)
                local c=color.SpecifiedColor
                return 0.2126*c.R+0.7152*c.G+0.0722*c.B
            end
            local background=luminance(style.BackgroundColor)
            for _,ink in ipairs({style.ForegroundColor or false,style.FocusedForegroundColor or false,
                style.ReadOnlyForegroundColor or false,style.TextStyle.ColorAndOpacity or false}) do
                assert(ink and (background+0.05)/(luminance(ink)+0.05)>12)
            end
            assert(style.TextStyle.Font.Size==14 and style.TextStyle.OtherNativeField=="preserve")
            assert(style.Padding.Left==7 and style.Padding.Bottom==13)
            child.styled_before_attachment=true
        end
        if kind=="Button" then
            -- Record scalar fields at first content attachment, independently
            -- from later mutations of the reference returned by WidgetStyle.
            self.style_before_content={
                normal=native_style.Normal.DrawAs,
                hovered=native_style.Hovered.DrawAs,
                pressed=native_style.Pressed.DrawAs,
                disabled=native_style.Disabled.DrawAs,
                normalLeft=native_style.NormalPadding.Left,
                pressedTop=native_style.PressedPadding.Top,
            }
        end
        return ordinary_content(self,child)
    end
    return value
end
'''


class UINativeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(NATIVE_BOUNDARY)
        self.lua.execute(PLAYER_FIXTURE)

    def test_boundary_rejects_the_previous_oversized_style_call(self):
        self.lua.execute('''
        local button=object("Button")
        local ok,err=pcall(function() button:SetStyle(button.WidgetStyle) end)
        assert(not ok and err:find("912-byte parameters",1,true))
        assert(native_style_calls==1 and native_parameter_bytes.Button_SetStyle-native_parameter_capacity==400)
        ''')

    def test_open_render_resize_and_reopen_never_copy_whole_styles(self):
        self.lua.execute('''
        open_player()
        page("ready");page("missing");page("settings")
        ui:_change_settings({tileDensity="compact"});drain_render()
        page("learned")
        viewport_width=1280;viewport_height=720
        ui:tick(work_budget(8));drain_render()
        assert(ui:is_open() and ui.search_box.WidgetStyle.TextStyle.Font.Size==14)
        assert(ui:close("user"));assert(ui:toggle());drain_render()
        assert(native_style_calls==0 and whole_style_assignments==0)
        ''')

    def test_button_padding_and_borderless_brushes_are_set_before_content(self):
        self.lua.execute('''
        open_player()
        local favorite_count=0
        for _,record in ipairs(ui.buttons) do
            local snapshot=record.widget.style_before_content
            if record.kind~="paint_select" then
                assert(snapshot and snapshot.normalLeft==0 and snapshot.pressedTop==0)
                if record.kind=="favorite" then
                    favorite_count=favorite_count+1
                    assert(snapshot.normal==0 and snapshot.hovered==0 and snapshot.pressed==0 and snapshot.disabled==0)
                else
                    assert(snapshot.normal==1 and snapshot.hovered==1 and snapshot.pressed==1 and snapshot.disabled==1)
                end
            end
        end
        assert(favorite_count==#ui.visible_tiles and favorite_count>0)
        assert(native_style_calls==0 and whole_style_assignments==0)
        ''')

    def test_every_entry_has_opaque_dark_ink_before_attachment(self):
        self.lua.execute('''
        assert(ui:toggle())
        assert(ui.search_box.styled_before_attachment and ui.search_box.hint=="Search paint names…")
        page("presets")
        assert(ui.preset_name_box.styled_before_attachment and ui.preset_name_box.hint=="Configuration name")
        ui:_change_settings({showDeveloperTools=true});page("debug")
        for group in pairs(ui.processing_group_hosts) do ui:_processing_select_group(group) end
        local numeric_fields=0
        for _,descriptor in ipairs(ui.processing_descriptors) do
            if descriptor.kind=="number" then
                local entry=ui.processing_fields[descriptor.key].entry
                assert(entry.styled_before_attachment and entry.text~="")
                numeric_fields=numeric_fields+1
            end
        end
        assert(numeric_fields>0)
        assert(native_style_calls==0 and whole_style_assignments==0)
        ''')


if __name__ == "__main__":
    unittest.main()
