"""Scan actual credentials only in memory, then seal completed private evidence."""
import json
import os
import sys
import zipfile
from pathlib import Path
from acceptance.continuation import ROOT,digest,dump,frozen_check

out=Path(sys.argv[1]);secret=os.environ.get('PHONE_LIFE_API_KEY','');assert len(secret)>6
patterns=[secret.encode(e) for e in ('utf8','utf-16le','utf-16be')]
state=json.loads((out/'end-state.json').read_text(encoding='utf8'))
assert state['test_workers_exited'] and state['shared_lease_rows']==0 and state['original_emulator_retained']
frozen_check()
files=set(p for p in out.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
files.update(p for p in (ROOT/'acceptance').glob('*') if p.is_file())
files.add(ROOT/'tests/test_continuation_acceptance.py')
files.add(ROOT/'docs/PHASE1_2_1_CONTINUATION_ACCEPTANCE.md')
files.update((ROOT/'data').glob('continuation-*.log'))
files.update((ROOT/'dist').glob('*0.2.2-continuation*'))
files.add(ROOT/'dist/proj_digitallife-0.2.2-py3-none-any.whl')
matches=[];entries=0
for p in files:
    if any(s in p.read_bytes() for s in patterns):matches.append(p.relative_to(ROOT).as_posix())
    if p.suffix in {'.zip','.whl'}:
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                if info.is_dir():continue
                entries+=1;content=z.read(info)
                if any(s in content for s in patterns):matches.append(p.name+'::<entry>')
assert not matches,'Credential scan failed: preserve private evidence and do not distribute'
dump(out/'credential-scan.json',{'files_checked':len(files),'archive_entries_checked':entries,'matches':0,
    'encodings':['utf8','utf-16le','utf-16be'],'secret_or_secret_digest_saved':False})
target=out/'PRIVATE-SHA256.json';assert not target.exists()
private=set(p for p in out.rglob('*') if p.is_file() and '__pycache__' not in p.parts and not p.name.endswith(('-wal','-shm')))
private.update((ROOT/'data').glob('continuation-*.log'))
manifest={p.relative_to(ROOT).as_posix():{'bytes':p.stat().st_size,'sha256':digest(p.read_bytes())} for p in sorted(private)}
dump(target,{'scope':'Closed private cohort and development/full test logs; excludes this manifest and volatile WAL/SHM',
    'files':manifest})
assert all(digest((ROOT/name).read_bytes())==row['sha256'] for name,row in manifest.items())
with (ROOT/'dist/SHA256SUMS-v0.2.2-continuation.txt').open('a',encoding='utf8') as stream:
    stream.write(digest(target.read_bytes())+'  ../'+target.relative_to(ROOT).as_posix()+'\n')
print(json.dumps({'credential_matches':0,'private_files_verified':len(manifest),'manifest_sha256':digest(target.read_bytes())}))
