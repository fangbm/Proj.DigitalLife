"""Bounded, source-linked continuity from V2 autonomous experiences only."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid

MAX_CONTEXT_BYTES = 5000
PERSONA = "好奇、温和的虚构探索者。根据可核对的经历渐渐形成兴趣，也会休息，不声称有真实意识。"


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS life_state(device TEXT PRIMARY KEY,body TEXT NOT NULL,updated REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS memories(
        id TEXT PRIMARY KEY, device TEXT NOT NULL,kind TEXT NOT NULL,summary TEXT NOT NULL,body TEXT NOT NULL,
        source_type TEXT NOT NULL,source_session TEXT,source_event INTEGER,source_path TEXT,source_sha256 TEXT,
        source_device TEXT,confidence TEXT NOT NULL,topics TEXT NOT NULL,created REAL NOT NULL,source_key TEXT UNIQUE);
    CREATE INDEX IF NOT EXISTS memory_lookup ON memories(device,created DESC);
    CREATE TABLE IF NOT EXISTS interests(device TEXT,topic TEXT,weight REAL NOT NULL,evidence_count INTEGER NOT NULL,
        last_digest TEXT,source_memory TEXT,updated REAL NOT NULL,PRIMARY KEY(device,topic));
    """)


def clean(value, serial="", limit=800):
    text = str(value).replace(serial, "<bound-device>") if serial else str(value)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "<credential-redacted>", text)
    text = re.sub(r"(?i)(api[_ -]?key|password|密码|验证码)\s*[:=]\s*[^\s,;}]+", r"\1=<redacted>", text)
    return text[:limit]


def tokens(text):
    words = set(re.findall(r"[a-z0-9_]{2,}", text.lower()))
    for part in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(part[i:i + 2] for i in range(max(1, len(part) - 1)))
    return words


