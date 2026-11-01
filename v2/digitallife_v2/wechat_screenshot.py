"""Local OCR fallback for a calibrated desktop layout; never guesses hidden controls."""
from contextlib import suppress
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time

from .safety import Blocked
from .wechat_desktop_worker import PROGRAM,WechatDesktopUI,normalized_title


def image_hash(image,box):
    crop=image.crop(box).convert('RGB')
    return hashlib.sha256(str(crop.size).encode()+crop.tobytes()).hexdigest()


def ink_hash(image,box):
    crop=image.crop(box).convert('L').point(lambda v:255 if v<128 else 0)
    return hashlib.sha256(str(crop.size).encode()+crop.tobytes()).hexdigest()


def literal_keys(text):
    return ''.join('{'+c+'}' if c in '+^%~{}()' else c for c in text)


class Hit:
    def __init__(self,owner,window,image,box):self.owner,self.window,self.image,self.box=owner,window,image,tuple(box)
    def click_input(self,button='left'):
        self.owner.check_stop()
        fresh=self.owner.capture(self.window)
        if image_hash(fresh,self.box)!=image_hash(self.image,self.box):raise Blocked('WECHAT_VISUAL_CONTROL_CHANGED')
        x=(self.box[0]+self.box[2])//2;y=(self.box[1]+self.box[3])//2
        rect=self.window.rectangle()
        self.window.click_input(button=button,coords=(rect.left+x,rect.top+y),absolute=True)


