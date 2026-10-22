"""Private, bounded diagnostic evidence. Never a GUI observation provider."""
from __future__ import annotations
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import warnings
import xml.etree.ElementTree as ET
from PIL import Image


class DiagnosticDenied(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)  # No raw output, paths, serials or UI text.


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _windows_acl(path):
    """Check owner-only protected DACL, including files; no CLI ACL parsing."""
    from ctypes import wintypes as w
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.LocalFree.argtypes = [w.HLOCAL]
    adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), ctypes.c_void_p]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    owner = ctypes.c_void_p(); dacl = ctypes.c_void_p(); sd = ctypes.c_void_p()
    token = w.HANDLE(); text = w.LPWSTR(); sidtext = w.LPWSTR()
    try:
        if adv.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(sd)) or not dacl.value:
            raise DiagnosticDenied('PRIVATE_ACL_UNVERIFIED')
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise DiagnosticDenied('PRIVATE_OWNER_UNVERIFIED')
        size = w.DWORD(); adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)):
            raise DiagnosticDenied('PRIVATE_OWNER_UNVERIFIED')
        current_sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        adv.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if not adv.EqualSid(owner, current_sid): raise DiagnosticDenied('PRIVATE_OWNER_MISMATCH')
        if not adv.ConvertSidToStringSidW(owner, ctypes.byref(sidtext)) or not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 4, ctypes.byref(text), None):
            raise DiagnosticDenied('PRIVATE_ACL_UNVERIFIED')
        value = text.value
        aces = re.findall(r'\(([^()]*)\)', value)
        # New root must be protected. Descendants may inherit ONLY this owner.
        if path.is_dir() and 'P' not in value.split('(', 1)[0]:
            raise DiagnosticDenied('PRIVATE_ACL_NOT_PROTECTED')
        if not aces or any(len(a.split(';')) != 6 or a.split(';')[0] != 'A' or a.split(';')[5] != sidtext.value or a.split(';')[2] not in {'FA','0x1f01ff'} for a in aces):
            raise DiagnosticDenied('PRIVATE_ACL_NOT_OWNER_ONLY')
    finally:
        for ptr in (text, sidtext, sd):
            if ptr: kernel.LocalFree(ptr)
        if token: kernel.CloseHandle(token)


def private_path(raw):
    p = Path(raw)
    if not p.is_absolute() or '..' in p.parts or (os.name == 'nt' and (str(p).startswith('\\\\') or ':' in str(p)[2:])):
        raise DiagnosticDenied('PRIVATE_PATH_INVALID')
    for part in (p, *p.parents):
        s = part.lstat()
        if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
            raise DiagnosticDenied('PRIVATE_PATH_REPARSE')
    if not p.is_dir(): raise DiagnosticDenied('PRIVATE_DIRECTORY_REQUIRED')
    if os.name == 'nt': _windows_acl(p)
    else:
        s = p.stat()
        if s.st_uid != os.getuid() or s.st_mode & 0o077:
            raise DiagnosticDenied('PRIVATE_ACL_NOT_OWNER_ONLY')
    return p


