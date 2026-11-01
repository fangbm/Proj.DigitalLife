"""Public Windows UIA adapter. No private databases, audio APIs or login input."""
from contextlib import suppress
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time

from .safety import Blocked
from .timed_voice import VoiceIntent,send_timed_voice
from .windows_audio import PROGRAM,VirtualAudio

VOICE = re.compile(r'^语音\s*(\d+)\s*["″]?\s*秒(.*)$',re.S)


def normalized_title(text):return re.sub(r'[（(]\d+人?[）)]$','',text).strip()


def direction_from_image(image,rect,window):
    """A visible voice needs positive left-side glyph or outgoing-green evidence."""
    left,top,right,bottom=rect;wl,wt,wr,wb=window
    sx=image.width/(wr-wl);sy=image.height/(wb-wt)
    l=max(0,round((left-wl)*sx));r=min(image.width,round((right-wl)*sx))
    t=max(0,round((top-wt)*sy));b=min(image.height,round((bottom-wt)*sy))
    if r-l<150 or b-t<15:return 'unknown'
    green=dark_left=dark_right=0
    for y in range(t+3,b-3,3):
        for x in range(l+round((r-l)*.09),r-round((r-l)*.09),3):
            red,g,blue=image.getpixel((x,y))[:3]
            if x>l+(r-l)*.5 and 60<red<210 and 165<g<255 and 60<blue<220 and g-red>25 and g-blue>15:green+=1
            if max(red,g,blue)<140:
                if x<l+(r-l)*.42:dark_left+=1
                if x>l+(r-l)*.58:dark_right+=1
    if green>=12:return 'outgoing'
    if dark_left>=4 and dark_right<max(3,dark_left*.15):return 'incoming'
    return 'unknown'


