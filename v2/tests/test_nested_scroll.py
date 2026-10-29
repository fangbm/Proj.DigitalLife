import tempfile,unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import AsyncMock,Mock
from test_m1 import config,observation
from digitallife_v2.safety import guard,Blocked
from digitallife_v2.phone import PhoneController


class NativeNestedScrollTests(unittest.IsolatedAsyncioTestCase):
    def frame(self,top=1868,scrollable=True):
        obs=observation('nested',foreground='tv.danmaku.bili/.AuthorSpaceActivity')
        obs.width=1240;obs.height=2772
        row=replace(obs.controls[0],package='tv.danmaku.bili',resource='tv.danmaku.bili:id/recycler',kind='androidx.recyclerview.widget.RecyclerView',
            bounds=(0,top,1240,2772),scrollable=scrollable,enabled=True,visible=True)
        obs.controls=[row];return obs,row,{'type':'swipe','control_id':row.control_id,'direction':'up'}
    async def test_clipped_lower_native_list_generates_a_gesture_inside_its_visible_viewport(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg=config(Path(folder));cfg.full_access=True;obs,row,action=self.frame()
            self.assertIs(guard(action,obs,{},full_access=True),row)
            phone=PhoneController(cfg,Path(folder));phone.identity=AsyncMock();phone.scene=AsyncMock(return_value=obs.foreground);phone.check_windows=Mock();phone.rotation=0
            command=await phone.prepare(action,row,obs)
            self.assertEqual(command[:3],('shell','input','swipe'))
            x,start,x2,end=map(int,command[3:7])
            self.assertEqual(x,x2);self.assertTrue(row.bounds[0]<x<row.bounds[2])
            self.assertTrue(row.bounds[1]<end<start<int(obs.height*.82))
    async def test_restricted_mode_retains_its_existing_minimum(self):
        obs,_,action=self.frame()
        with self.assertRaisesRegex(Blocked,'SCROLL_REGION_TOO_SMALL'):guard(action,obs,{},full_access=False)
    async def test_full_mode_still_rejects_a_tiny_or_non_scrollable_visible_region(self):
        for top,scrollable in [(2150,True),(1868,False)]:
            obs,_,action=self.frame(top,scrollable)
            with self.assertRaises(Blocked):guard(action,obs,{},full_access=True)
