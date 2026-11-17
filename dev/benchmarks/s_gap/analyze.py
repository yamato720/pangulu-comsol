#!/usr/bin/env python3
"""Actual sweep clocks, nested API/SDK windows, and GPU interval unions."""
from pathlib import Path
import bisect
import collections
import json
import re
import sys

OUT=Path(__file__).resolve().parent

def union(intervals,begin,end):
    spans=sorted((max(a,begin),min(b,end)) for a,b in intervals if b>a and b>begin and a<end)
    total=0;right=begin
    for a,b in spans:
        if b>right:total+=b-max(a,right);right=b
    return total*1e-9

def analyze(label):
    folder=OUT/label;report=json.loads((OUT/(label+'.json')).read_text());assert report['status']=='completed'
    run=report['runs'][0];assert all(run['checks'].values())
    clock=json.loads(re.search(r'^BRIDGE_SWEEP_CLOCK_JSON (.+)$',(folder/'stdout.log').read_text(),re.M)[1]);begin,end=clock['begin_ns'],clock['end_ns']
    logs=collections.defaultdict(list)
    for line in (folder/'stderr.log').read_text(errors='replace').splitlines():
        tag,_,body=line.partition(' ')
        if tag in ['PGL_EXEC_SPAN','PGL_WORKER_PHASE','PGL_WORKER_INITIALIZE','PGL_BRIDGE','PGL_BRIDGE_SUMMARY','PGL_BACKEND_SUMMARY','NV_API_SPAN']:
            logs[tag].append(json.loads(body))
    callbacks=logs['PGL_EXEC_SPAN'] if report['backend']=='pangu' else [x for x in logs['NV_API_SPAN'] if x['kind']=='execute']
    callbacks.sort(key=lambda x:x['begin_ns']);assert all(a['end_ns']<=b['begin_ns'] for a,b in zip(callbacks,callbacks[1:]))
    assert all(begin<=x['begin_ns']<=x['end_ns']<=end for x in callbacks)
    callback_seconds=sum((x['end_ns']-x['begin_ns'])*1e-9 for x in callbacks)
    phase_api=collections.defaultdict(lambda:{'calls':0,'wall_seconds':0})
    for x in callbacks:
        row=phase_api[str(x['phase'])];row['calls']+=1;row['wall_seconds']+=(x['end_ns']-x['begin_ns'])*1e-9
    gpu=collections.defaultdict(list);kernels=[];apis=[];trace=[];clocks=[]
    for path in folder.glob('activities-*.jsonl'):
        summary=None
        for line in path.open():
            x=json.loads(line);kind=x['type']
            if kind=='clock':clocks.append(x);assert abs((x['host_before']+x['host_after'])/2-x['cupti_ns'])<1_000_000
            elif kind in ['kernel','memcpy','memset']:
                gpu[kind].append((x['start'],x['end']))
                if kind=='kernel':kernels.append(x)
            elif kind in ['runtime','driver']:apis.append(x)
            elif kind=='summary':summary=x
        assert summary and summary['dropped']==0;trace.append({'path':str(path),**summary})
    traced=report['traced']
    if traced:assert clocks and trace
    else:assert not clocks and not trace
    observed_begin=max(begin,min(x['host_before'] for x in clocks)) if clocks else begin
    observed_prefix=max(0,observed_begin-begin)*1e-9
    active=[v for spans in gpu.values() for v in spans];busy=union(active,observed_begin,end)
    result={'label':label,'backend':report['backend'],'runAll_seconds':(end-begin)*1e-9,'process_wall_seconds':run['process_wall_seconds'],
            'fields_passed':run['field_records_passed'],'callbacks':len(callbacks),'execute_API_wall_seconds':callback_seconds,'API_phase_wall':dict(phase_api),
            'wall_outside_execute_API_seconds':(end-begin)*1e-9-callback_seconds,
            'unobserved_prefix_seconds':observed_prefix,'GPU':{kind+'_union_seconds':union(spans,observed_begin,end) for kind,spans in gpu.items()},
            'observed_no_GPU_wall_seconds':(end-observed_begin)*1e-9-busy,'trace_summaries':trace,
            'bridge_summary':logs['PGL_BRIDGE_SUMMARY'],'backend_summary':logs['PGL_BACKEND_SUMMARY'],
            'traced':traced,'method':'Observer starts at normal backend DLL load. Prefix before its clock is unobserved, not classified as CPU or GPU idle. GPU intervals overlap host API windows. Native wrapper adds no synchronization.'}
    result['GPU']['active_union_seconds']=busy
    assert abs(result['unobserved_prefix_seconds']+result['observed_no_GPU_wall_seconds']+busy-result['runAll_seconds'])<1e-6
    late=[x for spans in gpu.values() for x in spans if x[1]>end];result['GPU_intervals_after_runAll_end']=len(late)
    # Correlate a kernel to the phase containing its recorded CUDA launch API.
    starts=[x['begin_ns'] for x in callbacks];cid_phases=collections.defaultdict(set)
    for api in apis:
        i=bisect.bisect_right(starts,api['start'])-1
        if i>=0 and api['start']<callbacks[i]['end_ns']:
            cid_phases[api['cid']].add(str(callbacks[i]['phase']))
    attributed=collections.defaultdict(list);unmatched=[]
    for kernel in kernels:
        candidates=cid_phases.get(kernel['cid'],set())
        if len(candidates)==1:attributed[next(iter(candidates))].append((kernel['start'],kernel['end']))
        else:unmatched.append((kernel['start'],kernel['end']))
    result['kernel_by_launch_API_phase_seconds']={p:union(v,observed_begin,end) for p,v in attributed.items()}
    result['unattributed_kernel_union_seconds']=union(unmatched,observed_begin,end)
    top=collections.Counter()
    for k in kernels:
        top[k['name']]+=max(0,min(k['end'],end)-max(k['start'],observed_begin))*1e-9
    result['top_kernel_cumulative_seconds_may_overlap']=top.most_common(12)
    if report['backend']=='pangu':
        sdk={};sdk_total=0
        for phase in [1027,4,8,1008]:
            windows=[(x['begin_ns'],x['end_ns']) for x in logs['PGL_WORKER_PHASE'] if x['phase']==phase]
            wall=sum((b-a)*1e-9 for a,b in windows);sdk_total+=wall
            sdk[str(phase)]={'calls':len(windows),'wall_seconds':wall,'GPU_active_seconds':sum(union(active,a,b) for a,b in windows),'GPU_kernel_seconds':sum(union(gpu['kernel'],a,b) for a,b in windows)}
        result['SDK_phase_wall_and_GPU']=sdk;result['SDK_total_wall_seconds']=sdk_total
        result['bridge_remainder_inside_execute_seconds']=callback_seconds-sdk_total
        assert result['bridge_remainder_inside_execute_seconds']>=-1e-6
        result['initialization_seconds']=sum(x['create_seconds'] for x in logs['PGL_WORKER_INITIALIZE'])
        front=collections.defaultdict(lambda:{'calls':0,'total_seconds':0,'status_readback_bytes':0})
        for x in logs['PGL_BRIDGE']:
            if x['event']!='matrix_stage_timing':continue
            values=dict(item.split('=',1) for item in x['detail'].split() if '=' in item)
            row=front[values['kind']];row['calls']+=1;row['total_seconds']+=float(values['total_seconds']);row['status_readback_bytes']+=int(values.get('status_readback_bytes',0))
        result['matrix_front_nested_in_bridge']=dict(front)
    else:
        result['initialization_seconds']=sum((x['end_ns']-x['begin_ns'])*1e-9 for x in logs['NV_API_SPAN'] if x['kind'] in ['create','data_create'])
    if not traced:
        result['GPU']=None;result['unobserved_prefix_seconds']=result['runAll_seconds'];result['observed_no_GPU_wall_seconds']=None
        result['GPU_intervals_after_runAll_end']=None;result['kernel_by_launch_API_phase_seconds']=None;result['unattributed_kernel_union_seconds']=None
        for row in result.get('SDK_phase_wall_and_GPU',{}).values():row['GPU_active_seconds']=None;row['GPU_kernel_seconds']=None
    (folder/'attribution.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result.get(k) for k in ['label','runAll_seconds','API_phase_wall','SDK_phase_wall_and_GPU','GPU','kernel_by_launch_API_phase_seconds','unattributed_kernel_union_seconds','bridge_remainder_inside_execute_seconds','unobserved_prefix_seconds','observed_no_GPU_wall_seconds']},ensure_ascii=False),flush=True)
    return result

if __name__=='__main__':
    for label in sys.argv[1:]:analyze(label)
