"""Non-granting, fail-closed Android 16 native metadata reader.

The named constants are from android16-release WindowManager.LayoutParams.
This is a versioned dumpsys grammar, not hardware/compositor attestation. Raw
metadata and identities must remain private. Public results use window indexes.
"""
from __future__ import annotations

import hashlib
import re

TYPE_VERSION = 'aosp-android16-layoutparams/1'
WINDOW_TYPES = {
    'BASE_APPLICATION': 1, 'APPLICATION': 2, 'APPLICATION_STARTING': 3,
    'DRAWN_APPLICATION': 4, 'STATUS_BAR': 2000, 'SYSTEM_ALERT': 2003,
    'KEYGUARD': 2004, 'TOAST': 2005, 'SYSTEM_OVERLAY': 2006,
    'SYSTEM_DIALOG': 2008, 'KEYGUARD_DIALOG': 2009, 'INPUT_METHOD': 2011,
    'INPUT_METHOD_DIALOG': 2012, 'WALLPAPER': 2013, 'NAVIGATION_BAR': 2019,
    'VOLUME_OVERLAY': 2020, 'NAVIGATION_BAR_PANEL': 2024,
    'DISPLAY_OVERLAY': 2026, 'ACCESSIBILITY_OVERLAY': 2032,
    'APPLICATION_OVERLAY': 2038, 'NOTIFICATION_SHADE': 2040,
}
LOCK_FIELDS = ('mShowingLockscreen', 'mKeyguardShowing', 'isKeyguardShowing', 'keyguardShowing')
BOOL = r'(true|false)(?=$|[\s,;}])'


class NativeMetadataError(ValueError):
    pass


def window_type(token):
    """Exact constant or exact decimal value; no guessed OEM ranges."""
    if not isinstance(token, str):
        raise NativeMetadataError('INVALID_WINDOW_TYPE')
    name = token.removeprefix('TYPE_')
    if name in WINDOW_TYPES:
        return WINDOW_TYPES[name]
    if re.fullmatch(r'[1-9][0-9]*', token) and int(token) in WINDOW_TYPES.values():
        return int(token)
    raise NativeMetadataError('UNKNOWN_WINDOW_TYPE')


def _booleans(text, names):
    values, invalid = [], False
    for name in names:
        declarations = list(re.finditer(r'(?<![\w])' + re.escape(name) + r'\s*[=:]\s*([^\s,;}]+)', text))
        for match in declarations:
            token = match[1]
            if token not in {'true', 'false'}:
                invalid = True
            else:
                values.append(token == 'true')
    return values, invalid


def lock_state(policy, displays):
    """Read only explicit declarations, with delegate-local generic fields.

    Global render-completion fields are not lock state. Positive/conflicting
    lock evidence wins; malformed/absent evidence never becomes unlocked.
    """
    values, invalid = _booleans(policy, LOCK_FIELDS)
    delegate = re.search(r'^(\s*)KeyguardServiceDelegate\s*:?\s*$', policy, re.M)
    if delegate:
        indent = len(delegate[1].split('\n')[-1])
        lines = []
        for line in policy[delegate.end():].splitlines():
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                break
            lines.append(line)
        extra, bad = _booleans('\n'.join(lines), ('showing', 'mIsShowing'))
        values.extend(extra); invalid |= bad
    extra, bad = _booleans(displays, LOCK_FIELDS)
    values.extend(extra); invalid |= bad
    # Validate completion syntax independently; its value grants no trust.
    _, bad = _booleans(displays, ('mKeyguardDrawComplete',))
    invalid |= bad
    result = True if any(values) else None if invalid or not values else False
    return {'locked': result, 'conflict': len(set(values)) > 1,
            'invalid': invalid, 'declarations': len(values)}


def interactive_state(power):
    values, bad = _booleans(power, ('mInteractive',))
    wakes = re.findall(r'(?<!\w)mWakefulness\s*=\s*([^\s,;}]+)', power)
    for wake in wakes:
        if wake not in {'Awake', 'Asleep', 'Dreaming', 'Dozing'}:
            bad = True
        else:
            values.append(wake == 'Awake')
    return values[0] if values and len(set(values)) == 1 and not bad else None


def _one(pattern, text, code):
    values = re.findall(pattern, text, re.M)
    if len(values) != 1:
        raise NativeMetadataError(code)
    return values[0]


def _bool(text, name):
    values, invalid = _booleans(text, (name,))
    if invalid or len(values) != 1:
        raise NativeMetadataError('MISSING_DUPLICATE_OR_INVALID_BOOLEAN')
    return values[0]


def _rect(value):
    m = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', value)
    if not m:
        raise NativeMetadataError('INVALID_WINDOW_BOUNDS')
    rect = tuple(map(int, m.groups()))
    if rect[2] < rect[0] or rect[3] < rect[1]:
        raise NativeMetadataError('INVALID_WINDOW_BOUNDS')
    return rect


def _attrs(block):
    """Only the direct mAttrs object, excluding rotated nested mAttrs."""
    _one(r'^    mAttrs=(\{[^\n]*)', block, 'MISSING_OR_DUPLICATE_ATTRIBUTES')
    at = re.search(r'^    mAttrs=', block, re.M).end()
    depth = 0
    direct = []
    for pos, ch in enumerate(block[at:]):
        if ch == '{':
            depth += 1
            if depth == 2: direct.append(' ')
        if ch == '}':
            depth -= 1
            if depth == 0:
                return ''.join(direct)
        if depth == 1 and ch not in '{}':
            direct.append(ch)
    raise NativeMetadataError('TRUNCATED_ATTRIBUTES')


