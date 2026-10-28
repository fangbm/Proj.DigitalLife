"""Action checks, process-owned device lock and durable stop barriers."""
from contextlib import AbstractContextManager
import json
from pathlib import Path
import re
import time
from .perception import READ, RISK, SEARCH


class Blocked(ValueError):
    pass


class KnownNonDispatch(Blocked):
    """Trusted operation response explicitly confirms no dispatch occurred."""


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
        if not isinstance(d, dict) or set(d) - {"intent", "action", "note", "life", "event_response"} or not {"intent", "action", "note"} <= set(d):
            raise ValueError()
        if d["intent"] not in {"explore", "rest"} or not isinstance(d["note"], str) or len(d["note"]) > 400:
            raise ValueError()
        a = d["action"]
        fields = {"open_app": {"type", "package_name"}, "tap": {"type", "control_id"},
                  "swipe": {"type", "control_id", "direction"}, "back": {"type"},
                  "home": {"type"}, "recent_apps": {"type"},
                  "search": {"type", "control_id", "query"}, "rest": {"type", "seconds"}}
        fields["type_text"] = {"type", "control_id", "text"}
        fields["watch_video"] = fields["read_article"] = {"type", "seconds"}
        fields['inspect_chat'] = {'type'}
        for social_kind in ('reply_to_contact','initiate_dm','group_post'):
            fields[social_kind] = {'type','binding_id'}
        fields['post_video_comment']={'type','source_id'}
        fields['inspect_video_replies']={'type','source_id'}
        fields['reply_video_comment']={'type','thread_id'}
        fields['share_video_to_group']={'type','source_id','binding_id'}
        if not isinstance(a, dict) or a.get("type") not in fields or set(a) != fields[a["type"]]:
            raise ValueError()
        for key in set(a) - {"type", "seconds"}:
            if not isinstance(a[key], str) or len(a[key]) > 256:
                raise ValueError()
        if a["type"] == "rest" and (type(a["seconds"]) is not int or not 1 <= a["seconds"] <= 120):
            raise ValueError()
        if a["type"] in {"watch_video", "read_article"} and (type(a["seconds"]) is not int or not 5 <= a["seconds"] <= 60):
            raise ValueError()
        if a["type"] == "swipe" and a["direction"] not in {"up", "down"}:
            raise ValueError()
        if a["type"] == "search" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,?_-]{0,79}", a["query"]):
            raise ValueError()
        if a["type"] == "type_text" and (not re.fullmatch(r"[ -~]{1,160}", a["text"]) or "%s" in a["text"]):
            raise ValueError()
        if a["type"] == "open_app" and not re.fullmatch(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+", a["package_name"], re.ASCII):
            raise ValueError()
        if (d["intent"] == "rest") != (a["type"] == "rest"):
            raise ValueError()
        if "life" in d:
            life = d["life"]
            limits = {"topic": 40, "goal": 160, "memory_id": 64}
            if not isinstance(life, dict) or set(life) - set(limits):
                raise ValueError()
            if any(not isinstance(value, str) or len(value) > limits[key] for key, value in life.items()):
                raise ValueError()
        if "event_response" in d:
            event = d["event_response"]
            if (not isinstance(event,dict) or set(event)!={"event_id","decision","note"}
                    or not isinstance(event["event_id"],str) or not 1<=len(event["event_id"])<=64
                    or event["decision"] not in {"read_only","reply_later","ignore","resume_activity","reply_now"}
                    or not isinstance(event["note"],str) or len(event["note"])>200):
                raise ValueError()
        return d
    except (ValueError, TypeError, KeyError) as e:
        raise Blocked("MODEL_OUTPUT_INVALID") from e


def guard(action, obs, apps, *, full_access=False):
    kind = action["type"]
    if kind in {'inspect_chat','reply_to_contact','initiate_dm','group_post','post_video_comment','share_video_to_group','inspect_video_replies','reply_video_comment'}:
        return None  # SocialAgent performs scope and fresh identity checks.
    if kind == "rest":
        return None
    if time.monotonic() - obs.monotonic > 45:
        raise Blocked("OBSERVATION_EXPIRED")
    if kind == "open_app":
        if action["package_name"] not in apps:
            raise Blocked("APP_NOT_CURRENTLY_LAUNCHABLE")
        return None
    if kind in {"home", "recent_apps"} or full_access and kind == "back":
        return None
    if obs.redacted or obs.incomplete:
        raise Blocked("SENSITIVE_OR_INCOMPLETE_PAGE")
    if kind in {"back", "watch_video", "read_article"}:
        return None
    controls = [c for c in obs.controls if c.control_id == action.get("control_id")]
    if len(controls) != 1:
        raise Blocked("CURRENT_CONTROL_REQUIRED")
    c = controls[0]
    x1, y1, x2, y2 = c.bounds
    visual=getattr(obs,'visual_controls',{}).get(c.control_id)
    if visual:
        from .visual import crop_sha
        import hashlib
        if (not full_access or kind not in {'tap','swipe'} or kind=='swipe' and visual.get('role') not in {'group_info_scroll','chat_history_scroll'}
                or visual.get('kind') not in {'ocr','vision','template'}
                or not c.kind.startswith('visual.') or c.package!=obs.foreground.split('/')[0]
                or visual.get('bounds')!=list(c.bounds) or visual.get('source_png_sha256')!=obs.png_sha256
                or not obs.png_path or hashlib.sha256(Path(obs.png_path).read_bytes()).hexdigest()!=obs.png_sha256
                or crop_sha(obs.png_path,c.bounds)!=visual.get('crop_sha256')
                or visual.get('confidence',0)<(.995 if visual.get('kind')=='template' else .9 if visual.get('kind')=='vision' else .5)
                or not 0<=x1<x2<=obs.width or not 0<=y1<y2<=obs.height):
            raise Blocked('VISUAL_TARGET_SOURCE_UNVERIFIED')
        return c
    if (c.package != obs.foreground.split("/")[0] or c.enabled is not True or c.visible is False or c.covered
            or not (0 <= x1 < x2 <= obs.width and 0 <= y1 < y2 <= obs.height)
            or (not full_access and kind != "swipe" and (y1 < obs.height * .035 or y2 > obs.height * .96))):
        raise Blocked("TARGET_NOT_USABLE")
    if not full_access and RISK.search(c.words):
        raise Blocked("EXTERNAL_SIDE_EFFECT_OR_SENSITIVE_TARGET")
    if kind == "swipe":
        if c.scrollable is not True:
            raise Blocked("SCROLL_CONTAINER_REQUIRED")
        swipe_region(obs, c)
    elif kind == "search":
        if (not SEARCH.search(c.words) or c.focused is not True
                or not c.is_editor):
            raise Blocked("FOCUSED_SEARCH_EDITOR_REQUIRED")
    elif kind == "type_text":
        if not full_access:
            raise Blocked("FULL_ACCESS_REQUIRED")
        if not c.is_editor or c.focused is not True:
            raise Blocked("FOCUSED_EDITOR_REQUIRED")
    else:
        own_words = c.own_words
        editor = c.is_editor
        clickable = c.clickable is True
        if not clickable and c.tap_owner and "TextView" in c.kind:
            package, _, _, bounds, enabled, visible, risky, covered = c.tap_owner
            a, b, d, e = bounds
            clickable = (package == c.package and enabled is True and visible is not False and (full_access or not risky)
                         and not covered and a <= x1 < x2 <= d and b <= y1 < y2 <= e)
        if not clickable or not full_access and (not READ.search(own_words) or (editor and not SEARCH.search(own_words))):
            raise Blocked("UNKNOWN_ACTION_ROLE")
    return c


def swipe_region(obs, control):
    """Clip the gesture inside a known scroll container, away from fixed edge UI."""
    x1, y1, x2, y2 = control.bounds
    top, bottom = max(y1, int(obs.height * .12)), min(y2, int(obs.height * .82))
    if bottom - top < max(160, obs.height * .18):
        raise Blocked("SCROLL_REGION_TOO_SMALL")
    return (x1, top, x2, bottom)


def rebind(action, before, fresh, apps, *, full_access=False):
    """Only a current control's unchanged identity can authorize a dispatch."""
    target = guard(action, before, apps, full_access=full_access)
    if (before.foreground, before.width, before.height, before.rotation) != (
            fresh.foreground, fresh.width, fresh.height, fresh.rotation):
        raise Blocked("SCENE_CHANGED")
    rebound = dict(action)
    if target:
        matches = [c for c in fresh.controls if c.signature == target.signature]
        if len(matches) != 1:
            raise Blocked("TARGET_CHANGED")
        rebound["control_id"] = matches[0].control_id
    return guard(rebound, fresh, apps, full_access=full_access)


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
