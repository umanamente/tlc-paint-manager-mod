-- Embedded PNGs avoid runtime mod-file I/O. Texture imports and native
-- brushes stay on the game thread. A separate owned host retains this bank when
-- the visible PaintManager window closes; window lifetime does not own textures.
local data = require("paintmanager.ui_icon_data")
local safe = require("paintmanager.safe")
local processing = require("paintmanager.processing")
local M = {}; local Icons = {}; Icons.__index = Icons
local FALLBACK = { refresh="R", close="x", clear="x", star="*", star_filled="*",
    learned="+", ready="+", missing="-", settings="=", debug="#", search="?",
    sort="^", learn="+", presets="[]", save="S", apply=">", delete="x", import="v", back="<",
    to_backpack=">", to_box="<" }
local IDS = { "close", "refresh", "star", "star_filled", "presets", "learned", "ready", "missing",
    "settings", "clear", "sort", "learn", "back", "debug", "search", "save", "apply", "delete", "import" }
local DECODE_BYTES = 256
local ILLUSTRATION_DECODE_BYTES = 1024
local ILLUSTRATION_MAX_BYTES = 1048576
local ILLUSTRATION_MAX_PIXELS = 2097152
local MAX_ILLUSTRATION_TARGETS = 16
local MAX_PUMP_UNITS = 8
local DETAILED = { learned = "learned_detailed", ready = "ready_detailed" }

local function semantic_id(id)
    if id == "learned_detailed" then return "learned" end
    if id == "ready_detailed" then return "ready" end
    return id
end

local function logical_size(value)
    return type(value)=="number" and value==value and value>0 and value<math.huge and value or 18
end

function M.variant_id(id,size,physical_scale)
    id=semantic_id(id)
    local artwork=id=="learn" and "ready" or id
    size=logical_size(size)
    physical_scale=type(physical_scale)=="number" and physical_scale==physical_scale
        and physical_scale>0 and physical_scale<math.huge and physical_scale or 1
    if DETAILED[artwork] and size>=32 and size*physical_scale>=48 then return DETAILED[artwork] end
    return artwork
end

function M.new(tree, config, make)
    local bank=make("HorizontalBox");bank:SetVisibility(1)
    return setmetatable({tree=tree,config=config,make=make,bank=bank,entries={},order={},closed=false,index=1,
        physical_scale=1,lod_targets={},lod_scan_index=nil,illustration_targets={}},Icons)
end

function Icons:host()
    return self.bank
end

