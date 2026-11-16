#!/usr/bin/env python3
"""Isolated local profiling; production components and input models stay intact."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/python'))
from pangulu_comsol import runtime as core

OUT = Path(json.loads((ROOT/'data/runs/latest-bottleneck.json').read_text())['directory'])
SOURCE = OUT/'source'
PRODUCTION = core.module_dir()
CUPTI = OUT/'tools/cupti/nvidia/cuda_cupti'
sdk = core.sdk()
cuda = Path(core.cfg()['cuda_root'])


def write(path, value):
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temp.replace(path)


def command(args):
    with (OUT/'build.log').open('a') as log:
        log.write(repr(args)+'\n');log.flush()
        subprocess.run(list(map(str,args)), stdout=log, stderr=subprocess.STDOUT, check=True)


def build():
    includes = ['-I'+str(sdk/'include'), '-I'+str(cuda/'include'), '-I'+str(ROOT/'src'), '-I'+str(SOURCE)]
    libraries = ['-L'+str(sdk/'lib'), '-L'+str(cuda/'lib64')]
    native = [*libraries, '-Wl,--no-as-needed', '-lpangulu', '-lcudart', '-lcublas', '-lcublasLt', '-ldl', '-lm']
    tracer = ['-DPGL_AUDIT_CUPTI=1','-I'+str(CUPTI/'include')]
    command(['g++','-O2','-std=c++17','-fPIC',*includes,*tracer,'-c',SOURCE/'activity.cpp','-o',OUT/'activity.o'])
    command(['g++','-O2','-std=c++17','-shared','-fPIC',*includes,*tracer,SOURCE/'native_entry.cpp',OUT/'activity.o',
             '-L'+str(CUPTI/'lib'),'-Wl,-rpath,'+str(CUPTI/'lib'),'-l:libcupti.so.12','-pthread','-o',OUT/'native-trace.so'])
    for mode in ['timed','traced']:
        target = OUT/(mode+'-module');target.mkdir(exist_ok=True)
        command(['g++','-O2','-std=c++17','-shared','-fPIC','-DPGL_PROFILE=1',*includes,'-I'+str(ROOT/'src/include/cudss'),
                 ROOT/'src/bridge.cpp','-o',target/'libcudss.so.0.7.1',*libraries,'-lcudart','-Wl,-soname,libcudss.so.0'])
        if mode == 'timed':
            command(['gcc','-O2','-std=gnu11','-DPGL_PROFILE=1',*includes,ROOT/'src/worker.c','-o',target/'pangulu_worker',*native,'-pthread'])
        else:
            command(['gcc','-O2','-std=gnu11','-DPGL_PROFILE=1',*tracer,*includes,'-c',SOURCE/'worker.traced.c','-o',OUT/'worker.o'])
            command(['g++',OUT/'worker.o',OUT/'activity.o','-o',target/'pangulu_worker',*native,
                     '-L'+str(CUPTI/'lib'),'-Wl,-rpath,'+str(CUPTI/'lib'),'-l:libcupti.so.12','-pthread'])
        for name in ['libcudss.so','libcudss.so.0']:
            if not (target/name).exists():(target/name).symlink_to('libcudss.so.0.7.1')
        for name in ['native_license','PanguModule.jar','PanguDesktop.jar']:
            shutil.copy2(PRODUCTION/name,target/name)
        if not (target/'java').exists():(target/'java').symlink_to(PRODUCTION/'java',target_is_directory=True)
        manifest=json.loads((PRODUCTION/'manifest.json').read_text())
        manifest['files']={name:core.sha(target/name) for name in manifest['files']}
        manifest['diagnostic_only']=True
        write(target/'manifest.json',manifest)
    shutil.copy2(ROOT/'dev/benchmarks/s/SStudy3.java',SOURCE/'SStudy3.java')
    classes=OUT/'java';classes.mkdir(exist_ok=True)
    comsol=Path(core.cfg()['comsol_root'])
    command([comsol/'java/glnxa64/jre/bin/javac','-proc:none','-classpath',str(comsol/'plugins/*')+os.pathsep+str(PRODUCTION/'java'),
             '-d',classes,SOURCE/'SStudy3.java'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','pangu-timed','pangu-traced','cudss-timed','cudss-traced'])
    parser.add_argument('--frequencies',choices=['20000','all'],default='20000');parser.add_argument('--suffix',default='');args=parser.parse_args()
    if args.mode=='prepare':
        paths=[ROOT/'config/runtime.json',sdk/'lib/libpangulu.so',*core.licence_paths(),*PRODUCTION.glob('*')]
        baseline={str(p):core.sha(p) for p in paths if p.is_file()}
        write(OUT/'baseline.json',baseline)
        build();print('Prepared isolated timed and traced modules',flush=True);return
    spec=importlib.util.spec_from_file_location('s_runner',ROOT/'dev/benchmarks/s/run.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    ordinary_gpu=runner.gpu
    def gpu_sample():
        result=ordinary_gpu()
        extra=subprocess.run(['nvidia-smi','--query-gpu=clocks.current.sm,clocks.current.memory,power.draw,temperature.gpu',
                              '--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5)
        if not extra.returncode:
            for key,value in zip(['sm_clock_MHz','memory_clock_MHz','power_W','temperature_C'],extra.stdout.strip().split(',')):
                try:result[key]=float(value.strip())
                except ValueError:result[key]=value.strip()
        return result
    runner.gpu=gpu_sample
    backend='cudss' if args.mode.startswith('cudss') else 'pangu'
    mode='traced' if 'traced' in args.mode else 'timed'
    core.module_dir=lambda:OUT/(mode+'-module')
    original=core.comsol_command
    def launch_settings(directory,threads=None,backend='pangu'):
        cmd,env,libroot=original(directory,threads,backend)
        env['PC_TRACE_DIR']=str(directory)
        if backend=='cudss':
            if mode=='traced':env['LD_PRELOAD']=str(OUT/'native-trace.so')
            # Runtime returns the Java component root as its third result.
            # This benchmark's backend mapping checks need the actual cuDSS root.
            libroot=Path(core.cfg()['comsol_root'])/'ext/cudss/glnxa64'
        return cmd,env,libroot
    core.comsol_command=launch_settings
    inputs={name:{'path':str(Path('/home/lwf/comsol-projects/study3-pangulu-s/models')/file)}
            for name,file in [('source','S_after_mapping.mph'),('reference','S_full59_solved.mph')]}
    for item in inputs.values():item['sha256']=core.sha(item['path'])
    label=args.mode+'-'+args.frequencies+('-'+args.suffix if args.suffix else '')
    trial={'status':'running','directory':str(OUT),'scope':'Study3','mode':args.mode,'inputs':inputs,'sdk':core.sdk_info(),
           'threads':8,'runs':[],'started_at':datetime.datetime.now().astimezone().isoformat(),
           'background_GUI_pid':53384,'background_GUI_present_at_start':Path('/proc/53384').exists(),
           'background_load_note':'User processes are not modified. CPU/GPU telemetry and ambient load are recorded. Diagnostic timings are not substituted for historical production baselines.',
           'load_average_at_start':list(os.getloadavg())}
    parameters=argparse.Namespace(threads=8,backend=backend,timeout=1800)
    def publish():
        write(OUT/(label+'.json'),trial)
        write(OUT/'progress.json',{'phase':label,'status':trial['status'],'runs':[{k:r.get(k) for k in ['label','phase','status','elapsed_seconds','current_frequency_Hz','error']} for r in trial['runs']]})
    publish()
    try:
        runner.run_phase(trial,label,runner.ORIGINAL if args.frequencies=='all' else [20000],parameters,publish)
        if mode=='traced':
            files=list((OUT/label).glob('activities-*.jsonl'))
            if not files:raise RuntimeError('No CUPTI trace produced')
            summaries=[]
            for p in files:
                last=p.read_bytes()[-4000:].decode(errors='replace').splitlines()
                found=[json.loads(line) for line in last if line.startswith('{') and '"type":"summary"' in line]
                if not found or found[-1]['dropped']!=0:raise RuntimeError('CUPTI completion/dropped-record check failed: '+str(p))
                summaries.append({'file':str(p),'bytes':p.stat().st_size,**found[-1]})
            trial['CUPTI']=summaries
        trial['status']='completed'
    except BaseException as error:
        trial.update(status='failed',error=str(error));raise
    finally:
        trial['inputs_unchanged']=all(core.sha(v['path'])==v['sha256'] for v in inputs.values())
        baseline=json.loads((OUT/'baseline.json').read_text());trial['production_files_unchanged']=all(Path(p).is_file() and core.sha(p)==digest for p,digest in baseline.items())
        publish()
        print(json.dumps({'label':label,'status':trial['status'],'inputs_unchanged':trial['inputs_unchanged'],
                          'production_files_unchanged':trial['production_files_unchanged'],'report':str(OUT/(label+'.json'))}),flush=True)


if __name__=='__main__':main()
