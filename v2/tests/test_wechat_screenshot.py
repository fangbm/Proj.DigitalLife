from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from PIL import Image,ImageDraw
from digitallife_v2.safety import Blocked
from digitallife_v2.wechat_screenshot import Hit,WechatScreenshotUI,ink_hash,literal_keys


class ScreenshotWechatTests(unittest.TestCase):
    def test_changed_control_pixels_are_rejected_before_input(self):
        before=Image.new('RGB',(200,100),'white');after=before.copy()
        ImageDraw.Draw(after).rectangle((20,20,30,30),fill='black')
        window=SimpleNamespace(click_input=Mock())
        owner=SimpleNamespace(check_stop=lambda:None,capture=lambda w:after)
        with self.assertRaisesRegex(Blocked,'CONTROL_CHANGED'):Hit(owner,window,before,(10,10,50,50)).click_input()
        window.click_input.assert_not_called()

    def test_click_uses_window_origin_without_client_frame_offset(self):
        image=Image.new('RGB',(200,100),'white')
        window=SimpleNamespace(rectangle=lambda:SimpleNamespace(left=300,top=400),click_input=Mock())
        owner=SimpleNamespace(check_stop=lambda:None,capture=lambda w:image)
        Hit(owner,window,image,(10,20,50,60)).click_input()
        window.click_input.assert_called_once_with(button='left',coords=(330,440),absolute=True)

    def test_header_background_changes_preserve_glyph_but_changed_name_does_not(self):
        image=Image.new('RGB',(100,40),(250,250,250));ImageDraw.Draw(image).rectangle((10,10,20,25),fill='black')
        dim=Image.new('RGB',(100,40),(210,210,210));ImageDraw.Draw(dim).rectangle((10,10,20,25),fill='black')
        self.assertEqual(ink_hash(image,(0,0,100,40)),ink_hash(dim,(0,0,100,40)))
        ImageDraw.Draw(dim).rectangle((30,10,40,25),fill='black')
        self.assertNotEqual(ink_hash(image,(0,0,100,40)),ink_hash(dim,(0,0,100,40)))

    def make_ui(self):
        ui=WechatScreenshotUI.__new__(WechatScreenshotUI)
        ui.account='fixture';ui.title='测试对象';ui.require_target=lambda:None
        image=Image.new('RGB',(1000,700),(250,250,250))
        ImageDraw.Draw(image).rectangle((400,180,500,240),fill='white')
        value={'desktop_bubbles':[{'kind':'incoming_neutral','bounds':[400,180,500,240]}],
               'lines':[{'text':'3"','bounds':[445,195,470,220],'confidence':.99}]}
        ui.read=lambda:(image,value)
        return ui,image,value

    def test_native_transcript_does_not_create_a_new_message_identity(self):
        ui,image,value=self.make_ui();before=ui.rows()[0]
        self.assertEqual(before['kind'],'voice');self.assertEqual(before['direction'],'incoming')
        value['desktop_bubbles'].append({'kind':'transcript_gray','bounds':[400,250,680,310]})
        value['lines'].append({'text':'今天散步怎么样？','bounds':[415,260,660,290],'confidence':.99})
        after=ui.rows()
        self.assertEqual(len(after),1);self.assertEqual(after[0]['message_key'],before['message_key'])
        self.assertEqual(after[0]['text'],'今天散步怎么样？')

    def test_outgoing_voice_is_never_an_incoming_transcript(self):
        ui,image,value=self.make_ui();value['desktop_bubbles'][0]['kind']='outgoing_green'
        self.assertEqual(ui.inspect()['outcome'],'no_unanswered_incoming')

    def test_voice_wave_ocr_artifact_does_not_become_a_text_message(self):
        for artifact in ('6" (',') 6"','))6"'):
            with self.subTest(artifact=artifact):
                ui,image,value=self.make_ui();value['lines'][0]['text']=artifact
                self.assertEqual(ui.rows()[0]['kind'],'voice');self.assertEqual(ui.rows()[0]['seconds'],6)

    def test_unknown_empty_component_cannot_become_a_voice(self):
        ui,image,value=self.make_ui();value['lines']=[]
        self.assertEqual(ui.inspect()['outcome'],'no_unanswered_incoming')

    def test_gray_received_voice_is_not_discarded_as_a_transcript(self):
        ui,image,value=self.make_ui()
        value['desktop_bubbles'][0]['kind']='transcript_gray'
        value['lines'][0]['text']=') 5"'
        row=ui.rows()[0]
        self.assertEqual((row['kind'],row['direction'],row['seconds']),('voice','incoming',5))
        value['desktop_bubbles'].append({'kind':'transcript_gray','bounds':[400,250,680,310]})
        value['lines'].append({'text':'请用语音回复','bounds':[415,260,660,290],'confidence':.99})
        self.assertEqual(ui.rows()[0]['text'],'请用语音回复')

    def test_right_side_gray_duration_cannot_become_incoming(self):
        ui,image,value=self.make_ui()
        value['desktop_bubbles'][0]={'kind':'transcript_gray','bounds':[800,180,900,240]}
        value['lines'][0]['bounds']=[835,195,870,220]
        self.assertEqual(ui.rows(),[])

    def test_model_text_control_sequences_remain_literal_characters(self):
        text='不要执行{ENTER}，1+1=2 (测试) 50% ^ ~'
        escaped=literal_keys(text)
        self.assertIn('{{}ENTER{}}',escaped);self.assertIn('{+}',escaped)
        self.assertIn('{%}',escaped);self.assertIn('{^}',escaped);self.assertIn('{~}',escaped)

    def test_stop_does_not_prevent_cancelling_only_owned_recording(self):
        ui,image,value=self.make_ui();ui.recording_owned=True;ui.stop=threading.Event();ui.stop.set()
        ui.header=((0,0,100,40),ink_hash(image,(0,0,100,40)))
        ui.capture=lambda:image;ui.record_buttons={};ui.window=SimpleNamespace(type_keys=Mock())
        self.assertTrue(ui.cancel_if_self_recording());ui.window.type_keys.assert_called_once_with('{ESC}')
        ui.recording_owned=False;self.assertFalse(ui.cancel_if_self_recording())