def parse_windows(text):
    if text.count('WINDOW MANAGER WINDOWS (dumpsys window windows)') != 1:
        raise NativeMetadataError('UNTRUSTED_WINDOWS_HEADER')
    heads = list(re.finditer(r'^  Window #(\d+) Window\{([0-9a-f]+) u\d+ ([^\n}]+)\}:\s*$', text, re.M))
    if not heads or [int(h[1]) for h in heads] != list(range(len(heads))):
        raise NativeMetadataError('WINDOW_ORDER_OR_TRUNCATION')
    if len(re.findall(r'^  Window #', text, re.M)) != len(heads):
        raise NativeMetadataError('MALFORMED_WINDOW_HEADER')
    footer = re.search(r'^  mGlobalConfiguration=', text[heads[-1].end():], re.M)
    if not footer or 'mSystemBooted=true' not in text[heads[-1].end():]:
        raise NativeMetadataError('MISSING_WINDOWS_FOOTER')
    end = heads[-1].end() + footer.start()
    rows = []
    for index, head in enumerate(heads):
        block = text[head.end():heads[index+1].start() if index+1 < len(heads) else end]
        # Footer sections (e.g. "Hiding non-system overlay windows") can dump
        # another WindowState with mAttrs. They are not indexed windows.
        boundary = re.search(r'^ {0,2}\S', block, re.M)
        if boundary:
            block = block[:boundary.start()]
        attrs = _attrs(block)
        token = _one(r'(?<!\w)ty=([^\s}]+)', attrs, 'MISSING_OR_DUPLICATE_TYPE')
        try:
            typ, type_error = window_type(token), None
        except NativeMetadataError as error:
            typ, type_error = None, str(error)
        pkg = _one(r'(?<!\w)package=([\w.]+)', block, 'MISSING_OR_DUPLICATE_OWNER')
        row = {'index': index, 'id': head[2], 'package': pkg,
               'type': typ, 'type_error': type_error,
               'display': int(_one(r'\bmDisplayId=(\d+)', block, 'MISSING_DISPLAY')),
               'uid': int(_one(r'\bmOwnerUid=(\d+)', block, 'MISSING_OWNER_UID')),
               'view': int(_one(r'\bmViewVisibility=(0x[0-9a-fA-F]+)', block, 'MISSING_VISIBILITY'), 16),
               'frame': _rect(_one(r'^    Frames:[^\n]*\bframe=(\[-?\d+,-?\d+\]\[-?\d+,-?\d+\])', block, 'MISSING_FRAME')),
               'base_layer': int(_one(r'\bmBaseLayer=(-?\d+)', block, 'MISSING_LAYER')),
               'sub_layer': int(_one(r'\bmSubLayer=(-?\d+)', block, 'MISSING_LAYER'))}
        for field, key in [('mHasSurface', 'surface'), ('isReadyForDisplay()', 'ready'),
                           ('isVisibleRequested()', 'requested'), ('isOnScreen', 'on_screen'),
                           ('isVisible', 'visible'), ('mLastHidden', 'last_hidden'), ('mObscured', 'obscured')]:
            row[key] = _bool(block, field)
        surface_lines = '\n'.join(line for line in block.splitlines() if re.match(r'^\s+Surface:', line))
        shown, malformed = _booleans(surface_lines, ('shown',))
        if malformed or len(shown) > 1 or (len(shown) != 1 and row['surface']):
            raise NativeMetadataError('MISSING_OR_DUPLICATE_SURFACE_SHOWN')
        row['shown'] = shown[0] if len(shown) == 1 else False
        if row['view'] not in {0, 4, 8}:
            raise NativeMetadataError('INVALID_VIEW_VISIBILITY')
        rows.append(row)
    if len({r['id'] for r in rows}) != len(rows):
        raise NativeMetadataError('DUPLICATE_WINDOW_ID')
    return rows


