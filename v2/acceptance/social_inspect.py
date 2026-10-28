"""Explicit source-bound navigation to inspect social UI; never types or sends."""
import argparse
import asyncio
import json
import re
import unicodedata
from dataclasses import asdict
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop, SessionStopped
from digitallife_v2.store import write_json

APPS = {'qq': 'com.tencent.mobileqq', 'wechat': 'com.tencent.mm'}


class Inspect:
    def __init__(self, config, store, app, selectors, current=False, loop=None,read_marker=None):
        self.store, self.app, self.selectors = store, app, selectors
        self.stage = 0 if current else -1
        self.loop=loop;self.real=LifeBrain(config,store)
        self.read_marker=read_marker

    async def structured(self,*args):return await self.real.structured(*args)

    async def decide(self, observation, apps, recent):
        if recent and recent[-1].get('outcome')=='blocked':
            raise SessionStopped('SOCIAL_INSPECT_NAVIGATION_BLOCKED:'+recent[-1].get('reason','unknown'))
        if self.stage >= 0 and observation.foreground.split('/')[0] != self.app:
            raise SessionStopped('SOCIAL_INSPECT_FOREGROUND_CHANGED')
        if self.stage == -1:
            self.stage = 0
            action = {'type': 'open_app', 'package_name': self.app}
        elif self.stage < len(self.selectors):
            field, value = self.selectors[self.stage]
            if field in {'visual','visual_scroll'}:
                from digitallife_v2.visual import locate
                control=await locate(self.loop,observation,value);self.stage+=1
                action={'type':'tap','control_id':control.control_id} if field=='visual' else {'type':'swipe','control_id':control.control_id,'direction':'up'}
                from digitallife_v2.social_ui import AndroidSocialUI
                await AndroidSocialUI(self.loop,self.app).act(action,observation)
                return {'intent':'rest','action':{'type':'rest','seconds':1},'note':'Source-bound visual inspection completed internally'}
            if field == 'navigation':
                self.stage += 1
                return {'intent':'explore','action':{'type':'back'},'note':'Explicit source-bound back navigation inspection'}
            matches = [c for c in observation.controls if c.package == self.app
                and getattr(c, field) == value and (c.enabled or c.control_id in observation.visual_controls) and not c.covered]
            if len(matches) != 1:
                raise SessionStopped('SOCIAL_INSPECT_SELECTOR_AMBIGUOUS_OR_MISSING')
            self.stage += 1
            action = {'type': 'tap', 'control_id': matches[0].control_id}
        else:
            if self.read_marker:
                from digitallife_v2.social_ui import AndroidSocialUI
                from digitallife_v2.social import digest
                ui=AndroidSocialUI(self.loop,self.app);ui.composer(observation)
                await ui.ground_editor_bounds(observation)
                incoming=[m for m in ui.messages(observation) if m['direction']=='incoming']
                row=self.store.db.execute("SELECT preview FROM inbound_events WHERE device=? AND source_app=? AND preview LIKE ? ORDER BY received DESC LIMIT 1",(self.loop.config.serial,self.app,'%'+self.read_marker+'%')).fetchone()
                normalize=lambda s:re.sub(r'\s+','',unicodedata.normalize('NFKC',s))
                if (not row or not incoming or self.read_marker not in incoming[-1]['text']
                        or not normalize(row[0]).endswith(normalize(incoming[-1]['text']))):
                    raise SessionStopped('SOCIAL_READ_FULL_BODY_NOT_MATCHED')
                write_json(self.store.directory/'M4_READ_READY.json',{'source':'LIVE_CURRENT_SCREEN_FULL_MESSAGE_READ_ONLY',
                    'observation_id':observation.observation_id,'message_sha256':digest(incoming[-1]['text']),
                    'preview_sha256':digest(row[0]),'normalized_full_body_match':True,
                    'notification_routing_verified':False,'outgoing_sends':0})
                await ui.act({'type':'home'},observation)
                raise SessionStopped('SOCIAL_CURRENT_MESSAGE_READ_VERIFIED')
            snapshot = observation.summary()
            snapshot['controls'] = [asdict(c) for c in observation.controls]
            write_json(self.store.directory / 'UI_READY.json', snapshot)
            raise SessionStopped('SOCIAL_UI_INSPECTED')
        return {'intent': 'explore', 'action': action, 'note': 'Explicit social UI navigation inspection'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', required=True)
    parser.add_argument('--profile', default='find-x6')
    parser.add_argument('--app', choices=APPS, required=True)
    parser.add_argument('--tap', action='append', default=[])
    parser.add_argument('--description', action='append', default=[])
    parser.add_argument('--resource', action='append', default=[])
    parser.add_argument('--current', action='store_true')
    parser.add_argument('--read-marker')
    parser.add_argument('--step', action='append', default=[], help='Ordered text=, description= or resource= selectors')
    args = parser.parse_args()
    if args.read_marker and (args.app!='wechat' or not re.fullmatch('[A-Za-z0-9-]{6,60}',args.read_marker)):
        raise ValueError('WECHAT_ASCII_READ_MARKER_REQUIRED')
    selectors = [('text', s) for s in args.tap] + [('description', s) for s in args.description] + [('resource', s) for s in args.resource]
    if args.step:
        if selectors:
            raise ValueError('USE_ORDERED_STEPS_OR_SEPARATE_SELECTORS')
        selectors = [tuple(s.split('=', 1)) for s in args.step]
        if any(len(s) != 2 or s[0] not in {'text','description','resource','navigation','visual','visual_scroll'} or not s[1]
               or s[0]=='navigation' and s[1]!='back' for s in selectors):
            raise ValueError('INVALID_INSPECTION_SELECTOR')
    if len(selectors) > 8:
        raise ValueError('BOUNDED_INSPECTION_REQUIRED')
    loop=SessionLoop(Config.load(args.profile), args.session, minutes=3,
        brain_factory=lambda c, s: Inspect(c, s, APPS[args.app], selectors, args.current,loop,args.read_marker))
    result=asyncio.run(loop.run())
    print(json.dumps(result))
    return 0 if result['reason'] in {'SOCIAL_UI_INSPECTED','SOCIAL_CURRENT_MESSAGE_READ_VERIFIED'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
