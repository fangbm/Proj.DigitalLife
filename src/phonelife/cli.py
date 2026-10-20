"""Safe autonomous, targeted and offline-replay entry points."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

from .benchmark import benchmark, reset_settings
from .config import Config
from .devices import AdbPhone, MockPhone
from .llm import APIPlanner, APIGuiPlanner, ChatAPI, MockGuiPlanner, MockPlanner
from .loop import PhoneLife
from .models import action_schema
from .policy import ActionPolicy
from .replay import replay_task
from .storage import Store
from .autonomy import AutonomousLife
from .brain import APILifeBrain, MockLifeBrain, MockLifeGui
from .budgets import LifeLimits
from .life_storage import control, enable_life
from .soak import run_life, statistics

EXIT_CODES = {"succeeded": 0, "rested": 0, "sleeping": 0, "uncertain": 3,
              "blocked": 4, "failed": 5, "step_limit": 6, "busy": 7}


async def run(args) -> int:
    config = Config.from_env()
    if args.print_schema:
        print(json.dumps(action_schema(), ensure_ascii=False, indent=2)); return 0
    if getattr(args,'export_public',None):
        from .public_evidence import export_public
        store=Store.open_readonly(config.db_path)
        try: print(json.dumps(export_public(store,args.export_public)))
        finally: store.close()
        return 0
    if args.replay:
        store = Store.open_readonly(config.db_path)
        try:
            print(json.dumps(replay_task(store, args.replay), ensure_ascii=False, indent=2))
        finally:
            store.close()
        return 0
    if getattr(args, 'status', False):
        store = Store.open_readonly(config.db_path)
        leases = store
        try:
            path = getattr(args,'lease_db',None) or Path(os.getenv('PHONE_LIFE_LEASE_DB','./data/phone-life.sqlite3'))
            if args.device != 'mock' and path.resolve()!=config.db_path.resolve() and path.is_file():
                leases = Store.open_readonly(path)
            print(json.dumps(statistics(store, args.device, 'mock_test' if getattr(args,'autonomous',False) and args.device=='mock' else 'autonomous', leases), ensure_ascii=False, indent=2))
        finally:
            if leases is not store:
                leases.close()
            store.close()
        return 0
    for option, mode in [('pause','paused'), ('resume','running'), ('emergency_stop','stopped')]:
        if getattr(args, option, False):
            store = Store(config.db_path)
            try:
                enable_life(store)
                control(store, args.device, mode, 'Explicit CLI operator request')
                print(json.dumps({'device': args.device, 'control': mode}))
            finally:
                store.close()
            return 0
    if getattr(args,'prepare_semantics',False):
        from .semantic_storage import prepare_semantics
        if args.device!='emulator':
            raise ValueError('Semantic shell deployment requires explicitly selected emulator')
        phone=AdbPhone(args.serial or config.emulator_serial,'emulator')
        store=Store(config.db_path)
        path=getattr(args,'lease_db',None) or Path(os.getenv('PHONE_LIFE_LEASE_DB','./data/phone-life.sqlite3'))
        leases=store if path.resolve()==config.db_path.resolve() else Store(path)
        try:
            result=await prepare_semantics(store,leases,args.device,phone)
            print(json.dumps(result,ensure_ascii=False))
            return 7 if result['status']=='busy' else 0
        finally:
            if leases is not store: leases.close()
            store.close()
    if args.mode == "api":
        config.require_api()
        if args.device == "mock":
            raise ValueError("API mode needs a screenshot-capable emulator or real device")
    elif args.device != "mock":
        raise ValueError("Mock mode is restricted to the mock device")
    if args.device == "mock":
        phone = MockPhone()
    else:
        serial = args.serial or (config.emulator_serial if args.device == "emulator" else config.real_serial)
        phone = AdbPhone(serial, args.device)
    if args.mode == "mock":
        planner, gui = MockPlanner(), MockGuiPlanner()
    else:
        persona = config.persona_path.read_text(encoding="utf-8") if config.persona_path else ""
        planner = APIPlanner(ChatAPI(config.api_base, config.api_key, json_mode=config.json_mode), config.brain_model, persona)
        gui = APIGuiPlanner(ChatAPI(config.gui_api_base, config.gui_api_key, json_mode=config.json_mode),
                            config.gui_model, config.allowed_apps)
    if args.benchmark_about_phone and (args.device != "emulator" or args.mode != "api" or not args.reset_settings or not args.benchmark_output):
        raise ValueError("Benchmark requires API emulator mode, --reset-settings and a NEW --benchmark-output directory")
    if args.reset_settings and args.device != "emulator":
        raise ValueError("Settings reset is only supported on explicitly selected emulators")
    store = Store(config.db_path)
    lease_store = store
    try:
        lease_path = getattr(args, 'lease_db', None)
        if lease_path is None and args.device != 'mock':
            lease_path = Path(os.getenv('PHONE_LIFE_LEASE_DB', './data/phone-life.sqlite3'))
        if lease_path is not None and lease_path.resolve() != config.db_path.resolve():
            lease_store = Store(lease_path)
        autonomous = (not args.goal and not args.benchmark_about_phone and not getattr(args,'benchmark_pages',None) and not getattr(args, 'legacy_loop', False)
                      and (args.mode == 'api' or getattr(args, 'autonomous', False)))
        controller = None
        run_kind = 'mock_test' if args.mode == 'mock' else 'autonomous'
        if autonomous:
            limits = LifeLimits(max_actions=args.step_limit, hourly_requests=args.hourly_api_limit,
                daily_requests=args.daily_api_limit, consecutive_failures=args.max_failures,
                min_rest_seconds=args.min_rest_seconds, task_timeout_seconds=args.task_timeout,
                max_goal_requests=args.max_goal_requests)
            brain = APILifeBrain(planner.api, config.brain_model, persona) if args.mode == 'api' else MockLifeBrain()
            if args.mode == 'mock':
                gui = MockLifeGui()
            controller = AutonomousLife(store, args.device, brain, limits, run_kind)
        from .semantic import BoundedSemanticProvider
        semantics=BoundedSemanticProvider() if args.mode=='api' and args.device=='emulator' else None
        life = PhoneLife(store, args.device, phone, planner, gui, ActionPolicy(args.device, config.allowed_apps),
            step_limit=args.step_limit, action_delay=0 if args.device == "mock" else config.action_delay_seconds,
            evidence_dir=args.evidence_dir, run_kind=run_kind, autonomy=controller, lease_store=lease_store,
            semantic_provider=semantics)
        if autonomous:
            await run_life(life, cycles=None if args.forever or args.soak_hours else args.cycles,
                           hours=args.soak_hours, output=args.life_output, force=args.force)
            return 0
        if args.benchmark_about_phone:
            summary = await benchmark(life, args.benchmark_about_phone, args.benchmark_output)
            print(json.dumps(summary, ensure_ascii=False))
            return 0 if summary["passed_initial_gate"] else 5
        if getattr(args,'benchmark_pages',None):
            from .semantic_benchmark import benchmark_pages
            if args.device!='emulator' or not args.benchmark_output:
                raise ValueError('Page Benchmark requires API emulator and a NEW --benchmark-output')
            summary=await benchmark_pages(life,args.benchmark_pages,args.benchmark_output)
            print(json.dumps(summary));return 0 if summary['passed_gate'] else 5
        if args.goal:
            result = await life.execute_goal(args.goal, prepare=(lambda: reset_settings(phone)) if args.reset_settings else None)
            print(json.dumps(asdict(result), ensure_ascii=False))
            return EXIT_CODES[result.status]
        if args.forever:
            await life.run_forever()
        else:
            for _ in range(args.cycles):
                result = await life.run_once(force=args.force)
                print(json.dumps(result, ensure_ascii=False))
                if result["status"] not in {"succeeded", "rested"}:
                    break
            return EXIT_CODES[result["status"]]
        return 0
    finally:
        if lease_store is not store:
            lease_store.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description="Phase 1.2 model-led autonomous life loop")
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--device", choices=("mock", "emulator", "real"), default="mock")
    parser.add_argument("--serial", help="Explicit ADB serial; never auto-selects a device")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="Skip wake time only, never the device lease")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--forever", action="store_true")
    mode.add_argument("--goal", help="Run one audited target with an independent verifier")
    mode.add_argument("--replay", metavar="TASK_RUN_ID", help="Offline dry-run only; no device calls")
    mode.add_argument("--benchmark-about-phone", type=int, metavar="TRIALS")
    mode.add_argument('--soak-hours', type=float, help='Bounded autonomous run, respecting persistent sleeps')
    mode.add_argument('--pause', action='store_true', help='Persist pause and cancel active autonomous work')
    mode.add_argument('--resume', action='store_true', help='Clear pause/stop; does not start a process')
    mode.add_argument('--emergency-stop', action='store_true', help='Persist stop, cancel active life session and exit its loop')
    mode.add_argument('--status', action='store_true', help='Read-only life statistics; no model/device construction')
    mode.add_argument('--prepare-semantics',action='store_true',help='Leased, audited read-only shell JAR deployment; no APK or permission grant')
    mode.add_argument('--benchmark-pages',type=int,help='Fixed multi-page semantic GUI trials; explicit leased Settings reset')
    mode.add_argument('--export-public',type=Path,help='Read-only DB export of hashes/action facts; excludes PNG/UI text/model prose')
    parser.add_argument('--autonomous', action='store_true', help='Use new life loop also in offline mock mode')
    parser.add_argument('--legacy-loop', action='store_true', help='Compatibility explore/rest planner, without Phase 1.2 life')
    parser.add_argument('--life-output', type=Path, help='Append a new self-contained observation/evidence session')
    parser.add_argument('--lease-db', type=Path, help='Shared device lease coordinator for every executor of this device')
    parser.add_argument('--hourly-api-limit', type=int, default=120)
    parser.add_argument('--daily-api-limit', type=int, default=500)
    parser.add_argument('--max-goal-requests', type=int, default=32)
    parser.add_argument('--max-failures', type=int, default=3)
    parser.add_argument('--min-rest-seconds', type=int, default=30)
    parser.add_argument('--task-timeout', type=int, default=300)
    parser.add_argument("--reset-settings", action="store_true", help="Explicitly reset isolated emulator to Settings main")
    parser.add_argument("--benchmark-output", type=Path)
    parser.add_argument("--evidence-dir", type=Path, help="Opt in to PNG evidence on an isolated emulator only")
    parser.add_argument("--step-limit", type=int, default=15)
    parser.add_argument("--print-schema", action="store_true")
    args = parser.parse_args()
    if args.soak_hours is not None and (not 0 < args.soak_hours <= 72):
        parser.error('soak-hours must be finite and in (0,72]')
    if args.soak_hours and (args.legacy_loop or (args.mode == 'mock' and not args.autonomous)):
        parser.error('Soak requires the new autonomous life loop')
    if not 1 <= args.cycles <= 100 or not 1 <= args.step_limit <= 100:
        parser.error("cycles and step-limit must be between 1 and 100")
    if args.benchmark_about_phone is not None and not 1 <= args.benchmark_about_phone <= 100:
        parser.error("benchmark trials must be between 1 and 100")
    if args.benchmark_pages is not None and not 1<=args.benchmark_pages<=100:
        parser.error('benchmark-pages must be between 1 and 100')
    if args.reset_settings and not (args.goal or args.benchmark_about_phone or args.benchmark_pages):
        parser.error("--reset-settings requires --goal or --benchmark-about-phone")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (ValueError, FileNotFoundError) as error:
        parser.exit(2, f"Configuration error: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted\n")


if __name__ == "__main__":
    main()
