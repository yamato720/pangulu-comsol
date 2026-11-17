#!/usr/bin/env python3
"""Identical S graph and changing coefficients; compare only caller phase policy."""
from pathlib import Path
import ast
import collections
import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core,testing
PAIR=Path('/home/lwf/comsol-projects/study3-pangulu-s/cudss_pangulu_bridge/refactor_diagnosis/pair')

def main():
    suite=core.read(OUT/'suite-r1.json');assert len(suite)==4 and all(x['returncode']==0 for x in suite)
    directory=OUT/'reuse-control';directory.mkdir()
    # Passive timed modules have no CUPTI dependency; only host/SDK stage clocks.
    shutil.copytree(OUT/'module',OUT/'timed-module',symlinks=True)
    commands=[ast.literal_eval(line) for line in (OUT/'build.log').read_text().splitlines() if line.startswith('[')]
    bridge=next(x for x in commands if any(str(v).endswith('/bridge.cpp') for v in x))
    target=bridge.index('-o')+1;bridge[target]=str(OUT/'timed-module/libcudss.so.0.7.1')
    with (directory/'build.log').open('w') as log:subprocess.run(bridge,stdout=log,stderr=subprocess.STDOUT,check=True)
    m=core.read(OUT/'timed-module/manifest.json');m['files']={name:core.sha(OUT/'timed-module'/name) for name in m['files']};core.write(OUT/'timed-module/manifest.json',m)
    cuda=Path(core.cfg()['cuda_root']);native=testing.native_library()
    source=(ROOT/'dev/benchmarks/mycudss/fixed_benchmark.cpp').read_text()
    source=source.replace('if(argc!=3)return 2;','if(argc!=4)return 2;')
    needle='if(ptr!=read<uint64_t>'
    assert needle in source;source=source.replace(needle,'for(size_t k=0;k<matrices[0].size();k++)matrices[1][k]=matrices[0][k]*1.01;\n  '+needle)
    calls='ok(cudssExecute(h,CUDSS_PHASE_REORDERING,config,data,A,nullptr,nullptr));ok(cudssExecute(h,CUDSS_PHASE_SYMBOLIC_FACTORIZATION,config,data,A,nullptr,nullptr));ok(cudssExecute(h,CUDSS_PHASE_FACTORIZATION,config,data,A,nullptr,nullptr));'
    assert calls in source;source=source.replace(calls,'if(iteration==0||std::string(argv[3])=="repeat-analysis"){'+calls+'}else{ok(cudssExecute(h,CUDSS_PHASE_REFACTORIZATION,config,data,A,nullptr,nullptr));}')
    needle='ok(cudssCreate(&h));ok(cudssConfigCreate(&config));ok(cudssDataCreate(h,&data));'
    assert needle in source;source=source.replace(needle,'double api_begin=now();'+needle+'std::cout<<"INITIALIZE {\\"seconds\\":"<<now()-api_begin<<"}"<<std::endl;')
    src=directory/'phase_policy.cpp';src.write_text(source);binary=directory/'phase_policy'
    with (directory/'build.log').open('a') as log:
        subprocess.run(['g++','-O2','-std=c++17','-I'+str(ROOT/'src/include/cudss'),'-I'+str(cuda/'include'),str(src),'-L'+str(native.parent),'-l:libcudss.so.0.7.1','-L'+str(cuda/'lib64'),'-Wl,-rpath-link,'+str(cuda/'lib64'),'-lcudart','-o',str(binary)],stdout=log,stderr=subprocess.STDOUT,check=True)
    old_module=core.module_dir;core.module_dir=lambda:OUT/'timed-module'
    state={'status':'running','scope':'Same captured S graph. Coefficients alternate A0 and1.01*A0; each RHS is independently generated for its matrix. Only caller phase schedule changes. Six numeric iterations, two RHS each; three independent processes per backend/policy.','source_sha256':core.sha(src),'completed':[]}
    try:
        configurations=[('cudss','repeat-analysis'),('pangu','repeat-analysis'),('cudss','refactor'),('pangu','refactor')]
        for block in range(3):
            for backend,policy in configurations if block%2==0 else list(reversed(configurations)):
                label=f'{block+1}-{backend}-{policy}';folder=directory/label;folder.mkdir()
                env=core.base_env(8) if backend=='cudss' else core.clean_env(threads=8,execution='direct')
                env['LD_LIBRARY_PATH']=str(native.parent if backend=='cudss' else OUT/'timed-module')+':'+str(cuda/'lib64')+('' if backend=='cudss' else ':'+str(core.sdk()/'lib'))
                command=[str(binary),str(PAIR),'6',policy]
                with (folder/'stdout.log').open('w') as stdout,(folder/'stderr.log').open('w') as stderr:r=subprocess.run(command,env=env,stdout=stdout,stderr=stderr,timeout=180)
                events=[];sdk=[];backend_summary=[]
                for line in (folder/'stdout.log').read_text().splitlines():
                    tag,_,body=line.partition(' ')
                    if tag in ['BRIDGE_ITERATION','INITIALIZE','CHECK','RESULT']:events.append({'tag':tag,**json.loads(body)})
                for line in (folder/'stderr.log').read_text().splitlines():
                    tag,_,body=line.partition(' ')
                    if tag=='PGL_WORKER_PHASE':sdk.append(json.loads(body))
                    elif tag=='PGL_BACKEND_SUMMARY':backend_summary.append(json.loads(body))
                checks=[x for x in events if x['tag']=='CHECK'];iterations=[x for x in events if x['tag']=='BRIDGE_ITERATION']
                assert r.returncode==0 and len(checks)==12 and all(x['passed'] for x in checks) and any(x['tag']=='RESULT' and x['passed'] for x in events)
                if backend=='pangu':
                    assert len(backend_summary)==1 and backend_summary[0]['handles_created']==1 and backend_summary[0]['analysis_calls']==1 and backend_summary[0]['numeric_calls']==1 and backend_summary[0]['refactor_calls']==5
                row={'block':block+1,'backend':backend,'policy':policy,'factor_seconds':sum(x['factor_callbacks_seconds'] for x in iterations),'solve_seconds':sum(x['solve_callbacks_seconds'] for x in iterations),'callback_seconds':sum(x['seconds'] for x in iterations),'initialization_seconds':next(x['seconds'] for x in events if x['tag']=='INITIALIZE'),'checks_passed':12,'maximum_relative_error':max(x['relative_x_error'] for x in checks),'SDK_phase_counts':dict(collections.Counter(str(x['phase']) for x in sdk)),'backend_summary':backend_summary,'directory':str(folder)}
                row['initialization_plus_callbacks_seconds']=row['callback_seconds']+row['initialization_seconds']
                core.write(folder/'result.json',{'status':'completed','events':events,**row});state['completed'].append(row);core.write(directory/'report.json',state)
                print('CONTROL_COMPLETE',backend,policy,block+1,row['initialization_plus_callbacks_seconds'],flush=True)
        state['summary']={}
        for backend,policy in configurations:
            rows=[r for r in state['completed'] if r['backend']==backend and r['policy']==policy]
            state['summary'][backend+'/'+policy]={k:statistics.mean(r[k] for r in rows) for k in ['factor_seconds','solve_seconds','callback_seconds','initialization_seconds','initialization_plus_callbacks_seconds']}
        state['status']='completed';core.write(directory/'report.json',state);print(json.dumps(state['summary']),flush=True)
    finally:core.module_dir=old_module

if __name__=='__main__':main()
