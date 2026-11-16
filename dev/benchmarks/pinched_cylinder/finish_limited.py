#!/usr/bin/env python3
"""Bound an unsuccessful L1 trace, then finish independent plain controls."""
from pathlib import Path
import datetime
import json
import os
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'data/runs/pinched-cylinder_20261116T184505';HERE=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text())
def write(p,v):
    q=p.with_suffix(p.suffix+'.tmp');q.write_text(json.dumps(v,indent=2)+'\n');q.replace(p)

def main():
    folder=OUT/'million-single-pangu-L1-traced-r0'
    while True:
        d=read(folder/'result.json')
        if d['status']=='completed':print('TRACE_COMPLETED; original suite continues',flush=True);return
        if d['status']=='failed':break
        if d.get('process_wall_seconds',0)>300:
            pid=int(next(iter(d['maps'])))
            note={'intentional_stop':True,'pid':pid,'elapsed_seconds':d['process_wall_seconds'],
                  'reason':'Original single-RHS GPU CSC L1 numeric did not finish within a 300-second profiling bound near full VRAM. The same full MOOSE HOST CSC L1 solve completed correctly (numeric80.7138s). No finite result or GPU metric claimed for interrupted profiling; other vendors/configuration controls proceed.',
                  'at':datetime.datetime.now().astimezone().isoformat()}
            write(folder/'stop-note.json',note);os.kill(pid,signal.SIGTERM);break
        time.sleep(2)
    while len(read(OUT/'suite.json'))<3:time.sleep(1)
    time.sleep(2)
    records=read(OUT/'suite.json');assert len(records)==3 and records[-1]['returncode']!=0
    plan=read(OUT/'suite-plan.json');remaining=[x for x in plan[3:] if not(x['backend']=='pangu' and x['reorder']=='L1' and x['label'].endswith('r1'))]
    write(OUT/'suite-adjustment.json',{'reason':'Bound L1 GPU profiling and avoid repeating an expensive uncharacterized configuration. Complete two untraced controls each for native cuDSS and default Pangu L0; run one bounded untraced Pangu L1 for compatibility diagnosis.','remaining':remaining})
    p=OUT/'datasets/million/metadata.json';meta=read(p);meta['timeout_seconds']=180;write(p,meta)
    for item in remaining:
        command=[sys.executable,'-B',str(HERE/'bench.py'),'run','--case','million','--backend',item['backend'],
                 '--label',item['label'],'--refactors','0','--reorder',item['reorder'] if item['backend']=='pangu' else 'L1']
        begin=time.monotonic()
        with (OUT/(item['label']+'-controller.log')).open('w') as log:
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
            while process.poll() is None:
                write(ROOT/'coordination/pinched_cylinder_progress.json',{'status':'benchmarking_controls','directory':str(OUT),'current':item,'completed_attempts':len(records),'remaining':len(remaining),'trial_elapsed_seconds':time.monotonic()-begin,'updated_at':datetime.datetime.now().astimezone().isoformat()})
                time.sleep(2)
        record={**item,'returncode':process.returncode,'wall_seconds':time.monotonic()-begin};records.append(record);write(OUT/'suite.json',records);print(json.dumps(record),flush=True)
        if process.returncode and item['reorder']!='L1':raise RuntimeError('Default configuration failed')
    print('CONTROLS_COMPLETE',flush=True)

if __name__=='__main__':main()