class WechatScreenshotUI(WechatDesktopUI):
    def __init__(self,account,title,stop,data,directory,ocr_python):
        import psutil
        from pywinauto import Desktop
        self.desktop=Desktop(backend='uia');self.stop=stop or threading.Event()
        self.account=account;self.title=normalized_title(title);self.authorized=False;self.recording_owned=False
        self.data=Path(data);self.directory=Path(directory);self.frame_file=self.directory/'local-ocr-frame.png'
        calibration=self.data/'windows-wechat-visual.private.json'
        try:self.calibration=json.loads(calibration.read_text(encoding='utf8'))
        except (OSError,ValueError):raise Blocked('WECHAT_SCREENSHOT_CALIBRATION_REQUIRED') from None
        windows=[w for w in self.desktop.windows(title='微信')
                 if Path(psutil.Process(w.element_info.process_id).exe()).resolve()==Path(PROGRAM).resolve()
                 and w.element_info.class_name=='Qt51514QWindowIcon' and w.rectangle().width()>600]
        if len(windows)!=1:raise Blocked('ONE_WECHAT_VISUAL_MAIN_WINDOW_REQUIRED')
        self.window=windows[0];self.ocr=None;self.ocr_python=ocr_python
        self.header=None;self.last_image=None;self.last_result=None
        self.directory.mkdir(parents=True,exist_ok=True)
        try:self.read()
        except BaseException:self.close();raise

    def capture(self,window=None):
        import win32gui,win32process
        window=window or self.window;window.set_focus()
        # Keep the desktop cursor/highlight off the controls being recognized.
        window.move_mouse_input(coords=(window.rectangle().width()//2,15))
        time.sleep(.03 if self.recording_owned else .2)
        foreground=win32gui.GetForegroundWindow()
        if win32process.GetWindowThreadProcessId(foreground)[1]!=window.element_info.process_id:
            raise Blocked('WECHAT_VISUAL_WINDOW_NOT_FOREGROUND')
        return window.capture_as_image().convert('RGB')

    def read(self,window=None,*,templates_only=False):
        from . import visual
        window=window or self.window;image=self.capture(window)
        if window.handle==self.window.handle and list(image.size)!=self.calibration['size']:
            raise Blocked('WECHAT_VISUAL_LAYOUT_RECALIBRATION_REQUIRED')
        image.save(self.frame_file);sha=hashlib.sha256(self.frame_file.read_bytes()).hexdigest()
        if self.ocr is None:
            self.ocr=subprocess.Popen([self.ocr_python,str(Path(visual.__file__)),'--worker'],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        request={'png':str(self.frame_file),'sha256':sha,'templates':[],'templates_only':templates_only}
        if window.handle==self.window.handle:
            request.update(templates=self.calibration['templates'],desktop_region=self.calibration['messages'])
        self.ocr.stdin.write(json.dumps(request).encode()+b'\n');self.ocr.stdin.flush()
        value=json.loads(self.ocr.stdout.readline(500001))
        if value.get('sha256')!=sha:raise Blocked('WECHAT_LOCAL_OCR_UNAVAILABLE')
        if window.handle==self.window.handle:self.last_image,self.last_result=image,value
        return image,value

    def text_hit(self,text,*,window=None,area=None,optional=False):
        window=window or self.window;image,value=self.read(window)
        found=[line for line in value['lines'] if line['confidence']>=.6 and line['text']==text
               and (area is None or area(line['bounds'],image.size))]
        if optional and not found:return None
        if len(found)!=1:raise Blocked('WECHAT_OCR_TEXT_UNAVAILABLE:'+text)
        return Hit(self,window,image,found[0]['bounds'])

    def template_hit(self,role):
        image,value=self.read()
        matches=[m for m in value['templates'] if m['role']==role and m['confidence']>=.995]
        if len(matches)!=1:raise Blocked('WECHAT_VISUAL_BUTTON_UNAVAILABLE:'+role)
        return Hit(self,self.window,image,matches[0]['bounds'])

    def verify_account(self):
        import psutil
        expected={'account':self.account,'pid':self.window.element_info.process_id,'hwnd':self.window.handle,
                  'created':psutil.Process(self.window.element_info.process_id).create_time()}
        pin=self.data/'wechat-desktop-account-pin.json'
        try:
            if json.loads(pin.read_text(encoding='utf8'))==expected:self.authorized=True;return
        except (OSError,ValueError):pass
        settings=[w for w in self.desktop.windows(title='设置') if w.element_info.process_id==self.window.element_info.process_id]
        if not settings:
            area=lambda b,size:b[2]<size[0]*.35 and b[1]>size[1]*.6
            settings_hit=self.text_hit('设置',area=area,optional=True)
            if settings_hit is None:
                self.template_hit('windows_more').click_input()
                settings_hit=self.text_hit('设置',area=area)
            settings_hit.click_input()
            until=time.monotonic()+2
            while time.monotonic()<until:
                settings=[w for w in self.desktop.windows(title='设置') if w.element_info.process_id==self.window.element_info.process_id]
                if settings:break
                time.sleep(.1)
        if len(settings)!=1:raise Blocked('WECHAT_VISUAL_ACCOUNT_SETTINGS_UNAVAILABLE')
        settings=settings[0]
        try:
            self.text_hit('账号与存储',window=settings).click_input()
            _,value=self.read(settings)
            names={re.sub(r'^微信号\s*[:：]\s*','',x['text']).strip() for x in value['lines'] if x['confidence']>=.8}
            if self.account not in names:raise Blocked('WECHAT_DESKTOP_ACCOUNT_MISMATCH_OR_UNREADABLE')
            self.authorized=True;pin.write_text(json.dumps(expected),encoding='utf8')
        finally:settings.close()
        self.read()

    def current_title(self):
        image,value=self.read()
        names=[x for x in value['lines'] if x['bounds'][0]>image.width*.34 and x['bounds'][3]<image.height*.12
               and x['confidence']>=.65 and normalized_title(x['text'])==self.title]
        if len(names)==1:
            self.header=(names[0]['bounds'],ink_hash(image,names[0]['bounds']));return self.title
        return None

    def open_target(self):
        self.check_stop()
        if not self.authorized:raise Blocked('WECHAT_ACCOUNT_NOT_VERIFIED')
        if self.current_title()!=self.title:
            self.text_hit(self.title,area=lambda b,size:b[2]<size[0]*.35 and b[1]>size[1]*.12).click_input()
            if self.current_title()!=self.title:raise Blocked('WECHAT_VISUAL_CHAT_NOT_OPEN')
        image,value=self.read()
        markers=[x for x in value['lines'] if re.fullmatch(r'[↓↧]?\s*\d+条新消息',x['text'])
                 and x['bounds'][0]>image.width*.65 and image.height*.3<x['bounds'][1]<self.calibration['editor'][1]]
        if len(markers)==1:Hit(self,self.window,image,markers[0]['bounds']).click_input()
        self.require_target()
        self.cached_record=self.template_hit('windows_record')

    def require_target(self,empty=True):
        self.check_stop();image=self.capture()
        if not self.authorized or not self.header or ink_hash(image,self.header[0])!=self.header[1]:
            raise Blocked('WECHAT_VISUAL_CURRENT_TARGET_CHANGED')
        if empty and not self.recording_owned:
            # Reject any dark draft glyphs; caret color is excluded.
            editor=image.crop(self.calibration['editor']).convert('RGB')
            dark=sum(max(pixel)<150 for pixel in editor.getdata())
            if dark>5:raise Blocked('EMPTY_WECHAT_DRAFT_REQUIRED')

    def rows(self):
        self.require_target();image,value=self.read();stamp='';result=[]
        bubbles=sorted(value.get('desktop_bubbles',[]),key=lambda b:b['bounds'][1])
        for bubble in bubbles:
            a,b,c,d=bubble['bounds']
            lines=[x for x in value['lines'] if x['confidence']>=.6 and a<=x['bounds'][0]+5 and x['bounds'][2]-5<=c
                   and b<=x['bounds'][1]+5 and x['bounds'][3]-5<=d]
            text=''.join(x['text'] for x in sorted(lines,key=lambda x:(x['bounds'][1],x['bounds'][0])))
            if not text:continue
            # Desktop WeChat also paints received voice bubbles light gray.
            # Recognize the visible duration before treating gray as a transcript.
            voice=re.fullmatch(r'[()（）]{0,3}\s*(\d{1,2})\s*["″”′’]+\s*[()（）]{0,3}',text)
            gray_voice=bubble['kind']=='transcript_gray' and voice and a<image.width*.7
            if bubble['kind']=='transcript_gray' and not gray_voice:
                if (result and result[-1]['kind']=='voice' and 0<=b-result[-1]['bounds'][3]<120
                        and min(abs(a-result[-1]['bounds'][0]),abs(c-result[-1]['bounds'][2]))<30):
                    result[-1]['text']=text
                continue
            incoming=bubble['kind']=='incoming_neutral' or bool(gray_voice)
            if incoming and a>image.width*.7:continue
            times=[x['text'] for x in value['lines'] if x['bounds'][3]<b and re.fullmatch(r'\d{1,2}:\d{2}',x['text'])]
            if times:stamp=times[-1]
            # OCR can include one of the adjacent voice-wave strokes as '(' or ')'.
            kind='voice' if voice else 'text'
            key=hashlib.sha256((self.account+'|'+self.title+'|'+stamp+'|'+str(len(result))+'|'+image_hash(image,bubble['bounds'])).encode()).hexdigest()
            result.append({'message_key':key,'direction':'incoming' if incoming else 'outgoing','kind':kind,
                'seconds':int(voice[1]) if voice else None,'text':'' if voice else text,'time_label':stamp,
                'bounds':bubble['bounds'],'row_id':key})
        return result[-12:]

    def transcribe_row(self,rows,latest):
        if latest['kind']!='voice' or latest['text']:return rows,latest
        image=self.capture();a,b,c,d=latest['bounds']
        Hit(self,self.window,image,(a+5,b+5,min(c-5,a+45),d-5)).click_input(button='right')
        self.text_hit('语音转文字').click_input()
        until=time.monotonic()+8
        while time.monotonic()<until:
            after=self.rows();candidates=[r for r in after if r['message_key']==latest['message_key']]
            if len(candidates)==1 and candidates[0]['text']:return after,candidates[0]
            time.sleep(.15)
        raise Blocked('AUDIO_SOURCE_UNAVAILABLE')

    def button(self,name,optional=False):
        if name=='record':return self.cached_record
        if name in ('发送语音','取消') and self.recording_owned and name in getattr(self,'record_buttons',{}):
            return self.record_buttons[name]
        return self.text_hit(name,optional=optional)

    def start_recording(self):
        self.require_target()
        _,before=self.read(templates_only=True)
        if any(m['role']=='windows_voice_send' for m in before['templates']):raise Blocked('EXISTING_RECORDING_MUST_BE_HANDLED_MANUALLY')
        self.recording_owned=True;self.cached_record.click_input()
        until=time.monotonic()+1.5
        while time.monotonic()<until:
            image,value=self.read(templates_only=True);self.record_buttons={}
            for name,role in (('发送语音','windows_voice_send'),('取消','windows_voice_cancel')):
                matches=[m for m in value['templates'] if m['role']==role and m['confidence']>=.995]
                if len(matches)==1:self.record_buttons[name]=Hit(self,self.window,image,matches[0]['bounds'])
            if len(self.record_buttons)==2:return
        raise Blocked('WECHAT_VISUAL_RECORDING_NOT_READY')

    def cancel_if_self_recording(self):
        if not self.recording_owned:return False
        try:
            fresh=self.capture()
            if not self.header or ink_hash(fresh,self.header[0])!=self.header[1]:return False
            hit=getattr(self,'record_buttons',{}).get('取消')
            if hit is None:
                self.window.type_keys('{ESC}');return True
            # Stop permits cancellation, but not a send or target change.
            fresh=self.capture()
            if image_hash(fresh,hit.box)!=image_hash(hit.image,hit.box):return False
            rect=self.window.rectangle()
            self.window.click_input(coords=(rect.left+(hit.box[0]+hit.box[2])//2,rect.top+(hit.box[1]+hit.box[3])//2),absolute=True);return True
        except (KeyError,Blocked):return False

    def send_text(self,text):
        from pywinauto.keyboard import send_keys
        self.require_target();before={r['message_key'] for r in self.rows()}
        a,b,c,d=self.calibration['editor'];image=self.capture()
        Hit(self,self.window,image,(a+10,b+10,a+50,b+35)).click_input()
        send_keys(literal_keys(text),with_spaces=True,pause=.01,vk_packet=True)
        image,value=self.read()
        actual=''.join(x['text'] for x in value['lines'] if a<=x['bounds'][0] and b<=x['bounds'][1] and x['bounds'][2]<=c and x['bounds'][3]<=d)
        normalize=lambda s:re.sub(r'\s+','',s)
        if normalize(actual)!=normalize(text):raise Blocked('WECHAT_VISUAL_TEXT_DRAFT_MISMATCH')
        self.require_target(empty=False);self.text_hit('发送',area=lambda box,size:box[0]>size[0]*.8).click_input()
        until=time.monotonic()+4
        while time.monotonic()<until:
            matches=[r for r in self.rows() if r['message_key'] not in before and r['direction']=='outgoing' and normalize(r['text'])==normalize(text)]
            if len(matches)==1:return {'outcome':'sent_observed','accepted':True,'native_text_observed':True}
        return {'outcome':'delivery_unknown','accepted':False}

    def close(self):
        if self.ocr and self.ocr.poll() is None:
            self.ocr.terminate()
            with suppress(subprocess.TimeoutExpired):self.ocr.wait(timeout=2)
        self.frame_file.unlink(missing_ok=True)
