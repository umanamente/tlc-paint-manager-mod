-- The ship actor position is the shared center for stock and connection checks.
-- The stock radius is measured in game-world centimetres.
-- Engine work belongs to the game thread. Retained state is primitive-only.
local safe = require("paintmanager.safe")
local ids = require("paintmanager.persistent_id")
local M = {}
local BARGE = "/Game/Blueprints/Vehicles/BP_Boat_Barge.BP_Boat_Barge_C"
local BOAT = "/Script/Voyage.VoyageBoatPawn"
local CHARACTER = "/Script/Voyage.VoyageCharacter"
local VEHICLE = "/Script/Voyage.VoyageVehiclePawn"
local MISC = "/Script/Voyage.Default__VoyageMiscBlueprintFunctionLibrary"
local PERSIST = "/Script/Voyage.Default__VoyagePersistentSubsystem"
local MIN_RADIUS_CM, MAX_RADIUS_CM, SOURCE_POLICY = 3500, 5000, 2
local cached, active, epoch, last_error, last_code, retry_at = nil, nil, 0, nil, nil, 0

local function finite(n) return type(n)=="number" and n==n and math.abs(n)<math.huge end
local function copy(value)
    if type(value)~="table" then return value end
    local out={};for key,item in pairs(value) do out[key]=copy(item) end;return out
end
local function path(object)
    local name=safe.name(object)
    return name and (name:match("^[^ ]+ (/.+)$") or name) or nil
end
local function is_a(object,class)
    local ok,result=pcall(function() return safe.valid(object) and object:IsA(class) end)
    return ok and result==true
end
local function identity(object)
    local name=safe.name(object)
    local ok,address=pcall(function() return object:GetAddress() end)
    if not name or not ok or type(address)~="number" and type(address)~="string" then return nil end
    return name.."@"..tostring(address)
end
local function ref(object)
    local name,id,class=safe.name(object),identity(object),safe.name(safe.class(object))
    if not name or not id or not class then return nil end
    return {name=name,id=id,className=class}
end
local function resolve(descriptor)
    assert(type(descriptor)=="table" and type(descriptor.name)=="string","Missing ship runtime descriptor")
    local object=StaticFindObject(descriptor.name:match("^[^ ]+ (/.+)$") or descriptor.name)
    assert(identity(object)==descriptor.id and safe.name(safe.class(object))==descriptor.className,
        "Ship runtime object changed")
    return object
end
local function live(object)
    assert(safe.valid(object),"Ship object is unavailable")
    local template=object:HasAnyFlags(0x30) -- RF_ClassDefaultObject | RF_ArchetypeObject
    assert(type(template)=="boolean","Ship template flags are unreadable")
    return not template
end
local function position(actor)
    local point=actor:K2_GetActorLocation()
    local x,y,z=safe.get(point,"X"),safe.get(point,"Y"),safe.get(point,"Z")
    assert(finite(x) and finite(y) and finite(z),"Ship/player position is unavailable")
    return {X=x,Y=y,Z=z}
end
local function discovery_limit(config)
    local maximum=config.ship_max_candidates or 32
    assert(math.type(maximum)=="integer" and maximum>=1 and maximum<=128,"Invalid ship discovery limit")
    return maximum
end
local function settings(config)
    local radius=config.ship_radius_cm
    if radius==nil then radius=MIN_RADIUS_CM end
    -- These constants are an admission boundary. Misconfigured min/max values
    -- cannot widen the supported 35..50 metre range.
    assert(finite(radius) and radius%1==0 and radius>=MIN_RADIUS_CM and radius<=MAX_RADIUS_CM,
        "Ship scan radius must be between 3500 and 5000 centimetres")
    radius=math.tointeger(radius)
    return {radius=radius,maxCandidates=discovery_limit(config)}
end
local function base_context()
    local controller=FindFirstOf("VoyagePlayerController")
    assert(is_a(controller,"/Script/Voyage.VoyagePlayerController") and controller:IsLocalController()==true,
        "Local ship controller unavailable")
    local world,controller_ref=controller:GetWorld(),ref(controller)
    local world_id=identity(world)
    assert(controller_ref and world_id,"Ship world/controller identity unavailable")
    return {controller=controller,worldId=world_id,controllerRef=controller_ref,
        token=world_id.."|"..controller_ref.id}
end
local function library(location,class)
    local object=StaticFindObject(location)
    assert(is_a(object,class),"Ship native library unavailable: "..location)
    return object
end

function M.persistent_actor_id(actor)
    local ok,result=pcall(function()
        assert(safe.valid(actor),"Persistent actor unavailable")
        local component=library(PERSIST,"/Script/Voyage.VoyagePersistentSubsystem"):GetPersistentActorComponent(actor)
        assert(is_a(component,"/Script/Voyage.VoyagePersistentActorComponent"),"Persistent identity component unavailable")
        local actor_id=identity(actor)
        assert(actor_id and identity(component:GetOwner())==actor_id,"Persistent identity owner changed")
        return ids.encode(component.PersistentUniqueIdRuntime)
    end)
    if not ok then return nil,tostring(result) end
    if result=="0000000000000000" then return nil,"Persistent identity is zero or invalid","zero" end
    return result
