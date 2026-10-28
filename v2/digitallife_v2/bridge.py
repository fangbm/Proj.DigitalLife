"""Authenticated device-local notification bridge over an owned ADB forward."""
import asyncio
import json
import secrets
import hashlib
import re
from pathlib import Path
import time
from .phone import PhoneController
from .safety import Blocked, DeviceLock
from .store import write_json

PACKAGE = "io.digitallife.companion"
COMPONENT = PACKAGE + "/.BridgeListener"


class NotificationBridge:
    def __init__(self, config, phone):
        self.config,self.phone=config,phone
        value=json.loads((config.data/'notification-bridge.private.json').read_text(encoding='utf-8'))
        if value.get('serial')!=config.serial or not isinstance(value.get('token'),str) or len(value['token'])!=64:
            raise Blocked('NOTIFICATION_BRIDGE_PROFILE_INVALID')
        self.token=value['token']; self.port=None

    async def request(self, method, path, body=None):
        if self.port is None:raise Blocked('NOTIFICATION_BRIDGE_NOT_CONNECTED')
        async def exchange():
            reader,writer=await asyncio.open_connection('127.0.0.1',self.port,limit=2_000_000)
            try:
                wire=json.dumps(body).encode() if body is not None else b''
                header=f'{method} {path} HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {self.token}\r\nContent-Type: application/json\r\nContent-Length: {len(wire)}\r\nConnection: close\r\n\r\n'
                writer.write(header.encode()+wire);await writer.drain()
                response=await reader.readuntil(b'\r\n\r\n')
                if not response.startswith(b'HTTP/1.1 200 '):raise Blocked('NOTIFICATION_BRIDGE_REQUEST_REFUSED')
                length=next((int(x.split(b':',1)[1]) for x in response.split(b'\r\n') if x.lower().startswith(b'content-length:')),0)
                if not 0<length<=1_000_000:raise Blocked('NOTIFICATION_BRIDGE_RESPONSE_LIMIT')
                return json.loads(await reader.readexactly(length))
            finally:
                writer.close();await writer.wait_closed()
        return await asyncio.wait_for(exchange(),3)

    async def connect(self, seconds):
        await self.phone.identity()
        raw=await self.phone._text('forward','tcp:0','tcp:8766')
        if not raw.strip().isdigit():raise Blocked('NOTIFICATION_FORWARD_UNAVAILABLE')
        self.port=int(raw.strip())
        status=await self.request('GET','/status')
        if not status.get('listener_ready'):raise Blocked('NOTIFICATION_LISTENER_NOT_READY')
        await self.request('POST','/session',{'ttl_seconds':max(1,min(600,int(seconds))),'packages':list(self.config.notification_packages)})

    async def poll(self, hub):
        result=await self.request('GET','/events');keys=[];new=[]
        for item in result.get('events',[])[:100]:
            age=item.get('age_seconds')
            if type(age) not in (int,float) or not 0<=age<=3600:continue
            # Normalize device-wall-clock skew using an age measured on the same device.
            event={**item,'posted_at':time.time()-age}
            eid=hub.ingest(event)
            if eid:new.append(eid)
            keys.append(item['event_key'])
        if keys:await self.request('POST','/ack',{'keys':keys})
        return new

    async def close(self):
        inactive=False
        if self.port is not None:
            try:
                await self.request('DELETE','/session')
                inactive=True
                state=await self.request('GET','/status')
                self.audio_projection_released=not state['audio_authorized'] if 'audio_authorized' in state else None
            except (OSError,ValueError,asyncio.TimeoutError,EOFError):pass
            try:await self.phone._adb('forward','--remove',f'tcp:{self.port}')
            except (OSError,ValueError,asyncio.TimeoutError):pass
            self.port=None
        return inactive


async def grant_sensitive_notifications(config,phone):
    """Explicit administrative setup, never a model action or normal Run."""
    await phone.identity()
    previous=(await phone._text('shell','cmd','appops','get',PACKAGE,'RECEIVE_SENSITIVE_NOTIFICATIONS')).strip()
    path=config.data/'notification-permissions.private.json'
    if not path.exists():write_json(path,{'serial':config.serial,'sensitive_original':previous,'grant_source':'explicit_operator_phone_permission_setup'})
    await phone._adb('shell','cmd','appops','set',PACKAGE,'RECEIVE_SENSITIVE_NOTIFICATIONS','allow')
    actual=(await phone._text('shell','cmd','appops','get',PACKAGE,'RECEIVE_SENSITIVE_NOTIFICATIONS')).strip()
    if not re.search(r'RECEIVE_SENSITIVE_NOTIFICATIONS:\s*allow(?:;|\s|$)',actual):raise Blocked('SENSITIVE_NOTIFICATION_PERMISSION_NOT_GRANTED')
    return True


async def setup(config, apk, provision_only=False,allow_sensitive=False):
    """Explicit administrative CLI, never called by model or normal Run."""
    path=Path(apk).resolve()
    if not path.is_file() or path.suffix!='.apk':raise ValueError('COMPANION_APK_REQUIRED')
    with DeviceLock(config.serial):
        phone=PhoneController(config,config.data/'unused-bridge-setup')
        await phone.identity()
        if not provision_only:
            await phone._adb('install','--no-incremental','-r',str(path),timeout=90)
        location=(await phone._text('shell','pm','path',PACKAGE)).strip()
        if not re.fullmatch(r'package:/data/app/[A-Za-z0-9_~=/.-]+/base.apk',location):raise Blocked('COMPANION_INSTALLATION_NOT_VERIFIED')
        digest=(await phone._text('shell','sha256sum',location[8:])).split()[0]
        if digest!=hashlib.sha256(path.read_bytes()).hexdigest():raise Blocked('COMPANION_APK_HASH_MISMATCH')
        await phone._adb('shell','am','start','-W','-n',PACKAGE+'/.MainActivity')
        token=secrets.token_hex(32)
        await phone._adb('shell','run-as',PACKAGE,'mkdir','-p','files')
        proc=await asyncio.create_subprocess_exec(config.adb,'-s',config.serial,'shell','run-as',PACKAGE,'tee','files/bridge.token',
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        try:await asyncio.wait_for(proc.communicate(token.encode()),10)
        finally:
            if proc.returncode is None:proc.kill();await proc.wait()
        if proc.returncode:raise Blocked('NOTIFICATION_TOKEN_PROVISION_FAILED')
        await phone._adb('shell','pm','grant',PACKAGE,'android.permission.POST_NOTIFICATIONS')
        await phone._adb('shell','pm','grant',PACKAGE,'android.permission.RECORD_AUDIO')
        if allow_sensitive:
            await grant_sensitive_notifications(config,phone)
            # Android caches trusted listener UIDs when the service connects.
            # Reconnect after the explicit app-op grant, outside any live lease.
            await phone._adb('shell','cmd','notification','disallow_listener',COMPONENT)
        await phone._adb('shell','cmd','notification','allow_listener',COMPONENT)
        write_json(config.data/'notification-bridge.private.json',{'serial':config.serial,'token':token})
        bridge=NotificationBridge(config,phone)
        try:
            for attempt in range(6):
                try:
                    await bridge.connect(15)
                    return {'installed':True,'listener_ready':True,'collection_active_after_setup':False}
                except (OSError,ValueError,asyncio.TimeoutError):
                    await bridge.close()
                    if attempt==5:raise
                    await asyncio.sleep(1)
        finally:await bridge.close()
