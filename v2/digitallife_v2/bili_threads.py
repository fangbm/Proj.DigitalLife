"""Public comment threads identified by observed UI, never invented server IDs."""
import hashlib,json,re,time,unicodedata,uuid
from pathlib import Path
from .media import BILIBILI,VideoSources
from .safety import Blocked


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text) if not c.isspace() and unicodedata.category(c)!='Cf')


def native_comment_text(text):
    return re.sub(r'\u200b\s+收起\s*$','',text).strip('\u200b\u200c\u200d ')


def native_profile_uid(raw):
    """UID is the clickable number in native profile tags, never a badge."""
    import xml.etree.ElementTree as ET
    try:root=ET.fromstring(raw)
    except ET.ParseError:return None
    ids=[]
    for parent in root.iter('node'):
        if parent.get('resource-id')!=BILIBILI+':id/bottom_tags_layout' or parent.get('package')!=BILIBILI:continue
        for node in parent:
            value=node.get('text','')
            if (node.get('package')==BILIBILI and not node.get('resource-id') and node.get('clickable')=='true'
                    and node.get('enabled')!='false' and node.get('visible-to-user')!='false' and re.fullmatch('[1-9][0-9]{4,23}',value)):ids.append(value)
    return ids[0] if len(set(ids))==1 else None


def comment_rows(ui,obs):
    names=sorted(ui.controls(obs,'user_name'),key=lambda c:c.bounds[1]);result=[]
    for i,name in enumerate(names):
        bottom=names[i+1].bounds[1] if i+1<len(names) else obs.height
        bodies=[c for c in ui.controls(obs,'primary_message') if name.bounds[1]<=c.bounds[1]<bottom]
        replies=[c for c in ui.controls(obs,'reply_button') if name.bounds[1]<=c.bounds[1]<bottom]
        if len(bodies)==1 and len(replies)==1:
            text=native_comment_text(bodies[0].text)
            result.append({'author':name.text,'text':text,
                'name_control':name,'body_control':bodies[0],'reply_control':replies[0]})
    return result


