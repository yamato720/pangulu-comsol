#!/usr/bin/env python3
import json
from pathlib import Path
from run import ROOT,OUT,run_case,write
from analyze import analyze


def main():
    cases=['s_complex','poisson3d_48','transport3d_48']
    state={'status':'running','cases':cases,'plain_trials':3,'trace_trials':2,'completed':[]}
    def publish():
        write(OUT/'suite.json',state);write(ROOT/'data/runs/latest-matrix-duel.json',{'directory':str(OUT),'status':state['status'],'current':state.get('current'),'completed':len(state['completed'])})
    publish()
    try:
        for traced,blocks in [(False,3),(True,2)]:
            for block in range(blocks):
                order=['pangu','mycudss'] if block%2==0 else ['mycudss','pangu']
                # Reverse case order as well to reduce consistent thermal-order bias.
                for case in (cases if block%2==0 else list(reversed(cases))):
                    for backend in order:
                        label=f'{case}-{backend}-'+('trace' if traced else 'plain')+f'-{block+1}'
                        state['current']={'case':case,'backend':backend,'traced':traced,'block':block+1};publish()
                        run_case(case,backend,traced,label,4,'csc');metrics=analyze(OUT/label)
                        state['completed'].append({'label':label,'case':case,'backend':backend,'traced':traced,'block':block+1,'metrics':metrics['metrics'],'checks_passed':metrics['checks_passed']});publish()
        for path,digest in json.loads((OUT/'dataset-sha256.json').read_text()).items():
            from pangulu_comsol import runtime as core
            assert core.sha(OUT/path)==digest
        state.update(status='completed',inputs_unchanged=True);state.pop('current',None);publish()
    except BaseException as error:state.update(status='failed',error=str(error));publish();raise


if __name__=='__main__':main()