end

function M.character(controller)
    local ok,character,detail=pcall(function()
        assert(is_a(controller,"/Script/Voyage.VoyagePlayerController"),"Character controller unavailable")
        local pawn=safe.get(controller,"Pawn")
        local result,in_vehicle=pawn,false
        if not is_a(result,CHARACTER) then
            assert(is_a(pawn,VEHICLE),"Current pawn is neither character nor supported vehicle")
            in_vehicle=true
            result=library(MISC,"/Script/Voyage.VoyageMiscBlueprintFunctionLibrary"):GetCharacterOrDriver(pawn)
            assert(is_a(result,CHARACTER),"Current vehicle driver unavailable")
            assert(identity(pawn:GetDriver())==identity(result),"Vehicle driver changed")
        end
        assert(is_a(result,CHARACTER) and identity(result:GetWorld())==identity(controller:GetWorld()),
            "Character world changed")
        return result,{inVehicle=in_vehicle,possessedCharacter=not in_vehicle,pawnRef=ref(pawn),characterRef=ref(result)}
    end)
    if ok then return character,nil,detail end
    return nil,tostring(character)
end
function M.is_character_possessed(controller)
    return is_a(safe.get(controller,"Pawn"),CHARACTER)
end

local function validate_ship(ship,context)
    assert(live(ship) and path(safe.class(ship))==BARGE and is_a(ship,BOAT),"Selected ship type/lifetime changed")
    assert(identity(ship:GetWorld())==context.worldId,"Selected ship world changed")
end
local function resolve_ship(cache,context)
    assert(context.token==cache.baseToken,"Ship controller/world changed")
    local ship=resolve(cache.shipRef)
    validate_ship(ship,context)
    local id=M.persistent_actor_id(ship)
    assert(id==cache.shipId,"Ship persistent identity changed")
    return ship
end
local function read_scope(cache,config)
    local policy,context=settings(config),base_context()
    local ship=resolve_ship(cache,context)
    local center=position(ship)
    local revision="ship_point_v"..SOURCE_POLICY..":"..tostring(policy.radius)
    assert(base_context().token==context.token,"Ship controller/world changed during point capture")
    return {token=context.token.."|"..cache.shipRef.id.."|"..revision,worldId=context.worldId,
        controllerRef=copy(context.controllerRef),runtimeRef=copy(cache.shipRef),shipId=cache.shipId,
        center=center,radiusCm=policy.radius,sourcePolicyVersion=SOURCE_POLICY,policyRevision=revision,
        anchorSource="native_ship_actor_position",radiusPolicy="fixed_35_to_50_metres"}
end

function M.describe(scope) return scope and copy(scope) or nil end
function M.same_scope(a,b)
    return type(a)=="table" and type(b)=="table" and type(a.token)=="string" and a.token==b.token
        and (a.worldId==nil or b.worldId==nil or a.worldId==b.worldId)
        and (a.policyRevision==nil or b.policyRevision==nil or a.policyRevision==b.policyRevision)
        and (a.radiusCm==nil or b.radiusCm==nil or a.radiusCm==b.radiusCm)
end
function M.contains(scope,point)
    if type(scope)~="table" or not finite(scope.radiusCm) or scope.radiusCm<MIN_RADIUS_CM
        or scope.radiusCm>MAX_RADIUS_CM then return false,nil end
    local distance=safe.distance_cm(scope.center,point)
    return distance~=nil and distance<=scope.radiusCm,distance
end
function M.invalidate(reason)
    epoch=epoch+1;cached=nil
    if active then active.state,active.error,active.code="cancelled",reason or "Ship scope invalidated","ship_invalidated" end
    active=nil;last_error,last_code=reason or "Ship scope unavailable","ship_unavailable";retry_at=0
end
function M.current(config,expected)
    if not cached then return nil,last_error or "Ship point has not been prepared",last_code or "ship_unavailable" end
    local ok,result=pcall(read_scope,cached,config)
    if not ok then
        M.invalidate(tostring(result))
        return nil,last_error,last_code
    end
    if expected and not M.same_scope(expected,result) then return nil,"Expected ship scope changed","ship_scope_changed" end
    return result
end
M.capture=M.current

local Job={};Job.__index=Job
function Job:get_progress()
    return {state=self.state,phase=self.phase,error=self.error,code=self.code,
        scope=self.scope and copy(self.scope) or nil,shipLocated=self.state=="done",pointReady=self.state=="done"}
end
function Job:cancel(reason)
    self.state,self.error,self.code="cancelled",reason or "Ship preparation cancelled","ship_cancelled"
    return self:get_progress()
