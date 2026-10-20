"""Explicit, serial fixed-goal emulator trials. No historical action replay."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import platform
import sqlite3
from pathlib import Path
from PIL import __version__ as pillow_version

from . import __version__
from .models import utc_now
from .verification import AboutPhoneVerifier, IndependentVisionVerifier

ABOUT_PHONE_GOAL = ("从 Android 系统设置主界面进入 About phone（关于手机）页面，观察至少一个设备信息字段后结束。"
                    "优先滚动设置列表找到关于手机，不要进入搜索输入、登录、权限或修改设置。")


async def reset_settings(phone):
    if phone.kind != "emulator" or not phone.serial.startswith("emulator-"):
        raise ValueError("Benchmark reset requires an explicit Android emulator serial")
    if await phone._adb("shell", "getprop", "ro.kernel.qemu") != "1":
        raise ValueError("Device did not identify as an emulator")
    await phone._adb("shell", "am", "force-stop", "com.android.settings")
    await phone._adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    await phone._adb("shell", "am", "start", "-a", "android.settings.SETTINGS", "-f", "0x10008000")
    await asyncio.sleep(2)
    foreground = await phone.foreground()
    if not re.search(r"com\.android\.settings/(?:\.Settings|\.homepage\.SettingsHomepageActivity)(?:[ }\s]|$)", foreground):
        raise RuntimeError("Benchmark reset did not reach Settings main activity")
    return {"reset_method": "force-stop Settings; wake; am start android.settings.SETTINGS NEW_TASK|CLEAR_TASK; wait 2s",
            "setup_mutating_adb_commands": 3, "foreground": foreground}


def source_manifest():
    root = Path(__file__).resolve().parents[2]
    return {str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / "src").rglob("*.py"))}


async def benchmark(life, count: int, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    metadata = {"schema_version": "1.1.1", "application_version": __version__, "run_kind": "benchmark", "started_wall_utc": utc_now(), "trials": count,
                "python": platform.python_version(), "pillow": pillow_version, "sqlite": sqlite3.sqlite_version,
                "progress_detector": "stable-body-tiles/2", "lease_seconds": life.lease_seconds,
                "session_timeout_seconds": life.session_timeout,
                "goal": ABOUT_PHONE_GOAL, "device_serial": life.phone.serial,
                "gui_model": life.gui.model, "provider": life.gui.api.provider,
                "json_mode": life.gui.api.json_mode, "max_steps": life.step_limit,
                "format_repairs": 2, "coordinate_repairs": 1, "max_generations_per_step": 3,
                "verifier_version": AboutPhoneVerifier.version, "source_sha256": source_manifest()}
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    async def audit_under_lease(result, final):
        verification = await AboutPhoneVerifier(life.phone,
            IndependentVisionVerifier(life.gui.api, life.gui.model)).verify(ABOUT_PHONE_GOAL, final, [])
        return {"verification": verification.__dict__, "foreground": await life.phone.foreground()}

    with (output / "trials.jsonl").open("x", encoding="utf-8") as stream:
        for index in range(1, count + 1):
            result = await life.execute_goal(ABOUT_PHONE_GOAL, prepare=lambda: reset_settings(life.phone),
                                             run_kind="benchmark", post_audit=audit_under_lease)
            if not result.task_run_id:
                row = {"trial": index, "status": "blocked", "reason_code": result.reason_code,
                       "task_run_id": "", "summary": result.summary}
            else:
                task = life.store.task(result.task_run_id)
                attempts = [a["payload"] for a in task["attempts"]]
                responses = sum("response_sha256" in a for a in attempts)
                valid = sum(a.get("validation_status") in {"valid", "stagnation_blocked"} for a in attempts)
                # Device access is confined to the callback inside execute_goal's lease.
                protected = result.post_audit or {}
                audit = protected.get("verification", {})
                verifier_calls = (task['result'].get('verification') or {}).get('api_calls', 0)
                row = {"trial": index, "run_kind": "benchmark", "task_run_id": result.task_run_id, "status": result.status,
                    "reason_code": result.reason_code, "summary": result.summary,
                    "api_calls": len(attempts) + verifier_calls + audit.get("api_calls", 0),
                    "gui_api_calls": len(attempts), "verification_api_calls": verifier_calls + audit.get("api_calls", 0),
                    "model_responses": responses, "valid_model_responses": valid,
                    "legal_response_rate": valid / responses if responses else None,
                    "adb_actions_executed": sum(a.get("execution_status") == "executed" for a in attempts),
                    "adb_actions_uncertain": sum(a.get("execution_status") == "uncertain_after_transport_error" for a in attempts),
                    "elapsed_monotonic_ns": task["elapsed_ns"], "verification": task["result"].get("verification"),
                    "independent_audit": audit, "audit_session": protected,
                    "final_observation_sha256": protected.get("observation_sha256", result.final_observation_sha256),
                    "final_foreground": protected.get("foreground", ""),
                    "false_success": protected.get("primary_status") == "succeeded" and audit.get("verdict") != "verified"}
                (output / f"trial-{index:02d}-{result.task_run_id}.json").write_text(
                    json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n"); stream.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)
    succeeded = sum(r["status"] == "succeeded" and not r.get("false_success", False) for r in rows)
    summary = {"completed_trials": len(rows), "verified_successes": succeeded,
        "success_rate": succeeded / count, "false_successes": sum(r.get("false_success", False) for r in rows),
        "passed_initial_gate": count >= 20 and succeeded / count >= .9 and not any(r.get("false_success") for r in rows),
        "total_api_calls": sum(r.get("api_calls", 0) for r in rows),
        "total_adb_actions_executed": sum(r.get("adb_actions_executed", 0) for r in rows),
        "stagnation_stops": sum(r.get("reason_code") == "STAGNATION" for r in rows),
        "scope": "Phase 1.1.1 fixed About phone task; no claim of unattended long-term stability"}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
