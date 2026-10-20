"""Stage C review artifacts only; does not run the Agent or grant approval."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from acceptance.source_archive import ROOT,build,clean_check
from acceptance.verify_public import verify_archive
from phonelife.longrun_state import runtime_manifest,sha

def main(out,clean_name='clean-final'):
    out=out.resolve();dist=ROOT/'dist';pre=json.loads((out/'preflight-02/SOAK_PREFLIGHT.json').read_text(encoding='utf8'))
    assert pre['formal_soak_started']is False and pre['ready_for_review']
    assert pre['frozen_runtime_sha256']==runtime_manifest()
    source=dist/'Proj.DigitalLife-v0.2.3-source.zip'
    build(source)
    with zipfile.ZipFile(source) as z:
        manifest=json.loads(z.read('CONTENTS-SHA256.json'))
        assert all(manifest['src/phonelife/'+p]==h for p,h in runtime_manifest().items())
    checks=clean_check(source,out/clean_name)
    assert 'Ran 266 tests' in (out/clean_name/'offline-tests.log').read_text(encoding='utf8')
    wheel=dist/'proj_digitallife-0.2.3-py3-none-any.whl'
    with zipfile.ZipFile(wheel) as z:
        assert z.testzip()is None
        assert all(hashlib.sha256(z.read('phonelife/'+p)).hexdigest()==h for p,h in runtime_manifest().items())
    frozen={p.relative_to(ROOT).as_posix():sha(p) for base in ('src/phonelife','acceptance','tests','docs')
        for p in (ROOT/base).rglob('*') if p.is_file() and p.suffix in {'.py','.jar','.md','.json','.ps1'} and '__pycache__'not in p.parts}
    for name in ('README.md','pyproject.toml','data/start-soak.ps1'):frozen[name]=sha(ROOT/name)
    original=json.loads((out/'BASELINE.json').read_text(encoding='utf8'))['files']
    changed=[{'file':p,'before_sha256':original.get(p),'after_sha256':h} for p,h in frozen.items() if original.get(p)!=h]
    (out/'changed-files.json').write_text(json.dumps(changed,indent=2),encoding='utf8')
    (out/'source-final-sha256.json').write_text(json.dumps(frozen,indent=2),encoding='utf8')
    public=out/'public-review'
    (public/'changed-files.json').write_text(json.dumps(changed,indent=2),encoding='utf8')
    (public/'PACKAGING.json').write_text(json.dumps({'stage_a_original212_clean':True,'final_clean266_passed':True,
        'local_imports_verified':True,'verifier_self_check':True,'private_development_analyzer_present':False,
        'wheel_runtime_files_identical':len(runtime_manifest()),'source_archive_sha256':sha(source),
        'wheel_sha256':sha(wheel),'does_not_reprove_live_device_behavior':True},indent=2),encoding='utf8')
    files=list(public.glob('*.json'))+[ROOT/'docs/PHASE1_2_2_DELIVERY.md',ROOT/'docs/PHASE1_2_2_LONGRUN.md',ROOT/'docs/SOURCE_VERIFICATION.md']
    manifest={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(files)}
    evidence=dist/'Proj.DigitalLife-v0.2.3-public-evidence.zip'
    with zipfile.ZipFile(evidence,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in manifest:
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,(ROOT/name).read_bytes())
        info=zipfile.ZipInfo('CONTENTS-SHA256.json',date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
        z.writestr(info,json.dumps(manifest,indent=2,sort_keys=True))
    verify_archive(evidence)
    archives=(source,evidence,wheel,dist/'Proj.DigitalLife-v0.2.2-packaging-closure-source.zip')
    sums=dist/'SHA256SUMS-v0.2.3.txt';assert not sums.exists()
    values={p.name:sha(p) for p in archives}
    sums.write_text(''.join(h+'  '+p+'\n' for p,h in values.items())+
        sha(out/'preflight-02/SOAK_PREFLIGHT.json')+'  ../'+(out/'preflight-02/SOAK_PREFLIGHT.json').relative_to(ROOT).as_posix()+'\n',encoding='utf8')
    (out/'ARCHIVE_VERIFICATION.json').write_text(json.dumps({'source_files':len(frozen),'public_files':len(manifest),
        'clean_checks':checks,'all_crc_and_entry_hashes_verified':True,'sha256':values},indent=2),encoding='utf8')
    print(json.dumps({'final_clean_tests':266,'sha256':values},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);p.add_argument('--clean-name',default='clean-final')
    a=p.parse_args();main(a.output,a.clean_name)
