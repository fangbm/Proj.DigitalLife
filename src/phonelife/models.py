"""Versioned GUI contracts. Untrusted values are never coerced into actions."""
from __future__ import annotations

import hashlib
import struct
import time
import uuid
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Literal

ACTION_SCHEMA_VERSION = "1.1"
TaskStatus = Literal["succeeded", "uncertain", "blocked", "failed", "step_limit", "rested", "exploration_observed", "partially_met", "interrupted", "needs_review"]
ActionKind = Literal["tap", "swipe", "text", "back", "home", "launch", "finish"]
FIELDS = {
    "tap": ("x", "y"), "swipe": ("x", "y", "x2", "y2", "duration_ms"),
    "text": ("text",), "launch": ("package",), "home": (), "back": (),
    "finish": ("outcome",),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ValidationError(ValueError):
    code = "MODEL_OUTPUT_INVALID"

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("; ".join(f"{e['field']}: {e['expected']}" for e in errors))

    @property
    def coordinate_error(self) -> bool:
        return any(e["code"] in {"INVALID_COORDINATE", "STALE_OBSERVATION"} for e in self.errors)


@dataclass(frozen=True)
class ScreenObservation:
    png: bytes = field(repr=False)
    width: int
    height: int
    sha256: str
    captured_wall_utc: str
    captured_monotonic_ns: int
    device_serial: str
    observation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @classmethod
    def from_png(cls, png: bytes, device_serial: str) -> ScreenObservation:
        if not isinstance(png, bytes) or len(png) < 33 or len(png) > 32 * 1024 * 1024:
            raise ValueError("SCREEN_INVALID: PNG length outside bounds")
        if png[:8] != b"\x89PNG\r\n\x1a\n" or png[8:16] != b"\0\0\0\rIHDR":
            raise ValueError("SCREEN_INVALID: missing PNG IHDR")
        if zlib.crc32(png[12:29]) & 0xffffffff != struct.unpack(">I", png[29:33])[0]:
            raise ValueError("SCREEN_INVALID: invalid IHDR CRC")
        width, height = struct.unpack(">II", png[16:24])
        if not (1 <= width <= 8192 and 1 <= height <= 8192 and width * height <= 32_000_000):
            raise ValueError("SCREEN_INVALID: dimensions outside bounds")
        return cls(png, width, height, hashlib.sha256(png).hexdigest(), utc_now(),
                   time.monotonic_ns(), device_serial)

    def metadata(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "png"}


@dataclass(frozen=True)
class Decision:
    kind: Literal["explore", "rest"]
    goal: str
    reason: str
    wake_after_seconds: int = 900

    @classmethod
    def from_dict(cls, value: dict) -> Decision:
        kind = value.get("kind")
        seconds = value.get("wake_after_seconds", 900)
        if kind not in ("explore", "rest"):
            raise ValueError("decision.kind must be explore or rest")
        if type(seconds) is not int or not 30 <= seconds <= 86400:
            raise ValueError("wake_after_seconds must be an integer in 30..86400")
        if not isinstance(value.get("goal", ""), str) or not isinstance(value.get("reason", ""), str):
            raise ValueError("decision goal and reason must be strings")
        return cls(kind, value.get("goal", "")[:300], value.get("reason", "")[:500], seconds)


@dataclass(frozen=True)
class Action:
    type: ActionKind
    reason: str = ""
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int = 400
    text: str = ""
    package: str = ""
    outcome: str = ""
    observation_id: str = ""

    @classmethod
    def from_dict(cls, value: dict) -> Action:
        if not isinstance(value, dict):
            raise ValidationError([{"code": "INVALID_SCHEMA", "field": "$", "value": None,
                                    "expected": "one JSON object"}])
        kind = value.get("type")
        if not isinstance(kind, str) or kind not in FIELDS:
            raise ValidationError([{"code": "INVALID_SCHEMA", "field": "type", "value": kind,
                                    "expected": "known action type"}])
        required = {"type", "reason", *FIELDS[kind]}
        errors = []
        for name in sorted(required | set(value)):
            item = value.get(name)
            code, expected = "INVALID_SCHEMA", "required field"
            if name not in required:
                expected = "no unknown fields in action schema 1.1"
            elif name in {"x", "y", "x2", "y2", "duration_ms"}:
                code = "INVALID_COORDINATE" if name != "duration_ms" else "INVALID_SCHEMA"
                expected = "integer (not bool, string or null)"
                if type(item) is int:
                    continue
            elif name == "outcome":
                expected = "achieved, uncertain or cannot_proceed"
                if isinstance(item, str) and item in {"achieved", "uncertain", "cannot_proceed"}:
                    continue
            else:
                limit = {"reason": 400, "text": 200, "package": 200, "type": 20}[name]
                expected = f"string of at most {limit} characters"
                if isinstance(item, str) and len(item) <= limit:
                    continue
            errors.append({"code": code, "field": name, "value": item, "expected": expected})
        if errors:
            raise ValidationError(errors)
        return cls(**value)

    def wire(self) -> dict:
        return {k: getattr(self, k) for k in ("type", "reason", *FIELDS.get(self.type, ()))}

    def bind(self, observation: ScreenObservation) -> Action:
        return replace(self, observation_id=observation.observation_id)


def action_schema() -> dict:
    variants = []
    for kind, fields in FIELDS.items():
        properties = {"type": {"const": kind}, "reason": {"type": "string", "maxLength": 400}}
        for name in fields:
            if name in {"x", "y", "x2", "y2", "duration_ms"}:
                properties[name] = {"type": "integer"}
            elif name == "outcome":
                properties[name] = {"enum": ["achieved", "uncertain", "cannot_proceed"]}
            else:
                properties[name] = {"type": "string", "maxLength": 200}
        variants.append({"type": "object", "properties": properties,
                         "required": list(properties), "additionalProperties": False})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "phonelife:action:1.1",
            "oneOf": variants}


@dataclass(frozen=True)
class VerificationResult:
    verdict: Literal["verified", "not_met", "uncertain", "exploration_observed", "novelty_detected", "goal_partially_met"]
    evidence: list[str]
    reason: str
    evidence_type: str = "none"
    observation_sha256: str = ""
    verifier_version: str = "1.1"
    api_calls: int = 0
    audit: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ActionRecord:
    attempt_id: str
    step_index: int
    action: Action
    observation_sha256: str
    execution_status: str


@dataclass(frozen=True)
class ExecutionResult:
    status: TaskStatus
    summary: str
    steps: int
    reason_code: str = ""
    task_run_id: str = ""
    verification: VerificationResult | None = None
    final_observation_sha256: str = ""
    post_audit: dict | None = None


@dataclass(frozen=True)
class ModelResponse:
    content: str
    api_request_id: str = ""
    provider: str = ""
    model: str = ""
    finish_reason: str = "stop"
