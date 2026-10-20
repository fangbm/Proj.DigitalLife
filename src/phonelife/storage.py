"""SQLite journal, durable wake-up schedule, episodic memories and process lease."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from .audit import scrub
from .models import utc_now


class Store:
    RUN_KINDS = {"autonomous", "manual_targeted", "benchmark", "mock_test"}

    def __init__(self, path: Path):
        self.readonly = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device TEXT NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schedule (
                device TEXT PRIMARY KEY,
                next_wake_at REAL NOT NULL DEFAULT 0,
                last_result TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS lease (
                device TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
        """)
        self._migrate()

    @classmethod
    def open_readonly(cls, path: Path):
        """No mkdir, journal change, schema migration, recovery, or application writes."""
        if not path.is_file():
            raise FileNotFoundError(f"Replay database does not exist: {path}")
        store = cls.__new__(cls)
        store.readonly = True
        store.conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=10, isolation_level=None)
        store.conn.row_factory = sqlite3.Row
        store.conn.execute("PRAGMA query_only=ON")
        return store

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > 12:
            raise ValueError("Database schema is newer than this program")
        if version == 12:
            return
        self.conn.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, applied_wall_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_runs (
                task_run_id TEXT PRIMARY KEY, device TEXT NOT NULL, goal TEXT NOT NULL,
                status TEXT NOT NULL, schema_version TEXT NOT NULL,
                started_wall_utc TEXT NOT NULL, started_monotonic_ns INTEGER NOT NULL,
                ended_wall_utc TEXT, elapsed_ns INTEGER, metadata TEXT NOT NULL,
                result TEXT
            );
            CREATE TABLE IF NOT EXISTS observations (
                observation_id TEXT PRIMARY KEY, task_run_id TEXT NOT NULL,
                metadata TEXT NOT NULL, evidence_path TEXT,
                FOREIGN KEY(task_run_id) REFERENCES task_runs(task_run_id)
            );
            CREATE TABLE IF NOT EXISTS action_attempts (
                attempt_id TEXT PRIMARY KEY, task_run_id TEXT NOT NULL,
                step_index INTEGER NOT NULL, generation_attempt INTEGER NOT NULL,
                payload TEXT NOT NULL,
                FOREIGN KEY(task_run_id) REFERENCES task_runs(task_run_id),
                UNIQUE(task_run_id,step_index,generation_attempt)
            );
            CREATE INDEX IF NOT EXISTS task_runs_device ON task_runs(device,started_wall_utc);
            CREATE INDEX IF NOT EXISTS attempts_task ON action_attempts(task_run_id,step_index,generation_attempt);
            PRAGMA user_version=11;
            COMMIT;
        """)
        self.conn.execute("INSERT OR IGNORE INTO schema_migrations VALUES(11,?)", (utc_now(),))
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            for table, column, declaration in (
                ("task_runs", "run_kind", "TEXT"),
                ("memories", "run_kind", "TEXT"),
                ("memories", "task_run_id", "TEXT"),
            ):
                if column not in {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            self.conn.execute("CREATE TABLE IF NOT EXISTS event_sources (event_id INTEGER PRIMARY KEY, run_kind TEXT NOT NULL, task_run_id TEXT)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS run_schedules (device TEXT NOT NULL, run_kind TEXT NOT NULL, next_wake_at REAL NOT NULL, last_result TEXT NOT NULL, task_run_id TEXT, PRIMARY KEY(device,run_kind))")
            self.conn.execute("CREATE INDEX IF NOT EXISTS memories_source ON memories(device,run_kind,id)")
            self.conn.execute("INSERT OR IGNORE INTO schema_migrations VALUES(12,?)", (utc_now(),))
            self.conn.execute("PRAGMA user_version=12")
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.conn.close()

    def event(self, device: str, event_type: str, payload: dict) -> None:
        run_id = payload.get("task_run_id")
        row = self.conn.execute("SELECT run_kind FROM task_runs WHERE task_run_id=?", (run_id,)).fetchone() if run_id else None
        run_kind = (row[0] or "legacy_unclassified") if row else payload.get("run_kind", "autonomous")
        payload = {**payload, "run_kind": run_kind}
        cursor = self.conn.execute(
            "INSERT INTO events(device,event_type,payload,created_at) VALUES(?,?,?,?)",
            (device, event_type, self._json(payload), time.time()),
        )
        self.conn.execute("INSERT INTO event_sources VALUES(?,?,?)", (cursor.lastrowid, run_kind, run_id))

    def remember(self, device: str, kind: str, summary: str, *, run_kind="autonomous", task_run_id=None) -> None:
        self.conn.execute(
            "INSERT INTO memories(device,kind,summary,created_at,run_kind,task_run_id) VALUES(?,?,?,?,?,?)",
            (device, kind, scrub(summary)[:1500], time.time(), run_kind, task_run_id),
        )

    def recent_memories(self, device: str, limit: int = 10, *, run_kind="autonomous") -> list[str]:
        # NULL legacy rows remain queryable, but are not guessed to be autonomous life.
        return [row["summary"] for row in self.conn.execute(
            "SELECT summary FROM memories WHERE device=? AND run_kind IS ? ORDER BY id DESC LIMIT ?",
            (device, run_kind, limit),
        )][::-1]

    def count_events(self, device: str, *, run_kind="autonomous") -> int:
        row = self.conn.execute("SELECT COUNT(*) n FROM events e JOIN event_sources s ON s.event_id=e.id WHERE device=? AND s.run_kind=?", (device, run_kind)).fetchone()
        return int(row["n"])

    def next_wake(self, device: str, *, run_kind="autonomous") -> float:
        row = self.conn.execute("SELECT next_wake_at FROM schedule WHERE device=?", (device,)).fetchone() if run_kind == "autonomous" else self.conn.execute("SELECT next_wake_at FROM run_schedules WHERE device=? AND run_kind=?", (device, run_kind)).fetchone()
        return float(row["next_wake_at"]) if row else 0.0

    def set_wake(self, device: str, when: float, result: str, *, run_kind="autonomous", task_run_id=None) -> None:
        if run_kind != "autonomous":
            self.conn.execute("INSERT INTO run_schedules VALUES(?,?,?,?,?) ON CONFLICT(device,run_kind) DO UPDATE SET next_wake_at=excluded.next_wake_at,last_result=excluded.last_result,task_run_id=excluded.task_run_id",
                (device, run_kind, when, result[:500], task_run_id))
            return
        self.conn.execute("""
            INSERT INTO schedule(device,next_wake_at,last_result) VALUES(?,?,?)
            ON CONFLICT(device) DO UPDATE SET
              next_wake_at=excluded.next_wake_at, last_result=excluded.last_result
        """, (device, when, result[:500]))

    def acquire(self, device: str, owner: str, duration_seconds: int = 600) -> bool:
        now = time.time()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT owner,expires_at FROM lease WHERE device=?", (device,)).fetchone()
            if row and row["expires_at"] > now and row["owner"] != owner:
                self.conn.execute("ROLLBACK")
                return False
            self.conn.execute("""
                INSERT INTO lease(device,owner,expires_at) VALUES(?,?,?)
                ON CONFLICT(device) DO UPDATE SET owner=excluded.owner,
                expires_at=excluded.expires_at
            """, (device, owner, now + duration_seconds))
            self.conn.execute("COMMIT")
            return True
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def renew(self, device: str, owner: str, duration_seconds: int = 600) -> None:
        now = time.time()
        cursor = self.conn.execute("UPDATE lease SET expires_at=? WHERE device=? AND owner=? AND expires_at>?",
                                   (now + duration_seconds, device, owner, now))
        if cursor.rowcount != 1:
            raise RuntimeError("Device lease was lost")

    def release(self, device: str, owner: str) -> None:
        self.conn.execute("DELETE FROM lease WHERE device=? AND owner=?", (device, owner))

    @staticmethod
    def _json(value) -> str:
        return json.dumps(scrub(value), ensure_ascii=False, allow_nan=False)

    def start_task(self, run_id: str, device: str, goal: str, metadata: dict, *, run_kind="autonomous") -> None:
        if run_kind not in self.RUN_KINDS:
            raise ValueError("Unknown run_kind")
        metadata = {**metadata, "run_kind": run_kind}
        self.conn.execute("INSERT INTO task_runs(task_run_id,device,goal,status,schema_version,started_wall_utc,started_monotonic_ns,ended_wall_utc,elapsed_ns,metadata,result,run_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, device, scrub(goal), "running", "1.1.1", utc_now(), time.monotonic_ns(),
             None, None, self._json(metadata), None, run_kind))
        self.event(device, "task_started", {"task_run_id": run_id, "goal": goal, **metadata})

    def update_goal(self, run_id: str, goal: str) -> None:
        self.conn.execute("UPDATE task_runs SET goal=? WHERE task_run_id=?", (scrub(goal), run_id))

    def observation(self, run_id: str, observation, evidence_path: str | None = None) -> None:
        self.conn.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?)",
            (observation.observation_id, run_id, self._json(observation.metadata()), evidence_path))

    def attempt(self, run_id: str, step: int, generation: int, payload: dict) -> str:
        attempt_id = str(uuid.uuid4())
        self.conn.execute("INSERT INTO action_attempts VALUES(?,?,?,?,?)",
            (attempt_id, run_id, step, generation, self._json(payload)))
        return attempt_id

    def update_attempt(self, attempt_id: str, **updates) -> None:
        row = self.conn.execute("SELECT payload FROM action_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        payload = json.loads(row[0]); payload.update(updates)
        self.conn.execute("UPDATE action_attempts SET payload=? WHERE attempt_id=?", (self._json(payload), attempt_id))

    def finish_task(self, run_id: str, result: dict, next_wake_at: float, memory: str, *, finalizer=None) -> None:
        row = self.conn.execute("SELECT * FROM task_runs WHERE task_run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown TaskRun")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("UPDATE task_runs SET status=?,ended_wall_utc=?,elapsed_ns=?,result=? WHERE task_run_id=?",
                (result["status"], utc_now(), time.monotonic_ns() - row["started_monotonic_ns"], self._json(result), run_id))
            self.event(row["device"], "outcome", {"task_run_id": run_id, **result})
            run_kind = row["run_kind"]
            if run_kind in {"autonomous", "mock_test"}:
                self.remember(row["device"], "episode", memory, run_kind=run_kind, task_run_id=run_id)
            self.set_wake(row["device"], next_wake_at, result["status"], run_kind=run_kind, task_run_id=run_id)
            if finalizer:
                finalizer()  # Life state/memory/goal transition commits with the authoritative TaskRun.
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    @staticmethod
    def interpret_legacy_status(status: str) -> str:
        return "legacy_unverified" if status == "completed" else status

    def task(self, run_id: str) -> dict:
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_runs'").fetchone():
            raise ValueError("This legacy database has no TaskRuns to replay; read-only mode will not migrate it")
        row = self.conn.execute("SELECT * FROM task_runs WHERE task_run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown TaskRun")
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        result["attempts"] = [{**dict(a), "payload": json.loads(a["payload"])} for a in self.conn.execute(
            "SELECT * FROM action_attempts WHERE task_run_id=? ORDER BY step_index,generation_attempt", (run_id,))]
        result["observations"] = [{**dict(o), "metadata": json.loads(o["metadata"])} for o in self.conn.execute(
            "SELECT * FROM observations WHERE task_run_id=?", (run_id,))]
        return result

