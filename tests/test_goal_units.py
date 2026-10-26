"""offline_fixture: measured storage units do not expand verified goal scope."""
import unittest
from types import SimpleNamespace
from phonelife.goal_contracts import consistent
from phonelife.verification_registry import GoalVerifierRegistry
from phonelife.models import ScreenObservation
from phonelife.devices import mock_png

SPEC={'kind':'readonly_fact','facts':['storage_free_kb']}

class GoalUnitTests(unittest.TestCase):
    def test_observed_storage_kb_description_is_aligned(self):
        self.assertTrue(consistent('Read free storage in KB',SPEC)[0])
    def test_bounded_unit_variants_are_specific_to_storage(self):
        for description in ('Read available storage in KB','Read storage free in kB','Read free storage (KB)','读取可用存储（KB）','读取剩余存储(KB)'):
            with self.subTest(description=description):self.assertTrue(consistent(description,SPEC)[0])
    def test_other_units_and_other_facts_do_not_borrow_storage_unit(self):
        for description in ('Read free storage in MB','Read free storage in GB','Read free storage in KiB'):
            with self.subTest(description=description):self.assertFalse(consistent(description,SPEC)[0])
        self.assertFalse(consistent('Read battery level in KB',{'kind':'readonly_fact','facts':['battery_percent']})[0])
    def test_extra_claims_remain_unverified(self):
        for suffix in (' and delete important files',' and complete device security check',' and battery level',' and browse ten articles'):
            with self.subTest(suffix=suffix):self.assertFalse(consistent('Read free storage in KB'+suffix,SPEC)[0])

class IndependentUnitVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_units_still_require_independent_fact_and_exact_goal(self):
        reads=[]
        class Phone:
            async def read_fact(self,key):reads.append(key);return '27496972'
        obs=ScreenObservation.from_png(mock_png(32,32),'offline')
        candidate=SimpleNamespace(description='Read free storage in KB',verification=SPEC)
        verifier=GoalVerifierRegistry(Phone()).bind(candidate,True)
        result=await verifier.verify(candidate.description,obs,[])
        self.assertEqual(result.verdict,'verified');self.assertEqual(reads,['storage_free_kb'])
        self.assertEqual(result.evidence,['measured_storage_free_kb=27496972']);self.assertEqual(result.observation_sha256,obs.sha256)
        result=await verifier.verify(candidate.description+' and browse',obs,[])
        self.assertEqual(result.verdict,'uncertain');self.assertEqual(result.evidence_type,'goal_contract_mismatch')
    async def test_unit_alignment_cannot_substitute_for_failed_read(self):
        class Phone:
            async def read_fact(self,key):raise OSError('offline unavailable')
        candidate=SimpleNamespace(description='Read free storage in KB',verification=SPEC)
        result=await GoalVerifierRegistry(Phone()).bind(candidate,True).verify(candidate.description,ScreenObservation.from_png(mock_png(32,32),'offline'),[])
        self.assertEqual(result.verdict,'uncertain');self.assertEqual(result.evidence,[])
