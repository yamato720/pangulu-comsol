#!/usr/bin/env python3
"""Phase wall clocks and actual device-activity interval unions."""
import collections
import json
from pathlib import Path


def union(intervals,begin,end):
    spans=sorted((max(a,begin),min(b,end)) for a,b in intervals if b>a and b>begin and a<end)
    total=0;right=begin
    for a,b in spans:
        if b>right:total+=b-max(a,right);right=b
    return total*1e-9


def classify(phase):
    name=phase['name']
    if name in ['analysis','reordering','symbolic']:return 'analysis'
    if name=='numeric':return 'numeric'
    if name=='physical_solve':return 'physical_solve'
    if name=='solve' and phase['iteration']==0:return 'initial_known_solve_'+str(phase['nrhs'])
    if name=='refactor' and phase['iteration']>=2:return 'refactor_steady'
    if name=='solve' and phase['iteration']>=2:return 'solve_'+str(phase['nrhs'])+'_steady'
    return None


def analyze(directory):
    directory=Path(directory);result=json.loads((directory/'result.json').read_text());assert result['status']=='completed'
    phases=[dict(e) for e in result['events'] if e['tag']=='PHASE'];checks=[e for e in result['events'] if e['tag']=='CHECK']
    gpu=collections.defaultdict(list);apis=[];kernels=[];scopes={};trace=[]
    for path in directory.glob('activities-*.jsonl'):
        summary=None;clock=None
        for line in path.open():
            event=json.loads(line);kind=event['type']
            if kind=='clock':
                clock=event;assert abs((event['host_before']+event['host_after'])/2-event['cupti_ns'])<1_000_000
            elif kind in ['kernel','memcpy','memset']:
                assert event['end']>=event['start']
                gpu[kind].append((event['start'],event['end']))
                if kind=='kernel':kernels.append(event)
            elif kind in ['runtime','driver']:apis.append((event['start'],event['end']))
            elif kind=='scope_begin':scopes.setdefault(event['external'],{})['begin']=event['timestamp']
            elif kind=='scope_end':scopes.setdefault(event['external'],{})['end']=event['timestamp']
            elif kind=='summary':summary=event
        assert clock and summary and summary['dropped']==0;trace.append({'path':str(path),'summary':summary,'clock':clock})
    if trace:
        assert len(phases)==len(scopes)
        active=[span for spans in gpu.values() for span in spans]
        for p in phases:
            scope=scopes[p['id']];assert abs(scope['begin']-p['begin_ns'])<100_000_000 and abs(scope['end']-p['end_ns'])<100_000_000
            a,b=p['begin_ns'],p['end_ns']
            p['GPU_kernel_seconds']=union(gpu['kernel'],a,b)
            p['GPU_memcpy_seconds']=union(gpu['memcpy'],a,b)
            p['GPU_memset_seconds']=union(gpu['memset'],a,b)
            p['GPU_active_seconds']=union(active,a,b)
            p['GPU_coverage']=p['GPU_active_seconds']/p['wall_seconds']
            p['no_GPU_activity_wall_seconds']=p['wall_seconds']-p['GPU_active_seconds']
            p['CUDA_API_CPU_union_seconds']=union(apis,a,b)
            p['kernel_launches']=sum(e['start']>=a and e['end']<=b for e in kernels)
            assert p['GPU_active_seconds']<=p['wall_seconds']+1e-6
    groups=collections.defaultdict(list)
    for p in phases:
        key=classify(p)
        if key:groups[key].append(p)
    metrics={}
    fields=['wall_seconds','process_cpu_seconds','GPU_kernel_seconds','GPU_memcpy_seconds','GPU_memset_seconds','GPU_active_seconds','no_GPU_activity_wall_seconds','kernel_launches']
    for key,rows in groups.items():
        values={field:sum(p.get(field,0) for p in rows)/(1 if key=='analysis' else len(rows)) for field in fields if field in rows[0]}
        values['calls']=len(rows)
        if trace:values['GPU_coverage']=values['GPU_active_seconds']/values['wall_seconds']
        metrics[key]=values
    output={'case':result['case'],'backend':result['backend'],'layout':result['layout'],'metadata':result['metadata'],
            'checks_passed':len(checks),'maximum_x_error':max(e['relative_x_error'] for e in checks),'maximum_residual':max(e['relative_residual'] for e in checks),
            'metrics':metrics,'phases':phases,'traces':trace,
            'method_note':'Steady metrics exclude the initial factor and first refactor/solve warmup. Analysis is the combined reorder/symbolic/preprocess phase. GPU metrics are device interval unions; process CPU clocks and API-inclusive clocks overlap GPU work.'}
    (directory/'metrics.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps({'label':result['label'],'metrics':metrics}),flush=True)
    return output


if __name__=='__main__':
    import sys
    for path in sys.argv[1:]:analyze(path)
