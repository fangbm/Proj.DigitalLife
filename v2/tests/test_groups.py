from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from digitallife_v2.groups import verify_qq,QQ
from digitallife_v2.safety import Blocked
from test_m1 import observation

class QQGroupTests(unittest.IsolatedAsyncioTestCase):
 def page(self,oid,values):
  o=observation(oid,foreground=QQ+'/.Chat')
  o.controls=[replace(o.controls[0],control_id=oid+':'+str(n),package=QQ,resource=QQ+':id/'+res,text=text) for n,(res,text) in enumerate(values)]
  return o
 def setup_ui(self):
  chat=self.page('chat',[('3o2','(3)')]);info=self.page('info',[('rpd','123456789'),('label','群成员')]);profile=self.page('profile',[('gmx','QQ: 23456789'),('joined','2026.10.27加入该群')])
  def one(obs,resource):return next(c for c in obs.controls if c.resource==resource)
  ui=SimpleNamespace(one=one,account_display_name='Test Self',sources=['real-scene-source-fixture'],loop=SimpleNamespace(run_kind='fixture'),tap=AsyncMock(side_effect=[info,profile]),act=AsyncMock(side_effect=[info,chat]),chat_hint=lambda o:'Test Group')
  return ui,chat,info,profile
 async def test_group_number_and_actual_self_member_profile_form_group_binding(self):
  ui,chat,_,_=self.setup_ui();binding,_=await verify_qq(ui,chat,'23456789','Test Group')
  self.assertEqual(binding.kind,'group');self.assertEqual(binding.peer,'123456789');self.assertTrue(binding.existing_contact)
  self.assertEqual(ui.group_own_name,'Test Self')
 async def test_matching_display_name_without_matching_account_number_is_rejected(self):
  ui,chat,_,_=self.setup_ui()
  with self.assertRaisesRegex(Blocked,'MEMBER_UNVERIFIED'):await verify_qq(ui,chat,'99999999','Test Group')
  ui.act.assert_not_awaited()
 async def test_group_change_after_member_inspection_is_rejected(self):
  ui,chat,info,_=self.setup_ui();changed=self.page('changed',[('rpd','99999999')]);ui.act=AsyncMock(side_effect=[changed,chat])
  with self.assertRaisesRegex(Blocked,'GROUP_CHANGED'):await verify_qq(ui,chat,'23456789','Test Group')
 async def test_dm_cannot_be_relabelled_as_a_group(self):
  ui,chat,_,_=self.setup_ui();chat.controls=[]
  with self.assertRaisesRegex(Blocked,'GROUP_CHAT_ROLE'):await verify_qq(ui,chat,'23456789','Test Group')
  ui.tap.assert_not_awaited()


class WeChatGroupTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  import tempfile,hashlib
  from pathlib import Path
  from PIL import Image
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  png=Path(self.temp.name)/'info.png';Image.new('RGB',(720,1600),'white').save(png)
  def page(oid,values):
   o=observation(oid,foreground='com.tencent.mm/com.tencent.mm.chatroom.ui.ChatroomInfoUI')
   o.png_path=str(png);o.png_sha256=hashlib.sha256(png.read_bytes()).hexdigest()
   o.controls=[replace(o.controls[0],control_id=oid+':'+str(n),package='com.tencent.mm',text=text,bounds=box) for n,(text,box) in enumerate(values)]
   return o
  self.info=page('info',[('聊天信息（3）',(250,100,450,150)),('Group Alias',(50,300,200,350)),('群聊名称',(20,800,200,850)),('Test Group',(500,800,700,850))])
  self.bottom=page('bottom',[('我在群里的昵称',(20,900,300,950)),('Group Alias',(500,900,700,950)),('退出群聊',(250,1200,450,1250))])
  self.ui=SimpleNamespace(loop=SimpleNamespace(run_kind='fixture'),sources=['fixture-source'],act=AsyncMock(return_value=observation('chat')),chat_hint=lambda o:'Test Group(3)')
 async def test_current_account_personal_group_settings_and_roster_are_required(self):
  from digitallife_v2.groups import verify_wechat
  from unittest.mock import patch
  with patch('digitallife_v2.groups.scroll_info',new=AsyncMock(return_value=self.bottom)):
   binding,_=await verify_wechat(self.ui,self.info,'account123@fingerprint','Test Group(3)')
  self.assertEqual(binding.kind,'group');self.assertTrue(binding.peer.startswith('wxgroup:'))
  self.assertEqual(self.ui.group_own_name,'Group Alias')
 async def test_group_name_alone_is_insufficient_without_personal_membership_controls(self):
  from digitallife_v2.groups import verify_wechat
  from unittest.mock import patch
  self.bottom.controls=[c for c in self.bottom.controls if c.text!='退出群聊']
  with patch('digitallife_v2.groups.scroll_info',new=AsyncMock(return_value=self.bottom)):
   with self.assertRaisesRegex(Blocked,'NICKNAME_UNVERIFIED'):
    await verify_wechat(self.ui,self.info,'account123@fingerprint','Test Group(3)')
  self.ui.act.assert_not_awaited()
