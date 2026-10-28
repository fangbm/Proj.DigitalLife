"""Small durable journal. State reads never open ADB/API or create a database."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from .safety import Blocked
from .memory import initialize
from .events import initialize as initialize_events
from .reading import initialize as initialize_reading
from .social import initialize as initialize_social


def open_database(data):
    data = Path(data)
    data.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(data / "life.sqlite3")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in {0, 2, 3, 4, 5}:
        db.close()
        raise ValueError("UNSUPPORTED_V2_DATABASE_VERSION")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    if version in {2, 3, 4}:
        backups = data / "backups"
        backups.mkdir(exist_ok=True)
        prefix = {2:'pre-m3', 3:'pre-reading', 4:'pre-social'}[version]
        backup = sqlite3.connect(backups / f"{prefix}-{time.time_ns()}.sqlite3")
        try:
            db.backup(backup)
        finally:
            backup.close()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,serial TEXT,started REAL,ended REAL,status TEXT,reason TEXT);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,session TEXT,at REAL,kind TEXT,payload TEXT);
    CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY,session TEXT,at REAL,kind TEXT,input_tokens INTEGER,output_tokens INTEGER,cost REAL,reserved REAL);
    """)
    if "run_kind" not in {r[1] for r in db.execute("PRAGMA table_info(sessions)")}:
        if db.execute("SELECT count(*) FROM sessions").fetchone()[0]:
            backups = data / "backups"
            backups.mkdir(exist_ok=True)
            backup = sqlite3.connect(backups / f"pre-m2-{time.time_ns()}.sqlite3")
            try:
                db.backup(backup)
            finally:
                backup.close()
        db.execute("ALTER TABLE sessions ADD COLUMN run_kind TEXT NOT NULL DEFAULT 'legacy_unclassified'")
    initialize(db)
    initialize_events(db)
    initialize_reading(db)
    initialize_social(db)
    db.execute("PRAGMA user_version=5")
    db.commit()
    return db


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    # Windows readers may briefly deny rename/delete sharing on status.json.
    # Preserve atomic replacement and the old file, with a bounded retry only
    # for this transient filesystem condition. Never replay a device action.
    for attempt in range(11):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt==10:raise
            time.sleep(.025)


class LifeStore:
    def __init__(self, data, session, serial, run_kind="unclassified"):
        self.session = session
        self.serial = serial
        self.directory = Path(session)
        self.data = Path(data)
        self.db = open_database(data)
        self.memory = None
        self.inbound_context = None
        self.state = {"session": session, "serial": serial, "status": "starting", "started_at": time.time(),
                      "foreground": None, "last_action": None, "dispatched": 0, "blocked": 0,
                      "requests": 0, "input_tokens": 0, "output_tokens": 0, "cost": None,
                      "last_error": None, "next_wake": None, "run_kind": run_kind}
        self.db.execute("INSERT INTO sessions(id,serial,started,status,run_kind) VALUES(?,?,?,'starting',?)", (session, serial, time.time(), run_kind))
        self.db.commit()
        self.update()

    def update(self, **fields):
        self.state.update(fields)
        self.state["updated_at"] = time.time()
        self.state["elapsed_seconds"] = round(time.time() - self.state["started_at"], 1)
        write_json(self.directory / "status.json", self.state)

    def event(self, kind, payload):
        now = time.time()
        cursor = self.db.execute("INSERT INTO events(session,at,kind,payload) VALUES(?,?,?,?)",
                        (self.session, now, kind, json.dumps(payload, ensure_ascii=False)))
        self.db.commit()
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({**payload,"at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                                "source": "v2", "kind": kind}, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return cursor.lastrowid

    def reserve_request(self, config):
        """Reserve before sending; failed/cancelled requests retain pessimistic cost."""
        now = time.time()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for seconds, maximum in ((3600, config.requests_hour), (86400, config.requests_day)):
                count = self.db.execute("SELECT count(*) FROM usage WHERE kind='request' AND at>?", (now - seconds,)).fetchone()[0]
                if count >= maximum:
                    raise Blocked("REQUEST_BUDGET_EXHAUSTED")
            reservation = None
            if config.input_price is not None and config.output_price is not None:
                reservation = (config.max_input_tokens * config.input_price + config.max_output_tokens * config.output_price) / 1e6
            spent = self.db.execute("SELECT coalesce(sum(coalesce(cost,reserved)),0) FROM usage WHERE session=?", (self.session,)).fetchone()[0]
            if config.max_cost is not None and spent + reservation > config.max_cost:
                raise Blocked("MONEY_BUDGET_EXHAUSTED")
            cursor = self.db.execute("INSERT INTO usage(session,at,kind,reserved) VALUES(?,?,'request',?)", (self.session, now, reservation))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        self.update(requests=self.state["requests"] + 1)
        return cursor.lastrowid

    def complete_request(self, request_id, usage, config):
        i, o = usage.get("prompt_tokens"), usage.get("completion_tokens")
        valid = type(i) is int and type(o) is int and i >= 0 and o >= 0
        cost = None
        if valid and config.input_price is not None and config.output_price is not None:
            cost = (i * config.input_price + o * config.output_price) / 1e6
        self.db.execute("UPDATE usage SET input_tokens=?,output_tokens=?,cost=? WHERE id=?",
                        (i if valid else None, o if valid else None, cost, request_id))
        self.db.commit()
        self.update(input_tokens=self.state["input_tokens"] + (i if valid else 0),
                    output_tokens=self.state["output_tokens"] + (o if valid else 0),
                    cost=(self.state["cost"] or 0) + cost if cost is not None else None)
        if valid and (i > config.max_input_tokens or o > config.max_output_tokens):
            raise Blocked("PROVIDER_TOKEN_LIMIT_EXCEEDED")

    def finish(self, status, reason):
        self.db.execute("UPDATE sessions SET ended=?,status=?,reason=? WHERE id=?", (time.time(), status, reason, self.session))
        self.db.commit()
        self.update(status=status, reason=reason, next_wake=None)

    def close(self):
        self.db.close()


def status(session):
    p = Path(session).resolve()
    state = json.loads((p / "status.json").read_text(encoding="utf-8"))
    state["stop_requested"] = (p / "STOP").exists()
    state["stale"] = state["status"] in {"starting", "running"} and time.time() - state["updated_at"] > 20
    return state
