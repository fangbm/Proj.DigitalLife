"""Action checks, process-owned device lock and durable stop barriers."""
from contextlib import AbstractContextManager
import json
from pathlib import Path
import re
import time
from .perception import READ, RISK, SEARCH


class Blocked(ValueError):
    pass


class UnknownDispatch(RuntimeError):
    pass


def parse_decision(content):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise Blocked("DUPLICATE_JSON_KEY")
            result[k] = v
        return result
    try:
        if not isinstance(content, str) or len(content) > 12000:
            raise ValueError()
        d = json.loads(content, object_pairs_hook=unique)
        if not isinstance(d, dict) or set(d) != {"intent", "action", "note"}:
            raise ValueError()
        if d["intent"] not in {"explore", "rest"} or not isinstance(d["note"], str) or len(d["note"]) > 400:
            raise ValueError()
        a = d["action"]
        fields = {"open_app": {"type", "package_name"}, "tap": {"type", "control_id"},
                  "swipe": {"type", "control_id", "direction"}, "back": {"type"},
                  "search": {"type", "control_id", "query"}, "rest": {"type", "seconds"}}
        if not isinstance(a, dict) or a.get("type") not in fields or set(a) != fields[a["type"]]:
            raise ValueError()
        for key in set(a) - {"type", "seconds"}:
            if not isinstance(a[key], str) or len(a[key]) > 256:
                raise ValueError()
        if a["type"] == "rest" and (type(a["seconds"]) is not int or not 1 <= a["seconds"] <= 120):
            raise ValueError()
        if a["type"] == "swipe" and a["direction"] not in {"up", "down"}:
            raise ValueError()
        if a["type"] == "search" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,?_-]{0,79}", a["query"]):
            raise ValueError()
        if a["type"] == "open_app" and not re.fullmatch(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+", a["package_name"], re.ASCII):
            raise ValueError()
        if (d["intent"] == "rest") != (a["type"] == "rest"):
            raise ValueError()
        return d
    except (ValueError, TypeError, KeyError) as e:
        raise Blocked("MODEL_OUTPUT_INVALID") from e


def guard(action, obs, apps):
    kind = action["type"]
    if kind == "rest":
        return None
    if time.monotonic() - obs.monotonic > 45:
        raise Blocked("OBSERVATION_EXPIRED")
    if kind == "open_app":
        if action["package_name"] not in apps:
            raise Blocked("APP_NOT_CURRENTLY_LAUNCHABLE")
        return None
    if obs.redacted or obs.incomplete:
        raise Blocked("SENSITIVE_OR_INCOMPLETE_PAGE")
    if kind == "back":
        return None
    controls = [c for c in obs.controls if c.control_id == action.get("control_id")]
    if len(controls) != 1:
        raise Blocked("CURRENT_CONTROL_REQUIRED")
    c = controls[0]
    x1, y1, x2, y2 = c.bounds
    if (c.package != obs.foreground.split("/")[0] or c.enabled is not True or c.visible is False
            or not (0 <= x1 < x2 <= obs.width and 0 <= y1 < y2 <= obs.height)
            or y1 < obs.height * .035 or y2 > obs.height * .96):
        raise Blocked("TARGET_NOT_USABLE")
    if RISK.search(c.words):
        raise Blocked("EXTERNAL_SIDE_EFFECT_OR_SENSITIVE_TARGET")
    if kind == "swipe":
        if c.scrollable is not True or y2 - y1 < 160:
            raise Blocked("SCROLL_CONTAINER_REQUIRED")
    elif kind == "search":
        if (not SEARCH.search(c.words) or c.focused is not True
                or not ("EditText" in c.kind or c.editable is True) or c.text.strip()):
            raise Blocked("EMPTY_FOCUSED_SEARCH_EDITOR_REQUIRED")
    else:
        own_words = " ".join((c.resource.replace("_", " "), c.text, c.description))
        editor = "EditText" in c.kind or c.editable is True
        if c.clickable is not True or not READ.search(own_words) or (editor and (not SEARCH.search(own_words) or c.text.strip())):
            raise Blocked("UNKNOWN_ACTION_ROLE")
    return c


def rebind(action, before, fresh, apps):
    """Only a current control's unchanged identity can authorize a dispatch."""
    target = guard(action, before, apps)
    if (before.foreground, before.width, before.height, before.rotation) != (
            fresh.foreground, fresh.width, fresh.height, fresh.rotation):
        raise Blocked("SCENE_CHANGED")
    rebound = dict(action)
    if target:
        matches = [c for c in fresh.controls if c.signature == target.signature]
        if len(matches) != 1:
            raise Blocked("TARGET_CHANGED")
        rebound["control_id"] = matches[0].control_id
    return guard(rebound, fresh, apps)


class DeviceLock(AbstractContextManager):
    """OS releases lock after crashes; lock path is global per bound USB serial."""
    def __init__(self, serial):
        self.root = Path.home() / ".digitallife-v2" / serial
        self.file = None

    @property
    def emergency(self):
        return self.root / "EMERGENCY_STOP"

    @property
    def pending(self):
        return self.root / "PENDING_ACTION.json"

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = (self.root / "session.lock").open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.emergency.exists() or self.pending.exists():
                raise Blocked("PERSISTENT_STOP_OR_UNRESOLVED_ACTION")
        except BaseException:
            self.file.close()
            raise
        return self

    def __exit__(self, *args):
        self.file.close()
