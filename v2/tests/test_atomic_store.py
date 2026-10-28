import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from digitallife_v2.store import write_json


class AtomicStoreTests(unittest.TestCase):
    def test_transient_windows_reader_denying_replace_does_not_lose_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'status.json';write_json(path,{'state':'old'})
            replace=os.replace;attempts=[]
            def briefly_locked(source,target):
                attempts.append(source)
                if len(attempts)==1:raise PermissionError('Reader denies delete sharing')
                return replace(source,target)
            with patch('os.replace',side_effect=briefly_locked),patch('digitallife_v2.store.time.sleep'):
                write_json(path,{'state':'new'})
            self.assertEqual(json.loads(path.read_text()),{'state':'new'});self.assertEqual(len(attempts),2)
            self.assertEqual(list(Path(tmp).glob('*.tmp')),[])

    def test_persistent_permissions_failure_keeps_old_file_and_stops_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'status.json';write_json(path,{'state':'old'})
            with patch('os.replace',side_effect=PermissionError('Persistent refusal')) as replace,patch('digitallife_v2.store.time.sleep'):
                with self.assertRaises(PermissionError):write_json(path,{'state':'new'})
            self.assertEqual(replace.call_count,11);self.assertEqual(json.loads(path.read_text()),{'state':'old'})