function Icons:_entry(id)
    if self.closed or type(data[id])~="string" or #data[id]==0 or #data[id]>65536 then return nil end
    local entry=self.entries[id]
    if not entry then
        entry={id=id,hex=data[id],bytes={},cursor=1,targets={},phase="keeper",nextTarget=1}
        self.entries[id]=entry;self.order[#self.order+1]=entry
    end
    return entry
end

-- Queue only copied plain data here. Native keeper construction and import are
-- individually admitted by pump, even when no visible window has been opened.
function Icons:preload_all()
    if self.closed then return false end
    if not processing.get("automatic_preparation", true) or not processing.get("preload_icons", true)
        or not processing.get("texture_cache_enabled", true) then return false end
    for _,id in ipairs(IDS) do self:_entry(id) end
    self.preloaded=true
    return true
end

function Icons:has_work()
    if self.closed then return false end
    if self.lod_scan_index then return true end
    for _,entry in ipairs(self.order) do
        if entry.phase~="done" and entry.phase~="failed" and self:_runnable(entry) then return true end
    end
    return false
end

function Icons:has_demand()
    if self.closed then return false end
    for _,entry in ipairs(self.order) do
        if entry.phase~="done" and entry.phase~="failed" then
            for _,target in ipairs(entry.targets or {}) do
                if target and target.active~=false and target.pending_entry==entry then return true end
            end
        end
    end
    return false
end

function Icons:is_ready()
    if self.closed or not self.preloaded then return false end
    for _,entry in ipairs(self.order) do
        if not entry.illustration and (not safe.valid(entry.texture) or entry.phase=="failed" or entry.phase=="failed_bind") then return false end
    end
    return #self.order>=#IDS
end

local function fallback_visible(target)
    -- A lazy LOD import failure preserves an already valid compact brush of
    -- the same semantic icon. It must not flash an empty slot while resizing.
    if target.bound_semantic==target.semantic_id and safe.valid(target.bound_texture) then return end
    if safe.valid(target.image) then pcall(function() target.image:SetVisibility(1) end) end
    if safe.valid(target.fallback) then pcall(function() target.fallback:SetVisibility(4) end) end
end

local function bind(target,texture)
    if target.active==false or not safe.valid(target.image) then return true end
    local ok=pcall(function()
        target.image:SetBrushFromTexture(texture,false)
        target.image:SetVisibility(4)
        if safe.valid(target.fallback) then target.fallback:SetVisibility(1) end
    end)
    if ok then target.bound_texture,target.bound_semantic=texture,target.semantic_id end
    if not ok then fallback_visible(target) end
    return ok
end

local function illustration_data()
    -- This separate 1024x512 PNG constant is required only on the first actual
    -- disconnected-page request. Startup/control preparation never parses it.
    -- Package checks prove the source/embedded twins. Runtime validation stays
    -- bounded: inspect the fixed header now and validate hex while decoding.
    local value=require("paintmanager.ui_signal_data")
    assert(type(value)=="table" and type(value.hex)=="string", "ui_signal_data_unavailable")
    assert(math.type(value.bytes)=="integer" and value.bytes>=33 and value.bytes<=ILLUSTRATION_MAX_BYTES
        and #value.hex==value.bytes*2, "ui_signal_byte_limit")
    assert(math.type(value.width)=="integer" and math.type(value.height)=="integer"
        and value.width>0 and value.height>0 and value.width==value.height*2
        and value.width*value.height<=ILLUSTRATION_MAX_PIXELS, "ui_signal_dimensions")
    assert(type(value.sha256)=="string" and #value.sha256==64 and not value.sha256:find("[^0-9a-f]"), "ui_signal_digest")
    assert(value.hex:sub(1,32)=="89504e470d0a1a0a0000000d49484452"
        and tonumber(value.hex:sub(33,40),16)==value.width and tonumber(value.hex:sub(41,48),16)==value.height
        and value.hex:sub(49,58)=="0806000000"
        and value.hex:sub(-24)=="0000000049454e44ae426082", "ui_signal_png_header")
    return value
end

function Icons:_illustration_entry(id)
    if self.closed or id~="signal_lost" then return nil,"ui_signal_unknown_id" end
    local entry=self.entries[id]
    if entry then return entry,entry.error end
    local ok,value=pcall(illustration_data)
    if not ok then
        entry={id=id,illustration=true,phase="failed",targets={},error=tostring(value)}
    else
        entry={id=id,illustration=true,hex=value.hex,byteLength=value.bytes,width=value.width,height=value.height,
            bytes={},cursor=1,decodeBytes=ILLUSTRATION_DECODE_BYTES,targets={},phase="keeper",nextTarget=1}
    end
    self.entries[id]=entry;self.order[#self.order+1]=entry
    return entry,entry.error
end

function Icons:illustration_status(id)
    if self.closed or id~="signal_lost" then return "failed" end
    local entry=self.entries[id]
    if not entry then return "unrequested" end
    if entry.phase=="failed" or entry.phase=="failed_bind" then return "failed",entry.error end
    if safe.valid(entry.texture) then return "ready" end
    return "pending"
end

-- The caller owns its Image and its aspect-preserving layout. This function
-- registers a demand without importing/decoding or creating a native keeper.
-- A previously ready brush is rebound immediately; SetBrush match-size stays
-- false so texture pixel dimensions cannot force the panel's desired size.
function Icons:bind_illustration(image,id,fallback)
    if self.closed or not safe.valid(image) then return false,nil,"ui_signal_target_unavailable" end
    local entry,err=self:_illustration_entry(id)
    if not entry or entry.phase=="failed" or entry.phase=="failed_bind" then return false,nil,err end
    if not self.preloaded then self:preload_all() end
    for _,target in ipairs(self.illustration_targets) do
        if target.active~=false and target.image==image and target.entry==entry then
            return safe.valid(entry.texture),target
        end
    end
    if #self.illustration_targets>=MAX_ILLUSTRATION_TARGETS then return false,nil,"ui_signal_target_limit" end
    local target={image=image,fallback=fallback,entry=entry,semantic_id=id,active=true,illustration=true}
    self.illustration_targets[#self.illustration_targets+1]=target
    if safe.valid(entry.texture) then return bind(target,entry.texture),target end
    target.pending_entry=entry;entry.targets[#entry.targets+1]=target
    return false,target
end

function Icons:_controls_settled()
    if not processing.get("automatic_preparation", true) or not processing.get("preload_icons", true)
        or not processing.get("texture_cache_enabled", true) then return true end
    if not self.preloaded then return false end
    for _,id in ipairs(IDS) do
        local entry=self.entries[id]
        if not entry or entry.phase~="done" and entry.phase~="failed" then return false end
    end
    return true
end

function Icons:_track(target)
    local artwork=target.semantic_id=="learn" and "ready" or target.semantic_id
    -- Small slots cannot cross the logical threshold at any DPI. Keep their
    -- semantics on the returned target without adding resize-scan work.
    if DETAILED[artwork] and target.logical_size>=32 and not target.lod_tracked then
        target.lod_tracked=true
        self.lod_targets[#self.lod_targets+1]=target
    end
    return target
end

function Icons:image(id,size)
    id=semantic_id(id)
    size=logical_size(size)
    local entry=self:_entry(M.variant_id(id,size,self.physical_scale))
    if not entry then return nil end
    local cache_hit_failed=false
    local ready_entry=entry.phase~="failed" and entry.phase~="failed_bind" and safe.valid(entry.texture) and entry or nil
    if not ready_entry and entry.id:match("_detailed$") then
        local compact=self.entries[M.variant_id(id,18,self.physical_scale)]
        if compact and safe.valid(compact.texture) then ready_entry=compact end
    end
    if ready_entry then
        local ok,image=pcall(function()
            local widget=self.make("Image")
            widget:SetBrushFromTexture(ready_entry.texture,false)
            widget:SetVisibility(4)
            return widget
        end)
        if ok then
            local target=self:_track({image=image,entry=entry,semantic_id=id,logical_size=size,
                active=true,bound_texture=ready_entry.texture,bound_semantic=id})
            if ready_entry~=entry and entry.phase~="failed" and entry.phase~="failed_bind" then
                entry.targets[#entry.targets+1]=target;target.pending_entry=entry
            end
            return image,target
        end
        cache_hit_failed=true
    end
    local overlay, image, fallback
    local ok = pcall(function()
        overlay=self.make("Overlay");image=self.make("Image");fallback=self.make("TextBlock")
        fallback:SetText(FText(FALLBACK[id] or "?"));fallback:SetAutoWrapText(false)
        fallback:SetJustification(1)
        local font=fallback.Font;font.Size=12;fallback:SetFont(font)
        overlay:AddChild(image);overlay:AddChild(fallback)
        image:SetVisibility(1)
        -- Loading is a quiet empty slot; ASCII symbols are reserved for a real
        -- import/brush failure, rather than flashing during ordinary startup.
        fallback:SetVisibility(1)
    end)
    if not ok then return nil end
    local target=self:_track({image=image,fallback=fallback,entry=entry,semantic_id=id,logical_size=size,active=true})
    if entry.phase=="failed" or entry.phase=="failed_bind" or cache_hit_failed then
        fallback_visible(target)
    else
        if entry.texture~=nil then
            entry.texture=nil
            entry.phase,entry.cursor,entry.bytes,entry.nextTarget="keeper",1,{},1
        end
        entry.targets[#entry.targets+1]=target
        target.pending_entry=entry
    end
    return overlay, target
end

-- Patch an already owned image when one row's recipe changes. Old pending
-- bindings are ignored by entry identity; no widget or worker work is needed.
function Icons:rebind(target,id)
    if type(target)~="table" or target.active==false or not safe.valid(target.image) then return false end
    id=semantic_id(id)
    local entry=self:_entry(M.variant_id(id,target.logical_size,self.physical_scale))
    if not entry then return false end
    target.semantic_id=id
    self:_track(target)
    target.entry=entry
    if entry.phase~="failed" and entry.phase~="failed_bind" and safe.valid(entry.texture) then
        target.pending_entry=nil
        return bind(target,entry.texture)
    end
    if safe.valid(target.fallback) then target.fallback:SetText(FText(FALLBACK[id] or "?")) end
    if entry.phase=="failed" or entry.phase=="failed_bind" then fallback_visible(target);return false end
    if entry.phase=="done" or entry.texture~=nil then
        entry.texture=nil
        entry.phase,entry.cursor,entry.bytes,entry.nextTarget="keeper",1,{},1
    end
    if target.pending_entry~=entry then entry.targets[#entry.targets+1]=target;target.pending_entry=entry end
    return true
end

-- Resize records copied scale only. The ordinary game-thread pump admits one
-- existing target at a time; no widget construction or native loop runs here.
function Icons:set_scale(value)
    if self.closed or type(value)~="number" or value~=value or value<=0 or value==math.huge then return false end
    if self.physical_scale==value then return true end
    self.physical_scale=value
    self.lod_scan_index=#self.lod_targets>0 and 1 or nil
    return true
end

function Icons:retire(target)
    if type(target)~="table" then return end
    target.active,target.pending_entry=false,nil
    target.image,target.fallback,target.bound_texture=nil,nil,nil
    if target.lod_tracked and not self.lod_scan_index and #self.lod_targets>0 then self.lod_scan_index=1 end
end

function Icons:_lod_unit()
    local index=self.lod_scan_index
    local target=index and self.lod_targets[index]
    if not target then self.lod_scan_index=nil;return end
    if target.active==false then
        self.lod_targets[index]=self.lod_targets[#self.lod_targets]
        self.lod_targets[#self.lod_targets]=nil
    else
        local variant=M.variant_id(target.semantic_id,target.logical_size,self.physical_scale)
        if not target.entry or target.entry.id~=variant then self:rebind(target,target.semantic_id) end
        self.lod_scan_index=index+1
    end
    if self.lod_scan_index>#self.lod_targets then self.lod_scan_index=nil end
end

function Icons:_failed(entry)
    entry.bytes=nil
    entry.phase=#entry.targets>0 and "failed_bind" or "failed"
end

-- This image lives in the game's open box, independently of PaintManager's
-- window targets. Its owner requests at most one resource unit per pump.
function Icons:bind_deposit_icon(image, budget)
    local entry = self:_entry("deposit_paint_can")
    if not entry or entry.phase == "failed" or not safe.valid(image) then return true end
    if entry.phase == "done" then
        if safe.valid(entry.texture) then
            image:SetBrushFromTexture(entry.texture, false)
            image:SetVisibility(4)
        end
        return true
    end
    self:_unit(entry)
    if budget then budget:consume(1) end
    return false
end

function Icons:_finish_target(entry)
    local target=entry.targets[entry.nextTarget]
    if target and target.pending_entry==entry then target.pending_entry=nil end
    entry.targets[entry.nextTarget]=false
    entry.nextTarget=entry.nextTarget+1
    if entry.nextTarget>#entry.targets then
        entry.targets,entry.nextTarget={},1
        entry.phase=entry.phase=="failed_bind" and "failed" or "done"
    end
end

function Icons:_unit(entry)
    if entry.phase=="keeper" then
        local ok=pcall(function()
            assert(safe.valid(self.tree) and safe.valid(self.bank))
            if not safe.valid(entry.keeper) then
                entry.keeper=self.make("Image");self.bank:AddChild(entry.keeper)
            end
        end)
        if not ok then self:_failed(entry);return end
        entry.phase="decode"
    elseif entry.phase=="decode" then
        local slice=entry.illustration and processing.get("illustration_decode_bytes", ILLUSTRATION_DECODE_BYTES)
            or processing.get("icon_decode_bytes", DECODE_BYTES)
        local last=math.min(#entry.hex,entry.cursor+slice*2-1)
        for first=entry.cursor,last,2 do
            local pair=entry.hex:sub(first,first+1)
            local byte=tonumber(pair,16)
            if entry.illustration and not pair:match("^[0-9a-f][0-9a-f]$") then byte=nil end
            if byte==nil then entry.error="ui_png_hex_invalid";self:_failed(entry);return end
            entry.bytes[#entry.bytes+1]=byte
        end
        entry.cursor=last+1
        if entry.cursor>#entry.hex then entry.phase="import" end
    elseif entry.phase=="import" then
        local ok,texture=pcall(function()
            local library=StaticFindObject("/Script/Engine.Default__KismetRenderingLibrary")
            if not safe.valid(library) or not safe.valid(self.tree) then return nil end
            return library:ImportBufferAsTexture2D(self.tree,entry.bytes)
        end)
        entry.bytes=nil
        if ok and safe.valid(texture) and texture:IsA("/Script/Engine.Texture2D") then
            -- No native object is published/yielded before the owned keeper
            -- brush holds its texture. Outer/Lua references alone are not roots.
            if safe.valid(entry.keeper) then
                local attached=pcall(function()
                    entry.keeper:SetBrushFromTexture(texture,false)
                end)
                if attached then
                    entry.texture=texture
                    entry.phase=#entry.targets>0 and "bind" or "done"
                else self:_failed(entry) end
            else self:_failed(entry) end
        else self:_failed(entry) end
    elseif entry.phase=="bind" or entry.phase=="failed_bind" then
        local target=entry.targets[entry.nextTarget]
        if entry.phase=="failed_bind" then
            if target and target.active~=false and target.entry==entry and target.pending_entry==entry then fallback_visible(target) end
        elseif not safe.valid(entry.texture) then
            entry.texture=nil
            entry.phase,entry.cursor,entry.bytes="keeper",1,{}
            return
        elseif target and target.active~=false and target.entry==entry and target.pending_entry==entry then
            bind(target,entry.texture)
        end
        self:_finish_target(entry)
    end
end

function Icons:_runnable(entry)
    if processing.get("automatic_preparation", true) and processing.get("preload_icons", true)
        and processing.get("texture_cache_enabled", true) then return true end
    for _,target in ipairs(entry.targets or {}) do
        if target and target.active~=false and target.pending_entry==entry then return true end
    end
    return false
end

function Icons:pump(budget)
    if self.closed or #self.order==0 then return 0 end
    local used=0
    local maximum=processing.batching_enabled() and processing.get("icon_pump_units", MAX_PUMP_UNITS) or math.huge
    while used<maximum and not (budget and budget:expired()) do
        local advanced=false
        if self.lod_scan_index then
            self:_lod_unit()
            if budget then budget:consume(1) end
            used=used+1
            advanced=true
        end
        if not advanced then
        for offset=0,#self.order-1 do
            local index=(self.index+offset-1)%#self.order+1
            local entry=self.order[index]
            if entry.phase~="done" and entry.phase~="failed"
                and self:_runnable(entry)
                and (not entry.illustration or self:_controls_settled()) then
                self:_unit(entry)
                if budget then budget:consume(1) end
                used=used+1
                self.index=index%#self.order+1
                advanced=true
                break
            end
        end
        end
        if not advanced then break end
    end
    return used
end

-- Visible targets belong to a particular window, while keeper brushes belong
-- to the cache host. Closing a window retires only its pending target wrappers.
function Icons:release_targets()
    if self.closed then return end
    for _,target in ipairs(self.lod_targets) do
        target.active,target.pending_entry=false,nil
        target.image,target.fallback,target.bound_texture=nil,nil,nil
    end
    self.lod_targets,self.lod_scan_index={},nil
    for _,target in ipairs(self.illustration_targets) do
        target.active,target.pending_entry=false,nil
        target.image,target.fallback,target.bound_texture=nil,nil,nil
    end
    self.illustration_targets={}
    for _,entry in ipairs(self.order) do
        entry.targets,entry.nextTarget={},1
        if entry.phase=="bind" then entry.phase="done"
        elseif entry.phase=="failed_bind" then entry.phase="failed" end
    end
end

function Icons:close()
    self:release_targets()
    self.closed=true;self.entries,self.order,self.tree,self.make,self.bank={},{},nil,nil,nil
    self.lod_targets,self.lod_scan_index={},nil
    self.illustration_targets={}
end
return M
