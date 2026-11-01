"""Compatibility self-only probe; shared audio lives in the installed runtime."""
import json
from pathlib import Path
import re
import sys
import time
import warnings
from digitallife_v2.safety import Blocked
from digitallife_v2.timed_voice import VoiceIntent, send_timed_self_voice
from digitallife_v2.windows_audio import PROGRAM,VirtualAudio,audio_objects,restore_lease


class FileAssistantUI:
    def __init__(self):
        import psutil
        from pywinauto import Desktop
        self.desktop = Desktop(backend='uia')
        windows = [w for w in self.desktop.windows(title='微信')
                   if Path(psutil.Process(w.element_info.process_id).exe()).resolve() == Path(PROGRAM).resolve()]
        if len(windows) != 1:raise Blocked('ONE_WECHAT_WINDOW_REQUIRED')
        self.window = windows[0]
        self.require_self_chat()
        self.window.set_focus()

    def require_self_chat(self):
        controls = self.window.descendants()
        if any(c.element_info.automation_id == 'login_layout_' and c.is_visible() for c in controls):
            raise Blocked('WECHAT_MANUAL_LOGIN_REQUIRED')
        names = [c.element_info.name for c in controls
                 if c.element_info.automation_id.endswith('.current_chat_name_label')]
        if names != ['文件传输助手']:raise Blocked('FILE_TRANSFER_ASSISTANT_ONLY')
        drafts = [c for c in controls
                  if c.element_info.automation_id == 'chat_input_field']
        if len(drafts) != 1 or drafts[0].get_value().strip():raise Blocked('EMPTY_SELF_CHAT_DRAFT_REQUIRED')

    def require_target(self):return self.require_self_chat()

    def button(self, name, *, optional=False):
        found = [b for b in self.window.descendants(control_type='Button')
                 if (b.element_info.name.startswith('发语音') if name == 'record' else b.element_info.name == name)]
        if optional and not found:return None
        if len(found) != 1:raise Blocked('UNIQUE_WECHAT_VOICE_CONTROL_REQUIRED')
        return found[0]

    def start_recording(self):
        if self.button('发送语音', optional=True):raise Blocked('EXISTING_RECORDING_MUST_BE_HANDLED_MANUALLY')
        self.recording_owned = True
        self.button('record').click_input()
        until = time.monotonic()+2
        while time.monotonic() < until:
            if self.button('发送语音', optional=True):return
            time.sleep(.05)
        raise Blocked('WECHAT_RECORDING_NOT_READY')

    def send_recording(self):self.button('发送语音').click_input()

    def cancel_if_self_recording(self):
        if not getattr(self,'recording_owned',False):return False
        self.require_self_chat()
        if self.button('发送语音', optional=True):
            self.button('取消').click_input(); return True
        return False

    def voices(self):
        result = {}
        for row in self.window.descendants(control_type='ListItem'):
            match = re.match(r'^语音\s*(\d+)\s*["″]?\s*秒', row.element_info.name)
            if match:result[tuple(row.element_info.runtime_id)] = {'seconds':int(match[1])}
        return result

    def voice_ids(self):return set(self.voices())

    def observe_new_voice(self, before):
        until = time.monotonic()+4
        while time.monotonic() < until:
            self.require_self_chat()
            rows = [body for key, body in self.voices().items() if key not in before]
            if len(rows) == 1:return rows[0]
            if len(rows) > 1:return None
            time.sleep(.1)
        return None


def main():
    warnings.filterwarnings('ignore', category=UserWarning, module='pycaw')
    if len(sys.argv) == 3 and sys.argv[1] == '--restore-lease':
        restore_lease(sys.argv[2]); return 0
    value = json.loads(sys.stdin.buffer.read(32769))
    directory = Path(value['directory']).resolve()
    intent = VoiceIntent(value['data'], value['intent'])
    ui=None
    try:
        audio = VirtualAudio(value['source'], value['sha256'], directory)
        try:ui = FileAssistantUI()
        except Blocked:
            if not value.get('ocr_python'):raise
            from digitallife_v2.wechat_screenshot import WechatScreenshotUI
            import threading
            ui=WechatScreenshotUI(value['account'],'文件传输助手',threading.Event(),value['data'],directory,value['ocr_python'])
            ui.verify_account();ui.open_target()
        result = send_timed_self_voice(ui, audio, audio.seconds, intent)
    except BaseException as error:
        (directory/'WORKER_FAILURE.json').write_text(json.dumps({
            'reason':str(error)[:1000], 'intent_state':intent.body['state'],
            'automatic_retry':False}, ensure_ascii=False, indent=2), encoding='utf8')
        raise
    finally:
        if ui is not None and hasattr(ui,'close'):ui.close()
    result.update(target='文件传输助手', speaker_used=False, physical_microphone_used=False,
                  virtual_capture_owner_pids=audio.capture_owners, intent_path=str(intent.path))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':raise SystemExit(main())
