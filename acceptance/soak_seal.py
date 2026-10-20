"""Seal new Stage A-C evidence after all writers stop. Never changes old artifacts."""
import json
import os
from pathlib import Path
import sys
import zipfile
from acceptance.source_archive import ROOT
from phonelife.longrun_state import sha,runtime_manifest

out=Path(sys.argv[1]).resolve();target=out/'PRIVATE-SHA256.json';assert not target.exists()
end=json.loads((out/'END_STATE.json').read_text(encoding='utf8'))
assert end['test_workers_exited'] and end['shared_lease_rows']==0 and end['formal_started']is False
assert end['original_emulator_retained']
pre=json.loads((out/'preflight-02/SOAK_PREFLIGHT.json').read_text(encoding='utf8'))
assert runtime_manifest()==pre['frozen_runtime_sha256']
secret=os.environ.get('PHONE_LIFE_API_KEY','');assert len(secret)>6
patterns=[secret.encode(e) for e in ('utf8','utf-16le','utf-16be')]
files={p for p in out.rglob('*') if p.is_file() and '__pycache__'not in p.parts}
files.update(p for base in ('src/phonelife','acceptance','tests') for p in (ROOT/base).rglob('*') if p.is_file() and '__pycache__'not in p.parts)
files.update(ROOT/p for p in ('README.md','pyproject.toml','data/start-soak.ps1','docs/PHASE1_2_2_LONGRUN.md','docs/PHASE1_2_2_DELIVERY.md','docs/SOURCE_VERIFICATION.md'))
files.update(p for p in (ROOT/'dist').glob('*0.2.3*') if p.is_file())
files.add(ROOT/'dist/Proj.DigitalLife-v0.2.2-packaging-closure-source.zip')
entries=0
for p in files:
    assert not any(s in p.read_bytes() for s in patterns),'Credential found; do not distribute'
    if p.suffix in {'.zip','.whl'}:
        with zipfile.ZipFile(p) as z:
            for item in z.infolist():
                if item.is_dir():continue
                entries+=1;data=z.read(item)
                assert not any(s in data for s in patterns),'Credential found in archive; do not distribute'
(out/'CREDENTIAL_SCAN.json').write_text(json.dumps({'files_checked':len(files),'archive_entries_checked':entries,'matches':0,
    'encodings':['utf8','utf-16le','utf-16be'],'secret_or_plaintext_secret_digest_saved':False},indent=2),encoding='utf8')
private=sorted(p for p in out.rglob('*') if p.is_file() and p!=target and '__pycache__'not in p.parts and not p.name.endswith(('-wal','-shm')))
manifest={p.relative_to(ROOT).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)} for p in private}
target.write_text(json.dumps({'scope':'Closed Stage A-C evidence; excludes this manifest, Python caches, volatile WAL/SHM',
    'formal_24h_started':False,'files':manifest},ensure_ascii=False,indent=2),encoding='utf8')
assert all(sha(ROOT/p)==v['sha256'] for p,v in manifest.items())
with (ROOT/'dist/SHA256SUMS-v0.2.3.txt').open('a',encoding='utf8') as stream:
    stream.write(sha(target)+'  ../'+target.relative_to(ROOT).as_posix()+'\n')
print(json.dumps({'credential_matches':0,'private_files_verified':len(manifest),'manifest_sha256':sha(target)}))
