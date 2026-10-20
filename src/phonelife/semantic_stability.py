"""Evidence-based stability rules; no model-controlled masks or safety coercion."""
import hashlib
import io
import re
import json
from PIL import Image, ImageDraw

TRUSTED_QUALITIES = frozenset({'trusted', 'trusted_dynamic_noninteractive'})
ABOUT_ACTIVITIES=frozenset({'.SubSettings','com.android.settings.SubSettings',
    '.Settings$MyDeviceInfoActivity','com.android.settings.Settings$MyDeviceInfoActivity'})
ABOUT_TITLES=frozenset({'About emulated device','About phone','关于手机','关于模拟设备'})


def trusted(semantic):
    return semantic is not None and semantic.evidence_quality in TRUSTED_QUALITIES and not semantic.overlays


def is_about_page(semantic):
    # Settings preference navigation actually uses SubSettings with a fragment. Activity alone
    # is insufficient: require the exact owned, visible, noninteractive About header as well.
    return (semantic.foreground_package=='com.android.settings' and semantic.foreground_activity in ABOUT_ACTIVITIES
        and any(c.package=='com.android.settings' and c.visible is True and c.enabled is True
            and c.resource_id=='com.android.settings:id/collapsing_toolbar'
            and c.class_name=='android.widget.FrameLayout'
            and (c.text in ABOUT_TITLES or c.content_description in ABOUT_TITLES)
            and all(getattr(c,k) is False for k in ('clickable','checkable','editable','password','long_clickable'))
            for c in semantic.controls))


def dynamic_regions(semantic):
    """One API-35 Settings display proven by title, class, ancestry and all safety flags.

    This intentionally does not generalize to arbitrary animations or numeric labels.
    Unknown/nonmatching displays retain exact tree and pixel consistency requirements.
    """
    if not is_about_page(semantic):
        return ()
    nodes = {c.control_id: c for c in semantic.controls}
    result = []
    for c in semantic.controls:
        if (c.resource_id != 'android:id/summary' or c.class_name != 'android.widget.TextView'
                or c.children or not re.fullmatch(r'\d{1,6}:\d{2}:\d{2}', c.text)
                or c.content_description or c.visible is not True or c.enabled is not True):
            continue
        if any(getattr(c,k) is not False for k in ('clickable','scrollable','checkable','editable','password','long_clickable')):
            continue
        parent = nodes.get(c.parent)
        if not parent or any(getattr(parent,k) is not False for k in ('clickable','checkable','editable','password','long_clickable')):
            continue
        siblings = [nodes[x] for x in parent.children]
        if not any(n.resource_id == 'android:id/title' and n.text == 'Uptime' and n.visible is True
                   and n.clickable is False and n.editable is False and n.password is False for n in siblings):
            continue
        # A clickable ancestor would make this a potential action surface rather than display.
        ancestor = parent
        while ancestor:
            if any(getattr(ancestor,k) is not False for k in ('clickable','checkable','editable','password','long_clickable')):
                break
            ancestor = nodes.get(ancestor.parent)
        if ancestor:
            continue
        a,b,r,d = c.bounds
        bounds = (a-2,b-2,r+2,d+2)
        if not (0 <= bounds[0] < bounds[2] <= semantic.screen_width and
                semantic.screen_height*.12 <= bounds[1] < bounds[3] <= semantic.screen_height*.92):
            continue
        if (r-a+4)*(d-b+4) > semantic.screen_width*semantic.screen_height*.01:
            continue
        result.append({'control_id':c.control_id,'structural_path':c.structural_path,'bounds':bounds,
                       'basis':'settings_api35_about_header+uptime_summary+noninteractive_ancestors+exact_safety_flags',
                       'ignored_fields':['text'],'involves_interactive_control':False})
    return tuple(result) if len(result) == 1 else ()


def structure(semantic):
    dynamic = {r['structural_path'] for r in dynamic_regions(semantic)}
    values=[]
    for c in semantic.controls:
        value=c.identity()
        if c.structural_path in dynamic:
            value={**value,'text':'<verified-uptime-display>'}
        values.append(value)
    # Session-allocated Android window IDs are retained raw, compared by logical window identity.
    windows=[{k:w.get(k) for k in ('type','active','bounds')} | {'root_package':w.get('root',{}).get('package'),
        'root_class':w.get('root',{}).get('class_name')} for w in semantic.raw.get('windows',[])]
    return {'controls':values,'windows':windows}


def stable_pixels(observation, regions=()):
    with Image.open(io.BytesIO(observation.png)) as source:
        im=source.convert('RGB')
    if im.size != (observation.width,observation.height):
        raise ValueError('INVALID_IMAGE_DIMENSIONS')
    draw=ImageDraw.Draw(im)
    for r in regions:
        a,b,c,d=r['bounds'];draw.rectangle((a,b,c-1,d-1),fill=(0,0,0))
    # Match the established excluded system/edge regions, without downsampling masking other changes.
    return im.crop((int(im.width*.05),int(im.height*.08),int(im.width*.95),int(im.height*.92))).tobytes()


def compare(before, after):
    for s in (before,after):
        actual=hashlib.sha256(json.dumps([c.identity() for c in s.controls],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if actual!=s.tree_sha256:
            raise ValueError('TREE_OR_ROTATION_CHANGED: invalid_tree_digest')
    keys=('foreground_package','foreground_activity','orientation','screen_width','screen_height')
    if any(getattr(before,k)!=getattr(after,k) for k in keys):
        raise ValueError('TREE_OR_ROTATION_CHANGED: context')
    if not trusted(before) or not trusted(after) or structure(before)!=structure(after):
        raise ValueError('TREE_OR_ROTATION_CHANGED: structure_or_safety')
    left,right=dynamic_regions(before),dynamic_regions(after)
    if [(r['structural_path'],r['bounds']) for r in left] != [(r['structural_path'],r['bounds']) for r in right]:
        raise ValueError('DYNAMIC_REGION_CHANGED')
    old={c.structural_path:c for c in before.controls};new={c.structural_path:c for c in after.controls}
    return tuple({**r,'before_text_sha256':hashlib.sha256(old[r['structural_path']].text.encode()).hexdigest(),
                  'after_text_sha256':hashlib.sha256(new[r['structural_path']].text.encode()).hexdigest(),
                  'change_type':'noninteractive_text' if old[r['structural_path']].text!=new[r['structural_path']].text else 'none',
                  'consistency':'verified'} for r in left)
