"""Fixed ADB operations. No subprocess shell, arbitrary Intent or model argv."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import re
import time
import uuid
from PIL import Image
from .config import IDENTITY
from .perception import Observation, parse_xml, sha, unlocked_awake
from .safety import Blocked, UnknownDispatch, guard, swipe_region

LAUNCH_FLAGS = ("-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER")
COMPONENT = re.compile(r"([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)/([A-Za-z_.$][\w.$]*)", re.ASCII)


def quoted_text(value):
    """One literal argument for Android's remote shell, including apostrophes."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def fixed_text_argument(value):
    if not isinstance(value, str) or len(value) > 1100 or not value.startswith("'") or not value.endswith("'"):
        return False
    decoded = value[1:-1].replace("'\"'\"'", "'")
    return bool(re.fullmatch(r"[ -~]{1,800}", decoded)) and quoted_text(decoded) == value


class PhoneController:
    def __init__(self, config, directory):
        self.config = config
        self.directory = directory
        self.apps = {}
        self.last_windows = ""
        self.rotation = None
        self._recent_apps_requested = False

    async def _adb(self, *args, dispatch=False):
        """Only internal fixed builders call this method; cancellation kills own child."""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(self.config.adb, "-s", self.config.serial, *args,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async def read(stream, limit):
                chunks, count = [], 0
                while part := await stream.read(65536):
                    count += len(part)
                    if count > limit:
                        raise Blocked("ADB_OUTPUT_LIMIT")
                    chunks.append(part)
                return b"".join(chunks)
            out, _, _ = await asyncio.wait_for(asyncio.gather(
                read(proc.stdout, 24_000_000), read(proc.stderr, 8192), proc.wait()), 12)
            if proc.returncode:
                raise Blocked("ADB_COMMAND_FAILED")
            return out
        except BaseException as error:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            if dispatch:
                raise UnknownDispatch("ADB_DISPATCH_UNKNOWN") from error
            raise

    async def _text(self, *args):
        return (await self._adb(*args)).decode("utf-8", "replace")

    async def identity(self):
        if (await self._text("get-state")).strip() != "device":
            raise Blocked("DEVICE_OFFLINE")
        values = {key: (await self._text("shell", "getprop", key)).strip() for key in IDENTITY}
        if values != self.config.identity:
            raise Blocked("DEVICE_IDENTITY_CHANGED")
        return values

    async def catalog(self):
        installed = await self._text("shell", "pm", "list", "packages")
        packages = {s.removeprefix("package:").strip() for s in installed.splitlines() if s.startswith("package:")}
        listing = await self._text("shell", "cmd", "package", "query-activities", "--brief", *LAUNCH_FLAGS)
        self.apps = {}
        for line in listing.splitlines():
            match = COMPONENT.fullmatch(line.strip())
            if match and match[1] in packages:
                self.apps.setdefault(match[1], []).append(match[0])
        if not self.apps or len(self.apps) > 1500:
            raise Blocked("LAUNCHER_INVENTORY_UNAVAILABLE")
        return self.apps

    async def scene(self):
        power = await self._text("shell", "dumpsys", "power")
        policy = await self._text("shell", "dumpsys", "window", "policy")
        windows = await self._text("shell", "dumpsys", "window", "windows")
        displays = await self._text("shell", "dumpsys", "window", "displays")
        self.last_windows = windows
        if not unlocked_awake(power, policy, displays):
            raise Blocked("SCREEN_LOCK_OR_POWER_UNSAFE")
        focuses = re.findall(r"mCurrentFocus=Window\{[^}\r\n]*?\s([\w.]+/[\w.$]+)", windows + "\n" + displays)
        if not focuses or len(set(focuses)) != 1:
            raise Blocked("FOREGROUND_UNVERIFIED")
        input_state = await self._text("shell", "dumpsys", "input")
        rotations = set(re.findall(r"Viewport INTERNAL:[^\r\n]*\bdisplayId=0,[^\r\n]*\borientation=([0-3])\b", input_state))
        rotations.update(re.findall(r"(?:SurfaceOrientation|surfaceOrientation)\s*[:=]\s*([0-3])", input_state))
        if len(rotations) != 1:
            raise Blocked("ORIENTATION_UNVERIFIED")
        self.rotation = int(next(iter(rotations)))
        return focuses[0]

    def check_windows(self, foreground, bounds=None):
        """Reject concrete shown overlays; background retained surfaces are insufficient."""
        blocks = re.split(r"(?=\bWindow #\d+ Window\{)", self.last_windows)
        focus_index = next((i for i, b in enumerate(blocks) if foreground in b.split("\n")[0]), None)
        for index, block in enumerate(blocks):
            shown = bool(re.search(r"isOnScreen\(\)=true", block) and
                         re.search(r"isVisible\(\)=true|mSurfaceShown=true", block))
            if not shown:
                continue
            if re.search(r"DROP_INPUT|TYPE_APPLICATION_OVERLAY|TYPE_SYSTEM_ALERT|\bty=(?:2038|2002|2003)\b", block):
                raise Blocked("VISIBLE_INPUT_OVERLAY")
            if not self.config.full_access and re.search(r"permissioncontroller|securitypermission|biometric|credential", block, re.I):
                raise Blocked("VISIBLE_SECURITY_WINDOW")
            # Reject positively shown foreign app windows above the focus. System bars
            # and the keyboard are checked geometrically against the intended target.
            foreign = re.search(r"\b(?:package|packageName)=([\w.]+)", block)
            if (foreign and foreign[1] != foreground.split("/")[0] and focus_index is not None
                    and index < focus_index and re.search(r"\bty=(?:1|2|3|APPLICATION|BASE_APPLICATION)\b", block)):
                raise Blocked("VISIBLE_FOREIGN_WINDOW")
            if bounds and re.search(r"\bty=(?:2011|INPUT_METHOD|2000|STATUS_BAR|2019|NAVIGATION_BAR)\b", block):
                frame = re.search(r"\b(?:mFrame|frame)=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", block)
                if frame:
                    a, b, c, d = map(int, frame.groups())
                    x1, y1, x2, y2 = bounds
                    if max(a, x1) < min(c, x2) and max(b, y1) < min(d, y2):
                        raise Blocked("TARGET_OCCLUDED_BY_SYSTEM_WINDOW")

    async def observe(self):
        await self.identity()
        before = await self.scene()
        before_rotation = self.rotation
        oid = uuid.uuid4().hex
        raw = await self._text("exec-out", "uiautomator", "dump", "/proc/self/fd/1")
        incomplete = False
        try:
            xml, controls, redacted, rotation = parse_xml(raw, oid, full_access=self.config.full_access)
        except (ValueError, RecursionError):
            xml, controls, redacted, rotation, incomplete = "", [], True, 0, True
        if before != await self.scene() or before_rotation != self.rotation:
            raise Blocked("SCENE_CHANGED_DURING_OBSERVATION")
        self.check_windows(before)
        if not self.config.full_access:
            redacted |= bool(re.search(r"permissioncontroller|securitypermission|credential|biometric", before, re.I))
        # Do not acquire or persist a sensitive PNG/XML, and never send a placeholder as real imagery.
        if redacted:
            return Observation(oid, datetime.now(timezone.utc).isoformat(), self.config.serial,
                               before, 0, 0, rotation, [], True, incomplete)
        png = await self._adb("exec-out", "screencap", "-p")
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise Blocked("INVALID_SCREEN_PNG")
        with Image.open(BytesIO(png)) as image:
            w, h = image.size
            if max(w, h) > 16384 or w * h > 40_000_000:
                raise Blocked("SCREEN_TOO_LARGE")
            image.verify()
        # Check sensitivity again after acquiring PNG, before either artifact is persisted.
        try:
            new_xml, new_controls, new_redacted, new_rotation = parse_xml(
                await self._text("exec-out", "uiautomator", "dump", "/proc/self/fd/1"), oid,
                full_access=self.config.full_access)
        except (ValueError, RecursionError):
            return Observation(oid, datetime.now(timezone.utc).isoformat(), self.config.serial,
                               before, 0, 0, rotation, [], True, True)
        if new_redacted:
            return Observation(oid, datetime.now(timezone.utc).isoformat(), self.config.serial,
                               before, 0, 0, new_rotation, [], True, False)
        if (new_rotation != rotation or (self.rotation is not None and rotation != self.rotation)):
            raise Blocked("ORIENTATION_CHANGED_DURING_OBSERVATION")
        xml, controls = new_xml, new_controls
        if before != await self.scene() or before_rotation != self.rotation:
            raise Blocked("SCENE_CHANGED_DURING_OBSERVATION")
        self.check_windows(before)
        self.directory.mkdir(parents=True, exist_ok=True)
        pp, xp = self.directory / (oid + ".png"), self.directory / (oid + ".xml")
        pp.write_bytes(png)
        xp.write_text(xml, encoding="utf-8")
        overview = not self.config.full_access and (self._recent_apps_requested or bool(
            re.search(r"launcher|quickstep|systemui|recents", before, re.I) and
            re.search(r"recents.?view|recent_apps|overview|task.?view|recent.?task|task.?switch", xml, re.I)))
        return Observation(oid, datetime.now(timezone.utc).isoformat(), self.config.serial, before,
            w, h, rotation, controls, False, False, str(pp), sha(png), str(xp), sha(xml.encode()),
            vision_allowed=not overview, image_withheld_reason="unclassified_task_preview_images" if overview else None)

    async def prepare(self, action, target, observation):
        """All pre-dispatch failures are known non-dispatches."""
        await self.identity()
        fg = await self.scene()
        region = target.bounds if target else None
        if action["type"] == "swipe":
            a, b, c, d = swipe_region(observation, target)
            x = (a + c) // 2
            region = (x - 1, b + (d - b) // 4, x + 1, d - (d - b) // 4)
        self.check_windows(fg, region)
        if (fg != observation.foreground or time.monotonic() - observation.monotonic > 10
                or (self.rotation is not None and self.rotation != observation.rotation)):
            raise Blocked("FINAL_SCENE_CHANGED_OR_EXPIRED")
        kind = action["type"]
        if kind == "open_app":
            await self.catalog()
            package = action["package_name"]
            if package not in self.apps:
                raise Blocked("APP_NO_LONGER_LAUNCHABLE")
            resolved = await self._text("shell", "cmd", "package", "resolve-activity", "--brief", *LAUNCH_FLAGS, "-p", package)
            matches = [s.strip() for s in resolved.splitlines() if COMPONENT.fullmatch(s.strip())]
            if len(matches) != 1 or matches[0] not in self.apps[package]:
                raise Blocked("LAUNCHER_RESOLUTION_CHANGED")
            if await self.scene() != fg:
                raise Blocked("FINAL_SCENE_CHANGED_OR_EXPIRED")
            self.check_windows(fg)
            return ("shell", "am", "start", "-W", *LAUNCH_FLAGS, "-n", matches[0])
        if kind in {"back", "home", "recent_apps"}:
            return ("shell", "input", "keyevent", {"back": "4", "home": "3", "recent_apps": "187"}[kind])
        if kind in {"search", "type_text"}:
            # Select only in the freshly verified focused search editor. The next
            # observation/recheck must still bind that editor before typing.
            return ("shell", "input", "keycombination", "113", "29")  # CTRL+A
        x1, y1, x2, y2 = target.bounds
        x, y = (x1 + x2) // 2, (y1 + y2) // 2
        if kind == "tap":
            return ("shell", "input", "tap", str(x), str(y))
        _, safe_top, _, safe_bottom = swipe_region(observation, target)
        top = safe_top + (safe_bottom - safe_top) // 4
        bottom = safe_bottom - (safe_bottom - safe_top) // 4
        begin, end = (bottom, top) if action["direction"] == "up" else (top, bottom)
        return ("shell", "input", "swipe", str(x), str(begin), str(x), str(end), "350")

    async def dispatch(self, command):
        # Defense at the transport boundary, in addition to strict model parsing.
        launch = (len(command) == 10 and command[:4] == ("shell", "am", "start", "-W")
                  and command[4:8] == LAUNCH_FLAGS and command[8] == "-n"
                  and COMPONENT.fullmatch(command[9]))
        input_command = command[:2] == ("shell", "input") and (
            (len(command) == 4 and command[2] == "keyevent" and command[3] in {"3", "4", "66", "187"}) or
            (command == ("shell", "input", "keycombination", "113", "29")) or
            (len(command) == 5 and command[2] == "tap" and all(s.isdigit() for s in command[3:])) or
            (len(command) == 8 and command[2] == "swipe" and all(s.isdigit() for s in command[3:])) or
            (len(command) == 4 and command[2] == "text" and (
                re.fullmatch(r"'[A-Za-z0-9% .,?_-]{1,240}'", command[3]) or
                self.config.full_access and fixed_text_argument(command[3]))))
        if not (launch or input_command):
            raise Blocked("FIXED_DISPATCH_COMMAND_REQUIRED")
        await self._adb(*command, dispatch=True)
        if command == ("shell", "input", "keyevent", "187"):
            self._recent_apps_requested = True
        elif command[:3] == ("shell", "am", "start") or command in {
                ("shell", "input", "keyevent", "3"), ("shell", "input", "keyevent", "4")}:
            self._recent_apps_requested = False

    def search_editor(self, observation, old_target, expected_text=None, *, kind="search"):
        candidates = [c for c in observation.controls if c.resource == old_target.resource and c.package == old_target.package
                      and c.kind == old_target.kind and c.focused is True]
        if len(candidates) != 1:
            raise Blocked("SEARCH_FOCUS_OR_TEXT_CHANGED")
        target = candidates[0]
        # Unique nonempty resource identity allows normal editor width changes
        # when a clear icon appears. Unnamed widgets keep exact geometry binding.
        if (not target.resource and target.bounds != old_target.bounds
                or expected_text is not None and target.text != expected_text):
            raise Blocked("SEARCH_FOCUS_OR_TEXT_CHANGED")
        guard({"type": kind, "control_id": target.control_id}, observation, {}, full_access=self.config.full_access)
        return target

    async def recheck_search(self, observation, old_target, expected_text, *, kind="search"):
        await self.identity()
        if time.monotonic() - observation.monotonic > 10:
            raise Blocked("SEARCH_OBSERVATION_EXPIRED")
        self.search_editor(observation, old_target, expected_text, kind=kind)
        _, controls, redacted, rotation = parse_xml(await self._text(
            "exec-out", "uiautomator", "dump", "/proc/self/fd/1"), uuid.uuid4().hex,
            full_access=self.config.full_access)
        if redacted or rotation != observation.rotation:
            raise Blocked("SEARCH_FOCUS_OR_TEXT_CHANGED")
        fresh = replace(observation, controls=controls, monotonic=time.monotonic())
        target = self.search_editor(fresh, old_target, expected_text, kind=kind)
        if await self.scene() != observation.foreground:
            raise Blocked("SEARCH_SCENE_CHANGED")
        self.check_windows(observation.foreground, target.bounds)

    async def prepare_search_text(self, selected, action, old_target):
        await self.recheck_search(selected, old_target, old_target.text)
        # Query is validated ASCII, remotely shell-quoted; no model metacharacters.
        return ("shell", "input", "text", "'" + action["query"].replace(" ", "%s") + "'")

    async def prepare_input_text(self, selected, action, old_target):
        await self.recheck_search(selected, old_target, old_target.text, kind="type_text")
        return ("shell", "input", "text", quoted_text(action["text"].replace(" ", "%s")))

    async def prepare_search_submit(self, typed, action, old_target):
        await self.recheck_search(typed, old_target, action["query"])
        return ("shell", "input", "keyevent", "66")
