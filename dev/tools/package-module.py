#!/usr/bin/env python3
"""Package module code only. SDKs, licences, binaries and runs stay external."""
import pathlib,tarfile
root=pathlib.Path(__file__).resolve().parents[2];dist=root/'data/runs/packages';dist.mkdir(parents=True,exist_ok=True)
target=dist/'pangulu-comsol-source.tar.gz'
with tarfile.open(target,'w:gz') as t:
 for name in ['pangu-comsol','start.sh','README.md','.gitignore','config','src','dev','docs','licence/README.txt']:
  p=root/name
  entries=[p] if p.is_file() else [q for q in p.rglob('*') if q.is_file() and '__pycache__' not in q.parts]
  for f in entries:t.add(f,arcname='pangulu-comsol/'+str(f.relative_to(root)),recursive=False)
 for name in ['runtime','runtime/module','runtime/dependencies','data','data/models','data/runs','data/archive','pangu','licence']:
  entry=tarfile.TarInfo('pangulu-comsol/'+name);entry.type=tarfile.DIRTYPE;entry.mode=0o755;t.addfile(entry)
print(target)
