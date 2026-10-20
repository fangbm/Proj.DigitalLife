"""Mock and Android Debug Bridge implementations of the phone interface."""

from __future__ import annotations

import asyncio
import functools
import struct
import time
import zlib
from typing import Protocol

from .models import Action, ScreenObservation


class DeviceTransportError(RuntimeError):
    code = "ADB_OUTCOME_UNKNOWN"


@functools.lru_cache(maxsize=16)
def mock_png(width: int = 720, height: int = 1600, color: int = 0) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    rows = (b"\0" + bytes([color % 256]) * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class Phone(Protocol):
    kind: str
    async def describe(self) -> str: ...
    async def screenshot(self) -> bytes | None: ...
    async def act(self, action: Action) -> str: ...
    async def observe(self) -> ScreenObservation: ...
    async def hierarchy(self) -> str: ...
    async def foreground(self) -> str: ...


class MockPhone:
    kind = "mock"

    def __init__(self):
        self.current_app = "home"
        self.actions: list[Action] = []

    async def describe(self) -> str:
        return f"Mock Android device; foreground={self.current_app}"

    async def screenshot(self) -> bytes | None:
        return mock_png(color=1 if self.current_app == "home" else 2)

    async def observe(self) -> ScreenObservation:
        return ScreenObservation.from_png(await self.screenshot(), "mock")

    async def hierarchy(self) -> str:
        return ""

    async def foreground(self) -> str:
        return self.current_app

    async def read_fact(self, name):
        return {'android_version': '15', 'model': 'offline_mock', 'battery_percent': '75',
                'storage_free_kb': '1000000'}[name]

    async def act(self, action: Action) -> str:
        self.actions.append(action)
        if action.type == "launch":
            self.current_app = action.package
        elif action.type == "home":
            self.current_app = "home"
        return f"mock_action={action.type}, foreground={self.current_app}"


class AdbPhone:
    def __init__(self, serial: str, kind: str):
        if kind not in ("emulator", "real"):
            raise ValueError("ADB device kind must be emulator or real")
        if not serial or serial.isspace() or serial.startswith("-"):
            raise ValueError("Supply an explicit ADB device serial")
        self.serial = serial
        self.kind = kind

    async def _adb(self, *args: str, binary: bool = False, timeout: float = 25) -> bytes | str:
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", self.serial, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, errors = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise RuntimeError("ADB command timed out") from None
        except asyncio.CancelledError:
            proc.kill()
            await proc.communicate()
            raise
        if proc.returncode != 0:
            raise RuntimeError("ADB failed: " + errors.decode("utf-8", "replace")[:350])
        return output if binary else output.decode("utf-8", "replace").strip()

    async def describe(self) -> str:
        state = await self._adb("get-state")
        return f"Android device serial={self.serial}, kind={self.kind}, state={state}"

    async def screenshot(self) -> bytes | None:
        raw = await self._adb("exec-out", "screencap", "-p", binary=True)
        if not isinstance(raw, bytes) or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("ADB screencap returned no valid PNG")
        return raw

    async def observe(self) -> ScreenObservation:
        if self.kind == "emulator" and not getattr(self, "_emulator_checked", False):
            if await self._adb("shell", "getprop", "ro.kernel.qemu") != "1":
                raise ValueError("Selected device is not an Android emulator; coordinate control refused")
            self._emulator_checked = True
        return ScreenObservation.from_png(await self.screenshot(), self.serial)

    async def hierarchy(self) -> str:
        if time.monotonic() < getattr(self, "_hierarchy_cooldown", 0):
            raise RuntimeError("UI hierarchy temporarily unavailable")
        try:
            raw = await self._adb("exec-out", "uiautomator", "dump", "/dev/tty", timeout=12)
        except RuntimeError:
            self._hierarchy_cooldown = time.monotonic() + 60
            raise
        start, end = raw.find("<?xml"), raw.rfind("</hierarchy>")
        if start < 0 or end < 0 or len(raw) > 2_000_000:
            self._hierarchy_cooldown = time.monotonic() + 60
            if 'could not get idle state' in raw:
                raise RuntimeError('UIAUTOMATOR_IDLE_TIMEOUT: waitForIdle(1000ms,10000ms)')
            raise RuntimeError("UI hierarchy unavailable")
        return raw[start:end + len("</hierarchy>")]

    async def foreground(self) -> str:
        output = await self._adb("shell", "dumpsys", "activity", "activities")
        return next((line.strip() for line in output.splitlines() if "topResumedActivity=" in line), "")

    async def read_fact(self, name):
        # Fixed read-only commands; the model cannot supply shell arguments.
        if name in {'android_version', 'model'}:
            prop = {'android_version': 'ro.build.version.release', 'model': 'ro.product.model'}[name]
            return await self._adb('shell', 'getprop', prop)
        if name == 'battery_percent':
            output = await self._adb('shell', 'dumpsys', 'battery')
            import re
            match = re.search(r'^\s*level:\s*(\d+)\s*$', output, re.M)
            if not match or not 0 <= int(match.group(1)) <= 100:
                raise ValueError('Battery level unavailable')
            return match.group(1)
        if name == 'storage_free_kb':
            output = await self._adb('shell', 'df', '-k', '/data')
            fields = output.splitlines()[-1].split()
            if len(fields) < 4 or not fields[3].isdigit():
                raise ValueError('Storage measurement unavailable')
            return fields[3]
        raise ValueError('Fact capability unavailable')

    async def act(self, action: Action) -> str:
        try:
            return await self._act_once(action)
        except (RuntimeError, OSError) as error:
            raise DeviceTransportError(f"ADB action outcome unknown ({type(error).__name__}); observe before proceeding") from None

    async def reopen_settings(self):
        # Fixed launch intent only: no model arguments, force-stop, data reset, or permission changes.
        if self.kind!='emulator' or await self._adb('shell','getprop','ro.kernel.qemu')!='1':
            raise ValueError('Recovery launch restricted to isolated QEMU emulators')
        try:
            output=await self._adb('shell','am','start','-W','-a','android.settings.SETTINGS','-p','com.android.settings','-f','0x14000000')
            if 'Error:' in output or 'Status: ok' not in output:
                raise RuntimeError('Settings recovery launch not confirmed')
        except (RuntimeError,OSError) as error:
            raise DeviceTransportError('Settings recovery outcome unknown; no resend') from None

    async def _act_once(self, action: Action) -> str:
        if action.type == "home":
            await self._adb("shell", "input", "keyevent", "3")
        elif action.type == "back":
            await self._adb("shell", "input", "keyevent", "4")
        elif action.type == "launch":
            await self._adb("shell", "monkey", "-p", action.package, "-c",
                            "android.intent.category.LAUNCHER", "1")
        elif action.type == "tap":
            await self._adb("shell", "input", "tap", str(action.x), str(action.y))
        elif action.type == "swipe":
            await self._adb("shell", "input", "swipe", str(action.x), str(action.y),
                            str(action.x2), str(action.y2), str(action.duration_ms))
        elif action.type == "text":
            # 'adb input text' has problematic Unicode handling: do not silently corrupt it.
            if not action.text.isascii():
                raise ValueError("Phase 1 ADB text input only supports ASCII")
            await self._adb("shell", "input", "text", action.text.replace(" ", "%s"))
        else:
            raise ValueError("Unsupported ADB action")
        return f"adb_action={action.type}"

