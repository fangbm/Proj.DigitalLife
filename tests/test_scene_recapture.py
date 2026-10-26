"""offline_fixture: transient capture races, never a real phone/API dependency."""
import unittest
import json
import asyncio
from dataclasses import replace
import test_app_exploration as fx
from phonelife.storage import Store
from phonelife.policy import PolicyDenied

class SceneRecaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h=fx.AppsTests();self.h.setUp();self.scenes=0
        def cleanup():
            while self.h._cleanups:
                function,args,kwargs=self.h._cleanups.pop();function(*args,**kwargs)
        self.addCleanup(cleanup)
    def change_scene(self,change):
        original=self.h.phone
        async def phone():
            p=await original();scene=p._scene
            async def read():
                value=await scene();self.scenes+=1
                return change(self.scenes,value)
            p._scene=read;return p
        self.h.phone=phone
    def events(self):
        s=Store.open_readonly(self.h.f.life)
        try:return [json.loads(r[0])for r in s.conn.execute("select payload from events where event_type='observation_scene_changed' order by id")]
        finally:s.close()
    async def finish(self):
        return await self.h.execute(lambda n,s:{'type':'finish','reason':'offline current fresh observation','outcome':'uncertain'})
    async def test_transient_xml_change_recollects_before_any_gui_proposal(self):
        self.change_scene(lambda n,v:{**v,'foreground':fx.SECOND+'/.Main'}if n==2 else v)
        result=await self.finish()
        self.assertEqual(self.h.used_gui.calls,1,'A transient capture race must not bypass the GUI for the entire ten-minute session')
        self.assertEqual(result.reason_code,'EXECUTOR_UNCERTAIN');self.assertEqual(self.h.adb.inputs,[])
        audit=self.events();self.assertEqual(len(audit),1);self.assertTrue(audit[0]['will_retry'])
        self.assertEqual(audit[0]['phase'],'xml');self.assertEqual(audit[0]['changed_fields'],['foreground'])
        # Rejected capture + fresh capture + existing finish observation all count.
        self.assertEqual(self.h.used_life.capability_usage['observe_screen'],3)
        self.assertEqual(sum(args==('exec-out','uiautomator','dump','/proc/self/fd/1')for _,args in self.h.adb.calls),3)
    async def test_transient_png_change_recollects_complete_pair(self):
        self.change_scene(lambda n,v:{**v,'orientation':1}if n==3 else v)
        result=await self.finish()
        self.assertEqual(self.h.used_gui.calls,1);self.assertEqual(result.reason_code,'EXECUTOR_UNCERTAIN')
        self.assertEqual(self.events()[0]['phase'],'png');self.assertEqual(self.events()[0]['changed_fields'],['orientation'])
        self.assertEqual(self.h.adb.inputs,[])
    async def test_persistent_change_is_bounded_and_does_not_call_gui(self):
        self.change_scene(lambda n,v:{**v,'foreground':fx.SECOND+'/.Main'}if n%2==0 else v)
        result=await self.finish()
        self.assertEqual(result.status,'blocked');self.assertEqual(result.reason_code,'OBSERVATION_SCENE_CHANGED')
        self.assertEqual(self.h.used_gui.calls,0);self.assertEqual(self.h.adb.inputs,[])
        self.assertEqual(self.scenes,6) # Two capture attempts, one existing final-audit attempt.
        self.assertEqual([e['will_retry']for e in self.events()],[True,False,False])
        s=Store.open_readonly(self.h.f.lease)
        try:self.assertFalse(s.conn.execute('select 1 from lease').fetchone())
        finally:s.close()
    async def test_other_policy_denials_are_not_recapture_candidates(self):
        def denied(n,v):raise PolicyDenied('SCREEN_LOCK_OR_POWER_UNSAFE')
        self.change_scene(denied);result=await self.finish()
        self.assertEqual(result.status,'blocked');self.assertEqual(result.reason_code,'POLICY_DENIED')
        self.assertEqual(self.scenes,2);self.assertEqual(self.events(),[]);self.assertEqual(self.h.used_gui.calls,0)
    async def test_strict_scope_never_uses_all_app_recapture(self):
        self.change_scene(lambda n,v:{**v,'orientation':1}if n%2==0 else v)
        original=self.h.phone
        async def phone():
            p=await original();p.app_scope=None;return p
        self.h.phone=phone
        result=await self.finish()
        self.assertEqual(result.status,'blocked');self.assertEqual(self.scenes,4)
        self.assertEqual(self.events(),[]);self.assertEqual(self.h.used_gui.calls,0)
    async def test_recapture_cannot_reset_observation_capability_budget(self):
        self.change_scene(lambda n,v:{**v,'orientation':1}if n==2 else v)
        original=self.h.phone
        async def phone():
            p=await original()
            for c in ('observe_screen','inspect_controls'):p.capabilities.entries[c]=replace(p.capabilities.entries[c],maximum_usage=1)
            return p
        self.h.phone=phone;result=await self.finish()
        self.assertEqual(result.status,'blocked');self.assertEqual(self.scenes,2)
        self.assertEqual(self.h.used_life.capability_usage['observe_screen'],1)
        self.assertEqual([e['will_retry']for e in self.events()],[False]);self.assertEqual(self.h.used_gui.calls,0)
    async def test_unknown_dispatch_is_not_replayed_or_recaptured(self):
        result=await self.h.execute(lambda n,s:self.h.candidate(s,package_name=fx.SECOND),fail=True)
        self.assertEqual(result.reason_code,'ADB_OUTCOME_UNKNOWN');self.assertEqual(len(self.h.adb.inputs),1)
        self.assertTrue(self.h.used_phone._unknown);self.assertEqual(self.events(),[])
    async def test_cancellation_during_recapture_releases_leases(self):
        waiting=asyncio.Event();original=self.h.phone
        async def phone():
            p=await original();scene=p._scene
            async def read():
                value=await scene();self.scenes+=1
                if self.scenes==2:return {**value,'foreground':fx.SECOND+'/.Main'}
                if self.scenes==3:
                    waiting.set();await asyncio.Event().wait()
                return value
            p._scene=read;return p
        self.h.phone=phone;task=asyncio.create_task(self.finish())
        try:
            await asyncio.wait_for(waiting.wait(),10);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        finally:
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(self.h.used_gui.calls,0);self.assertEqual(self.h.adb.inputs,[])
        s=Store.open_readonly(self.h.f.lease)
        try:self.assertFalse(s.conn.execute('select 1 from lease').fetchone())
        finally:s.close()
