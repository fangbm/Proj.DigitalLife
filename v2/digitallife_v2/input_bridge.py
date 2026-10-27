"""Temporary owned IME, literal text only, independently observed afterwards."""
from contextlib import asynccontextmanager
import json
import re
import uuid
from .safety import Blocked
from .social import digest
from .store import write_json

IME = 'io.digitallife.companion/.BridgeIme'
COMPONENT = re.compile(r'[A-Za-z_][\w.]*/[A-Za-z_.$][\w.$]*', re.ASCII)


class InputBridge:
    def __init__(self, loop):
        self.loop = loop
        self.recovery = loop.config.data / 'ime-recovery.private.json'

    async def selected(self):
        value = (await self.loop.phone._text('shell', 'settings', 'get', 'secure', 'default_input_method')).strip()
        if not COMPONENT.fullmatch(value):
            raise Blocked('ORIGINAL_IME_UNVERIFIED')
        return value

    @asynccontextmanager
    async def temporary(self):
        loop = self.loop
        if not loop.bridge:
            raise Blocked('UNICODE_INPUT_BRIDGE_UNAVAILABLE')
        original = await loop.guarded(self.selected())
        if original == IME or self.recovery.exists():
            raise Blocked('IME_REQUIRES_OPERATOR_RECOVERY')
        loop.check_stop()
        write_json(self.recovery, {'serial': loop.config.serial, 'original': original, 'session': str(loop.directory)})
        try:
            await loop.guarded(loop.phone._adb('shell', 'ime', 'enable', IME))
            await loop.guarded(loop.phone._adb('shell', 'ime', 'set', IME))
            if await loop.guarded(self.selected()) != IME:
                raise Blocked('UNICODE_IME_NOT_SELECTED')
            yield self
        finally:
            # Restoration must run even after STOP/deadline. Only restore our
            # current selection, preserving any user-selected different IME.
            try:
                if await self.selected() == IME:
                    await loop.phone._adb('shell', 'ime', 'set', original)
                if await self.selected() != IME:
                    self.recovery.unlink(missing_ok=True)
                    loop.store.event('social_ime_restored', {'owned_selection_released': True})
            except (OSError, ValueError, TimeoutError):
                loop.store.update(ime_recovery_required=True)

    async def insert(self, observation, outbound_id, text):
        loop = self.loop
        editors = [c for c in observation.controls if c.is_editor and c.focused and c.enabled
                   and c.package == observation.foreground.split('/')[0] and not c.covered]
        visual_editor=not editors and observation.foreground.split('/')[0]=='com.tencent.mm' and bool(observation.visual_controls)
        if visual_editor:
            await self.observe_editor(observation)
            if observation.input_editor.get('text_sha256')!=digest(''):
                raise Blocked('SOCIAL_EXISTING_DRAFT_OR_UNVERIFIED_EDITOR')
            package=observation.foreground.split('/')[0]
        elif len(editors)!=1 or editors[0].text:
            raise Blocked('SOCIAL_EXISTING_DRAFT_OR_UNVERIFIED_EDITOR')
        else:package=editors[0].package
        request_id = uuid.uuid4().hex
        await loop.guarded(loop.phone.identity())
        if await loop.guarded(loop.phone.scene()) != observation.foreground:
            raise Blocked('SOCIAL_EDITOR_SCENE_CHANGED')
        async def commit():
            result = await loop.bridge.request('POST', '/input/text', {'request_id': request_id,
                'package': package, 'text': text})
            if result.get('committed') is not True or result.get('request_id') != request_id:
                raise Blocked('UNICODE_COMMIT_UNCONFIRMED')
        after, _ = await loop.send_operation(commit, {'type': 'social_type_text', 'outbound_id': outbound_id,
            'text_sha256': digest(text), 'request_id': request_id}, observation)
        if visual_editor:
            await self.observe_editor(after)
            if after.input_editor.get('text_sha256')!=digest(text):raise Blocked('SOCIAL_TYPED_TEXT_NOT_OBSERVED')
        else:
            typed = [c for c in after.controls if c.resource == editors[0].resource and c.package == editors[0].package and c.is_editor]
            if len(typed) != 1 or typed[0].text != text:
                raise Blocked('SOCIAL_TYPED_TEXT_NOT_OBSERVED')
        return after

    async def observe_editor(self,observation):
        request_id=uuid.uuid4().hex;package=observation.foreground.split('/')[0]
        value=await self.loop.guarded(self.loop.bridge.request('POST','/input/inspect',{'request_id':request_id,'package':package}))
        if (value.get('available') is not True or value.get('request_id')!=request_id or value.get('package')!=package
                or not isinstance(value.get('text'),str) or len(value['text'])>300
                or await self.loop.guarded(self.loop.phone.scene())!=observation.foreground):
            raise Blocked('SOCIAL_EDITOR_READBACK_UNAVAILABLE')
        observation.input_editor={'source':'Android_InputConnection_independent_read','request_id':request_id,
            'package':package,'field_id':value.get('field_id'),'text_sha256':digest(value['text']),
            'observation_id':observation.observation_id}
        self.loop.store.event('social_editor_readback',observation.input_editor)
        return observation
