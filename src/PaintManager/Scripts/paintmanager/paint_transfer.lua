-- Explicit game-thread transfers between physical inventories. Only a completed
-- before/after check may turn a prepared origin entry into a returnable entry.
local collection = require("paintmanager.collection")
local adapter = require("paintmanager.game_adapter")
local ship = require("paintmanager.ship_scope")
local ids = require("paintmanager.persistent_id")
local safe = require("paintmanager.safe")
local origins = require("paintmanager.transfer_ledger")
local quantity = require("paintmanager.transfer_quantity")
local M = {}
local Job = {}; Job.__index = Job
local quarantine = {}
local WEIGHT = "/Script/Voyage.VoyageInventoryWeightLimitedComponent"
local function integer(value, low, high)
    return math.type(value) == "integer" and value >= low and value <= high
end
local function same(a,b)
    if not safe.valid(a) or not safe.valid(b) then return false end
    local address = a:GetAddress()
    return (type(address) == "number" or type(address) == "string") and address == b:GetAddress()
end

local payload = require("paintmanager.paint_payload")
local function inventory_snapshot(inventory, config)
    local out = {slots={},counts={},paints={}}
    local paint_ids = {}
    local visited = 0
    inventory.Items:ForEach(function(key,wrapped)
        visited = visited + 1
        assert(visited <= config.max_slots,"Inventory read limit exceeded")
        local row,reason = adapter.read_slot(key,wrapped,true)
        assert(row,reason)
        local slot = math.tointeger(tonumber(row.slot))
        assert(slot and slot >= 0 and slot <= 2147483647 and not out.slots[slot],"Invalid or duplicate inventory slot")
        row.slot = slot
        if row.itemCount == 0 then return end
        out.slots[slot] = row
        local entry = safe.unwrap(wrapped)
        local item = safe.unwrap(entry.Item)
        assert(row.itemResolved or not safe.paint_hint(row.assetName,row.itemPath,row.itemClass),
            "Cannot verify the identity of a paint in this inventory")
        -- Item paths include the native class. Slots for duplicate cans share
        -- their loaded item asset; keep this cache only during this traversal.
        local paint_id = row.itemPath and paint_ids[row.itemPath]
        if row.itemResolved and paint_id == nil then
            paint_id = collection.eligible_paint(item) and {
                name = assert(safe.short_name(item)), assetType = collection.primary_type(item) } or false
            if row.itemPath then paint_ids[row.itemPath] = paint_id end
        end
        if paint_id then
            assert(paint_id.name == row.assetName and row.itemPath and row.assetType
                and row.assetType ~= "" and row.assetType ~= "None"
                and (not paint_id.assetType or paint_id.assetType == row.assetType),"Incomplete native paint identity")
            local readable, fingerprint, plain = pcall(payload,entry.Data)
            if readable then row.fingerprint,row.plain = fingerprint,plain end
            out.counts[row.itemPath] = (out.counts[row.itemPath] or 0) + row.itemCount
            out.paints[#out.paints+1] = row
        end
    end)
    out.recordsVisited = visited
    table.sort(out.paints,function(a,b) return a.slot < b.slot end)
    return out
end

local function player(token)
    local context, err = collection.native_context()
    assert(context and context.token == token,err or "Player context changed")
    assert(context.controller:IsLocalController() == true,"Local player unavailable")
    local inventory = context.controller:GetInventoryComponent(1)
    assert(safe.valid(inventory) and inventory:IsA(WEIGHT) == true
        and same(inventory,context.pawn.WeightInventory) and same(inventory:GetOwner(),context.pawn),
        "Backpack identity changed")
    return context, inventory
end

-- For same-call native proofs. Resolve the local player's actual component
-- each time, including its owner; callers must not retain it across callbacks.
function M.player_backpack(token)
    local ok, context, inventory = pcall(player, token)
    if ok then return inventory end
    return nil, tostring(context)
end

function M.backpack(config,token)
    local ok, snapshot = pcall(function()
        local _, inventory = player(token)
        local data = inventory_snapshot(inventory,config)
        local _, current = player(token)
        assert(same(current,inventory),"Backpack changed during inspection")
        return data
    end)
    if ok then return snapshot end
    return nil,tostring(snapshot)
end

local function matches(row,record)
    return row and row.itemPath == record.itemPath and row.assetName == record.assetName
        and row.assetType == record.assetType and row.itemCount == record.count
        and row.fingerprint ~= nil and row.fingerprint == record.fingerprint
end

-- When identical payloads share a color, every matching copy must have an
-- attributed slot before a return can distinguish this group from personal cans.
local function available_slots(snapshot,entries)
    local slots, ambiguous, unreadable = {}, {}, {}
    for _,record in ipairs(entries) do
        if matches(snapshot.slots[record.slot],record) then slots[record.slot] = record end
    end
    for _,row in ipairs(snapshot.paints) do
        if not slots[row.slot] then
            if row.fingerprint then ambiguous[row.itemPath .. "\0" .. row.fingerprint] = true
            else unreadable[row.itemPath] = true end
        end
    end
    for slot,record in pairs(slots) do
        if unreadable[record.itemPath] or ambiguous[record.itemPath .. "\0" .. record.fingerprint] then slots[slot] = nil end
    end
    return slots
end

function M.returnable(snapshot,entries)
    local counts = {}
    for _,record in pairs(available_slots(snapshot,entries)) do
        counts[record.itemPath] = (counts[record.itemPath] or 0) + record.count
    end
    return counts
end

local function box_owner(component,context)
    if not (safe.valid(component) and component:IsA(WEIGHT)) then
        return nil, "The box source is not a valid native weight-limited inventory"
    end
    local owner = component:GetOwner()
    if not safe.valid(owner) then return nil, "The box inventory has no live owner" end
    if not collection.box_kind(owner) then return nil, "The inventory owner is not a Small, Medium or Large player box" end
    if not same(owner:GetWorld(),context.world) then return nil, "The box owner belongs to a different world" end
    if component:HasAnyFlags(0x30) ~= false then return nil, "The box inventory is a template or its object flags are unavailable" end
    if owner:HasAnyFlags(0x30) ~= false then return nil, "The box owner is a template or its object flags are unavailable" end
    if not same(owner.VoyageInventoryWeightLimited,component) then
        return nil, "The box owner's declared weight inventory differs from the source inventory"
    end
    return owner
end

function M.accessible_box(component, context)
    local owner = box_owner(component, context)
    if owner and owner.IsLocked == false and owner.InteractEnabled == true then return owner end
end

-- The native menu has already selected its source. InteractEnabled controls
-- which actions the actor offers in the world, before this menu is opened.
-- IsLocked and the source inventory's native write permission still apply.
function M.open_writable_box(component, context)
    local owner, reason = box_owner(component, context)
    if not owner then return nil, reason end
    local locked = safe.get(owner, "IsLocked")
    if locked == true then return nil, "The open player box is locked" end
    if locked ~= false then return nil, "The open player box lock state is unreadable" end
    local access = safe.get(component, "Access")
    if access ~= 1 and access ~= 2 then
        return nil, "The open box inventory is not writable (Access=" .. tostring(access) .. ")"
    end
    return owner
end

local function open_deposit_owner(token, bag, box)
    local context, reason = collection.native_context()
    assert(context and context.token == token, reason or "The local player context changed during paint deposit")
    local current_bag, bag_error = M.player_backpack(token)
    assert(current_bag and same(current_bag, bag), bag_error or "The local backpack changed during paint deposit")
    return assert(M.open_writable_box(box, context))
end

function Job:_scope()
    local scope,err = ship.player_access(self.config,self.shipScope)
    assert(scope and scope.token == self.shipScope.token,err or "Ship scope changed")
    return scope
end

function Job:_guard()
    assert(not self.stop,"Transfer cancelled")
    assert(not quarantine[self.request.contextToken],"A previous transfer has an uncertain outcome")
    local allowed,reason = self.request.guard()
    assert(allowed == true,reason or "Transfer window or context changed")
end

function Job:_box(descriptor,context)
    assert(descriptor and self.by_id[descriptor.boxId] == descriptor,"Original container is unavailable or ambiguous")
    local component = assert(collection.resolve_ref(descriptor.component))
    local owner = box_owner(component,context)
    assert(owner and same(owner,assert(collection.resolve_ref(descriptor.owner)))
        and collection.box_kind(owner) == descriptor.boxKind
        and ship.persistent_actor_id(owner) == descriptor.boxId,"Original container identity changed")
    assert(owner.IsLocked == false and owner.InteractEnabled == true,"Container is locked or unavailable")
    if self.taking then
        assert(ship.contains(self:_scope(),owner:K2_GetActorLocation()),"Container left the ship radius")
    end
    return component
end

function Job:_skip(amount,reason,unknown)
    -- For a top-up, several rejected slots may represent the same unmet need.
    -- Its final fresh backpack count determines the exact shortfall.
    if not self.targetCount then
        if self.request.itemPath and not self.taking and (amount or 0)>0 then self.skipped = 1
        else self.skipped = self.skipped + (amount or 0) end
    end
    self.incomplete = self.incomplete or unknown == true
    self.reason = tostring(reason)
end

function Job:_finish_top_up(backpack_count)
    self.state = "done"
    self.skipped = math.max(0,self.targetCount-backpack_count)
    if self.skipped == 0 then
        self.incomplete,self.reason = false,nil
        self.outcome = self.moved > 0 and "completed" or "none"
    else
        self.outcome = "partial"
        self.reason = self.reason or "Not enough of the selected paint is available in ship storage"
    end
end

-- On the reference build, Small and Large both inherit from PlayerBox.
-- FindAllOf includes subclasses. Verify each exact class before reading it.
function M.stock_actor_refs(limit)
    local actors = FindAllOf("BP_Inventory_PlayerBox_C") or {}
    assert(type(actors)=="table" and #actors<=limit,"Could not check every loaded stock box")
    local refs = {}
    for _,owner in ipairs(actors) do
        local kind,readable = collection.box_kind(owner)
        assert(readable and safe.valid(owner),"Cannot classify a loaded stock actor")
        if kind then
            refs[#refs+1] = assert(collection.runtime_ref(owner),"Cannot identify a loaded stock box")
        end
    end
    return refs
end

-- The view orders work only. All exact boxes still participate in duplicate
-- ID detection and in the fallback search if the captured locations changed.
local function preferred_sources(model,path)
    local preferred = {}
    if not model then return preferred end
    if not path then
        for _,source in ipairs(model.stockLocationSources or {}) do
            if source.persistentId and next(source.paintCounts or {}) then preferred[source.persistentId]=true end
        end
        for _,source in ipairs(model.sources or {}) do
            if source.persistentId and (source.paintCount or 0)>0 then preferred[source.persistentId]=true end
        end
        return preferred
    end
    for _,row in ipairs(model.rows or {}) do
        if row.id==path or row.itemPath==path then
            for _,i in ipairs(row.stockLocationHints or {}) do
                local source = model.stockLocationSources and model.stockLocationSources[i]
                if source and source.persistentId then preferred[source.persistentId]=true end
            end
            break
        end
    end
    return preferred
end

function Job:_capture_box(owner,ref,context,scope,id,duplicate)
    if self.taking then
        local inside,distance = ship.contains(scope,owner:K2_GetActorLocation())
        assert(distance~=nil,"Cannot verify container position")
        if not inside then return end
    elseif not self.wanted_ids[id] then return end
    if not id or duplicate then
        self:_skip(0,id and "Container ID is duplicated" or "Container has no persistent ID",true)
        return
    end
    local component = owner.VoyageInventoryWeightLimited
    assert(same(box_owner(component,context),owner),"Stock inventory identity changed")
    local box = {boxId=id,boxKind=collection.box_kind(owner),owner=ref,component=assert(collection.runtime_ref(component))}
    self.by_id[id] = box
    if self.taking then
        local list = self.preferred[id] and self.boxes or self.other_boxes
        list[#list+1] = box
    end
end

function Job:_index_actor(ref,context,scope)
    local owner = assert(collection.resolve_ref(ref),"Container disappeared before its ID could be checked")
    local inspected,eligible = pcall(function()
        local template = owner:HasAnyFlags(0x30)
        assert(type(template)=="boolean","Container flags are unavailable")
        if template then return false end
        local world = owner:GetWorld()
        assert(safe.valid(world),"Container world is unavailable")
        return same(world,context.world)
    end)
    if inspected and not eligible then return end
    local id,reason,status = ship.persistent_actor_id(owner)
    local duplicate
    if status~="zero" then
        assert(ids.is_valid(id),reason or "Could not check persistent ID uniqueness for every stock box")
        duplicate = self.by_id[id]~=nil
        self.by_id[id]=false
    end
    -- Even an out-of-range or unreadable box makes its own ID ineligible if
    -- another actor shares it. An unknown ID stops the initial index entirely.
    if not inspected then self:_skip(0,eligible,true); return end
    local ok,err=pcall(self._capture_box,self,owner,ref,context,scope,id,duplicate)
    if not ok then self:_skip(0,err,true) end
end

function Job:_index_boxes()
    local context = player(self.request.contextToken)
    local scope = self.taking and self:_scope()
    -- Share the native player/ship reads within this synchronous group.
    -- Only plain actor descriptors and the cursor survive the call.
    local last = math.min(self.index+7,#self.refs)
    for i=self.index,last do
        self:_index_actor(self.refs[i],context,scope)
    end
    self.index = last+1
    if self.index>#self.refs then
        for _,box in ipairs(self.other_boxes) do self.boxes[#self.boxes+1]=box end
        self.refs,self.other_boxes,self.preferred,self.wanted_ids=nil,nil,nil,nil
        self.index,self.phase=1,self.taking and "boxes" or "returns"
    end
end

function Job:_next_work()
    if self.remainder then
        local pending = self.remainder
        self.remainder = nil
        return pending.box,pending.work
    end
    if self.taking then
        local snapshot
        if not self.rows or self.cursor>#self.rows then
            self.box = self.boxes[self.index]
            if self.box then
                self.index = self.index+1
                local ok,result = pcall(function()
                    local context = player(self.request.contextToken)
                    return inventory_snapshot(self:_box(self.box,context),self.config)
                end)
                if ok then snapshot=result else self:_skip(0,result,true) end
                self.rows,self.cursor = snapshot and snapshot.paints or {},1
            end
        end
        if self.box then
            while self.rows[self.cursor] do
                local row = self.rows[self.cursor]
                self.cursor = self.cursor+1
                if not self.request.itemPath or self.request.itemPath==row.itemPath then
                    return self.box,row,snapshot
                end
            end
            return
        end
    else
        local record = self.return_work[self.index]
        if record then
            self.index = self.index+1
            return self.by_id[record.boxId],record
        end
    end
    if self.targetCount then
        local bag,err = M.backpack(self.config,self.request.contextToken)
        assert(bag,err)
        self:_finish_top_up(bag.counts[self.request.itemPath] or 0)
        return
    end
    self.state = "done"
    if self.request.itemPath and self.moved==0 and self.skipped==0 then
        self:_skip(self.taking and 0 or 1,self.reason or "The selected paint is no longer available")
    end
    self.outcome = (self.skipped>0 or self.incomplete) and "partial" or self.moved>0 and "completed" or "none"
end

local function same_asset(left,right)
    return left and right and left.assetName == right.assetName and left.assetType == right.assetType
        and left.itemPath == right.itemPath
end

local function same_contents(left,right)
    return same_asset(left,right) and left.fingerprint == right.fingerprint
end

local function compare_other_slots(before,after,changed)
    for slot,row in pairs(before.slots) do
        if slot ~= changed then
            local next_row = after.slots[slot]
            assert(same_contents(row,next_row) and next_row.itemCount==row.itemCount,"An unrelated inventory slot changed")
        end
    end
    for slot in pairs(after.slots) do
        assert(slot==changed or before.slots[slot],"An unexpected inventory slot appeared")
    end
end

local function admit_slot(source, destination, dst, row, requested)
    local count = row.itemCount
    local target_slot = destination:GetNextEmptySlot()
    assert(integer(target_slot,0,2147483647) and not dst.slots[target_slot],"Destination has no empty slot")
    local item = StaticFindObject(row.itemPath:match("^[^ ]+ (/.+)$") or row.itemPath)
    assert(collection.eligible_paint(item) and safe.name(item) == row.itemPath and safe.short_name(item) == row.assetName
        and collection.primary_type(item) == row.assetType,
        "Native paint identity changed")
    assert(source:IsValidSlotIndex(row.slot)==true and source:CanRemoveSlot(row.slot)==true,
        "Source inventory cannot release this paint")
    assert(destination:IsValidSlotIndex(target_slot)==true and destination:CanAddItem(item)==true
        and destination:CanAddToSlot(target_slot,item)==true,"Destination rejected this paint or slot")
    local weight = destination:EstimateAddAmount(item,requested)
    local space = destination:EstimateAddAmountToSlot(target_slot,item,requested)
    assert(integer(weight,1,requested) and integer(space,1,requested),
        "Destination lacks room or weight allowance for this paint")
    local amount = math.min(requested,weight,space)
    assert(amount==count or row.plain,"A paint stack with instance data can only be moved whole")
    if amount < requested then
        assert(destination:EstimateAddAmount(item,amount)==amount
            and destination:EstimateAddAmountToSlot(target_slot,item,amount)==amount,
            "Destination did not confirm the exact paint quantity")
    end
    return target_slot, amount
end

local function confirm_slots(proof, src, dst)
    compare_other_slots(proof.src, src, proof.slot)
    compare_other_slots(proof.dst, dst, proof.target)
    local row = src.slots[proof.slot]
    assert(same_contents(row, proof.row) and row.itemCount == proof.original and not dst.slots[proof.target],
        "A transfer slot changed during admission")
end

local function confirm_moved(proof, result, src, dst)
    local arrived = dst.slots[proof.target]
    local remaining = proof.original - proof.count
    local left = src.slots[proof.slot]
    assert(result == proof.count and same_asset(arrived, proof.row)
        and origins.same_transferred_fingerprint(proof.row.fingerprint, arrived.fingerprint)
        and arrived.itemCount == proof.count, "Native transfer or item conservation could not be verified")
    assert((remaining == 0 and not left) or (remaining > 0 and same_contents(left, proof.row) and left.itemCount == remaining),
        "Native transfer left an unexpected source stack")
    compare_other_slots(proof.src, src, proof.slot)
    compare_other_slots(proof.dst, dst, proof.target)
    return arrived, remaining
end

-- Only the explicitly opened box may be used by the stock Deposit Similar
-- action. Its caller resolves the same local widget, backpack and box at every
-- boundary. This operation records no borrowed-can origin.
function M.deposit_open_slot(config, token, work, endpoints, still_active)
    assert(not quarantine[token], "A previous transfer has an uncertain outcome")
    local bag, box = endpoints()
    local owner = open_deposit_owner(token, bag, box)
    local src = inventory_snapshot(bag, config)
    local dst = inventory_snapshot(box, config)
    local row = src.slots[work.slot]
    if not row then return 0, "The backpack slot is now empty", "changed_source" end
    if not row.fingerprint then return 0, "The paint instance data could not be verified" end
    if not same_contents(row, work) or row.itemCount > work.itemCount then
        return 0, "The backpack paint stack changed", "changed_source"
    end
    -- A new empty target slot introduces one record. The post-transfer
    -- inventory must still fit the same bounded conservation check.
    if not next(dst.counts) then return 0, "The open box no longer contains paint" end
    if dst.recordsVisited >= config.max_slots then return 0, "The box reached the verifiable inventory size limit" end
    local allowed, target, count = pcall(admit_slot, bag, box, dst, row, row.itemCount)
    if not allowed then return 0, tostring(target) end
    local live_bag, live_box = endpoints()
    assert(same(live_bag, bag) and same(live_box, box), "Open inventory changed before paint deposit")
    local proof = { src = src, dst = dst, slot = row.slot, target = target, row = row, original = row.itemCount, count = count }
    local checked, confirmed_target, confirmed_count = pcall(admit_slot, bag, box, dst, row, row.itemCount)
    if not checked then return 0, tostring(confirmed_target) end
    if confirmed_target ~= target or confirmed_count ~= count then return 0, "The destination capacity changed before transfer" end
    live_bag, live_box = endpoints()
    assert(same(live_bag, bag) and same(live_box, box), "Open inventory changed during paint deposit admission")
    assert(same(owner, open_deposit_owner(token, bag, box)), "The open box inventory owner changed before transfer")
    -- Native eligibility and menu checks can reenter the game. Inspect both
    -- slots after those calls; only a plain cancellation check follows.
    local current_src = inventory_snapshot(bag, config)
    local current_dst = inventory_snapshot(box, config)
    confirm_slots(proof, current_src, current_dst)
    assert(still_active() and not quarantine[token], "Paint deposit cancelled before transfer")
    quarantine[token] = true
    local verified, remaining = pcall(function()
        local result = box:TransferSlot(bag, row.slot, target, count)
        local final_dst = inventory_snapshot(box, config)
        local final_src = inventory_snapshot(bag, config)
        local _, left = confirm_moved(proof, result, final_src, final_dst)
        assert(same(owner, open_deposit_owner(token, bag, box)), "The open box inventory owner changed during transfer")
        return left
    end)
    if not verified then return nil, tostring(remaining) end
    quarantine[token] = nil
    return count, remaining
end

-- The stock button only needs paint presence while displaying a menu. Stop
-- at the first positive native paint and leave payloads and sorting for input.
function M.inventory_has_paint(inventory, config)
    local found, visited = false, 0
    inventory.Items:ForEach(function(_, wrapped)
        if found or visited >= config.max_slots then return true end
        visited = visited + 1
        local entry = safe.unwrap(wrapped)
        local count = safe.get(safe.get(entry, "Data"), "ItemCount")
        if integer(count, 1, 2147483647) and collection.eligible_paint(safe.unwrap(safe.get(entry, "Item"))) then
            found = true
            return true
        end
        if visited >= config.max_slots then return true end
    end)
    return found
end

-- Plain paint rows only; native handles stay in the calling game-thread step.
function M.inventory_paints(inventory, config)
    return inventory_snapshot(inventory, config).paints
end

function Job:_preflight(box,work,prepared)
    local context,bag = player(self.request.contextToken)
    local container = self:_box(box,context)
    local source,destination = bag,container
    if self.taking then source,destination = container,bag end
    -- After staging the origin, recompute native admission for the proposed
    -- slots. The caller immediately captures both inventories AFTER those
    -- native calls and checks them against the pre-intent snapshots.
    local src = prepared and prepared.src or inventory_snapshot(source,self.config)
    local dst = prepared and prepared.dst or inventory_snapshot(destination,self.config)
    local count = self.taking and work.itemCount or work.count
    local row = src.slots[work.slot]
    assert(row and same_contents(row,work) and row.itemCount == count and row.fingerprint,
        "Paint slot or instance data changed or could not be verified")
    local requested = count
    if self.targetCount then
        local missing = self.targetCount - (dst.counts[row.itemPath] or 0)
        if missing <= 0 then return nil end
        requested = math.min(count,missing)
    elseif not self.taking then
        assert(box.boxKind == work.boxKind and available_slots(src,self.entries)[work.slot],
            "The returned can no longer has an unambiguous backpack origin")
        if self.request.itemPath then requested = 1 end
    end
    local target_slot, amount = admit_slot(source, destination, dst, row, requested)
    return {src=src,dst=dst,source=source,destination=destination,bag=bag,container=container,
        slot=row.slot,target=target_slot,count=amount,original=count,row=row}
end

function Job:_move(box,work,source_snapshot)
    local requested = not self.taking and self.request.itemPath and 1 or work.count or work.itemCount
    -- The first matching slot in a box was selected from a full native read
    -- earlier in this same call. Use it when planning the durable intent.
    local allowed,proof = pcall(self._preflight,self,box,work,source_snapshot and {src=source_snapshot})
    if not allowed then self:_skip(requested,proof); return end
    if not proof then self:_finish_top_up(self.targetCount); return end
    local entry = {boxId=box.boxId,boxKind=box.boxKind,assetName=work.assetName,assetType=work.assetType,
        itemPath=work.itemPath,slot=self.taking and proof.target or proof.slot,fingerprint=work.fingerprint,
        count=self.taking and proof.count or proof.original}
    local ledger = self.request.ledger
    local ticket,reason = ledger:prepare(self.request.direction,entry)
    assert(ticket,reason or "Transfer origin could not be prepared")
    local resize = false
    local checked,current = pcall(function()
        -- Writing the intent calls into the HUD. Re-read both live inventories and
        -- every admission condition before the only mutation in this step.
        -- prepare() has already compared and read back this exact HUD intent.
        self:_guard()
        local live = self:_preflight(box,work,proof)
        assert(live,"The backpack already has the selected quantity")
        resize = self.targetCount and live.count ~= proof.count
        assert(live.target==proof.target and live.count==proof.count and same(live.bag,proof.bag)
            and same(live.container,proof.container),"Inventory or destination slot changed before transfer")
        self:_guard()
        local context,bag = player(self.request.contextToken)
        assert(same(live.bag,bag) and same(live.container,self:_box(box,context)),"Transfer endpoint changed")
        -- The guard, admission and endpoint checks can call native code. Check
        -- both exact slots again after those calls, immediately before entry.
        local src = inventory_snapshot(live.source,self.config)
        local dst = inventory_snapshot(live.destination,self.config)
        resize = self.targetCount and live.dst.counts[work.itemPath] ~= dst.counts[work.itemPath]
        confirm_slots(live,src,dst)
        assert(not self.targetCount or (dst.counts[live.row.itemPath] or 0)+live.count <= self.targetCount,
            "The selected paint quantity in the backpack changed")
        assert(self.taking or available_slots(src,self.entries)[work.slot],"Backpack origin changed during admission")
        assert(not self.stop,"Transfer cancelled")
        live.src,live.dst = src,dst
        return live
    end)
    if not checked then
        local cancelled,err = ledger:cancel(ticket)
        assert(cancelled,err or "Transfer origin intent could not be cancelled")
        -- The prepared intent reserves the old quantity. Once it is cancelled,
        -- allow one fresh attempt for the newly observed amount.
        if resize and not self.stop and self.resizedWork ~= work then
            self.remainder,self.resizedWork = {box=box,work=work},work
            return
        end
        self:_skip(requested,current)
        return
    end
    self.attempted = true
    quarantine[self.request.contextToken] = true
    local verified,err = pcall(function()
        local result = current.destination:TransferSlot(current.source,current.slot,current.target,current.count)
        local context,bag = player(self.request.contextToken)
        assert(same(bag,current.bag),"Backpack changed after transfer")
        local dst = inventory_snapshot(current.destination,self.config)
        local src = inventory_snapshot(current.source,self.config)
        local arrived, remaining = confirm_moved(current,result,src,dst)
        local owner = box_owner(current.container,context)
        assert(same(owner,assert(collection.resolve_ref(box.owner)))
            and ship.persistent_actor_id(owner)==box.boxId,"Container changed after transfer")
        if self.taking then entry.fingerprint = arrived.fingerprint end
        local committed,why = ledger:commit(ticket,entry,not self.taking and remaining or nil)
        assert(committed,why or "Transfer origin could not be committed")
        -- Publish only the final paint counts of each changed box. A position
        -- is optional for a return to a loaded origin outside the ship radius.
        local change = self.changes_by_id[box.boxId]
        if not change then
            change = {componentRef=box.component,ownerRef=box.owner,persistentId=box.boxId,kind=box.boxKind}
            self.changes_by_id[box.boxId]=change
            self.changes[#self.changes+1]=change
        end
        change.counts = self.taking and src.counts or dst.counts
        self.backpackCounts = self.taking and dst.counts or src.counts
        local located,point=pcall(function() return owner:K2_GetActorLocation() end)
        change.location = located and safe.distance_cm(point,point) and {X=point.X,Y=point.Y,Z=point.Z} or nil
    end)
    if not verified then self.outcome="uncertain"; error(err,0) end
    quarantine[self.request.contextToken] = nil
    self.resizedWork = nil
    self.moved = self.moved+current.count
    if self.targetCount and (current.dst.counts[work.itemPath] or 0)+current.count >= self.targetCount then
        self:_finish_top_up(self.targetCount)
    elseif not self.taking and self.request.itemPath then
        self.state,self.outcome,self.skipped,self.reason="done","completed",0,nil
    elseif current.original > current.count then
        -- A slot-specific stack limit may permit further portions in other empty
        -- slots. The next step rechecks the new physical stack and live capacity.
        if self.taking then work.itemCount = current.original-current.count
        else work.count = current.original-current.count end
        self.remainder = {box=box,work=work}
    end
end

function Job:_unit()
    self:_guard()
    if self.phase=="discover" then
        assert(self.request.gameBuild==self.config.reference_build,"Unsupported game build for inventory transfers")
        local ok,reason = self.request.ledger:current()
        assert(ok,reason)
        local context,bag = player(self.request.contextToken)
        if self.taking then
            if self.targetCount then
                local counts = inventory_snapshot(bag,self.config).counts
                if (counts[self.request.itemPath] or 0) >= self.targetCount then
                    self:_finish_top_up(counts[self.request.itemPath])
                    return
                end
            end
            local expected = self.request.model and self.request.model.stockScope
            self.shipScope = assert(ship.player_access(self.config,expected))
            assert(self.shipScope.worldId==context.worldId,"Selected ship belongs to a different world")
            self.preferred = preferred_sources(self.request.model,self.request.itemPath)
        else
            self.entries,self.wanted_ids,self.return_work=self.request.ledger:entries(),{},{}
            for _,record in ipairs(self.entries) do
                -- Every origin is needed for backpack ambiguity checks, even
                -- when the selected color needs only a subset of boxes.
                if not self.request.itemPath or self.request.itemPath==record.itemPath then
                    self.wanted_ids[record.boxId]=true
                    self.return_work[#self.return_work+1]=record
                end
            end
            if #self.return_work==0 then self.phase="returns"; self:_next_work(); return end
        end
        self.refs,self.phase = M.stock_actor_refs(self.config.max_discovery_objects),"index"
    elseif self.phase=="index" then self:_index_boxes()
    else
        local box,work,source_snapshot = self:_next_work()
        if work then self:_move(box,work,source_snapshot) end
    end
end

function Job:get_receipt()
    return {state=self.state,phase=self.phase,outcome=self.outcome,moved=self.moved,skipped=self.skipped,
        incomplete=self.incomplete,reason=self.reason,error=self.error,attempted=self.attempted}
end
function Job:get_stock_changes()
    if self.outcome~="uncertain" then return self.changes end
end
function Job:step(budget)
    if self.state~="running" or self.busy or budget and budget:expired() then return self:get_receipt() end
    self.busy = true
    local ok,err = pcall(self._unit,self)
    self.busy = false
    if not ok then self.state,self.outcome,self.error="error",self.outcome or "error",tostring(err) end
    if self.stop and self.state~="error" then self.state,self.outcome="cancelled","cancelled" end
    if budget then budget:consume(1) end
    return self:get_receipt()
end
function Job:cancel(reason)
    self.stop = true
    if self.state=="running" and not self.busy then self.state,self.outcome,self.reason="cancelled","cancelled",reason end
    return self:get_receipt()
end
function M.begin(config,request)
    assert(request.direction=="take" or request.direction=="return","Unknown transfer direction")
    local target_count
    if request.direction=="take" and request.itemPath then
        local selected = request.quantity
        if selected == nil then selected = config.selected_backpack_target or quantity.default end
        assert(quantity.valid(selected),"Invalid selected paint quantity")
        if selected ~= "all" then target_count = math.tointeger(selected) end
    end
    return setmetatable({config=config,request=request,state="running",phase="discover",taking=request.direction=="take",
        targetCount=target_count,index=1,boxes={},other_boxes={},by_id={},changes={},changes_by_id={},
        moved=0,skipped=0,incomplete=false},Job)
end
function M.can_transfer(token) return not quarantine[token] end
function M.reset_context() quarantine={} end
return M
