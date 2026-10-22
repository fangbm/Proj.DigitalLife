"""Synthetic, temporary-only tests for one explicitly supervised diagnosis."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch,AsyncMock
from phonelife.diagnostic_evidence import PrivateEvidence,create_private_directory,DiagnosticDenied,sha
from phonelife.diagnostic_capture import DiagnosticSafetySources,DiagnosticLease
from phonelife.device_profiles import PhysicalDeviceProfile,IDENTITY_PROPERTIES
from phonelife.manual_diagnostic import (prepare_request,request_sha,capture_manually_approved,
    ManualDiagnosticTransport,extract_xml,PNG,XML,METADATA)
from phonelife.storage import Store
from test_diagnostic_capture import scene,png,xml


class Pipe:
    def __init__(self,data=b''):self.data=data
    async def read(self,n):
        if self.data is None:await asyncio.sleep(100)
        part=self.data[:n];self.data=self.data[n:];return part
    async def readexactly(self,n):
        raw=await self.read(n)
        if len(raw)!=n:raise asyncio.IncompleteReadError(raw,n)
        return raw


class Writer:
    def write(self,value):pass
    async def drain(self):pass
    def close(self):pass
    async def wait_closed(self):pass


class Proc:
    pid=123456
    def __init__(self,code=1,output=b'private output'):
        self.returncode=code;self.stdout=Pipe(output);self.stderr=Pipe();self.killed=0
    async def wait(self):
        while self.returncode is None:await asyncio.sleep(.001)
        return self.returncode
    def kill(self):self.killed+=1;self.returncode=-9


class ManualDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.out=self.root/'private';create_private_directory(self.out);self.evidence=PrivateEvidence(self.out)
        self.adb=self.root/'adb.exe';self.adb.write_bytes(b'synthetic executable identity; never run')
        identity=dict.fromkeys(IDENTITY_PROPERTIES,'synthetic')
        identity.update({'ro.product.manufacturer':'OPPO','ro.product.model':'PGFM10','ro.build.version.sdk':'36','ro.build.version.release':'16','ro.build.fingerprint':'offline_fixture','ro.kernel.qemu':'0'})
        self.profile=PhysicalDeviceProfile('fixture-usb',identity)
        self.db=self.root/'coordinator.sqlite3';s=Store(self.db);s.close();self.sources=DiagnosticSafetySources(self.db)
        self.request=prepare_request(self.profile,self.adb,self.evidence,self.sources)
        self.metadata=scene();self.metadata['windows']+='  mCurrentFocus=Window{a0 u0 com.android.settings/.Settings}\n'
        self.calls=[];self.replies={PNG:png(),XML:xml()+b'\nUI hierchary dumped to: /proc/self/fd/1\n'}

    async def reply(self,transport,args):
        transport.guard(args);self.calls.append(args)
        if args in self.replies:return self.replies[args]
        if args==('devices','-l'):return b'List of devices attached\nfixture-usb device\n'
        if args==('get-state',):return b'device\n'
        if args[:2]==('shell','getprop'):return self.profile.identity[args[2]].encode()
        return self.metadata[next(k for k,v in METADATA.items() if v==args)].encode()

    def run_capture(self,ref='offline-human-approval',approved=None):
        async def run():
            async def reply(transport,args):return await self.reply(transport,args)
            with patch.object(ManualDiagnosticTransport,'run',new=reply):
                return await capture_manually_approved(self.request,approved_sha256=approved or request_sha(self.request),
                    operator_reference=ref,profile=self.profile,evidence=self.evidence,sources=self.sources)
        return asyncio.run(run())

    def mark_approved(self):
        (self.out/'manual-session-started.json').write_text(json.dumps({'request':self.request,'approval_reference':'offline-fixture-only','started_monotonic_ns':time.monotonic_ns()}),'utf8')

    def test_success_native_mock_pair_and_private_audit(self):
        result=self.run_capture();self.assertEqual(result['result'],'REAL_SCREEN_CAPTURE_VERIFIED')
        self.assertEqual(self.calls.count(PNG),1);self.assertEqual(self.calls.count(XML),1)
        audit=json.loads((self.out/'manual-result-private.json').read_bytes())
        self.assertTrue(all(f['scope_verified'] for f in audit['files']));self.assertFalse(result['GUI_EXECUTION_SEMANTICS_TRUSTED'])
        self.assertNotIn(self.profile.serial,json.dumps(result));self.assertFalse(any(c[:2]==('shell','input') for c in self.calls))
    def test_no_approval_no_process_or_output(self):
        with self.assertRaises(DiagnosticDenied):asyncio.run(capture_manually_approved(self.request))
        self.assertEqual(list(self.out.iterdir()),[])
    def test_digest_mismatch_no_execution(self):
        with self.assertRaises(DiagnosticDenied):self.run_capture(approved='0'*64)
        self.assertEqual(self.calls,[])
    def test_expired_request(self):
        self.request['created_at']=time.time()-601
        with self.assertRaises(DiagnosticDenied):self.run_capture()
        self.assertEqual(self.calls,[])
    def test_request_changed_quota_rejected(self):
        self.request['png']=2
        with self.assertRaises(DiagnosticDenied):self.run_capture()
    def test_restart_session_cannot_replay(self):
        self.run_capture()
        with self.assertRaises(FileExistsError):self.run_capture()
        self.assertEqual(self.calls.count(PNG),1)
    def test_failed_xml_keeps_private_png_and_no_repeat(self):
        self.replies[XML]=b'unsupported output; private content'
        result=self.run_capture();self.assertEqual(result['result'],'FAILED_OR_UNKNOWN')
        self.assertEqual(self.calls.count(XML),1);self.assertTrue(list(self.out.glob('*.png')))
        self.assertTrue((self.out/'xml-spent.json').exists());self.assertNotIn('private content',json.dumps(result))
    def test_corrupt_png_not_success(self):
        self.replies[PNG]=b'corrupt';result=self.run_capture();self.assertEqual(result['result'],'FAILED_OR_UNKNOWN');self.assertEqual(self.calls.count(XML),0)
    def test_foreground_change_quarantines_raw(self):
        original=self.reply
        async def change(transport,args):
            raw=await original(transport,args)
            if args==PNG:self.metadata['displays']=self.metadata['displays'].replace('com.android.settings/.Settings','fixture.private/.Screen')
            return raw
        self.reply=change;result=self.run_capture();self.assertEqual(result['result'],'FAILED_OR_UNKNOWN')
        audit=json.loads((self.out/'manual-result-private.json').read_bytes());self.assertFalse(audit['files'][0]['scope_verified']);self.assertTrue(list(self.out.glob('*.png')))
    def test_wms_focus_missing_before_capture_rejected(self):
        self.metadata['windows']=self.metadata['windows'].split('  mCurrentFocus=')[0]
        self.metadata['displays']=self.metadata['displays'].replace('mCurrentFocus=','missingFocus=')
        result=self.run_capture();self.assertEqual(result['reason_code'],'DIAGNOSTIC_CONTENT_SCOPE_UNVERIFIED');self.assertNotIn(PNG,self.calls)
    def test_active_unknown_overlay_rejected(self):
        self.metadata['windows']=self.metadata['windows'].replace('ty=BASE_APPLICATION','ty=2314',1)
        self.assertEqual(self.run_capture()['result'],'FAILED_OR_UNKNOWN');self.assertNotIn(PNG,self.calls)
    def test_shared_lease_busy_no_commands(self):
        s=Store(self.db);s.acquire('real','other',30);s.close()
        self.assertEqual(self.run_capture()['result'],'FAILED_OR_UNKNOWN');self.assertEqual(self.calls,[])
    def test_no_task_memory_or_budget_writes(self):
        self.run_capture();s=Store.open_readonly(self.db)
        try:
            for t in ['task_runs','memories','events','schedule','run_schedules','lease']:self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0],0)
        finally:s.close()
    def test_xml_known_trailer_and_pure_xml(self):
        self.assertEqual(extract_xml(xml()),xml());self.assertEqual(extract_xml(self.replies[XML]),xml())
    def test_xml_arbitrary_prefix_or_trailer_rejected(self):
        for raw in [b'private warning\n'+xml(),xml()+b'\narbitrary log',b'<!DOCTYPE x>'+xml()]:
            with self.assertRaises(DiagnosticDenied):extract_xml(raw)
    def test_transport_default_denied_and_shell_injection(self):
        with DiagnosticLease(self.sources) as lease:
            t=ManualDiagnosticTransport(self.request,self.profile,lease,self.evidence)
            with self.assertRaises(DiagnosticDenied):asyncio.run(t.run(PNG))
            t.active=True
            with self.assertRaises(DiagnosticDenied):asyncio.run(t.run(('shell','input','tap','1','2')))
    def test_binary_substitution_denied(self):
        self.adb.write_bytes(b'changed')
        with self.assertRaises(DiagnosticDenied):self.run_capture()
        self.assertEqual(self.calls,[])
    def test_normalized_xml_keeps_original_bytes_hash(self):
        self.run_capture();audit=json.loads((self.out/'manual-result-private.json').read_bytes());item=audit['files'][1]
        self.assertEqual(item['raw_sha256'],sha(self.replies[XML]));self.assertEqual((self.out/item['normalized_file']).read_bytes(),xml())
    def test_fixed_process_vector_and_nonzero_consumes_once(self):
        reader=Pipe(b'OKAY00040029')
        self.mark_approved()
        with DiagnosticLease(self.sources) as lease:
            t=ManualDiagnosticTransport(self.request,self.profile,lease,self.evidence);t.active=True
            with patch('asyncio.open_connection',new=AsyncMock(return_value=(reader,Writer()))),patch('asyncio.create_subprocess_exec',new=AsyncMock(return_value=Proc())) as spawn:
                with self.assertRaises(DiagnosticDenied):asyncio.run(t.run(PNG))
                self.assertEqual(spawn.call_args.args,(str(self.adb),'-s',self.profile.serial,*PNG));self.assertNotIn('shell',spawn.call_args.kwargs)
                with self.assertRaises(DiagnosticDenied):asyncio.run(t.run(PNG))
                self.assertEqual(spawn.call_count,1)
    def test_unknown_dispatch_source_not_cleared(self):
        s=Store(self.db);s.start_task('fixture','real','fixture',{},run_kind='mock_test');s.conn.execute('INSERT INTO action_attempts VALUES(?,?,?,?,?)',('a','fixture',0,0,json.dumps({'execution_status':'outcome_unknown'})));s.close()
        self.assertEqual(self.run_capture()['result'],'FAILED_OR_UNKNOWN');self.assertEqual(self.calls,[])
    def test_timeout_kills_only_owned_client(self):
        proc=Proc(None,None)
        self.mark_approved()
        with DiagnosticLease(self.sources) as lease:
            t=ManualDiagnosticTransport(self.request,self.profile,lease,self.evidence);t.active=True
            with patch('asyncio.open_connection',new=AsyncMock(return_value=(Pipe(b'OKAY00040029'),Writer()))),patch('asyncio.create_subprocess_exec',new=AsyncMock(return_value=proc)) as spawn,patch('phonelife.manual_diagnostic.STEP_SECONDS',.03):
                with self.assertRaises(DiagnosticDenied):asyncio.run(t.run(PNG))
            self.assertEqual(proc.killed,1);self.assertEqual(spawn.call_count,1);self.assertTrue((self.out/'png-spent.json').exists())
    def test_cancel_never_retries_or_returns_quota(self):
        proc=Proc(None,None)
        self.mark_approved()
        with DiagnosticLease(self.sources) as lease:
            t=ManualDiagnosticTransport(self.request,self.profile,lease,self.evidence);t.active=True
            async def run():
                task=asyncio.create_task(t.run(XML))
                while not t.calls or t.calls[0]['status']!='dispatched_unknown':await asyncio.sleep(.001)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            with patch('asyncio.open_connection',new=AsyncMock(return_value=(Pipe(b'OKAY00040029'),Writer()))),patch('asyncio.create_subprocess_exec',new=AsyncMock(return_value=proc)) as spawn:asyncio.run(run())
            self.assertEqual(proc.killed,1);self.assertEqual(spawn.call_count,1);self.assertTrue((self.out/'xml-spent.json').exists())
    def test_coloros_windows_missing_duplicate_focus_with_display_input_consensus(self):
        self.metadata['windows']=self.metadata['windows'].split('  mCurrentFocus=')[0]
        self.assertEqual(self.run_capture()['result'],'REAL_SCREEN_CAPTURE_VERIFIED')
    def test_active_flag_alone_not_human_approval(self):
        with DiagnosticLease(self.sources) as lease:
            t=ManualDiagnosticTransport(self.request,self.profile,lease,self.evidence);t.active=True
            with patch('asyncio.create_subprocess_exec',new=AsyncMock()) as spawn:
                with self.assertRaises(DiagnosticDenied):asyncio.run(t.run(PNG))
                spawn.assert_not_called()
