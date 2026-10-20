"""Bounded, credential-free audit data. Raw screenshots are opt-in only."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
import ipaddress
from enum import Enum
from pathlib import Path

EXCERPT_LIMIT = 4096


class AuditKind(str, Enum):
    CredentialSecret='CredentialSecret'
    PersonalIdentifier='PersonalIdentifier'
    FreeText='FreeText'
    UUID='UUID'
    SHA256='SHA256'
    BooleanSafetyAttribute='BooleanSafetyAttribute'
    NumericMeasurement='NumericMeasurement'
    DeviceActionParameter='DeviceActionParameter'


SAFETY_FIELDS=frozenset({'password','editable','checkable','enabled','clickable','visible','scrollable','long_clickable'})
SECRET_FIELDS=frozenset({'api_key','token','cookie','authorization','password','password_text','credential','secret'})
PERSONAL_FIELDS=frozenset({'email','phone_number','imei','imsi','account','ip_address','mac_address','android_id'})
UUID_FIELDS=frozenset({'task_run_id','source_task_run_id','observation_id','fresh_observation_id','bound_observation_id',
    'initial_observation_id','final_observation_id','memory_id','goal_id','continuation_of','source_run','selected_goal_id'})


def classify(value,key):
    key=key.lower()
    if key in SAFETY_FIELDS and (value is None or type(value) is bool): return AuditKind.BooleanSafetyAttribute
    if key in SECRET_FIELDS or key.endswith(('_api_key','_token','_password','_cookie')): return AuditKind.CredentialSecret
    if key in PERSONAL_FIELDS: return AuditKind.PersonalIdentifier
    if key.endswith('sha256') or key=='digest': return AuditKind.SHA256
    if key in UUID_FIELDS: return AuditKind.UUID
    if key in {'x','y','x2','y2','duration_ms','control_id','scrollable_region_id'}: return AuditKind.DeviceActionParameter
    if type(value) in (int,float): return AuditKind.NumericMeasurement
    return AuditKind.FreeText


def typed_value(value,kind):
    if kind in {AuditKind.CredentialSecret,AuditKind.PersonalIdentifier}: return '[REDACTED]'
    if kind==AuditKind.BooleanSafetyAttribute:
        return value if value is None or type(value) is bool else '[INVALID_SAFETY_ATTRIBUTE]'
    if kind==AuditKind.SHA256:
        if isinstance(value,str) and re.fullmatch(r'[0-9a-fA-F]{64}',value):
            return value if _mask_credentials(value)==value else '[REDACTED]'
        return redact(value) if isinstance(value,str) else value
    if kind==AuditKind.UUID and isinstance(value,str):
        try:
            if str(uuid.UUID(value))==value.lower() and _mask_credentials(value)==value: return value
        except ValueError: pass
        # Legacy/local IDs remain explicitly untyped strings; never repair or invent UUIDs.
        return redact(value)
    if kind==AuditKind.DeviceActionParameter and isinstance(value,str) and re.fullmatch(r'[0-9a-f]{24}',value):
        return value if _mask_credentials(value)==value else '[REDACTED]'
    return redact(value) if isinstance(value,str) else value


def _mask_credentials(text):
    for name,secret in os.environ.items():
        if any(s in name.upper() for s in ('API_KEY','TOKEN','PASSWORD','COOKIE')) and len(secret)>=6:
            text=text.replace(secret,'[REDACTED]')
    text=re.sub(r'(?i)\bsk-[a-z0-9_-]+','[REDACTED]',text)
    return text


def redact(value: str, limit: int = EXCERPT_LIMIT) -> str:
    text = str(value)
    text = _mask_credentials(text)
    def mask_ip(match):
        try:
            ipaddress.ip_address(match.group());return '[REDACTED_IP]'
        except ValueError:return match.group()
    text=re.sub(r'(?<![A-Za-z0-9])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9])',mask_ip,text)
    text=re.sub(r'(?<![A-Za-z0-9])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![A-Za-z0-9])','[REDACTED_MAC]',text)
    text=re.sub(r'(?<![A-Za-z0-9])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![A-Za-z0-9])',mask_ip,text)
    text = re.sub(r"(?i)(bearer\s+)[^\s\"',;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:password|api[_-]?key|token|cookie|authorization|密码|验证码)\s*[=:：]\s*)[^\n,;]+",
                  r"\1[REDACTED]", text)
    # A complete validated identifier can occur under a legacy/arbitrary field name or inside
    # prose. Protect whole tokens AFTER credential removal, before personal-number patterns.
    identifiers=[]
    pattern=r'(?<![A-Za-z0-9])(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{64})(?![A-Za-z0-9])'
    def protect(match):
        token=match.group()
        if len(token)!=64:
            try:uuid.UUID(token)
            except ValueError:return token
        marker='__DIGITALLIFE_IDENTIFIER_'+str(len(identifiers))+'__'
        identifiers.append((marker,token));return marker
    text=re.sub(pattern,protect,text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", text)
    text = re.sub(r"(?<![A-Za-z0-9])(?:\+?86[- ]?)?1[3-9]\d{9}(?![A-Za-z0-9])", "[REDACTED_PHONE]", text)
    text = re.sub(r"(?<![A-Za-z0-9])\d{11,19}(?![A-Za-z0-9])", "[REDACTED_IDENTIFIER]", text)
    text = re.sub(r"(?<!\d)\+\d[\d ()-]{8,20}\d(?!\d)", "[REDACTED_PHONE]", text)
    for marker,token in identifiers:text=text.replace(marker,token)
    return text[:limit]


def scrub(value, key: str = ""):
    kind=classify(value,key)
    if kind in {AuditKind.CredentialSecret,AuditKind.PersonalIdentifier,AuditKind.BooleanSafetyAttribute}:
        return typed_value(value,kind)
    if key.lower()=='text':
        return "[REDACTED]"
    if isinstance(value, dict):
        # Untrusted model IDs may also become dictionary keys in candidate bindings.
        return {typed_value(str(k),AuditKind.UUID): scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        child_key='goal_id' if key in {'eligible_goal_ids','source_runs'} else ''
        if key=='source_runs': child_key='task_run_id'
        return [scrub(v,child_key) for v in value[:(1800 if key=='controls' else 50)]]
    if isinstance(value, str):
        return typed_value(value,kind)
    return value


def response_audit(content: str) -> dict:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        obj = json.loads(content)
        def mask(v, key=""):
            if isinstance(v, dict):
                return {str(k): mask(x, str(k)) for k, x in v.items()}
            if isinstance(v, list):
                return [mask(x) for x in v[:20]]
            if isinstance(v, str) and key not in {"type", "outcome", "package"}:
                return "[REDACTED]"
            return scrub(v, key)
        excerpt = json.dumps(mask(obj), ensure_ascii=False, allow_nan=False)[:EXCERPT_LIMIT]
    except (ValueError, TypeError, RecursionError):
        excerpt = "[REDACTED unstructured model response]"
    return {"response_sha256": digest, "model_response_excerpt": excerpt,
            "response_bytes": len(content.encode("utf-8"))}


class EvidenceSink:
    def __init__(self, directory: Path | None, device_kind: str):
        if directory is not None and device_kind != "emulator":
            raise ValueError("Screenshot evidence is limited to explicitly selected isolated emulators")
        self.directory = directory
        if directory:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def save(self, task_run_id: str, label: str, observation) -> str | None:
        if self.directory is None:
            return None
        target = self.directory / f"{task_run_id}-{label}.png"
        with target.open("xb") as stream:
            stream.write(observation.png)
        target.chmod(0o600)
        return str(target)
