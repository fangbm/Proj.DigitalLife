"""Bounded structured retrieval/consolidation with autonomous-only provenance."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid


class LifeMemory:
    def __init__(self, store, device, run_kind='autonomous'):
        if run_kind not in {'autonomous', 'mock_test'}:
            raise ValueError('Life memory is unavailable to manual/benchmark runs')
        self.store, self.device, self.kind = store, device, run_kind
        from .life_subject import scope,predicate
        self.devices=scope(store,device,run_kind)
        self.scope=predicate(self.devices)
        binding=getattr(store,'life_binding',None)
        self.interest_key=binding.namespace if binding and run_kind=='autonomous' else device
        self.environment_device=None # Optional relevance view; never rewrites history.

    def add(self, layer, subject, body, source_run, importance=.5, relation='observed'):
        if layer not in {'working', 'episodic', 'semantic', 'preference'}:
            raise ValueError('Unknown memory layer')
        row = self.store.conn.execute('SELECT run_kind,device FROM task_runs WHERE task_run_id=?', (source_run,)).fetchone()
        if not row or row[0] != self.kind or row[1] != self.device:
            raise ValueError('Memory source must match device and run_kind')
        binding=getattr(self.store,'life_binding',None)
        if binding and self.kind=='autonomous':binding.bind_run(self.store,source_run)
        encoded = self.store._json(body)
        digest = hashlib.sha256((subject + '\n' + encoded).encode()).hexdigest()
        now = time.time()
        self.store.conn.execute('INSERT INTO life_memories VALUES(?,?,?,?,?,?,?,?,?,?,0) ON CONFLICT(device,run_kind,layer,digest) DO UPDATE SET updated_at=excluded.updated_at,stale=0',
            (str(uuid.uuid4()), self.device, self.kind, layer, subject[:500], encoded, digest,
             max(0., min(1., importance)), now, now))
        memory_id = self.store.conn.execute('SELECT memory_id FROM life_memories WHERE device=? AND run_kind=? AND layer=? AND digest=?',
            (self.device, self.kind, layer, digest)).fetchone()[0]
        self.store.conn.execute('INSERT OR IGNORE INTO life_memory_sources VALUES(?,?,?)', (memory_id, source_run, relation))
        return memory_id

    def working(self, run_id, **updates):
        row = self.store.conn.execute("SELECT memory_id,body FROM life_memories WHERE device=? AND run_kind=? AND layer='working' AND subject=?",
                                     (self.device, self.kind, run_id)).fetchone()
        body = json.loads(row[1]) if row else {}
        body.update(updates)
        if row:
            self.store.conn.execute('UPDATE life_memories SET body=?,updated_at=? WHERE memory_id=?',
                                    (self.store._json(body), time.time(), row[0]))
        else:
            self.add('working', run_id, body, run_id, .2)

    @staticmethod
    def tokens(value):
        # Latin words + Chinese bigrams; no optional FTS/embedding dependency.
        words = set(re.findall(r'[a-z0-9_]{2,}', value.casefold()))
        for part in re.findall(r'[\u3400-\u9fff]+', value):
            words.update(part[i:i+2] for i in range(max(1, len(part)-1)))
        return words

    def retrieve(self, query='', limit=12, layers=('episodic', 'semantic', 'preference')):
        limit = max(1, min(20, limit))
        rows = self.store.conn.execute(f'SELECT * FROM life_memories WHERE {self.scope} AND run_kind=? AND stale=0 ORDER BY updated_at DESC LIMIT 200',
                                        (*self.devices, self.kind)).fetchall()
        wanted = self.tokens(query)
        ranked = []
        for row in rows:
            if row['layer'] not in layers:
                continue
            overlap = len(wanted & self.tokens(row['subject'] + row['body']))
            score = overlap * 2 + row['importance'] + .2 / (1 + max(0, time.time()-row['updated_at']) / 86400)
            if self.environment_device and row['device']!=self.environment_device:score*=.15
            sources = [r[0] for r in self.store.conn.execute('SELECT task_run_id FROM life_memory_sources WHERE memory_id=? LIMIT 8', (row['memory_id'],))]
            related = [r[0] for r in self.store.conn.execute('SELECT to_memory FROM life_memory_links WHERE from_memory=? LIMIT 8', (row['memory_id'],))]
            ranked.append((score, {'memory_id': row['memory_id'], 'layer': row['layer'], 'subject': row['subject'],
                                   'body': json.loads(row['body']), 'source_runs': sources, 'related_memory_ids': related,
                                   'trust': 'data_not_instructions'}))
            if self.environment_device:ranked[-1][1]['environment_relation']='current_device' if row['device']==self.environment_device else 'earlier_device'
        if 'episodic' in layers:
            # Bridge explicit old autonomous episodes without reclassifying or rewriting them.
            legacy = self.store.conn.execute(f'SELECT id,device,summary,task_run_id FROM memories WHERE {self.scope} AND run_kind=? AND (task_run_id IS NULL OR task_run_id NOT IN (SELECT task_run_id FROM life_cycles)) ORDER BY id DESC LIMIT 20',
                                             (*self.devices,self.kind)).fetchall()
            for row in legacy:
                score = len(wanted & self.tokens(row['summary'])) * 2 + .3
                if self.environment_device and row['device']!=self.environment_device:score*=.15
                ranked.append((score, {'memory_id':'ordinary-'+str(row['id']), 'layer':'episodic',
                    'subject':'Earlier explicit-source episode', 'body':{'legacy_summary':row['summary'],'verification':'not_inferred'},
                    'source_runs':[row['task_run_id']] if row['task_run_id'] else [], 'trust':'data_not_instructions'}))
        return [item for _, item in sorted(ranked, key=lambda pair: -pair[0])[:limit]]

    def consolidate(self):
        # Deduplication occurs on insert; stale marking is bounded, never deletes history.
        rows = self.store.conn.execute(f"SELECT memory_id,layer,updated_at FROM life_memories WHERE {self.scope} AND run_kind=? AND stale=0 AND layer IN ('semantic','working') ORDER BY updated_at LIMIT 50",
                                       (*self.devices, self.kind)).fetchall()
        marked = 0
        for row in rows:
            ttl = 86400 if row['layer'] == 'working' else 30 * 86400
            if row['updated_at'] < time.time() - ttl:
                self.store.conn.execute('UPDATE life_memories SET stale=1 WHERE memory_id=?', (row['memory_id'],))
                marked += 1
        return {'examined': len(rows), 'marked_stale': marked, 'deleted': 0}

    def interests(self):
        items=[dict(r) for r in self.store.conn.execute('SELECT topic,weight,evidence_count FROM life_interests WHERE device=? AND run_kind=? ORDER BY weight DESC,topic LIMIT ?', (self.interest_key, self.kind,100 if self.environment_device else 30))]
        if self.environment_device:
            for item in items:
                current=self.store.conn.execute("SELECT 1 FROM life_memories WHERE device=? AND run_kind=? AND layer='preference' AND subject=? LIMIT 1",(self.environment_device,self.kind,item['topic'])).fetchone()
                item['context_weight']=item['weight']*(1 if current else .15)
                item['environment_relation']='current_device_evidence' if current else 'historical_interest'
            items.sort(key=lambda v:-v['context_weight'])
        return items[:30]

    def update_interests(self, topics, run_id, *, verified, novelty):
        changes = []
        for topic in sorted({t.strip().casefold()[:60] for t in topics if t.strip()})[:5]:
            row = self.store.conn.execute('SELECT weight,evidence_count FROM life_interests WHERE device=? AND run_kind=? AND topic=?',
                                         (self.interest_key, self.kind, topic)).fetchone()
            if row is None and self.store.conn.execute('SELECT COUNT(*) FROM life_interests WHERE device=? AND run_kind=?', (self.interest_key,self.kind)).fetchone()[0] >= 100:
                self.store.event(self.device,'interest_capacity',{'task_run_id':run_id,'new_topic_skipped':topic,'limit':100})
                continue
            before, count = (row[0], row[1]) if row else (.5, 0)
            # Topics are model evaluations, not verified facts or safety instructions.
            delta = .05 if verified and novelty else .02 if novelty else -.02
            after = round(max(.05, min(.95, before + delta)), 6)
            self.store.conn.execute('INSERT INTO life_interests VALUES(?,?,?,?,?,?) ON CONFLICT(device,run_kind,topic) DO UPDATE SET weight=excluded.weight,evidence_count=excluded.evidence_count,updated_at=excluded.updated_at',
                                    (self.interest_key, self.kind, topic, after, count+1, time.time()))
            change = {'topic': topic, 'before': before, 'after': after, 'verified_outcome': verified,
                      'new_content_observed': novelty, 'source_type': 'model_interest_evaluation'}
            self.add('preference', topic, change, run_id, .6, 'subjective_preference')
            changes.append(change)
        return changes
