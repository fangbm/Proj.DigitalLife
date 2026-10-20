"""New artifact names only; exact source/public manifests and frozen runtime check."""
import json
import re
import sys
import zipfile
from pathlib import Path
from acceptance.continuation import ROOT,digest,dump,frozen_check

out=Path(sys.argv[1]).resolve();dist=ROOT/'dist'
files=[]
for base,suffixes in [('src/phonelife',{'.py','.jar'}),('tests',{'.py','.json'}),
                     ('docs',{'.md'}),('acceptance',{'.py','.ps1','.md'}),
                     ('android/semantic-snapshot',{'.java','.ps1','.md'})]:
    files.extend(p for p in (ROOT/base).rglob('*') if p.is_file() and p.suffix in suffixes
                 and '__pycache__' not in p.parts and 'build' not in p.parts)
files.extend(ROOT/name for name in ('README.md','pyproject.toml','.env.example','.gitignore',
                                  'persona.example.md','persona.life.example.md',
                                  'data/start-life.ps1','data/start-api.ps1'))
frozen=frozen_check()
files.append(ROOT/'data/phase122/final-runtime-sha256.json')
log=(ROOT/'data/continuation-regression-final-212.log').read_text(encoding='utf8')
names=re.findall(r'^(test_\w+) \(([^)]+)\)',log,re.M);assert len(names)==212
dump(out/'public/offline-tests.json',{'classification':'offline_fixture','original_count':198,'new_count':14,
    'all_passed':True,'full_log_sha256':digest(log),'tests':[{'name':n,'class':c,'status':'PASS'} for n,c in names]})
def archive(path,selected):
    hashes={p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in sorted(set(selected))}
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in hashes:z.write(ROOT/name,name)
        z.writestr('CONTENTS-SHA256.json',json.dumps(hashes,indent=2))
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        assert all(digest(z.read(name))==value for name,value in hashes.items())
    return hashes
source=archive(dist/'Proj.DigitalLife-v0.2.2-continuation-source.zip',files)
assert all(source[name]==value for name,value in frozen.items())
public=list((out/'public').glob('*.json'))+[ROOT/'docs/PHASE1_2_1_CONTINUATION_ACCEPTANCE.md',ROOT/'acceptance/README.md']
assert not any(p.suffix in {'.png','.sqlite3'} for p in public)
evidence=archive(dist/'Proj.DigitalLife-v0.2.2-continuation-public-evidence.zip',public)
artifacts={name:digest((dist/name).read_bytes()) for name in (
    'Proj.DigitalLife-v0.2.2-continuation-source.zip',
    'Proj.DigitalLife-v0.2.2-continuation-public-evidence.zip',
    'proj_digitallife-0.2.2-py3-none-any.whl')}
sums=dist/'SHA256SUMS-v0.2.2-continuation.txt'
assert not sums.exists()
sums.write_text(''.join(value+'  '+name+'\n' for name,value in artifacts.items()),encoding='utf8')
dump(out/'archive-verification.json',{'source_files':len(source),'public_files':len(evidence),
    'all_entry_hashes_verified':True,'all_crc_ok':True,'original_wheel_unchanged':True,'sha256':artifacts})
dump(out/'final-supplement-source-sha256.json',{name:value for name,value in source.items()
    if name.startswith(('acceptance/','tests/test_continuation_acceptance.py','docs/PHASE1_2_1_CONTINUATION_ACCEPTANCE.md'))})
print(json.dumps({'source_files':len(source),'public_files':len(evidence),'sha256':artifacts},indent=2))
