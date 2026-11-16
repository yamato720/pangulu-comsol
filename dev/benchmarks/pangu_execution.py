#!/usr/bin/env python3
"""Isolated acceptance for the shared worker/direct Pangu backend."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core
OUT=Path(json.loads((ROOT/'data/runs/latest-pangu-execution.json').read_text())['directory'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('execution',choices=['worker','direct']);parser.add_argument('--frequencies',choices=['20000','all'],default='20000');parser.add_argument('--label');args=parser.parse_args()
    core.module_dir=lambda:OUT/'module'
    spec=importlib.util.spec_from_file_location('execution_runner',ROOT/'dev/benchmarks/mycudss/runner.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    classes=OUT/'java'
    if not classes.is_dir():
        classes.mkdir();comsol=Path(core.cfg()['comsol_root'])
        with (OUT/'compile-helper.log').open('w') as log:subprocess.run([str(comsol/'java/glnxa64/jre/bin/javac'),'-proc:none','-classpath',str(comsol/'plugins/*')+':'+str(OUT/'module/java'),'-d',str(classes),str(ROOT/'dev/benchmarks/mycudss/SStudy3.java')],check=True,stdout=log,stderr=subprocess.STDOUT)
    inputs={name:{'path':str(Path('/home/lwf/comsol-projects/study3-pangulu-s/models')/file)} for name,file in [('source','S_after_mapping.mph'),('reference','S_full59_solved.mph')]}
    for item in inputs.values():item['sha256']=core.sha(item['path'])
    label=args.label or args.execution+'-'+args.frequencies
    trial={'status':'running','directory':str(OUT),'inputs':inputs,'sdk':core.sdk_info(),'execution':args.execution,'runs':[]}
    def publish():
        runner.write(OUT/(label+'.json'),trial);runner.write(OUT/'progress.json',{'label':label,'status':trial['status'],'runs':[{k:r.get(k) for k in ['phase','status','elapsed_seconds','current_frequency_Hz','error']} for r in trial['runs']]})
    parameters=types.SimpleNamespace(backend='pangu',execution=args.execution,threads=8,timeout=1800)
    publish()
    try:
        runner.run_phase(trial,label,runner.ORIGINAL if args.frequencies=='all' else [20000],parameters,publish);trial['status']='completed'
    except BaseException as error:trial.update(status='failed',error=str(error));raise
    finally:
        trial['inputs_unchanged']=all(core.sha(item['path'])==item['sha256'] for item in inputs.values());publish()
        print(json.dumps({'status':trial['status'],'execution':args.execution,'report':str(OUT/(label+'.json'))}),flush=True)


if __name__=='__main__':main()