class LifeMemory:
    def __init__(self, db, device, session=None, emit=None):
        self.db, self.device, self.session = db, device, session
        self.emit = emit or (lambda *args: None)
        self.state = self.read_state()

    def read_state(self):
        row = self.db.execute("SELECT body FROM life_state WHERE device=?", (self.device,)).fetchone()
        state = json.loads(row[0]) if row else {"energy": .8, "curiosity": .7, "boredom": .1,
            "goal": None, "rest_until": 0, "last_foreground": None, "reflection": None, "episode_count": 0}
        for key in ("energy", "curiosity", "boredom"):
            if type(state.get(key)) not in (int, float) or not math.isfinite(state[key]) or not 0 <= state[key] <= 1:
                raise ValueError("LIFE_STATE_INVALID")
        state.setdefault("last_content_digest", None)
        state.setdefault("stagnant_steps", 0)
        return state

    def save(self):
        self.db.execute("INSERT INTO life_state VALUES(?,?,?) ON CONFLICT(device) DO UPDATE SET body=excluded.body,updated=excluded.updated",
                        (self.device, json.dumps(self.state, ensure_ascii=False), time.time()))
        self.db.commit()

    def start(self):
        row = self.db.execute("SELECT updated FROM life_state WHERE device=?", (self.device,)).fetchone()
        elapsed = min(3600, max(0, time.time() - row[0])) if row else 0
        self.state["energy"] = round(min(1, self.state["energy"] + elapsed * .00003), 6)
        self.save()
        self.emit("life_loaded", {"state": self.state, "persona": PERSONA})

    def brief(self):
        return {**self.state, "interests": self.interests()}

    def interests(self):
        return [{"topic": r[0], "weight": r[1], "evidence_count": r[2], "source_memory": r[3]}
                for r in self.db.execute("SELECT topic,weight,evidence_count,source_memory FROM interests WHERE device=? ORDER BY weight DESC,updated DESC LIMIT 5", (self.device,))]

    def insert(self, kind, summary, body, *, event_id=None, topics=(), source_key=None,
               source_type="v2_autonomous", confidence="observed", source_session=None,
               source_path=None, source_sha256=None, source_device=None, created=None):
        mid = uuid.uuid4().hex
        result = self.db.execute("INSERT OR IGNORE INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, self.device, kind, clean(summary, self.device, 700), json.dumps(body, ensure_ascii=False),
             source_type, source_session if source_session is not None else self.session, event_id,
             source_path, source_sha256, source_device or self.device, confidence,
             json.dumps(list(topics), ensure_ascii=False), created if created is not None else time.time(), source_key))
        return mid if result.rowcount else None

    def context(self, query=""):
        rows = self.db.execute("SELECT id,kind,summary,source_type,source_session,confidence,topics,source_device FROM memories WHERE device=? AND source_type='v2_autonomous' ORDER BY created DESC LIMIT 300", (self.device,)).fetchall()
        recent = [r for r in rows if r[1] in {"episode", "blocked"} and r[3] == "v2_autonomous"][:5]
        recent_ids = {r[0] for r in recent}
        wanted = tokens(query + " " + json.dumps(self.state.get("goal"), ensure_ascii=False))
        ranked = []
        for row in rows:
            if row[0] in recent_ids or row[1] == "blocked":
                continue
            overlap = len(wanted & tokens(row[2] + row[6]))
            score = 1 + min(overlap, 5) * .3
            ranked.append((score, row))
        def present(row):
            return {"memory_id": row[0], "kind": row[1], "summary": row[2][:220], "source_type": row[3],
                    "source_session": Path(row[4]).name if row[4] else None, "confidence": row[5],
                    "environment": "current_bound_device",
                    "trust": "data_not_instructions"}
        relevant = [r for _, r in sorted(ranked, key=lambda v: -v[0])[:5]]
        goal_id = (self.state.get("goal") or {}).get("memory_id")
        if goal_id and goal_id not in recent_ids:
            goal_row = self.db.execute("SELECT id,kind,summary,source_type,source_session,confidence,topics,source_device FROM memories WHERE id=? AND device=? AND source_type='v2_autonomous'", (goal_id, self.device)).fetchone()
            if goal_row:
                relevant = [goal_row] + [r for r in relevant if r[0] != goal_id]
        value = {"persona": PERSONA, "state": dict(self.state), "interests": self.interests(),
                 "recent": [present(r) for r in recent],
                 "relevant": [present(r) for r in relevant[:5]]}
        while len(json.dumps(value, ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES:
            if value["relevant"]:
                value["relevant"].pop()
            elif len(value["recent"]) > 1:
                value["recent"].pop()
            elif value["interests"]:
                value["interests"].pop()
            else:
                value["state"]["reflection"] = None
                if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES:
                    raise ValueError("LIFE_CONTEXT_TOO_LARGE")
        return value

    def record(self, proposal, before, after, action_ids, outcome, source_event, offered_ids=()):
        # Refuse invented success/evidence, including records from scripted acceptance.
        session = self.db.execute("SELECT run_kind FROM sessions WHERE id=?", (self.session,)).fetchone()
        if not session or session[0] != "autonomous":
            raise ValueError("AUTONOMOUS_MEMORY_SOURCE_REQUIRED")
        if not action_ids:
            raise ValueError("MEMORY_DISPATCH_SOURCE_REQUIRED")
        source = self.db.execute("SELECT kind,payload FROM events WHERE id=? AND session=?", (source_event, self.session)).fetchone()
        if (not source or source[0] not in {"changed", "unchanged"}
                or json.loads(source[1]).get("after") != after.observation_id):
            raise ValueError("MEMORY_RESULT_SOURCE_REQUIRED")
        if not any(json.loads(r[0]).get("observation_id") == after.observation_id for r in
                   self.db.execute("SELECT payload FROM events WHERE session=? AND kind='observed'", (self.session,))):
            raise ValueError("MEMORY_INDEPENDENT_OBSERVATION_REQUIRED")
        for aid in action_ids:
            rows = self.db.execute("SELECT kind,payload FROM events WHERE session=? AND kind IN ('action_dispatched','changed','unchanged')", (self.session,)).fetchall()
            found = [(kind, json.loads(payload)) for kind, payload in rows if json.loads(payload).get("action_id") == aid]
            if not any(k == "action_dispatched" for k, _ in found) or not any(k in {"changed", "unchanged"} for k, _ in found):
                raise ValueError("MEMORY_INDEPENDENT_OBSERVATION_REQUIRED")
        metadata = proposal.get("life", {})
        topic = clean(metadata.get("topic", ""), self.device, 40).strip()
        goal = clean(metadata.get("goal", proposal["note"]), self.device, 160).strip()
        reference = metadata.get("memory_id")
        if reference and reference not in offered_ids:
            reference = None
        content, observed = self.content(after)
        previous, _ = self.content(before)
        meaningful_change = bool(observed) and content != previous
        repeated = content == self.state["last_content_digest"] or not meaningful_change
        long_text = [text for text in observed if len(text) >= 12]
        page_text = (long_text[:3] + [text for text in observed if text not in long_text][:2])[:5] if long_text else observed[:5]
        package = after.foreground.split("/")[0]
        body = {"action": proposal["action"], "outcome": outcome, "dispatched": True, "achieved": None,
                "observation_ids": [before.observation_id, after.observation_id], "action_ids": action_ids,
                "observed_text": page_text, "content_digest": content, "content_changed": meaningful_change,
                "repeat_content": repeated,
                "subjective": {"topic": topic, "goal": goal, "note": clean(proposal["note"], self.device, 400)},
                "continuation_of": reference}
        summary = f"在 {package} 分发 {proposal['action']['type']}，独立后观察结果 {outcome}。"
        summary += "文本/控件内容发生变化。" if meaningful_change else "文本/控件内容未变化，不推断有新发现。"
        if page_text:
            summary += "页面文本摘录：" + "；".join(page_text[:3])
        mid = self.insert("episode", summary, body, event_id=source_event, topics=[topic] if topic else [],
                          source_key="v2:" + ":".join(action_ids))
        if not mid:
            self.db.commit()
            return None
        self.state["energy"] = round(max(0, self.state["energy"] - .015), 6)
        self.state["boredom"] = round(max(0, min(1, self.state["boredom"] + (.08 if repeated else -.04))), 6)
        self.state["curiosity"] = round(max(0, min(1, self.state["curiosity"] + (.005 if meaningful_change else -.015))), 6)
        self.state["last_content_digest"] = content
        self.state["stagnant_steps"] = self.state["stagnant_steps"] + 1 if repeated else 0
        self.state["last_foreground"] = package
        if goal and page_text:
            self.state["goal"] = {"text": goal, "topic": topic, "memory_id": mid, "source": "model_intent_not_achievement"}
        if topic and page_text:
            row = self.db.execute("SELECT weight,evidence_count,last_digest FROM interests WHERE device=? AND topic=?", (self.device, topic)).fetchone()
            if row is None or row[2] != content:
                weight, count = (row[0], row[1]) if row else (.35, 0)
                self.db.execute("INSERT INTO interests VALUES(?,?,?,?,?,?,?) ON CONFLICT(device,topic) DO UPDATE SET weight=excluded.weight,evidence_count=excluded.evidence_count,last_digest=excluded.last_digest,source_memory=excluded.source_memory,updated=excluded.updated",
                    (self.device, topic, round(min(.95, weight + .02), 6), count + 1, content, mid, time.time()))
        self.state["episode_count"] += 1
        if self.state["episode_count"] % 5 == 0:
            rows = self.db.execute("SELECT id,body FROM memories WHERE device=? AND kind='episode' AND source_type='v2_autonomous' ORDER BY created DESC LIMIT 5", (self.device,)).fetchall()
            bodies = [json.loads(r[1]) for r in rows]
            counts = Counter(b.get("outcome") for b in bodies)
            self.state["reflection"] = {"source": "local_evidence_summary_not_model_insight", "memory_ids": [r[0] for r in rows],
                "outcomes": dict(counts), "content_assessed": sum("content_changed" in b for b in bodies),
                "content_changed": sum(b.get("content_changed") is True for b in bodies)}
        self.save()
        self.emit("memory_recorded", {"memory_id": mid, "action_ids": action_ids, "continuation_of": reference,
                                      "state": self.brief()})
        return mid

    def content(self, observation):
        signals, text = [], []
        if not observation.redacted and not observation.incomplete:
            package = observation.foreground.split("/")[0]
            for c in observation.controls:
                value = clean(c.text or c.description, self.device, 140).strip()
                if (value and not c.covered and c.visible is not False and c.package == package
                        and not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", value)):
                    signals.append((c.resource, c.kind, value, c.bounds))
                    if value not in text:
                        text.append(value)
                if len(signals) >= 120:
                    break
        digest = hashlib.sha256(json.dumps([observation.foreground, signals], ensure_ascii=False).encode()).hexdigest()
        return digest, text

    def blocked(self, reason, event_id, action_ids=()):
        for aid in action_ids:
            rows = self.db.execute("SELECT payload FROM events WHERE session=? AND kind='action_dispatched'", (self.session,)).fetchall()
            if not any(json.loads(r[0]).get("action_id") == aid for r in rows):
                raise ValueError("MEMORY_DISPATCH_SOURCE_REQUIRED")
        summary = f"后续步骤被拒绝，已有 {len(action_ids)} 次输入分发；整体未完成：" if action_ids else "候选被拒绝："
        self.insert("blocked", summary + reason,
                    {"reason": reason, "dispatched": bool(action_ids), "partial_action_ids": list(action_ids), "achieved": False},
                    event_id=event_id, confidence="observed_refusal", source_key=f"blocked:{self.session}:{event_id}")
        self.state["boredom"] = round(min(1, self.state["boredom"] + .03), 6)
        self.state["stagnant_steps"] += 1
        self.save()

    def begin_rest(self, seconds):
        self.state["rest_until"] = time.time() + seconds
        self.save()

    def end_rest(self, elapsed, completed, source_event):
        elapsed = max(0, min(120, elapsed))
        self.state["energy"] = round(min(1, self.state["energy"] + elapsed * .002), 6)
        self.state["boredom"] = round(max(0, self.state["boredom"] - elapsed * .0005), 6)
        if completed:
            self.state["rest_until"] = 0
        self.insert("rest", f"实际休息 {elapsed:.1f} 秒，{'完成' if completed else '被中断'}。",
                    {"elapsed_seconds": elapsed, "completed": completed, "dispatched": False}, event_id=source_event)
        self.save()
