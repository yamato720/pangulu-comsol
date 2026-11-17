#!/usr/bin/env python3
"""Native GPU-resident matrix duel; all data preparation precedes phase timers."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core,testing
HERE=Path(__file__).resolve().parent
OUT=Path(json.loads((ROOT/'data/runs/latest-matrix-duel.json').read_text())['directory'])
CUPTI=ROOT/'data/runs/bottleneck_20261116T125114/tools/cupti/nvidia/cuda_cupti'


def write(path,value):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)


def prepare():
    binary=OUT/'bin';binary.mkdir(exist_ok=True);sdk=core.sdk();cuda=Path(core.cfg()['cuda_root'])
    def compile(args):
        with (OUT/'build.log').open('a') as log:
            log.write(repr(list(map(str,args)))+'\n');log.flush();subprocess.run(list(map(str,args)),stdout=log,stderr=subprocess.STDOUT,check=True)
    compile(['g++','-O2','-std=c++17',HERE/'generate.cpp','-o',binary/'generate'])
    flags=['g++','-O2','-std=c++17','-I'+str(HERE),'-I'+str(cuda/'include')]
    compile([*flags,'-DPGL_AUDIT_CUPTI=1','-I'+str(CUPTI/'include'),'-c',HERE/'activity.cpp','-o',binary/'activity.o'])
    for backend in ['pangu','mycudss']:
        for traced in [False,True]:
            target=binary/(backend+('-traced' if traced else '-plain'))
            args=[*flags,'-DPGL_AUDIT_CUPTI='+str(int(traced)),'-DBENCH_PANGU='+str(int(backend=='pangu')),
                  '-I'+str(sdk/'include'),'-I'+str(ROOT/'src/include/cudss'),HERE/'duel.cpp']
            if traced:args += [binary/'activity.o','-L'+str(CUPTI/'lib'),'-Wl,-rpath,'+str(CUPTI/'lib'),'-l:libcupti.so.12']
            if backend=='pangu':args += ['-L'+str(sdk/'lib'),'-Wl,--no-as-needed','-lpangulu','-lcublas','-lcublasLt']
            else:args += ['-L'+str(testing.directory()/'direct'),'-l:libcudss.so.0.7.1']
            args += ['-L'+str(cuda/'lib64'),'-lcudart','-ldl','-lm','-pthread','-o',target]
            compile(args)
    datasets=OUT/'datasets';datasets.mkdir(exist_ok=True)
    old=Path('/home/lwf/comsol-projects/study3-pangulu-s/cudss_pangulu_bridge/refactor_diagnosis/pair');captured=datasets/'s_complex';captured.mkdir(exist_ok=True)
    expected=json.loads((old/'manifest.json').read_text())['files']
    for filename,original in [('ptr.u64','previous_ptr.u64'),('idx.i32','previous_idx.i32'),('values.bin','previous_values.bin')]:
        assert core.sha(old/original)==expected[original]['sha256'];shutil.copy2(old/original,captured/filename);assert core.sha(captured/filename)==expected[original]['sha256']
    write(captured/'metadata.json',{'n':300475,'nnz':15688748,'dtype':'complex','type':'general','source':'Actual captured S canonical CSC A0; explicit stored zeros retained.'})
    for name,side,kind in [('pilot_spd',12,'spd'),('poisson3d_48',48,'spd'),('transport3d_48',48,'general')]:
        directory=datasets/name;result=subprocess.run([str(binary/'generate'),str(directory),str(side),kind],capture_output=True,text=True,check=True)
        write(directory/'metadata.json',json.loads(result.stdout))
    write(OUT/'dataset-sha256.json',{str(p.relative_to(OUT)):core.sha(p) for p in datasets.rglob('*') if p.is_file()})
    write(OUT/'provenance.json',{'SDK':core.sdk_info(),'native_cudss':str(testing.native_library()),'native_cudss_sha256':core.sha(testing.native_library()),
           'mycudss_manifest':testing.verify(),'CUPTI':str(CUPTI),'CUPTI_sha256':core.sha(CUPTI/'lib/libcupti.so.12'),
           'sources':{p.name:core.sha(p) for p in HERE.iterdir() if p.is_file() and p.suffix in {'.cpp','.h','.py'}},
           'binaries':{p.name:core.sha(p) for p in binary.iterdir() if p.is_file()},
           'method':'PanguLU original public C API, GPU CSC64/32 (or explicit CSR check); mycudss-direct/native NVIDIA cuDSS GPU CSR32. Equivalent layouts, three numeric variants and eight RHS columns prepared/uploaded outside phase clocks. No COMSOL, PanguLU cuDSS adapter, socket or worker.'})
    print('PREPARED',OUT,flush=True)


def run_case(case,backend,traced=False,label=None,refactors=4,layout='csc',reorder='L0'):
    metadata=json.loads((OUT/'datasets'/case/'metadata.json').read_text());label=label or case+'-'+backend+('-traced' if traced else '-plain')
    directory=OUT/label;directory.mkdir();cuda=Path(core.cfg()['cuda_root'])
    if backend=='pangu':
        env=core.base_env(8);env.update(LD_LIBRARY_PATH=str(core.sdk()/'lib')+':'+str(cuda/'lib64'),PANGULU_LICENSE_PATHS=','.join(map(str,core.licence_paths())))
    else:
        _,env,_=testing.environment('mycudss-direct',8);env['LD_LIBRARY_PATH']=str(testing.directory()/'direct')+':'+str(cuda/'lib64')
        layout='csr'
    env['PC_TRACE_DIR']=str(directory)
    env['PC_MATRIX_REORDER']=reorder
    env['PC_MATRIX_SINGLE_VALUES']='1' if metadata.get('single_gpu_values_buffer',False) else '0'
    env['PC_MATRIX_MAX_RHS']=str(metadata.get('rhs_columns',8))
    command=[str(OUT/'bin'/(backend+('-traced' if traced else '-plain'))),str(OUT/'datasets'/case),metadata['dtype'],metadata['type'],str(refactors),layout]
    write(directory/'command.json',{'command':command,'backend':backend,'traced':traced,'layout':layout,'reorder':reorder,'metadata':metadata})
    state={'status':'running','label':label,'backend':backend,'case':case,'traced':traced,'layout':layout,'directory':str(directory),'metadata':metadata,'maps':{},'started_at':datetime.datetime.now().astimezone().isoformat()}
    process=None;start=time.monotonic();telemetry=[];last_sample=-100
    try:
        with (directory/'stdout.log').open('w') as stdout,(directory/'stderr.log').open('w') as stderr:
            process=subprocess.Popen(command,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
            while process.poll() is None:
                try:
                    maps=Path(f'/proc/{process.pid}/maps').read_text();libs=sorted({line.split()[-1] for line in maps.splitlines() if len(line.split())>=6 and any(s in line for s in ['libpangulu','libcudss','libcudart','libcublas','libcupti'])})
                    if libs:state['maps'][str(process.pid)]={'executable':str(Path(f'/proc/{process.pid}/exe').resolve()),'libraries':libs}
                except OSError:pass
                elapsed=time.monotonic()-start
                if elapsed-last_sample>=1:
                    result=subprocess.run(['nvidia-smi','--query-gpu=name,uuid,driver_version,memory.used,memory.free,utilization.gpu,clocks.current.sm,temperature.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5)
                    if not result.returncode:telemetry.append({'elapsed':elapsed,'data':result.stdout.strip()})
                    last_sample=elapsed
                state['process_wall_seconds']=elapsed;write(directory/'result.json',state)
                limit=metadata.get('trace_timeout_seconds',metadata.get('timeout_seconds',360)) if traced else metadata.get('timeout_seconds',360)
                if elapsed>limit:raise TimeoutError('Matrix phase test exceeded timeout')
                time.sleep(.15)
        events=[]
        for line in (directory/'stdout.log').read_text().splitlines():
            tag,_,payload=line.partition(' ')
            if tag in ['MATRIX','DEVICE','PHASE','MEMORY','CHECK','RESULT']:events.append({'tag':tag,**json.loads(payload)})
        checks=[e for e in events if e['tag']=='CHECK'];phases=[e for e in events if e['tag']=='PHASE']
        state.update(exit_code=process.returncode,events=events,checks_passed=sum(e['passed'] for e in checks))
        assert process.returncode==0 and len(checks)==(refactors+1)*(1 if metadata.get('rhs_columns',8)==1 else 2)+int(metadata.get('physical_rhs',False)) and all(e['passed'] for e in checks)
        assert any(e['tag']=='RESULT' and e['passed'] for e in events)
        libraries={str(Path(path).resolve()) for ev in state['maps'].values() for path in ev['libraries']}
        self_inventory={str(Path(line.removeprefix('LOADED ')).resolve()) for line in (directory/'stdout.log').read_text().splitlines() if line.startswith('LOADED ')}
        state['self_mapped_libraries']=sorted(self_inventory);libraries|=self_inventory
        if backend=='pangu':assert str(core.sdk()/'lib/libpangulu.so') in libraries and not any('libcudss' in Path(p).name for p in libraries)
        else:
            assert str(testing.native_library()) in libraries and str((testing.directory()/'direct/libcudss.so.0.7.1').resolve()) in libraries
            assert not any('libpangulu' in p for p in libraries)
        if traced:
            summaries=[]
            for p in directory.glob('activities-*.jsonl'):
                tail=p.read_bytes()[-3000:].decode().splitlines();summary=[json.loads(line) for line in tail if line.startswith('{"type":"summary"')]
                assert summary and summary[-1]['dropped']==0;summaries.append(summary[-1])
            assert summaries;state['trace_summaries']=summaries
        state['status']='completed'
    except BaseException as error:state.update(status='failed',error=str(error));raise
    finally:
        if process is not None and process.poll() is None:os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=10)
        state['process_wall_seconds']=time.monotonic()-start;state['telemetry']=telemetry;write(directory/'result.json',state)
    print('MATRIX_COMPLETE',json.dumps({'label':label,'status':state['status'],'checks_passed':state['checks_passed'],'phase_seconds':{name:sum(p['wall_seconds'] for p in phases if p['name']==name) for name in ['analysis','reordering','symbolic','numeric','refactor','solve']}}),flush=True)
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','run']);parser.add_argument('--case',default='pilot_spd');parser.add_argument('--backend',choices=['pangu','mycudss'],default='pangu');parser.add_argument('--trace',action='store_true');parser.add_argument('--label');parser.add_argument('--refactors',type=int,default=4);parser.add_argument('--layout',choices=['csc','csr'],default='csc');args=parser.parse_args()
    if args.action=='prepare':prepare()
    else:run_case(args.case,args.backend,args.trace,args.label,args.refactors,args.layout)
