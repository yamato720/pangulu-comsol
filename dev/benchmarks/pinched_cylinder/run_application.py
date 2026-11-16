#!/usr/bin/env python3
"""Run the unmodified upstream application driver and additionally capture PETSc systems."""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'data/runs/pinched-cylinder_20261116T184505'
SOURCE=OUT/'sources/MOOSE-with-Solved-Tech'
sys.path.insert(0,str(SOURCE/'tools'))
import run_case

def main():
    p=argparse.ArgumentParser();p.add_argument('--profile',choices=['small','million'],required=True)
    p.add_argument('--solver',choices=['pangulu','reference'],required=True);p.add_argument('--label',required=True)
    a=p.parse_args();folder=OUT/a.label
    original_run=run_case.run
    def captured_run(command,location,label):
        if label=='application':
            command += ['-ksp_view_mat','binary:'+str(folder/'A.petsc'),
                        '-ksp_view_rhs','binary:'+str(folder/'b.petsc'),
                        '-ksp_view_solution','binary:'+str(folder/'x.petsc')]
        try:return original_run(command,location,label)
        finally:
            manifest=location/'run.json'
            if label=='application' and manifest.exists():
                value=json.loads(manifest.read_text());value['command']=command
                manifest.write_text(json.dumps(value,indent=2)+'\n')
    run_case.run=captured_run
    sys.argv=['run_case.py','pinched-cylinder','--exe',str(OUT/'moose-env/bin/moose-opt'),
              '--profile',a.profile,'--solver',a.solver,'--reference-solver','mumps',
              '--plugin',str(OUT/'libmoose_pangulu.so'),'--output',str(folder)]
    run_case.main()

if __name__=='__main__':main()
