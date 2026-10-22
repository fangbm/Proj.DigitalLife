"""offline_fixture ONLY. Temporary databases, synthetic USB identity/PNG/XML.

No real authorization is issued, no adb binary or connected device is used.
"""
import asyncio
import ast
import copy
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
from PIL import Image
from phonelife.device_profiles import PhysicalDeviceProfile, IDENTITY_PROPERTIES
from phonelife.diagnostic_authorization import (DiagnosticLedger, OfflinePermitVerifier,
    PURPOSE, ENTROPY, strict_json, validate_permit, unprotect_diagnostic)
from phonelife.diagnostic_capture import (PhysicalDiagnosticCapture, MockAdbDiagnosticBackend,
    RealAdbDiagnosticBackend, CaptureOperation, DiagnosticSafetySources, DiagnosticLease,
    diagnostic_scene, diagnostic_capture_admission, fixed_capture_argv, FIXED_METADATA_ARGV)
from phonelife.diagnostic_evidence import (PrivateEvidence, DiagnosticDenied, private_path,
    canonical, sha, validate_payload, analyze_pair, create_private_directory)
from phonelife.storage import Store
from test_window_admission import graph_scene

PNG = 'CAPTURE_SCREEN_PNG'; XML = 'CAPTURE_UI_XML'; KEY = b'offline fixture verifier only!!!.'*2


def scene():
    value = graph_scene()
    value['displays'] = value['displays'].replace('com.android.settings/FixtureActivity','com.android.settings/.Settings')
    return value


def png(width=100,height=200):
    out = io.BytesIO(); Image.new('RGB',(width,height),(20,30,40)).save(out,format='PNG'); return out.getvalue()


def xml():
    return b'<hierarchy rotation="0"><node package="com.android.settings" class="android.view.View" bounds="[0,0][100,200]" clickable="false" enabled="true" focusable="false"/></hierarchy>'