def restrict_new_directory(path):
    """Only for a newly created diagnostic directory; never repair old evidence."""
    p = Path(path)
    if os.name != 'nt':
        p.chmod(0o700)
        return private_path(p)
    from ctypes import wintypes as w
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32'); kernel.LocalFree.argtypes = [ctypes.c_void_p]
    owner=ctypes.c_void_p(); sd=ctypes.c_void_p(); sid=w.LPWSTR(); new_sd=ctypes.c_void_p()
    adv.GetNamedSecurityInfoW.argtypes=[w.LPWSTR,w.DWORD,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    adv.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(w.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    adv.GetSecurityDescriptorDacl.argtypes=[ctypes.c_void_p,ctypes.POINTER(w.BOOL),ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(w.BOOL)]
    adv.SetNamedSecurityInfoW.argtypes=[w.LPWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
    try:
        if adv.GetNamedSecurityInfoW(str(p),1,1,ctypes.byref(owner),None,None,None,ctypes.byref(sd)) or not adv.ConvertSidToStringSidW(owner,ctypes.byref(sid)):
            raise DiagnosticDenied('PRIVATE_ACL_SET_FAILED')
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;OICI;FA;;;'+sid.value+')',1,ctypes.byref(new_sd),None):
            raise DiagnosticDenied('PRIVATE_ACL_SET_FAILED')
        present=w.BOOL(); defaulted=w.BOOL(); dacl=ctypes.c_void_p()
        if not adv.GetSecurityDescriptorDacl(new_sd,ctypes.byref(present),ctypes.byref(dacl),ctypes.byref(defaulted)) or not present:
            raise DiagnosticDenied('PRIVATE_ACL_SET_FAILED')
        if adv.SetNamedSecurityInfoW(str(p),1,0x80000004,None,None,dacl,None):
            raise DiagnosticDenied('PRIVATE_ACL_SET_FAILED')
    finally:
        for ptr in (sd,sid,new_sd):
            if ptr: kernel.LocalFree(ptr)
    return private_path(p)


def create_private_directory(raw):
    p=Path(raw)
    if not p.is_absolute() or '..' in p.parts: raise DiagnosticDenied('PRIVATE_PATH_INVALID')
    # Never follow an existing reparse parent, or change any existing ACL.
    for part in p.parents:
        s=part.lstat()
        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0) & 0x400:
            raise DiagnosticDenied('PRIVATE_PATH_REPARSE')
    p.mkdir(mode=0o700,exist_ok=False)
    return restrict_new_directory(p)


def validate_payload(operation, raw):
    limit = 16 * 1024**2 if operation == 'CAPTURE_SCREEN_PNG' else 2_000_000
    if not isinstance(raw, bytes) or not 0 < len(raw) <= limit:
        raise DiagnosticDenied('CAPTURE_OUTPUT_LIMIT')
    try:
        if operation == 'CAPTURE_SCREEN_PNG':
            if not raw.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError()
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    width, height = image.size
                    if image.format != 'PNG' or width * height > 16_000_000 or min(width,height) < 1: raise ValueError()
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image: image.load()
            return {'width': width, 'height': height}
        if operation != 'CAPTURE_UI_XML': raise ValueError()
        if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', raw, re.I): raise ValueError()
        root = ET.fromstring(raw)
        if root.tag != 'hierarchy' or root.get('rotation') not in {'0','1','2','3'}: raise ValueError()
        nodes = list(root.iter('node'))
        if not nodes or len(nodes) > 10000: raise ValueError()
        attrs = {'clickable','enabled','focusable','bounds','package','class','editable','password','visible-to-user','checkable','long-clickable'}
        return {'nodes': len(nodes), 'rotation': int(root.get('rotation')),
                'missing_safety_attributes': sorted(set().union(*(attrs - set(n.attrib) for n in nodes))),
                'all_settings': all(n.get('package') == 'com.android.settings' for n in nodes)}
    except Exception:
        raise DiagnosticDenied('CAPTURE_INVALID_FORMAT') from None


class PrivateEvidence:
    def __init__(self, directory):
        self.path = private_path(directory)
        self.identity = (self.path.stat().st_dev, self.path.stat().st_ino)

    def check(self):
        p = private_path(self.path)
        if (p.stat().st_dev, p.stat().st_ino) != self.identity:
            raise DiagnosticDenied('PRIVATE_DIRECTORY_CHANGED')

    def save(self, request_id, operation, raw):
        self.check()
        if not re.fullmatch(r'[0-9a-f]{32}', request_id): raise DiagnosticDenied('INVALID_REQUEST_ID')
        name = request_id + ('.png' if operation == 'CAPTURE_SCREEN_PNG' else '.xml')
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        fd = os.open(self.path / name, flags, 0o600)
        with os.fdopen(fd, 'wb') as f:
            if os.name == 'nt': _windows_acl(self.path / name)
            f.write(raw); f.flush(); os.fsync(f.fileno())
        self.check()
        return name


def analyze_pair(png, xml, *, metadata_before=None, metadata_after=None, started_ns=None, ended_ns=None):
    """Offline fact summary; missing native safety properties never inferred."""
    p=validate_payload('CAPTURE_SCREEN_PNG',png); x=validate_payload('CAPTURE_UI_XML',xml)
    from .native_windows import assess_xml, assess_window_graph
    timed=type(started_ns) is int and type(ended_ns) is int and 0 <= ended_ns-started_ns <= 2_000_000_000
    before=sha(canonical(metadata_before)) if metadata_before is not None else None
    after=sha(canonical(metadata_after)) if metadata_after is not None else None
    semantics=assess_xml(xml.decode('utf8'),width=p['width'],height=p['height'],rotation=x['rotation'],
        observed_ns=started_ns,now_ns=ended_ns,before_scene=before,after_scene=after)
    return {'png': p,'xml': x,'native_semantics':semantics,'timing_consistent':timed,
            'window_graph':assess_window_graph(metadata_before) if metadata_before is not None else {'classification':'NOT_OBSERVED'},
            'GUI_EXECUTION_SEMANTICS_TRUSTED': False,
            'semantic_state': 'NOT_TRUSTED',
            'gaps': ['NATIVE_WINDOW_SURFACE_INPUT_PROOF','ATOMIC_TIMING_PROOF','NODE_BOUNDS_AND_OWNER_REVALIDATION'],
            'physical_input_allowed': False}
