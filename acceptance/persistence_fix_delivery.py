"""Offline persistence-fix evidence, build and integrity. Never starts Soak/API/ADB."""
import argparse,ast,collections,difflib,hashlib,json,os,re,sqlite3,subprocess,sys,zipfile
from pathlib import Path
from acceptance.source_archive import build,clean_check
from phonelife.longrun_state import runtime_manifest,SessionLedger,canonical

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/phase1222-persistence-fix-01'
DIST=ROOT/'dist/stagec-persistence-fix-01'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text(encoding='utf8'))
def put(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf8',newline='\n') as f:f.write(v if isinstance(v,str) else json.dumps(v,ensure_ascii=False,indent=2))
def ro(p):
    c=sqlite3.connect(Path(p).resolve().as_uri()+'?mode=ro',uri=True);c.execute('PRAGMA query_only=ON');return c

def retained():
    b=read(DATA/'BASELINE_PRIVATE.json');rows=[]
    for n,names in b['old_test_methods'].items():
        raw=(ROOT/n).read_bytes();assert sha(ROOT/n)==b['files'][n]
        now=[x.name for x in ast.walk(ast.parse(raw.decode('utf8'))) if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name.startswith('test_')]
        assert now==names;rows.append({'file':n,'sha256':sha(ROOT/n),'original_methods':len(names),'byte_unchanged':True})
    count=lambda p:sum(isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name.startswith('test_') for x in ast.walk(ast.parse(p.read_text(encoding='utf8'))))
    added=count(ROOT/'tests/test_stop_persistence.py');assert added==35
    put(DATA/'TEST_INTENT_RETENTION_FINAL.json',{'old_methods_retained_byte_unchanged':294,'new_methods':added,'total':294+added,'old_files':rows})
    changes=[];patches=[]
    for n in ['src/phonelife/longrun.py','src/phonelife/longrun_state.py']:
        a=DATA/'source-before'/n;p=ROOT/n
        changes.append({'file':n,'before_sha256':sha(a),'after_sha256':sha(p)})
        patches.extend(difflib.unified_diff(a.read_text(encoding='utf8').splitlines(True),p.read_text(encoding='utf8').splitlines(True),fromfile='preflight-04/'+n,tofile='persistence-fix/'+n))
    put(DATA/'CHANGED_RUNTIME_FILES.json',changes);put(DATA/'minimal-production.diff',''.join(patches))
    return added

def freeze_build():
    DIST.mkdir(parents=True,exist_ok=True);retained();runtime=runtime_manifest()
    old=read(ROOT/'data/phase1222-stop-fix/runtime-final-sha256.json')
    changed=[n for n,h in runtime.items() if h!=old[n]];assert changed==['longrun.py','longrun_state.py'] and len(runtime)==39
    put(DATA/'runtime-final-sha256.json',runtime)
    for n in runtime:
        p=DATA/'frozen-runtime'/n;p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('xb') as f:f.write((ROOT/'src/phonelife'/n).read_bytes())
    archive=DIST/'Proj.DigitalLife-v0.2.3-persistence-fix-source-final.zip';result=build(archive)
    staging=DATA/'build-source';staging.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as z:z.extractall(staging)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',PIP_NO_INDEX='1')
    env.pop('STOP_PERSISTENCE_EVIDENCE_DIR',None)
    for k in list(env):
        if any(w in k.upper() for w in ['API_KEY','TOKEN','PASSWORD','COOKIE']):env.pop(k)
    r=subprocess.run([sys.executable,'-m','pip','wheel','.', '--no-deps','--no-build-isolation','--no-cache-dir','--wheel-dir',str(DIST)],cwd=staging,env=env,capture_output=True,timeout=120)
    put(DATA/'build-wheel.log',(r.stdout+r.stderr).decode('utf8',errors='replace'));assert r.returncode==0
    wheel=DIST/'proj_digitallife-0.2.3-py3-none-any.whl';pairs=[]
    with zipfile.ZipFile(archive) as z,zipfile.ZipFile(wheel) as w:
        for n,h in runtime.items():
            a=z.read('src/phonelife/'+n);b=w.read('phonelife/'+n)
            assert a==b==(ROOT/'src/phonelife'/n).read_bytes() and hashlib.sha256(a).hexdigest()==h
            pairs.append({'file':n,'sha256':h,'source_wheel_actual_byte_identical':True,'unchanged_from_preflight04':h==old[n]})
        for name in ['tree-0.json','tree-1.json','PROVENANCE.json']:
            n='tests/fixtures/uptime-api35/'+name
            assert hashlib.sha256(z.read(n)).hexdigest()==read(DATA/'BASELINE_PRIVATE.json')['files'][n]
        assert not any(n.lower().endswith(('.sqlite3','.db','.png','.xml','.key','.dpapi')) for n in z.namelist()+w.namelist())
    put(DATA/'WHEEL_VERIFICATION.json',{'source_archive':archive.relative_to(ROOT).as_posix(),'source_sha256':sha(archive),'wheel':wheel.relative_to(ROOT).as_posix(),'wheel_sha256':sha(wheel),
        'source_integrity':result,'runtime_count':39,'unchanged_from_preflight04':37,'changed_runtime':changed,'rows':pairs,'public_upload':False,'fixture_exception_authorized':False})
    print(json.dumps({'build':'PASS','source_sha256':sha(archive),'wheel_sha256':sha(wheel),'runtime_equal':39,'old_approval_valid':False}))

def integrity():
    base=read(DATA/'BASELINE_PRIVATE.json');allowed={'src/phonelife/longrun.py','src/phonelife/longrun_state.py','README.md'}
    bad=[n for n,h in base['files'].items() if n not in allowed and sha(ROOT/n)!=h]
    assert not bad,bad
    checks=[]
    for r in base['backups']:
        a=ro(ROOT/r['backup']);b=ro(ROOT/r['original'])
        try:
            missing=0
            for table, in a.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'"):
                q='SELECT * FROM "'+table.replace('"','""')+'"';missing+=sum((collections.Counter(a.execute(q))-collections.Counter(b.execute(q))).values())
            assert b.execute('PRAGMA quick_check').fetchone()[0]=='ok' and missing==0
            checks.append({'database':r['original'],'missing_original_rows':missing,'quick_check':'ok'})
        finally:a.close();b.close()
    candidate=ROOT/'data/phase1222-stop-fix/preflight-04/formal-24h-candidate'
    l=SessionLedger(candidate/'soak-control.sqlite3',readonly=True)
    try:v=l.status()
    finally:l.close()
    assert v['state']=='awaiting_approval' and v['elapsed_seconds']==0 and v['active_pid'] is None
    c=ro(candidate/'agent.sqlite3')
    try:tasks=c.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0];api=c.execute('SELECT COUNT(*) FROM life_requests').fetchone()[0]
    finally:c.close()
    c=ro(ROOT/'data/phone-life.sqlite3')
    try:leases=c.execute('SELECT COUNT(*) FROM lease').fetchone()[0]
    finally:c.close()
    assert tasks==api==leases==0
    assert v['config']['runtime_sha256']!=runtime_manifest()
    put(DATA/'HISTORY_INTEGRITY_FINAL.json',{'baseline_files_checked':len(base['files']),'unexpected_hash_changes':bad,'original_sqlite_checks':checks,
        'old_source_wheel_reports_and_preflight04_unchanged':True,'key_and_persona_and_fixture_bytes_unchanged':True,
        'old_candidate':{'state':v['state'],'elapsed_seconds':0,'task_runs':tasks,'api_requests':api,'shared_lease_rows':leases,
            'fingerprint':v['fingerprint'],'runtime_freeze_matches_new_code':False,'approval_invalid_for_new_code':True},
        'new_formal_candidate_created':False,'real_api_preflight_executed':False,'formal_soak_started':False})
    print(json.dumps({'historical_integrity':'PASS','sqlite_checked':len(checks),'baseline_files':len(base['files']),'old_candidate_unstarted_but_invalid':True}))

def clean():
    os.environ.pop('STOP_PERSISTENCE_EVIDENCE_DIR',None)
    result=clean_check(DIST/'Proj.DigitalLife-v0.2.3-persistence-fix-source-final.zip',DATA/'clean-source-final')
    print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['build','clean','integrity']);a=p.parse_args()
    {'build':freeze_build,'clean':clean,'integrity':integrity}[a.command]()