class WechatDesktopUI:
    def __init__(self,account,title,stop=None):
        import psutil
        from pywinauto import Desktop
        self.desktop=Desktop(backend='uia');self.stop=stop or threading.Event()
        self.account=account;self.title=normalized_title(title);self.authorized=False
        self.recording_owned=False
        candidates=[]
        for window in self.desktop.windows(title='微信'):
            if Path(psutil.Process(window.element_info.process_id).exe()).resolve()!=Path(PROGRAM).resolve():continue
            if any(c.element_info.automation_id=='MainView' for c in window.descendants()):candidates.append(window)
        if len(candidates)!=1:raise Blocked('WECHAT_MAIN_UI_ACCESSIBILITY_UNAVAILABLE')
        self.window=candidates[0];self.window.set_focus()

    def check_stop(self):
        if self.stop.is_set():raise Blocked('DESKTOP_JOB_CANCELLED')

    def exact(self,name,kind='Button',root=None,optional=False):
        root=root or self.window
        found=[c for c in root.descendants(control_type=kind) if c.element_info.name==name and c.is_visible()]
        if optional and not found:return None
        if len(found)>1:
            identified=[c for c in found if c.element_info.automation_id]
            if len(identified)==1:found=identified
            else:found=list({tuple(c.rectangle()):c for c in found}.values())
        if len(found)!=1:raise Blocked('WECHAT_UI_CONTROL_UNAVAILABLE:'+name)
        return found[0]

    def verify_account(self):
        import psutil
        pin=getattr(self,'account_pin_path',None)
        identity={'account':self.account,'pid':self.window.element_info.process_id,'hwnd':self.window.handle,
                  'created':psutil.Process(self.window.element_info.process_id).create_time()}
        if pin:
            try:
                if json.loads(pin.read_text(encoding='utf8'))==identity:self.authorized=True;return
            except (OSError,ValueError):pass
        self.check_stop()
        menu=self.exact('设置','MenuItem',optional=True)
        if menu is None:
            self.exact('更多').click_input();menu=self.exact('设置','MenuItem')
        menu.click_input()
        settings=[w for w in self.desktop.windows(title='设置') if w.element_info.process_id==self.window.element_info.process_id]
        if len(settings)!=1:raise Blocked('WECHAT_ACCOUNT_SETTINGS_UNAVAILABLE')
        settings=settings[0]
        try:
            labels=[c for c in settings.descendants() if c.element_info.name=='账号与存储' and c.is_visible()]
            labels=list({tuple(c.rectangle()):c for c in labels}.values())
            if len(labels)!=1:raise Blocked('WECHAT_ACCOUNT_TAB_UNAVAILABLE')
            labels[0].click_input()
            names={c.element_info.name.strip() for c in settings.descendants(control_type='Text')}
            matches={re.sub(r'^微信号\s*[:：]\s*','',s).strip() for s in names}
            if self.account not in matches:raise Blocked('WECHAT_DESKTOP_ACCOUNT_MISMATCH_OR_UNREADABLE')
            self.authorized=True
            if pin:pin.write_text(json.dumps(identity),encoding='utf8')
        finally:settings.close()
        self.window.set_focus()

    def open_target(self):
        self.check_stop()
        if not self.authorized:raise Blocked('WECHAT_ACCOUNT_NOT_VERIFIED')
        if self.current_title()!=self.title:
            candidates=[r for r in self.window.descendants(control_type='ListItem')
                        if r.element_info.automation_id=='session_item_'+self.title]
            if len(candidates)!=1:raise Blocked('WECHAT_RECENT_TARGET_UNAVAILABLE_OR_AMBIGUOUS')
            candidates[0].click_input()
        self.require_target()

    def current_title(self):
        names=[c.element_info.name for c in self.window.descendants()
               if c.element_info.automation_id.endswith('.current_chat_name_label')]
        return normalized_title(names[0]) if len(names)==1 else None

    def editor(self):
        found=[c for c in self.window.descendants() if c.element_info.automation_id=='chat_input_field']
        if len(found)!=1:raise Blocked('WECHAT_EDITOR_UNAVAILABLE')
        return found[0]

    def require_target(self,empty=True):
        self.check_stop()
        if not self.authorized or self.current_title()!=self.title:raise Blocked('WECHAT_CURRENT_TARGET_CHANGED')
        if empty and self.editor().get_value().strip():raise Blocked('EMPTY_WECHAT_DRAFT_REQUIRED')

    def native_rows(self):
        lists=[c for c in self.window.descendants() if c.element_info.automation_id=='chat_message_list']
        if len(lists)!=1:raise Blocked('WECHAT_MESSAGE_LIST_UNAVAILABLE')
        return lists[0].descendants(control_type='ListItem')

    def rows(self):
        self.require_target()
        self.window.set_focus()
        import win32gui
        if win32gui.GetForegroundWindow()!=self.window.handle:raise Blocked('WECHAT_WINDOW_NOT_FOREGROUND')
        image=self.window.capture_as_image().convert('RGB');window=tuple(self.window.rectangle())
        stamp='';result=[]
        for row in self.native_rows():
            name=row.element_info.name
            if re.fullmatch(r'(?:\d{1,2}:\d{2}|(?:今天|昨天|前天|\d{1,2}月\d{1,2}日)\s*\d{1,2}:\d{2})',name):stamp=name;continue
            if not name:continue
            match=VOICE.fullmatch(name)
            direction=direction_from_image(image,tuple(row.rectangle()),window)
            if direction=='unknown':continue
            key=hashlib.sha256((self.account+'|'+self.title+'|'+str(tuple(row.element_info.runtime_id))).encode()).hexdigest()
            result.append({'message_key':key,'direction':direction,'kind':'voice' if match else 'text',
                'seconds':int(match[1]) if match else None,'text':match[2].strip() if match else name,
                'time_label':stamp,'row_id':tuple(row.element_info.runtime_id)})
        return result[-12:]

    def inspect(self):
        rows=self.rows()
        if not rows or rows[-1]['direction']!='incoming':return {'outcome':'no_unanswered_incoming','messages':rows}
        latest=rows[-1]
        rows,latest=self.transcribe_row(rows,latest)
        if latest['kind']=='voice' and not latest['text']:raise Blocked('AUDIO_SOURCE_UNAVAILABLE')
        return {'outcome':'incoming_observed','messages':rows,'incoming':latest,'account':self.account,'title':self.title,
                'engine_id':'wechat_platform_transcription' if latest['kind']=='voice' else 'wechat_native_text'}

    def transcribe_row(self,rows,latest):
        if latest['kind']=='voice' and not latest['text']:
            native=[r for r in self.native_rows() if tuple(r.element_info.runtime_id)==latest['row_id']]
            if len(native)!=1:raise Blocked('WECHAT_VOICE_SOURCE_CHANGED')
            row=native[0];box=row.rectangle()
            # UIA bounds anchor the incoming bubble, excluding its avatar.
            fraction=.18 if latest['direction']=='incoming' else .82
            row.click_input(button='right',coords=(round(box.width()*fraction),box.height()//2))
            self.exact('语音转文字','MenuItem').click_input()
            until=time.monotonic()+8
            while time.monotonic()<until:
                self.check_stop();after=self.rows()
                candidates=[r for r in after if r['message_key']==latest['message_key']]
                if len(candidates)==1 and candidates[0]['text']:
                    rows=after;latest=candidates[0];break
                time.sleep(.2)
            else:raise Blocked('AUDIO_SOURCE_UNAVAILABLE')
        return rows,latest

    def readback(self):
        rows=self.rows()
        if not rows or rows[-1]['direction']!='outgoing':raise Blocked('WECHAT_OUTGOING_MESSAGE_UNAVAILABLE')
        rows,latest=self.transcribe_row(rows,rows[-1])
        return {'outcome':'outgoing_observed','outgoing':latest,'engine_id':'wechat_platform_transcription',
                'account':self.account,'title':self.title,'additional_messages_sent':0}

    def button(self,name,optional=False):
        self.check_stop()
        found=[b for b in self.window.descendants(control_type='Button')
               if (b.element_info.name.startswith('发语音') if name=='record' else b.element_info.name==name)]
        if optional and not found:return None
        if len(found)!=1:raise Blocked('WECHAT_VOICE_CONTROL_UNAVAILABLE')
        return found[0]

    def start_recording(self):
        self.require_target()
        if self.button('发送语音',True):raise Blocked('EXISTING_RECORDING_MUST_BE_HANDLED_MANUALLY')
        self.recording_owned=True
        self.button('record').click_input()
        until=time.monotonic()+2
        while time.monotonic()<until:
            if self.button('发送语音',True):return
            time.sleep(.05)
        raise Blocked('WECHAT_RECORDING_NOT_READY')

    def send_recording(self):self.require_target();self.button('发送语音').click_input()

    def cancel_if_self_recording(self):
        # Cancellation must remain possible after a stop signal.
        if not self.recording_owned or self.current_title()!=self.title:return False
        buttons=[b for b in self.window.descendants(control_type='Button') if b.element_info.name=='取消']
        if len(buttons)==1:buttons[0].click_input();return True
        return False

    def voices(self):
        return {r['message_key']:{'seconds':r['seconds']} for r in self.rows() if r['kind']=='voice' and r['direction']=='outgoing'}

    def voice_ids(self):return self.voices()

    def observe_new_voice(self,before):
        until=time.monotonic()+4
        while time.monotonic()<until:
            current=self.voices()
            if isinstance(before,dict):
                # A new bubble can scroll old rows and change OCR row indexes.
                # Compare visible duration counts as well as the current UI,
                # rather than misclassifying a moved old bubble as a second send.
                added=Counter(v['seconds'] for v in current.values())-Counter(v['seconds'] for v in before.values())
                if sum(added.values())==1:return {'seconds':next(iter(added))}
                if sum(added.values())>1:return None
                time.sleep(.1);continue
            rows=[v for k,v in current.items() if k not in before]
            if len(rows)==1:return rows[0]
            if len(rows)>1:return None
            time.sleep(.1)
        return None

    def send_text(self,text):
        self.require_target();before={r['message_key'] for r in self.rows()}
        self.editor().set_edit_text(text)
        self.require_target(empty=False)
        if self.editor().get_value()!=text:raise Blocked('WECHAT_TEXT_DRAFT_MISMATCH')
        self.button('发送').click_input()
        until=time.monotonic()+4
        while time.monotonic()<until:
            if not self.editor().get_value():
                matches=[r for r in self.rows() if r['message_key'] not in before and r['direction']=='outgoing' and r['text']==text]
                if len(matches)==1:return {'outcome':'sent_observed','accepted':True,'native_text_observed':True}
            time.sleep(.1)
        return {'outcome':'delivery_unknown','accepted':False}


def emit(body):print(json.dumps(body,ensure_ascii=False),flush=True)


def read_line(limit):
    value=bytearray()
    while len(value)<limit:
        part=os.read(sys.stdin.fileno(),1)
        if not part or part==b'\n':return bytes(value)
        value.extend(part)
    raise Blocked('WECHAT_WORKER_INPUT_LIMIT')


def main():
    value=json.loads(read_line(32769));stop=threading.Event();intent=None;ui=None
    try:
        try:
            ui=WechatDesktopUI(value['account'],value['title'],stop)
            ui.account_pin_path=Path(value['data'])/'wechat-desktop-account-pin.json'
        except Blocked as error:
            if str(error)!='WECHAT_MAIN_UI_ACCESSIBILITY_UNAVAILABLE' or not value.get('ocr_python'):raise
            from .wechat_screenshot import WechatScreenshotUI
            ui=WechatScreenshotUI(value['account'],value['title'],stop,value['data'],value['directory'],value['ocr_python'])
        ui.verify_account();ui.open_target()
        if value['operation']=='inspect':emit(ui.inspect());return 0
        if value['operation']=='readback':emit(ui.readback());return 0
        if value['operation']=='status':emit({'outcome':'ready','account':ui.account,'title':ui.title});return 0
        if value['operation']=='self_send':
            if ui.title!='文件传输助手':raise Blocked('DESKTOP_SELF_PROBE_ONLY')
        elif value['operation']=='send':
            actual=ui.inspect()
            if actual.get('incoming',{}).get('message_key')!=value['message_key']:raise Blocked('WECHAT_SOURCE_CHANGED_BEFORE_SEND')
        else:raise Blocked('WECHAT_WORKER_OPERATION_UNAVAILABLE')
        intent=VoiceIntent(value['data'],value['intent'],target=ui.title)
        audio=None
        if value['format']=='voice':
            audio=VirtualAudio(value['source'],value['sha256'],Path(value['directory']));audio.stop=stop
        emit({'phase':'ready','target_verified':True,'account':ui.account,'title':ui.title})
        if read_line(100).strip()!=b'GO':raise Blocked('DESKTOP_JOB_NOT_DISPATCHED')
        def monitor():
            # EOF (parent crash/cancel) cancels recording as well as explicit stop.
            os.read(sys.stdin.fileno(),100);stop.set()
        threading.Thread(target=monitor,daemon=True).start()
        if value['format']=='voice':result=send_timed_voice(ui,audio,audio.seconds,intent)
        else:
            intent.update('dispatch_attempted');result=ui.send_text(value['text']);intent.update(result['outcome'])
        result.update(reply_format=value['format'],speaker_used=False,physical_microphone_used=False,
                      audio_content_independently_verified=False,intent_path=str(intent.path),
                      virtual_capture_owner_pids=audio.capture_owners if audio else [],
                      audio_lease_path=str(audio.lease) if audio else None,
                      read_method='local_screenshot_OCR' if hasattr(ui,'ocr') else 'native_UIA')
        emit(result);return 0
    except BaseException as error:
        emit({'outcome':'worker_failed','reason':str(error)[:160],
              'intent_state':intent.body['state'] if intent else 'not_created','automatic_retry':False})
        return 2
    finally:
        if ui is not None and hasattr(ui,'close'):ui.close()


if __name__=='__main__':raise SystemExit(main())
