"""Runtime configuration. API keys are never loaded from repository files."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    db_path: Path
    api_base: str
    api_key: str
    brain_model: str
    gui_api_base: str
    gui_api_key: str
    gui_model: str
    persona_path: Path | None
    emulator_serial: str
    real_serial: str
    allowed_apps: frozenset[str]
    step_limit: int = 15
    action_delay_seconds: float = 1.0
    json_mode: str = "auto"

    @classmethod
    def from_env(cls) -> "Config":
        apps = os.getenv("PHONE_LIFE_ALLOWED_APPS", "com.android.chrome,tv.danmaku.bili")
        api_base = os.getenv("PHONE_LIFE_API_BASE", "").rstrip("/")
        api_key = os.getenv("PHONE_LIFE_API_KEY", "")
        persona = os.getenv("PHONE_LIFE_PERSONA_PATH", "")
        return cls(
            db_path=Path(os.getenv("PHONE_LIFE_DB", "./data/phone-life.sqlite3")),
            api_base=api_base,
            api_key=api_key,
            brain_model=os.getenv("PHONE_LIFE_BRAIN_MODEL", ""),
            gui_api_base=os.getenv("PHONE_LIFE_GUI_API_BASE", api_base).rstrip("/"),
            gui_api_key=os.getenv("PHONE_LIFE_GUI_API_KEY", api_key),
            gui_model=os.getenv("PHONE_LIFE_GUI_MODEL", ""),
            persona_path=Path(persona) if persona else None,
            emulator_serial=os.getenv("PHONE_LIFE_EMULATOR_SERIAL", ""),
            real_serial=os.getenv("PHONE_LIFE_REAL_SERIAL", ""),
            allowed_apps=frozenset(a.strip() for a in apps.split(",") if a.strip()),
            json_mode=os.getenv("PHONE_LIFE_JSON_MODE", "auto"),
        )

    def require_api(self) -> None:
        if not all((self.api_base, self.api_key, self.brain_model,
                    self.gui_api_base, self.gui_api_key, self.gui_model)):
            raise ValueError("API mode requires brain and GUI API endpoints, keys and models")
        for base in (self.api_base, self.gui_api_base):
            if not base.startswith("https://") and not (
                base.startswith("http://localhost:") or
                base.startswith("http://127.0.0.1:")
            ):
                raise ValueError("API base must be HTTPS or a loopback HTTP endpoint")