def envelope(grant):
    return canonical({'offline_fixture':True,'payload':grant,'mac':hmac.new(KEY,canonical(grant),hashlib.sha256).hexdigest()})


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.output = self.root/'private'; create_private_directory(self.output)
        identity = dict.fromkeys(IDENTITY_PROPERTIES,'fixture')
        identity.update({'ro.product.manufacturer':'OPPO','ro.product.model':'PGFM10','ro.build.version.sdk':'36',
                         'ro.build.version.release':'16','ro.build.fingerprint':'offline_fixture','ro.kernel.qemu':'0'})
        self.profile = PhysicalDeviceProfile('fixture-usb',identity)
        self.evidence = PrivateEvidence(self.output)
        self.coordinator = self.root/'coordinator.sqlite3'; s = Store(self.coordinator); s.close()
        self.sources = DiagnosticSafetySources(self.coordinator)
        self.backend = MockAdbDiagnosticBackend(self.profile,scene(),{PNG:png(),XML:xml()})
        self.verifier = OfflinePermitVerifier(KEY)
        self.channel = PhysicalDiagnosticCapture(backend=self.backend,verifier=self.verifier,evidence=self.evidence,sources=self.sources)
        now = time.time()
        self.grant = {'schema':'diagnostic-approval/1','purpose':PURPOSE,'identity_sha256':self.profile.identity_sha256,
            'operations':{PNG:2,XML:1},'page_scope':'settings_home','not_before':now-1,'expires_at':now+120,
            'output_directory':str(self.output),'nonce':uuid.uuid4().hex,'operator_approved':True,
            'operator_reference':'offline-fixture-human-approval','reason':'synthetic regression',
            'audit_id':uuid.uuid4().hex,'category':'offline_fixture'}

    def capture(self,operation=PNG,request_id=None,blob=None):
        return asyncio.run(self.channel.capture(envelope(self.grant) if blob is None else blob,
            request_id=request_id or uuid.uuid4().hex,operation=CaptureOperation(operation)))

    def denied(self,code=None,**kwargs):
        with self.assertRaises(DiagnosticDenied) as error: self.capture(**kwargs)
        if code: self.assertEqual(error.exception.code,code)
        return error.exception.code

    def records(self):
        p = self.output/'diagnostic.sqlite3'
        if not p.exists(): return []
        c = sqlite3.connect(p)
        try: return [(a,b,json.loads(v)) for a,b,v in c.execute('SELECT request_id,status,record FROM captures')]
        finally: c.close()

    def control(self,state):
        p = self.root/'control.sqlite3'; c = sqlite3.connect(p)
        c.execute('CREATE TABLE session(id INTEGER PRIMARY KEY,payload TEXT)'); c.execute('INSERT INTO session VALUES(1,?)',(json.dumps(state),)); c.commit(); c.close()
        self.sources.control_paths = (p,)
        return p

    def test_synthetic_png(self):
        r = self.capture(); self.assertEqual(r['result'],'DIAGNOSTIC_CAPTURE_COMPLETED'); self.assertEqual(r['summary'],{'width':100,'height':200})
    def test_synthetic_xml(self):
        r = self.capture(XML); self.assertEqual(r['summary']['nodes'],1); self.assertFalse(r['GUI_EXECUTION_SEMANTICS_TRUSTED'])
    def test_missing_authorization(self): self.denied(blob=b'{}'); self.assertEqual(self.backend.calls,[])
    def test_expired(self): self.grant['expires_at']=time.time()-1; self.denied('PERMIT_EXPIRED_OR_WINDOW')
    def test_not_yet_valid(self): self.grant['not_before']=time.time()+20; self.denied('PERMIT_EXPIRED_OR_WINDOW')
    def test_unbounded_window(self): self.grant['expires_at']=time.time()+700; self.denied('PERMIT_EXPIRED_OR_WINDOW')
    def test_wrong_purpose(self): self.grant['purpose']='physical-execution-approval/1'; self.denied('PERMIT_PURPOSE')
    def test_wrong_device(self): self.grant['identity_sha256']='0'*64; self.denied('PERMIT_DEVICE_MISMATCH')
    def test_wrong_type(self): self.grant['operations']={PNG:1}; self.denied('PERMIT_TYPE_NOT_ALLOWED',operation=XML); self.assertNotIn(XML,self.backend.calls)
    def test_exhaustion(self): self.grant['operations']={PNG:1}; self.capture(); self.denied('PERMIT_EXHAUSTED'); self.assertEqual(self.backend.calls.count(PNG),1)
    def test_request_replay(self):
        rid=uuid.uuid4().hex; self.capture(request_id=rid); self.denied(request_id=rid); self.assertEqual(self.backend.calls.count(PNG),1)
    def test_tampered_envelope(self):
        e=json.loads(envelope(self.grant));e['payload']['operations'][XML]=2;self.denied('PERMIT_TAMPERED',blob=canonical(e))
    def test_permit_nonce_conflict(self): self.capture(); self.grant['reason']='changed'; self.denied('PERMIT_NONCE_CONFLICT')
    def test_false_consent(self): self.grant['operator_approved']=False; self.denied('OPERATOR_APPROVAL_REQUIRED')
    def test_numeric_consent(self): self.grant['operator_approved']=1; self.denied('OPERATOR_APPROVAL_REQUIRED')
    def test_other_scope(self): self.grant['page_scope']='any_app'; self.denied('PERMIT_SCOPE')
    def test_model_text_not_permit(self): self.denied(blob=b'Capture the screen now!'); self.assertEqual(self.backend.calls,[])
    def test_cli_flag_not_permit(self): self.denied(blob=b'--enabled=true --force'); self.assertEqual(self.backend.calls,[])
    def test_config_enabled_not_permit(self): self.denied(blob=b'{"enabled":true}'); self.assertEqual(self.backend.calls,[])
    def test_real_category_denied(self): self.grant['category']='live_device_diagnostic'; self.denied('PREP_REAL_APPROVAL_DISABLED')
    def test_unknown_permit_field(self): self.grant['execute_gui']=True; self.denied('PERMIT_SCHEMA')
    def test_boolean_count(self): self.grant['operations']={PNG:True}; self.denied('PERMIT_OPERATIONS')
    def test_excessive_count(self): self.grant['operations']={PNG:3}; self.denied('PERMIT_OPERATIONS')
    def test_nan_time(self):
        value=copy.deepcopy(self.grant);value['expires_at']=float('nan')
        with self.assertRaises(DiagnosticDenied): validate_permit(value,identity_sha256=self.profile.identity_sha256,output_directory=self.output,now=time.time())
    def test_duplicate_keys(self):
        with self.assertRaises(DiagnosticDenied): strict_json(b'{"a":1,"a":2}')
    def test_large_permit(self): self.denied(blob=b'x'*32769)
    def test_output_binding(self): self.grant['output_directory']=str(self.root);self.denied('PERMIT_OUTPUT_MISMATCH')
    def test_wrong_serial(self): self.backend.snapshots[0]['serial']='other'; self.denied('DIAGNOSTIC_DEVICE_AMBIGUOUS_OR_OFFLINE')
    def test_multiple_adb_devices(self): self.backend.snapshots[0]['devices'].append(('other','device'));self.denied('DIAGNOSTIC_DEVICE_AMBIGUOUS_OR_OFFLINE')
    def test_disconnected(self): self.backend.snapshots[0]['devices']=[];self.denied('DIAGNOSTIC_DEVICE_AMBIGUOUS_OR_OFFLINE')
    def test_usb_unauthorized(self): self.backend.snapshots[0]['devices']=[(self.profile.serial,'unauthorized')];self.denied('DIAGNOSTIC_DEVICE_AMBIGUOUS_OR_OFFLINE')
    def test_usb_unknown(self): self.backend.snapshots[0]['devices']=[(self.profile.serial,'unknown')];self.denied('DIAGNOSTIC_DEVICE_AMBIGUOUS_OR_OFFLINE')
    def test_before_identity_mismatch(self): self.backend.snapshots[0]['identity']['ro.product.device']='other';self.denied('DIAGNOSTIC_IDENTITY_MISMATCH');self.assertNotIn(PNG,self.backend.calls)
    def test_after_identity_change(self):
        self.backend.snapshots.append(copy.deepcopy(self.backend.snapshots[0]));self.backend.snapshots[1]['identity']['ro.product.device']='other'
        self.denied('DIAGNOSTIC_IDENTITY_MISMATCH');self.assertEqual(self.records()[0][1],'spent_failed_or_unknown');self.assertFalse(list(self.output.glob('*.png')))
    def test_after_page_change(self):
        self.backend.snapshots.append(copy.deepcopy(self.backend.snapshots[0]));self.backend.snapshots[1]['metadata']['displays']=self.backend.snapshots[1]['metadata']['displays'].replace('.Settings','.Private')
        self.denied('DIAGNOSTIC_CONTENT_SCOPE_UNVERIFIED');self.assertEqual(self.backend.calls.count(PNG),1)
    def test_page_changed_but_still_settings(self):
        self.backend.snapshots.append(copy.deepcopy(self.backend.snapshots[0]));self.backend.snapshots[1]['metadata']['input']=self.backend.snapshots[1]['metadata']['input'].replace('token=0xab','token=0xae')
        self.denied('DIAGNOSTIC_SCENE_CHANGED')
    def test_competing_gui_lease(self):
        s=Store(self.coordinator);self.assertTrue(s.acquire('real','autonomous-gui',30));s.close()
        self.denied('DIAGNOSTIC_DEVICE_BUSY');self.assertEqual(self.backend.calls,[])
    def test_concurrent_diagnostic_lease(self):
        with DiagnosticLease(self.sources): self.denied('DIAGNOSTIC_DEVICE_BUSY')
    def test_life_controls_absence_normal(self): self.assertFalse(self.sources.check()['life_controls_present']);self.capture()
    def test_missing_coordinator_not_created(self): self.sources.coordinator=self.root/'absent.sqlite3';self.denied('DIAGNOSTIC_SAFETY_SOURCE_UNREADABLE');self.assertFalse(self.sources.coordinator.exists())
    def test_unknown_dispatch_not_bypassed(self):
        s=Store(self.coordinator);s.start_task('fixture','real','fixture',{},run_kind='mock_test');s.conn.execute('INSERT INTO action_attempts VALUES(?,?,?,?,?)',('a','fixture',0,0,json.dumps({'execution_status':'outcome_unknown'})));s.close()
        before=self.coordinator.read_bytes();self.denied('UNKNOWN_DISPATCH_NEEDS_REVIEW');self.assertEqual(before,self.coordinator.read_bytes());self.assertEqual(self.backend.calls,[])
    def test_review_marker_blocks(self):
        p=self.control({'state':'awaiting_approval'});Path(str(p)+'.review-stop.json').write_text('corrupt');self.denied('UNKNOWN_DISPATCH_NEEDS_REVIEW')
    def test_sql_review_blocks(self): self.control({'state':'failed','safety_stop':{'reason_code':'UNKNOWN_DISPATCH_NEEDS_REVIEW'}});self.denied('UNKNOWN_DISPATCH_NEEDS_REVIEW')
    def test_emergency_stop_not_cleared(self):
        p=self.control({'state':'emergency_stopped'});before=p.read_bytes();self.denied('EMERGENCY_STOP');self.assertEqual(before,p.read_bytes())
    def test_corrupt_control_denies(self): p=self.control({'state':'paused'});p.write_bytes(b'broken');self.denied('DIAGNOSTIC_SAFETY_SOURCE_UNREADABLE')
    def test_private_directory_acl_error(self):
        with patch('phonelife.diagnostic_evidence.private_path',side_effect=DiagnosticDenied('PRIVATE_ACL_NOT_OWNER_ONLY')): self.denied('PRIVATE_ACL_NOT_OWNER_ONLY')
    def test_relative_path_denied(self):
        with self.assertRaises(DiagnosticDenied): private_path(Path('relative'))
    def test_traversal_path_denied(self):
        with self.assertRaises(DiagnosticDenied): private_path(self.output/'..'/'private')
    def test_reparse_path_denied(self):
        fake=self.output.lstat(); values=dict(st_mode=fake.st_mode,st_file_attributes=0x400)
        with patch.object(Path,'lstat',return_value=type('Stat',(),values)()):
            with self.assertRaises(DiagnosticDenied): private_path(self.output)
    def test_corrupt_png(self): self.backend.payloads[PNG]=b'not png';self.denied('CAPTURE_INVALID_FORMAT')
    def test_truncated_png(self): self.backend.payloads[PNG]=png()[:40];self.denied('CAPTURE_INVALID_FORMAT')
    def test_wrong_png_dimensions(self): self.backend.payloads[PNG]=png(50,50);self.denied('DIAGNOSTIC_GEOMETRY_MISMATCH')
    def test_oversized_png(self): self.backend.payloads[PNG]=b'x'*(16*1024**2+1);self.denied('CAPTURE_OUTPUT_LIMIT')
    def test_corrupt_xml(self): self.backend.payloads[XML]=b'<broken';self.denied('CAPTURE_INVALID_FORMAT',operation=XML)
    def test_oversized_xml(self): self.backend.payloads[XML]=b'x'*2000001;self.denied('CAPTURE_OUTPUT_LIMIT',operation=XML)
    def test_xml_external_entity(self): self.backend.payloads[XML]=b'<!DOCTYPE hierarchy [<!ENTITY x SYSTEM "file:///private">]><hierarchy/>';self.denied('CAPTURE_INVALID_FORMAT',operation=XML)
    def test_xml_wrong_owner(self): self.backend.payloads[XML]=xml().replace(b'com.android.settings',b'fixture.private');self.denied('DIAGNOSTIC_XML_SCOPE_MISMATCH',operation=XML)
    def test_xml_wrong_rotation(self): self.backend.payloads[XML]=xml().replace(b'rotation="0"',b'rotation="1"');self.denied('DIAGNOSTIC_XML_SCOPE_MISMATCH',operation=XML)
    def test_xml_missing_properties_never_trusted(self): self.assertTrue(self.capture(XML)['summary']['missing_safety_attributes']);self.assertFalse(analyze_pair(png(),xml())['GUI_EXECUTION_SEMANTICS_TRUSTED'])
    def test_timeout_no_retry_spent(self):
        self.backend.delay_seconds=.1
        with patch('phonelife.diagnostic_capture.STEP_TIMEOUT_SECONDS',.02):self.denied('DIAGNOSTIC_TIMEOUT_UNKNOWN')
        self.assertEqual(self.backend.calls.count(PNG),1);self.assertEqual(self.records()[0][1],'spent_failed_or_unknown')
    def test_nonzero_no_retry(self): self.backend.capture_error='DIAGNOSTIC_ADB_NONZERO';self.denied('DIAGNOSTIC_ADB_NONZERO');self.assertEqual(self.backend.calls.count(PNG),1)
    def test_unknown_no_retry(self): self.backend.capture_error='DIAGNOSTIC_ADB_OUTCOME_UNKNOWN';self.denied('DIAGNOSTIC_ADB_OUTCOME_UNKNOWN');self.assertEqual(self.backend.calls.count(PNG),1)
    def test_file_write_failure(self):
        with patch.object(self.evidence,'save',side_effect=OSError('sensitive raw text')):self.denied('DIAGNOSTIC_IO_OR_AUDIT_FAILURE')
        self.assertEqual(self.records()[0][1],'spent_failed_or_unknown')
    def test_filename_conflict(self):
        rid=uuid.uuid4().hex;p=self.output/(rid+'.png');p.write_bytes(b'preserve')
        self.denied('DIAGNOSTIC_IO_OR_AUDIT_FAILURE',request_id=rid);self.assertEqual(p.read_bytes(),b'preserve')
    def test_hash_and_audit_binding(self):
        r=self.capture();v=self.records()[0][2];self.assertEqual(r['raw_sha256'],sha(png()));self.assertEqual(v['permit_sha256'],sha(canonical(self.grant)));self.assertEqual(v['audit_id'],self.grant['audit_id']);self.assertEqual(sha((self.output/v['private_file']).read_bytes()),r['raw_sha256'])
    def test_summary_has_no_private_text(self):
        text=json.dumps(self.capture());self.assertNotIn(self.profile.serial,text);self.assertNotIn(str(self.output),text);self.assertNotIn('operator_reference',text);self.assertNotIn('raw_xml',text)
    def test_no_task_memory_schedule_budget_pollution(self):
        s=Store(self.coordinator);tables=['task_runs','memories','events','schedule','run_schedules'];before={t:s.conn.execute('SELECT * FROM '+t).fetchall() for t in tables};s.close()
        self.capture();s=Store.open_readonly(self.coordinator)
        self.assertEqual(before,{t:s.conn.execute('SELECT * FROM '+t).fetchall() for t in tables});self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0);s.close()
    def test_no_brain_gui_reflection_adb_or_worker_calls(self):
        with patch('asyncio.create_subprocess_exec',side_effect=AssertionError('subprocess')),patch('subprocess.Popen',side_effect=AssertionError('process')),patch('urllib.request.urlopen',side_effect=AssertionError('network')):
            self.capture()
        self.assertFalse(any(c in {'tap','swipe','text','back','home'} for c in self.backend.calls))
    def test_no_forbidden_core_imports(self):
        for name in ('diagnostic_capture','diagnostic_authorization','diagnostic_evidence'):
            module=__import__('phonelife.'+name,fromlist=['__file__']);tree=ast.parse(Path(module.__file__).read_text('utf8'))
            imports={n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)}
            self.assertFalse(imports & {'brain','llm','loop','physical_execution','life_storage','life_subject','longrun','budgets','policy','capabilities'})
    def test_real_backend_disabled(self):
        with self.assertRaises(DiagnosticDenied): PhysicalDiagnosticCapture(backend=RealAdbDiagnosticBackend(),verifier=self.verifier,evidence=self.evidence,sources=self.sources)
        with self.assertRaises(DiagnosticDenied):asyncio.run(RealAdbDiagnosticBackend().capture('anything',CaptureOperation.CAPTURE_SCREEN_PNG))
    def test_default_constructor_disabled(self):
        with self.assertRaises(DiagnosticDenied): PhysicalDiagnosticCapture()
    def test_backend_subclass_cannot_activate_real(self):
        class Fake(MockAdbDiagnosticBackend):pass
        b=Fake(self.profile,scene(),{PNG:png()})
        with self.assertRaises(DiagnosticDenied): PhysicalDiagnosticCapture(backend=b,verifier=self.verifier,evidence=self.evidence,sources=self.sources)
    def test_real_profile_cannot_use_mock(self):
        identity=dict(self.profile.identity);identity['ro.build.fingerprint']='real-build'
        with self.assertRaises(DiagnosticDenied):MockAdbDiagnosticBackend(PhysicalDeviceProfile('fixture-usb',identity),scene(),{})
    def test_command_injection_rejected(self):
        for serial in ('x;rm','-s','x y','127.0.0.1:5555'):
            with self.assertRaises(ValueError):fixed_capture_argv(serial,CaptureOperation.CAPTURE_SCREEN_PNG)
    def test_model_command_not_enum(self):
        with self.assertRaises(DiagnosticDenied):fixed_capture_argv('fixture-usb','CAPTURE_SCREEN_PNG')
    def test_only_two_capture_operations(self):
        self.assertEqual({o.value for o in CaptureOperation},{PNG,XML});self.assertEqual(fixed_capture_argv('fixture-usb',CaptureOperation.CAPTURE_SCREEN_PNG),('-s','fixture-usb','exec-out','screencap','-p'))
    def test_fixed_metadata_no_input(self):
        self.assertTrue(all('input' not in r or r==('shell','dumpsys','input') for r in FIXED_METADATA_ARGV));self.assertFalse(any('am' in r or 'install' in r or 'pull' in r for r in FIXED_METADATA_ARGV))
    def test_dpapi_purpose_isolated(self):self.assertEqual(PURPOSE,'PHYSICAL_DIAGNOSTIC_CAPTURE');self.assertNotEqual(ENTROPY,b'Proj.DigitalLife physical execution approval v1')
    def test_invalid_dpapi_no_key_access(self):
        with self.assertRaises(DiagnosticDenied):unprotect_diagnostic(b'')
    def test_lease_released_on_error(self): self.backend.payloads[PNG]=b'broken';self.denied();s=Store.open_readonly(self.coordinator);self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0);s.close()
    def test_audit_write_failure_stays_spent(self):
        with patch.object(DiagnosticLedger,'finish',side_effect=sqlite3.OperationalError('disk')):self.denied('DIAGNOSTIC_IO_OR_AUDIT_FAILURE')
        self.assertEqual(self.records()[0][1],'started');self.grant['operations'][PNG]=1;self.denied('PERMIT_NONCE_CONFLICT')
    def test_before_dispatch_ledger_failure_no_capture(self):
        with patch.object(DiagnosticLedger,'consume',side_effect=sqlite3.OperationalError('disk')):self.denied('DIAGNOSTIC_IO_OR_AUDIT_FAILURE')
        self.assertNotIn(PNG,self.backend.calls)
    def test_restart_durable_exhaustion(self):
        self.grant['operations']={PNG:1};self.capture();new=PrivateEvidence(self.output);l=DiagnosticLedger(new);self.assertEqual(l.conn.execute('SELECT COUNT(*) FROM captures').fetchone()[0],1);l.close();self.denied('PERMIT_EXHAUSTED')
    def test_subprocess_restart_spent_after_abrupt_exit(self):
        code='from pathlib import Path;import os;from phonelife.diagnostic_evidence import PrivateEvidence;from phonelife.diagnostic_authorization import DiagnosticLedger;l=DiagnosticLedger(PrivateEvidence(Path(__import__("sys").argv[1])));l.conn.execute("INSERT INTO permits VALUES(?,?)",("fixture-nonce","fixture-digest"));l.conn.execute("INSERT INTO captures VALUES(?,?,?,?,?)",("fixture-request","fixture-nonce","CAPTURE_SCREEN_PNG","started","{}"));os._exit(23)'
        r=subprocess.run([sys.executable,'-c',code,str(self.output)],capture_output=True,timeout=10)
        self.assertEqual(r.returncode,23,r.stderr.decode());l=DiagnosticLedger(PrivateEvidence(self.output));self.assertEqual(l.conn.execute('SELECT status FROM captures').fetchone()[0],'started');l.close();self.assertEqual(self.backend.calls,[])
    def test_cancel_spent_and_release(self):
        async def run():
            self.backend.delay_seconds=1
            t=asyncio.create_task(self.channel.capture(envelope(self.grant),request_id=uuid.uuid4().hex,operation=CaptureOperation.CAPTURE_SCREEN_PNG))
            while PNG not in self.backend.calls: await asyncio.sleep(.001)
            t.cancel()
            with self.assertRaises(asyncio.CancelledError):await t
        asyncio.run(run());self.assertEqual(self.records()[0][1],'spent_failed_or_unknown');s=Store.open_readonly(self.coordinator);self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0);s.close()
    def test_nonsettings_content_rejected(self):
        v=scene();v['displays']=v['displays'].replace('com.android.settings','fixture.private')
        with self.assertRaises(DiagnosticDenied):diagnostic_scene(v)
    def test_locked_device_rejected(self):
        v=scene();v['displays']=v['displays'].replace('isKeyguardShowing=false','isKeyguardShowing=true')
        with self.assertRaises(DiagnosticDenied):diagnostic_scene(v)
    def test_unknown_visible_window_rejected(self):
        v=scene();v['windows']=v['windows'].replace('ty=BASE_APPLICATION','ty=2314',1)
        with self.assertRaises(DiagnosticDenied):diagnostic_scene(v)
    def test_hidden_input_channels_not_execution_bypass(self):
        v=scene();v['input']=v['input'].replace('NO_INPUT_CHANNEL | ','').replace('token=0x0','token=0xac')
        self.assertEqual(diagnostic_scene(v)['scope'],'settings_home');from phonelife.native_windows import assess_window_graph
        self.assertNotEqual(assess_window_graph(v)['classification'],'SAFE_FOR_READONLY_CAPTURE');self.assertFalse(assess_window_graph(v)['physical_input_allowed'])
    def test_unknown_dormant_oem_window_can_be_diagnostic_only(self):
        v=scene();at=v['windows'].index('  Window #1');v['windows']=v['windows'][:at]+v['windows'][at:].replace('ty=BASE_APPLICATION','ty=2315')
        self.assertEqual(diagnostic_scene(v)['scope'],'settings_home');self.assertFalse(diagnostic_scene(v)['physical_input_allowed'])
    def test_error_text_not_exposed(self):
        self.backend.capture_error='private serial or raw UI';self.denied('DIAGNOSTIC_BACKEND_FAILED')
    def test_xml_empty_tree_denied(self):self.backend.payloads[XML]=b'<hierarchy rotation="0"/>';self.denied('CAPTURE_INVALID_FORMAT',operation=XML)
    def test_screen_unknown_foreground_denied(self):
        self.backend.snapshots[0]['metadata']['displays']=self.backend.snapshots[0]['metadata']['displays'].replace('mCurrentFocus=','mUnknownFocus=');self.denied('DIAGNOSTIC_CONTENT_SCOPE_UNVERIFIED')
    def test_permission_window_denied(self):
        v=scene();v['windows']=v['windows'].replace('com.android.settings','com.android.permissioncontroller')
        with self.assertRaises(DiagnosticDenied):diagnostic_scene(v)
    def test_after_disconnect_no_raw_saved(self):
        self.backend.snapshots.append(copy.deepcopy(self.backend.snapshots[0]));self.backend.snapshots[1]['devices']=[]
        self.denied('DIAGNOSTIC_DEVICE_AMBIGUOUS_OR_OFFLINE');self.assertFalse(list(self.output.glob('*.png')))
    def test_evidence_parent_changed_denied(self):
        self.evidence.identity=(0,0);self.denied('PRIVATE_DIRECTORY_CHANGED');self.assertEqual(self.backend.calls,[])
    def test_private_create_no_existing_repair(self):
        before=self.output.stat()
        with self.assertRaises(FileExistsError):create_private_directory(self.output)
        self.assertEqual(before.st_ino,self.output.stat().st_ino)
    def test_unreadable_audit_db_denies(self):self.sources.audit_paths=(self.root/'missing-agent.sqlite3',);self.denied('DIAGNOSTIC_SAFETY_SOURCE_UNREADABLE')
    def test_unknown_control_state_denies(self):self.control({'state':'mystery'});self.denied('DIAGNOSTIC_SAFETY_SOURCE_UNREADABLE')
    def test_formal_control_not_modified(self):
        p=self.control({'state':'awaiting_approval','operator':'running','duration_seconds':86400,'elapsed_seconds':0})
        before=p.read_bytes();self.capture();self.assertEqual(before,p.read_bytes())
    def test_concurrent_async_calls_only_one_dispatch(self):
        async def run():
            self.backend.delay_seconds=.04
            async def one():
                try:return await self.channel.capture(envelope(self.grant),request_id=uuid.uuid4().hex,operation=CaptureOperation.CAPTURE_SCREEN_PNG)
                except DiagnosticDenied as e:return e.code
            return await asyncio.gather(one(),one())
        rows=asyncio.run(run());self.assertEqual(sum(isinstance(r,dict) for r in rows),1);self.assertIn('DIAGNOSTIC_DEVICE_BUSY',rows);self.assertEqual(self.backend.calls.count(PNG),1)
    def test_clock_expiry_during_capture(self):
        now=time.time();original=self.backend.capture
        with patch('phonelife.diagnostic_capture.time.time',return_value=now) as clock:
            async def expire(*args):
                raw=await original(*args);clock.return_value=now+300;return raw
            with patch.object(self.backend,'capture',side_effect=expire):self.denied('PERMIT_EXPIRED_OR_WINDOW')
        self.assertEqual(self.backend.calls.count(PNG),1)
    def test_lease_loss_does_not_release_other_owner(self):
        original=self.evidence.save
        def lose(*args):
            name=original(*args);c=sqlite3.connect(self.coordinator);c.execute('UPDATE lease SET owner=? WHERE device=?',('other','real'));c.commit();c.close();return name
        with patch.object(self.evidence,'save',side_effect=lose):self.denied('DIAGNOSTIC_LEASE_LOST')
        c=sqlite3.connect(self.coordinator);self.assertEqual(c.execute('SELECT owner FROM lease WHERE device=?',('real',)).fetchone()[0],'other');c.close()
    def test_dump_timestamps_do_not_define_content_scope(self):
        self.backend.snapshots.append(copy.deepcopy(self.backend.snapshots[0]));self.backend.snapshots[1]['metadata']['power']+='\n elapsed=42'
        self.assertEqual(self.capture()['result'],'DIAGNOSTIC_CAPTURE_COMPLETED')
    def test_offline_analysis_bad_bounds_not_trusted(self):
        x=xml().replace(b'[0,0][100,200]',b'[0,0][999,999]')
        r=analyze_pair(png(),x,metadata_before=scene(),metadata_after=scene(),started_ns=1,ended_ns=2)
        self.assertIn('XML_BOUNDS_UNVERIFIED',r['native_semantics']['reason_codes']);self.assertFalse(r['GUI_EXECUTION_SEMANTICS_TRUSTED'])
    def test_offline_analysis_stale_not_trusted(self):
        r=analyze_pair(png(),xml(),started_ns=1,ended_ns=3_000_000_001)
        self.assertFalse(r['timing_consistent']);self.assertFalse(r['GUI_EXECUTION_SEMANTICS_TRUSTED'])
    def test_physical_namespace_unknown_dispatch_blocks(self):
        s=Store(self.coordinator);device='physical:'+self.profile.identity_sha256;s.start_task('physical-fixture',device,'fixture',{},run_kind='mock_test');s.conn.execute('INSERT INTO action_attempts VALUES(?,?,?,?,?)',('a','physical-fixture',0,0,json.dumps({'execution_status':'dispatching'})));s.close()
        self.denied('UNKNOWN_DISPATCH_NEEDS_REVIEW');self.assertEqual(self.backend.calls,[])
    def test_reason_only_stop_record_blocks(self):self.control({'state':'failed','reason':'UNKNOWN_DISPATCH_NEEDS_REVIEW'});self.denied('UNKNOWN_DISPATCH_NEEDS_REVIEW')
    def test_wms_focus_conflict_blocks(self):
        v=scene();v['windows']+='  mCurrentFocus=Window{a1 u0 fixture.other/FixtureActivity}\n'
        with self.assertRaises(DiagnosticDenied):diagnostic_scene(v)
    def test_existing_approval_cannot_be_used_as_envelope(self):
        self.denied(blob=b'{"schema":"physical-execution-approval/1","execute_gui":true}')
    def test_consumption_rollback_leaves_no_open_transaction(self):
        l=DiagnosticLedger(self.evidence)
        try:
            l.consume(self.grant,'fixture',PNG,{})
            with self.assertRaises(sqlite3.IntegrityError):l.consume(self.grant,'fixture',PNG,{})
            self.assertFalse(l.conn.in_transaction);self.assertEqual(l.conn.execute('SELECT COUNT(*) FROM captures').fetchone()[0],1)
        finally:l.close()
    def test_missing_node_class_never_grants_execution(self):
        x=xml().replace(b' class="android.view.View"',b'');self.assertIn('class',validate_payload(XML,x)['missing_safety_attributes']);self.assertFalse(analyze_pair(png(),x)['GUI_EXECUTION_SEMANTICS_TRUSTED'])
    def test_model_api_budget_not_an_approval(self):self.denied(blob=b'{"api_key":"synthetic-placeholder","max_calls":20}');self.assertEqual(self.backend.calls,[])
    def test_duplicate_agreeing_viewports(self):
        v=scene();v['input']+='\nViewport INTERNAL: displayId=0, orientation=0\n';self.assertEqual(diagnostic_scene(v)['rotation'],0)
    def test_conflicting_viewports_rejected(self):
        v=scene();v['input']+='\nViewport INTERNAL: displayId=0, orientation=1\n'
        with self.assertRaises(DiagnosticDenied):diagnostic_scene(v)
    def test_requested_unknown_without_surface_is_not_displayed(self):
        v=scene();at=v['windows'].index('  Window #1');v['windows']=v['windows'][:at]+v['windows'][at:].replace('ty=BASE_APPLICATION','ty=2315').replace('mHasSurface=true','mHasSurface=false').replace('isVisible=true','isVisible=false').replace('mViewVisibility=0x0','mViewVisibility=0x8').replace('isVisibleRequested()=false','isVisibleRequested()=true').replace('mLastHidden=true','mLastHidden=false')
        # Contradictory fixture still had an explicit Surface object: deny it.
        with self.assertRaises(DiagnosticDenied):diagnostic_scene(v)
        v['windows']=v['windows'].replace('      mSurface=Surface(name=fixture)/@0xfe\n','')
        self.assertEqual(diagnostic_scene(v)['scope'],'settings_home')
