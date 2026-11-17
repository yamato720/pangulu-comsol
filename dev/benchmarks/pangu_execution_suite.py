#!/usr/bin/env python3
"""Balanced public CLI comparison for PanguLU worker/direct execution."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core
OUT=Path(json.loads((ROOT/'data/runs/latest-pangu-execution.json').read_text())['directory'])


def write(path,value):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)


def main():
    paths=[ROOT/'config/runtime.json',Path(core.cfg()['comsol_root'])/'bin/comsol',core.sdk()/'lib/libpangulu.so',*core.licence_paths(),*core.module_dir().glob('*')]
    baseline={str(path):core.sha(path) for path in paths if path.is_file()}
    prefs={str(path):hashlib.sha256(path.read_bytes().partition(b'\n')[2]).hexdigest() for path in (Path.home()/'.comsol/v64').glob('*.prefs')}
    write(OUT/'runtime-baseline-0.4.json',baseline);write(OUT/'preference-entry-baseline.json',prefs)
    state={'status':'running','plan':['worker','direct','direct','worker'],'completed':[],'started_at':datetime.datetime.now().astimezone().isoformat()}
    def publish():
        write(OUT/'suite.json',state);write(ROOT/'data/runs/latest-pangu-execution.json',{'directory':str(OUT),'status':state['status'],'current':state.get('current'),'completed':len(state['completed'])})
    publish()
    try:
        for index,mode in enumerate(state['plan'],1):
            state['current']={'step':index,'execution':mode};publish();print('EXECUTION START',index,mode,flush=True)
            command=[str(ROOT/'pangu-comsol'),'test','benchmark','--backend','pangu','--execution',mode,'--project',
                     '/home/lwf/comsol-projects/study3-pangulu-s','--frequencies','all','--threads','8']
            log=OUT/f'step-{index}.cli.log'
            with log.open('w') as stream:
                process=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT)
                while process.poll() is None:
                    pointer=ROOT/'data/runs/latest-test.json'
                    if pointer.is_file():
                        last=json.loads(pointer.read_text());p=Path(last['report'])
                        if last['backend']=='pangu' and p.is_file():
                            trial=json.loads(p.read_text())
                            if trial.get('execution')==mode and trial['runs']:
                                run=trial['runs'][-1];state['current'].update({key:run.get(key) for key in ['phase','elapsed_seconds','current_frequency_Hz','frequency_steps_started']})
                    publish();time.sleep(2)
            match=re.search(r'^TEST_REPORT_JSON (.+)$',log.read_text(),re.M)
            if process.returncode or not match:raise RuntimeError('Execution benchmark failed: '+str(log))
            result=json.loads(match[1]);trial=json.loads(Path(result['report']).read_text());run=trial['runs'][0]
            assert trial['status']=='completed' and trial['inputs_unchanged'] and run['field_records_passed']==236 and all(run['checks'].values())
            row={'step':index,'execution':mode,'seconds':run['validation']['runAll_seconds'],'GPU_sampled_peak_MiB':run['gpu_peak_device_used_MiB'],
                 'fields_passed':236,'model':run['solved_model'],'report':result['report'],'directory':run['directory'],'CPU_relative_L2':run['validation']['reference_worst_relative_L2']}
            state['completed'].append(row);publish();print('EXECUTION COMPLETE',json.dumps(row),flush=True)
        state['current']={'phase':'comparing_saved_results'};publish()
        directory=OUT/'field-comparison';directory.mkdir();classes=directory/'java';classes.mkdir();comsol=Path(core.cfg()['comsol_root'])
        with (directory/'compile.log').open('w') as log:subprocess.run([str(comsol/'java/glnxa64/jre/bin/javac'),'-proc:none','-classpath',str(comsol/'plugins/*'),'-d',str(classes),
              str(ROOT/'dev/benchmarks/mycudss/CompareMyCudssFields.java')],stdout=log,stderr=subprocess.STDOUT,check=True)
        # Saved-field comparison performs no solve; use the original COMSOL launcher.
        command,env,_=core.comsol_command(directory,8,'cudss')
        command+=['batch','-classpathadd',str(classes),'-nosave','-batchlog',str(directory/'batch.log'),'-inputfile',str(classes/'CompareMyCudssFields.class'),*[row['model']['path'] for row in state['completed']]]
        with (directory/'stdout.log').open('w') as stdout,(directory/'stderr.log').open('w') as stderr:result=subprocess.run(command,env=env,cwd=directory,stdout=stdout,stderr=stderr,timeout=600)
        text=(directory/'stdout.log').read_text();rows=[json.loads(line.split(' ',1)[1]) for line in text.splitlines() if line.startswith('NATIVE_COMPARE ')]
        assert result.returncode==0 and 'NATIVE_COMPARE_COMPLETE' in text and len(rows)==3 and all(row['passed'] and row['fields']==236 for row in rows)
        state['field_comparison']={'passed':True,'fields_passed':708,'comparisons':rows,'directory':str(directory)}
        state['runtime_files_unchanged']=all(core.sha(path)==digest for path,digest in baseline.items());assert state['runtime_files_unchanged']
        state['preference_entries_unchanged']=all(hashlib.sha256(Path(path).read_bytes().partition(b'\n')[2]).hexdigest()==digest for path,digest in prefs.items());assert state['preference_entries_unchanged']
        state['summary']={mode:{'seconds':[row['seconds'] for row in state['completed'] if row['execution']==mode],
                   'mean_seconds':statistics.mean(row['seconds'] for row in state['completed'] if row['execution']==mode),
                   'GPU_peak_MiB':[row['GPU_sampled_peak_MiB'] for row in state['completed'] if row['execution']==mode]} for mode in ['worker','direct']}
        state.update(status='completed',finished_at=datetime.datetime.now().astimezone().isoformat());state.pop('current',None);publish();print('EXECUTION RESULT',json.dumps(state['summary']),flush=True)
    except BaseException as error:state.update(status='failed',error=str(error));publish();raise


if __name__=='__main__':main()
