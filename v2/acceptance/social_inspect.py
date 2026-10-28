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

APPS = {'qq': 'com.tencent.mobileqq', 'wechat': 'com.tencent.mm', 'bilibili':'tv.danmaku.bili'}


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
            if field=='media':
                if self.app=='com.tencent.mobileqq' and value=='qq_group_search':
                    from digitallife_v2.native_share import NativeShareUI
                    await NativeShareUI(self.loop).search_qq_group()
                elif self.app=='tv.danmaku.bili' and value=='bili_comment_context':
                    from digitallife_v2.bilibili import BilibiliUI
                    from digitallife_v2.media import VideoSources,MediaPolicy
                    from digitallife_v2.social import digest
                    sources=VideoSources(self.store).offered()
                    if not sources:raise SessionStopped('MEDIA_WATCHED_SOURCE_REQUIRED')
                    source=VideoSources(self.store).get(sources[0]['source_id'])
                    binding,context,_=await BilibiliUI(self.loop).resolve_comment(source)
                    MediaPolicy(self.loop.config).check(binding,'post_video_comment')
                    write_json(self.store.directory/'M5_CONTEXT_READY.json',{'source':'LIVE_SELF_UID_WATCHED_VIDEO_COMMENT_PANEL_NO_SEND',
                        'source_id':source['source_id'],'account_sha256':digest(binding.account),'outgoing_sends':0})
                elif self.app=='tv.danmaku.bili' and value=='bili_comment_controls':
                    from digitallife_v2.bilibili import BilibiliUI
                    from digitallife_v2.input_bridge import InputBridge
                    from digitallife_v2.social import digest
                    ui=BilibiliUI(self.loop);obs=await ui.observe()
                    if not ui.controls(obs,'edit'):obs=await ui.focus_comment(obs)
                    controller=InputBridge(self.loop)
                    async with controller.temporary():
                        obs=await ui.observe();await controller.observe_editor(obs)
                        if obs.input_editor.get('text_sha256')!=digest(''):raise SessionStopped('MEDIA_EXISTING_DRAFT_NOT_TOUCHED')
                        await ui.check_dynamic_off(obs);await ui.publish_control(obs)
                        write_json(self.store.directory/'M5_COMPOSER_CONTROL_READY.json',{'source':'LIVE_EMPTY_COMMENT_COMPOSER_READ_ONLY',
                            'dynamic_repost_verified_off':True,'publication_attempted':False,'outgoing_sends':0})
                        await ui.act({'type':'back'},obs)
                elif self.app=='tv.danmaku.bili' and value=='bili_reply_controls':
                    from digitallife_v2.bilibili import BilibiliUI
                    from digitallife_v2.bili_threads import comment_rows
                    ui=BilibiliUI(self.loop);obs=await ui.observe();rows=comment_rows(ui,obs)
                    if len(rows)<2:raise SessionStopped('MEDIA_REPLY_ROW_REQUIRED')
                    await ui.act({'type':'tap','control_id':rows[-1]['reply_control'].control_id},obs)
                elif self.app=='tv.danmaku.bili' and value=='bili_reply_context':
                    from digitallife_v2.bilibili import BilibiliUI
                    from digitallife_v2.media import VideoSources
                    source=VideoSources(self.store).get(VideoSources(self.store).offered()[0]['source_id'])
                    thread,_,_=await BilibiliUI(self.loop).inspect_replies(source)
                    write_json(self.store.directory/'M5_THREAD_READY.json',{'thread_id':thread['thread_id'],'source_id':source['source_id'],'source':'ACTUAL_PUBLIC_REPLY_PARENT_AND_SENDER_UID','outgoing_sends':0})
                elif self.app in {'tv.danmaku.bili','com.tencent.mobileqq'} and value=='native_qq_preview':
                    from digitallife_v2.media import MediaPolicy,VideoSources
                    from digitallife_v2.native_share import NativeShareUI
                    source=VideoSources(self.store).get(VideoSources(self.store).offered()[0]['source_id'])
                    targets=[x for x in MediaPolicy(self.loop.config).targets(self.store) if x['app']=='com.tencent.mobileqq']
                    if len(targets)!=1:raise SessionStopped('MEDIA_SINGLE_QQ_TARGET_REQUIRED')
                    _,obs=await NativeShareUI(self.loop).prepare_qq(source,targets[0]['binding_id'])
                    self.app='com.tencent.mobileqq'
                elif self.app=='tv.danmaku.bili' and value=='native_wx_picker':
                    from digitallife_v2.media import VideoSources
                    from digitallife_v2.native_share import NativeShareUI
                    source=VideoSources(self.store).get(VideoSources(self.store).offered()[0]['source_id'])
                    await NativeShareUI(self.loop).open_wechat_picker(source)
                    self.app='com.tencent.mm'
                elif self.app in {'tv.danmaku.bili','com.tencent.mm','com.tencent.mobileqq'} and value=='native_wx_preview':
                    from digitallife_v2.media import MediaPolicy,VideoSources
                    from digitallife_v2.native_share import NativeShareUI
                    source=VideoSources(self.store).get(VideoSources(self.store).offered()[0]['source_id'])
                    targets=[x for x in MediaPolicy(self.loop.config).targets(self.store) if x['app']=='com.tencent.mm']
                    if len(targets)!=1:raise SessionStopped('MEDIA_SINGLE_WECHAT_TARGET_REQUIRED')
                    await NativeShareUI(self.loop).prepare_wechat(source,targets[0]['binding_id'])
                    self.app='com.tencent.mm'
                else:raise SessionStopped('MEDIA_INSPECTION_KIND_NOT_ALLOWED')
                self.stage+=1
                return {'intent':'rest','action':{'type':'rest','seconds':1},'note':'Explicit read-only M5 UI inspection, no publication or recipient send'}
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
        if any(len(s) != 2 or s[0] not in {'text','description','resource','navigation','visual','visual_scroll','media'} or not s[1]
               or s[0]=='navigation' and s[1]!='back' for s in selectors):
            raise ValueError('INVALID_INSPECTION_SELECTOR')
    if len(selectors) > 8:
        raise ValueError('BOUNDED_INSPECTION_REQUIRED')
    minutes=10 if any(field=='media' for field,_ in selectors) else 3
    loop=SessionLoop(Config.load(args.profile), args.session, minutes=minutes,
        brain_factory=lambda c, s: Inspect(c, s, APPS[args.app], selectors, args.current,loop,args.read_marker))
    result=asyncio.run(loop.run())
    print(json.dumps(result))
    return 0 if result['reason'] in {'SOCIAL_UI_INSPECTED','SOCIAL_CURRENT_MESSAGE_READ_VERIFIED'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
