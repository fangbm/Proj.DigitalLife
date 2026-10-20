"""Public archive integrity only; never proves private device/API behavior."""
import argparse
import hashlib
import json
import zipfile
from pathlib import PurePosixPath

def verify_archive(path):
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate archive entries')
        for name in names:
            p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name:raise ValueError('Unsafe archive path')
        if archive.testzip() is not None:raise ValueError('CRC failure')
        manifest=json.loads(archive.read('CONTENTS-SHA256.json'))
        if set(names)!=(set(manifest)|{'CONTENTS-SHA256.json'}):raise ValueError('Unmanifested archive entries')
        for name,expected in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=expected:raise ValueError('Hash mismatch')
    return {'mode':'public_integrity_only','integrity_passed':True,'files_verified':len(manifest),
        'live_device_behavior_reproved':False,'private_evidence_required_for_causality':True}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('archive')
    args=parser.parse_args(argv);print(json.dumps(verify_archive(args.archive),indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
