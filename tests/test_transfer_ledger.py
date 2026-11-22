"""Actual Lua transfer origins, scoped to a single ordinary game save."""
import unittest

from lupa.lua54 import LuaRuntime

from test_collection import FIXTURE as COLLECTION
from test_paint_transfer import TRANSFER
from test_save_bridge import FIXTURE, SCRIPTS


ORIGINS = r'''
scope=bridge.capture_context().scopeId
native_storage_context=bridge.storage_context
generation=4
storage_ready=true
account="76561198000000001"
local root_dir="/fixture/Saved/SaveGames"
local player=object("LocalPlayer /Player","/Script/Engine.LocalPlayer",71)
player.PlayerController=controller;controller.Player=player
local resolver=object("SaveGame /Resolver","/Script/Voyage.VoyageSaveGame",72)
function resolver:GetSaveGameUserId(value) assert(value==player);return account end
function resolver:GetSaveGameRootDirectory() return root_dir end
local find_service=StaticFindObject
function StaticFindObject(path)
    if path=="/Script/Voyage.Default__VoyageSaveGame" then return resolver end
    return find_service(path)
end
package.loaded["paintmanager.save_lifecycle"]={
    is_ready=function(expected,id) return storage_ready and expected==token and id==scope,generation end,
    admit_account=function(expected,id,user,path)
        return storage_ready and expected==token and id==scope and user==account
            and path==root_dir.."/"..account.."/PaintManager.library.json"
    end,
}
function bridge.storage_context(expected)
    assert(storage_ready and token==expected,"Save is loading")
    return {status="ready",scopeId=scope,contextToken=token,accountId=account,loadGeneration=generation,
        libraryPath=root_dir.."/"..account.."/PaintManager.library.json"}
end
function FName(name,mode)
    assert(mode==EFindName.FNAME_Add and (name=="PaintManager.State" or name=="PaintManager.TransferOrigins"))
    return {ToString=function() return name end}
end
save={["PaintManager.State"]="unchanged presets and settings"}
function library:GetPersistentHudData(pc,k,out)
    native_reads=native_reads+1
    if read_hook then read_hook() end
    local data=save[k:ToString()]
    if data==nil then return false end
    out.bIsString=true;out.StringValue=data;return true
end
function library:SetPersistentHudData(pc,k,data)
    native_writes=native_writes+1
    if write_hook then write_hook() end
    save[k:ToString()]=data.StringValue
    return not uncertain_write
end
name="PaintManager.TransferOrigins"
origins=require("paintmanager.transfer_ledger")
function open()
    local result=origins.new(token)
    assert(result:load())
    return result
end
function can(slot)
    return {slot=slot or 12,count=1,boxKind="Small",boxId="fffffffffffffffe",assetType="Item",
        assetName="DA_Paint_Red",itemPath="VoyagePaintItem /Game/Paint/DA_Paint_Red.DA_Paint_Red",
        fingerprint="01|00000abc|03"}
end
function pickup(book,record)
    local ticket=assert(book:prepare("take",record))
    assert(book:commit(ticket,record))
end
function live(record)
    return {assetType=record.assetType,assetName=record.assetName,itemPath=record.itemPath,
        itemCount=record.count,slot=record.slot,fingerprint=record.fingerprint}
end
'''


class TransferLedgerTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path')
        self.lua.execute(FIXTURE + ORIGINS)

    def test_readonly_open_take_reload_return_and_separate_native_snapshot(self):
        self.lua.execute('''
        local book=open()
        assert(#book:entries()==0 and native_writes==0)
        local selected=can()
        local take=assert(book:prepare("take",selected))
        local before_save=save[name]
        assert(#open():entries()==0,"unsettled take was exposed for return")
        local changed=can();changed.fingerprint="post-native-attributes"
        assert(not book:commit(take,changed))
        assert(book:commit(take,selected))
        assert(not book:commit(take,selected))
        assert(book:current())
        local saved_after=save[name]
        generation=generation+1; assert(not book:current())
        book=open()
        local read=book:entries()[1]
        assert(read.boxId=="fffffffffffffffe" and read.slot==12 and read.fingerprint==selected.fingerprint)
        assert(save["PaintManager.State"]=="unchanged presets and settings")
        local undo=assert(book:prepare("return",read))
        assert(#book:entries()==0)
        assert(book:commit(undo))
        assert(#open():entries()==0)
        save[name]=before_save; generation=generation+1; assert(#open():entries()==0)
        save[name]=nil; generation=generation+1; assert(#open():entries()==0)
        save[name]=saved_after; generation=generation+1; assert(#open():entries()==1)
        ''')

    def test_cancellation_restores_old_origin_but_uncertain_movements_stay_quarantined(self):
        self.lua.execute('''
        local book=open()
        pickup(book,can())
        local lost=book:entries()[1]
        local ticket=assert(book:prepare("return",lost))
        assert(not book:prepare("return",lost))
        assert(book:cancel(ticket))
        assert(book:entries()[1].boxId==lost.boxId)
        local changed=can();changed.boxKind="Large";changed.boxId="0123456789abcdef"
        local replace=assert(book:prepare("take",changed))
        assert(#open():entries()==0)
        assert(book:commit(replace,changed))
        assert(book:entries()[1].boxId==changed.boxId)
        local incompatible=can();assert(not book:prepare("return",incompatible))
        local attempted=assert(book:prepare("return",changed))
        generation=generation+1
        assert(not book:commit(attempted))
        local restored=open()
        assert(#restored:entries()==0,"an ambiguous return was replayed")
        pickup(restored,can(9))
        assert(#restored:entries()==1 and restored:entries()[1].slot==9)
        ''')

    def test_verified_partial_return_preserves_only_the_original_stack_remainder(self):
        self.lua.execute('''
        local book=open();local r=can(8);r.count=5;pickup(book,r)
        local ticket=assert(book:prepare("return",r))
        assert(not book:commit(ticket,r,5) and not book:commit(ticket,nil,4))
        local other=can(8);other.count=5;other.fingerprint="other"
        assert(not book:commit(ticket,other,4) and #open():entries()==0)
        assert(book:commit(ticket,r,4))
        book=open()
        local remainder=book:entries()[1]
        assert(remainder.count==4 and remainder.boxId==r.boxId and remainder.fingerprint==r.fingerprint)
        assert(not book:prepare("return",r))
        local final=assert(book:prepare("return",remainder))
        assert(book:commit(final,remainder,0) and #open():entries()==0)
        ''')

    def test_only_the_native_timestamp_rule_can_change_a_take_witness(self):
        self.lua.execute('''
        local same=origins.same_transferred_fingerprint
        local function stamp(n,flags) return string.format("%016x:%02x:aabb:61=ff",n,flags) end
        assert(same(stamp(7,0),stamp(8,0)) and same(stamp(0,1),stamp(8,1)))
        assert(same(stamp(7,1),stamp(7,1)) and not same(stamp(7,1),stamp(8,1)))
        assert(not same(stamp(7,0),stamp(0,0)) and not same(stamp(7,0),stamp(8,1)))
        assert(not same(stamp(7,0),stamp(8,0).."ff") and not same(nil,nil))
        local r=can();r.fingerprint=stamp(7,0)
        local book=open();local t=assert(book:prepare("take",r))
        local observed=can();observed.fingerprint=stamp(8,0)
        assert(book:commit(t,observed) and open():entries()[1].fingerprint==observed.fingerprint)
        assert(not book:prepare("return",r))
        ''')

    def test_confirmation_with_a_restamped_fingerprint_shared_by_another_box_roundtrips(self):
        self.lua.execute('''
        local book=open()
        local personal_box=can(3);personal_box.fingerprint="0000000000000003:00::"
        pickup(book,personal_box)
        local elsewhere=can(19);elsewhere.fingerprint="0000000000000007:00::"
        elsewhere.boxId="fffffffffffffffa"
        local ticket=assert(book:prepare("take",elsewhere))
        elsewhere.fingerprint=personal_box.fingerprint
        assert(book:commit(ticket,elsewhere))
        local entries=open():entries()
        assert(#entries==2 and entries[1].boxId==personal_box.boxId and entries[2].boxId==elsewhere.boxId)
        assert(entries[1].fingerprint==entries[2].fingerprint)
        -- The next prepared row shares the stored fingerprint, then obtains
        -- a distinct timestamp. Its earlier copies must retain their own data.
        local third=can(5);third.fingerprint=personal_box.fingerprint
        local pending=assert(book:prepare("take",third))
        third.fingerprint="0000000000000008:00::"
        assert(book:commit(pending,third))
        entries=open():entries()
        assert(#entries==3 and entries[1].fingerprint==personal_box.fingerprint
            and entries[2].fingerprint==third.fingerprint and entries[3].fingerprint==elsewhere.fingerprint)
        ''')

    def test_account_change_during_origin_cas_or_native_notification_is_detected(self):
        self.lua.execute('''
        local book=open();local initial=account
        read_hook=function() account="76561198000000002" end
        assert(not book:prepare("take",can()) and native_writes==0)
        read_hook=nil;account=initial
        write_hook=function() account="76561198000000002" end
        assert(not book:prepare("take",can()) and native_writes==1)
        write_hook=nil;account=initial
        assert(not book:prepare("take",can()) and native_writes==1)
        ''')

    def test_two_phase_transfer_checks_account_and_hud_without_global_rediscovery_for_each_check(self):
        self.open_inventory_carrier()
        self.lua.execute('''
        local find_controller,lookups=FindFirstOf,0
        FindFirstOf=function(...) lookups=lookups+1;return find_controller(...) end
        local previous_reads,previous_writes=native_reads,native_writes
        local record={slot=3,count=1,boxId=id1,boxKind="Small",assetType="Item",assetName="DA_Paint_A",
            itemPath=A.path,fingerprint="0000000000000007:00::"}
        local t=assert(ledger:prepare("take",record))
        assert(ledger:commit(t,record))
        assert(lookups<=10,"Repeated global player resolution in transfer storage admission")
        assert(native_reads-previous_reads==4 and native_writes-previous_writes==2)
        ''')

    def test_take_reserves_for_a_previously_shared_fingerprint_becoming_unique(self):
        self.lua.execute('''
        local book=open();local r=can(0)
        r.fingerprint="0000000000000001:00:"..string.rep("f",1000)..":"
        pickup(book,r)
        for i=1,70 do
            local space=32000-#save[name];if space<120 then break end
            local filler=can(i)
            filler.fingerprint=string.format("%016x:01:",i)..string.rep("e",math.min(980,space-65))..":"
            pickup(book,filler)
        end
        assert(#save[name]>31770 and #save[name]<32100)
        local previous=save[name];local writes=native_writes;r.slot=83
        assert(not book:prepare("take",r) and save[name]==previous and writes==native_writes)
        ''')

    def test_conflict_load_readiness_and_foreign_account_block_before_native_entry(self):
        self.lua.execute('''
        local first,second=open(),open()
        local t=assert(first:prepare("take",can()));assert(not second:prepare("take",can(3)))
        assert(first:commit(t,can()))
        local count=native_writes
        assert(not second:prepare("take",can(3)) and native_writes==count)
        assert(second:load())
        account="76561198000000002"
        assert(not second:current() and not second:prepare("take",can(3)))
        account="76561198000000001"
        read_hook=function() storage_ready=false end
        assert(not second:prepare("take",can(3)) and native_writes==count)
        ''')

    def test_publication_failure_preserves_uncertainty_for_origins_only(self):
        self.lua.execute('''
        local book=open()
        uncertain_write=true; assert(not book:prepare("take",can()))
        uncertain_write=false
        local count=native_writes
        assert(not open():prepare("take",can(5)) and native_writes==count)
        local r=bridge.write(token,save["PaintManager.State"],"legitimate edit",scope)
        assert(r.status=="staged" and save["PaintManager.State"]=="legitimate edit")
        assert(#open():entries()==0)
        generation=generation+1
        assert(not book:current())
        local reloaded=open();pickup(reloaded,can(10))
        assert(#reloaded:entries()==1 and reloaded:entries()[1].slot==10)
        ''')

    def test_reconcile_keeps_only_matching_witnesses_and_never_moves_a_claim(self):
        self.lua.execute('''
        local book=open()
        for i=1,4 do pickup(book,can(i)) end
        local read=book:entries();read[1].boxId="forged"
        assert(book:entries()[1].boxId==can().boxId)
        local slots={}
        slots[1]=live(can(1))
        slots[2]=live(can(2));slots[2].fingerprint="personal can"
        slots[3]=live(can(3));slots[3].fingerprint=nil
        slots[8]=live(can(4))
        assert(book:reconcile(slots))
        local retained=open():entries()
        assert(#retained==2 and retained[1].slot==1 and retained[2].slot==3)
        slots[3].itemCount=2;assert(book:reconcile(slots))
        assert(#book:entries()==1)
        ''')

    def test_reconcile_after_paint_use_reduces_only_a_unique_proven_stack(self):
        self.lua.execute('''
        local book=open();local slots={}
        for i=1,5 do
            local r=can(i);r.count=4;r.itemPath=r.itemPath..i
            pickup(book,r);slots[i]=live(r);slots[i].itemCount=3
        end
        slots[6]=live(book:entries()[2]) -- identical untracked can
        slots[7]=live(book:entries()[3]);slots[7].fingerprint=nil
        slots[4].itemCount=5
        slots[5].itemCount=0
        assert(book:reconcile(slots))
        local entries=open():entries()
        assert(#entries==1 and entries[1].slot==1 and entries[1].count==3)
        ''')

    def test_unexpected_format_and_malformed_data_are_never_replaced(self):
        self.lua.execute('''
        local book=open();pickup(book,can())
        local valid=save[name]
        local tail=assert(valid:match("\\n(.*)"))
        for _,bad in ipairs({valid:gsub("/1","/2"),valid..tail,valid:sub(1,-2),valid.."broken\\n",
            valid:gsub("fffffffffffffffe","0"),valid:gsub("Small","Foreign"),
            ""}) do
            local count=native_writes
            save[name]=bad;assert(not book:load())
            assert(#book:entries()==0 and not book:prepare("take",can()) and count==native_writes)
            assert(save[name]==bad)
        end
        ''')

    def test_storage_bound_and_native_reentry_before_publication(self):
        self.lua.execute('''
        local book=open();local another=open()
        write_hook=function() assert(not another:prepare("take",can(77))) end
        local count=0
        for i=1,100 do
            local c=can(i);c.fingerprint=string.format("%08x",i)..string.rep("f",1016)
            local t=book:prepare("take",c)
            if not t then break end
            assert(book:commit(t,c));count=count+1
        end
        assert(count>10 and count<100 and #book:entries()==count and #save[name]<=32768)
        local wrong=can(29);wrong.boxId="0"
        assert(not book:prepare("take",wrong))
        ''')

    def test_return_near_limit_when_dictionary_compaction_would_grow(self):
        self.lua.execute(r'''
        local function document(padding)
            local lines={"PaintManager.TransferOrigins/1\t10\n"}
            for slot=0,140 do
                local key=slot<=40 and slot or 0
                if slot<=40 then
                    local length=slot>0 and padding//40+(slot<=padding%40 and 1 or 0) or 0
                    lines[#lines+1]=string.format("B\t%016x\tSmall\n",slot+1)
                    lines[#lines+1]=string.format("I\tItem\tDA_%d\t/Game/Paint%d\n",slot,slot)
                    lines[#lines+1]="F\tfp"..slot..string.rep("x",length).."\n"
                end
                lines[#lines+1]=string.format("A\t%d\t1\t%d\t%d\t%d\n",slot,3*key+1,3*key+2,3*key+3)
            end
            return table.concat(lines)
        end
        save[name]=document(32740-#document(0))
        assert(#save[name]==32740)
        local book=open()
        local first=book:entries()[1]
        assert(first.slot==0)
        -- Removing slot 0 moves the three popular references from 1..3 to
        -- 121..123 in canonical order, which would overflow this HUD payload.
        local t=assert(book:prepare("return",first))
        assert(book:commit(t,first,0))
        local reloaded=open()
        assert(#reloaded:entries()==140 and #save[name]<=32768)
        local again=reloaded:entries()[41]
        assert(again.slot==41 and again.boxId==first.boxId)
        t=assert(reloaded:prepare("return",again))
        assert(reloaded:cancel(t))
        assert(#open():entries()==140)
        ''')

    def test_hundreds_of_distinct_backpack_witnesses_fit_and_survive_reload(self):
        inventory = LuaRuntime(unpack_returned_tuples=True)
        inventory.globals().scripts_path = SCRIPTS.as_posix()
        inventory.execute('package.path=scripts_path.."/?.lua;"..package.path')
        inventory.execute(COLLECTION + TRANSFER)
        # Run the production backpack reader on native-slot fixtures. Each
        # physical can has its own timestamp and empty opaque payload.
        snapshot = inventory.execute('''
        local paints={}
        for i=1,50 do paints[i]=paint("DA_Paint_Fixture"..i,"Color "..i) end
        for slot=0,599 do bag.slots[slot]=can(paints[slot%50+1],638987654321000000+slot) end
        return assert(transfer.backpack(config,token))
        ''')
        records = self.lua.table()
        for slot, observed in snapshot.slots.items():
            records[slot] = self.lua.table_from({
                "slot": slot, "count": observed.itemCount, "fingerprint": observed.fingerprint,
                "boxId": f"{slot % 66 + 1:016x}", "boxKind": ("Small", "Medium", "Large")[slot % 3],
                "itemPath": observed.itemPath, "assetName": observed.assetName, "assetType": observed.assetType,
            })
        self.lua.globals().observed = records
        self.lua.execute('''
        local book=open()
        for slot=0,599 do pickup(book,observed[slot]) end
        assert(#save[name] <= 32768)
        local records=open():entries()
        assert(#records==600)
        for _,r in ipairs(records) do
            local original=observed[r.slot]
            assert(r.fingerprint==original.fingerprint and r.boxId==original.boxId and r.itemPath==original.itemPath)
        end
        ''')

    def open_inventory_carrier(self):
        self.lua.execute('native_fname=FName')
        self.lua.execute(COLLECTION + TRANSFER)
        self.lua.execute('''
        -- Connect the inventory and HUD fixtures to the same local player.
        -- The production storage admission, native-carrier marshalling,
        -- transfer jobs and ledger run unchanged.
        bridge.storage_context=native_storage_context
        controller.PlayerState=state
        function pawn:GetController() return controller end
        function controller:GetHUD() return hud end
        local player=object("LocalPlayer /Player","/Script/Engine.LocalPlayer",7001)
        player.PlayerController=controller;controller.Player=player
        local resolver=object("SaveGame /Resolver","/Script/Voyage.VoyageSaveGame",7002)
        function resolver:GetSaveGameUserId(value) assert(value==player);return account end
        function resolver:GetSaveGameRootDirectory() return "/fixture/Saved/SaveGames" end
        local find_runtime=StaticFindObject
        function StaticFindObject(path)
            if path=="/Script/Voyage.Default__VoyageBlueprintUIFunctionLibrary" then return library end
            if path=="/Script/Voyage.Default__VoyageSaveGame" then return resolver end
            return find_runtime(path)
        end
        local game_fname=FName
        function FName(value,mode)
            if value==name then return native_fname(value,mode) end
            return game_fname(value,mode)
        end
        package.loaded["paintmanager.save_lifecycle"].admit_account=function(ctx,id,user,path)
            return ctx==token and id==scope and user==account and path:find(user,1,true)~=nil
        end
        scope=assert(bridge.capture_context().scopeId)
        ledger=open()
        ''')

    def test_selected_top_up_from_two_boxes_returns_only_borrowed_cans(self):
        self.open_inventory_carrier()
        self.lua.execute('''
        local personal=can(A,700,2)
        bag.slots[0]=personal
        local first=stock("OneCan",id1,100,{[1]=can(A,701)})
        local second=stock("TwoCans",id2,200,{[2]=can(A,702,2)})
        local take=run("take",A)
        assert(take.outcome=="completed" and take.moved==3 and take.skipped==0,take.error or take.reason)
        assert(not next(first.slots) and not next(second.slots))
        local bagView=assert(transfer.backpack(config,token))
        assert(bagView.counts[A.path]==5 and bag.slots[0]==personal)
        ledger=open() -- Read the persisted origins for both transfers.
        local borrowed={}
        for _,record in ipairs(ledger:entries()) do
            borrowed[record.boxId]=(borrowed[record.boxId] or 0)+record.count
        end
        assert(borrowed[id1]==1 and borrowed[id2]==2)
        assert(transfer.returnable(bagView,ledger:entries())[A.path]==3)

        local one=run("return",A)
        local rest=run("return")
        assert(one.outcome=="completed" and one.moved==1,one.error or one.reason)
        assert(rest.outcome=="completed" and rest.moved==2,rest.error or rest.reason)
        local function total(inventory,stamp)
            local count=0
            for _,item in pairs(inventory.slots) do
                assert(item.Data.Timestamp==stamp)
                count=count+item.Data.ItemCount
            end
            return count
        end
        assert(total(first,701)==1 and total(second,702)==2)
        assert(total(bag,700)==2 and bag.slots[0]==personal and #open():entries()==0)
        ''')

    def test_custom_then_all_of_selected_color_keep_same_box_origin(self):
        self.open_inventory_carrier()
        self.lua.execute('''
        local function gather(quantity)
            return finish(transfer.begin(config,{
                direction="take",itemPath=A.path,quantity=quantity,ledger=ledger,
                gameBuild=config.reference_build,contextToken=token,guard=function() return true end,
            }))
        end
        local personal=can(A,710,2)
        local blue=can(B,711,4)
        bag.slots[0]=personal
        local source=stock("Mixed",id1,100,{[0]=can(A,712,9),[1]=blue})
        local first=gather(7)
        assert(first.outcome=="completed" and first.moved==5,first.error or first.reason)
        assert(source.slots[0].Data.ItemCount==4)
        ledger=open()
        local rest=gather("all")
        assert(rest.outcome=="completed" and rest.moved==4,rest.error or rest.reason)
        local view=assert(transfer.backpack(config,token))
        assert(view.counts[A.path]==11 and not view.counts[B.path] and source.slots[1]==blue)
        ledger=open()
        assert(transfer.returnable(view,ledger:entries())[A.path]==9)
        local returned=run("return")
        assert(returned.moved==9 and returned.outcome=="completed",returned.error or returned.reason)
        assert(bag.slots[0]==personal and personal.Data.ItemCount==2 and #open():entries()==0)
        assert(source.slots[1]==blue and blue.Data.ItemCount==4)
        local inBox=0
        for _,item in pairs(source.slots) do
            if item.Item:get()==A then inBox=inBox+item.Data.ItemCount end
        end
        assert(inBox==9)
        ''')

    def test_transfer_job_round_trip_through_native_carrier_after_map_reload(self):
        self.open_inventory_carrier()
        self.lua.execute('''
        after_native=function(dst,src,s,t) dst.slots[t].Data.Timestamp=9000+moves end
        local origin=stock("NearBeforeReload",id1,100,{[4]=can(A,91,4)})
        local picked=run("take")
        assert(picked.outcome=="completed" and picked.moved==4,picked.error or picked.reason)
        assert(not origin.slots[4] and bag.slots[0].Data.Timestamp==9001 and bag.slots[0].Data.ItemCount==4)
        local saved=assert(save[name]);assert(native_writes==2)
        assert(ledger:entries()[1].boxId==id1)
        local old=ledger
        -- Recreate the wrapper and load the saved HUD value with the same
        -- player's possessions. The original ID now lives beyond ship range.
        world=obj("World /Game/Map.Map","World")
        containers={};generation=generation+1
        token=assert(collection.context());scope=assert(bridge.capture_context().scopeId)
        save={[name]=saved};ledger=open();assert(not old:current())
        bag.slots={[0]=can(A,9001,4),[7]=can(A,177)}
        local original=stock("FarAfterReload",id1,9000,{})
        local replacement=stock("NearAfterReload",id2,100,{})
        fixtureShipUnavailable=true
        bag.slots[0].Data.ItemCount=3 -- The native editor consumed one can.
        local view=assert(transfer.backpack(config,token))
        assert(ledger:reconcile(view.slots) and open():entries()[1].count==3)
        local sent=run("return",A)
        assert(sent.outcome=="completed" and sent.moved==1,sent.error or sent.reason)
        assert(original.slots[0].Data.Timestamp==9002 and original.slots[0].Data.ItemCount==1)
        assert(bag.slots[0].Data.ItemCount==2 and open():entries()[1].count==2)
        sent=run("return")
        assert(sent.outcome=="completed" and sent.moved==2,sent.error or sent.reason)
        assert(original.slots[1].Data.Timestamp==9003 and original.slots[1].Data.ItemCount==2)
        assert(not next(replacement.slots))
        assert(not bag.slots[0] and bag.slots[7].Data.Timestamp==177)
        assert(native_writes==7 and #open():entries()==0 and moves==3)
        ''')


if __name__ == "__main__":
    unittest.main()
