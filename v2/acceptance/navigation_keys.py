"""Explicit live, scripted acceptance for HOME / APP_SWITCH / BACK. No model API."""
import argparse
import asyncio
import json
from pathlib import Path
from digitallife_v2.config import Config
from digitallife_v2.safety import parse_decision
from digitallife_v2.session import SessionLoop, SessionStopped
from digitallife_v2.store import write_json


class NavigationProbe:
    def __init__(self, config, store):
        self.store, self.index, self.observations = store, 0, []
        self.actions = [{"type": "home"}, {"type": "recent_apps"}, {"type": "back"},
                        {"type": "open_app", "package_name": "com.absinthe.libchecker"}, {"type": "home"}]

    async def decide(self, observation, apps, recent):
        if self.index:
            count = self.store.db.execute("SELECT count(*) FROM events WHERE session=? AND kind='action_dispatched'", (self.store.session,)).fetchone()[0]
            if count != self.index:
                raise SessionStopped("NAVIGATION_STEP_NOT_DISPATCHED")
            self.observations.append({"after_action": self.actions[self.index - 1],
                "observation_id": observation.observation_id, "foreground": observation.foreground,
                "png_path": observation.png_path, "xml_path": observation.xml_path,
                "vision_allowed": observation.vision_allowed, "redacted": observation.redacted,
                "image_withheld_reason": observation.image_withheld_reason})
        if self.index == len(self.actions):
            write_json(self.store.directory / "NAVIGATION_READY.json", {
                "source": "LIVE_DEVICE_SCRIPTED_ACCEPTANCE", "observations": self.observations})
            raise SessionStopped("NAVIGATION_ACCEPTANCE_DONE")
        action = self.actions[self.index]
        self.index += 1
        return parse_decision(json.dumps({"intent": "explore", "action": action, "note": "Explicit navigation key acceptance"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="find-x6")
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    result = asyncio.run(SessionLoop(Config.load(args.profile), args.session, minutes=3,
                                    brain_factory=NavigationProbe).run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["reason"] == "NAVIGATION_ACCEPTANCE_DONE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
