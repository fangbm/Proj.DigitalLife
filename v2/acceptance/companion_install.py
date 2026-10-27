"""Explicit companion install with a source-bound OEM confirmation and audit."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
from digitallife_v2.bridge import PACKAGE
from digitallife_v2.config import Config
from digitallife_v2.phone import PhoneController
from digitallife_v2.session import SessionLoop,SessionStopped


class Installer:
    def __init__(self,config,store,apk):
        self.phone=PhoneController(config,store.directory/'unused-install-screens')
        self.apk=apk;self.confirmed=False
        self.install=asyncio.create_task(self.phone._adb('install','--no-incremental','-r',str(apk),timeout=170))

    async def decide(self,observation,apps,recent):
        if self.install.done():
            self.install.result()
            path=(await self.phone._text('shell','pm','path',PACKAGE)).strip()
            if not re.fullmatch(r'package:/data/app/[A-Za-z0-9_~=/.-]+/base.apk',path):raise SessionStopped('INSTALL_NOT_OBSERVED')
            digest=(await self.phone._text('shell','sha256sum',path[8:])).split()[0]
            if digest!=hashlib.sha256(self.apk.read_bytes()).hexdigest():raise SessionStopped('INSTALL_HASH_MISMATCH')
            raise SessionStopped('COMPANION_INSTALL_VERIFIED')
        action={'type':'rest','seconds':1}
        if not self.confirmed and observation.foreground=='com.android.packageinstaller/com.android.packageinstaller.PackageInstallerActivity':
            if not any(c.text=='DigitalLife 通知桥' for c in observation.controls):raise SessionStopped('INSTALL_LABEL_MISMATCH')
            targets=[c for c in observation.controls if c.text=='安装' and c.clickable]
            if len(targets)==1:
                action={'type':'tap','control_id':targets[0].control_id};self.confirmed=True
        return {'intent':'rest' if action['type']=='rest' else 'explore','action':action,'note':'Explicit local companion installation acceptance'}


async def run(config,directory,apk):
    loop=SessionLoop(config,directory,minutes=3,brain_factory=lambda c,s:Installer(c,s,apk))
    try:return await loop.run()
    finally:
        task=getattr(getattr(loop,'brain',None),'install',None)
        if task:
            if not task.done():task.cancel()
            try:await task
            except BaseException:pass


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apk',required=True);parser.add_argument('--session',required=True);parser.add_argument('--profile',default='find-x6');args=parser.parse_args()
    result=asyncio.run(run(Config.load(args.profile),args.session,Path(args.apk).resolve()))
    print(json.dumps(result));return 0 if result['reason']=='COMPANION_INSTALL_VERIFIED' else 2
if __name__=='__main__':raise SystemExit(main())
