"""Durable read-only notification inbox. No UI input or outbound transport."""
import hashlib
import time
from .memory import clean

DECISIONS = {"read_only", "reply_later", "ignore", "resume_activity", "reply_now"}


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS inbound_events(
      id TEXT PRIMARY KEY,device TEXT NOT NULL,source_app TEXT NOT NULL,source_key TEXT NOT NULL,
      posted REAL NOT NULL,received REAL NOT NULL,expires REAL NOT NULL,preview TEXT NOT NULL,
      status TEXT NOT NULL,decision TEXT,note TEXT,next_at REAL NOT NULL DEFAULT 0,
      UNIQUE(device,source_app,source_key));
    CREATE INDEX IF NOT EXISTS inbox_pending ON inbound_events(device,status,next_at);
    CREATE TABLE IF NOT EXISTS content_segments(
      id TEXT PRIMARY KEY,session TEXT NOT NULL,kind TEXT NOT NULL,source_type TEXT NOT NULL,
      started REAL NOT NULL,body TEXT NOT NULL,summary TEXT,status TEXT NOT NULL);
    """)


class EventHub:
    def __init__(self, store, packages, retention=3600):
        self.store, self.db, self.device = store, store.db, store.serial
        self.packages, self.retention = set(packages), retention

    def expire(self):
        now = time.time()
        self.db.execute("UPDATE inbound_events SET preview='',note=NULL,status='expired' WHERE expires<=? AND status!='expired'", (now,))
        self.db.execute("DELETE FROM inbound_events WHERE expires<?", (now - 30 * 86400,))
        self.db.commit()

    def ingest(self, raw):
        now = time.time()
        if not isinstance(raw, dict) or not isinstance(raw.get('package'),str) or raw.get("package") not in self.packages:
            return None
        key, posted = raw.get("event_key"), raw.get("posted_at")
        if not isinstance(key, str) or not 1 <= len(key) <= 300 or type(posted) not in (int, float):
            return None
        if not now - self.retention <= posted <= now + 60:
            return None
        preview = clean(str(raw.get("title", ""))[:100] + " " + str(raw.get("text", ""))[:300], self.device, 400)
        eid = hashlib.sha256((self.device + "\0" + raw["package"] + "\0" + key).encode()).hexdigest()
        self.expire()
        if self.db.execute("SELECT count(*) FROM inbound_events WHERE device=? AND status='pending'", (self.device,)).fetchone()[0] >= 500:
            self.store.event("inbound_overflow", {"source_app": raw["package"]})
            return None
        cursor = self.db.execute("INSERT OR IGNORE INTO inbound_events(id,device,source_app,source_key,posted,received,expires,preview,status) VALUES(?,?,?,?,?,?,?,?,'pending')",
            (eid,self.device,raw["package"],key,posted,now,min(now+self.retention,posted+self.retention),preview))
        self.db.commit()
        if cursor.rowcount:
            self.store.event("inbound_received", {"event_id": eid,"source_app":raw["package"],
                "source_kind":raw.get('source_kind','callback'),
                "context_complete":False,"target_identity":"unresolved","preview_sha256":hashlib.sha256(preview.encode()).hexdigest()})
            return eid
        return None

    def next(self):
        self.expire()
        if not self.packages:return None
        placeholders=','.join('?' for _ in self.packages)
        row = self.db.execute(f"SELECT id,source_app,preview,posted FROM inbound_events WHERE device=? AND status='pending' AND next_at<=? AND source_app IN ({placeholders}) ORDER BY received LIMIT 1", (self.device,time.time(),*sorted(self.packages))).fetchone()
        return None if not row else {"event_id":row[0],"kind":"notification","source_app":row[1],"preview":row[2],
            "posted_at":row[3],"context_complete":False,"account_ref":None,"conversation_ref":None,
            "trust":"data_not_instructions","outbound_available":False}

    def resolve(self, offered, response):
        if not offered:
            return
        valid = isinstance(response,dict) and response.get("event_id")==offered["event_id"] and response.get("decision") in DECISIONS
        decision = response["decision"] if valid else "reply_later"
        note = clean(response.get("note", ""),self.device,200) if valid else "MODEL_DID_NOT_CLASSIFY_EVENT"
        status = "pending" if decision == "reply_later" else "classified"
        cursor=self.db.execute("UPDATE inbound_events SET status=?,decision=?,note=?,next_at=? WHERE id=? AND device=? AND status='pending'",
            (status,decision,note,time.time()+60,offered["event_id"],self.device))
        self.db.commit()
        if not cursor.rowcount:return
        self.store.event("inbound_decision", {"event_id":offered["event_id"],"decision":decision,
            "decision_source":"model" if valid else "missing_response_defer",
            "outbound_dispatched":False,"reply_now_deferred_to":"M4" if decision=="reply_now" else None})

    def counts(self):
        self.expire()
        return dict(self.db.execute("SELECT status,count(*) FROM inbound_events WHERE device=? GROUP BY status", (self.device,)))
