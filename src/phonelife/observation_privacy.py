"""Structural privacy evidence; menu words alone are not a credential surface.

Returns reason codes and structural paths, never private text or field values.
This controls observation only. Action risk checks remain a separate veto.
"""
import hashlib
import re
import xml.etree.ElementTree as ET
from .policy import PolicyDenied

SENSITIVE=re.compile(r'(?i)(password|credential|login|sign.?in|sign.?up|register|authenticat|payment|checkout|credit.?card|密码|验证码|登录|注册|支付|付款|银行卡|发消息|发表评论|编辑资料)')
SEARCH=re.compile(r'(?i)(search|搜索|搜尋)')
SECURITY_PACKAGE=re.compile(r'(?i)(permissioncontroller|securitypermission)')
HEADER=re.compile(r'(?i)(toolbar[_]?title|action[_]?bar[_]?title|page[_]?title|entity_header_title|alertTitle)$')
PRIVATE_RESOURCE=re.compile(r'(?i)(?:^|/)(?:(?:password|credential|pin|otp|verification_code|credit_card|card_number|private_key|api_key)(?:$|_(?:input|field|value|list|form)$)|(?:login|register|authentication|account|payment|credential|checkout|compose|chat|message|comment|profile)_(?:form|input|editor|panel|page)$)')

def search_editor(cls,resource,description,editable=None):
    return (any(t in cls for t in ('EditText','AutoCompleteTextView')) or editable is True) and bool(SEARCH.search(resource+' '+description))

def assess_nodes(nodes):
    signals=[]
    for n in nodes:
        resource=n.get('resource','').split(':')[-1];cls=n.get('class','');label=n.get('text','')+' '+n.get('description','')
        reason=None
        if n.get('password') is True:reason='NATIVE_PASSWORD_FIELD'
        elif SECURITY_PACKAGE.search(n.get('package','')):reason='PERMISSION_SURFACE'
        elif PRIVATE_RESOURCE.search(resource):reason='PRIVATE_FORM_OR_VALUE_ROLE'
        elif HEADER.search(resource) and SENSITIVE.search(label):reason='SENSITIVE_PAGE_HEADER'
        elif any(t in cls for t in ('EditText','AutoCompleteTextView')) or n.get('editable') is True:
            if not search_editor(cls,resource,n.get('description',''),n.get('editable')):reason='NON_SEARCH_EDITOR'
            elif re.search(r'(?i)(\bsk-[\w-]+|password\s*[:=]|token\s*[:=]|[\w.+-]+@[\w.-]+\.\w+|\d{11,19})',n.get('text','')):reason='SENSITIVE_SEARCH_VALUE'
        if reason:signals.append({'reason_code':reason,'path':n.get('path','')})
    return {'redacted':bool(signals),'reason_codes':sorted({s['reason_code']for s in signals}),
        'node_count':len(nodes),'signals':signals[:16],'directory_words_alone_are_not_forms':True}

def assess_xml(xml):
    if len(xml.encode())>2_000_000 or '<!DOCTYPE' in xml or '<!ENTITY' in xml:raise PolicyDenied('UNSAFE_XML')
    try:root=ET.fromstring(xml)
    except ET.ParseError:raise PolicyDenied('XML_PARSE_FAILED') from None
    nodes=[]
    def visit(element,path):
        if len(nodes)>=1800 or len(path.split('/'))>60:raise PolicyDenied('XML_NODE_LIMIT')
        if element.tag=='node':
            flags={}
            for key in ('password','editable'):
                value=element.get(key)
                if value is not None and value not in {'true','false'}:raise PolicyDenied('INVALID_ATTRIBUTE')
                flags[key]={'true':True,'false':False}.get(value)
            nodes.append({'path':path,'resource':element.get('resource-id',''),'class':element.get('class',''),
                'package':element.get('package',''),'text':element.get('text',''),'description':element.get('content-desc',''),
                **flags})
        for i,c in enumerate(element):visit(c,path+'/'+str(i))
    visit(root,'')
    return {**assess_nodes(nodes),'xml_sha256':hashlib.sha256(xml.encode()).hexdigest()}

def assess_controls(controls):
    return assess_nodes([{'path':c.structural_path,'resource':c.resource_id,'class':c.class_name,'package':c.package,
        'text':c.text,'description':c.content_description,'password':c.password,'editable':c.editable}for c in controls])

def sensitive_window_reason(metadata,foreground):
    sensitive=re.compile(r'permissioncontroller|securitypermission|biometric|credential',re.I)
    if sensitive.search(foreground):return 'FOREGROUND_SECURITY_SURFACE'
    for block in re.split(r'(?=\bWindow #\d+ Window\{)',metadata.get('windows','')):
        block=re.split(r'(?m)^ {0,2}(?:mGlobalConfiguration=|Hiding non-system overlay|mTopFocusedDisplayId=)',block)[0]
        if not (re.search(r'mViewVisibility=0x0\b',block) and 'mHasSurface=true' in block):continue
        if re.search(r'\b(?:isOnScreen|isVisible|mPolicyVisibility)=false\b|\bmLastHidden=true\b|Surface:\s*shown=false\b',block):continue
        keyguard=bool(re.search(r'\bty=(?:TYPE_)?(?:KEYGUARD|KEYGUARD_DIALOG|2004|2009)\b',block))
        overlay=bool(re.search(r'\bty=(?:TYPE_)?(?:SYSTEM_DIALOG|APPLICATION_OVERLAY|2008|2038)\b',block))
        shown=bool(re.search(r'\bisOnScreen=true\b',block) and re.search(r'\bisVisible=true\b',block))
        if keyguard or sensitive.search(block) and (overlay or shown):return 'VISIBLE_SECURITY_WINDOW'
    return None
