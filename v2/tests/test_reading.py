import asyncio
from dataclasses import replace
import hashlib
import html
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from digitallife_v2.perception import parse_xml
from digitallife_v2.reading import ArticleReader, ReadingStore, extract
from digitallife_v2.safety import Blocked
from digitallife_v2.store import LifeStore
from test_m1 import config, observation, decision


class ReadingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.config = config(self.root)
        directory = self.root / 'session'; directory.mkdir()
        self.store = LifeStore(self.config.data, str(directory), self.config.serial, 'fixture')
        self.addCleanup(self.store.close)
        self.reading = ReadingStore(self.store, 'fixture')

    def page(self, oid, texts, title='测试长文章'):
        nodes = '<node package="com.reader" resource-id="article_title" text="'+html.escape(title, quote=True)+'" bounds="[0,100][700,180]"/>'
        for text in texts:
            nodes += '<node package="com.reader" class="android.widget.TextView" text="'+html.escape(text, quote=True)+'" bounds="[0,200][700,1400]"/>'
        xml = '<?xml version="1.0"?><hierarchy>'+nodes+'</hierarchy>'
        path = self.root / (oid+'.xml'); path.write_text(xml, encoding='utf-8')
        controls = parse_xml(xml, oid, full_access=True)[1]
        return replace(observation(oid), controls=controls, xml_path=str(path), xml_sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def test_reads_full_xml_after_navigation_excerpt_has_been_truncated(self):
        text = '真正的正文内容。'*140+'末尾关键结论'
        obs = self.page('one', [text])
        self.assertEqual(len(obs.controls[-1].text), 300)
        page = extract(obs)
        restored = ''.join(p['text'] for p in page['paragraphs'])
        self.assertEqual(restored, text)
        self.assertIn('末尾关键结论', restored)

    def test_page_overlap_and_new_process_keep_reading_progress(self):
        a, b, c = '第一段内容。'*20, '中间共有段落。'*20, '最后新内容。'*20
        first = extract(self.page('one', [a, b]))
        doc, _, added = self.reading.ingest(first); self.assertEqual(added, 2)
        self.store.db.execute('UPDATE reading_paragraphs SET summarized=1'); self.store.db.commit()
        second = ReadingStore(self.store, 'fixture')
        same, _, added = second.ingest(extract(self.page('two', [b, c])))
        self.assertEqual(doc, same); self.assertEqual(added, 1)
        self.assertEqual(second.progress(doc)['known_paragraphs'], 3)
        self.assertEqual(second.progress(doc)['pending_paragraphs'], 1)
        self.assertEqual(second.pending(doc)[0]['text'], c)

    def test_different_titles_and_source_types_do_not_share_processed_state(self):
        page = extract(self.page('one', ['相同内容。'*25]))
        first = self.reading.ingest(page)[0]
        other = self.reading.ingest(extract(self.page('two', ['相同内容。'*25], title='另一篇文章')))[0]
        autonomous = ReadingStore(self.store, 'v2_autonomous').ingest(page)[0]
        self.assertNotEqual(first, other); self.assertNotEqual(first, autonomous)

    def test_modified_xml_and_hidden_or_foreign_content_are_not_read(self):
        obs = self.page('one', ['真正正文。'*20])
        hidden = replace(obs, controls=[replace(c, covered=True) for c in obs.controls])
        self.assertIsNone(extract(hidden))
        foreign = replace(obs, foreground='com.other/.Main')
        self.assertIsNone(extract(foreign))
        Path(obs.xml_path).write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(Blocked, 'HASH_MISMATCH'): extract(obs)

    async def test_direct_text_chunks_use_no_images_and_repeat_read_uses_no_api(self):
        text = '文章正文及其完整论证。'*160+'末尾需要保留的结论'
        obs = self.page('one', [text]); calls = []
        async def guarded(awaitable): return await awaitable
        async def structured(system, data, images, validate):
            self.assertEqual(images, []); calls.append(data)
            value = {'summary':'本批正文摘要', 'topics':['阅读'], 'evidence_ids':[p['paragraph_id'] for p in data['paragraphs']], 'uncertainty':'仅处理提供的正文'}
            validate(value); return value
        loop = SimpleNamespace(config=self.config, store=self.store, run_kind='fixture', memory=None,
                               brain=SimpleNamespace(structured=structured), guarded=guarded, check_stop=lambda:None)
        proposal = decision({'type':'read_article','seconds':10})
        first = await ArticleReader(loop).run(proposal, obs)
        sent = ''.join(p['text'] for call in calls for p in call['paragraphs'])
        self.assertEqual(sent, text)
        self.assertEqual(first['reading']['pending_paragraphs'], 0)
        count = len(calls)
        second = await ArticleReader(loop).run(proposal, obs)
        self.assertEqual(second['outcome'], 'already_read'); self.assertEqual(len(calls), count)
        self.assertFalse(self.store.db.execute('SELECT 1 FROM memories').fetchone())

    async def test_interrupted_chunk_keeps_progress_and_only_retries_unprocessed_text(self):
        obs = self.page('one', ['甲'*1000, '乙'*1000, '丙'*1000])
        async def guarded(awaitable): return await awaitable
        calls = []
        async def structured(system, data, images, validate):
            calls.append(data['paragraphs'])
            if len(calls)==2: raise Blocked('TEST_INTERRUPTION')
            value={'summary':'已提供段落的摘要','topics':[],'evidence_ids':[data['paragraphs'][0]['paragraph_id']],'uncertainty':'未处理后续段落'}
            validate(value);return value
        loop=SimpleNamespace(config=self.config,store=self.store,run_kind='fixture',memory=None,
            brain=SimpleNamespace(structured=structured),guarded=guarded,check_stop=lambda:None)
        with self.assertRaises(Blocked):await ArticleReader(loop).run(decision({'type':'read_article','seconds':10}),obs)
        body=json.loads(self.store.db.execute('SELECT body FROM content_segments').fetchone()[0])
        self.assertGreater(body['progress']['pending_paragraphs'],0)
        done={p['paragraph_id'] for p in calls[0]}
        result=await ArticleReader(loop).run(decision({'type':'read_article','seconds':10}),obs)
        self.assertEqual(result['reading']['pending_paragraphs'],0)
        self.assertFalse(done & {p['paragraph_id'] for group in calls[2:] for p in group})


if __name__ == '__main__': unittest.main()
