"""Saved logical layouts and explicit native transfer guards with real Lua jobs."""
import json
from pathlib import Path
import tempfile
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE, SCRIPTS


PRESET_FIXTURE = r'''
presets=require("paintmanager.presets")
workflow=require("paintmanager.preset_workflow")
budget_module=require("paintmanager.work_budget")
nativeCalls,admissionCalls,transferReturn=0,{},1
nativeTransferMode="normal"
local make_entry=entry
function entry(item,count,identity)
    local row=make_entry(item,count,identity)
    row.Data.Timestamp,row.Data.Flags=101,1
    row.Data.ItemDataMap={}
    row.Data.ItemData=setmetatable({ForEach=function(_,visit) visit(0,wrap(7)) end},{__len=function() return 1 end})
    return row
end
function moved_can(row)
    local moved=entry(row.Item:get(),1)
    for key,value in pairs(row.Data) do if key~="ItemCount" then moved.Data[key]=value end end
    if nativeTransferMode=="lose_payload" then moved.Data.ItemData={} end
    return moved
end
paintA=paint("DA_Paint_Canada","Last Leaf")
paintB=paint("DA_Paint_Suomi","Suomi")
function controller:IsLocalController() return true end
function native_map(entries)
    return {ForEach=function(_,callback)
        for _,e in ipairs(entries) do if callback(wrap(e[1]),wrap(e[2]))==true then break end end
    end}
end
function inventory(path,owner,kind,rows)
    local c=obj(path,kind)
    c.owner,c.rows=owner,rows or {}
    function c:GetOwner() return self.owner end
    c.Items={ForEach=function(_,callback)
        inventory_reads=inventory_reads+1
        if c.readError then error("inventory read error") end
        for _,e in ipairs(c.rows) do if callback(wrap(e[1]),wrap(e[2]))==true then break end end
    end}
    function c:IsValidSlotIndex(index)
        admissionCalls[#admissionCalls+1]="IsValidSlotIndex"
        if self.admitSlot~=nil then return self.admitSlot end
        return type(index)=="number" and index>=0
    end
    function c:CanRemoveSlot(index)
        admissionCalls[#admissionCalls+1]="CanRemoveSlot"
        if self.admitRemove~=nil then return self.admitRemove end
        return true
    end
    function c:CanAddItem(item)
        admissionCalls[#admissionCalls+1]="CanAddItem"
        if self.admitItem~=nil then return self.admitItem end
        return item==paintA or item==paintB
    end
    function c:CanAddToSlot(index,item)
        admissionCalls[#admissionCalls+1]="CanAddToSlot"
        if self.admitAddSlot~=nil then return self.admitAddSlot end
        return true
    end
    function c:EstimateAddAmount(item,count)
        assert(count==1)
        admissionCalls[#admissionCalls+1]="EstimateAddAmount"
        return self.estimate or 1
    end
    function c:EstimateAddAmountToSlot(index,item,count)
        assert(count==1)
        admissionCalls[#admissionCalls+1]="EstimateAddAmountToSlot"
        return self.estimateSlot or 1
    end
    function c:GetNextEmptySlot()
        if self.full then return -1 end
        local occupied={}
        for _,e in ipairs(self.rows) do if e[2].Data.ItemCount>0 then occupied[e[1]]=true end end
        for i=0,200 do if not occupied[i] then return i end end
        return -1
    end
    function c:TransferSlot(source,sourceSlot,targetSlot,amount)
        nativeCalls=nativeCalls+1
        assert(amount==1 and targetSlot>=0)
        for i,e in ipairs(self.rows) do if e[1]==targetSlot then
            assert(e[2].Data.ItemCount==0,"Transfer into an occupied slot");table.remove(self.rows,i);break
        end end
        lastTransfer={sourceSlot=sourceSlot,targetSlot=targetSlot,amount=amount,target=self.path}
        if nativeTransferMode=="throw_before" then error("native transfer threw") end
        local row,sourceIndex
        for i,e in ipairs(source.rows) do if e[1]==sourceSlot then row,sourceIndex=e[2],i end end
        assert(row and row.Data.ItemCount==1)
        if nativeTransferMode~="no_change" then
            self.rows[#self.rows+1]={targetSlot,moved_can(row)}
            if nativeTransferMode~="duplicate" then table.remove(source.rows,sourceIndex) end
        end
        if nativeTransferMode=="move_unrelated" then source.rows[1][1]=99 end
        if nativeTransferMode=="context_change" then world.address=world.address+1 end
        if nativeTransferMode=="throw_after" then error("native transfer threw after write") end
        return transferReturn
    end
    return c
end
function paint_component(path,owner,indices)
    local c=inventory(path,owner,"VoyagePaintInventoryComponent",{})
    c.MinimumItemsToUnlockSet,c.bAllowSetUnlock=4,true
    c.PaintSetName,c.PaintSetDisplayName=text("Caretaker"),text("Caretaker Paint")
    local entries={}
    for i,index in ipairs(indices or {7,2}) do
        entries[#entries+1]={index,{SlotDisplayName=text("Zone "..i),
            MaterialSlotName=text("Material"..i),PartName=text("Part"..i)}}
    end
    c.PaintSlots=native_map(entries)
    return c
end
backpack=inventory("Inventory /Game/Map.Map.Pawn.WeightInventory",pawn,"VoyageInventoryWeightLimitedComponent",
    {{12,entry(paintA,1)},{13,entry(paintB,1)}})
pawn.WeightInventory=backpack
function controller:GetInventoryComponent(kind) assert(kind==1);return backpack end
pawn.PaintInventory=paint_component("Paint /Game/Map.Map.Pawn.PaintInventory",pawn)
boat=obj("Boat /Game/Map.Map.Boat","VoyageBoatPawn")
boat.location={X=100,Y=0,Z=0}
function boat:GetWorld() return world end
function boat:K2_GetActorLocation() return self.location end
boatPaint=paint_component("Paint /Game/Map.Map.Boat.PaintInventory",boat,{20,30})
paintComponents={pawn.PaintInventory,boatPaint}
oldFindAllOf=FindAllOf
function FindAllOf(kind)
    if kind=="VoyagePaintInventoryComponent" then return paintComponents end
    return oldFindAllOf(kind)
end
function drain(job)
    for i=1,10000 do
        local result=job:step(budget_module.new(10000,1))
        if result.state~="running" then return result end
    end
    error("job did not finish")
end
function plain_job(value,seen)
    if type(value)=="userdata" then return false end
    if type(value)~="table" then return true end
    if value.IsValid then return false end
    seen=seen or {};if seen[value] then return true end;seen[value]=true
    for k,v in pairs(value) do if not plain_job(k,seen) or not plain_job(v,seen) then return false end end
    return true
end
function until_phase(job,phase)
    for i=1,10000 do
        if job.phase==phase then return end
        assert(job.state=="running","job ended before "..phase)
        job:step()
    end
    error("phase not reached")
end
function refresh()
    local result=drain(presets.begin_capture(config))
    assert(result.state=="done",result.error)
    view=result.view
    return view
end
function copy(value)
    if type(value)~="table" or value==json.null then return value end
    local result={}
    for k,v in pairs(value) do result[k]=copy(v) end
    return result
end
function request_for(kind)
    refresh()
    for _,target in ipairs(view.targets) do if target.kind==(kind or "character") then
        local preset=presets.from_target(target,"My layout","preset-test",config.reference_build)
        for _,zone in ipairs(preset.zones) do
            zone.paint={assetType="Item",assetName="DA_Paint_Canada",displayName="Last Leaf"}
        end
        -- Two distinct singleton sources are needed for two equal zones.
        if #backpack.rows==2 and backpack.rows[2][2].AssetName:ToString()=="DA_Paint_Suomi" then
            backpack.rows[2][2]=entry(paintA,1)
        end
        return {preset=preset,target=target,gameBuild=config.reference_build,
            contextToken=view.contextToken}
    end end
    error("target not found")
end
function start(kind)
    request=request_for(kind)
    job=presets.begin_apply(config,request)
    return job
end
function receipt() return json.encode(job:get_receipt()) end
'''


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE)
        self.lua.execute(PRESET_FIXTURE)

    def run_lua(self, code):
        return self.lua.execute(code)

    def test_stock_editor_save_reads_only_the_component_bound_to_its_paint_bar(self):
        self.run_lua('''
        local bar = obj("Widget /Game/Map.Map.PaintBar", "VoyagePaintBarWidget")
        function bar:GetSourceComponent() return boatPaint end
        local target = assert(presets.editor_target(bar))
        assert(target.kind == "boat" and target.id == target.runtimeRef.id)
        boatPaint.rows = {{20,entry(paintB,1)}}
        function FindAllOf() error("The selected editor target needs no global component search") end
        function boat:K2_GetActorLocation() error("Preset saving queries the edited component directly") end
        function pawn:K2_GetActorLocation() error("Preset saving needs no player position") end
        local written
        local store = {
            new_id = function() return "test" end,
            begin_save_layout = function(record)
                written = record
                return { step = function() return {state="done", snapshot={}} end,
                    get_result = function() return {presets={record}} end }
            end
        }
        local request = { targetId = target.id, target = target, name = "Preset 1",
            gameBuild = config.reference_build, contextToken = assert(require("paintmanager.collection").context()) }
        local result = drain(workflow.begin(config, nil, "preset_save", request, store))
        assert(result.state == "done", result.error)
        assert(written.name == "Preset 1" and written.target.kind == "boat" and #written.zones == 2)
        local painted = 0
        for _, zone in ipairs(written.zones) do
            if zone.paint ~= json.null then
                assert(zone.paint.assetName == "DA_Paint_Suomi")
                painted = painted + 1
            end
        end
        assert(painted == 1 and nativeCalls == 0)
        function bar:GetSourceComponent() return backpack end
        assert(presets.editor_target(bar) == nil)
        ''')

    def value(self, expression):
        return self.lua.eval(expression)

    def receipt(self):
        return json.loads(self.run_lua('return receipt()'))

    def test_new_ids_require_native_uuid_producer_and_normalize_guid_text(self):
        self.run_lua('''
        assert(presets.new_id(function() return "{01234567-89AB-4CDE-8F01-23456789ABCD}" end)
            =="01234567-89ab-4cde-8f01-23456789abcd")
        assert(presets.new_id(function() return "0123456789AB4CDE8F0123456789ABCD" end)
            =="01234567-89ab-4cde-8f01-23456789abcd")
        for _,value in ipairs({"", "preset-1", "00000000-0000-0000-0000-000000000000",
            "01234567{89AB4CDE8F0123456789ABCD", "g123456789AB4CDE8F0123456789ABCD"}) do
            assert(not pcall(function() presets.new_id(function() return value end) end))
        end
        assert(not pcall(presets.new_id))
        assert(nativeCalls==0)
        ''')

    def test_duplicate_names_keep_ids_distinct_in_display(self):
        self.run_lua('''
        local first={id="same-prefix-a",name="Layout"};local second={id="same-prefix-b",name="layout"}
        assert(presets.display_name(first,{first})=="Layout")
        assert(presets.display_name(first,{first,second})=="Layout [same-prefix-a]")
        assert(presets.display_name(second,{first,second})=="layout [same-prefix-b]")
        assert(first.name=="Layout" and second.name=="layout")
        ''')

    def test_capture_contains_current_character_and_explicit_boat_zones(self):
        data = json.loads(self.run_lua('refresh();return json.encode(view)'))
        self.assertEqual([t['kind'] for t in data['targets']], ['character', 'boat'])
        self.assertTrue(all(t['complete'] for t in data['targets']))
        self.assertEqual(len(data['targets'][0]['logicalZones']), 2)
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_compare_layout_uses_exact_zone_and_asset_identity(self):
        self.run_lua('''
        pawn.PaintInventory.rows={{7,entry(paintA,1)}}
        local live=refresh().targets[1]
        local saved=copy(presets.from_target(live,"First","preset-1",config.reference_build))
        local painted,empty=saved.zones[1],saved.zones[2]
        if painted.paint==json.null then painted,empty=empty,painted end
        saved.zones={saved.zones[2],saved.zones[1]}
        painted.displayName="Translated label"
        painted.paint.displayName="Translated paint"
        assert(presets.compare_layout(live,saved)=="match")
        empty.paint=copy(painted.paint)
        assert(presets.compare_layout(live,saved)=="fill")
        painted.paint.assetType="DifferentType"
        assert(presets.compare_layout(live,saved)=="replace")
        painted.paint=json.null
        assert(presets.compare_layout(live,saved)=="replace")
        empty.partName="AnotherPart"
        assert(presets.compare_layout(live,saved)=="incompatible")
        saved.target.className="/Game/OtherCharacter"
        assert(presets.compare_layout(live,saved)=="incompatible")
        live.complete=false
        assert(presets.compare_layout(live,saved)=="unknown")
        ''')

    def test_zone_order_uses_native_identities_and_keeps_unknown_order(self):
        self.run_lua('''
        local function zone(material,part)
            return {materialSlotName=material,partName=part}
        end
        local extra_a,extra_b=zone("Other","Head"),zone("Other","Tail")
        local arms=zone("MI_Player_Character_Arms","Arms")
        local torso=zone("MI_Player_Character_Torso","Torso")
        local source={extra_a,arms,extra_b,torso}
        local ordered=presets.ordered_zones("character",source)
        assert(ordered[1]==torso and ordered[2]==arms and ordered[3]==extra_a and ordered[4]==extra_b)
        assert(source[1]==extra_a and source[2]==arms)
        local flag=zone("FlagMat","Flag")
        local deck=zone("M_Deck","Deck")
        ordered=presets.ordered_zones("boat",{flag,deck})
        assert(ordered[1]==deck and ordered[2]==flag)
        ''')

    def test_persistent_layout_contains_logical_zones_without_runtime_indices(self):
        data = json.loads(self.run_lua('refresh();return json.encode(presets.from_target(view.targets[1],"First","preset-1",config.reference_build))'))
        self.assertEqual(data['target']['className'], '/Script/Voyage.VoyageCharacter')
        self.assertEqual({z['materialSlotName'] for z in data['zones']}, {'Material1', 'Material2'})
        self.assertTrue(all(z['paint'] is None for z in data['zones']))
        raw = json.dumps(data)
        for forbidden in ('runtimeRef', 'ownerRuntimeRef', 'contextToken', 'PaintInventory', 'index', 'address'):
            self.assertNotIn(forbidden, raw)

    def test_connection_loss_at_final_admission_never_attempts_transfer(self):
        self.run_lua('''
        start();until_phase(job,"preset_commit")
        connectionChecks=0
        request.connection_guard=function()
            connectionChecks=connectionChecks+1
            assert(#admissionCalls>0)
            return false,"Signal lost before native transfer"
        end
        drain(job)
        assert(connectionChecks==1 and nativeCalls==0 and not job:get_receipt().attempted)
        assert(job:get_receipt().outcome=="rejected")
        ''')

    def test_one_transfer_per_step_and_exact_native_arguments(self):
        self.run_lua('start();until_phase(job,"preset_commit");assert(nativeCalls==0);job:step()')
        self.assertEqual(self.value('nativeCalls'), 1)
        self.assertEqual(self.value('lastTransfer.amount'), 1)
        self.assertEqual(self.value('lastTransfer.sourceSlot'), 12)
        self.assertEqual(self.value('lastTransfer.targetSlot'), 2)
        self.run_lua('drain(job);job:step();job:step()')
        self.assertEqual(self.value('nativeCalls'), 2)
        self.assertEqual(self.receipt()['completed'], 2)
        self.assertEqual(self.receipt()['outcome'], 'applied_verified')

    def test_already_matching_layout_performs_no_transfer(self):
        self.run_lua('start();pawn.PaintInventory.rows={{2,entry(paintA,1)},{7,entry(paintA,1)}};drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'already_matches')
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_saved_document_guard_is_checked_at_bounded_action_boundaries(self):
        self.run_lua('''
        start();guardCalls=0
        request.persistence_guard=function() guardCalls=guardCalls+1;return true end
        until_phase(job,"preset_ready");assert(guardCalls==1 and nativeCalls==0)
        job:step();assert(guardCalls==2 and nativeCalls==0)
        job:step();assert(guardCalls==4 and nativeCalls==1)
        drain(job);assert(guardCalls==6 and nativeCalls==2)
        ''')
        self.assertEqual(self.receipt()['outcome'], 'applied_verified')

    def test_saved_document_guard_rejects_before_preflight_reads(self):
        self.run_lua('''
        start();local before=inventory_reads
        request.persistence_guard=function() return false,"Saved document replaced" end
        drain(job);assert(inventory_reads==before)
        ''')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertIn('Saved document replaced', self.receipt()['error'])
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_saved_document_changed_before_commit_never_attempts_transfer(self):
        for result in ('return false,"HUD/progress replaced"', 'error("HUD/progress replaced")'):
            with self.subTest(result=result):
                self.setUp()
                self.run_lua('''
                start();request.persistence_guard=function() return true end
                until_phase(job,"preset_commit")
                request.persistence_guard=function() '''+result+''' end
                drain(job)
                ''')
                self.assertEqual(self.receipt()['outcome'], 'rejected')
                self.assertFalse(self.receipt()['attempted'])
                self.assertIn('HUD/progress replaced', self.receipt()['error'])
                self.assertEqual(self.value('nativeCalls'), 0)

    def test_changed_saved_document_rejects_noop_before_claiming_matches(self):
        self.run_lua('''
        start();pawn.PaintInventory.rows={{2,entry(paintA,1)},{7,entry(paintA,1)}}
        request.persistence_guard=function() return true end
        until_phase(job,"preset_ready")
        request.persistence_guard=function() return false,"No-op document changed" end
        drain(job)
        ''')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertIn('No-op document changed', self.receipt()['error'])
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_saved_document_changed_during_transfer_is_uncertain_and_blocks_retry(self):
        for result in ('return false,"Carrier changed during transfer"', 'error("Carrier changed during transfer")'):
            with self.subTest(result=result):
                self.setUp()
                self.run_lua('''
                start();request.persistence_guard=function() return true end
                local native=pawn.PaintInventory.TransferSlot
                function pawn.PaintInventory:TransferSlot(...)
                    local result=native(self,...)
                    request.persistence_guard=function() '''+result+''' end
                    return result
                end
                drain(job)
                ''')
                self.assertEqual(self.receipt()['outcome'], 'uncertain')
                self.assertTrue(self.receipt()['attempted'])
                self.assertEqual(self.value('nativeCalls'), 1)
                self.assertEqual(self.value('#pawn.PaintInventory.rows'), 1)
                self.assertIn('Carrier changed during transfer', self.receipt()['error'])
                self.run_lua('request.persistence_guard=function() return true end;job=presets.begin_apply(config,request);drain(job)')
                self.assertEqual(self.value('nativeCalls'), 1)

    def test_preset_can_be_applied_with_changed_runtime_slot_indices(self):
        self.run_lua('start();pawn.PaintInventory.PaintSlots=native_map({{40,{SlotDisplayName=text("Other"),MaterialSlotName=text("Material1"),PartName=text("Part1")}},{50,{SlotDisplayName=text("Other"),MaterialSlotName=text("Material2"),PartName=text("Part2")}}});drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'applied_verified')
        self.assertEqual({r['destinationSlot'] for r in self.receipt()['transfers']}, {40, 50})

    def test_missing_paint_rejects_entire_layout_before_attempt(self):
        self.run_lua('start();backpack.rows={backpack.rows[1]};drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertFalse(self.receipt()['attempted'])
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_stack_is_not_counted_as_singleton_can_availability(self):
        self.run_lua('start();backpack.rows={{12,entry(paintA,10)}};drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_occupied_zone_stores_then_installs_through_two_empty_slots(self):
        self.run_lua('''
        start("boat")
        boatPaint.rows={{20,entry(paintB,1)},{30,entry(paintA,1)}}
        until_phase(job,"preset_commit");job:step()
        assert(nativeCalls==1 and job.state=="running" and job.receipt.completed==0)
        assert(lastTransfer.target==backpack.path and lastTransfer.sourceSlot==20)
        assert(#boatPaint.rows==1 and #backpack.rows==3)
        drain(job)
        assert(#boatPaint.rows==2 and #backpack.rows==2 and nativeCalls==2)
        assert(backpack.rows[2][2].Item:get()==paintB and lastTransfer.target==boatPaint.path)
        ''')
        self.assertEqual(self.receipt()['outcome'], 'applied_verified')
        self.assertEqual([leg['direction'] for leg in self.receipt()['transfers']], ['remove', 'install'])

    def test_desired_empty_returns_installed_paint(self):
        self.run_lua('''
        start();request.preset.zones[1].paint=json.null
        pawn.PaintInventory.rows={{2,entry(paintB,1)},{7,entry(paintA,1)}}
        drain(job);assert(#backpack.rows==3 and #pawn.PaintInventory.rows==1)
        assert(backpack.rows[3][2].Item:get()==paintB and nativeCalls==1)
        ''')
        self.assertEqual(self.receipt()['outcome'], 'applied_verified')
        self.assertEqual(self.receipt()['completed'], 1)

    def test_no_room_or_lost_incoming_source_leaves_occupied_zone_untouched(self):
        for change in ('backpack.full=true', 'backpack.estimateSlot=0',
                       'backpack.rows[1][2]=entry(paintB,1)', 'backpack.admitRemove=false'):
            with self.subTest(change=change):
                self.setUp()
                self.run_lua('''
                start();pawn.PaintInventory.rows={{2,entry(paintB,1)},{7,entry(paintA,1)}}
                until_phase(job,"preset_commit")
                '''+change+'''
                drain(job);assert(nativeCalls==0 and pawn.PaintInventory.rows[1][2].Item:get()==paintB)
                ''')
                self.assertEqual(self.receipt()['outcome'], 'rejected')

    def test_uncertain_removed_can_blocks_replacement_and_new_requests(self):
        self.run_lua('''
        start();pawn.PaintInventory.rows={{2,entry(paintB,1)},{7,entry(paintA,1)}}
        nativeTransferMode="throw_after";drain(job)
        assert(job.receipt.outcome=="uncertain" and job.receipt.completed==0 and #job.receipt.transfers==1)
        job=presets.begin_apply(config,request);drain(job);assert(nativeCalls==1)
        ''')

    def test_changed_source_after_verified_removal_stops_without_rollback(self):
        self.run_lua('''
        start();pawn.PaintInventory.rows={{2,entry(paintB,1)},{7,entry(paintA,1)}}
        until_phase(job,"preset_commit");job:step()
        assert(job.receipt.transfers[1].outcome=="applied_verified")
        backpack.rows[1][2]=entry(paintB,1);drain(job)
        assert(job.receipt.outcome=="partial_stopped" and nativeCalls==1 and #pawn.PaintInventory.rows==1)
        ''')

    def test_singleton_instance_change_during_admission_cancels_before_removal(self):
        for which in ('incoming', 'installed'):
            with self.subTest(which=which):
                self.setUp()
                self.run_lua('''
                start();pawn.PaintInventory.rows={{2,entry(paintB,1)},{7,entry(paintA,1)}}
                until_phase(job,"preset_commit")
                local row='''+('backpack' if which=='incoming' else 'pawn.PaintInventory')+'''.rows[1][2]
                request.connection_guard=function() row.Data.Timestamp=row.Data.Timestamp+1;return true end
                drain(job);assert(nativeCalls==0 and job.receipt.outcome=="rejected",job.receipt.error)
                ''')

    def test_lost_instance_payload_stops_after_native_move_even_when_color_and_count_match(self):
        for which in ('return', 'equip'):
            with self.subTest(which=which):
                self.setUp()
                self.run_lua('''
                start();pawn.PaintInventory.rows={{2,entry(paintB,1)},{7,entry(paintA,1)}}
                until_phase(job,"preset_commit")
                '''+('job:step()' if which=='equip' else '')+'''
                nativeTransferMode="lose_payload";drain(job)
                assert(job.receipt.outcome=="uncertain" and job.receipt.completed==0)
                ''')
                self.assertEqual(self.value('nativeCalls'), 1 if which=='return' else 2)

    def test_native_zero_count_row_can_receive_displaced_paint(self):
        self.run_lua('''
        start();pawn.PaintInventory.rows={{2,entry(paintB,1)},{7,entry(paintA,1)}}
        backpack.rows[#backpack.rows+1]={0,entry(nil,0)}
        drain(job);assert(job.receipt.outcome=="applied_verified",job.receipt.error)
        assert(job.receipt.transfers[1].destinationSlot==0 and nativeCalls==2)
        ''')

    def test_each_native_admission_failure_rejects_before_mutation(self):
        for setup in ('backpack.admitSlot=false', 'backpack.admitRemove=false',
                      'pawn.PaintInventory.admitSlot=false', 'pawn.PaintInventory.admitItem=false',
                      'pawn.PaintInventory.admitAddSlot=false', 'pawn.PaintInventory.estimate=0',
                      'pawn.PaintInventory.estimateSlot=0'):
            with self.subTest(setup=setup):
                self.setUp()
                self.run_lua('start();job:step();' + setup + ';drain(job)')
                self.assertEqual(self.receipt()['outcome'], 'rejected')
                self.assertEqual(self.value('nativeCalls'), 0)

    def test_exception_in_admission_query_is_not_an_attempt(self):
        self.run_lua('start();function pawn.PaintInventory:CanAddToSlot() error("query unavailable") end;drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertFalse(self.receipt()['attempted'])
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_negative_current_paint_slot_is_refused(self):
        self.run_lua('start();pawn.PaintInventory.PaintSlots=native_map({{-2,{SlotDisplayName=text("Other"),MaterialSlotName=text("Material1"),PartName=text("Part1")}},{-1,{SlotDisplayName=text("Other"),MaterialSlotName=text("Material2"),PartName=text("Part2")}}});drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_unsupported_build_does_not_transfer(self):
        self.run_lua('start();request.gameBuild="other";drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_changed_target_identity_and_layout_cannot_replay_old_request(self):
        for setup in ('pawn.PaintInventory.address=pawn.PaintInventory.address+1',
                      'pawn.PaintInventory.PaintSetName=text("Different")',
                      'pawn.PaintInventory.rows={{7,entry(paintB,1)}}'):
            with self.subTest(setup=setup):
                self.setUp()
                self.run_lua('start();job:step();' + setup + ';drain(job)')
                self.assertEqual(self.receipt()['outcome'], 'rejected')
                self.assertEqual(self.value('nativeCalls'), 0)

    def test_world_change_rejects_pending_commit(self):
        self.run_lua('start();job:step();world.address=world.address+1;drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'rejected')
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_selected_boat_is_used_without_affecting_character(self):
        self.run_lua('start("boat");drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'applied_verified')
        self.assertEqual(self.value('#pawn.PaintInventory.rows'), 0)
        self.assertEqual(self.value('#boatPaint.rows'), 2)
        self.assertEqual(self.value('lastTransfer.target'), 'Paint /Game/Map.Map.Boat.PaintInventory')

    def test_boat_can_be_discovered_and_applied_without_player_or_boat_coordinates(self):
        self.run_lua('''
        function boat:K2_GetActorLocation() error("Preset discovery needs no boat position") end
        function pawn:K2_GetActorLocation() error("Preset application needs no player position") end
        start("boat");drain(job)
        ''')
        self.assertEqual(self.receipt()['outcome'], 'applied_verified')
        self.assertEqual(self.value('#boatPaint.rows'), 2)

    def test_incompatible_class_kind_set_or_logical_zone_is_rejected(self):
        for setup in ('request.preset.target.kind="boat"',
                      'request.preset.target.className="/Script/Voyage.Other"',
                      'request.preset.target.paintSetName="Other"',
                      'request.preset.zones[1].partName="Other"'):
            with self.subTest(setup=setup):
                self.setUp()
                self.run_lua('start();' + setup + ';drain(job)')
                self.assertEqual(self.receipt()['outcome'], 'rejected')
                self.assertEqual(self.value('nativeCalls'), 0)

    def test_incomplete_source_or_target_refuses_application(self):
        for setup in ('backpack.readError=true',
                      'pawn.PaintInventory.readError=true',
                      'pawn.PaintInventory.owner=boat',
                      'backpack.owner=boat'):
            with self.subTest(setup=setup):
                self.setUp()
                self.run_lua('start();' + setup + ';drain(job)')
                self.assertEqual(self.receipt()['outcome'], 'rejected')
                self.assertEqual(self.value('nativeCalls'), 0)

    def test_native_noop_exception_duplicate_or_context_change_is_uncertain_and_not_retried(self):
        for mode in ('no_change', 'throw_before', 'duplicate', 'context_change'):
            with self.subTest(mode=mode):
                self.setUp()
                self.run_lua(f'start();nativeTransferMode="{mode}";drain(job);job:step();first=job:get_receipt()')
                self.assertEqual(self.receipt()['outcome'], 'uncertain')
                self.assertEqual(self.value('nativeCalls'), 1)
                if mode != 'context_change':
                    self.run_lua('job=presets.begin_apply(config,request);drain(job)')
                    self.assertEqual(self.value('nativeCalls'), 1)

    def test_unrelated_backpack_slot_move_is_uncertain_even_with_conserved_totals(self):
        self.run_lua('start();nativeTransferMode="move_unrelated";drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'uncertain')
        self.assertIn('Unrelated backpack slot changed', self.receipt()['error'])
        self.assertEqual(self.value('nativeCalls'), 1)

    def test_exception_after_mutation_is_uncertain_even_with_expected_poststate(self):
        self.run_lua('start();nativeTransferMode="throw_after";drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'uncertain')
        self.assertEqual(self.value('#pawn.PaintInventory.rows'), 1)
        self.assertTrue(self.receipt()['transfers'][0]['postcheck']['quantitiesConserved'])
        self.run_lua('job=presets.begin_apply(config,request);drain(job)')
        self.assertEqual(self.value('nativeCalls'), 1)

    def test_unexpected_native_return_is_uncertain_even_with_expected_poststate(self):
        for result in ('0', '-1', 'true', 'nil'):
            with self.subTest(result=result):
                self.setUp()
                self.run_lua('start();transferReturn=' + result + ';drain(job)')
                self.assertEqual(self.receipt()['outcome'], 'uncertain')
                self.assertEqual(self.value('nativeCalls'), 1)
                self.assertEqual(self.value('#pawn.PaintInventory.rows'), 1)

    def test_cancel_before_commit_performs_no_mutation(self):
        self.run_lua('start();job:step();job:cancel("closed");drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'cancelled')
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_cancel_after_one_completed_zone_preserves_partial_receipt(self):
        self.run_lua('start();until_phase(job,"preset_commit");job:step();job:cancel("closed");drain(job)')
        self.assertEqual(self.receipt()['outcome'], 'partial_cancelled')
        self.assertEqual(self.receipt()['completed'], 1)
        self.assertEqual(self.value('nativeCalls'), 1)

    def test_exhausted_work_budget_does_not_capture_or_apply(self):
        self.run_lua('start();local b=budget_module.new(1,1);b:consume();job:step(b)')
        self.assertEqual(self.value('job.processed'), 0)
        self.assertEqual(self.value('nativeCalls'), 0)

    def test_capture_and_application_retain_only_primitive_descriptors_between_steps(self):
        self.run_lua('''
            local capture=presets.begin_capture(config)
            for i=1,10000 do
                local p=capture:step(budget_module.new(10000,1))
                assert(plain_job(capture),"native handle retained by capture")
                if p.state~="running" then assert(p.state=="done",p.error);break end
            end
            start()
            for i=1,10000 do
                local p=job:step(budget_module.new(10000,1))
                assert(plain_job(job),"native handle retained by application")
                if p.state~="running" then break end
            end
        ''')
        self.assertEqual(self.receipt()['outcome'], 'applied_verified')

    def test_capture_and_save_workflow_use_fresh_logical_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'PaintManager.presets.json'
            self.lua.globals().preset_file = path.as_posix()
            self.run_lua('''
                refresh();local target=view.targets[1];local other=view.targets[2]
                pawn.PaintInventory.rows={{7,entry(paintB,1)}}
                FindAllOf=function() error("Save must not discover other components or boxes") end
                local before=inventory_reads
                job=workflow.begin(config,preset_file,"preset_save",{targetId=target.id,target=target,view=view,
                    name="Fresh",newId="preset-fresh",gameBuild=config.reference_build,
                    contextToken=view.contextToken})
                result=drain(job);assert(result.state=="done",result.error)
                assert(result.view.targets[2]==other and result.savedId=="preset-fresh" and inventory_reads==before+1)
            ''')
            self.assertEqual(self.value('result.state'), 'done', self.value('result.error'))
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(len(data['presets']), 1)
            painted = [z['paint'] for z in data['presets'][0]['zones'] if z['paint'] is not None]
            self.assertEqual([p['assetName'] for p in painted], ['DA_Paint_Suomi'])
            self.assertEqual(self.value('nativeCalls'), 0)

    def test_save_skips_a_match_found_in_a_fresh_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'PaintManager.presets.json'
            self.lua.globals().preset_file = path.as_posix()
            original = self.run_lua('''
            pawn.PaintInventory.rows={{7,entry(paintA,1)}}
            local target=refresh().targets[1]
            local saved=copy(presets.from_target(target,"Only copy","preset-original",config.reference_build))
            saved.zones=json.array({saved.zones[2],saved.zones[1]})
            pawn.PaintInventory.rows={}
            return json.encode({schemaVersion=1,presets=json.array({saved})})
            ''')
            path.write_text(original, encoding='utf-8')
            self.run_lua('''
            local target=refresh().targets[1]
            pawn.PaintInventory.rows={{7,entry(paintA,1)}}
            job=workflow.begin(config,preset_file,"preset_save",{
                targetId=target.id,name="Duplicate",gameBuild=config.reference_build,
                contextToken=view.contextToken})
            result=drain(job)
            ''')
            self.assertEqual(self.value('result.state'), 'done', self.value('result.error'))
            self.assertTrue(self.value('result.saveSkipped'))
            self.assertEqual(self.value('#result.view.library.presets'), 1)
            self.assertEqual(path.read_text(encoding='utf-8'), original)
            self.assertEqual(self.value('nativeCalls'), 0)

    def test_ship_save_rechecks_connection_before_storing(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'PaintManager.presets.json'
            self.lua.globals().preset_file = path.as_posix()
            self.run_lua('''
            local target=refresh().targets[2]
            job=workflow.begin(config,preset_file,"preset_save",{
                targetId=target.id,name="Ship plan",newId="preset-ship",
                gameBuild=config.reference_build,contextToken=view.contextToken,
                connection_guard=function() return false,"Ship changed" end})
            result=drain(job)
            ''')
            self.assertEqual(self.value('result.state'), 'error')
            self.assertIn('Ship changed', self.value('result.error'))
            self.assertFalse(path.exists())

    def test_apply_workflow_resolves_saved_id_and_recaptures_after_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'PaintManager.presets.json'
            self.lua.globals().preset_file = path.as_posix()
            raw = self.run_lua('request=request_for();return json.encode({schemaVersion=1,presets=json.array({request.preset})})')
            path.write_text(raw, encoding='utf-8')
            self.run_lua('job=workflow.begin(config,preset_file,"preset_apply",{presetId="preset-test",target=request.target,gameBuild=request.gameBuild,contextToken=request.contextToken});result=drain(job)')
            self.assertEqual(self.value('result.state'), 'done', self.value('result.error'))
            self.assertEqual(self.value('result.receipt.outcome'), 'applied_verified')
            self.assertEqual(self.value('nativeCalls'), 2)
            self.assertEqual(self.value('result.view.targets[1].logicalZones[1].paint.assetName'), 'DA_Paint_Canada')

    def test_save_load_and_apply_real_workflow_preserves_logical_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'PaintManager.presets.json'
            self.lua.globals().preset_file = path.as_posix()
            self.run_lua('''
                pawn.PaintInventory.rows={{2,entry(paintA,1)},{7,entry(paintA,1)}}
                refresh();local target=view.targets[1]
                job=workflow.begin(config,preset_file,"preset_save",{targetId=target.id,name="Restorable",newId="preset-roundtrip",
                    gameBuild=config.reference_build,contextToken=view.contextToken})
                result=drain(job);assert(result.state=="done",result.error)
            ''')
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['presets'][0]['id'], 'preset-roundtrip')
            self.assertTrue(all(z['paint']['assetName'] == 'DA_Paint_Canada' for z in data['presets'][0]['zones']))
            for forbidden in ('runtimeRef', 'ownerRuntimeRef', 'contextToken', 'slotIndex', 'address'):
                self.assertNotIn(forbidden, path.read_text(encoding='utf-8'))
            self.run_lua('''
                local target=result.view.targets[1]
                pawn.PaintInventory.rows={}
                backpack.rows={{12,entry(paintA,1)},{13,entry(paintA,1)}}
                job=workflow.begin(config,preset_file,"preset_apply",{presetId="preset-roundtrip",target=target,
                    gameBuild=config.reference_build,contextToken=result.view.contextToken})
                result=drain(job)
            ''')
            self.assertEqual(self.value('result.state'), 'done', self.value('result.error'))
            self.assertEqual(self.value('result.receipt.outcome'), 'applied_verified')
            self.assertEqual(self.value('nativeCalls'), 2)
            self.assertEqual(self.value('#result.view.targets[1].logicalZones'), 2)
            self.assertEqual(data, json.loads(path.read_text(encoding='utf-8')))

    def test_missing_saved_id_refuses_without_capture_or_transfer(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.lua.globals().preset_file = (Path(temporary) / 'PaintManager.presets.json').as_posix()
            self.run_lua('request=request_for();job=workflow.begin(config,preset_file,"preset_apply",{presetId="absent",target=request.target,gameBuild=request.gameBuild,contextToken=request.contextToken});result=drain(job)')
            self.assertEqual(self.value('result.state'), 'error')
            self.assertIn('no longer exists', self.value('result.error'))
            self.assertEqual(self.value('nativeCalls'), 0)


if __name__ == '__main__':
    unittest.main()
