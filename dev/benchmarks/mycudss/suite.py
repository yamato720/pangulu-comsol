#!/usr/bin/env python3
"""Run the public CLI in balanced order, then compare saved fields to native."""
import collections
import datetime
import json
import os
from pathlib import Path
import re
import statistics
import hashlib
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core
OUT=Path(json.loads((ROOT/'data/runs/latest-mycudss.json').read_text())['directory'])
CLI=ROOT/'runtime/app-env/bin/pangu-comsol'
PLAN=['cudss','mycudss-direct','mycudss-worker','mycudss-worker','mycudss-direct','cudss']
state={'status':'running','started_at':datetime.datetime.now().astimezone().isoformat(),'plan':PLAN,'completed':[]}


def write(path,value):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)


def publish():
    write(OUT/'suite.json',state)
    write(ROOT/'data/runs/latest-mycudss.json',{'directory':str(OUT),'status':state['status'],'current':state.get('current'),
          'completed':len(state['completed']),'report':str(OUT/'suite.json')})


def main():
    preferences={str(path):hashlib.sha256(path.read_bytes().partition(b'\n')[2]).hexdigest()
                 for path in (Path.home()/'.comsol/v64').glob('*.prefs')}
    write(OUT/'preference-entry-baseline.json',preferences)
    publish()
    try:
        for index,backend in enumerate(PLAN,1):
            state['current']={'step':index,'backend':backend};publish()
            command=[str(CLI),'--home',str(ROOT),'test','benchmark','--backend',backend,'--project','/home/lwf/comsol-projects/study3-pangulu-s',
                     '--frequencies','all','--threads','8']
            log=OUT/f'step-{index}.cli.log';started=time.monotonic()
            print('SUITE START',index,backend,flush=True)
            with log.open('w') as stream:
                process=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT)
                while process.poll() is None:
                    state['current']['elapsed_seconds']=time.monotonic()-started
                    pointer=ROOT/'data/runs/latest-test.json'
                    if pointer.is_file():
                        latest=json.loads(pointer.read_text());report=Path(latest['report'])
                        if report.is_file() and latest['backend']==backend:
                            child=json.loads(report.read_text())
                            if child['runs']:
                                run=child['runs'][-1]
                                state['current'].update({key:run.get(key) for key in ['phase','current_frequency_Hz','frequency_steps_started','elapsed_seconds']})
                    publish();time.sleep(2)
            text=log.read_text();match=re.search(r'^TEST_REPORT_JSON (.+)$',text,re.M)
            if process.returncode or not match:raise RuntimeError(f'CLI test failed; see {log}')
            result=json.loads(match[1]);child=json.loads(Path(result['report']).read_text());run=child['runs'][0]
            assert child['status']=='completed' and child['inputs_unchanged'] and run['field_records_passed']==236
            assert all(run['checks'].values())
            row={'step':index,'backend':backend,'report':result['report'],'directory':run['directory'],
                 'seconds':run['validation']['runAll_seconds'],'fields_passed':run['field_records_passed'],
                 'GPU_sampled_peak_MiB':run['gpu_peak_device_used_MiB'],'model':run['solved_model'],
                 'max_relative_L2_to_CPU':run['validation']['reference_worst_relative_L2']}
            state['completed'].append(row);publish();print('SUITE COMPLETE',json.dumps(row,ensure_ascii=False),flush=True)
        state['current']={'phase':'direct_field_comparison_to_native'};publish()
        comparison=OUT/'native-comparison';comparison.mkdir()
        classes=comparison/'java';classes.mkdir();comsol=Path(core.cfg()['comsol_root'])
        compile=[str(comsol/'java/glnxa64/jre/bin/javac'),'-proc:none','-classpath',str(comsol/'plugins/*'),'-d',str(classes),str(Path(__file__).with_name('CompareMyCudssFields.java'))]
        with (comparison/'compile.log').open('w') as log:subprocess.run(compile,stdout=log,stderr=subprocess.STDOUT,check=True)
        command,env,_=core.comsol_command(comparison,8,'cudss')
        command+=['batch','-classpathadd',str(classes),'-nosave','-batchlog',str(comparison/'batch.log'),'-inputfile',str(classes/'CompareMyCudssFields.class'),
                  *[row['model']['path'] for row in state['completed']]]
        write(comparison/'command.json',{'command':command})
        with (comparison/'stdout.log').open('w') as stdout,(comparison/'stderr.log').open('w') as stderr:
            result=subprocess.run(command,env=env,cwd=comparison,stdout=stdout,stderr=stderr,timeout=600)
        text=(comparison/'stdout.log').read_text();records=[json.loads(line.split(' ',1)[1]) for line in text.splitlines() if line.startswith('NATIVE_COMPARE ')]
        assert result.returncode==0 and 'NATIVE_COMPARE_COMPLETE' in text and len(records)==5
        assert all(row['passed'] and row['fields']==236 for row in records)
        state['native_comparison']={'passed':True,'field_records_passed':1180,'comparisons':records,'directory':str(comparison)}
        fields=[line.removeprefix('NATIVE_FIELD ') for line in text.splitlines() if line.startswith('NATIVE_FIELD ')]
        (comparison/'fields.csv').write_text('candidate,frequency_Hz,field,relative_L2,relative_max,passed\n'+'\n'.join(fields)+'\n')
        baseline=json.loads((OUT/'baseline.json').read_text());state['baseline_files_unchanged']={path:core.sha(path)==digest for path,digest in baseline.items()}
        assert all(ok for path,ok in state['baseline_files_unchanged'].items() if not path.endswith('.prefs'))
        state['preference_entries_unchanged']=all(hashlib.sha256(Path(path).read_bytes().partition(b'\n')[2]).hexdigest()==digest for path,digest in preferences.items())
        assert state['preference_entries_unchanged']
        assert all(core.sha(row['model']['path'])==row['model']['sha256'] for row in state['completed'])
        state['summary']={backend:{'seconds':[row['seconds'] for row in state['completed'] if row['backend']==backend],
                       'mean_seconds':statistics.mean(row['seconds'] for row in state['completed'] if row['backend']==backend),
                       'whole_GPU_peak_MiB':[row['GPU_sampled_peak_MiB'] for row in state['completed'] if row['backend']==backend]}
                       for backend in dict.fromkeys(PLAN)}
        state.update(status='completed',finished_at=datetime.datetime.now().astimezone().isoformat());state.pop('current',None);publish()
        print('SUITE RESULT',json.dumps(state['summary'],ensure_ascii=False),flush=True)
    except BaseException as error:
        state.update(status='failed',error=str(error));publish();raise


if __name__=='__main__':main()
