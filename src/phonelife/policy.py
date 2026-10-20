"""Hard permissions, separate from repairable schema/pixel validation."""
from __future__ import annotations

import re
from .models import Action, ScreenObservation, ValidationError


class PolicyDenied(ValueError):
    code = "POLICY_DENIED"


def validate_coordinates(action: Action, observation: ScreenObservation) -> None:
    errors = []
    if action.observation_id and action.observation_id != observation.observation_id:
        errors.append({"code": "STALE_OBSERVATION", "field": "observation_id",
                       "value": action.observation_id, "expected": observation.observation_id})
    if action.type in {"tap", "swipe"}:
        for name in (("x", "y") if action.type == "tap" else ("x", "y", "x2", "y2")):
            size = observation.width if name.startswith("x") else observation.height
            v = getattr(action, name)
            if type(v) is not int or not 0 <= v < size:
                errors.append({"code": "INVALID_COORDINATE", "field": name, "value": v,
                               "expected": f"integer in [0,{size - 1}]",
                               "screen": f"{observation.width}x{observation.height}"})
    if errors:
        raise ValidationError(errors)


class ActionPolicy:
    def __init__(self, device_kind: str, allowed_apps: frozenset[str]):
        self.device_kind, self.allowed_apps = device_kind, allowed_apps

    def check(self, action: Action, observation: ScreenObservation | None = None) -> None:
        if self.device_kind == "real" and action.type in {"tap", "swipe", "text"}:
            raise PolicyDenied("Phase 1 blocks coordinate and text control on physical devices")
        if action.type == "launch":
            if action.package not in self.allowed_apps:
                raise PolicyDenied("App package not on the launch allowlist (not an app sandbox)")
            if not re.fullmatch(r"[a-zA-Z_][\w]*(\.[a-zA-Z_][\w]*)+", action.package):
                raise PolicyDenied("Invalid package name")
        if observation is not None:
            validate_coordinates(action, observation)
        elif action.type in {"tap", "swipe"}:
            names = ("x", "y") if action.type == "tap" else ("x", "y", "x2", "y2")
            if any(type(getattr(action, n)) is not int or not 0 <= getattr(action, n) <= 10000 for n in names):
                raise PolicyDenied("Missing or invalid screen coordinates")
        if action.type == "swipe" and (type(action.duration_ms) is not int or not 100 <= action.duration_ms <= 1500):
            raise PolicyDenied("Swipe duration outside bound")
        if action.type == "text":
            if not isinstance(action.text, str) or len(action.text) > 200:
                raise PolicyDenied("Text input over limit")
            if not re.fullmatch(r"[A-Za-z0-9 .,?@_\-]*", action.text):
                raise PolicyDenied("Phase 1 only accepts restricted ASCII text")
            if re.search(r"(password|验证码|银行卡|支付密码|转账|付款|充值|购买|下单)", action.text, re.I):
                raise PolicyDenied("Potentially sensitive text input")
