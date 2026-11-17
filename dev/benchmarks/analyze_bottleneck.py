#!/usr/bin/env python3
"""Aggregate disjoint host intervals and CUPTI GPU activity interval unions."""
import collections
import csv
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(json.loads((ROOT/'data/runs/latest-bottleneck.json').read_text())['directory'])


def union_ns(intervals, start, end):
    spans=sorted((max(a,start),min(b,end)) for a,b in intervals if b>start and a<end and b>a)
    total=0;right=start
    for a,b in spans:
        if b>right:total+=b-max(a,right);right=b
    return total


def analyze(label, host_only=False):
    run=OUT/label
    report=json.loads((OUT/(label+'.json')).read_text());assert report['status']=='completed'
    result=report['runs'][0];assert result['checks'] and all(result['checks'].values())
    text=(run/'stdout.log').read_text();clock=json.loads(re.search(r'^BRIDGE_SWEEP_CLOCK_JSON (.+)$',text,re.M)[1])
    begin,end=clock['begin_ns'],clock['end_ns'];seconds=(end-begin)*1e-9
    groups=collections.defaultdict(list)
    for line in (run/'stderr.log').read_text().splitlines():
        tag,_,payload=line.partition(' ')
        if tag in ['PGL_WORKER_PHASE','PGL_EXEC_SPAN','PC_WORKER']:
            groups[tag].append(json.loads(payload))
    spans=sorted(groups['PGL_EXEC_SPAN'],key=lambda x:x['begin_ns'])
    assert all(begin<=x['begin_ns']<x['end_ns']<=end for x in spans)
    assert all(a['end_ns']<=b['begin_ns'] for a,b in zip(spans,spans[1:]))
    worker=sorted(groups['PGL_WORKER_PHASE'],key=lambda x:x['begin_ns'])
    worker_phases=collections.defaultdict(lambda:{'calls':0,'seconds':0})
    for p in worker:
        row=worker_phases[str(p['phase'])];row['calls']+=1;row['seconds']+=(p['end_ns']-p['begin_ns'])*1e-9
    callback=sum((x['end_ns']-x['begin_ns'])*1e-9 for x in spans)
    sdk=sum(x['seconds'] for x in worker_phases.values())
    cpu_format=sum(x.get(k,0) for x in groups['PC_WORKER'] for k in ['conversion_seconds','pattern_seconds','value_update_seconds'])
    release=sum(x.get('release_seconds',0) for x in groups['PC_WORKER'])
    disjoint=[]
    if spans:
        for phase,name in [('1027','PanguLU analysis'),('4','PanguLU numeric factorization'),('8','PanguLU refactorization'),('1008','PanguLU solve')]:
            disjoint.append({'stage':name,'seconds':worker_phases[phase]['seconds']})
        disjoint.extend([{'stage':'Worker format/index/value processing (measured)','seconds':cpu_format},
                         {'stage':'Worker backend resource release','seconds':release},
                         {'stage':'Remaining bridge, copies, IPC and synchronization inside execute','seconds':callback-sdk-cpu_format-release},
                         {'stage':'COMSOL and lifecycle work outside execute callbacks','seconds':seconds-callback}])
        assert abs(sum(x['seconds'] for x in disjoint)-seconds)<1e-6
        assert all(x['seconds']>=-1e-6 for x in disjoint)
    output={'label':label,'frequency_count':result['validation']['frequency_count'],'fields_passed':result['field_records_passed'],
            'runAll_seconds':seconds,'worker_phases':dict(worker_phases),'execute_callbacks':len(spans),
            'execute_seconds':callback,'SDK_seconds':sdk,'disjoint_host_stages':disjoint,
            'measured_format_parts':{k:sum(x.get(k,0) for x in groups['PC_WORKER']) for k in ['conversion_seconds','pattern_seconds','value_update_seconds']},
            'GPU_activity_note':'GPU interval unions overlap host API calls. Do not add GPU busy time to host wall categories. Time without traced GPU work is not automatically CPU arithmetic.'}
    gpu=collections.defaultdict(list);api_intervals=[];runtime_totals=collections.Counter();kernel_totals=collections.Counter();scopes=[];trace_summaries=[]
    late_gpu=[]
    for path in ([] if host_only else sorted(run.glob('activities-*.jsonl'))):
        clock_delta=None;local_scopes={};summary=None;events=[]
        with path.open() as stream:
            for line in stream:
                event=json.loads(line);kind=event['type']
                if kind=='clock':
                    clock_delta=(event['host_before']+event['host_after'])/2-event['cupti_ns']
                    output.setdefault('clock_calibrations',[]).append({'file':str(path),'offset_ns':clock_delta,
                          'calibration_bracket_ns':event['host_after']-event['host_before']})
                elif kind=='scope_begin':local_scopes.setdefault(event['external'],{})['begin']=event['timestamp']
                elif kind=='scope_end':local_scopes.setdefault(event['external'],{})['end']=event['timestamp']
                elif kind=='summary':summary=event
                elif kind in ['kernel','memcpy','memset','runtime','driver']:
                    assert clock_delta is not None
                    a,b=event['start']+clock_delta,event['end']+clock_delta
                    if kind in ['kernel','memcpy','memset'] and b>end:
                        late_gpu.append({'kind':kind,'start_after_end_seconds':(a-end)*1e-9,
                                         'end_after_end_seconds':(b-end)*1e-9,'name':event.get('name','')})
                    if b<=a or a>=end or b<=begin:continue
                    if kind in ['kernel','memcpy','memset']:
                        gpu[kind].append((a,b))
                        if kind=='kernel':kernel_totals[event['name']]+=max(0,min(b,end)-max(a,begin))*1e-9
                    else:
                        api_intervals.append((a,b))
                        if kind=='runtime':runtime_totals[event['name']]+=max(0,min(b,end)-max(a,begin))*1e-9
        assert summary is not None and summary['dropped']==0
        trace_summaries.append({'file':str(path),**summary})
        for identity,s in local_scopes.items():
            assert 'begin' in s and 'end' in s
            scopes.append({'id':identity,'begin':s['begin']+clock_delta,'end':s['end']+clock_delta})
    if trace_summaries:
        active=[span for intervals in gpu.values() for span in intervals]
        output['GPU']={kind+'_union_seconds':union_ns(intervals,begin,end)*1e-9 for kind,intervals in gpu.items()}
        output['GPU']['active_union_seconds']=union_ns(active,begin,end)*1e-9
        output['GPU']['active_fraction']=output['GPU']['active_union_seconds']/seconds
        output['GPU']['host_time_without_traced_GPU_work_seconds']=seconds-output['GPU']['active_union_seconds']
        output['CUDA_API_CPU_union_seconds']=union_ns(api_intervals,begin,end)*1e-9
        output['top_runtime_API_inclusive_seconds']=runtime_totals.most_common(12)
        output['top_kernels_cumulative_seconds_may_overlap']=kernel_totals.most_common(12)
        output['trace_summaries']=trace_summaries
        output['GPU_work_after_runAll_end']=late_gpu
        if worker:
            scopes.sort(key=lambda x:x['begin']);assert len(scopes)==len(worker),(len(scopes),len(worker))
            phase_gpu=collections.defaultdict(lambda:collections.Counter())
            for scope,phase in zip(scopes,worker):
                a,b=scope['begin'],scope['end'];row=phase_gpu[str(phase['phase'])]
                assert abs(a-phase['begin_ns'])<100_000_000 and abs(b-phase['end_ns'])<100_000_000
                row['calls']+=1;row['scope_seconds']+=(b-a)*1e-9
                row['GPU_active_union_seconds']+=union_ns(active,a,b)*1e-9
                row['kernel_union_seconds']+=union_ns(gpu['kernel'],a,b)*1e-9
            output['SDK_phase_GPU']=dict(phase_gpu)
    (run/('bottleneck-host-only.json' if host_only else 'bottleneck.json')).write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    if disjoint:
        with (run/'host_stages.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['stage','seconds']);writer.writeheader();writer.writerows(disjoint)
    print(json.dumps({k:output.get(k) for k in ['label','runAll_seconds','SDK_seconds','measured_format_parts','GPU','SDK_phase_GPU']},ensure_ascii=False))
    return output


if __name__=='__main__':
    import sys
    host_only='--host-only' in sys.argv
    labels=[arg for arg in sys.argv[1:] if arg!='--host-only'] or [p.stem for p in OUT.glob('*-*.json') if p.stem.startswith(('pangu-','cudss-'))]
    for label in labels:analyze(label,host_only)
