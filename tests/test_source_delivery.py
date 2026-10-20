"""Portable public checks; private device behavior is deliberately not asserted."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from acceptance.verify_public import verify_archive
from acceptance.source_archive import build

class SourceDeliveryTests(unittest.TestCase):
    def test_archive_dependency_is_delivered_outside_historical_data(self):
        import acceptance.evidence_metrics as metrics
        import acceptance.verify_continuation as verifier
        self.assertTrue(callable(metrics.inspect))
        text=Path(verifier.__file__).read_text(encoding='utf8')
        self.assertNotIn('from analyze import',text);self.assertNotIn("sys.path.insert(0,str(ROOT/'data/phase122'))",text)
    def test_verifier_selfcheck_does_not_require_private_sqlite(self):
        r=subprocess.run([sys.executable,'-m','acceptance.verify_continuation','--self-check'],capture_output=True,timeout=20)
        self.assertEqual(r.returncode,0);self.assertFalse(json.loads(r.stdout)['live_behavior_reproved'])
    def test_public_integrity_explicitly_does_not_reprove_live_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'public.zip';data=b'{"passed":true}'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('summary.json',data);z.writestr('CONTENTS-SHA256.json',json.dumps({'summary.json':hashlib.sha256(data).hexdigest()}))
            result=verify_archive(p)
            self.assertTrue(result['integrity_passed']);self.assertFalse(result['live_device_behavior_reproved'])
    def test_unmanifested_file_and_path_traversal_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('extra.txt','../escape'):
                p=Path(tmp)/(name.replace('/','_')+'.zip')
                with zipfile.ZipFile(p,'w') as z:z.writestr(name,b'x');z.writestr('CONTENTS-SHA256.json','{}')
                with self.assertRaises(ValueError):verify_archive(p)
    def test_private_verifier_missing_evidence_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=subprocess.run([sys.executable,'-m','acceptance.verify_continuation',tmp],capture_output=True,timeout=20)
            self.assertNotEqual(r.returncode,0);self.assertIn(b'Private SQLite evidence absent',r.stderr)
    def test_two_source_builds_have_identical_bytes_and_include_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a.zip';b=Path(tmp)/'b.zip';build(a);build(b)
            self.assertEqual(hashlib.sha256(a.read_bytes()).digest(),hashlib.sha256(b.read_bytes()).digest())
            with zipfile.ZipFile(a) as z:self.assertIn('acceptance/evidence_metrics.py',z.namelist())

if __name__=='__main__':unittest.main()
