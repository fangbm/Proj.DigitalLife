"""M5 video provenance and independent comment/share scopes over the M4 outbox."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from .safety import Blocked
from .social import Binding, SocialStore, CHINA, digest

BILIBILI='tv.danmaku.bili'
COMMENT_KINDS={'post_video_comment','reply_video_comment'}
MEDIA_KINDS=COMMENT_KINDS|{'group_video_share'}


def canonical_video(url):
    """Only an observed, resolved public video URL; never a model-built URL."""
    try:
        p=urlsplit(url)
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443):raise ValueError()
        if p.hostname not in {'www.bilibili.com','m.bilibili.com','bilibili.com'}:raise ValueError()
        match=re.fullmatch(r'/video/(BV[0-9A-Za-z]{10})/?',p.path)
        if not match:raise ValueError()
        return match[1],'https://www.bilibili.com/video/'+match[1]
    except (ValueError,TypeError):raise Blocked('MEDIA_PUBLIC_VIDEO_URL_UNVERIFIED') from None


async def resolve_share_url(url):
    """Follow only the public Bilibili short-link chain, without cookies/auth."""
    import asyncio
    import urllib.request
    import urllib.error
    import threading
    from urllib.parse import urljoin
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    cancelled=threading.Event()
    def resolve():
        current=url;opener=urllib.request.build_opener(NoRedirect())
        deadline=time.monotonic()+9
        for _ in range(5):
            if cancelled.is_set() or time.monotonic()>=deadline:raise Blocked('MEDIA_SHARE_RESOLUTION_CANCELLED')
            p=urlsplit(current)
            if p.scheme!='https' or p.hostname not in {'b23.tv','www.bilibili.com','m.bilibili.com','bilibili.com'} or p.username or p.password or p.port not in (None,443):
                raise Blocked('MEDIA_SHARE_REDIRECT_NOT_ALLOWED')
            try:return canonical_video(current)[1]
            except Blocked:
                if p.hostname!='b23.tv' or not re.fullmatch(r'/[A-Za-z0-9]+/?',p.path):raise
            request=urllib.request.Request(current,headers={'User-Agent':'Mozilla/5.0 DigitalLife-Video-Provenance','Range':'bytes=0-511'})
            try:
                with opener.open(request,timeout=3) as response:
                    # No script evaluation, arbitrary HTML scraping or login.
                    raise Blocked('MEDIA_SHORT_LINK_NO_PUBLIC_VIDEO_REDIRECT')
            except urllib.error.HTTPError as error:
                try:
                    if error.code not in {301,302,303,307,308} or not error.headers.get('Location'):raise Blocked('MEDIA_SHARE_RESOLUTION_FAILED')
                    current=urljoin(current,error.headers['Location'])
                finally:error.close()
        raise Blocked('MEDIA_SHARE_REDIRECT_LIMIT')
    try:return await asyncio.wait_for(asyncio.to_thread(resolve),12)
    except Blocked:raise
    except (OSError,ValueError,asyncio.TimeoutError):raise Blocked('MEDIA_SHARE_RESOLUTION_FAILED') from None
    finally:cancelled.set()


@dataclass(frozen=True)
class VideoBinding(Binding):
    def validate(self,now=None):
        self.validate_common(now)
        if self.app!=BILIBILI or self.kind not in {'video','comment_thread'}:
            raise Blocked('MEDIA_VIDEO_IDENTITY_UNVERIFIED')
        if not re.fullmatch(r'BV[0-9A-Za-z]{10}(?::[0-9]{1,24})?',self.peer):
            raise Blocked('MEDIA_VIDEO_OR_THREAD_ID_UNVERIFIED')
        if (self.kind=='comment_thread')!=(':' in self.peer):raise Blocked('MEDIA_COMMENT_THREAD_UNVERIFIED')


class VideoSources:
    """Extend an actual summarized watch segment; no new database or V1 import."""
    def __init__(self,store):self.store=store;self.db=store.db

    def offered(self,limit=6):
        rows=self.db.execute("SELECT c.id FROM content_segments c JOIN sessions s ON s.id=c.session WHERE s.serial=? AND c.kind='watch_video' AND c.status='summarized' AND c.body LIKE '%\"public_video\"%' ORDER BY c.started DESC LIMIT ?",(self.store.serial,limit)).fetchall()
        result=[]
        for row in rows:
            try:
                s=self.get('video:'+row[0]);result.append({k:s[k] for k in ('source_id','video_id','title','summary','uncertainty','source_type')})
            except Blocked:continue
        return result

    def _watch(self,segment):
        row=self.db.execute("SELECT c.body,c.summary,c.source_type FROM content_segments c JOIN sessions s ON s.id=c.session WHERE c.id=? AND s.serial=? AND c.kind='watch_video' AND c.status='summarized'",(segment,self.store.serial)).fetchone()
        try:
            if not row or row[2] not in {'live_scripted','v2_autonomous'}:raise ValueError()
            body=json.loads(row[0]);summary=json.loads(row[1]);samples=body['samples']
            if not 2<=len(samples)<=10 or body['time_range_seconds'][1]<3:raise ValueError()
            for s in samples:
                if (s['foreground'].split('/')[0]!=BILIBILI or s['redacted'] or s['incomplete']
                        or hashlib.sha256(Path(s['png_path']).read_bytes()).hexdigest()!=s['png_sha256']):raise ValueError()
            if not summary['summary'] or not summary['evidence_ids'] or not set(summary['evidence_ids'])<={s['observation_id'] for s in samples}:raise ValueError()
            return body,summary,row[2]
        except (ValueError,KeyError,TypeError,OSError):raise Blocked('MEDIA_WATCH_EVIDENCE_UNVERIFIED') from None

    def bind(self,segment,*,public_url,title,uploader,followed,captures):
        video,url=canonical_video(public_url);body,summary,source_type=self._watch(segment)
        # Captures bracket the watch and come from the real source App's share
        # UI, not from an arbitrary URL supplied by a model.
        if (not isinstance(title,str) or not title or len(title)>300 or not isinstance(uploader,str)
                or not re.fullmatch('[0-9]{1,24}',uploader) or type(followed) is not bool
                or not isinstance(captures,list) or len(captures)!=2):raise Blocked('MEDIA_SOURCE_ANCHOR_UNVERIFIED')
        for capture in captures:
            try:
                if (capture['app']!=BILIBILI or capture['kind'] not in {'native_share','native_video_identity'}
                        or canonical_video(capture['url'])[0]!=video or capture['title']!=title
                        or not capture['observation_id']
                        or hashlib.sha256(Path(capture['png_path']).read_bytes()).hexdigest()!=capture['png_sha256']):raise ValueError()
                if capture['kind']=='native_video_identity':
                    if hashlib.sha256(Path(capture['xml_path']).read_bytes()).hexdigest()!=capture['xml_sha256']:raise ValueError()
                    root=ET.fromstring(Path(capture['xml_path']).read_bytes())
                    ids=[re.match(r'^(BV[0-9A-Za-z]{10})(?:\s|$)',n.get('text','').strip()) for n in root.iter('node') if n.get('resource-id')==BILIBILI+':id/desc']
                    if len(ids)!=1 or not ids[0] or ids[0][1]!=video:raise ValueError()
            except (ValueError,KeyError,TypeError,OSError,ET.ParseError):raise Blocked('MEDIA_SHARE_SOURCE_UNVERIFIED') from None
        normalize=lambda s:re.sub(r'\s+','',s)
        if any(not any(normalize(title) in normalize(t) or video in t for t in sample['texts']) for sample in (body['samples'][0],body['samples'][-1])):
            raise Blocked('MEDIA_WATCH_CHANGED_OR_TITLE_NOT_OBSERVED')
        anchor={'video_id':video,'public_url':url,'title':title,'uploader':uploader,'followed':followed,
            'captures':captures,'summary_sha256':digest(json.dumps(summary,ensure_ascii=False,sort_keys=True)),
            'source_type':source_type}
        if body.get('public_video') and body['public_video']!=anchor:raise Blocked('MEDIA_SOURCE_ALREADY_BOUND_DIFFERENTLY')
        body['public_video']=anchor
        self.db.execute('UPDATE content_segments SET body=? WHERE id=?',(json.dumps(body,ensure_ascii=False),segment));self.db.commit()
        self.store.event('media_source_bound',{'segment_id':segment,'video_id':video,'source_type':source_type})
        return 'video:'+segment

    def get(self,source):
        try:return self._get(source)
        except Blocked:raise
        except (KeyError,TypeError,OSError,ValueError,ET.ParseError):raise Blocked('MEDIA_SOURCE_UNVERIFIED') from None

    def _get(self,source):
        if not isinstance(source,str) or not re.fullmatch(r'video:[a-f0-9]{32}',source):raise Blocked('MEDIA_SOURCE_NOT_OFFERED')
        body,summary,_=self._watch(source[6:]);anchor=body.get('public_video')
        if not anchor or anchor['summary_sha256']!=digest(json.dumps(summary,ensure_ascii=False,sort_keys=True)):
            raise Blocked('MEDIA_SOURCE_CHANGED')
        for c in anchor['captures']:
            if hashlib.sha256(Path(c['png_path']).read_bytes()).hexdigest()!=c['png_sha256']:raise Blocked('MEDIA_SOURCE_CHANGED')
            if c['kind']=='native_video_identity':
                if hashlib.sha256(Path(c['xml_path']).read_bytes()).hexdigest()!=c['xml_sha256']:raise Blocked('MEDIA_SOURCE_CHANGED')
                root=ET.fromstring(Path(c['xml_path']).read_bytes())
                ids=[re.match(r'^(BV[0-9A-Za-z]{10})(?:\s|$)',n.get('text','').strip()) for n in root.iter('node') if n.get('resource-id')==BILIBILI+':id/desc']
                if len(ids)!=1 or not ids[0] or ids[0][1]!=anchor['video_id']:raise Blocked('MEDIA_SOURCE_CHANGED')
        if (canonical_video(anchor['public_url'])[0]!=anchor['video_id']
                or any(c['title']!=anchor['title'] or canonical_video(c['url'])[0]!=anchor['video_id'] for c in anchor['captures'])):
            raise Blocked('MEDIA_SOURCE_CHANGED')
        return {**anchor,'source_id':source,'summary':summary['summary'],'uncertainty':summary.get('uncertainty','')}


class MediaPolicy:
    def __init__(self,config):self.config=config;self.path=config.data/'media.private.json'

    def read(self):
        if not self.path.exists():return {'enabled':False}
        try:
            if self.path.stat().st_size>65536:raise ValueError()
            value=json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('version')!=1 or value.get('serial')!=self.config.serial:raise ValueError()
            if any(not isinstance(value.get(k,{}),dict) for k in ('accounts','comments','shares','limits')):raise ValueError()
            return value
        except (ValueError,TypeError,OSError,AttributeError):raise Blocked('MEDIA_POLICY_INVALID') from None

    def check(self,binding,kind,now=None):
        now=time.time() if now is None else now;binding.validate(now);value=self.read()
        if value.get('enabled') is not True:raise Blocked('MEDIA_DISABLED')
        if kind not in MEDIA_KINDS:raise Blocked('MEDIA_ACTION_KIND_MISMATCH')
        if value.get('accounts',{}).get(binding.app)!=binding.account:raise Blocked('MEDIA_ACCOUNT_CHANGED_OR_UNBOUND')
        if kind in COMMENT_KINDS:
            expected='video' if kind=='post_video_comment' else 'comment_thread'
            if not isinstance(binding,VideoBinding) or binding.kind!=expected:raise Blocked('MEDIA_COMMENT_THREAD_UNVERIFIED')
            if value.get('comments',{}).get(kind) is not True:raise Blocked('MEDIA_COMMENT_NOT_ALLOWED')
            if value.get('comments',{}).get('following_only',True) and not binding.existing_contact:raise Blocked('MEDIA_UPLOADER_NOT_FOLLOWED')
        else:
            shares=value.get('shares',{});key=binding.app+'/'+binding.peer
            if (binding.kind!='group' or not binding.existing_contact or shares.get('group_video_share') is not True
                    or not isinstance(shares.get('allowed_groups',[]),list) or key not in shares.get('allowed_groups',[])):
                raise Blocked('MEDIA_GROUP_SHARE_NOT_ALLOWED')
        quiet=value.get('quiet_hours',['23:00','08:00'])
        if not isinstance(quiet,list) or len(quiet)!=2 or any(not isinstance(t,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',t) for t in quiet):raise Blocked('MEDIA_QUIET_HOURS_INVALID')
        local=datetime.fromtimestamp(now,CHINA).strftime('%H:%M');start,end=quiet
        if start!=end and ((start<=local<end) if start<end else local>=start or local<end):raise Blocked('MEDIA_QUIET_HOURS')
        hard={'hour':10,'day':50,'conversation_day':5,'cooldown_seconds':30,'comment_account_day':3,'video_shares_group_day':1}
        result={}
        for key,default in hard.items():
            n=value.get('limits',{}).get(key,default)
            if type(n) is not int or n<1:raise Blocked('MEDIA_LIMITS_INVALID')
            result[key]=max(n,default) if key=='cooldown_seconds' else min(n,default)
        from .social import SocialPolicy
        existing=SocialPolicy(self.config).read().get('limits',{})
        for key in ('hour','day','conversation_day'):
            n=existing.get(key,result[key])
            if type(n) is not int or n<1:raise Blocked('MEDIA_SHARED_LIMITS_INVALID')
            result[key]=min(result[key],n)
        return result

    def targets(self,store):
        value=self.read()
        if value.get('enabled') is not True or value.get('shares',{}).get('group_video_share') is not True:return []
        allowed=value.get('shares',{}).get('allowed_groups',[]);result=[]
        for bid,app,account,peer,body in store.db.execute("SELECT id,app,account,peer,body FROM social_bindings WHERE device=? AND kind='group' ORDER BY updated DESC",(store.serial,)):
            if value.get('accounts',{}).get(app)==account and app+'/'+peer in allowed:
                result.append({'binding_id':bid,'app':app,'kind':'group','label':json.loads(body)['route_hint'],'scope':'video_share_requires_fresh_group_UI'})
        return result[:8]


class MediaStore(SocialStore):
    def reconcile_comment(self,outbound_id,binding,source_id,evidence,*,reply=False):
        """Explicit read-only review of one earlier attempt; never re-dispatch."""
        row=self.db.execute('SELECT binding_id,kind,status,attempted,text_hash,sources,evidence FROM social_outbox WHERE id=? AND device=?',(outbound_id,self.store.serial)).fetchone()
        bid=digest(self.store.serial+'\0'+'\0'.join(binding.identity))
        source=VideoSources(self.store).get(source_id)
        if (not row or row[0]!=bid or row[1]!=('reply_video_comment' if reply else 'post_video_comment') or row[2]!='delivery_unknown' or row[3] is None
                or source_id not in json.loads(row[5]) or binding.peer.split(':')[0]!=source['video_id']
                or evidence.get('text_sha256')!=row[4] or not evidence.get('actor_label_observed')
                or not evidence.get('composer_closed') or not evidence.get('new_own_reply_exact_text' if reply else 'new_comment_exact_normalized_OCR')
                or not evidence.get('png_sha256') or not evidence.get('original_before_observation_id')):
            raise Blocked('MEDIA_UNKNOWN_REVIEW_EVIDENCE_INSUFFICIENT')
        if reply:
            from .bili_threads import ThreadSources
            threads=[s for s in json.loads(row[5]) if s.startswith('thread:')]
            if len(threads)!=1 or not evidence.get('same_original_parent'):raise Blocked('MEDIA_REPLY_THREAD_CHANGED')
            thread=ThreadSources(self.store).get(threads[0])
            if binding.peer!=source['video_id']+':'+thread['ui_thread_key']:raise Blocked('MEDIA_REPLY_THREAD_CHANGED')
        binding.validate(row[3])
        proof={**evidence,'original_unknown_evidence':json.loads(row[6] or '{}'),'review_session':self.store.session}
        cursor=self.db.execute("UPDATE social_outbox SET status='sent_observed',observed=?,evidence=? WHERE id=? AND device=? AND status='delivery_unknown'",(time.time(),json.dumps(proof),outbound_id,self.store.serial))
        self.db.commit()
        if cursor.rowcount!=1:raise Blocked('MEDIA_REVIEW_STATE_CHANGED')
        self.store.event('media_unknown_resolved_by_independent_UI_review',{'outbound_id':outbound_id,'evidence':proof,'dispatch_replayed':False})
        return {'outcome':'sent_observed','outbound_id':outbound_id,'source_id':source_id,'review_only':True}

    def check_thread(self,binding_id,limits,now,exclude=None):
        super().check_thread(binding_id,limits,now,exclude)
        binding=self.db.execute('SELECT app,account,kind FROM social_bindings WHERE id=?',(binding_id,)).fetchone()
        if not binding:return
        if binding[2] in {'video','comment_thread'}:
            count=self.db.execute("SELECT count(*) FROM social_outbox o JOIN social_bindings b ON b.id=o.binding_id WHERE o.device=? AND b.app=? AND b.account=? AND o.kind IN ('post_video_comment','reply_video_comment') AND o.created>? AND o.id!=? AND o.status!='cancelled_before_send'",(self.store.serial,*binding[:2],now-86400,exclude or '')).fetchone()[0]
            if count>=limits['comment_account_day']:raise Blocked('MEDIA_ACCOUNT_COMMENT_LIMIT')
        elif binding[2]=='group':
            count=self.db.execute("SELECT count(*) FROM social_outbox WHERE device=? AND binding_id=? AND kind='group_video_share' AND created>? AND id!=? AND status!='cancelled_before_send'",(self.store.serial,binding_id,now-86400,exclude or '')).fetchone()[0]
            if count>=limits['video_shares_group_day']:raise Blocked('MEDIA_GROUP_VIDEO_SHARE_LIMIT')

    def prepare_media(self,policy,binding,kind,source_id,text,motive,*,thread_sources=()):
        source=VideoSources(self.store).get(source_id)
        if kind in COMMENT_KINDS:
            if binding.peer.split(':')[0]!=source['video_id']:raise Blocked('MEDIA_SOURCE_THREAD_MISMATCH')
            if policy.read().get('comments',{}).get('following_only',True) and not source['followed']:raise Blocked('MEDIA_UPLOADER_NOT_FOLLOWED')
        if kind=='reply_video_comment' and not thread_sources:raise Blocked('MEDIA_REPLY_CONTEXT_MISSING')
        if kind=='reply_video_comment':
            from .bili_threads import ThreadSources
            if len(thread_sources)!=1:raise Blocked('MEDIA_REPLY_CONTEXT_MISSING')
            thread=ThreadSources(self.store).get(thread_sources[0])
            if (thread['source_id']!=source_id or thread['account']!=binding.account
                    or binding.peer!=source['video_id']+':'+thread['ui_thread_key']):raise Blocked('MEDIA_REPLY_THREAD_CHANGED')
        trigger=source['video_id']+(':'+binding.peer.split(':')[1] if kind=='reply_video_comment' else '')
        return self.prepare(policy,binding,kind,trigger,text,motive,[source_id,*thread_sources])

    def before_media_send(self,policy,binding,outbound_id):
        row=self.db.execute('SELECT sources,kind FROM social_outbox WHERE id=? AND device=?',(outbound_id,self.store.serial)).fetchone()
        if not row:raise Blocked('MEDIA_SEND_NOT_PREPARED')
        sources=[s for s in json.loads(row[0]) if s.startswith('video:')]
        if len(sources)!=1:raise Blocked('MEDIA_SOURCE_UNVERIFIED')
        VideoSources(self.store).get(sources[0])
        if row[1]=='reply_video_comment':
            from .bili_threads import ThreadSources
            threads=[s for s in json.loads(row[0]) if s.startswith('thread:')]
            if len(threads)!=1:raise Blocked('MEDIA_REPLY_CONTEXT_MISSING')
            thread=ThreadSources(self.store).get(threads[0])
            if binding.peer!=thread['video_id']+':'+thread['ui_thread_key']:raise Blocked('MEDIA_REPLY_THREAD_CHANGED')
        return self.before_send(policy,binding,outbound_id)
