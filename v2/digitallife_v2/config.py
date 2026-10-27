"""Local configuration; V1 identity files are read, never modified."""
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = ("ro.product.manufacturer", "ro.product.brand", "ro.product.model",
            "ro.product.device", "ro.build.version.release", "ro.build.version.sdk",
            "ro.build.fingerprint", "ro.kernel.qemu")


@dataclass
class Config:
    serial: str
    identity: dict
    adb: str = "adb"
    data: Path = field(default_factory=lambda: ROOT / "data")
    api_base: str = "https://api.deepseek.com"
    api_key: str = field(default="", repr=False)
    model: str = "deepseek-flash"
    vision: bool = False
    full_access: bool = True
    image_token_reserve: int = 4096
    max_input_tokens: int = 16000
    max_output_tokens: int = 600
    requests_hour: int = 60
    requests_day: int = 200
    max_actions: int = 80
    max_cost: float | None = None
    input_price: float | None = None  # operator supplied per million tokens
    output_price: float | None = None
    notification_packages: tuple = ("io.digitallife.companion",)
    content_samples: int = 3
    content_interval: int = 5

    def __post_init__(self):
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.serial)
                or self.serial.startswith(("-", "emulator-"))):
            raise ValueError("PHYSICAL_USB_SERIAL_REQUIRED")
        if set(self.identity) != set(IDENTITY) or any(
                not isinstance(v, str) or len(v) > 1024 or (not v and k != "ro.kernel.qemu")
                for k, v in self.identity.items()):
            raise ValueError("COMPLETE_IDENTITY_REQUIRED")
        if (self.identity["ro.product.manufacturer"].lower() != "oppo"
                or self.identity["ro.product.model"] != "PGFM10"
                or self.identity["ro.kernel.qemu"] not in ("", "0", "false")):
            raise ValueError("FIND_X6_IDENTITY_REQUIRED")
        u = urlsplit(self.api_base)
        if (u.scheme != "https" and not (u.scheme == "http" and u.hostname in
                {"localhost", "127.0.0.1", "::1"})) or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError("HTTPS_OR_LOOPBACK_API_REQUIRED")
        for n in (self.max_input_tokens, self.max_output_tokens, self.requests_hour,
                  self.requests_day, self.max_actions, self.image_token_reserve):
            if type(n) is not int or n < 1:
                raise ValueError("POSITIVE_LIMIT_REQUIRED")
        if type(self.content_samples) is not int or type(self.content_interval) is not int or not 1 <= self.content_samples <= 4 or not 3 <= self.content_interval <= 30:
            raise ValueError("INVALID_CONTENT_SAMPLING_LIMIT")
        if len(self.notification_packages)>20 or any(not isinstance(p,str) or not re.fullmatch(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+", p, re.ASCII) for p in self.notification_packages):
            raise ValueError("INVALID_NOTIFICATION_PACKAGE")
        for n in (self.max_cost, self.input_price, self.output_price):
            if n is not None and (not math.isfinite(n) or n < 0):
                raise ValueError("INVALID_PRICE_OR_COST")
        if self.max_cost is not None and (self.input_price is None or self.output_price is None):
            raise ValueError("MONEY_CAP_REQUIRES_EXPLICIT_RELIABLE_PRICES")
        self.data = Path(self.data).resolve()

    @classmethod
    def load(cls, profile="find-x6"):
        path = (ROOT / "profiles/find-x6.private.json") if profile == "find-x6" else Path(profile)
        if profile == "find-x6" and not path.exists():
            path = ROOT.parent / "data/phase123-find-x6-01/device-probe-02/pending-profile.private.json"
        if path.stat().st_size > 32768:
            raise ValueError("PROFILE_TOO_LARGE")
        p = json.loads(path.read_text(encoding="utf-8-sig"))
        def env(key, default):
            return os.getenv("DIGITALLIFE_V2_" + key, default)
        def optional(key):
            v = env(key, "")
            return float(v) if v else None
        endpoint = env("API_BASE", os.getenv("PHONE_LIFE_API_BASE", "https://api.deepseek.com")).rstrip("/")
        model = env("MODEL", os.getenv("PHONE_LIFE_BRAIN_MODEL", "deepseek-flash"))
        native_vision = urlsplit(endpoint).hostname == "api.deepseek.com" and model == "deepseek-flash"
        return cls(serial=p["serial"], identity=p["identity"],
                   adb=env("ADB", "adb"), data=Path(env("DATA", str(ROOT / "data"))),
                   api_base=endpoint,
                   api_key=env("API_KEY", os.getenv("PHONE_LIFE_API_KEY", "")),
                   model=model, vision=env("VISION", "true" if native_vision else "false").lower() == "true",
                   image_token_reserve=int(env("IMAGE_TOKEN_RESERVE", "1024" if native_vision else "4096")),
                   full_access=env("FULL_ACCESS", "true").lower() == "true",
                   max_input_tokens=int(env("MAX_INPUT_TOKENS", "16000")),
                   max_output_tokens=int(env("MAX_OUTPUT_TOKENS", "600")),
                   requests_hour=int(env("REQUESTS_HOUR", "60")), requests_day=int(env("REQUESTS_DAY", "200")),
                   max_actions=int(env("MAX_ACTIONS", "80")), max_cost=optional("MAX_COST"),
                   input_price=optional("INPUT_PRICE"), output_price=optional("OUTPUT_PRICE"),
                   notification_packages=tuple(p.strip() for p in env("NOTIFICATION_PACKAGES", "io.digitallife.companion").split(",") if p.strip()),
                   content_samples=int(env("CONTENT_SAMPLES", "3")), content_interval=int(env("CONTENT_INTERVAL", "5")))
