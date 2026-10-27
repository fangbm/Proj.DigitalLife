"""Explicit entrypoints; status/stop never load a device profile or an API key."""
import argparse
import asyncio
import json
import sqlite3
from pathlib import Path
import time
from .config import Config, ROOT
from .phone import PhoneController
from .safety import DeviceLock
from .session import SessionLoop
from .memory import LifeMemory
from .store import status


async def doctor(config):
    # Explicit doctor authorizes read-only metadata only, no screenshots or API requests.
    with DeviceLock(config.serial):
        phone = PhoneController(config, ROOT / "data/unused-doctor-screens")
        await phone.identity()
        foreground = await phone.scene()
        apps = await phone.catalog()
        return {"device_identity": "matched", "foreground": foreground, "launchable_apps": len(apps),
                "vision_configured": config.vision, "model": config.model,
                "full_access": config.full_access,
                "api_key_present": bool(config.api_key), "api_contacted": False,
                "cost": "operator_pricing" if config.input_price is not None else "unknown"}


def stop(directory, emergency=False, wait=15):
    state = status(directory)
    directory = Path(directory).resolve()
    (directory / "STOP").touch(exist_ok=True)
    if emergency:
        p = Path(state["emergency_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
    if state["status"] not in {"starting", "running"}:
        return status(directory)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        state = status(directory)
        if state["status"] not in {"starting", "running"}:
            return state
        time.sleep(.2)
    state = status(directory)
    state["stop_wait_expired"] = state["status"] in {"starting", "running"}
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description="DigitalLife V2 — bounded exploration and life continuity")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "run"):
        p = sub.add_parser(name)
        p.add_argument("--profile", default="find-x6", help="find-x6 or private identity JSON path")
        if name == "run":
            p.add_argument("--minutes", type=float, default=10)
            p.add_argument("--session", required=True)
    for name in ("status", "stop"):
        p = sub.add_parser(name)
        p.add_argument("--session", required=True)
        if name == "stop":
            p.add_argument("--emergency", action="store_true", help="also persist a device-wide stop; never auto-cleared")
            p.add_argument("--wait", type=float, default=15)
    p = sub.add_parser("memory", help="read-only local life state and bounded memory preview")
    p.add_argument("--profile", default="find-x6")
    p = sub.add_parser('bridge-setup',help='explicitly install/provision the local notification companion')
    p.add_argument('--profile',default='find-x6')
    p.add_argument('--apk',required=True)
    p.add_argument('--provision-only',action='store_true',help='verify already installed APK bytes, then provision without reinstall')
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            result = status(args.session)
        elif args.command == "stop":
            if not 0 <= args.wait <= 30:
                raise ValueError("STOP_WAIT_MUST_BE_0_TO_30")
            result = stop(args.session, args.emergency, args.wait)
        else:
            config = Config.load(args.profile)
            if args.command == 'bridge-setup':
                from .bridge import setup
                result=asyncio.run(setup(config,args.apk,args.provision_only))
            elif args.command == "memory":
                path = config.data / "life.sqlite3"
                db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                try:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='life_state'").fetchone():
                        result = LifeMemory(db, config.serial).context()
                    else:
                        result = {"state": "not_initialized"}
                finally:
                    db.close()
            elif args.command == "doctor":
                result = asyncio.run(doctor(config))
            else:
                if not config.api_key:
                    raise ValueError("API_KEY_REQUIRED_USE_START_PS1_OR_ENV")
                result = asyncio.run(SessionLoop(config, args.session, args.minutes).run())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") in {"failed", "unknown"} or result.get("stop_wait_expired") else 0
    except (ValueError, OSError, KeyError, sqlite3.Error) as e:
        # Do not leak raw subprocess output, profile values, keys or provider errors.
        code = str(e) if isinstance(e, ValueError) and str(e).replace("_", "").isalnum() else type(e).__name__
        print(json.dumps({"error": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
