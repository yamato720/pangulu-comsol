#!/usr/bin/env python3
"""Exact captured A/b replay for native, direct and worker calibration."""
import collections
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core, testing
OUT=Path(json.loads((ROOT/'data/runs/latest-mycudss.json').read_text())['directory'])
PAIR=Path('/home/lwf/comsol-projects/study3-pangulu-s/cudss_pangulu_bridge/refactor_diagnosis/pair')


def write(path,value):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)


def main():
    while True:
        suite=json.loads((OUT/'suite.json').read_text())
        if suite['status']=='failed':raise RuntimeError('Full suite failed; fixed replay not started')
        if suite['status']=='completed':break
        time.sleep(3)
    out=OUT/'fixed';out.mkdir();cuda=Path(core.cfg()['cuda_root']);native=testing.native_library();binary=out/'fixed_benchmark'
    files=[PAIR/name for name in ['previous_ptr.u64','previous_idx.i32','previous_values.bin','candidate_ptr.u64','candidate_idx.i32','candidate_values.bin']]
    hashes={str(p):core.sha(p) for p in files};write(out/'input-sha256.json',hashes)
    command=['g++','-O2','-std=c++17','-I'+str(core.production_sources()/'include/cudss'),'-I'+str(cuda/'include'),
             str(Path(__file__).with_name('fixed_benchmark.cpp')),'-L'+str(native.parent),'-l:libcudss.so.0.7.1',
             '-L'+str(cuda/'lib64'),'-Wl,-rpath-link,'+str(cuda/'lib64'),'-lcudart','-o',str(binary)]
    with (out/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    state={'status':'running','inputs_sha256':hashes,'binary_sha256':core.sha(binary),'completed':[]}
    orders=[['cudss','mycudss-direct','mycudss-worker'],['mycudss-worker','mycudss-direct','cudss'],['mycudss-direct','cudss','mycudss-worker']]
    for block,order in enumerate(orders,1):
        for backend in order:
            directory=out/f'{block}-{backend}';directory.mkdir();state['current']={'block':block,'backend':backend};write(out/'suite.json',state)
            write(ROOT/'data/runs/latest-mycudss.json',{'directory':str(OUT),'status':'fixed_replay','current':state['current']})
            if backend=='cudss':
                env=core.base_env(8);libroot=native.parent
            else:libroot,env,_=testing.environment(backend,8)
            env['LD_LIBRARY_PATH']=str(libroot)+os.pathsep+str(cuda/'lib64')
            command=[str(binary),str(PAIR),'6'];write(directory/'command.json',{'command':command,'backend':backend,'block':block,
                  'LD_LIBRARY_PATH':env['LD_LIBRARY_PATH'],'native_library_sha256':core.sha(native)})
            print('FIXED START',block,backend,flush=True);maps={};process=None;start=time.monotonic()
            try:
                with (directory/'stdout.log').open('w') as stdout,(directory/'stderr.log').open('w') as stderr:
                    process=subprocess.Popen(command,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
                    while process.poll() is None:
                        for p in Path('/proc').iterdir():
                            if not p.name.isdecimal():continue
                            try:
                                stat=(p/'stat').read_text().rsplit(')',1)[1].split()
                                if int(stat[3])!=process.pid:continue
                                content=(p/'maps').read_text();paths=sorted({l.split()[-1] for l in content.splitlines() if len(l.split())>=6 and any(k in l for k in ['libcudss','libcudart','libcublas','libpangulu'])})
                                if paths:maps[p.name]={'exe':str((p/'exe').resolve()),'libraries':paths}
                            except (OSError,ValueError,IndexError):continue
                        if time.monotonic()-start>180:raise TimeoutError('Fixed replay timeout')
                        time.sleep(.15)
            finally:
                if process is not None and process.poll() is None:os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=10)
            events=[]
            for line in (directory/'stdout.log').read_text().splitlines():
                tag,_,payload=line.partition(' ')
                if tag in ['BRIDGE_ITERATION','CHECK','RESULT']:events.append({'tag':tag,**json.loads(payload)})
            checks=[e for e in events if e['tag']=='CHECK'];iterations=[e for e in events if e['tag']=='BRIDGE_ITERATION']
            assert process.returncode==0 and len(checks)==12 and len(iterations)==6
            assert all(row['passed'] for row in checks) and any(e['tag']=='RESULT' and e['passed'] for e in events)
            mapped={str(Path(name).resolve()) for entry in maps.values() for name in entry['libraries']}
            expected={str(native)}
            if backend!='cudss':expected.add(str((libroot/'libcudss.so.0.7.1').resolve()))
            assert {name for name in mapped if Path(name).name.startswith('libcudss.so')}==expected
            assert not any('libpangulu' in name for name in mapped)
            if backend=='mycudss-worker':assert any(entry['exe']==str(testing.directory()/'worker/mycudss_worker') and str(native) in entry['libraries'] for entry in maps.values())
            row={'block':block,'backend':backend,'seconds':sum(e['seconds'] for e in iterations),
                 'factor_seconds':sum(e['factor_callbacks_seconds'] for e in iterations),'solve_seconds':sum(e['solve_callbacks_seconds'] for e in iterations),
                 'checks_passed':12,'max_relative_error':max(e['relative_x_error'] for e in checks),'maps':maps,'directory':str(directory)}
            write(directory/'result.json',{'exit_code':0,'events':events,**row});state['completed'].append(row);write(out/'suite.json',state)
            print('FIXED COMPLETE',json.dumps({k:row[k] for k in ['block','backend','seconds','checks_passed']}),flush=True)
    assert all(core.sha(p)==digest for p,digest in hashes.items())
    state['summary']={backend:{'seconds':[row['seconds'] for row in state['completed'] if row['backend']==backend],
         'mean_seconds':statistics.mean(row['seconds'] for row in state['completed'] if row['backend']==backend)} for backend in orders[0]}
    state.update(status='completed',checks_passed=108,inputs_unchanged=True);state.pop('current',None);write(out/'suite.json',state)
    print('FIXED RESULT',json.dumps(state['summary']),flush=True)


if __name__=='__main__':main()