end
local function discover_ship(context,maximum)
    local objects=FindAllOf("VoyageBoatPawn") or {}
    assert(type(objects)=="table","Ship query returned an invalid collection")
    assert(#objects<=maximum,"Ship discovery limit exceeded")
    local selected,count,seen=nil,0,{}
    for _,ship in ipairs(objects) do
        if safe.valid(ship) and path(safe.class(ship))==BARGE and live(ship)
            and identity(ship:GetWorld())==context.worldId then
            local descriptor=assert(ref(ship),"Ship descriptor unavailable")
            if not seen[descriptor.id] then
                seen[descriptor.id]=true;count=count+1
                selected={shipRef=descriptor,shipId=M.persistent_actor_id(ship),baseToken=context.token}
            end
        end
    end
    assert(count>0,"ship_unavailable: no supported loaded Barge")
    assert(count==1,"ship_ambiguous: multiple supported Barges")
    return selected
end

-- A cockpit preset is bound to the boat in the native editor. Resolve its
-- identity once, then verify that same object before storing the layout.
function M.editor_ship(config,expected)
    local ok,result=pcall(function()
        local context=base_context()
        local selected=expected or discover_ship(context,discovery_limit(config))
        resolve_ship(selected,context)
        assert(base_context().token==context.token,"Ship controller/world changed during identity check")
        return selected
    end)
    if ok then return result end
    return nil,tostring(result)
end

function Job:step(limit)
    if self.state~="running" then return self:get_progress() end
    if self.epoch~=epoch then return self:cancel("Ship preparation was superseded") end
    local budget=type(limit)=="table" and limit or nil
    if budget and budget:expired() then return self:get_progress() end
    if not budget then
        local units=limit or 1
        assert(math.type(units)=="integer" and units>=1 and units<=512,"Invalid ship preparation work limit")
    end
    local ok,result=pcall(function()
        local context=base_context()
        local selected=discover_ship(context,self.policy.maxCandidates)
        assert(not self.expectedShipId or selected.shipId==self.expectedShipId,"Selected ship persistent identity changed")
        local scope=read_scope(selected,self.config)
        assert(self.epoch==epoch,"Ship preparation was revoked")
        cached=selected
        return scope
    end)
    if budget then budget:consume(1) end
    if ok then
        self.scope,self.state,self.phase=result,"done","ready"
        last_error,last_code=nil,nil
    else
        self.state,self.phase,self.error="failed","failed",tostring(result)
        self.code=self.error:find("ship_ambiguous",1,true) and "ship_ambiguous" or "ship_unavailable"
        if self.epoch==epoch then cached=nil;last_error,last_code=self.error,self.code;retry_at=os.clock()+2 end
    end
    return self:get_progress()
end
function M.begin(config,options)
    options=options or {};M.invalidate("Preparing the selected ship point")
    local ok,policy=pcall(settings,config)
    if ok and options.expectedShipId~=nil and not ids.is_valid(options.expectedShipId) then
        ok,policy=false,"Expected ship persistent identity is invalid"
    end
    local job=setmetatable({state=ok and "running" or "failed",phase=ok and "discover" or "failed",epoch=epoch,
        policy=ok and policy or nil,error=not ok and tostring(policy) or nil,
        expectedShipId=ok and options.expectedShipId or nil,
        config=ok and {ship_radius_cm=policy.radius,ship_max_candidates=policy.maxCandidates} or nil},Job)
    if not ok then last_error,last_code=job.error,"ship_unavailable";job.code=last_code;retry_at=os.clock()+2 end
    active=job;return job
end
function M.prepare(config,options,budget)
    options=options or {}
    local scope,err=M.current(config)
    if scope and not options.force then return scope,nil,{state="done",phase="ready",scope=copy(scope),pointReady=true} end
    if options.force or not active or active.state~="running" and os.clock()>=retry_at then active=M.begin(config,options) end
    if not active then return nil,err,{state="failed",code=last_code,error=last_error} end
    local progress=active:step(budget or 1)
    if progress.state=="done" then
        local current,current_error=M.current(config)
        return current,current_error,progress
    end
    return nil,progress.error or err or "Ship point is being located",progress
end
function M.player_access(config,expected)
    local scope,err,code=M.current(config,expected)
    if not scope then
        return nil,err,{connected=false,code=code,status=code,reasonCode=code,reason=err,
            shipLocated=false,pointReady=false}
    end
    local detail={connected=false,code="ship_player_unavailable",status="ship_player_unavailable",
        shipLocated=true,pointReady=true,radiusCm=scope.radiusCm}
    local ok,context=pcall(base_context)
    if not ok then return nil,tostring(context),detail end
    local character,char_error,character_detail=M.character(context.controller)
    if not character then return nil,char_error,detail end
    local read,point=pcall(position,character)
    if not read then return nil,tostring(point),detail end
    local connected,distance=M.contains(scope,point)
    detail=character_detail
    detail.connected,detail.distanceCm,detail.displayDistanceCm,detail.radiusCm=connected,distance,distance,scope.radiusCm
    detail.code=connected and "connected" or "signal_lost";detail.status,detail.reasonCode=detail.code,detail.code
    detail.shipLocated,detail.pointReady=true,true
    detail.distanceReference,detail.displayOnly="ship_center",false
    if not connected then return nil,"Character is outside the ship scan radius",detail end
    return scope,nil,detail
end

return M
