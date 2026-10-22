"""B2 offline_fixture: isolated databases and synthetic native metadata only.

Real-derived fixtures are separately provenance marked. No device/model access.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import os
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch

from phonelife import native_windows as native
from phonelife.longrun_health import readonly_db_health
from phonelife.storage import Store
from phonelife.readonly_sources import inspect_coordinator,read_bound_sources,observation_admission
from phonelife.longrun import prepare_session,control_session,run_worker,LongRunSoakController
from phonelife.longrun_state import SessionLedger,canonical,REVIEW_STOP
from test_coloros_windows import scene,window,input_row


def graph_scene():
    v=scene()
    v['windows']=v['windows'].replace('mDisplayId=0 mOwnerUid=1000','mDisplayId=0 mSession=Session{e0 123:fixture} mOwnerUid=1000')
    v['windows']=v['windows'].replace('mAttrs={ty=BASE_APPLICATION}','mAttrs={ty=BASE_APPLICATION fl=NOT_FOCUSABLE NOT_TOUCHABLE}')
    v['windows']=v['windows'].replace('ty=BASE_APPLICATION fl=NOT_FOCUSABLE NOT_TOUCHABLE','ty=BASE_APPLICATION fl=LAYOUT_IN_SCREEN',1)
    v['windows']=v['windows'].replace('      Surface: shown=', '      mSurface=Surface(name=fixture)/@0xff\n      Surface: shown=',1)
    # A distinct Surface object per window, not an array-position association.
    at=v['windows'].index('  Window #1')
    v['windows']=v['windows'][:at]+v['windows'][at:].replace('      Surface: shown=', '      mSurface=Surface(name=fixture)/@0xfe\n      Surface: shown=',1)
    v['input']=v['input'].replace('ownerUid=1000,','ownerPid=123, ownerUid=1000, touchableRegion=<empty>, token=0x0,')
    v['input']=v['input'].replace('inputConfig=TRUSTED_OVERLAY,','inputConfig=TRUSTED_OVERLAY,',1)
    # The focused application owns a real nonempty input channel and region.
    v['input']=v['input'].replace('ownerPid=123, ownerUid=1000, touchableRegion=<empty>, token=0x0,','ownerPid=123, ownerUid=1000, touchableRegion=[0,0][100,200], token=0xab,',1)
    import re
    v['input']=''.join(line.replace('displayId=0,','id=%d, displayId=0,' % (100+int(re.match(r'^      (\d+):',line)[1]))) if re.match(r'^      \d+:',line) else line for line in v['input'].splitlines(True))
    return v


def graph(v):
    # Before B2 the strict B1 entry exists; new graph semantics are tested through
    # their distinct non-granting API, without changing B1's legacy assertions.
    return getattr(native,'assess_window_graph',native.assess_native)(v)


class BeforeCompatibilityTests(unittest.TestCase):
    def test_identical_dispatcher_sections_can_agree(self):
        v=graph_scene();v['input']+='\n'+v['input']
        self.assertEqual(graph(v)['classification'],'SAFE_FOR_READONLY_CAPTURE')
    def test_unknown_type_instance_proven_without_surface_or_input(self):
        v=graph_scene();extra=window(2,'a2','fixture.other','2315',True).replace('mHasSurface=true','mHasSurface=false').replace('mViewVisibility=0x0','mViewVisibility=0x8').replace('    isVisible=true','    isVisible=false').replace('      Surface: shown=false\n','').replace('mDisplayId=0 mOwnerUid=1000','mDisplayId=0 mSession=Session{e1 124:fixture} mOwnerUid=1000').replace('ty=2315','ty=2315 fl=NOT_FOCUSABLE NOT_TOUCHABLE')
        v['windows']=v['windows'].replace('  mGlobalConfiguration',extra+'  mGlobalConfiguration')
        self.assertEqual(graph(v)['classification'],'SAFE_FOR_READONLY_CAPTURE')
    def test_legacy_coordinator_is_not_a_life_database(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'coordinator.sqlite3';s=Store(p);s.close()
            try:
                from phonelife.readonly_sources import inspect_coordinator
                result=inspect_coordinator(p)
            except ImportError:
                result=readonly_db_health(p,p,device='real',worker_alive=False)
            self.assertTrue(result.get('role_readable',result.get('database_readable')))


class WindowAssociationTests(unittest.TestCase):
    def deny(self,v):self.assertNotEqual(graph(v)['classification'],'SAFE_FOR_READONLY_CAPTURE')
    def test_duplicate_sections_conflict(self):v=graph_scene();v['input']+='\n'+v['input'].replace('ownerPid=123','ownerPid=999');self.deny(v)
    def test_duplicate_section_truncated(self):v=graph_scene();v['input']+='\nInput Dispatcher State:';self.deny(v)
    def test_three_dispatcher_sections_deny(self):v=graph_scene();v['input']*=3;self.deny(v)
    def test_same_uid_different_window_ids_are_linked(self):r=graph(graph_scene());self.assertEqual(len(r['windows']),2);self.assertEqual(r['windows'][1]['classification'],'NONDISPLAYED_NONINTERACTIVE')
    def test_uid_match_without_window_id_never_links(self):v=graph_scene();v['input']=v['input'].replace('name=a1 ','name=a9 ');self.deny(v)
    def test_duplicate_input_window_name_deny(self):v=graph_scene();v['input']=v['input'].replace('name=a1 ','name=a0 ');self.deny(v)
    def test_same_pid_multiple_distinct_surfaces(self):self.assertEqual(graph(graph_scene())['classification'],'SAFE_FOR_READONLY_CAPTURE')
    def test_surface_shared_between_windows_deny(self):v=graph_scene();v['windows']=v['windows'].replace('/@0xfe','/@0xff');self.deny(v)
    def test_surface_id_missing_deny(self):v=graph_scene();v['windows']=v['windows'].replace('      mSurface=Surface(name=fixture)/@0xff\n','');self.deny(v)
    def test_input_owner_pid_conflict_deny(self):v=graph_scene();v['input']=v['input'].replace('ownerPid=123','ownerPid=124');self.deny(v)
    def test_input_owner_uid_conflict_deny(self):v=graph_scene();v['input']=v['input'].replace('ownerUid=1000','ownerUid=1001');self.deny(v)
    def test_hidden_window_active_channel_deny(self):v=graph_scene();v['input']=v['input'].replace('NO_INPUT_CHANNEL | ','').replace('token=0x0','token=0xac');self.deny(v)
    def test_channel_zero_without_no_channel_flag_deny(self):v=graph_scene();v['input']=v['input'].replace('token=0xab','token=0x0');self.deny(v)
    def test_input_removed_display_still_visible_deny(self):v=graph_scene();v['input']=v['input'].replace('name=a0 ','name=a9 ');self.deny(v)
    def test_unknown_type_visible_even_nontouchable_deny(self):v=graph_scene();v['windows']=v['windows'].replace('ty=BASE_APPLICATION','ty=2314',1);self.deny(v)
    def test_unknown_type_hidden_with_channel_deny(self):v=graph_scene();at=v['windows'].index('  Window #1');v['windows']=v['windows'][:at]+v['windows'][at:].replace('ty=BASE_APPLICATION','ty=2315');self.deny(v)
    def test_unknown_type_not_globally_normalized(self):self.assertRaises(native.NativeMetadataError,native.window_type,'2315')
    def test_unknown_2314_absence_proof_not_type_whitelist(self):
        v=graph_scene();extra=window(2,'a2','fixture.other','2314',True).replace('mHasSurface=true','mHasSurface=false').replace('mViewVisibility=0x0','mViewVisibility=0x4').replace('    isVisible=true','    isVisible=false').replace('      Surface: shown=false\n','').replace('mDisplayId=0 mOwnerUid=1000','mDisplayId=0 mSession=Session{e1 124:fixture} mOwnerUid=1000').replace('ty=2314','ty=2314 fl=NOT_FOCUSABLE');v['windows']=v['windows'].replace('  mGlobalConfiguration',extra+'  mGlobalConfiguration');r=graph(v);self.assertEqual(r['classification'],'SAFE_FOR_READONLY_CAPTURE');self.assertFalse(r['physical_input_allowed']);self.assertRaises(native.NativeMetadataError,native.window_type,'2314')
    def test_unknown_2314_anonymous_owner_channel_not_ignored(self):
        v=graph_scene();extra=window(2,'a2','fixture.other','2314',True).replace('mHasSurface=true','mHasSurface=false').replace('mViewVisibility=0x0','mViewVisibility=0x4').replace('    isVisible=true','    isVisible=false').replace('      Surface: shown=false\n','').replace('mDisplayId=0 mOwnerUid=1000','mDisplayId=0 mSession=Session{e1 124:fixture} mOwnerUid=1000').replace('ty=2314','ty=2314 fl=NOT_FOCUSABLE');v['windows']=v['windows'].replace('  mGlobalConfiguration',extra+'  mGlobalConfiguration')
        channel=input_row(2,'AnonymousProxy','NOT_FOCUSABLE').replace('displayId=0,','id=102, displayId=0,').replace('ownerUid=1000,','ownerPid=124, ownerUid=1000, touchableRegion=[0,0][100,200], token=0xab,');v['input']=v['input'].replace('  mMaximumObscuring',channel+'  mMaximumObscuring');self.assertIn('UNKNOWN_TYPE_INSTANCE_UNPROVEN',graph(v)['reason_codes'])
    def test_real_metadata_pair_keeps_unattributed_layers_blocked(self):
        p=Path(__file__).parent/'fixtures/coloros16-window-admission';a=json.loads((p/'sample-0.json').read_text('utf8'));b=json.loads((p/'sample-1.json').read_text('utf8'));r=native.assess_window_pair(a,b,started_ns=0,ended_ns=1);self.assertEqual(r['classification'],'INSUFFICIENT_WINDOW_EVIDENCE');self.assertIn('UNATTRIBUTED_INPUT_OR_SURFACE_LAYER',r['reason_codes']);self.assertEqual(r['first']['windows'][10]['classification'],'SNAPSHOT_DORMANT_UNKNOWN_TYPE');self.assertEqual(json.loads((p/'PROVENANCE.json').read_text('utf8'))['origin'],'real_device_metadata_deidentified')
    def test_duplicate_numeric_handle_id_blocks(self):v=graph_scene();v['input']=v['input'].replace('id=101,','id=100,');self.assertIn('DUPLICATE_INPUT_HANDLE_ID',graph(v)['reason_codes'])
    def test_pid_missing_deny(self):v=graph_scene();v['windows']=v['windows'].replace('mSession=Session{e0 123:fixture} ','');self.deny(v)
    def test_flags_missing_deny(self):v=graph_scene();v['windows']=v['windows'].replace(' fl=LAYOUT_IN_SCREEN','');self.deny(v)
    def test_surface_shown_input_not_visible_conflict(self):v=graph_scene();v['input']=v['input'].replace('inputConfig=TRUSTED_OVERLAY,','inputConfig=TRUSTED_OVERLAY | NOT_VISIBLE,');self.deny(v)
    def test_anonymous_spy_with_input_channel_blocks(self):
        v=graph_scene();extra=input_row(2,'AnonymousSpy','SPY | TRUSTED_OVERLAY').replace('displayId=0,','id=102, displayId=0,').replace('ownerUid=1000,','ownerPid=123, ownerUid=1000, touchableRegion=[0,0][100,200], token=0xaa,');v['input']=v['input'].replace('  mMaximumObscuring',extra+'  mMaximumObscuring');self.assertIn('UNATTRIBUTED_INPUT_OR_SURFACE_LAYER',graph(v)['reason_codes'])
    def test_multidisplay_blocks(self):v=graph_scene();v['windows']=v['windows'].replace('mDisplayId=0','mDisplayId=1');self.deny(v)
    def test_split_window_blocks(self):v=graph_scene();v['windows']=v['windows'].replace('frame=[0,0][100,200]','frame=[0,0][50,200]',1);self.deny(v)
    def test_malformed_alpha_blocks(self):v=graph_scene();v['windows']=v['windows'].replace('fl=LAYOUT_IN_SCREEN','alpha=1_bad fl=LAYOUT_IN_SCREEN');self.deny(v)
    def test_known_wallpaper_input_flag_recognized(self):v=graph_scene();v['input']=v['input'].replace('NO_INPUT_CHANNEL | NOT_VISIBLE','IS_WALLPAPER | NO_INPUT_CHANNEL | NOT_VISIBLE');self.assertNotIn('UNKNOWN_INPUT_CONFIG',graph(v)['reason_codes'])
    def test_two_samples_stable_ignores_only_raw_header_clock(self):
        v=graph_scene();b=copy.deepcopy(v);b['power']='Time: 999\n'+b['power'];r=native.assess_window_pair(v,b,started_ns=0,ended_ns=1);self.assertEqual(r['classification'],'SAFE_FOR_READONLY_CAPTURE');self.assertFalse(r['atomic_snapshot'])
    def test_two_samples_focus_change_deny(self):v=graph_scene();b=copy.deepcopy(v);b['displays']=b['displays'].replace('Window{a0','Window{a1');self.assertNotEqual(native.assess_window_pair(v,b,started_ns=0,ended_ns=1)['classification'],'SAFE_FOR_READONLY_CAPTURE')
    def test_two_samples_window_substitution_deny(self):v=graph_scene();b=copy.deepcopy(v);b['windows']=b['windows'].replace('/@0xfe','/@0xfd');self.assertIn('METADATA_GRAPH_CHANGED_OR_UNVERIFIED',native.assess_window_pair(v,b,started_ns=0,ended_ns=1)['reason_codes'])
    def test_pair_timeout(self):v=graph_scene();self.assertIn('METADATA_SAMPLE_BOUND_EXCEEDED',native.assess_window_pair(v,v,started_ns=0,ended_ns=8_000_000_001)['reason_codes'])
    def test_pair_monotonic_reversal(self):v=graph_scene();self.assertIn('METADATA_SAMPLE_BOUND_EXCEEDED',native.assess_window_pair(v,v,started_ns=2,ended_ns=1)['reason_codes'])


class DatabaseRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)/'session';self.value=prepare_session(self.directory,seconds=120)
        self.control=self.directory/'soak-control.sqlite3';self.agent=Path(self.value['config']['agent_db']);self.coordinator=Path(self.value['config']['lease_path'])
    def tearDown(self):self.temp.cleanup()
    def read(self,**kw):
        v=dict(expected_session_id=self.value['session_id'],agent_path=self.agent,coordinator_path=self.coordinator);v.update(kw)
        return read_bound_sources(self.control,**v)
    def sql(self,p,command,args=()):
        c=sqlite3.connect(p)
        try:c.execute(command,args);c.commit()
        finally:c.close()
    def unknown(self,p=None):
        s=Store(p or self.agent)
        try:s.start_task('b2-unknown','mock','offline',{},run_kind='mock_test');s.attempt('b2-unknown',0,0,{'execution_status':'outcome_unknown'});s.conn.execute("UPDATE task_runs SET status='needs_review' WHERE task_run_id='b2-unknown'")
        finally:s.close()
    def marker(self):self.control.with_name(self.control.name+'.review-stop.json').write_text(json.dumps({'session_id':self.value['session_id'],'reason_code':REVIEW_STOP,'requires_manual_review':True}),'utf8')
    def test_correct_roles(self):self.assertEqual(self.read()['state'],'PASS')
    def test_legacy_coordinator_no_life_controls_is_valid_role(self):r=inspect_coordinator(self.coordinator);self.assertTrue(r['role_readable']);self.assertFalse(r['life_controls_present'])
    def test_coordinator_cannot_substitute_for_agent(self):self.assertEqual(self.read(agent_path=self.coordinator)['state'],'BLOCKED')
    def test_agent_cannot_substitute_for_control(self):r=read_bound_sources(self.agent,expected_session_id=self.value['session_id'],agent_path=self.control,coordinator_path=self.coordinator);self.assertEqual(r['state'],'BLOCKED')
    def test_true_agent_missing_controls_blocks(self):self.sql(self.agent,'DROP TABLE life_controls');self.assertIn('AGENT_LIFE_SCHEMA_INCOMPLETE',self.read()['reason_codes'])
    def test_true_control_missing_session_blocks(self):self.sql(self.control,'DROP TABLE session');self.assertIn('WRONG_SESSION_CONTROL_ROLE',self.read()['reason_codes'])
    def test_unbound_current_control_blocks(self):r=read_bound_sources(None,expected_session_id=None,agent_path=None,coordinator_path=self.coordinator);self.assertIn('CURRENT_CONTROL_OR_AGENT_SOURCE_UNBOUND',r['reason_codes'])
    def test_agent_schema_newer_blocks(self):self.sql(self.agent,'PRAGMA user_version=99');self.assertEqual(self.read()['state'],'BLOCKED')
    def test_coordinator_schema_newer_blocks(self):self.sql(self.coordinator,'PRAGMA user_version=99');self.assertFalse(inspect_coordinator(self.coordinator)['role_readable'])
    def test_control_schema_newer_blocks(self):self.sql(self.control,'PRAGMA user_version=99');self.assertIn('UNSUPPORTED_CONTROL_SCHEMA',self.read()['reason_codes'])
    def test_life_schema_newer_blocks(self):self.sql(self.agent,'INSERT INTO life_schema_migrations VALUES(99,?)',('offline',));self.assertIn('UNSUPPORTED_LIFE_SCHEMA',self.read()['reason_codes'])
    def test_identity_mismatch(self):self.assertEqual(self.read(expected_session_id='different')['state'],'BLOCKED')
    def test_physical_identity_cannot_borrow_mock_control(self):self.assertIn('PHYSICAL_SAFETY_SOURCE_IDENTITY_MISMATCH',self.read(physical_identity_sha256='a'*64)['reason_codes'])
    def test_session_config_path_substitution(self):
        c=SessionLedger(self.control);v=c.load();v['config']['agent_db']=str(self.directory/'missing.sqlite3');v['fingerprint']=hashlib.sha256(canonical(v['config']).encode()).hexdigest();c.save(v);c.close();self.assertIn('SAFETY_SOURCE_PATH_MISMATCH',self.read()['reason_codes'])
    def test_unreadable_original_audit_blocks(self):self.sql(self.agent,'DROP TABLE action_attempts');self.assertEqual(self.read()['state'],'BLOCKED')
    def test_corrupt_coordinator_blocks(self):self.coordinator.write_bytes(b'broken sqlite');self.assertEqual(self.read()['state'],'BLOCKED')
    def test_corrupt_agent_blocks(self):self.agent.write_bytes(b'broken sqlite');self.assertEqual(self.read()['state'],'BLOCKED')
    def test_missing_database_does_not_create(self):missing=self.directory/'absent.sqlite3';self.assertFalse(inspect_coordinator(missing)['role_readable']);self.assertFalse(missing.exists())
    def test_unsupported_agent_control_value(self):self.sql(self.agent,'INSERT INTO life_controls VALUES(?,?,?,?)',('mock','unknown','offline','offline'));self.assertIn('UNSUPPORTED_AGENT_CONTROL_STATE',self.read()['reason_codes'])
    def test_unknown_agent_dispatch_blocks(self):self.unknown();r=self.read();self.assertEqual(r['state'],'BLOCKED');self.assertTrue(r['requires_manual_review'])
    def test_unknown_legacy_coordinator_audit_not_dropped(self):self.unknown(self.coordinator);r=self.read();self.assertEqual(r['state'],'BLOCKED');self.assertTrue(r['requires_manual_review'])
    def test_marker_only_blocks(self):self.marker();r=self.read();self.assertEqual(r['state'],'BLOCKED');self.assertTrue(r['requires_manual_review'])
    def test_corrupt_marker_blocks(self):self.control.with_name(self.control.name+'.review-stop.json').write_text('{partial','utf8');r=self.read();self.assertEqual(r['state'],'BLOCKED');self.assertTrue(r['requires_manual_review'])
    def test_sqlite_only_blocks(self):
        c=SessionLedger(self.control);v=c.load();v.update(safety_stop={'reason_code':REVIEW_STOP,'requires_manual_review':True},requires_manual_review=True);c.save(v);c.close();self.assertEqual(self.read()['state'],'BLOCKED');self.assertFalse(self.read()['base_resume_allowed'])
    def test_both_stop_media_absent_original_unknown_still_blocks(self):self.unknown();self.assertEqual(self.read()['state'],'BLOCKED')
    def test_unknown_history_survives_separate_process(self):
        self.unknown();code='from phonelife.readonly_sources import read_bound_sources;import json,sys;print(json.dumps(read_bound_sources(sys.argv[1],expected_session_id=sys.argv[2],agent_path=sys.argv[3],coordinator_path=sys.argv[4])))'
        r=subprocess.run([sys.executable,'-c',code,str(self.control),self.value['session_id'],str(self.agent),str(self.coordinator)],capture_output=True,text=True,timeout=10);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['state'],'BLOCKED')
    def test_bounded_lock_timeout(self):
        c=sqlite3.connect(self.control);c.execute('BEGIN EXCLUSIVE');started=time.monotonic()
        try:r=self.read()
        finally:c.rollback();c.close()
        self.assertEqual(r['state'],'BLOCKED');self.assertLess(time.monotonic()-started,2)
    def test_active_shared_lease_blocks(self):
        s=Store(self.coordinator);s.acquire('mock','offline-owner',60);s.close();self.assertEqual(self.read()['state'],'BLOCKED')
    def test_invalid_lease_evidence_is_not_assumed_free(self):self.sql(self.coordinator,'INSERT INTO lease VALUES(?,?,?)',('mock','offline-owner','not-a-number'));self.assertFalse(inspect_coordinator(self.coordinator)['role_readable'])
    def test_orphan_legacy_attempt_cannot_be_silently_dropped(self):self.sql(self.coordinator,'INSERT INTO action_attempts VALUES(?,?,?,?,?)',('orphan','missing-run',0,0,json.dumps({'execution_status':'outcome_unknown'})));self.assertFalse(inspect_coordinator(self.coordinator)['historical_audit_readable'])
    def test_status_read_does_not_change_database_bytes(self):
        before={p:p.read_bytes() for p in (self.control,self.agent,self.coordinator)};r=self.read();self.assertEqual(r['state'],'PASS');self.assertEqual(before,{p:p.read_bytes() for p in before})
    def test_wal_content_preserved(self):
        writer=sqlite3.connect(self.agent);writer.execute('PRAGMA wal_autocheckpoint=0');writer.execute("INSERT INTO memories(device,kind,summary,created_at) VALUES('mock','offline','fixture',1)");writer.commit()
        wal=Path(str(self.agent)+'-wal');self.assertTrue(wal.exists());before={p:p.read_bytes() for p in (self.control,self.agent,self.coordinator,wal)}
        try:self.assertEqual(self.read()['state'],'PASS');self.assertEqual(before,{p:p.read_bytes() for p in before})
        finally:writer.close()
    def test_control_changes_during_health_read_blocks(self):
        real=readonly_db_health
        def changing(*args,**kwargs):
            result=real(*args,**kwargs);ledger=SessionLedger(self.control);v=ledger.load();v['operator']='paused';ledger.save(v);ledger.close();return result
        with patch('phonelife.readonly_sources.readonly_db_health',side_effect=changing):self.assertIn('SAFETY_SOURCES_CHANGED_DURING_READ',self.read()['reason_codes'])
    def test_unknown_audit_rows_unchanged(self):
        self.unknown();c=sqlite3.connect(self.agent);before=list(c.execute('SELECT * FROM action_attempts'));c.close();self.read();c=sqlite3.connect(self.agent)
        try:self.assertEqual(before,list(c.execute('SELECT * FROM action_attempts')))
        finally:c.close()
    def test_emergency_still_highest(self):
        c=SessionLedger(self.control);c.operator('emergency_stopped');c.close();self.marker();r=self.read();self.assertTrue(r['emergency_stopped']);self.assertIn('OPERATOR_STOP',r['reason_codes'])
    def test_status_resume_consistent_on_marker(self):
        self.marker();self.assertFalse(SessionLedger.read_status(self.control)['resume_allowed']);self.assertFalse(self.read()['base_resume_allowed'])
        with self.assertRaises(ValueError):control_session(self.directory,'running')


class FourEntryRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_stop_media_fail_current_worker_stops_original_history_still_blocks(self):
        from test_stop_persistence import OwnedMockWorker
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/'session';value=prepare_session(directory,seconds=30)
            store=Store(directory/'agent.sqlite3');store.start_task('unknown','mock','offline',{},run_kind='mock_test');store.attempt('unknown',0,0,{'execution_status':'outcome_unknown'});store.close()
            c=LongRunSoakController(directory);child=OwnedMockWorker(c.ledger);c.worker=child;c.ledger.conn.execute('PRAGMA query_only=ON');real_open=Path.open;marker=c.ledger.review_path
            def fail(path,*a,**kw):
                if path==marker and a and a[0]=='x':raise PermissionError('offline marker failure')
                return real_open(path,*a,**kw)
            started=time.monotonic()
            try:
                with patch.object(Path,'open',fail),patch('phonelife.longrun.subprocess.Popen') as launch:
                    with self.assertRaises(sqlite3.Error):await c._review_stop(c._health())
                    launch.assert_not_called()
                self.assertIsNotNone(child.poll());self.assertLess(time.monotonic()-started,5)
            finally:c.ledger.conn.execute('PRAGMA query_only=OFF');c.close()
            report=read_bound_sources(directory/'soak-control.sqlite3',expected_session_id=value['session_id'],agent_path=directory/'agent.sqlite3',coordinator_path=value['config']['lease_path'])
            self.assertEqual(report['state'],'BLOCKED');self.assertTrue(report['requires_manual_review'])
    async def test_supervisor_worker_consistent_on_original_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/'session';prepare_session(directory,seconds=30)
            ledger=SessionLedger(directory/'soak-control.sqlite3');ledger.start();ledger.close()
            s=Store(directory/'agent.sqlite3');s.start_task('unknown','mock','offline',{},run_kind='mock_test');s.attempt('unknown',0,0,{'execution_status':'outcome_unknown'});s.close()
            with patch('phonelife.devices.MockPhone') as device,patch('phonelife.longrun_api.SoakAPI') as api:
                await run_worker(directory,os.getpid());device.assert_not_called();api.assert_not_called()
            c=LongRunSoakController(directory)
            try:
                with patch('phonelife.longrun.subprocess.Popen') as worker:
                    r=await c.run();worker.assert_not_called();self.assertEqual(r['state'],'failed')
            finally:c.close()
            self.assertFalse(SessionLedger.read_status(directory/'soak-control.sqlite3')['worker_launch_allowed'])


class PhysicalSourceBindingTests(unittest.TestCase):
    def test_explicit_physical_roles_can_be_verified_without_dpapi_or_adb(self):
        from test_physical_core import PhysicalFixture
        with tempfile.TemporaryDirectory() as d:
            f=PhysicalFixture(d)
            try:
                for key in ['real','autonomous-api-writer','subject:'+f.binding.life_subject_id]:f.leases.release(key,'offline-owner')
                with patch('phonelife.physical_execution.unprotect_approval') as grant,patch('phonelife.physical_execution.physical_read_check') as adb:
                    r=read_bound_sources(f.ledger.path,expected_session_id=f.state['session_id'],agent_path=f.directory/'agent.sqlite3',coordinator_path=f.state['config']['lease_path'],physical_identity_sha256=f.profile.identity_sha256)
                    grant.assert_not_called();adb.assert_not_called();self.assertEqual(r['state'],'PASS');self.assertFalse(r['physical_input_allowed'])
            finally:f.close()
    def test_copy_of_control_cannot_evade_original_marker_path(self):
        from test_physical_core import PhysicalFixture
        with tempfile.TemporaryDirectory() as d:
            f=PhysicalFixture(d)
            try:
                for key in ['real','autonomous-api-writer','subject:'+f.binding.life_subject_id]:f.leases.release(key,'offline-owner')
                f.ledger.review_path.write_text(json.dumps({'session_id':f.state['session_id'],'reason_code':REVIEW_STOP,'requires_manual_review':True}),'utf8')
                clone=f.directory/'clone.sqlite3';clone.write_bytes(f.ledger.path.read_bytes())
                r=read_bound_sources(clone,expected_session_id=f.state['session_id'],agent_path=f.directory/'agent.sqlite3',coordinator_path=f.state['config']['lease_path'],physical_identity_sha256=f.profile.identity_sha256)
                self.assertIn('PHYSICAL_CONTROL_PATH_MISMATCH',r['reason_codes'])
            finally:f.close()


class ObservationReviewTests(unittest.TestCase):
    def check(self,**changes):
        from test_device_profiles import IDENTITY,SERIAL
        from phonelife.device_profiles import PhysicalDeviceProfile
        args=dict(profile=PhysicalDeviceProfile(SERIAL,IDENTITY,True),identity_before=IDENTITY,identity_after=IDENTITY,first=graph_scene(),second=graph_scene(),sources=dict(state='PASS',source_roles_bound=True,safety_evidence_readable=True,requires_manual_review=False,emergency_stopped=False,device_lease_free=True,base_safety_admission_allowed=True),connected_before=True,connected_after=True,started_ns=0,ended_ns=1);args.update(changes);return observation_admission(**args)
    def test_review_is_not_raw_capture_or_execution_authority(self):r=self.check();self.assertEqual(r['state'],'READY_FOR_C0_CAPTURE_REVIEW');self.assertFalse(r['raw_capture_authorized']);self.assertFalse(r['physical_input_allowed']);self.assertFalse(r['worker_launch_allowed'])
    def test_unbound_roles_never_ready(self):self.assertEqual(self.check(sources={})['state'],'BLOCKED')
    def test_identity_changed_blocks(self):self.assertEqual(self.check(identity_after={})['state'],'BLOCKED')
    def test_usb_disconnect_blocks(self):self.assertEqual(self.check(connected_after=False)['state'],'BLOCKED')
    def test_paused_or_fault_source_not_promoted(self):self.assertEqual(self.check(sources={'state':'BLOCKED','reason_codes':['UNKNOWN_DISPATCH_NEEDS_REVIEW']})['state'],'BLOCKED')
    def test_nonsettings_foreground_blocks(self):v=graph_scene();v['windows']=v['windows'].replace('com.android.settings','fixture.other');self.assertEqual(self.check(first=v,second=v)['state'],'BLOCKED')
