"""The stock widget delegates only explicit, local, capacity-verified paint moves."""
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS
from test_native_deposit_widget import WIDGET_FIXTURE
from test_paint_transfer import TRANSFER


class DepositSimilarTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + TRANSFER)
        self.lua.execute(r'''
        fakeWidget={active=function() return false end,reset=function() end,close=function() end,set_settings=function() end}
        package.loaded["paintmanager.native_deposit_widget"]={
            install=function(_,bindings) hooks=bindings;return fakeWidget end
        }
        mod=require("paintmanager.deposit_similar")
        ownerAllowed=true; build=config.reference_build; changes=0; logs={}
        options={game_build=function() return build end,guard=function(t) return ownerAllowed and t==token end,
            changed=function(t) assert(t==token);changes=changes+1 end,
            log=function(s) logs[#logs+1]=s end}
        service=mod.install(config,options)
        function settings(stock,custom) return {depositSimilarAllColors=stock,showDepositAllPaints=custom} end
        service:set_settings(settings(true,true))
        boxInv,owner=stock("Open",id1,1,{[0]=can(A,9,1)})
        open=true;activeBox=boxInv
        function endpoints()
            assert(open and ownerAllowed and collection.context()==token)
            return bag,activeBox
        end
        function click(vanilla)
            local snapshot=hooks.prepare(token,endpoints)
            if vanilla then vanilla() end
            if snapshot then hooks.submit(snapshot,endpoints) end
            return snapshot
        end
        function drain()
            local steps=0
            while service:active() do
                local before=moves;service:step()
                assert(moves-before<=1)
                steps=steps+1;assert(steps<=2*config.max_slots+1)
            end
            return steps
        end
        ''')

    def test_button_readiness_stops_at_first_native_paint_without_capturing_slots(self):
        self.lua.execute('''
        local ordinary=obj("VoyageItem /Game/Items.Iron","VoyageItem")
        local function bounded(inv)
            inv.Items.ForEach=function(_,f)
                assert(f(wrap(0),wrap(entry(ordinary,30)))==nil)
                assert(f(wrap(1),wrap(entry(B,3)))==true)
            end
        end
        bounded(bag);bounded(boxInv)
        transfer.inventory_paints=function() error("button display requested a sorted inventory capture") end
        assert(hooks.can_offer(token,endpoints) and not service:active() and moves==0)
        config.max_slots=1
        boxInv.Items.ForEach=function(_,f) assert(f(wrap(1),wrap(entry(ordinary,10)))==true) end
        bag.Items.ForEach=function() error("backpack should be skipped") end
        assert(not hooks.can_offer(token,endpoints))
        ''')

    def test_existing_stock_action_finishes_before_only_remaining_paints_move(self):
        self.lua.execute('''
        local ordinary=obj("VoyageItem /Game/Items.Iron","VoyageItem")
        bag.slots={[0]=can(A,1,2),[1]=can(B,2,3),[2]=entry(ordinary,7)}
        boxInv.slots[2]=entry(ordinary,1)
        assert(click(function()
            -- The game moved its matching color and matching ordinary items.
            boxInv.slots[0].Data.ItemCount=3;bag.slots[0]=nil
            boxInv.slots[2].Data.ItemCount=8;bag.slots[2]=nil
            assert(moves==0)
        end))
        drain()
        assert(moves==1 and changes==1 and #logs==1 and logs[1]:find("3 additional paint cans verified",1,true))
        assert(next(bag.slots)==nil and boxInv.slots[1].Item:get()==B)
        assert(boxInv.slots[1].Data.ItemCount==3 and boxInv.slots[2].Data.ItemCount==8)
        ''')

    def test_separate_paint_action_is_independent_of_stock_deposit_and_moves_only_paints(self):
        self.lua.execute('''
        local iron=obj("VoyageItem /Game/Items.Iron","VoyageItem")
        local C=paint("DA_Paint_C","Copper")
        bag.slots={[0]=can(A,4,2),[1]=can(B,5,1),[3]=entry(iron,8),[4]=can(C,7,1)}
        boxInv.slots[5]=entry(iron,6)
        hooks.prepare=function() error("original button pre-handler used") end
        hooks.submit=function() error("original button post-handler used") end
        assert(hooks.activate(token,endpoints))
        assert(not hooks.activate(token,endpoints) and moves==0)
        drain()
        assert(moves==3 and changes==1 and bag.slots[3].Data.ItemCount==8 and boxInv.slots[5].Data.ItemCount==6)
        assert(boxInv.slots[1].Item:get()==A and boxInv.slots[1].Data.ItemCount==2)
        assert(boxInv.slots[2].Item:get()==B and boxInv.slots[3].Item:get()==C)
        assert(logs[1]:find("queued 4") and logs[2]:find("already running") and logs[3]:find("4 of 4 cans verified"))
        ''')

    def test_stock_action_records_zero_when_vanilla_handled_the_color_and_ignores_nonpaint_clicks(self):
        self.lua.execute('''
        bag.slots[0]=can(A,3,2)
        click(function() boxInv.slots[0].Data.ItemCount=3;bag.slots[0]=nil end);drain()
        assert(moves==0 and #logs==1 and logs[1]:find("0 additional paint cans verified",1,true))
        assert(logs[1]:find("no longer available after the native action",1,true))
        local iron=obj("VoyageItem /Game/Items.Iron","VoyageItem")
        bag.slots[1]=entry(iron,2)
        assert(not click());drain();assert(#logs==1)
        ''')

    def test_stock_extension_reports_capacity_even_if_vanilla_emptied_an_earlier_slot(self):
        self.lua.execute('''
        bag.slots={[0]=can(A,3,1),[1]=can(B,2,2)}
        click(function() boxInv.slots[0].Data.ItemCount=2;bag.slots[0]=nil;boxInv.limit=1 end)
        drain()
        assert(moves==0 and #logs==1 and logs[1]:find("0 additional paint cans verified",1,true))
        assert(logs[1]:find("no empty slot",1,true) and bag.slots[1].Data.ItemCount==2)
        ''')

    def test_clicked_action_reports_partial_capacity_and_preserves_remaining_cans(self):
        self.lua.execute('''
        bag.slots={[0]=can(B,5,4)}
        boxInv.weightSpace=2
        assert(hooks.activate(token,endpoints));drain()
        assert(moves==1 and changes==1 and bag.slots[0].Data.ItemCount==2 and boxInv.slots[1].Data.ItemCount==2)
        assert(logs[#logs]:find("2 of 4 cans verified") and logs[#logs]:find("weight"))
        boxInv.limit=2
        local visible,enabled=hooks.can_offer(token,endpoints)
        assert(visible and not enabled)
        assert(hooks.activate(token,endpoints));drain()
        assert(moves==1 and logs[#logs]:find("0 of 2 cans verified") and logs[#logs]:find("empty slot"))
        ''')

    def test_separate_paint_action_reports_rejection_when_source_or_marker_is_missing(self):
        self.lua.execute('''
        assert(not hooks.activate(token,endpoints) and logs[1]:find("backpack contains no paint"))
        bag.slots[0]=can(B,4,1);boxInv.slots={}
        assert(not hooks.activate(token,endpoints) and logs[2]:find("box contains no paint"))
        open=false
        assert(not hooks.activate(token,endpoints) and #logs==3 and not service:active())
        assert(moves==0 and changes==0)
        ''')

    def test_dedicated_input_cannot_survive_reload_or_reenter_another_deposit_during_capture(self):
        self.lua.execute('''
        bag.slots[0]=can(B,2,2)
        local read=boxInv.Items.ForEach
        local repeated
        boxInv.Items.ForEach=function(...)
            repeated=hooks.activate(token,endpoints)
            read(...)
            service:reset()
        end
        assert(not hooks.activate(token,endpoints) and repeated==false)
        assert(not service:active() and moves==0 and changes==0 and #logs==1)
        ''')

    def test_empty_and_nonpaint_only_boxes_are_decided_before_vanilla_action(self):
        self.lua.execute('''
        bag.slots[0]=can(B,2,3)
        local ordinary=obj("VoyageItem /Game/Items.Iron","VoyageItem")
        for _,initial in ipairs({{},{[0]=entry(ordinary,4)}}) do
            boxInv.slots=initial
            assert(not click(function() boxInv.slots[3]=can(A,9,1) end))
            assert(not service:active() and moves==0)
        end
        ownerAllowed=false;assert(not click())
        ownerAllowed=true;build="unverified";assert(not click())
        ''')

    def test_distinct_colors_include_hidden_paints_and_skip_nonpaint_and_new_pickups(self):
        self.lua.execute('''
        local C=paint("DA_Paint_C","Copper")
        local ordinary=obj("VoyageItem /Game/Items.Iron","VoyageItem")
        bag.slots={[0]=can(B,2,1),[2]=can(C,3,1),[3]=entry(ordinary,8)}
        assert(click(function() bag.slots[4]=can(C,5,1) end))
        drain()
        assert(moves==2 and changes==1 and #logs==1 and logs[1]:find("2 additional paint cans verified",1,true))
        assert(bag.slots[0]==nil and bag.slots[2]==nil and bag.slots[3].Data.ItemCount==8 and bag.slots[4])
        assert(boxInv.slots[1].Item:get()==B and boxInv.slots[2].Item:get()==C)
        ''')

    def test_capacity_limits_plain_portions_and_preserves_opaque_stacks(self):
        self.lua.execute('''
        bag.slots={[0]=can(B,2,7),[1]=opaque(A,7,3)}
        boxInv.weightSpace=5;boxInv.stackLimit=2
        click();drain()
        assert(moves==3 and changes==1 and #logs==1 and logs[1]:find("5 additional paint cans verified",1,true))
        assert(bag.slots[0].Data.ItemCount==2 and bag.slots[1].Data.ItemCount==3)
        assert(boxInv.slots[1].Data.ItemCount==2 and boxInv.slots[2].Data.ItemCount==2 and boxInv.slots[3].Data.ItemCount==1)
        boxInv.weightSpace=9
        bag.slots[0]=nil
        click();drain()
        assert(moves==3 and bag.slots[1].Data.ItemCount==3)
        boxInv.stackLimit=3
        click();drain()
        assert(moves==4 and not bag.slots[1] and boxInv.slots[4].Data.ItemCount==3)
        ''')

    def test_full_box_terminates_and_has_no_invented_slot_or_false_removal(self):
        self.lua.execute('''
        boxInv.limit=1;bag.slots[1]=can(B,3,2)
        click();assert(drain()==1 and moves==0 and bag.slots[1].Data.ItemCount==2)
        boxInv.limit=12;config.max_slots=1
        click();assert(drain()==1 and moves==0 and transfer.can_transfer(token))
        ''')

    def test_close_retarget_foreign_context_and_changed_slot_stop_original_deposit(self):
        for change in (
            'open=false', 'ownerAllowed=false', 'world.address=world.address+1', 'build="other"',
            'activeBox=stock("Other",id2,1,{[0]=can(A,9,1)})',
            'bag.slots[0]=can(B,77,1)', 'bag.slots[0]=can(B,2,4)', 'boxInv.slots={}',
            'hooks.cancel()', 'service:close()', 'service:reset()',
        ):
            with self.subTest(change=change):
                self.setUp()
                self.lua.execute('bag.slots[0]=can(B,2,1);click();' + change + ';drain();assert(moves==0 and changes==1)')

    def test_wrong_post_result_stops_all_extensions_and_never_repeats_uncertain_entry(self):
        self.lua.execute('''
        local C=paint("DA_Paint_C","Copper")
        bag.slots={[0]=can(B,2,1),[1]=can(C,3,1)}
        after_native=function(dst,src,s,t) dst.slots[t].Data.ItemCount=9 end
        click();drain()
        assert(moves==1 and changes==1 and #logs==1 and not transfer.can_transfer(token))
        assert(not click() and not service:active() and bag.slots[1].Data.ItemCount==1)
        ''')

    def test_reentrant_admission_cannot_move_replaced_or_cancelled_slots(self):
        self.lua.execute('''
        bag.slots[0]=can(B,2,1)
        click()
        local ordinary=obj("VoyageItem /Game/Items.Iron","VoyageItem")
        local previous=boxInv.EstimateAddAmountToSlot
        function boxInv:EstimateAddAmountToSlot(...)
            bag.slots[0]=entry(ordinary,1)
            return previous(self,...)
        end
        drain()
        assert(moves==0 and bag.slots[0].Item:get()==ordinary)
        boxInv.EstimateAddAmountToSlot=previous
        bag.slots[0]=can(B,2,1);click()
        local original=boxInv.Items.ForEach
        local reads=0
        function boxInv.Items:ForEach(...)
            reads=reads+1;original(self,...)
            if reads==2 then hooks.cancel() end
        end
        drain();assert(moves==0 and bag.slots[0].Item:get()==B and reads==2)
        ''')

    def test_native_cancellation_and_exhausted_budget_do_not_touch_the_next_slot(self):
        self.lua.execute('''
        bag.slots={[0]=can(B,2,1),[1]=can(B,3,1)}
        click();service:step({expired=function()return true end})
        assert(moves==0 and service:active())
        service:step();assert(moves==1 and service:active())
        hooks.cancel();assert(not service:active() and changes==1 and bag.slots[1])
        ''')

    def test_missing_native_ui_registration_leaves_other_mod_functions_usable(self):
        self.lua.execute('''
        package.loaded["paintmanager.native_deposit_widget"]={install=function() error("native hook unavailable") end}
        local inactive=mod.install(config,options)
        assert(not inactive:active())
        inactive:step();inactive:reset();inactive:close()
        assert(#logs==1 and moves==0)
        ''')

    def test_each_option_only_cancels_its_own_job_and_expires_only_its_own_snapshot(self):
        for explicit in (True, False):
            with self.subTest(explicit=explicit):
                self.setUp()
                self.lua.globals().explicit=explicit
                self.lua.execute('''
                bag.slots={[0]=can(A,22),[1]=can(B,77)}
                local stock_snapshot=hooks.prepare(token,endpoints)
                if explicit then assert(hooks.activate(token,endpoints)) else assert(hooks.submit(stock_snapshot,endpoints)) end
                service:set_settings(settings(not explicit,explicit));assert(service:active())
                service:step();assert(moves==1 and service:active())
                service:set_settings(settings(explicit,not explicit))
                assert(not service:active() and changes==1)
                drain();assert(moves==1 and bag.slots[1])
                service:set_settings(settings(true,true))
                assert(not hooks.submit(stock_snapshot,endpoints) and not service:active())
                local current_stock=hooks.prepare(token,endpoints)
                service:set_settings(settings(true,false))
                service:set_settings(settings(true,true))
                assert(hooks.submit(current_stock,endpoints));drain();assert(moves==2)
                ''')

    def test_disable_during_native_call_counts_inflight_result_and_stops_before_next_stack(self):
        self.lua.execute('''
        bag.slots={[0]=can(A,1),[1]=can(B,2)}
        assert(hooks.activate(token,endpoints))
        after_native=function() service:set_settings(nil) end
        service:step()
        assert(moves==1 and not service:active() and transfer.can_transfer(token))
        assert(bag.slots[1] and changes==1 and logs[#logs]:find("1 of 2 cans verified",1,true))
        assert(not hooks.can_offer(token,endpoints) and not hooks.prepare(token,endpoints) and not hooks.activate(token,endpoints))
        ''')

    def test_cannot_submit_stale_input_after_reload_or_after_switching_boxes(self):
        self.lua.execute('''
        bag.slots[0]=can(B,2,1)
        local old=hooks.prepare(token,endpoints)
        service:reset();assert(not hooks.submit(old,endpoints) and not service:active())
        assert(logs[#logs]:find("original paint selection expired",1,true))
        old=hooks.prepare(token,endpoints)
        activeBox=stock("Other",id2,1,{[0]=can(A,8,1)})
        assert(not hooks.submit(old,endpoints) and not service:active() and moves==0)
        assert(logs[#logs]:find("open deposit box or backpack changed",1,true))
        ''')


