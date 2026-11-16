#!/usr/bin/env python3
"""Fetch pinned public case sources and an isolated package manager."""
from pathlib import Path
import concurrent.futures
import hashlib
import json
import os
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'data/runs/pinched-cylinder_20261116T184505'
HERE=Path(__file__).resolve().parent

def fetch(url):
    request=urllib.request.Request(url,headers={'User-Agent':'pangulu-comsol-case-trial/1.0'})
    with urllib.request.urlopen(request,timeout=90) as response:
        return response.read()

def main():
    manifest=json.loads((HERE/'remote-files.json').read_text())
    def source(item):
        path=item['path']
        body=fetch(f"https://raw.githubusercontent.com/{manifest['repository']}/{manifest['commit']}/{path}")
        digest=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()
        assert digest==item['sha'],path
        target=OUT/'sources'/path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(body)
        return {'path':path,'git_blob':digest,'sha256':hashlib.sha256(body).hexdigest(),'size':len(body)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        hashes=list(pool.map(source,manifest['files']))
    (OUT/'source-hashes.json').write_text(json.dumps({'commit':manifest['commit'],'files':hashes},indent=2)+'\n')
    url='https://github.com/mamba-org/micromamba-releases/releases/download/2.10.0-0/micromamba-linux-64'
    expected=fetch(url+'.sha256').decode().strip()
    body=fetch(url)
    assert hashlib.sha256(body).hexdigest()==expected
    target=OUT/'tools/micromamba';target.write_bytes(body);target.chmod(0o755)
    (OUT/'tools/micromamba-source.json').write_text(json.dumps({'url':url,'sha256':expected,'size':len(body)},indent=2)+'\n')
    for subdir in ['linux-64','noarch']:
        body=fetch(f'https://conda.software.inl.gov/public/{subdir}/repodata.json')
        (OUT/'tools'/f'inl-{subdir}-repodata.json').write_bytes(body)
        data=json.loads(body)
        for section in ['packages','packages.conda']:
            for filename,value in data.get(section,{}).items():
                if value['name']=='moose':
                    print(subdir,filename,json.dumps(value),flush=True)
    print('BOOTSTRAP_COMPLETE',OUT,flush=True)

if __name__=='__main__':main()
