"""Reproducible source inventory and clean-extraction checks, without private data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
from acceptance.verify_public import verify_archive

ROOT=Path(__file__).resolve().parents[1]

def build(target):
    files=[]
    for directory,suffixes in [('src/phonelife',{'.py','.jar'}),('tests',{'.py','.json'}),
            ('acceptance',{'.py','.md','.ps1'}),('docs',{'.md'}),('android/semantic-snapshot',{'.java','.md','.ps1'})]:
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and p.suffix in suffixes
            and '__pycache__' not in p.parts and 'build' not in p.parts)
    files.extend(ROOT/p for p in ('README.md','pyproject.toml','.env.example','.gitignore',
        'persona.example.md','persona.life.example.md','data/start-life.ps1','data/start-api.ps1',
        'data/phase122/final-runtime-sha256.json'))
    if (ROOT/'data/start-soak.ps1').exists():files.append(ROOT/'data/start-soak.ps1')
    manifest={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in manifest:
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,(ROOT/name).read_bytes())
        info=zipfile.ZipInfo('CONTENTS-SHA256.json',date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
        z.writestr(info,json.dumps(manifest,sort_keys=True,indent=2))
    return verify_archive(target)

def clean_check(archive,output):
    output.mkdir(parents=True,exist_ok=False);workspace=output/'extracted';workspace.mkdir()
    verify_archive(archive)
    with zipfile.ZipFile(archive) as z:z.extractall(workspace)
    env=dict(os.environ,PYTHONPATH='src;.' if os.name=='nt' else 'src:.',PYTHONUTF8='1')
    for k in list(env):
        if any(w in k.upper() for w in ('API_KEY','TOKEN','PASSWORD','COOKIE')):env.pop(k)
    commands=[('import',[sys.executable,'-c',
        "import phonelife,acceptance.verify_continuation as v; from pathlib import Path; assert Path(phonelife.__file__).resolve().is_relative_to(Path.cwd()); assert Path(v.__file__).resolve().is_relative_to(Path.cwd()); print('local imports PASS')"]),
        ('cli',[sys.executable,'-m','phonelife.cli','--help']),
        ('verifier',[sys.executable,'-m','acceptance.verify_continuation','--self-check']),
        ('offline-tests',[sys.executable,'-m','unittest','discover','-s','tests','-v'])]
    results=[]
    for label,cmd in commands:
        r=subprocess.run(cmd,cwd=workspace,env=env,capture_output=True,timeout=240)
        (output/(label+'.log')).write_bytes(r.stdout+r.stderr)
        results.append({'check':label,'exit_code':r.returncode})
    result={'checks':results,'all_passed':all(r['exit_code']==0 for r in results),
        'private_development_directory_present':(workspace/'data/phase122/analyze.py').exists(),
        'network_or_device_behavior_reproved':False}
    (output/'CLEAN_CHECK.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    assert result['all_passed'] and not result['private_development_directory_present']
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('archive',type=Path);p.add_argument('--clean-check',type=Path)
    a=p.parse_args();r=build(a.archive.resolve())
    if a.clean_check:r['clean_extraction']=clean_check(a.archive.resolve(),a.clean_check.resolve())
    print(json.dumps(r,indent=2))