class ThreadSources:
    def __init__(self,store):self.store=store;self.db=store.db

    def bind(self,source_id,root_id,sender_id,sender_name,text,actor,observations,profile,*,parent=None,entry=None,preview_text=None):
        source=VideoSources(self.store).get(source_id)
        root=self.db.execute("SELECT o.text,b.account FROM social_outbox o JOIN social_bindings b ON b.id=o.binding_id WHERE o.id=? AND o.device=? AND o.kind='post_video_comment' AND o.status='sent_observed' AND o.trigger_id=?",(root_id,self.store.serial,source['video_id'])).fetchone()
        native_only=sender_id is None and profile is None
        if (not root or root[1]!=actor['id'] or not text or len(text)>600 or not sender_name
                or native_only and (parent is None or entry is None)
                or not native_only and (not isinstance(sender_id,str) or not re.fullmatch('[0-9]{5,24}',sender_id) or sender_id==actor['id'] or profile is None)):raise Blocked('MEDIA_PUBLIC_REPLY_THREAD_UNVERIFIED')
        sender_key='native-name:'+normalized(sender_name) if native_only else sender_id
        key=str(int(hashlib.sha256((source['video_id']+'|'+root_id+'|'+sender_key+'|'+normalized(text)).encode()).hexdigest()[:19],16))
        captures=[]
        witnesses=(parent,entry,*observations) if native_only else (parent,entry,*observations,profile) if parent is not None else (*observations,profile)
        for obs in witnesses:
            captures.append({'observation_id':obs.observation_id,'png_path':obs.png_path,'png_sha256':obs.png_sha256,'xml_path':obs.xml_path,'xml_sha256':obs.xml_sha256})
        body={'source_id':source_id,'video_id':source['video_id'],'root_outbound_id':root_id,'root_text':root[0],
            'account':actor['id'],'actor_name':actor['name'],'sender_uid':sender_id,'sender_name':sender_name,'text':text,
            'ui_thread_key':key,'identity_kind':'video_root_outbox_native_thread_and_full_text_UI_fingerprint' if native_only else 'video_root_outbox_sender_UID_and_full_text_UI_fingerprint','captures':captures}
        if parent is not None:
            body.update(parent_observation_id=parent.observation_id,entry_observation_id=entry.observation_id,preview_text=preview_text,
                incoming_observation_ids=[o.observation_id for o in observations])
        # Re-observing the same incoming text keeps the same semantic intent.
        segment=uuid.uuid4().hex
        raw=json.dumps(body,ensure_ascii=False)
        kind={'scripted':'live_scripted','autonomous':'v2_autonomous'}.get(self.store.state.get('run_kind'),'fixture')
        self.db.execute('INSERT INTO content_segments VALUES(?,?,?,?,?,?,?,?)',(segment,self.store.session,'comment_thread',kind,time.time(),raw,hashlib.sha256(raw.encode()).hexdigest(),'observed'));self.db.commit()
        self.get('thread:'+segment)
        self.store.event('media_public_reply_thread_observed',{'thread_id':'thread:'+segment,'source_id':source_id,'identity_kind':body['identity_kind']})
        return 'thread:'+segment

    def get(self,thread_id):
        try:
            if not re.fullmatch('thread:[a-f0-9]{32}',thread_id):raise ValueError()
            row=self.db.execute("SELECT c.body,c.summary FROM content_segments c JOIN sessions s ON s.id=c.session WHERE c.id=? AND c.kind='comment_thread' AND c.status='observed' AND s.serial=?",(thread_id[7:],self.store.serial)).fetchone()
            if not row or hashlib.sha256(row[0].encode()).hexdigest()!=row[1]:raise ValueError()
            body=json.loads(row[0]);VideoSources(self.store).get(body['source_id'])
            for c in body['captures']:
                for kind in ('png','xml'):
                    if hashlib.sha256(Path(c[kind+'_path']).read_bytes()).hexdigest()!=c[kind+'_sha256']:raise ValueError()
            import xml.etree.ElementTree as ET
            def xml_nodes(capture):return list(ET.fromstring(Path(capture['xml_path']).read_bytes()).iter('node'))
            def primary_texts(capture):return [normalized(native_comment_text(n.get('text',''))) for n in xml_nodes(capture) if n.get('resource-id')==BILIBILI+':id/primary_message']
            native_only=body.get('sender_uid') is None
            if native_only and (body.get('identity_kind')!='video_root_outbox_native_thread_and_full_text_UI_fingerprint' or not body.get('parent_observation_id') or len(body['captures'])!=4):raise ValueError()
            incoming=body['captures'] if native_only else body['captures'][:-1]
            if body.get('parent_observation_id'):
                by_id={c['observation_id']:c for c in body['captures']}
                parent=by_id[body['parent_observation_id']];entry=by_id[body['entry_observation_id']]
                if not any(n.get('resource-id')==BILIBILI+':id/title' and n.get('text')=='评论详情' for n in xml_nodes(entry)):raise ValueError()
                if normalized(body['root_text']) not in primary_texts(parent):raise ValueError()
                if not any(n.get('resource-id')==BILIBILI+':id/user_name' and n.get('text')==body['actor_name'] for n in xml_nodes(parent)):raise ValueError()
                previews=[n.get('text','') for n in xml_nodes(parent) if n.get('resource-id')==BILIBILI+':id/secondary_message']
                if body['preview_text'] not in previews:raise ValueError()
                names=[n.get('text','') for n in xml_nodes(entry) if n.get('resource-id')==BILIBILI+':id/user_name']
                anchor=normalized(body['preview_text'])
                if normalized(body['root_text']) not in primary_texts(entry) and not any(normalized(name+':'+message)==anchor for name in names for message in primary_texts(entry)):raise ValueError()
                incoming=[by_id[i] for i in body['incoming_observation_ids']]
                if len(incoming)!=2:raise ValueError()
                for capture in incoming:
                    ns=xml_nodes(capture)
                    if not any(n.get('resource-id')==BILIBILI+':id/title' and n.get('text')=='评论详情' for n in ns):raise ValueError()
                    if not any(n.get('resource-id')==BILIBILI+':id/user_name' and n.get('text')==body['sender_name'] for n in ns):raise ValueError()
            for capture in incoming:
                nodes=list(ET.fromstring(Path(capture['xml_path']).read_bytes()).iter('node'))
                messages=[normalized(native_comment_text(n.get('text',''))) for n in nodes if n.get('resource-id')==BILIBILI+':id/primary_message']
                if (not body.get('parent_observation_id') and normalized(body['root_text']) not in messages) or normalized(body['text']) not in messages:raise ValueError()
            if not native_only and native_profile_uid(Path(body['captures'][-1]['xml_path']).read_bytes())!=body['sender_uid']:raise ValueError()
            return {**body,'thread_id':thread_id}
        except (ValueError,KeyError,TypeError,OSError,SyntaxError):raise Blocked('MEDIA_PUBLIC_THREAD_SOURCE_CHANGED') from None

    def already_replied(self,thread):
        """An identity-decoder correction cannot authorize a second reply."""
        key=thread['video_id']+':'+thread['ui_thread_key']
        rows=self.db.execute("SELECT o.trigger_id,o.sources FROM social_outbox o JOIN social_bindings b ON b.id=o.binding_id WHERE o.device=? AND b.account=? AND b.app=? AND o.kind='reply_video_comment'",(self.store.serial,thread['account'],BILIBILI)).fetchall()
        for trigger,sources in rows:
            if trigger==key:return True
            for source in json.loads(sources):
                if not source.startswith('thread:'):continue
                row=self.db.execute("SELECT body FROM content_segments WHERE id=? AND kind='comment_thread'",(source[7:],)).fetchone()
                if not row:continue
                old=json.loads(row[0])
                if (old.get('root_outbound_id')==thread['root_outbound_id'] and old.get('source_id')==thread['source_id']
                        and old.get('account')==thread['account'] and old.get('sender_name')==thread['sender_name']
                        and old.get('sender_uid')!=thread['sender_uid'] and normalized(old.get('text',''))==normalized(thread['text'])):
                    # Raw legacy metadata is used only to prevent duplication,
                    # never as identity evidence or permission to send.
                    if thread.get('sender_uid') is None or old.get('sender_uid') is None:return True
                    try:self.get(source)
                    except Blocked:return True
        return False

    def offered(self):
        result=[];seen=set()
        for row in self.db.execute("SELECT c.id FROM content_segments c JOIN sessions s ON s.id=c.session WHERE s.serial=? AND c.kind='comment_thread' AND c.status='observed' ORDER BY c.started DESC LIMIT 6",(self.store.serial,)):
            try:
                t=self.get('thread:'+row[0]);key=t['video_id']+':'+t['ui_thread_key']
                if key in seen or self.already_replied(t):continue
                seen.add(key);result.append({k:t[k] for k in ('thread_id','source_id','sender_name','text')})
            except Blocked:continue
        return result