class NativeDepositIntegrationTests(unittest.TestCase):
    def test_opening_another_box_mid_deposit_never_redirects_the_remaining_can(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().scripts_path = SCRIPTS.as_posix()
        lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        lua.execute(FIXTURE + TRANSFER + WIDGET_FIXTURE)
        lua.execute(r'''
        -- Both stock controls are gray. Only the mod's own press/release can queue a transfer.
        RegisterHook=nil
        similar.enabled,all.enabled=false,false
        function menu:GetParent() error("The focused container can be nested") end
        local first=stock_box
        local other=stock("Other",id2,1,{[0]=can(B,12)})
        other.Access=2
        local red=can(A,42,2)
        local blue=can(B,43)
        local iron=entry(obj("VoyageItem /Game/Items.Iron","VoyageItem"),7)
        bag.slots={[0]=red,[1]=blue,[2]=iron}
        local reports,changed={},0
        local service=require("paintmanager.deposit_similar").install(config,{
            game_build=function() return config.reference_build end,
            guard=function(t) return t==token end,
            make_button=make_deposit_button,
            log=function(message) reports[#reports+1]=message end,
            changed=function(t) assert(t==token);changed=changed+1 end,
        })
        service:set_settings(settings(false,true));service:probe(controller);service:step()
        local original_button=paint_button
        assert(original_button.parent==deposit_row and not similar.enabled and not all.enabled)
        click_paint(service)
        service:step()
        assert(moves==1 and first.slots[1]==red and bag.slots[0]==nil)
        stock_box=other
        service:step()
        for _=1,40 do service:step() end
        assert(moves==1 and changed==1 and transfer.can_transfer(token))
        assert(first.slots[1].Data.ItemCount==2 and not first.slots[2])
        assert(bag.slots[1]==blue and bag.slots[2]==iron and other.slots[0].Data.Timestamp==12
            and not other.slots[1])
        assert(table.concat(reports," "):find("2 of 3 cans verified"))
        assert(not original_button.parent and paint_button~=original_button and paint_button.parent==deposit_row)
        service:close()
        ''')

    def test_normal_first_open_mouse_and_keyboard_preserve_stock_and_conservation(self):
        for mouse in (True, False):
            with self.subTest(mouse=mouse):
                lua = LuaRuntime(unpack_returned_tuples=True)
                lua.globals().scripts_path = SCRIPTS.as_posix()
                lua.globals().mouse = mouse
                lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
                lua.execute(FIXTURE + TRANSFER + WIDGET_FIXTURE)
                lua.execute(r'''
                changes=0;logs={}
                service=require("paintmanager.deposit_similar").install(config,{
                    game_build=function()return config.reference_build end,
                    make_button=make_deposit_button,
                    guard=function(t)return t==token end,
                    changed=function(t)assert(t==token);changes=changes+1 end,
                    log=function(s)logs[#logs+1]=s end})
                service:set_settings(settings(true,false))
                editor=require("paintmanager.native_editor").new(config,{
                    observe_container=function(pc)service:probe(pc)end,log=function(s)logs[#logs+1]=s end})
                assert(not similar.enabled and not service:active())
                editor:tick();cycle(service)
                assert(focus_queries>0 and overlay_queries==0 and similar.enabled and all.enabled and moves==0)
                local event={bPrimaryAction=true,bModifier=false,bMouseInput=mouse}
                local pre,post=hooks[deposit_key].pre,hooks[deposit_key].post
                assert(pre(wrap(menu),wrap(similar),event)==nil and moves==0)
                -- Vanilla sees no equal color; the registered post hook runs next.
                assert(post(wrap(menu),wrap(similar),event)==nil and moves==0)
                cycle(service)
                assert(moves==1 and changes==1 and not next(bag.slots))
                assert(stock_box.slots[0].Item:get()==A and stock_box.slots[0].Data.ItemCount==1)
                assert(stock_box.slots[1].Item:get()==B and stock_box.slots[1].Data.ItemCount==1)
                service:close();assert(not service:active() and not next(hooks))
                ''')

    def test_custom_button_moves_all_colors_independently_with_and_without_stock_overlap_or_hooks(self):
        for overlap, hook_available in ((False, True), (True, True), (False, False)):
            with self.subTest(overlap=overlap, hook_available=hook_available):
                lua = LuaRuntime(unpack_returned_tuples=True)
                lua.globals().scripts_path = SCRIPTS.as_posix()
                lua.globals().overlap = overlap
                lua.globals().hook_available = hook_available
                lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
                lua.execute(FIXTURE + TRANSFER + WIDGET_FIXTURE)
                lua.execute(r'''
                config.pump_ms=32
                if not hook_available then RegisterHook=nil end
                local C=paint("DA_Paint_C","Copper")
                local iron=obj("VoyageItem /Game/Items.Iron","VoyageItem")
                bag.slots[2]=can(overlap and A or C,2,2)
                bag.slots[3]=entry(iron,4)
                similar.enabled=overlap
                changes=0;logs={}
                service=require("paintmanager.deposit_similar").install(config,{
                    game_build=function() return config.reference_build end, make_button=make_deposit_button,
                    guard=function(t)return t==token end,
                    changed=function(t)assert(t==token);changes=changes+1 end,
                    log=function(s)logs[#logs+1]=s end})
                service:set_settings(settings(hook_available,true))
                editor=require("paintmanager.native_editor").new(config,{
                    observe_container=function(pc)service:probe(pc)end, log=function(s)logs[#logs+1]=s end})
                editor:tick();cycle(service)
                assert(focus_queries>0 and overlay_queries==0 and paint_button and paint_button.parent==deposit_row)
                assert(paint_button~=similar and paint_button~=all and moves==0)
                click_paint(service)
                for _=1,60 do service:step() end
                assert(changes==1 and moves==2 and bag.slots[3].Data.ItemCount==4 and not bag.slots[2] and not bag.slots[4])
                assert(stock_box.slots[0].Item:get()==A and stock_box.slots[0].Data.ItemCount==1)
                assert(stock_box.slots[1].Item:get()==(overlap and A or C) and stock_box.slots[1].Data.ItemCount==2)
                assert(stock_box.slots[2].Item:get()==B and stock_box.slots[2].Data.ItemCount==1)
                assert(table.concat(logs," "):find("3 of 3 cans verified") and not paint_button.parent)
                service:close()
                ''')
