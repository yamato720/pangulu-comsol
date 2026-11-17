#!/usr/bin/env python3
"""Isolated profiling of the actual current S sweep, without changing vendor binaries."""
from pathlib import Path
import argparse
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types

OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core,testing
PRODUCTION=core.module_dir();SOURCE=OUT/'source'
CUPTI=ROOT/'data/runs/bottleneck_20261116T125114/tools/cupti/nvidia/cuda_cupti'
def write(p,v):core.write(p,v)
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def compile(args):
    with (OUT/'build.log').open('a') as log:
        log.write(repr(list(map(str,args)))+'\n');log.flush();subprocess.run(list(map(str,args)),stdout=log,stderr=subprocess.STDOUT,check=True)

def prepare():
    sdk=core.sdk();cuda=Path(core.cfg()['cuda_root'])
    paths=[ROOT/'config/runtime.json',Path(core.cfg()['comsol_root'])/'bin/comsol',sdk/'lib/libpangulu.so',*core.licence_paths(),*[p for p in PRODUCTION.rglob('*') if p.is_file()]]
    write(OUT/'baseline.json',{'files':{str(p):core.sha(p) for p in paths},'prefs':{str(p):hashlib.sha256(p.read_bytes().partition(b'\n')[2]).hexdigest() for p in (Path.home()/'.comsol/v64').glob('*.prefs')}})
    shutil.copytree(ROOT/'src',SOURCE/'production',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(PRODUCTION,OUT/'module',symlinks=True)
    for name in ['activity.cpp','activity.h']:shutil.copy2(ROOT/'dev/benchmarks/matrix_duel'/name,SOURCE/name)
    shutil.copy2(ROOT/'dev/benchmarks/s_gap/source/observer_entry.cpp',SOURCE/'observer_entry.cpp')
    compile(['g++','-O2','-std=c++17','-fPIC','-shared','-DPGL_AUDIT_CUPTI=1','-I'+str(SOURCE),'-I'+str(CUPTI/'include'),'-I'+str(cuda/'include'),SOURCE/'activity.cpp',SOURCE/'observer_entry.cpp','-L'+str(CUPTI/'lib'),'-Wl,-rpath,'+str(CUPTI/'lib'),'-l:libcupti.so.12','-Wl,-soname,observer.so','-pthread','-o',OUT/'observer.so'])
    observer_libraries=['-L'+str(OUT),'-Wl,--no-as-needed','-l:observer.so','-Wl,-rpath,'+str(OUT)]
    commands=[ast.literal_eval(line) for line in (PRODUCTION/'build.log').read_text().splitlines() if line.startswith('[')]
    for command in commands:
        if not any(str(x).endswith(('/backend.c','/bridge.cpp')) for x in command):continue
        original=Path(command[command.index('-o')+1]).parent
        updated=[str(x).replace(str(original),str(OUT/'module')).replace(str(ROOT/'src'),str(SOURCE/'production')).replace('-DPGL_PROFILE=0','-DPGL_PROFILE=1') for x in command]
        if any(str(x).endswith('/bridge.cpp') for x in command):updated+=observer_libraries
        compile(updated)
    manifest=core.read(OUT/'module/manifest.json');manifest['diagnostic_only']=True;manifest['profiling']='PGL_PROFILE=1; original CUDA object and numerical operations retained'
    manifest['files']={name:core.sha(OUT/'module'/name) for name in manifest['files']};write(OUT/'module/manifest.json',manifest)
    code=(ROOT/'dev/benchmarks/mycudss/direct.cpp').read_text()
    header='''#include <time.h>
#include <stdint.h>
#include <unistd.h>
static uint64_t stamp(){timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
struct NvSpan {const char*kind;int phase;uint64_t data,begin;NvSpan(const char*k,int p,uint64_t d):kind(k),phase(p),data(d),begin(stamp()){}~NvSpan(){fprintf(stderr,"NV_API_SPAN {\\"kind\\":\\"%s\\",\\"phase\\":%d,\\"data\\":%llu,\\"begin_ns\\":%llu,\\"end_ns\\":%llu}\\n",kind,phase,(unsigned long long)data,(unsigned long long)begin,(unsigned long long)stamp());}};
'''
    for signature,span in [('cudssCreate(cudssHandle_t* h){','NvSpan span("create",0,0);'),('cudssDataCreate(cudssHandle_t h,cudssData_t* d){','NvSpan span("data_create",0,0);'),('cudssExecute(cudssHandle_t h,int p,cudssConfig_t c,cudssData_t d,cudssMatrix_t a,cudssMatrix_t x,cudssMatrix_t b){','NvSpan span("execute",p,(uint64_t)(uintptr_t)d);')]:
        assert signature in code;code=code.replace(signature,signature+span)
    # Includes precede the diagnostic scope's fprintf declaration.
    code=code.replace('static void* library()',header+'\nstatic void* library()')
    native_source=SOURCE/'native-timed.cpp';native_source.write_text(code)
    native_root=OUT/'nvidia/direct';native_root.mkdir(parents=True)
    compile(['g++','-O2','-std=c++17','-shared','-fPIC','-I'+str(ROOT/'src/include/cudss'),'-I'+str(cuda/'include'),native_source,'-ldl','-Wl,-soname,libcudss.so.0','-o',native_root/'libcudss.so.0.7.1']+observer_libraries)
    for name in ['libcudss.so','libcudss.so.0']:(native_root/name).symlink_to('libcudss.so.0.7.1')
    write(OUT/'nvidia/manifest.json',{'version':1,'native_library_sha256':core.sha(testing.native_library()),'files':{'direct/libcudss.so.0.7.1':core.sha(native_root/'libcudss.so.0.7.1')}})
    java=(ROOT/'dev/benchmarks/mycudss/SStudy3.java').read_text()
    (SOURCE/'SStudy3.java').write_text(java);classes=OUT/'java';classes.mkdir()
    comsol=Path(core.cfg()['comsol_root'])
    compile([comsol/'java/glnxa64/jre/bin/javac','-proc:none','-classpath',str(comsol/'plugins/*')+':'+str(OUT/'module/java'),'-d',classes,SOURCE/'SStudy3.java'])
    write(OUT/'provenance.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'working_patch_sha256':hashlib.sha256(subprocess.check_output(['git','diff'])).hexdigest(),'SDK':core.sdk_info(),'native_cudss':str(testing.native_library()),'native_cudss_sha256':core.sha(testing.native_library()),'profile_sources':{str(p.relative_to(SOURCE)):core.sha(p) for p in SOURCE.rglob('*') if p.is_file()},'no_extra_native_synchronization':True,'observer':'Normal external backend DSO dependency; native opt-in constructor; no Java runtime-system calls or LD_PRELOAD; concurrent CUPTI activity and CLOCK_MONOTONIC timestamps.'})
    print('PREPARED',OUT,flush=True)

def run(backend,frequencies,traced,label):
    runner=load('s_gap_runner',ROOT/'dev/benchmarks/mycudss/runner.py')
    core.module_dir=lambda:OUT/'module';testing.directory=lambda:OUT/'nvidia'
    original=core.comsol_command
    def command(*args,**kwargs):
        cmd,env,m=original(*args,**kwargs)
        if traced:env['PC_AUDIT_CUPTI']='1'
        env['PC_TRACE_DIR']=str(OUT/label)
        return cmd,env,m
    core.comsol_command=command
    models=Path('/home/lwf/comsol-projects/study3-pangulu-s/models')
    inputs={name:{'path':str(models/file),'sha256':core.sha(models/file)} for name,file in [('source','S_after_mapping.mph'),('reference','S_full59_solved.mph')]}
    state={'status':'running','directory':str(OUT),'inputs':inputs,'sdk':core.sdk_info(),'backend':backend,'traced':traced,'threads':8,'runs':[]}
    def publish():
        write(OUT/(label+'.json'),state)
        r=state['runs'][-1] if state['runs'] else {}
        status={'status':state['status'],'label':label,'directory':str(OUT),'current':{k:r.get(k) for k in ['phase','elapsed_seconds','current_frequency_Hz','frequency_steps_started','error']}}
        write(ROOT/'coordination/s_gap_progress.json',status);write(ROOT/'data/runs/latest-s-gap.json',status)
    args=types.SimpleNamespace(backend='pangu' if backend=='pangu' else 'mycudss-direct',execution='direct' if backend=='pangu' else None,threads=8,timeout=1200)
    publish()
    try:
        runner.run_phase(state,label,runner.ORIGINAL if frequencies=='all' else [20000],args,publish)
        if traced:
            traces=[]
            for p in (OUT/label).glob('activities-*.jsonl'):
                rows=[json.loads(x) for x in p.read_bytes()[-4096:].decode().splitlines() if x.startswith('{"type":"summary"')];assert rows and rows[-1]['dropped']==0;traces.append(rows[-1])
            assert traces;state['trace_summaries']=traces
        state['original_files_unchanged']=all(core.sha(path)==value for path,value in core.read(OUT/'baseline.json')['files'].items());assert state['original_files_unchanged']
        state['status']='completed'
    except BaseException as e:state.update(status='failed',error=type(e).__name__+': '+str(e));raise
    finally:publish()
    print('PROFILE_COMPLETE',label,state['runs'][0]['validation']['runAll_seconds'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run']);p.add_argument('--backend',choices=['pangu','cudss']);p.add_argument('--frequencies',choices=['20000','all'],default='20000');p.add_argument('--trace',action='store_true');p.add_argument('--label');a=p.parse_args()
    if a.action=='prepare':prepare()
    else:run(a.backend,a.frequencies,a.trace,a.label)
