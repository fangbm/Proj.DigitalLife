"""Conservative scope checks. A predicate match cannot prove a broader language objective."""
import re

ALIASES = {
    'android_version':('android version','android 版本','安卓版本'),
    'model':('device model','model','设备型号','型号'),
    'battery_percent':('battery percentage','battery percent','battery level','电量','电池百分比'),
    # Unit-bearing phrases belong only to this independently measured fact.
    # KB is not a general filler word that other facts/claims may discard.
    'storage_free_kb':('free storage in kb','available storage in kb','storage free in kb',
        'free storage (kb)','available storage (kb)','storage free (kb)',
        '可用存储（kb）','剩余存储（kb）','可用存储(kb)','剩余存储(kb)',
        'free storage','available storage','storage free','可用存储','剩余存储'),
}
KNOWN_PAGES = {'about phone','about emulated device','android version','model & hardware','battery','storage',
               'display','system','memory','关于手机','关于模拟设备','android 版本','电池','存储','显示','系统','内存'}


def consistent(description, spec):
    value = description.strip().casefold()
    kind = spec['kind']
    if kind in {'observe','visual','new_scroll_content'}:
        return False, 'Observation evidence does not establish complete natural-language goal success'
    if kind == 'all':
        # Each leaf must cover its claimed scope; extra words still need full coverage below.
        leaves=spec['checks']
    else:
        leaves=[spec]
    allowed, matched = [], []
    for leaf in leaves:
        leaf_kind=leaf['kind']
        if leaf_kind=='page':
            title=leaf['title'].strip().casefold()
            if title not in KNOWN_PAGES: return False,'Unknown page scope'
            groups=[(title,)]
        elif leaf_kind=='controls':
            groups=[(label.strip().casefold(),) for label in leaf['labels']]
            if any(g[0] not in KNOWN_PAGES for g in groups): return False,'Unknown control scope'
        elif leaf_kind=='readonly_fact':
            groups=[ALIASES[key] for key in leaf['facts']]
        elif leaf_kind=='about_phone':
            groups=[('about phone','about emulated device','关于手机','关于模拟设备')]
        elif leaf_kind=='foreground':
            groups=[('settings','设置')] if leaf['package']=='com.android.settings' else []
        else:
            return False,'Unsupported goal scope'
        for aliases in groups:
            found=next((alias for alias in sorted(aliases,key=len,reverse=True) if alias in value),None)
            if found is None: return False,'Goal does not name its verification scope'
            matched.append(found);allowed.extend(aliases)
    remainder=value
    for alias in sorted(set(allowed),key=len,reverse=True): remainder=remainder.replace(alias,' ')
    # Permit only bounded navigation/read objectives. Unrecognized claims remain uncertain.
    remainder=re.sub(r'\b(open|view|read|inspect|observe|visit|navigate|to|the|a|an|and|page|screen|information|info|'
                     r'current|device|public|value|values|on|in|of|show|collect|'
                     r'independently|confirm|that|is|visible|only)\b',' ',remainder)
    for token in ('打开','查看','读取','访问','导航到','页面','界面','当前','设备','公共','信息','的','并','确认','显示','仅'):
        remainder=remainder.replace(token,' ')
    remainder=re.sub(r'[\s.,，。:：;；!?！？&()（）\-]+','',remainder)
    return (not remainder, 'Aligned bounded goal scope' if not remainder else 'Uncovered goal claim; verified predicate is narrower')
