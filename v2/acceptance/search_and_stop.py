"""Explicit live search/stop acceptance using production SessionLoop/PhoneController.

Decisions here are scripted test steps, not autonomous model evidence. No model
API is called. Start explicitly, inspect READY.json, then stop with the real CLI.
Run from v2/: python -m acceptance.search_and_stop --session data/sessions/NAME
"""
import argparse
import asyncio
import json
from pathlib import Path

from digitallife_v2.config import Config
from digitallife_v2.perception import SEARCH
from digitallife_v2.safety import Blocked, guard, parse_decision
from digitallife_v2.session import SessionLoop, SessionStopped
from digitallife_v2.store import write_json


class SearchProbe:
    def __init__(self, config, store, package, query):
        self.store, self.package, self.query = store, package, query
        self.opened, self.proposed_search = False, False
        self.steps = 0

    def decision(self, action):
        return parse_decision(json.dumps({"intent": "rest" if action["type"] == "rest" else "explore",
                                          "action": action, "note": "Explicit scripted M1 acceptance step"}))

    async def decide(self, observation, apps, recent):
        self.steps += 1
        if self.steps > 12:
            raise SessionStopped("ACCEPTANCE_STEP_LIMIT")
        if not self.opened:
            self.opened = True
            return self.decision({"type": "open_app", "package_name": self.package})
        if observation.foreground.split("/")[0] != self.package:
            raise SessionStopped("ACCEPTANCE_APP_REDIRECTED")
        if observation.redacted or observation.incomplete:
            raise SessionStopped("ACCEPTANCE_SENSITIVE_OR_INCOMPLETE")
        if self.proposed_search:
            rows = self.store.db.execute("SELECT payload FROM events WHERE session=? AND kind='action_dispatched'",
                                         (self.store.session,)).fetchall()
            actions = [json.loads(r[0])["action"] for r in rows]
            submitted = any(a["type"] == "search_submit" and a.get("query") == self.query for a in actions)
            if not submitted:
                raise SessionStopped("ACCEPTANCE_SEARCH_NOT_SUBMITTED")
            matching = [c for c in observation.controls if self.query.lower() in (c.text + " " + c.description).lower()
                        and not c.covered and c.visible is not False]
            proof = {"source": "LIVE_DEVICE_SCRIPTED_ACCEPTANCE", "query": self.query,
                     "search_submit_dispatched": True, "matching_text_nodes": len(matching),
                     "observation_id": observation.observation_id, "foreground": observation.foreground,
                     "png_path": observation.png_path, "xml_path": observation.xml_path,
                     "instruction": "Inspect real results, then issue the ordinary stop command while resting."}
            write_json(self.store.directory / "READY.json", proof)
            return self.decision({"type": "rest", "seconds": 120})
        editors = [c for c in observation.controls if SEARCH.search(c.words)
                   and c.is_editor
                   and not c.covered]
        for c in editors:
            if c.focused is True:
                action = {"type": "search", "control_id": c.control_id, "query": self.query}
                guard(action, observation, apps)
                self.proposed_search = True
                return self.decision(action)
        for c in observation.controls:
            if not SEARCH.search(c.own_words):
                continue
            action = {"type": "tap", "control_id": c.control_id}
            try:
                guard(action, observation, apps)
            except Blocked:
                continue
            return self.decision(action)
        raise SessionStopped("ACCEPTANCE_SEARCH_CONTROL_UNAVAILABLE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="find-x6")
    parser.add_argument("--session", required=True)
    parser.add_argument("--package", default="com.absinthe.libchecker")
    parser.add_argument("--query", default="settings")
    parser.add_argument("--minutes", type=float, default=3)
    args = parser.parse_args()
    # Validate the query before any device access.
    parse_decision(json.dumps({"intent": "explore", "action": {"type": "search", "control_id": "pending",
                              "query": args.query}, "note": "acceptance"}))
    config = Config.load(args.profile)
    result = asyncio.run(SessionLoop(config, args.session, args.minutes,
        brain_factory=lambda c, s: SearchProbe(c, s, args.package, args.query)).run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["reason"] == "STOP_REQUESTED" and (Path(args.session) / "READY.json").exists() else 2


if __name__ == "__main__":
    raise SystemExit(main())
