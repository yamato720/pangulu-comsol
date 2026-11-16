#!/usr/bin/env python3
"""Fetch pinned, unmodified NVIDIA CUDA 12.9 wheel contents from PyPI."""
import concurrent.futures,hashlib,json,pathlib,urllib.request,zipfile
BASE=pathlib.Path(__file__).resolve().parents[2]/'runtime/dependencies';BASE.mkdir(parents=True,exist_ok=True)
PACKAGES={'nvidia-cuda-runtime-cu12':'12.9.79','nvidia-cublas-cu12':'12.9.2.10','nvidia-cuda-nvcc-cu12':'12.9.86'}
def fetch(item):
 name,version=item;op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with op.open(f'https://pypi.org/pypi/{name}/{version}/json',timeout=30) as f:meta=json.load(f)
 entry=next(u for u in meta['urls'] if u['filename'].endswith('x86_64.whl'))
 wheel=BASE/entry['filename']
 if not wheel.exists() or hashlib.sha256(wheel.read_bytes()).hexdigest()!=entry['digests']['sha256']:
  with op.open(entry['url'],timeout=60) as response,wheel.with_suffix('.part').open('wb') as dest:
   while chunk:=response.read(4*1024*1024):dest.write(chunk)
  wheel.with_suffix('.part').replace(wheel)
 with wheel.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
 assert actual==entry['digests']['sha256']
 with zipfile.ZipFile(wheel) as z:z.extractall(BASE/'packages')
 print(name,version,'verified',flush=True)
 return dict(package=name,version=version,url=entry['url'],sha256=actual)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:records=list(pool.map(fetch,PACKAGES.items()))
root=BASE/'cuda-12.9';(root/'include').mkdir(parents=True,exist_ok=True);(root/'lib64').mkdir(exist_ok=True)
for d in ['cuda_runtime','cublas','cuda_nvcc']:
 for p in (BASE/'packages/nvidia'/d/'include').iterdir():
  q=root/'include'/p.name
  if not q.exists():q.symlink_to(p)
 for p in (BASE/'packages/nvidia'/d/'lib').glob('*.so*'):
  q=root/'lib64'/p.name
  if not q.exists():q.symlink_to(p)
  if '.so.' in p.name:
   q=root/'lib64'/(p.name.split('.so.')[0]+'.so')
   if not q.exists():q.symlink_to(p)
(BASE/'manifest.json').write_text(json.dumps(records,indent=2)+'\n');print(root,flush=True)