def parse_input(text):
    if text.count('Input Dispatcher State:') != 1:
        raise NativeMetadataError('AMBIGUOUS_INPUT_DISPATCHER')
    section = _one(r'Input Dispatcher State:([\s\S]*?)mMaximumObscuringOpacityForTouch:', text, 'INCOMPLETE_INPUT_DISPATCHER')
    if not re.search(r'^\s+DispatchEnabled: true\s*$', section, re.M) or not re.search(r'^\s+DispatchFrozen: false\s*$', section, re.M):
        raise NativeMetadataError('INPUT_DISPATCH_UNVERIFIED')
    if _one(r'^\s+FocusedDisplayId: (\d+)\s*$', section, 'MISSING_FOCUSED_DISPLAY') != '0':
        raise NativeMetadataError('MULTI_DISPLAY_UNVERIFIED')
    focus_section = _one(r'FocusedWindows:([\s\S]*?)FocusRequests:', section, 'MISSING_INPUT_FOCUS')
    focus = _one(r"displayId=0, name='([0-9a-f]+) [^'\n]+'", focus_section, 'MISSING_INPUT_FOCUS')
    body = _one(r'^\s{4}Windows:([\s\S]*)', section, 'MISSING_INPUT_WINDOWS')
    entries = re.findall(r'^\s{6}(\d+): name=([^\n]+)', body, re.M)
    if not entries or [int(n) for n, _ in entries] != list(range(len(entries))):
        raise NativeMetadataError('INPUT_WINDOW_ORDER')
    rows = []
    for index, line in entries:
        flags = _one(r'inputConfig=([A-Z_ |]+),', line, 'INVALID_INPUT_CONFIG')
        known = {'NO_INPUT_CHANNEL', 'NOT_VISIBLE', 'NOT_FOCUSABLE', 'NOT_TOUCHABLE',
                 'DUPLICATE_TOUCH_TO_WALLPAPER', 'TRUSTED_OVERLAY', 'SPY',
                 'WATCH_OUTSIDE_TOUCH', 'SLIPPERY', 'WALLPAPER', 'IS_WALLPAPER', 'PREVENT_SPLITTING'}
        if not set(flags.split(' | ')) <= known:
            raise NativeMetadataError('UNKNOWN_INPUT_CONFIG')
        rows.append({'index': int(index), 'id': re.match(r'([0-9a-f]+) ', line)[1] if re.match(r'([0-9a-f]+) ', line) else None,
                     'flags': set(flags.split(' | ')),
                     'uid': int(_one(r'ownerUid=(\d+)', line, 'MISSING_INPUT_OWNER')),
                     'display': int(_one(r'\bdisplayId=(\d+)', line, 'MISSING_INPUT_DISPLAY')),
                     'frame': _rect(_one(r'\bframe=(\[-?\d+,-?\d+\]\[-?\d+,-?\d+\])', line, 'MISSING_INPUT_FRAME')),
                     'alpha': float(_one(r'\balpha=([0-9.]+),', line, 'MISSING_INPUT_ALPHA'))})
        if not 0 <= rows[-1]['alpha'] <= 1:
            raise NativeMetadataError('INVALID_INPUT_ALPHA')
    return focus, rows


