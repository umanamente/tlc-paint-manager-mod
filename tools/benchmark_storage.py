"""Measure real Lua storage jobs against in-memory native/filesystem fixtures.

This benchmark never touches game state or native save files. Source input is
read-only. CPU times exclude native UFunctions, physical I/O and UI housekeeping;
callback counts are cooperative scheduler simulations rather than measured FPS.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import runpy
import sys
from time import perf_counter, process_time

from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCRIPTS = ROOT / "src/PaintManager/Scripts"

INSTRUMENT = r'''
bench_stats={constructors={},steps={}}
local function instrument_job(job,kind)
    if type(job)~="table" or type(job.step_one)~="function" then return job end
    local original=job.step_one
    job.step_one=function(self,...)
        bench_stats.steps[kind]=(bench_stats.steps[kind] or 0)+1
        return original(self,...)
    end
    return job
end
local function wrap(module_name,methods)
    local module=require("paintmanager."..module_name)
    for _,method in ipairs(methods) do
        local original=module[method]
        if original then
            local kind=module_name.."."..method
            module[method]=function(...)
                bench_stats.constructors[kind]=(bench_stats.constructors[kind] or 0)+1
                return instrument_job(original(...),kind)
            end
        end
    end
end
wrap("json",{"begin"})
wrap("json_reader",{"begin"})
wrap("preset_store",{"begin_validate","begin_load","begin_save"})
wrap("library_schema",{"begin_validate"})
wrap("state_document",{"begin_decode","begin_validate","begin_encode","begin_merge","begin_update","begin_import"})
wrap("local_library",{"begin_load","begin_save"})
function reset_bench()
    bench_stats={constructors={},steps={}}
    operations={};read_sizes={};write_sizes={};reads=0;writes=0;attempts=0;context_reads=0
end
function bench_drain(job,mode)
    local budget_module=require("paintmanager.work_budget")
    local callbacks,units,maximum_units=0,0,0
    local before=os.clock()
    while job.state=="running" do
        callbacks=callbacks+1;assert(callbacks<2000000,"Benchmark job stalled")
        local b=budget_module.new(2,8,mode=="count_only" and function() return 0 end or nil)
        if mode=="housekeeping_overrun" then b.started=b.started-0.003 end
        local p=job:step(b)
        assert(b.units<=8,"Shared unit cap exceeded")
        maximum_units=math.max(maximum_units,b.units);units=units+b.units
        if p.state~="running" then break end
    end
    local progress=job:get_progress()
    local result=job:get_result()
    local doc=result and result.sections and result
    return json.encode({state=job.state,error=progress.error,callbacks=callbacks,chargedUnits=units,
        maximumUnitsPerCallback=maximum_units,luaObservedWorkMs=(os.clock()-before)*1000,
        constructors=bench_stats.constructors,stepOneCalls=bench_stats.steps,
        nativeReads=reads,nativeWrites=writes,nativeAttempts=attempts,nativeContextReads=context_reads,
        filesystemCalls=#operations,pureJsonReadCalls=#read_sizes,pureJsonWriteCalls=#write_sizes,
        resultBytes=files["library.json"] and #files["library.json"],
        livePresets=doc and #doc.sections.presets.data.presets,
        tombstones=doc and #doc.sections.presets.data.tombstones})
end
'''


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def seed_document() -> dict:
    return {
        "format": "PaintManager.State", "schemaVersion": 3, "revision": 2,
        "sections": {
            "settings": {"schemaVersion": 4, "data": {
                "shipRadiusCm": 3500, "sort": "name_asc", "lastPage": "learned", "showNames": False,
                "tileDensity": "comfortable", "showDeveloperTools": False,
                "updatedAt": {key: 0 for key in ("shipRadiusCm", "sort", "lastPage", "showNames", "tileDensity", "showDeveloperTools")}}},
            "favorites": {"schemaVersion": 1, "data": {"records": []}},
            "presets": {"schemaVersion": 2, "data": {"schemaVersion": 2, "tombstones": [], "presets": [{
                "id": "benchmark-existing", "name": "Existing character", "updatedAt": 0,
                "createdUtc": "2026-11-17T12:00:00Z", "gameBuild": "25982825",
                "target": {"kind": "character", "className": "/Script/Voyage.VoyageCharacter", "paintSetName": "Character"},
                "zones": [{"materialSlotName": name, "partName": name,
                           "paint": {"assetType": "Item", "assetName": "DA_Paint_Canada", "displayName": "Last Leaf"}}
                          for name in ("Head", "Scarf", "Arms", "Torso", "Legs")],
            }]}}
        }
    }


def legacy_document(document: dict) -> dict:
    result = deepcopy(document)
    result["schemaVersion"] = 1
    for name in ("settings", "presets"):
        result["sections"][name]["schemaVersion"] = 1
    result["sections"]["settings"]["data"] = {"radiusCm": 7500}
    result["sections"].pop("favorites", None)
    library = result["sections"]["presets"]["data"]
    library["schemaVersion"] = 1
    library.pop("tombstones")
    for record in library["presets"]:
        record.pop("updatedAt")
    return result


def large_document(document: dict, size: int = 32000) -> dict:
    result = deepcopy(document)
    record = deepcopy(result["sections"]["presets"]["data"]["presets"][0])
    rows = []
    for index in range(1, 65):
        candidate = deepcopy(record)
        candidate["id"], candidate["name"] = f"benchmark-{index:03d}", f"Character {index}"
        rows.append(candidate)
        result["sections"]["presets"]["data"]["presets"] = rows
        if len(canonical(result).encode()) > size - 1500:
            rows.pop()
            break
    result["sections"]["presets"]["data"]["presets"] = rows
    result["sections"]["benchmarkOptional"] = {"schemaVersion": 1, "data": ""}
    remainder = size - len(canonical(result).encode())
    assert 0 <= remainder <= 4096
    result["sections"]["benchmarkOptional"]["data"] = "p" * remainder
    assert len(canonical(result).encode()) == size
    return result


def fixtures(scripts: Path) -> str:
    # A frozen baseline may include fixture copies so later production/test
    # edits cannot alter its fake filesystem or native CAS contract.
    fixture_directory = scripts if (scripts / "test_save_store.py").is_file() else ROOT / "tests"
    sys.path.insert(0, str(fixture_directory))
    try:
        source = runpy.run_path(str(fixture_directory / "test_save_store.py"))
        return source["FIXTURE"]
    finally:
        sys.path.pop(0)


def run(scripts: Path, fixture: str, raw: str, scenario: str, mode: str) -> dict:
    runtime = LuaRuntime(unpack_returned_tuples=True)
    runtime.globals().scripts_path = scripts.as_posix()
    runtime.execute('package.path=scripts_path.."/?.lua;"..package.path')
    runtime.execute(INSTRUMENT)
    runtime.execute(fixture)
    runtime.globals().input_raw = raw
    runtime.globals().legacy_raw = canonical(legacy_document(json.loads(raw)))
    runtime.execute('files["library.json"]=input_raw;raw_by_context.A=input_raw')
    expressions = {
        "decode": 'codec.begin_decode(input_raw)',
        "validate": '(function() local d=codec.begin_decode(input_raw);drain(d);return codec.begin_validate(d:get_result()) end)()',
        "noop_sync": 'store.begin_state_load()',
        "refresh_library": 'store.begin_load()',
        "add": 'store.begin_add(preset("benchmark-new","New character",0))',
        "delete": 'store.begin_delete(first_id)',
        "radius_edit": 'store.begin_ui_settings({shipRadiusCm=4500})',
        "legacy_native_to_missing_local": 'store.begin_state_load()',
        "legacy_native_existing_local": 'store.begin_state_load()',
        "divergent_same_id": 'store.begin_state_load()',
        "local_to_missing_native": 'store.begin_state_load()',
    }
    runtime.globals().first_id = json.loads(raw)["sections"]["presets"]["data"]["presets"][0]["id"]
    if scenario == "legacy_native_to_missing_local":
        runtime.execute('files={};raw_by_context.A=legacy_raw')
    elif scenario == "legacy_native_existing_local":
        runtime.execute('raw_by_context.A=legacy_raw')
    elif scenario == "divergent_same_id":
        native = json.loads(raw)
        native["sections"]["presets"]["data"]["presets"][0]["updatedAt"] += 1
        native["sections"]["presets"]["data"]["presets"][0]["name"] = "Newest native"
        runtime.globals().divergent_raw = canonical(native)
        runtime.execute('raw_by_context.A=divergent_raw')
    elif scenario == "local_to_missing_native":
        runtime.execute('raw_by_context.A=nil')
    runtime.execute('reset_bench()')
    runtime.execute('measured_job=' + expressions[scenario])
    if scenario == "validate":
        # Seed decoding is setup, not part of validation measurement.
        runtime.execute('reset_bench()')
    runtime.globals().bench_mode = mode
    start = perf_counter()
    process_start = process_time()
    result = json.loads(runtime.execute('return bench_drain(measured_job,bench_mode)'))
    result["hostProcessCpuMs"] = (process_time() - process_start) * 1000
    result["hostWallMs"] = (perf_counter() - start) * 1000
    if result["state"] != "done":
        raise RuntimeError(f"{scenario}/{mode}: {result.get('error')}")
    output_raw = runtime.eval('files["library.json"]')
    if output_raw:
        output_document = json.loads(output_raw)
        output_library = output_document["sections"]["presets"]["data"]
        result["outputSha256"] = hashlib.sha256(output_raw.encode()).hexdigest()
        result["outputPresets"] = len(output_library["presets"])
        result["outputTombstones"] = len(output_library["tombstones"])
    result["inputBytes"] = len(raw.encode())
    result["inputPresets"] = len(json.loads(raw)["sections"]["presets"]["data"]["presets"])
    result["minimumNominalSecondsAt16msPump"] = result["callbacks"] * 0.016
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripts", type=Path, default=DEFAULT_SCRIPTS)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--modes", nargs="+", default=["count_only", "housekeeping_overrun"])
    parser.add_argument("--datasets", nargs="+", choices=["small", "near32KiB"], default=["small", "near32KiB"])
    parser.add_argument("--max-small-sync-callbacks", type=int,
                        help="Optional deterministic count-only regression gate for small no-op/refresh jobs")
    parser.add_argument("--cases", nargs="+", default=["decode", "validate", "noop_sync", "refresh_library", "add", "delete", "radius_edit", "legacy_native_to_missing_local", "legacy_native_existing_local", "divergent_same_id", "local_to_missing_native"])
    args = parser.parse_args()
    scripts = args.scripts.resolve()
    original = json.loads(args.library.read_text(encoding="utf-8-sig")) if args.library else seed_document()
    document_bytes = canonical(original)
    datasets = {"small": document_bytes, "near32KiB": canonical(large_document(original))}
    fixture = fixtures(scripts)
    report = {
        "scripts": str(scripts), "inputSha256": hashlib.sha256(document_bytes.encode()).hexdigest(),
        "semantics": "Actual Lua jobs with fake native/filesystem boundaries; process CPU excludes real engine/UI/disk and may have coarse Windows resolution. Lua os.clock is an observed work clock, not guaranteed CPU time. Callback counts simulate the 2ms/8-unit budget and 16ms requested pump, not measured frames.",
        "results": {},
    }
    for name, raw in datasets.items():
        if name not in args.datasets:
            continue
        report["results"][name] = {}
        for scenario in args.cases:
            report["results"][name][scenario] = {}
            for mode in args.modes:
                try:
                    result = run(scripts, fixture, raw, scenario, mode)
                except Exception as error:
                    raise RuntimeError(f"{name}/{scenario}/{mode}: {error}") from error
                report["results"][name][scenario][mode] = result
                if args.out:
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                if (args.max_small_sync_callbacks is not None and name == "small" and mode == "count_only"
                        and scenario in ("noop_sync", "refresh_library")
                        and result["callbacks"] > args.max_small_sync_callbacks):
                    raise RuntimeError(f"{scenario} callback regression: {result['callbacks']} > {args.max_small_sync_callbacks}")
    output = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    summary = []
    for name, cases in report["results"].items():
        for case, modes in cases.items():
            for mode, result in modes.items():
                summary.append({"dataset": name, "case": case, "mode": mode,
                                "bytes": result["inputBytes"], "presets": result["inputPresets"],
                                "units": result["chargedUnits"], "callbacks": result["callbacks"],
                                "processCpuMs": round(result["hostProcessCpuMs"], 2),
                                "observedWorkMs": round(result["luaObservedWorkMs"], 2),
                                "nominalSeconds": round(result["minimumNominalSecondsAt16msPump"], 2)})
    if args.out:
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
