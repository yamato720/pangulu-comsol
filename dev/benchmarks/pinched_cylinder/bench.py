#!/usr/bin/env python3
"""Internal pinned shell-matrix comparison; customer application is unchanged."""
from pathlib import Path
import argparse
import importlib.util
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'data/runs/pinched-cylinder_20261116T184505'
DUEL=ROOT/'dev/benchmarks/matrix_duel'

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result

runner=module('matrix_runner',DUEL/'run.py');runner.OUT=OUT
analyzer=module('matrix_analyzer',DUEL/'analyze.py')

def build():
    sdk=runner.core.sdk();cuda=Path(runner.core.cfg()['cuda_root']);binary=OUT/'bin';binary.mkdir(exist_ok=True)
    archive=OUT/'harness';archive.mkdir(exist_ok=True)
    for name in ['duel.cpp','activity.cpp','activity.h','run.py','analyze.py']:
        (archive/name).write_bytes((DUEL/name).read_bytes())
    flags=['g++','-O2','-std=c++17','-I'+str(archive),'-I'+str(cuda/'include')]
    def compile(args):
        with (OUT/'build.log').open('a') as log:
            log.write(repr(list(map(str,args)))+'\n');log.flush()
            subprocess.run(list(map(str,args)),stdout=log,stderr=subprocess.STDOUT,check=True)
    compile([*flags,'-DPGL_AUDIT_CUPTI=1','-I'+str(runner.CUPTI/'include'),'-c',archive/'activity.cpp','-o',binary/'activity.o'])
    for backend in ['pangu','mycudss']:
        for traced in [False,True]:
            target=binary/(backend+('-traced' if traced else '-plain'))
            args=[*flags,'-DPGL_AUDIT_CUPTI='+str(int(traced)),'-DBENCH_PANGU='+str(int(backend=='pangu')),
                  '-I'+str(sdk/'include'),'-I'+str(ROOT/'src/include/cudss'),archive/'duel.cpp']
            if traced:args += [binary/'activity.o','-L'+str(runner.CUPTI/'lib'),'-Wl,-rpath,'+str(runner.CUPTI/'lib'),'-l:libcupti.so.12']
            if backend=='pangu':args += ['-L'+str(sdk/'lib'),'-Wl,--no-as-needed','-lpangulu','-lcublas','-lcublasLt']
            else:args += ['-L'+str(runner.testing.directory()/'direct'),'-l:libcudss.so.0.7.1']
            args += ['-L'+str(cuda/'lib64'),'-lcudart','-ldl','-lm','-pthread','-o',target]
            compile(args)
    runner.write(OUT/'benchmark-provenance.json',{'sdk':runner.core.sdk_info(),'native_cudss':str(runner.testing.native_library()),
         'native_cudss_sha256':runner.core.sha(runner.testing.native_library()),'mycudss':runner.testing.verify(),
         'harness_sha256':{p.name:runner.core.sha(p) for p in archive.iterdir()},'binary_sha256':{p.name:runner.core.sha(p) for p in binary.iterdir()},
         'method':'Original full shell Jacobian, explicit zeros retained, real general LU. GPU CSC PanguLU L1/L2 versus GPU CSR NVIDIA cuDSS through calibrated mycudss-direct. Host conversion/upload outside timers. Analysis separately timed. Physical RHS checked against original system; manufactured RHS checked against known solutions. This is not whole MOOSE/COMSOL application time.'})
    print('BUILT',OUT,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['build','run']);p.add_argument('--case',default='small');p.add_argument('--backend',choices=['pangu','mycudss'],default='pangu');p.add_argument('--label');p.add_argument('--trace',action='store_true');p.add_argument('--refactors',type=int,default=0);p.add_argument('--reorder',choices=['L0','L1'],default='L1');a=p.parse_args()
    if a.action=='build':build()
    else:
        state=runner.run_case(a.case,a.backend,a.trace,a.label,a.refactors,'csc',a.reorder)
        analyzer.analyze(state['directory'])
