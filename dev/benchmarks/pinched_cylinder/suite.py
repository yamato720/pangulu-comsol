#!/usr/bin/env python3
"""Sequential, alternating vendor runs. Never shares GPU with a MOOSE solve."""
from pathlib import Path
import datetime
import json
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'data/runs/pinched-cylinder_20261116T184505'
HERE=Path(__file__).resolve().parent

def write(path,data):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(path)

def main():
    plan=[]
    configs=[('mycudss','native_auto'),('pangu','L0'),('pangu','L1')]
    for traced,count in [(True,1),(False,2)]:
        for repetition in range(count):
            for backend,reorder in configs if repetition%2==0 else list(reversed(configs)):
                plan.append({'backend':backend,'traced':traced,'reorder':reorder,
                             'label':f'million-single-{backend}-{reorder}-'+('traced' if traced else 'plain')+f'-r{repetition}'})
    assert not (OUT/'suite.json').exists()
    records=[];started=time.monotonic()
    write(OUT/'suite-plan.json',plan)
    for item in plan:
        command=[sys.executable,'-B',str(HERE/'bench.py'),'run','--case','million','--backend',item['backend'],
                 '--label',item['label'],'--refactors','0','--reorder',item['reorder'] if item['backend']=='pangu' else 'L1']
        if item['traced']:command.append('--trace')
        begin=time.monotonic()
        with (OUT/(item['label']+'-controller.log')).open('w') as log:
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
            while process.poll() is None:
                write(ROOT/'coordination/pinched_cylinder_progress.json',{'status':'benchmarking','directory':str(OUT),
                      'current':item,'completed':len(records),'planned':len(plan),'trial_elapsed_seconds':time.monotonic()-begin,
                      'suite_elapsed_seconds':time.monotonic()-started,'updated_at':datetime.datetime.now().astimezone().isoformat()})
                time.sleep(2)
        record={**item,'returncode':process.returncode,'wall_seconds':time.monotonic()-begin};records.append(record)
        write(OUT/'suite.json',records);print(json.dumps(record),flush=True)
        if process.returncode and not(item['backend']=='pangu' and item['reorder']=='L1' and item['traced']):raise RuntimeError('Trial failed: '+item['label'])
    print('SUITE_COMPLETE',len(records),time.monotonic()-started,flush=True)

if __name__=='__main__':main()
