"""Read-only, bounded UI evidence. Unknown attributes stay unknown, never safe defaults."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .models import utc_now
from .progress import ActionProgressDetector


class SemanticObservationProvider(Protocol):
    async def collect(self, device, observation) -> 'SemanticObservation': ...


@dataclass(frozen=True)
class Control:
    control_id: str
    bounds: tuple[int, int, int, int]
    text: str
    content_description: str
    class_name: str
    resource_id: str
    package: str
    clickable: bool | None
    scrollable: bool | None
    enabled: bool | None
    checkable: bool | None
    editable: bool | None
    visible: bool | None
    password: bool | None
    long_clickable: bool | None
    parent: str | None
    children: tuple[str, ...]
    evidence_source: str
    window_id: int
    structural_path: str = ''

    def identity(self):
        # Android window IDs are allocated per UiAutomation connection, not page identities.
        # Preserve them in the audit, use path/properties/window checks for cross-session comparison.
        return {k: v for k, v in asdict(self).items() if k not in {'control_id', 'parent', 'children', 'window_id'}}


@dataclass(frozen=True)
class SemanticObservation:
    observation_id: str
    screenshot_sha256: str
    screen_width: int
    screen_height: int
    orientation: int | None
    foreground_package: str
    foreground_activity: str
    provider: str
    collected_at: str
    controls: tuple[Control, ...] = ()
    scrollable_regions: tuple[str, ...] = ()
    overlays: tuple[dict, ...] = ()
    evidence_quality: str = 'semantic_unavailable'
    failure_reason: str = ''
    attempts: tuple[dict, ...] = ()
    tree_sha256: str = ''
    content_sha256: str = ''
    raw: dict = field(default_factory=dict, repr=False, compare=False)
    stability: tuple[dict, ...] = ()
    private_frames: tuple = field(default_factory=tuple, repr=False, compare=False)

    def audit(self):
        return {k:v for k,v in asdict(self).items() if k not in {'raw','private_frames'}}


def foreground_parts(value):
    match = re.search(r'\b([A-Za-z_][\w]*(?:\.[\w]+)+)/([\w.$]+)', value)
    return match.groups() if match else (value.strip(), '')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def semantic_content_progress(before, after):
    """Secondary progress signal, never a substitute for stable distributed pixel change."""
    from .semantic_stability import trusted, dynamic_regions
    if not trusted(before) or not trusted(after):
        return {'reliable':False,'changed':False,'reason':'SEMANTIC_PROGRESS_UNAVAILABLE'}
    def visible(s):
        dynamic={r['control_id'] for r in dynamic_regions(s)}
        return {(c.resource_id,c.text,c.content_description,c.bounds) for c in s.controls if c.visible is True
            and c.control_id not in dynamic
            and (c.text or c.content_description) and c.bounds[1]>=s.screen_height*.12
            and c.bounds[3]<=s.screen_height*.92 and not any(t in c.class_name for t in ('ProgressBar','Chronometer'))}
    left,right=visible(before),visible(after)
    return {'reliable':True,'changed':left!=right,'reason':'VISIBLE_CONTENT_CHANGED' if left!=right else 'VISIBLE_CONTENT_UNCHANGED',
        'content_before_sha256':digest(sorted(left)),'content_after_sha256':digest(sorted(right))}


def normalize(raw, obs, foreground, source):
    """Validate provider data rather than coercing missing safety attributes."""
    if not isinstance(raw, dict) or raw.get('rotation') not in {0, 1, 2, 3}:
        raise ValueError('ROTATION_UNAVAILABLE')
    if (raw.get('width'), raw.get('height')) != (obs.width, obs.height):
        raise ValueError('DIMENSIONS_MISMATCH')
    package, activity = foreground_parts(foreground)
    controls, windows = [], raw.get('windows')
    if not isinstance(windows, list) or not 1 <= len(windows) <= 20:
        raise ValueError('WINDOWS_UNAVAILABLE')
    overlays, application_roots = [], 0
    for window in windows:
        if (type(window.get('id')) is not int or type(window.get('type')) is not int or
                type(window.get('active')) is not bool):
            raise ValueError('INVALID_WINDOW_ATTRIBUTES')
        # Status/navigation bars may be ignored only with exact system package/type identity.
        root = window.get('root', {})
        wb=window.get('bounds',[])
        if (window.get('type') == 3 and root.get('package') == 'com.android.systemui' and
                isinstance(wb,list) and len(wb)==4 and all(type(n) is int for n in wb) and
                (0<=wb[1]<=wb[3]<=obs.height*.12 or obs.height*.92<=wb[1]<=wb[3]<=obs.height)):
            continue
        if window.get('type') != 1 or root.get('package') != package or not window.get('active'):
            overlays.append({'type': window.get('type'), 'package': root.get('package'), 'bounds': window.get('bounds')})
        else:
            application_roots += 1
        def visit(node, parent=None, depth=0):
            if depth > 60 or len(controls) >= 1800:
                raise ValueError('NODE_LIMIT')
            bounds = node.get('bounds')
            if (not isinstance(bounds, (list, tuple)) or len(bounds) != 4 or
                    any(type(n) is not int for n in bounds) or
                    ((bounds[0] > bounds[2] or bounds[1] > bounds[3]) and node.get('visible') is not False)):
                raise ValueError('INVALID_BOUNDS')
            attrs = {}
            for key in ('clickable','scrollable','enabled','checkable','editable','visible','password','long_clickable'):
                value = node.get(key)
                if value is not None and type(value) is not bool:
                    raise ValueError('INVALID_ATTRIBUTE')
                attrs[key] = value
            strings = {}
            for key in ('text','content_description','class_name','resource_id','package'):
                value = node.get(key, '')
                if not isinstance(value, str) or len(value) > 4000:
                    raise ValueError('INVALID_NODE_TEXT')
                strings[key] = value
            path = node.get('path')
            if not isinstance(path, str) or len(path) > 500:
                raise ValueError('INVALID_NODE_PATH')
            cid = digest([obs.observation_id, window['id'], path])[:24]
            index = len(controls); controls.append(None)
            children = tuple(visit(child, cid, depth+1) for child in node.get('children', []))
            controls[index] = Control(cid, tuple(bounds), **strings, **attrs, parent=parent,
                children=children, evidence_source=source, window_id=window['id'],
                structural_path='/'.join(path.split('/')[1:]))
            return cid
        visit(root)
    if application_roots != 1:
        overlays.append({'reason': 'ACTIVE_APPLICATION_WINDOW_COUNT', 'count': application_roots})
    if len({c.control_id for c in controls})!=len(controls):
        raise ValueError('DUPLICATE_CONTROL_PATH')
    if any(c.package!=package and c.visible is not False for c in controls):
        overlays.append({'reason':'FOREIGN_APPLICATION_CONTENT'})
    quality = 'trusted' if not overlays and all(c.editable is not None and c.visible is not None for c in controls) else 'partial'
    tree = digest([c.identity() for c in controls])
    content = hashlib.sha256(ActionProgressDetector()._body(obs)[0]).hexdigest()
    return SemanticObservation(obs.observation_id, obs.sha256, obs.width, obs.height, raw['rotation'],
        package, activity, source, utc_now(), tuple(controls), tuple(c.control_id for c in controls if c.scrollable is True),
        tuple(overlays), quality, '' if quality == 'trusted' else 'INCOMPLETE_ATTRIBUTES_OR_OVERLAY',
        tree_sha256=tree, content_sha256=content, raw=raw)


class UIAutomatorBackend:
    name, timeout = 'uiautomator_xml', 14

    async def read(self, phone, obs):
        xml = await phone.hierarchy()
        if len(xml) > 2_000_000 or '<!DOCTYPE' in xml or '<!ENTITY' in xml:
            raise ValueError('UNSAFE_XML')
        root = ET.fromstring(xml)
        def node(element, path):
            value = element.attrib
            b = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', value.get('bounds',''))
            output = {'path': path, 'bounds': list(map(int,b.groups())) if b else [],
                **{k: value.get(v,'') for k,v in {'text':'text','content_description':'content-desc',
                    'resource_id':'resource-id','package':'package','class_name':'class'}.items()}}
            for key in ('clickable','scrollable','enabled','checkable','editable','visible','password','long_clickable'):
                val = value.get(key.replace('_','-'))
                if key == 'visible':
                    # Alias only when explicitly serialized. Existence in a dump is
                    # never visibility evidence, including on OEM UIAutomator builds.
                    alias = value.get('visible-to-user')
                    if val is not None and alias is not None and val != alias:
                        raise ValueError('CONFLICTING_VISIBILITY_ATTRIBUTES')
                    val = val if val is not None else alias
                if val is not None and val not in {'true','false'}:
                    raise ValueError('INVALID_ATTRIBUTE')
                output[key] = {'true': True, 'false': False}.get(val)
            output['children'] = [node(c,path+'/'+str(i)) for i,c in enumerate(element)]
            return output
        children = list(root)
        if len(children) != 1:
            raise ValueError('AMBIGUOUS_XML_WINDOWS')
        return {'width':obs.width,'height':obs.height,'rotation':int(root.get('rotation','-1')),
            'windows':[{'id':0,'type':1,'active':True,'root':node(children[0],'w0')} ]}


class ShellSnapshotBackend:
    name, timeout = 'shell_ui_snapshot/1', 6

    def __init__(self):
        self.asset = Path(__file__).parent/'assets'/'ui-snapshot.jar'
        self.sha = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        self.remote = '/data/local/tmp/digitallife-ui-'+self.sha+'.jar'

    async def prepare(self, phone):
        if phone.kind != 'emulator' or await phone._adb('shell','getprop','ro.kernel.qemu') != '1':
            raise ValueError('Helper deployment restricted to isolated QEMU emulators')
        await phone._adb('push',str(self.asset.resolve()),self.remote,timeout=10)
        await self.check(phone)
        return {'backend':self.name,'asset_sha256':self.sha,'android_permissions_granted':0,
                'apk_installed':False,'operation':'deploy_readonly_shell_artifact'}

    async def check(self, phone):
        value = await phone._adb('shell','sha256sum',self.remote,timeout=3)
        if value.split()[0] != self.sha:
            raise ValueError('SHELL_ARTIFACT_HASH_MISMATCH')

    async def read(self, phone, obs):
        await self.check(phone)
        nonce = uuid.uuid4().hex
        raw = await phone._adb('shell','CLASSPATH='+self.remote+':/system/framework/uiautomator.jar',
            'app_process','/system/bin','org.digitallife.ReadOnlySnapshot',nonce,timeout=4)
        if len(raw) > 2_000_000 or raw.count('DIGITALLIFE_SNAPSHOT=') != 1:
            raise ValueError('INVALID_SHELL_SNAPSHOT')
        data = json.loads(raw.split('DIGITALLIFE_SNAPSHOT=',1)[1])
        if data.get('nonce') != nonce or data.get('schema') != 'shell-ui-snapshot/1':
            raise ValueError('SNAPSHOT_NONCE_MISMATCH')
        return data


class BoundedSemanticProvider:
    """At most two backends, each timed; no device input on failure. Collect under caller's lease."""
    def __init__(self, backends=None):
        self.backends = backends if backends is not None else [UIAutomatorBackend(),ShellSnapshotBackend()]
        if not 1 <= len(self.backends) <= 2:
            raise ValueError('At most two semantic backends')
        self.cooldown = {}

    async def collect(self, device, observation):
        from dataclasses import replace
        from .semantic_stability import compare, dynamic_regions, stable_pixels
        attempts = []
        samples, frames, rejected_overlays, frame_audits = [], [], [], []
        for backend in self.backends:
            started = time.monotonic_ns()
            if self.cooldown.get(backend.name,0) > time.monotonic():
                attempts.append({'backend':backend.name,'reason':'BOUNDED_FAILURE_COOLDOWN','elapsed_ns':0})
                continue
            try:
                async def capture():
                    fg = await device.foreground()
                    raw = await backend.read(device,observation)
                    samples.append({'backend':backend.name,'collected_at':utc_now(),'tree':raw})
                    semantic = normalize(raw,observation,fg,backend.name)
                    rejected_overlays.extend(semantic.overlays)
                    if semantic.evidence_quality != 'trusted':
                        raise ValueError(semantic.failure_reason)
                    # Two independently acquired trees detect transient/missing/changed controls even
                    # when a screenshot happens to match. Exclude only identified system bars.
                    second_raw=await backend.read(device,observation)
                    samples.append({'backend':backend.name,'collected_at':utc_now(),'tree':second_raw})
                    second = normalize(second_raw,observation,fg,backend.name)
                    rejected_overlays.extend(second.overlays)
                    fresh = await device.observe()
                    frames.append(fresh)
                    frame_audits.append({'index':len(frames)-1,'backend':backend.name,**fresh.metadata()})
                    if await device.foreground() != fg:
                        raise ValueError('FOREGROUND_ACTIVITY_CHANGED')
                    stability=compare(semantic,second)
                    if (fresh.width,fresh.height) != (observation.width,observation.height):
                        raise ValueError('SCREEN_ROTATED_OR_RESIZED')
                    regions=dynamic_regions(semantic)
                    from PIL import Image
                    import io
                    with Image.open(io.BytesIO(observation.png)) as old_image, Image.open(io.BytesIO(fresh.png)) as new_image:
                        stability=tuple({**r,'before_region_sha256':hashlib.sha256(old_image.crop(r['bounds']).tobytes()).hexdigest(),
                            'after_region_sha256':hashlib.sha256(new_image.crop(r['bounds']).tobytes()).hexdigest(),
                            'allowed_pixel_change':'only_within_explicit_2px_padded_uptime_bounds'} for r in stability)
                    if stable_pixels(fresh,regions) != stable_pixels(observation,regions):
                        raise ValueError('SCREEN_TREE_INCONSISTENT')
                    if semantic.evidence_quality != 'trusted':
                        raise ValueError(semantic.failure_reason)
                    raw={**raw,'_collection':{'samples':samples,'frames':frame_audits,'fresh_screenshot_sha256':fresh.sha256,
                        'original_screenshot_sha256':observation.sha256,'stability':stability,'completed_at':utc_now()}}
                    return replace(semantic,raw=raw,stability=stability,private_frames=tuple(frames),
                        evidence_quality='trusted_dynamic_noninteractive' if regions else 'trusted',
                        content_sha256=hashlib.sha256(stable_pixels(observation,regions)).hexdigest())
                semantic = await asyncio.wait_for(capture(),backend.timeout)
            except (RuntimeError,ValueError,OSError,TimeoutError,ET.ParseError,KeyError,TypeError,RecursionError) as error:
                reason = 'BACKEND_TIMEOUT' if isinstance(error,TimeoutError) else str(error)[:160]
                attempts.append({'backend':backend.name,'reason':reason,'error_type':type(error).__name__,
                                 'elapsed_ns':time.monotonic_ns()-started})
                # Acquisition failures cool down for 60s; transient frame/structure races for 1s.
                # The number of captures is unchanged, and a cooling backend is never bypassed.
                transient=reason.startswith(('TREE_OR_ROTATION_CHANGED','SCREEN_TREE_INCONSISTENT',
                                             'FOREGROUND_ACTIVITY_CHANGED','SCREEN_ROTATED_OR_RESIZED','DYNAMIC_REGION_CHANGED'))
                self.cooldown[backend.name] = time.monotonic()+(1 if transient else 60)
                continue
            attempts.append({'backend':backend.name,'reason':'OK','elapsed_ns':time.monotonic_ns()-started})
            semantic=replace(semantic,attempts=tuple(attempts))
            from .semantic_storage import journal_collection
            journal_collection(semantic)
            return semantic
        semantic=SemanticObservation(observation.observation_id,observation.sha256,observation.width,observation.height,
            None,'','','none',utc_now(),failure_reason='semantic_unavailable',attempts=tuple(attempts),
            overlays=tuple(rejected_overlays),raw={'_collection':{'samples':samples,'frames':frame_audits,'completed_at':utc_now()}},private_frames=tuple(frames))
        from .semantic_storage import journal_collection
        journal_collection(semantic)
        return semantic