def assess_native(metadata):
    """Complete grammar gate; no device identity, lease or capture authorization.

    SAFE is a window-only assessment. Never bypass the outer admission gate.
    Unrecognized OEM types remain blocking even when reporting no surface.
    """
    if not isinstance(metadata, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in metadata.items()):
        return {'schema': 'native-readonly-windows/1', 'classification': 'INSUFFICIENT_WINDOW_EVIDENCE',
                'reason_codes': ['MALFORMED_METADATA_SOURCE'], 'windows': [],
                'source_sha256': {}, 'physical_input_allowed': False}
    result = {'schema': 'native-readonly-windows/1', 'classification': 'INSUFFICIENT_WINDOW_EVIDENCE',
              'reason_codes': [], 'windows': [], 'physical_input_allowed': False,
              'source_sha256': {k: hashlib.sha256(v.encode()).hexdigest() for k, v in metadata.items()}}
    errors = result['reason_codes']
    try:
        if not all(k in metadata for k in ('power', 'policy', 'windows', 'displays', 'input')):
            raise NativeMetadataError('MISSING_METADATA_SOURCE')
        power, policy, windows, displays, inp = (metadata[k] for k in ('power', 'policy', 'windows', 'displays', 'input'))
        if 'WINDOW MANAGER POLICY STATE (dumpsys window policy)' not in policy or 'WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)' not in displays or 'POWER MANAGER (dumpsys power)' not in power:
            raise NativeMetadataError('UNTRUSTED_METADATA_HEADER')
        state = lock_state(policy, displays)
        result['keyguard'] = state
        if state['locked'] is not False or state['conflict'] or state['invalid']:
            errors.append('KEYGUARD_STATE_UNVERIFIED_OR_LOCKED')
        if lock_state(policy, '')['locked'] is not False or lock_state('', displays)['locked'] is not False:
            errors.append('INDEPENDENT_KEYGUARD_SOURCE_MISSING')
        if interactive_state(power) is not True:
            errors.append('INTERACTIVE_STATE_UNVERIFIED')
        rotation = re.findall(r'Viewport INTERNAL:[^\r\n]*\bdisplayId=0,[^\r\n]*\borientation=([0-3])\b', inp)
        if not rotation or len(set(rotation)) != 1:
            raise NativeMetadataError('ORIENTATION_UNVERIFIED')
        width, height = map(int, _one(r'\bcur=(\d+)x(\d+)', displays.split('WINDOW MANAGER DISPLAY CONTENTS (EXT)')[0], 'DISPLAY_GEOMETRY_UNVERIFIED'))
        if width < 1 or height < 1 or width > 16384 or height > 16384:
            raise NativeMetadataError('INVALID_DISPLAY_GEOMETRY')
        rows = parse_windows(windows)
        if any(r['type_error'] for r in rows):
            errors.append('UNKNOWN_WINDOW_TYPE')
        display_focus = _one(r'mCurrentFocus=Window\{([0-9a-f]+) u\d+ com\.android\.settings/[^}\n]+\}', displays, 'DISPLAY_FOCUS_UNVERIFIED')
        focus_id = display_focus
        # Android 16 puts the authoritative focus in display contents; older
        # output may repeat it in windows. A repetition must agree exactly.
        if 'mCurrentFocus=' in windows:
            focus_id = _one(r'mCurrentFocus=Window\{([0-9a-f]+) u\d+ com\.android\.settings/[^}\n]+\}', windows, 'SETTINGS_NOT_FOCUSED')
        obscuring = _one(r'mObscuringWindow=Window\{([0-9a-f]+) u\d+ com\.android\.settings/[^}\n]+\}', displays, 'OBSCURING_WINDOW_UNVERIFIED')
        input_focus, inputs = parse_input(inp)
        if len({focus_id, display_focus, obscuring, input_focus}) != 1:
            raise NativeMetadataError('FOCUS_SOURCE_CONFLICT')
        settings = [r for r in rows if r['id'] == focus_id and r['package'] == 'com.android.settings' and r['type'] in {1, 2}]
        if len(settings) != 1:
            raise NativeMetadataError('SETTINGS_WINDOW_UNVERIFIED')
        foreground = settings[0]
        if foreground['frame'] != (0, 0, width, height) or foreground['obscured']:
            errors.append('SPLIT_SCREEN_OR_OBSCURED_SETTINGS')
        if not all(foreground[k] for k in ('surface', 'ready', 'requested', 'on_screen', 'visible', 'shown')) or foreground['last_hidden'] or foreground['view'] != 0:
            errors.append('FOREGROUND_NOT_ACTUALLY_SHOWN')
        matched_ids = set()
        visible_denied = False
        for row in rows:
            input_rows = [r for r in inputs if r['id'] == row['id']]
            matched_ids.add(row['id'])
            if len(input_rows) > 1 or (input_rows and (input_rows[0]['uid'] != row['uid'] or input_rows[0]['display'] != row['display'])):
                errors.append('WINDOW_INPUT_OWNER_CONFLICT')
            handle = input_rows[0] if len(input_rows) == 1 else None
            flags = handle['flags'] if handle else set()
            inactive = not row['surface'] and not row['on_screen'] and not row['visible'] and not row['ready'] and not row['shown']
            hidden = (not row['shown'] and not row['on_screen'] and not row['ready'] and not row['requested'] and row['last_hidden'] and handle is not None and 'NOT_VISIBLE' in flags and {'NOT_TOUCHABLE', 'NOT_FOCUSABLE'} <= flags)
            visible = row['shown'] and row['on_screen'] and row['ready']
            overlap = max(row['frame'][0], 0) < min(row['frame'][2], width) and max(row['frame'][1], 0) < min(row['frame'][3], height)
            classification = 'UNVERIFIED'
            if row['display'] != 0: errors.append('MULTI_DISPLAY_UNVERIFIED')
            if row['type_error']: errors.append(row['type_error'])
            if row['id'] == focus_id:
                classification = 'FOCUSED_SETTINGS'
                if not handle or 'NOT_VISIBLE' in flags or 'NOT_TOUCHABLE' in flags or 'NO_INPUT_CHANNEL' in flags or handle['frame'] != row['frame'] or handle['alpha'] != 1:
                    errors.append('SETTINGS_INPUT_TARGET_UNVERIFIED')
            elif (inactive and (not handle or 'NOT_VISIBLE' in flags)) or hidden:
                classification = 'NONDISPLAYED_WITH_INPUT_CORROBORATION' if handle else 'NO_SURFACE_NO_INPUT_HANDLE'
            elif row['type'] in {2000, 2019} and row['package'] == 'com.android.systemui' and visible and handle and 'NOT_FOCUSABLE' in flags and 'TRUSTED_OVERLAY' in flags and handle['uid'] < 10000:
                x1, y1, x2, y2 = row['frame']
                edge = x1 == 0 and x2 == width and ((row['type'] == 2000 and y1 == 0 and 0 < y2 <= height // 10) or (row['type'] == 2019 and y2 == height and height * 9 // 10 <= y1 < height))
                if edge and handle['frame'] == row['frame']:
                    classification = 'BOUNDED_SYSTEM_BAR'
                else: errors.append('SYSTEM_BAR_GEOMETRY_UNVERIFIED')
            else:
                errors.append('VISIBLE_WINDOW_NOT_APPROVED' if visible else 'WINDOW_VISIBILITY_UNVERIFIED')
                visible_denied |= visible
            result['windows'].append({'index': row['index'], 'type': row['type'], 'owner_category': 'settings' if row['package'] == 'com.android.settings' else 'systemui' if row['package'] == 'com.android.systemui' else 'other_application',
                'classification': classification, 'frame': row['frame'], 'overlaps_settings': overlap,
                'same_base_layer_as_settings': row['base_layer'] == foreground['base_layer'],
                'wms_list_relation': 'before_settings' if row['index'] < foreground['index'] else 'after_settings' if row['index'] > foreground['index'] else 'self',
                'surface_shown': row['shown'], 'is_on_screen': row['on_screen'], 'is_visible': row['visible'],
                'visible_requested': row['requested'], 'ready_for_display': row['ready'], 'last_hidden': row['last_hidden'],
                'input_handle_found': handle is not None, 'input_not_visible': 'NOT_VISIBLE' in flags,
                'input_not_touchable': 'NOT_TOUCHABLE' in flags, 'input_no_channel': 'NO_INPUT_CHANNEL' in flags})
        # Unmatched compositor/input layers cannot be assumed harmless, including
        # OEM spy monitors. Do not infer their ownership from their names.
        for row in inputs:
            if row['id'] not in matched_ids and 'NOT_VISIBLE' not in row['flags']:
                errors.append('UNATTRIBUTED_INPUT_LAYER')
        if any(r['display'] != 0 for r in inputs): errors.append('MULTI_DISPLAY_UNVERIFIED')
        if visible_denied: result['classification'] = 'BLOCKED_BY_VISIBLE_WINDOW'
        elif not errors: result['classification'] = 'SAFE_FOR_READONLY_CAPTURE'
    except (NativeMetadataError, ValueError, TypeError) as error:
        errors.append(str(error) if isinstance(error, NativeMetadataError) else 'MALFORMED_NATIVE_METADATA')
    result['reason_codes'] = sorted(set(errors))
    return result


def readonly_capture_gate(profile, identity, metadata, control_status, *, connected):
    """Intersect window trust with explicit identity and existing safety facts.

    The caller must supply freshly read, read-only control/lease facts. This
    returns a recommendation only. It never creates a capture/worker permit.
    """
    scene = assess_native(metadata)
    reasons = list(scene['reason_codes'])
    try:
        profile.verify(profile.serial, identity)
    except ValueError:
        reasons.append('PHYSICAL_IDENTITY_OR_CONSENT_UNVERIFIED')
    if connected is not True: reasons.append('DEVICE_NOT_CONNECTED')
    if control_status.get('safety_evidence_readable') is not True: reasons.append('SAFETY_EVIDENCE_UNAVAILABLE')
    if control_status.get('requires_manual_review') is not False: reasons.append('MANUAL_REVIEW_UNVERIFIED')
    if control_status.get('emergency_stopped') is not False: reasons.append('EMERGENCY_STOP_UNVERIFIED')
    if control_status.get('device_lease_free') is not True: reasons.append('DEVICE_LEASE_NOT_FREE')
    return {'state': 'READY_FOR_READONLY_CAPTURE_APPROVAL' if not reasons and scene['classification'] == 'SAFE_FOR_READONLY_CAPTURE' else 'INSUFFICIENT_EVIDENCE',
            'reason_codes': sorted(set(reasons)), 'window_assessment': scene,
            'raw_capture_authorized': False, 'physical_input_allowed': False, 'worker_launch_allowed': False}


def assess_xml(xml, *, width, height, rotation, observed_ns, now_ns, before_scene, after_scene):
    """Offline contract preparation only. Native XML lacks trusted execution
    semantics; even a structurally complete fixture cannot authorize input.
    No dynamic text is ignored, and no helper is deployed by this function.
    """
    import xml.etree.ElementTree as ET
    errors = []
    try:
        if len(xml) > 2_000_000 or '<!DOCTYPE' in xml or '<!ENTITY' in xml:
            raise NativeMetadataError('UNSAFE_UI_XML')
        root = ET.fromstring(xml)
        if root.tag != 'hierarchy' or root.get('rotation') != str(rotation): errors.append('XML_ROTATION_UNVERIFIED')
        if width < 1 or height < 1: errors.append('XML_GEOMETRY_UNVERIFIED')
        if not isinstance(observed_ns, int) or not isinstance(now_ns, int) or not 0 <= now_ns-observed_ns <= 2_000_000_000:
            errors.append('XML_STALE_OR_UNTIMED')
        if not before_scene or before_scene != after_scene: errors.append('XML_SCENE_CHANGED')
        nodes = list(root.iter('node'))
        if not nodes: errors.append('XML_EMPTY')
        for node in nodes:
            if node.get('package') != 'com.android.settings': errors.append('XML_WINDOW_OWNER_UNVERIFIED')
            for key in ('clickable', 'enabled', 'focusable', 'password', 'checkable', 'visible-to-user', 'editable'):
                if node.get(key) not in {'true', 'false'}: errors.append('XML_SAFETY_ATTRIBUTE_MISSING_OR_INVALID')
            rect = _rect(node.get('bounds', ''))
            if not (0 <= rect[0] <= rect[2] <= width and 0 <= rect[1] <= rect[3] <= height): errors.append('XML_BOUNDS_UNVERIFIED')
            if node.get('text') or node.get('content-desc'): errors.append('XML_DYNAMIC_TEXT_UNVERIFIED')
    except (NativeMetadataError, ET.ParseError, TypeError, ValueError):
        errors.append('XML_MALFORMED_OR_UNTRUSTED')
    return {'compatibility': 'INSUFFICIENT_EVIDENCE' if errors else 'PARTIAL',
            'reason_codes': sorted(set(errors)), 'trusted_for_physical_input': False,
            'real_coloros_png_xml': 'NOT_OBSERVED'}


def _canonical_hash(value):
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _window_graph_rows(text):
    rows = parse_windows(text)
    heads = list(re.finditer(r'^  Window #\d+ Window\{[^\n]+', text, re.M))
    for i, row in enumerate(rows):
        block = text[heads[i].end():heads[i+1].start() if i+1 < len(heads) else len(text)]
        block = re.split(r'^ {0,2}\S', block, maxsplit=1, flags=re.M)[0]
        attrs = _attrs(block)
        row['raw_type'] = _one(r'\bty=([^\s}]+)', attrs, 'MISSING_TYPE')
        row['pid'] = int(_one(r'\bmSession=Session\{\S+\s+(\d+):', block, 'WINDOW_PID_UNVERIFIED'))
        flags = re.findall(r'\bfl=([A-Z_ ]+)', attrs)
        if len(flags) != 1: raise NativeMetadataError('WINDOW_FLAGS_UNVERIFIED')
        row['flags'] = flags[0].strip().split()
        alpha = re.findall(r'(?<!\w)alpha=([0-9]+(?:\.[0-9]+)?)(?=$|\s)', attrs)
        if len(alpha) != len(re.findall(r'(?<!\w)alpha=',attrs)) or len(alpha) > 1: raise NativeMetadataError('INVALID_OR_DUPLICATE_WINDOW_ALPHA')
        row['alpha'] = float(alpha[0]) if alpha else 1.0  # LayoutParams default.
        if not 0 <= row['alpha'] <= 1: raise NativeMetadataError('INVALID_WINDOW_ALPHA')
        surfaces = re.findall(r'^\s+mSurface=Surface\(name=[^\n]+\)/@0x([0-9a-f]+)\s*$', block, re.M)
        if len(surfaces) != (1 if row['surface'] else 0): raise NativeMetadataError('SURFACE_IDENTITY_UNVERIFIED')
        row['surface_id'] = surfaces[0] if surfaces else None
    ids = [r['surface_id'] for r in rows if r['surface_id'] is not None]
    if len(set(ids)) != len(ids): raise NativeMetadataError('SHARED_OR_DUPLICATE_SURFACE')
    return rows


def _input_consensus(text):
    """Compare every complete ColorOS Dispatcher section; never select a winner.

    AOSP InputConfig IS_WALLPAPER is a flag, not a WindowManager type alias.
    WindowInfo token is NOT a unique window identity; name+owner association and
    independent handles are required. Duplicate names/IDs are ambiguous.
    """
    sections = re.findall(r'Input Dispatcher State:[\s\S]*?mMaximumObscuringOpacityForTouch:[^\n]*', text)
    if not 1 <= len(sections) <= 2 or len(sections) != text.count('Input Dispatcher State:'):
        raise NativeMetadataError('INCOMPLETE_OR_EXCESS_DISPATCHER_SECTIONS')
    projections = []
    for section in sections:
        focus, rows = parse_input(section)
        lines = re.findall(r'^\s{6}\d+: name=([^\n]+)', section, re.M)
        for row, line in zip(rows, lines):
            row['handle_id'] = int(_one(r'(?<![\w.])id=(\d+)(?=,|$)', line, 'INPUT_HANDLE_ID_UNVERIFIED'))
            row['pid'] = int(_one(r'\bownerPid=(\d+)', line, 'INPUT_PID_UNVERIFIED'))
            row['token'] = _one(r'(?<![\w.])token=(0x[0-9a-f]+)(?=,|$)', line, 'INPUT_CHANNEL_UNVERIFIED')
            row['region'] = _one(r'touchableRegion=(<empty>|\[-?\d+,-?\d+\]\[-?\d+,-?\d+\])(?=,|$)', line, 'INPUT_REGION_UNVERIFIED')
            row['flags'] = sorted(row['flags'])
        nonnull = [r['id'] for r in rows if r['id'] is not None]
        if len(nonnull) != len(set(nonnull)): raise NativeMetadataError('AMBIGUOUS_INPUT_WINDOW_NAME')
        if len({r['handle_id'] for r in rows}) != len(rows): raise NativeMetadataError('DUPLICATE_INPUT_HANDLE_ID')
        projections.append({'focus': focus, 'rows': rows})
    if any(p != projections[0] for p in projections):
        raise NativeMetadataError('DISPATCHER_SECTION_CONFLICT')
    return projections[0]['focus'], projections[0]['rows'], len(sections)


def assess_window_graph(metadata):
    """B2 snapshot-scoped observation assessment, never an execution permission.

    B1 assess_native remains strict. Unknown types are NOT normalized or added to
    its global type map. Only an individual dormant instance can be classified
    using no Surface + no input handle + independent noninteraction flags.
    A two-sample gate is additionally required; one graph never authorizes PNG.
    """
    result = {'schema': 'native-window-graph/2', 'classification': 'INSUFFICIENT_WINDOW_EVIDENCE',
              'reason_codes': [], 'windows': [], 'unassociated_inputs': [],
              'raw_capture_authorized': False, 'physical_input_allowed': False,
              'surface_evidence': 'window_state_animator_report', 'surfaceflinger_independent_sample': 'NOT_OBSERVED'}
    errors = result['reason_codes']
    try:
        if not isinstance(metadata, dict) or any(not isinstance(v, str) for v in metadata.values()):
            raise NativeMetadataError('MALFORMED_METADATA_SOURCE')
        result['source_sha256'] = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in metadata.items()}
        power, policy, windows, displays, inp = (metadata[k] for k in ('power','policy','windows','displays','input'))
        if any(header not in body for body, header in [(power,'POWER MANAGER (dumpsys power)'),(policy,'WINDOW MANAGER POLICY STATE (dumpsys window policy)'),(displays,'WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)')]):
            raise NativeMetadataError('UNTRUSTED_METADATA_HEADER')
        state = lock_state(policy, displays)
        if state['locked'] is not False or state['invalid'] or state['conflict'] or lock_state(policy,'')['locked'] is not False or lock_state('',displays)['locked'] is not False:
            errors.append('KEYGUARD_UNVERIFIED_OR_LOCKED')
        if interactive_state(power) is not True: errors.append('INTERACTIVE_UNVERIFIED')
        rotations = re.findall(r'Viewport INTERNAL:[^\r\n]*\bdisplayId=0,[^\r\n]*\borientation=([0-3])\b',inp)
        if not rotations or len(set(rotations)) != 1: raise NativeMetadataError('ORIENTATION_UNVERIFIED')
        width, height = map(int,_one(r'\bcur=(\d+)x(\d+)',displays.split('WINDOW MANAGER DISPLAY CONTENTS (EXT)')[0],'DISPLAY_GEOMETRY_UNVERIFIED'))
        if not (1 <= width <= 16384 and 1 <= height <= 16384): raise NativeMetadataError('INVALID_DISPLAY_GEOMETRY')
        rows = _window_graph_rows(windows)
        focus, inputs, count = _input_consensus(inp)
        result['dispatcher_sections'] = count
        result['dispatcher_consensus'] = True
        def display_id(field):
            return _one(field+r'=Window\{([0-9a-f]+) u\d+ [^}\n]+\}',displays,'DISPLAY_FOCUS_UNVERIFIED')
        if focus != display_id('mCurrentFocus') or focus != display_id('mObscuringWindow'):
            errors.append('FOCUS_SOURCE_CONFLICT')
        if 'mCurrentFocus=' in windows and focus != _one(r'mCurrentFocus=Window\{([0-9a-f]+) u\d+ [^}\n]+\}',windows,'WINDOW_FOCUS_UNVERIFIED'):
            errors.append('FOCUS_SOURCE_CONFLICT')
        focused = [r for r in rows if r['id'] == focus]
        if len(focused) != 1 or focused[0]['package'] != 'com.android.settings' or focused[0]['type'] not in {1,2}:
            errors.append('SETTINGS_NOT_FOCUSED')
        ids = {r['id'] for r in rows}
        for row in rows:
            handles = [h for h in inputs if h['id'] == row['id']]
            handle = handles[0] if len(handles) == 1 else None
            if len(handles) > 1 or (handle and any(handle[k] != row[k] for k in ('uid','pid','display'))):
                errors.append('WINDOW_INPUT_OWNER_CONFLICT')
            flags = set(handle['flags']) if handle else set()
            no_channel = bool(handle and 'NO_INPUT_CHANNEL' in flags and handle['token'] == '0x0')
            if handle and (('NO_INPUT_CHANNEL' in flags) != (handle['token'] == '0x0')): errors.append('INPUT_CHANNEL_CONFLICT')
            absent = not any(row[k] for k in ('surface','shown','ready','on_screen','visible')) and row['view'] in {4,8}
            inert_flags = {'NOT_TOUCHABLE','NOT_FOCUSABLE'} <= set(row['flags'])
            hidden = bool(handle and not row['shown'] and not row['ready'] and not row['on_screen'] and not row['requested'] and row['last_hidden'] and 'NOT_VISIBLE' in flags and {'NOT_TOUCHABLE','NOT_FOCUSABLE'} <= flags and no_channel and handle['region'] == '<empty>')
            actually_shown = all(row[k] for k in ('surface','shown','ready','on_screen','visible','requested')) and not row['last_hidden'] and row['view'] == 0
            classification = 'UNVERIFIED'
            if row['display'] != 0: errors.append('MULTI_DISPLAY_UNVERIFIED')
            if row['id'] == focus:
                classification = 'FOCUSED_SETTINGS' if row['package'] == 'com.android.settings' else 'UNAPPROVED_FOCUS'
                if not actually_shown or row['obscured'] or row['frame'] != (0,0,width,height) or not handle or handle['frame'] != row['frame'] or handle['alpha'] != 1 or row['alpha'] != 1 or {'NOT_VISIBLE','NOT_TOUCHABLE','NOT_FOCUSABLE','NO_INPUT_CHANNEL'} & flags or handle['region'] == '<empty>' or {'NOT_FOCUSABLE','NOT_TOUCHABLE'} & set(row['flags']):
                    errors.append('SETTINGS_DISPLAY_OR_INPUT_UNVERIFIED')
            elif row['type'] is None:
                # No permission follows from type number, package, array order
                # or a previous sample. A present input channel always blocks.
                anonymous_owner_channel = any(h['id'] not in ids and h['uid']==row['uid'] and h['pid']==row['pid'] and h['token']!='0x0' for h in inputs)
                # Even without a NOT_TOUCHABLE attribute, a complete consensus
                # can prove no current target: no Surface, nonvisible view, no
                # exact WindowState-name handle, and no unattributed channel in
                # this owner process. This is a snapshot fact, not a type grant.
                no_target = 'NOT_FOCUSABLE' in row['flags'] and not anonymous_owner_channel
                if absent and (inert_flags or no_target) and handle is None:
                    classification = 'SNAPSHOT_DORMANT_UNKNOWN_TYPE'
                else: errors.append('UNKNOWN_TYPE_INSTANCE_UNPROVEN')
            elif (absent and handle is None) or hidden:
                classification = 'NONDISPLAYED_NONINTERACTIVE'
            elif row['type'] in {2000,2019} and row['package'] == 'com.android.systemui' and handle:
                x1,y1,x2,y2 = row['frame']
                edge = x1 == 0 and x2 == width and ((row['type'] == 2000 and y1 == 0 and 0 < y2 <= height//10) or (row['type'] == 2019 and y2 == height and height*9//10 <= y1 < height))
                transparent = row['alpha'] == 0 and handle['alpha'] == 0 and 'NOT_VISIBLE' in flags and handle['region'] == '<empty>'
                normal = row['alpha'] == 1 and handle['alpha'] == 1 and 'NOT_VISIBLE' not in flags
                if actually_shown and edge and row['frame'] == handle['frame'] and 'NOT_FOCUSABLE' in flags and 'TRUSTED_OVERLAY' in flags and (normal or transparent):
                    classification = 'ASSOCIATED_EDGE_SYSTEM_BAR'
                else: errors.append('SYSTEM_BAR_EVIDENCE_CONFLICT')
            else: errors.append('UNAPPROVED_OR_CONFLICTING_WINDOW')
            overlap = max(row['frame'][0],0) < min(row['frame'][2],width) and max(row['frame'][1],0) < min(row['frame'][3],height)
            result['windows'].append({'window_ref':'w'+str(row['index']), 'type':row['raw_type'],
                'type_definition':'UNKNOWN' if row['type'] is None else TYPE_VERSION,
                'owner_category':'settings' if row['package']=='com.android.settings' else 'systemui' if row['package']=='com.android.systemui' else 'other_application',
                'classification':classification, 'focus':row['id']==focus, 'frame':row['frame'],
                'surface_present':row['surface'], 'surface_identity_verified':row['surface_id'] is not None,
                'surface_shown':row['shown'],'on_screen':row['on_screen'],'visible':row['visible'],'requested':row['requested'],
                'touchable_attribute': 'NOT_TOUCHABLE' not in row['flags'],
                'focusable_attribute': 'NOT_FOCUSABLE' not in row['flags'],
                'input_handle_associated':handle is not None, 'input_channel':'absent' if handle is None or no_channel else 'present',
                'input_touch_region':'absent' if handle is None else 'empty' if handle['region']=='<empty>' else 'nonempty',
                'overlaps_settings_region':overlap, 'base_layer':row['base_layer'], 'compositor_z':'UNKNOWN', 'surface_parent':'UNKNOWN'})
        for handle in inputs:
            if handle['display'] != 0: errors.append('MULTI_DISPLAY_UNVERIFIED')
            if handle['id'] not in ids:
                flags=set(handle['flags']);frame=handle['frame']
                inert = 'NO_INPUT_CHANNEL' in flags and handle['token']=='0x0' and {'NOT_TOUCHABLE','NOT_FOCUSABLE'} <= flags and (frame[0]==frame[2] or frame[1]==frame[3])
                result['unassociated_inputs'].append({'input_ref':'i'+str(handle['index']),'classification':'EMPTY_NONVISUAL_NO_CHANNEL' if inert else 'UNPROVEN','channel_present':handle['token']!='0x0','region_empty':handle['region']=='<empty>','spy':'SPY'in flags})
                if not inert: errors.append('UNATTRIBUTED_INPUT_OR_SURFACE_LAYER')
        # Includes private object identities to detect substitution. Only the
        # digest is public; offsets, raw names, UID/PID and tokens stay private.
        result['safety_projection_sha256'] = _canonical_hash({'rows':rows,'input':inputs,'focus':focus,'rotation':rotations[0],'size':[width,height],'lock':state,'interactive':interactive_state(power)})
        result['settings_focus_verified'] = not any(x in errors for x in ('SETTINGS_NOT_FOCUSED','FOCUS_SOURCE_CONFLICT','SETTINGS_DISPLAY_OR_INPUT_UNVERIFIED'))
        if not errors: result['classification'] = 'SAFE_FOR_READONLY_CAPTURE'
    except (NativeMetadataError,KeyError,TypeError,ValueError) as error:
        errors.append(str(error) if isinstance(error,NativeMetadataError) else 'MALFORMED_GRAPH_METADATA')
    result['reason_codes'] = sorted(set(errors))
    return result


def assess_window_pair(first, second, *, started_ns, ended_ns):
    """Bounded consensus, not an atomic compositor/screenshot transaction."""
    a,b=assess_window_graph(first),assess_window_graph(second)
    reasons=sorted(set(a['reason_codes']+b['reason_codes']))
    if type(started_ns) is not int or type(ended_ns) is not int or not 0 <= ended_ns-started_ns <= 8_000_000_000:
        reasons.append('METADATA_SAMPLE_BOUND_EXCEEDED')
    if not a.get('safety_projection_sha256') or a.get('safety_projection_sha256') != b.get('safety_projection_sha256'):
        reasons.append('METADATA_GRAPH_CHANGED_OR_UNVERIFIED')
    return {'schema':'native-window-pair/2','classification':'SAFE_FOR_READONLY_CAPTURE' if not reasons else 'INSUFFICIENT_WINDOW_EVIDENCE',
        'reason_codes':sorted(set(reasons)), 'first':a,'second':b,
        'safety_projection_sha256':b.get('safety_projection_sha256'), 'sample_elapsed_ns':ended_ns-started_ns,
        'atomic_snapshot':False, 'raw_capture_authorized':False,'physical_input_allowed':False}
