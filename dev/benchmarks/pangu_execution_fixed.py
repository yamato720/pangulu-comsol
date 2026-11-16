#!/usr/bin/env python3
"""Constant captured A0 isolates execution location from COMSOL assembly."""
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core
OUT=Path(json.loads((ROOT/'data/runs/latest-pangu-execution.json').read_text())['directory'])
PAIR=Path('/home/lwf/comsol-projects/study3-pangulu-s/cudss_pangulu_bridge/refactor_diagnosis/pair')


def main():
    assert json.loads((OUT/'suite.json').read_text())['status']=='completed'
    directory=OUT/'fixed';directory.mkdir();source=directory/'fixed_a0.cpp'
    source.write_text((ROOT/'dev/benchmarks/mycudss/fixed_benchmark.cpp').read_text().replace('iteration%2','0'))
    cuda=Path(core.cfg()['cuda_root']);module=core.module_dir();binary=directory/'fixed_a0'
    with (directory/'compile.log').open('w') as log:subprocess.run(['g++','-O2','-std=c++17','-I'+str(ROOT/'src/include/cudss'),'-I'+str(cuda/'include'),str(source),
        '-L'+str(module),'-l:libcudss.so.0.7.1','-L'+str(cuda/'lib64'),'-lcudart','-o',str(binary)],stdout=log,stderr=subprocess.STDOUT,check=True)
    hashes={str(p):core.sha(p) for p in PAIR.iterdir() if p.name.endswith(('.u64','.i32','.bin'))}
    state={'status':'running','scope':'Same captured A0 repeated six times, two manufactured RHS each; CUDA completion inside timing; verification after timing.',
           'inputs_sha256':hashes,'completed':[]}
    def publish():
        (directory/'report.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    publish()
    try:
        for block,order in enumerate([['worker','direct'],['direct','worker'],['worker','direct'],['direct','worker']],1):
            for mode in order:
                output=directory/f'{block}-{mode}';output.mkdir();env=core.clean_env(threads=8,execution=mode);env['LD_LIBRARY_PATH']=str(module)+':'+env['LD_LIBRARY_PATH']
                print('PANGU FIXED START',block,mode,flush=True)
                with (output/'stdout.log').open('w') as stdout,(output/'stderr.log').open('w') as stderr:r=subprocess.run([str(binary),str(PAIR),'6'],env=env,stdout=stdout,stderr=stderr,timeout=180)
                events=[]
                for line in (output/'stdout.log').read_text().splitlines():
                    tag,_,payload=line.partition(' ')
                    if tag in ['BRIDGE_ITERATION','CHECK','RESULT']:events.append({'tag':tag,**json.loads(payload)})
                steps=[row for row in events if row['tag']=='BRIDGE_ITERATION'];checks=[row for row in events if row['tag']=='CHECK']
                assert r.returncode==0 and len(steps)==6 and len(checks)==12 and all(row['passed'] for row in checks)
                assert any(row['tag']=='RESULT' and row['passed'] for row in events)
                row={'block':block,'execution':mode,'seconds':sum(step['seconds'] for step in steps),'checks_passed':12,'max_relative_error':max(check['relative_x_error'] for check in checks),'directory':str(output)}
                state['completed'].append(row);publish();print('PANGU FIXED COMPLETE',json.dumps(row),flush=True)
        assert all(core.sha(path)==digest for path,digest in hashes.items())
        state['summary']={mode:{'seconds':[row['seconds'] for row in state['completed'] if row['execution']==mode],
              'mean_seconds':statistics.mean(row['seconds'] for row in state['completed'] if row['execution']==mode)} for mode in ['worker','direct']}
        state.update(status='completed',checks_passed=96,inputs_unchanged=True);publish();print('PANGU FIXED RESULT',json.dumps(state['summary']),flush=True)
    except BaseException as error:state.update(status='failed',error=str(error));publish();raise


if __name__=='__main__':main()
