"""Exercise the game-thread transfer job with real collection and mock native slots."""
import unittest
from lupa.lua54 import LuaRuntime
from test_collection import FIXTURE, SCRIPTS

TRANSFER = r'''
transfer=require("paintmanager.paint_transfer")
local base_find_all=FindAllOf
box_queries=0
function FindAllOf(kind)
    if kind~="BP_Inventory_PlayerBox_C" then return base_find_all(kind) end
    box_queries=box_queries+1
    local actors={}
    for _,c in ipairs(containers) do actors[#actors+1]=c:GetOwner() end
    return actors
end
ship=require("paintmanager.ship_scope")
ship.contains=function(s,p)
    local distance=require("paintmanager.safe").distance_cm(s.center,p)
    return distance and distance<=s.radiusCm,distance
end
fixtureShipRadius=3500
function controller:IsLocalController() return true end
function can(item,stamp,count)
    local row=entry(item,count or 1)
    local function empty() return setmetatable({ForEach=function() end},{__len=function() return 0 end}) end
    row.Data.Timestamp,row.Data.Flags,row.Data.ItemData,row.Data.ItemDataMap=stamp,0,empty(),empty()
    return row
end
function opaque(item,stamp,count)
    local row=can(item,stamp,count)
    row.Data.ItemData=setmetatable({ForEach=function(_,f) f(1,wrap(49)) end},{__len=function() return 1 end})
    return row
end
local basePaint = paint
function paint(...)
    local item=basePaint(...)
    item.Type={Name=text("Item")}
    return item
end
A=paint("DA_Paint_A","Amber");B=paint("DA_Paint_B","Blue")
function setup_inventory(c,owner,slots)
    c.slots,c.limit,c.reads=slots or {},12,0
    c.Items={ForEach=function(_,f)
        c.reads=c.reads+1
        for i,e in pairs(c.slots) do if f(wrap(i),wrap(e)) then break end end
    end}
    function owner:HasAnyFlags() return false end
    function c:HasAnyFlags() return false end
    function c:GetOwner() return owner end
    function c:GetNextEmptySlot()
        for i=0,self.limit-1 do if not self.slots[i] then return i end end
        return -1
    end
    function c:IsValidSlotIndex(i) return i>=0 and i<self.limit end
    function c:CanRemoveSlot(i) return self.slots[i]~=nil end
    function c:CanAddItem(it) return not self.denied or self.denied~=it end
    function c:CanAddToSlot(i,it) return not self.slots[i] end
    function c:EstimateAddAmount(it,n) return math.min(n,self.weightSpace or n) end
    function c:EstimateAddAmountToSlot(i,it,n) return self.slots[i] and 0 or math.min(n,self.stackLimit or n) end
    function c:TransferSlot(src,s,t,n)
        moves=moves+1
        local original=src.slots[s]
        assert(t>=0 and self.slots[t]==nil and n>0 and original.Data.ItemCount>=n)
        if original.Data.ItemCount==n then self.slots[t],src.slots[s]=original,nil
        else
            local copy={}
            for k,v in pairs(original) do copy[k]=v end
            copy.Data={}
            for k,v in pairs(original.Data) do copy.Data[k]=v end
            original.Data.ItemCount=original.Data.ItemCount-n
            copy.Data.ItemCount=n
            self.slots[t]=copy
        end
        if self.weightSpace then self.weightSpace=self.weightSpace-n end
        if after_native then after_native(self,src,s,t,n) end
        return n
    end
    return c
end
bag=setup_inventory(obj("Inventory /Game/Map.Map.Pawn.Bag","VoyageInventoryWeightLimitedComponent"),pawn)
pawn.WeightInventory=bag
function controller:GetInventoryComponent(k) assert(k==1);return bag end
function stock(label,id,distance,slots,kind)
    local o,c=box(label,distance,{},kind)
    o.persistentId,o.VoyageInventoryWeightLimited,o.IsLocked,o.InteractEnabled=id,c,false,true
    c.Access=2
    setup_inventory(c,o,slots)
    return c,o
end
id1,id2="0000000000000010","0000000000000020"
token=collection.context();moves=0
allowed=true
ledger={records={}}
function ledger:entries()
    local out={}
    for _,r in pairs(self.records) do out[#out+1]=r end
    table.sort(out,function(a,b) return a.slot<b.slot end);return out
end
function ledger:current() return true end
function ledger:prepare(dir,r)
    assert(not self.ticket)
    if dir=="return" then assert(self.records[r.slot]) end
    self.ticket={dir=dir,r=r}
    if prepared then prepared(self.ticket) end
    return self.ticket
end
function ledger:commit(ticket,r,remaining)
    assert(ticket==self.ticket)
    self.records[r.slot]=ticket.dir=="take" and r or nil
    if ticket.dir=="return" and remaining>0 then
        local prior={}
        for k,v in pairs(r) do prior[k]=v end
        prior.count=remaining
        self.records[r.slot]=prior
    end
    self.ticket=nil
    return true
end
function ledger:cancel(ticket) assert(ticket==self.ticket);self.ticket=nil;return true end
function new_job(dir,item,quantity)
    return transfer.begin(config,{contextToken=token,gameBuild=config.reference_build,direction=dir,
        guard=function() return allowed,"closed" end,ledger=ledger,itemPath=item and item.path,quantity=quantity})
end
function finish(job)
    for _=1,1000 do
        local before=moves
        local result=job:step()
        assert(moves<=before+1)
        if result.state~="running" then return result end
    end
    error("transfer did not finish")
end
function run(dir,item,quantity) return finish(new_job(dir,item,quantity)) end
'''


class PaintTransferTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + TRANSFER)

    def test_writable_open_boxes_are_independent_of_the_world_interaction_prompt(self):
        self.lua.execute('''
        local context=collection.native_context()
        for i,kind in ipairs({"BP_Inventory_PlayerBox_Small_C","BP_Inventory_PlayerBox_C","BP_Inventory_PlayerBox_Large_C"}) do
            local box,owner=stock("Opened"..i,id1,9000,{[0]=can(A,1)},kind)
            owner.InteractEnabled=false
            assert(not transfer.accessible_box(box,context))
            for _,access in ipairs({1,2}) do
                box.Access=access
                assert(transfer.open_writable_box(box,context)==owner)
            end
            bag.slots={[0]=can(B,2,1)}
            local paint=transfer.inventory_paints(bag,config)[1]
            assert(transfer.deposit_open_slot(config,token,paint,function() return bag,box end,function() return true end)==1)
            assert(not next(bag.slots) and box.slots[1].Item:get()==B)
        end
        assert(moves==3 and transfer.can_transfer(token))
        ''')

    def test_open_box_rejections_name_the_actual_failed_check_and_do_not_move(self):
        cases = {
            'owner.IsLocked=true': 'is locked',
            'owner.IsLocked=nil': 'lock state is unreadable',
            'box.Access=0': 'Access=0',
            'box.Access=3': 'Access=3',
            'box.Access=nil': 'Access=nil',
            'owner.VoyageInventoryWeightLimited=bag': 'declared weight inventory differs',
            'function owner:GetWorld() return pawn end': 'different world',
            'function box:GetOwner() return pawn end': 'not a Small, Medium or Large player box',
            'owner.valid=false': 'no live owner',
            'function box:HasAnyFlags() return true end': 'box inventory is a template',
            'function owner:HasAnyFlags() return true end': 'box owner is a template',
            'box.kind="VoyageFuelInventoryComponent"': 'not a valid native weight-limited inventory',
        }
        for change,reason in cases.items():
            with self.subTest(change=change):
                self.setUp()
                self.lua.globals().expected=reason
                self.lua.execute('box,owner=stock("Opened",id1,1,{[0]=can(A,1)});bag.slots[0]=can(B,2);'+change+'''
                local actual,reason=transfer.open_writable_box(box,collection.native_context())
                assert(actual==nil and reason:find(expected,1,true),tostring(reason))
                local paint=transfer.inventory_paints(bag,config)[1]
                local ok,err=pcall(transfer.deposit_open_slot,config,token,paint,function()return bag,box end,function()return true end)
                assert(not ok and tostring(err):find(expected,1,true) and moves==0 and transfer.can_transfer(token))
                ''')

    def test_open_box_rechecks_permission_before_and_after_the_native_move(self):
        for when in ('before','after'):
            with self.subTest(when=when):
                self.setUp()
                self.lua.globals().when=when
                self.lua.execute('''
                local box,owner=stock("Opened",id1,1,{[0]=can(A,1)})
                owner.InteractEnabled=false
                bag.slots[0]=can(B,2)
                local paint=transfer.inventory_paints(bag,config)[1]
                local function revoke() box.Access=0 end
                if when=="before" then
                    local estimate=box.EstimateAddAmountToSlot
                    function box:EstimateAddAmountToSlot(...) revoke();return estimate(self,...) end
                else after_native=revoke end
                local ok,count,reason=pcall(transfer.deposit_open_slot,config,token,paint,function()return bag,box end,function()return true end)
                if when=="before" then
                    assert(not ok and tostring(count):find("Access=0",1,true) and moves==0 and transfer.can_transfer(token))
                else
                    assert(ok and count==nil and reason:find("Access=0",1,true) and moves==1 and not transfer.can_transfer(token))
                end
                ''')

    def test_capture_includes_backpack_only_color_and_excludes_it_from_box_hints(self):
        self.lua.execute('''
        local source=stock("Stock",id1,2,{[0]=can(A,1,2)})
        containers[#containers+1]=bag -- also returned by global weight inventory discovery
        bag.slots={[1]=can(A,2,2),[2]=can(B,3,4)}
        local seed={contextToken=token,status="catalog_prepared",catalogComplete=true,recipeCatalogComplete=true,rows={}}
        for _,mode in ipairs({"paints","report"}) do
            local job=collection.begin(config,{catalog=seed,presentation=mode,background=false})
            local progress
            repeat progress=job:step() until progress.state~="running"
            assert(progress.model,progress.error)
            local m=progress.model
            assert(m.physicalCount==2 and m.backpackPhysicalCount==6 and m.combinedPhysicalCount==8)
            assert(m.sourceCount==1 and #m.stockLocationSources==1 and m.backpackCounts[B.path]==4)
            local rows={};for _,r in ipairs(m.rows) do rows[r.id]=r end
            assert(rows[A.path].count==2 and #rows[A.path].stockLocationHints==1)
            assert(rows[B.path].count==0 and #rows[B.path].stockLocationHints==0 and rows[B.path].recipeState=="locked")
            assert(rows[B.path].runtimeRef.id==collection.runtime_ref(B).id)
        end
        ''')

    def test_backpack_endpoint_and_native_paint_identity_must_be_current(self):
        self.lua.execute('''
        assert(transfer.player_backpack(token)==bag and not transfer.player_backpack("other"))
        local original=bag
        local impostor=stock("Other",id1,2,{[0]=can(A,1,4)})
        bag=impostor;assert(not transfer.player_backpack(token))
        pawn.WeightInventory=impostor;assert(not transfer.player_backpack(token))
        pawn.WeightInventory=original;bag=original
        bag.slots={[0]=can(A,1,4)}
        bag.slots[0].AssetType.Name=text("OtherType")
        assert(not transfer.backpack(config,token))
        bag.slots={};assert(not next(assert(transfer.backpack(config,token)).counts))
        ''')

    def test_selected_and_lean_bulk_visit_verified_box_hints_first(self):
        self.lua.execute('''
        local empty={}
        for i=1,21 do empty[i]=stock("Empty"..i,string.format("%016x",i+100),i,{}) end
        local unrelated=stock("Quest",id1,1,{[0]=can(A,5)},"QuestBox_C")
        local source=stock("Paints",id1,2,{[0]=can(A,70,2)},"BP_Inventory_PlayerBox_Small_C")
        local model={stockSourcesOmitted=true,sources={},stockLocationSources={{persistentId=id1,paintCounts={[A.path]=2}}},
            rows={{id=A.path,stockLocationHints={1}}}}
        local function first_move(job)
            job.request.model=model
            local prior=moves
            for i=1,5 do
                local r=job:step()
                assert(r.state~="error",r.error)
                if moves>prior then return end
            end
            error("A known paint box waited behind unrelated inventory work")
        end
        local single=new_job("take",A,1)
        first_move(single)
        assert(finish(single).outcome=="completed" and bag.slots[0].Data.ItemCount==1)
        assert(box_queries==1 and scans==0 and source.reads==3 and bag.reads==4)
        local bulk=new_job("take")
        first_move(bulk)
        assert(box_queries==2 and scans==0 and not source.slots[0] and unrelated.reads==0)
        for _,c in ipairs(empty) do assert(c.reads==0) end
        assert(finish(bulk).moved==1)
        ''')

    def test_failed_or_ambiguous_discovery_cannot_authorize_a_hinted_box(self):
        self.lua.execute('''
        local first=stock("Hint",id1,2,{[0]=can(A,1)})
        local c,other=stock("Other",id1,5000,{})
        local function hinted()
            local job=new_job("take",A,1)
            job.request.model={stockLocationSources={{persistentId=id1}},rows={{id=A.path,stockLocationHints={1}}}}
            return job
        end
        assert(finish(hinted()).moved==0 and first.reads==0)
        function other:GetWorld() error("streaming") end
        assert(finish(hinted()).moved==0 and first.reads==0)
        other.persistentId=nil
        assert(finish(hinted()).state=="error" and moves==0)
        other.persistentId=id2
        local job=hinted();assert(job:step().phase=="index")
        other.valid=false
        assert(finish(job).state=="error" and moves==0 and not next(ledger.records))
        ''')

    def test_templates_and_confirmed_zero_ids_do_not_block_other_boxes(self):
        self.lua.execute('''
        local good=stock("Source",id1,1,{[0]=can(A,1)})
        local _,template=stock("Template",nil,1,{[0]=can(B,2)})
        function template:HasAnyFlags() return true end
        local worldReads=0
        function template:GetWorld() worldReads=worldReads+1;error("no template world") end
        local zero,owner=stock("NoId",nil,5000,{[0]=can(B,3)})
        local original=ship.persistent_actor_id
        function ship.persistent_actor_id(actor)
            assert(actor~=template,"Template reached persistent identity lookup")
            if actor==owner then return nil,"Persistent identity is zero or invalid","zero" end
            return original(actor)
        end
        local r=run("take")
        assert(r.outcome=="completed" and r.moved==1 and zero.reads==0 and worldReads==0,r.error or r.reason)
        assert(run("return").moved==1 and zero.reads==0)
        function owner:K2_GetActorLocation() return {X=100,Y=0,Z=0} end
        r=run("take")
        assert(r.outcome=="partial" and r.incomplete and r.moved==1 and zero.reads==0 and worldReads==0)
        ''')

    def test_changes_report_last_committed_full_counts_per_box_and_hide_uncertain_result(self):
        self.lua.execute('''
        local source,owner=stock("First",id1,123,{[0]=can(A,24,7),[1]=can(B,48,2)},"BP_Inventory_PlayerBox_Large_C")
        bag.stackLimit=2
        local job=new_job("take",A,5)
        assert(finish(job).moved==5 and moves==3)
        local changes=job:get_stock_changes()
        assert(#changes==1 and changes[1].persistentId==id1 and changes[1].kind=="Large")
        assert(changes[1].ownerRef.name==owner.path and changes[1].componentRef.name==source.path)
        assert(changes[1].location.X==123 and changes[1].counts[A.path]==2 and changes[1].counts[B.path]==2)
        function owner:K2_GetActorLocation() error("no position") end
        job=new_job("return")
        assert(finish(job).moved==5)
        changes=job:get_stock_changes()
        assert(#changes==1 and changes[1].location==nil and changes[1].counts[A.path]==7 and changes[1].counts[B.path]==2)
        function owner:K2_GetActorLocation() return {X=124,Y=0,Z=0} end
        local prior=moves
        after_native=function(dst,src,s,t) if moves==prior+2 then src.slots[s]=dst.slots[t] end end
        job=new_job("take")
        assert(finish(job).outcome=="uncertain" and job.moved>0 and job:get_stock_changes()==nil)
        ''')

    def test_take_every_can_including_duplicates_and_return_without_ship_signal(self):
        self.lua.execute('''
        c1,o1=stock("First",id1,3500,{[1]=can(A,91),[2]=can(B,50,3)})
        c2,o2=stock("Second",id2,20,{[5]=can(A,91)})
        remote=stock("Far","0000000000000030",3501,{[4]=can(B,77)})
        local r=run("take");assert(r.outcome=="completed" and r.moved==5,r.error or r.reason)
        local bagView=assert(transfer.backpack(config,token))
        assert(bagView.counts[A.path]==2 and bagView.counts[B.path]==3)
        assert(transfer.returnable(bagView,ledger:entries())[A.path]==2)
        assert(#ledger:entries()==3 and c1:GetNextEmptySlot()==0 and remote.slots[4])
        fixtureShipUnavailable=true
        function o2:K2_GetActorLocation() error("Return inspected radius") end
        r=run("return");assert(r.outcome=="completed" and r.moved==5,r.error or r.reason)
        assert(not next(bag.slots) and not next(ledger.records))
        assert(c1.slots[0].Data.Timestamp==91 and c1.slots[1].Data.ItemCount==3 and c2.slots[0].Data.Timestamp==91)
        ''')

    def test_target_of_one_searches_past_an_opaque_stack_and_stops_when_met(self):
        self.lua.execute('''
        first=stock("One",id1,10,{[0]=opaque(A,10,3)})
        second=stock("Two",id2,20,{[0]=can(A,11)})
        local r=run("take",A,1)
        assert(r.outcome=="completed" and moves==1 and first.slots[0].Data.ItemCount==3)
        assert(ledger.records[0].boxId==id2)
        r=run("take",A,1);assert(r.moved==0 and moves==1)
        ''')

    def test_default_tops_up_to_five_from_several_boxes_and_keeps_personal_cans(self):
        self.lua.execute('''
        bag.slots[9]=can(A,99,2)
        local first=stock("First",id1,10,{[2]=can(A,10),[3]=can(B,8)})
        local second=stock("Second",id2,20,{[4]=can(A,11,7)})
        local r=run("take",A)
        assert(r.outcome=="completed" and r.moved==3 and r.skipped==0,r.reason or r.error)
        assert(transfer.backpack(config,token).counts[A.path]==5 and first.slots[3])
        assert(not first.slots[2] and second.slots[4].Data.ItemCount==5)
        assert(ledger.records[0].boxId==id1 and ledger.records[1].boxId==id2)
        local already=run("take",A);assert(already.moved==0 and already.skipped==0 and moves==2)
        assert(run("return").moved==3 and bag.slots[9].Data.ItemCount==2)
        ''')

    def test_target_shortfall_uses_live_backpack_count_after_capacity_or_short_stock(self):
        self.lua.execute('''
        bag.slots[9]=can(A,90,2)
        local first=stock("First",id1,10,{[0]=opaque(A,10,8),[1]=can(A,12,4)})
        bag.weightSpace=2
        local limited=run("take",A)
        assert(limited.outcome=="partial" and limited.moved==2 and limited.skipped==1)
        assert(first.slots[0].Data.ItemCount==8 and first.slots[1].Data.ItemCount==2)
        bag.weightSpace=20;first.slots[0]=nil
        local short=run("take",A,12)
        assert(short.outcome=="partial" and short.moved==2 and short.skipped==6)
        assert(transfer.backpack(config,token).counts[A.path]==6)
        ''')

    def test_five_from_empty_and_custom_target_continue_in_slot_limited_portions(self):
        self.lua.execute('''
        local source=stock("First",id1,10,{[0]=can(A,34,17)})
        bag.stackLimit=2
        local r=run("take",A)
        assert(r.outcome=="completed" and r.moved==5 and moves==3)
        assert(source.slots[0].Data.ItemCount==12 and #ledger:entries()==3)
        r=run("take",A,11.0)
        assert(r.outcome=="completed" and r.moved==6 and moves==6)
        assert(transfer.backpack(config,token).counts[A.path]==11 and source.slots[0].Data.ItemCount==6)
        ''')

    def test_all_of_selected_color_counts_eligible_duplicate_cans_and_skips_exactly(self):
        self.lua.execute('''
        bag.slots[9]=can(A,99,2)
        local a=stock("First",id1,10,{[0]=can(A,1,7),[1]=can(B,2)})
        local b=stock("Second",id2,10,{[0]=can(A,3,3),[1]=can(B,4)})
        bag.weightSpace=8
        local r=run("take",A,"all")
        assert(r.outcome=="partial" and r.moved==8 and r.skipped==2,r.reason or r.error)
        assert(transfer.backpack(config,token).counts[A.path]==10 and a.slots[1] and b.slots[1])
        assert(b.slots[0].Data.ItemCount==2)
        ''')

    def test_live_backpack_change_resizes_unattempted_intent_and_cancellation_preserves_origin(self):
        self.lua.execute('''
        local source=stock("First",id1,10,{[0]=can(A,10,12)})
        prepared=function() bag.slots[9]=can(A,90,3);prepared=nil end
        local r=run("take",A)
        assert(r.outcome=="completed" and r.moved==2 and moves==1,r.reason or r.error)
        assert(source.slots[0].Data.ItemCount==10 and ledger.records[0].count==2)
        bag.stackLimit=1
        local next_job=new_job("take",A,12)
        while moves<2 do assert(next_job:step().state=="running") end
        next_job:cancel("closed")
        assert(finish(next_job).state=="cancelled" and moves==2 and source.slots[0].Data.ItemCount==9)
        assert(ledger.records[1].count==1 and run("return").moved==3)
        ''')

    def test_return_skips_missing_box_and_untagged_paint_then_returns_other_can(self):
        self.lua.execute('''
        a=stock("First",id1,1,{[0]=can(A,1)})
        b=stock("Second",id2,1,{[0]=can(B,2)})
        assert(run("take").moved==2)
        bag.slots[9]=can(A,111)
        containers={b}
        local r=run("return")
        assert(r.outcome=="partial" and r.moved==1 and r.skipped==1,r.error or r.reason)
        assert(bag.slots[9] and b.slots[0].Data.Timestamp==2)
        assert(#ledger:entries()==1 and ledger:entries()[1].boxId==id1)
        ''')

    def test_slot_replacement_and_duplicate_container_id_cannot_reroute_a_return(self):
        self.lua.execute('''
        a=stock("First",id1,1,{[0]=can(A,1)})
        assert(run("take").moved==1)
        bag.slots[0]=can(A,99)
        local view=assert(transfer.backpack(config,token))
        assert(not next(transfer.returnable(view,ledger:entries())))
        assert(run("return").moved==0)
        bag.slots[0]=can(A,1)
        duplicate=stock("Duplicate",id1,5000,{})
        assert(run("return").moved==0 and moves==1)
        ''')

    def test_capacity_skips_one_stack_and_continues_to_a_lighter_can(self):
        self.lua.execute('''
        a=stock("First",id1,1,{[0]=opaque(A,1,3),[1]=can(B,2)})
        bag.weightSpace=2
        local r=run("take")
        assert(r.outcome=="partial" and r.moved==1 and r.skipped==3,r.error or r.reason)
        assert(a.slots[0].Data.ItemCount==3 and bag.slots[0].Data.Timestamp==2)
        ''')

    def test_single_take_and_return_a_plain_stack_portion_with_exact_origin(self):
        self.lua.execute('''
        a=stock("First",id1,1,{[4]=can(A,41,3)})
        assert(run("take",A,1).moved==1 and a.slots[4].Data.ItemCount==2 and ledger.records[0].count==1)
        assert(run("take").moved==2 and ledger.records[1].count==2)
        assert(run("return",A).moved==1 and not ledger.records[0])
        assert(run("return",A).moved==1 and ledger.records[1].count==1)
        assert(ledger.records[1].boxId==id1 and bag.slots[1].Data.ItemCount==1)
        assert(run("return",A).moved==1 and not next(ledger.records))
        ''')

    def test_weight_limited_bulk_finishes_each_slot_within_physical_capacity(self):
        self.lua.execute('''
        a=stock("First",id1,1,{[6]=can(A,101,7)})
        bag.weightSpace,bag.stackLimit=5,2
        local r=run("take")
        assert(r.outcome=="partial" and r.moved==5 and r.skipped==2,r.error or r.reason)
        assert(a.slots[6].Data.ItemCount==2 and #ledger:entries()==3)
        a.weightSpace=3
        r=run("return")
        assert(r.outcome=="partial" and r.moved==3 and r.skipped==2,r.error or r.reason)
        assert(ledger.records[1].count==1 and ledger.records[2].count==1)
        a.weightSpace=2
        assert(run("return").moved==2 and not next(ledger.records))
        ''')

    def test_selected_return_searches_past_a_stack_with_opaque_payload(self):
        self.lua.execute('''
        a=stock("First",id1,1,{[0]=opaque(A,1,3),[1]=can(A,2)})
        assert(run("take").moved==4)
        local r=run("return",A)
        assert(r.outcome=="completed" and r.moved==1 and bag.slots[0].Data.ItemCount==3)
        r=run("return",A)
        assert(r.moved==0 and r.skipped==1 and r.reason:find("instance data"))
        assert(run("return").moved==3 and not next(ledger.records))
        ''')

    def test_live_slot_change_during_admission_cancels_without_entering_native_transfer(self):
        self.lua.execute('''
        a=stock("First",id1,1,{[0]=can(A,1)})
        function bag:EstimateAddAmountToSlot(i,it,n)
            if ledger.ticket then a.slots[0]=can(B,111) end
            return n
        end
        local r=run("take")
        assert(r.moved==0 and moves==0 and not ledger.ticket and a.slots[0].Data.Timestamp==111)
        ''')

    def test_observed_native_timestamp_is_bound_to_the_moved_can(self):
        self.lua.execute('''
        a=stock("First",id1,1,{[0]=opaque(A,99,2)})
        after_native=function(dst,src,s,t) dst.slots[t].Data.Timestamp=1000+moves end
        local r=run("take")
        assert(r.moved==2 and bag.slots[0].Data.Timestamp==1001,r.error)
        assert(ledger.records[0].fingerprint:sub(1,16)==string.format("%016x",1001))
        assert(run("return").moved==2 and a.slots[0].Data.Timestamp==1002)
        a.slots[0].Data.Flags=1
        r=run("take")
        assert(r.outcome=="uncertain" and not next(ledger.records))
        ''')

    def test_ambiguous_personal_can_or_invalid_admission_cannot_authorize_a_move(self):
        self.lua.execute('''
        a=stock("First",id1,1,{[0]=can(A,88)})
        function bag:EstimateAddAmount(it,n) return n+1 end
        assert(run("take").moved==0 and not ledger.ticket)
        function bag:EstimateAddAmount(it,n) return n end
        assert(run("take").moved==1)
        bag.slots[8]=can(A,88)
        assert(not next(transfer.returnable(transfer.backpack(config,token),ledger:entries())))
        assert(run("return").moved==0 and moves==1)
        bag.slots[8].Data.ItemData=nil
        assert(run("return").moved==0)
        bag.slots[8]=nil
        assert(run("return").moved==1)
        ''')

    def test_unreadable_box_does_not_hide_other_verified_origins(self):
        self.lua.execute('''
        a,o=stock("First",id1,1,{[0]=can(A,1)})
        b=stock("Second",id2,1,{[0]=can(B,2)})
        assert(run("take").moved==2)
        function o:GetWorld() error("unloaded") end
        local r=run("return")
        assert(r.outcome=="partial" and r.moved==1 and r.skipped==1 and r.incomplete)
        assert(#ledger:entries()==1 and bag.slots[0].Data.Timestamp==1)
        ''')

    def test_revalidate_after_origin_write_and_never_retry_after_uncertain_native_result(self):
        self.lua.execute('''
        a,o=stock("First",id1,1,{[0]=can(A,11)})
        prepared=function(ticket) o.IsLocked=true end
        assert(run("take").moved==0 and moves==0 and not ledger.ticket)
        prepared=nil;o.IsLocked=false
        after_native=function(dst,src,s,t) src.slots[s]=dst.slots[t] end
        local r=run("take")
        assert(r.outcome=="uncertain" and r.state=="error" and moves==1 and ledger.ticket)
        assert(run("take").state=="error" and moves==1)
        ''')

    def test_closed_window_and_unknown_build_do_not_move_inventory(self):
        self.lua.execute('''
        stock("First",id1,1,{[0]=can(A,11)})
        local job=new_job("take"); job.request.gameBuild="other"
        assert(finish(job).state=="error")
        job=new_job("take");job:step();job:cancel("closed")
        assert(finish(job).state=="cancelled" and moves==0)
        ''')
