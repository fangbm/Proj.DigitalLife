"""offline_fixture: run actual helper lifecycle on host JVM, no Android/ADB/API."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from acceptance.native_target_observer import validate_diagnostic_result
from test_native_observer_prototype import packet

ROOT=Path(__file__).resolve().parents[1]
JDK=Path(r'C:\Program Files\Microsoft\jdk-17.0.5.8-hotspot\bin')
CASES=('success environment_unavailable automation_null constructor_unavailable connect_failure '
       'connect_security wrapped_security configure_null window_missing window_ambiguous root_null '
       'target_missing target_ambiguous target_mismatch target_security target_exception '
       'visible_unavailable native_getter_unavailable attribute_exception attribute_security second_sample_failure '
       'release_failure cleanup_failure body_and_cleanup_failure timeout elapsed_timeout '
       'unknown_exception interrupted watchdog_unknown_cleanup serialization_failure').split()


class NativeObserverStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='digitallife-offline-stages-')
        cls.addClassCleanup(cls.tmp.cleanup)
        result=subprocess.run([str(JDK/'javac.exe'),'-encoding','UTF-8','-d',cls.tmp.name,
            str(ROOT/'android/native-target-observer/src/org/digitallife/ObserverStages.java'),
            str(ROOT/'tests/native_observer/ObserverStagesTest.java')],capture_output=True,timeout=30)
        if result.returncode:raise AssertionError(result.stderr.decode('utf8',errors='replace'))

    def run_case(self,name):
        result=subprocess.run([str(JDK/'java.exe'),'-cp',self.tmp.name,'org.digitallife.ObserverStagesTest',name],
            capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf8',errors='replace'))
        self.assertEqual(result.stderr,b'')
        wire=result.stdout.decode('utf8').strip()
        if name=='success':
            self.assertEqual(wire,'SUCCESS_WITH_TWO_NATIVE_FIXTURE_FRAMES');return
        packet=json.loads(wire)
        self.assertEqual(set(packet),{'schema','status','code','stage','last_completed_stage','exception_type','duration_ns','cleanup'})
        self.assertEqual(packet['schema'],'find-x6-observer-stage/2')
        self.assertIn(packet['status'],('FAILED','PARTIAL'))
        self.assertGreaterEqual(packet['duration_ns'],0)
        self.assertNotIn('packet',packet)
        result=validate_diagnostic_result(wire.encode(),nonce='a'*32,host_started_ns=10,host_received_ns=50,now_ns=60)
        self.assertFalse(result['native_target_sample_consistent'])
        self.assertFalse(result['execution_allowed'])
        for forbidden in ('example.invalid','hierarchy','sample_started','sample_finished','stack','/private/'):
            self.assertNotIn(forbidden,wire)


def _case(name):
    def run(self):self.run_case(name)
    return run

for _name in CASES:
    setattr(NativeObserverStageTests,'test_'+_name,_case(_name))


class DiagnosticEnvelopeTests(unittest.TestCase):
    def success(self):
        return {'schema':'find-x6-observer-stage/2','status':'SUCCESS','stage':'CLEANUP','duration_ns':30,
                'cleanup':{'status':'OK','exception_type':'NONE'},'packet':packet()}
    def validate(self,p):
        return validate_diagnostic_result(json.dumps(p).encode(),nonce='a'*32,
            host_started_ns=10,host_received_ns=50,now_ns=60)
    def test_success_uses_existing_full_guard_and_grants_no_input(self):
        r=self.validate(self.success())
        self.assertTrue(r['native_target_sample_consistent']);self.assertFalse(r['execution_allowed'])
    def test_unknown_native_visible_never_becomes_false_or_true(self):
        p=self.success();p['packet']['frames'][0]['row']['visible']=None
        with self.assertRaises(ValueError):self.validate(p)
    def test_cleanup_failure_cannot_use_success_attributes(self):
        p=self.success();p['cleanup']['status']='FAILED'
        with self.assertRaises(ValueError):self.validate(p)
    def test_failure_cannot_carry_attributes(self):
        p=self.success();p['status']='FAILED'
        with self.assertRaises(ValueError):self.validate(p)
    def test_partial_cannot_promote_first_sample_to_success(self):
        p=self.success();p['status']='PARTIAL'
        with self.assertRaises(ValueError):self.validate(p)
    def test_success_duration_cannot_replace_observation_freshness(self):
        p=self.success();p['packet']['frames'][1]['sample_finished_ns']=1_000_000
        with self.assertRaises(ValueError):self.validate(p)
    def test_duration_must_cover_samples_and_fit_host_bracket(self):
        for value in (True,-1,10,41):
            p=self.success();p['duration_ns']=value
            with self.assertRaises(ValueError):self.validate(p)
    def test_arbitrary_message_and_duplicate_keys_rejected(self):
        p=self.success();p['message']='private'
        with self.assertRaises(ValueError):self.validate(p)
        raw=json.dumps(self.success()).replace('"status": "SUCCESS"','"status": "FAILED", "status": "SUCCESS"').encode()
        with self.assertRaises(ValueError):validate_diagnostic_result(raw,nonce='a'*32,host_started_ns=10,host_received_ns=50,now_ns=60)
