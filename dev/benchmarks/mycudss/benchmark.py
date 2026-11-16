#!/usr/bin/env python3
"""Optional S acceptance toolkit invoked by pangu-comsol test benchmark."""
import argparse
import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

PROJECT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(PROJECT/'src/python'))
from pangulu_comsol import runtime as core, testing
from runner import run_phase, ORIGINAL, write


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--backend',choices=list(testing.BACKENDS),required=True)
    parser.add_argument('--project',type=Path,required=True);parser.add_argument('--frequencies',choices=['20000','all'],default='20000')
    parser.add_argument('--threads',type=int,default=8);parser.add_argument('--repeat',type=int,default=1);parser.add_argument('--execution',choices=['worker','direct']);args=parser.parse_args()
    if not 1<=args.threads<=len(os.sched_getaffinity(0)):parser.error('threads exceeds available CPU affinity')
    if not 1<=args.repeat<=10:parser.error('repeat must be 1..10')
    if args.backend.startswith('mycudss'):testing.verify()
    base=core.ROOT/'data/runs';base.mkdir(parents=True,exist_ok=True)
    lock=(base/'.s-gpu.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    run_backend=args.backend+('-'+(args.execution or 'worker') if args.backend=='pangu' else '')
    output=base/('test_'+run_backend+'_'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S_%f'));output.mkdir()
    inputs={name:{'path':str((args.project/'models'/file).resolve(strict=True))} for name,file in [('source','S_after_mapping.mph'),('reference','S_full59_solved.mph')]}
    for value in inputs.values():value['sha256']=core.sha(value['path'])
    comsol=Path(core.cfg()['comsol_root']);classes=output/'java';classes.mkdir()
    source=Path(__file__).with_name('SStudy3.java')
    command=[str(comsol/'java/glnxa64/jre/bin/javac'),'-proc:none','-classpath',str(comsol/'plugins/*'),'-d',str(classes),str(source),str(core.production_sources()/'java/PanguModule.java')]
    with (output/'compile.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    trial={'status':'running','directory':str(output),'backend':args.backend,'threads':args.threads,'execution':args.execution or 'worker' if args.backend=='pangu' else None,'frequencies':args.frequencies,'runs':[],
           'inputs':inputs,'sdk':dict(core.sdk_info(),used=True) if args.backend=='pangu' else {'path':str(core.ROOT/'pangu'),'used':False},'native_library':str(testing.native_library()) if args.backend!='pangu' else None,
           'native_library_sha256':core.sha(testing.native_library()) if args.backend!='pangu' else None,'started_at':datetime.datetime.now().astimezone().isoformat()}
    args.timeout=1800
    def publish():
        write(output/'report.json',trial)
        write(base/'latest-test.json',{'directory':str(output),'backend':args.backend,'status':trial['status'],'report':str(output/'report.json')})
    publish()
    try:
        for index in range(args.repeat):
            label=('full_59' if args.frequencies=='all' else 'single_20kHz')+'_'+str(index+1)
            run_phase(trial,label,ORIGINAL if args.frequencies=='all' else [20000],args,publish)
        trial['status']='completed'
    except BaseException as error:
        trial.update(status='failed',error=str(error));raise
    finally:
        trial['inputs_unchanged']=all(core.sha(value['path'])==value['sha256'] for value in inputs.values())
        trial['finished_at']=datetime.datetime.now().astimezone().isoformat();publish()
    print('TEST_REPORT_JSON '+json.dumps({'status':trial['status'],'backend':args.backend,'report':str(output/'report.json'),
           'runs':[{'seconds':r['validation']['runAll_seconds'],'fields_passed':r['field_records_passed']} for r in trial['runs']]},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
