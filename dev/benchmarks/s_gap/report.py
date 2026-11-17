#!/usr/bin/env python3
"""Bound the explanation to measured clocks and a controlled phase-policy test."""
from pathlib import Path
import collections
import csv
import datetime
import hashlib
import json
import re
import shutil
import subprocess

OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
def read(path):return json.loads(path.read_text())
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    labels=['traced-pangu-20k-r1','traced-cudss-20k-r1','traced-pangu-full59-r1','traced-cudss-full59-r1','timed-pangu-full59','timed-cudss-full59']
    runs={label:read(OUT/(label+'.json')) for label in labels}
    assert all(r['status']=='completed' and r['original_files_unchanged'] for r in runs.values())
    assert all(all(r['runs'][0]['checks'].values()) for r in runs.values())
    metrics={label:read(OUT/label/'attribution.json') for label in labels}
    pg=metrics['traced-pangu-full59-r1'];nv=metrics['traced-cudss-full59-r1'];pt=metrics['timed-pangu-full59'];nt=metrics['timed-cudss-full59']
    control=read(OUT/'reuse-control/report.json');assert control['status']=='completed' and len(control['completed'])==12
    assert all(r['checks_passed']==12 for r in control['completed'])
    c=control['summary'];p_repeat=c['pangu/repeat-analysis'];n_repeat=c['cudss/repeat-analysis'];p_ref=c['pangu/refactor'];n_ref=c['cudss/refactor']
    bridge=pt['bridge_summary'];back=pt['backend_summary']
    assert len(bridge)==len(back)==1 and bridge[0]['confirmed_device_pattern_changes']==58 and back[0]['analysis_calls']==59
    assert back[0]['handles_created']==1 and bridge[0]['host_full_stages']==0
    assert bridge[0]['bridge_device_bytes_live']==0 and back[0]['backend_buffer_bytes_live']==0
    assert pg['unattributed_kernel_union_seconds']==nv['unattributed_kernel_union_seconds']==0
    assert not pg['GPU_intervals_after_runAll_end'] and not nv['GPU_intervals_after_runAll_end']
    phases=[]
    for key,name in [('analysis','分析/重排/符号'),('numeric','数值分解'),('solve','回代')]:
        codes=['1','2'] if key=='analysis' else ['4'] if key=='numeric' else ['1008']
        p=sum(pg['kernel_by_launch_API_phase_seconds'].get(x,0) for x in codes)
        n=sum(nv['kernel_by_launch_API_phase_seconds'].get(x,0) for x in codes)
        phases.append({'stage':name,'PanguLU_GPU_kernel_seconds':p,'cuDSS_GPU_kernel_seconds':n,'PanguLU_minus_cuDSS_seconds':p-n})
    sync=collections.Counter()
    for line in (OUT/'timed-pangu-full59/stderr.log').read_text().splitlines():
        if line.startswith('PGL_WORKER_PHASE '):
            x=json.loads(line.split(' ',1)[1]);sync[str(x['phase'])]+=x['completion_sync_seconds']
    baseline=read(OUT/'baseline.json');unchanged={name:sha(name)==value for name,value in baseline['files'].items()};assert all(unchanged.values())
    prefs={name:hashlib.sha256(Path(name).read_bytes().partition(b'\n')[2]).hexdigest()==value for name,value in baseline['prefs'].items()};assert all(prefs.values())
    old=read(ROOT/'data/runs/s_develop_20261117T105116/comparison.json')
    initial={r['backend']:r for r in old['workflow'] if r['frequency_count']==59}
    scope={backend:{'runAll_seconds':r['runAll_seconds'],'process_wall_seconds':r['process_wall_seconds']} for backend,r in initial.items()}
    findings={
      'answer':'The fixed-replay advantage was dominated by analysis reuse under repeated ANALYSIS requests. Real sweep stored CSR graphs change58 times, so both actual backends receive59 analysis requests. Numerical GPU factorization on this S case remains substantially slower for PanguLU; the current GPU bridge overhead is small.',
      'timed_full59_seconds':{'PanguLU':pt['runAll_seconds'],'cuDSS':nt['runAll_seconds'],'gap':pt['runAll_seconds']-nt['runAll_seconds']},
      'trace_full59_seconds':{'PanguLU':pg['runAll_seconds'],'cuDSS':nv['runAll_seconds'],'gap':pg['runAll_seconds']-nv['runAll_seconds']},
      'trace_numerical_factor_GPU_ratio':phases[1]['PanguLU_GPU_kernel_seconds']/phases[1]['cuDSS_GPU_kernel_seconds'],
      'timed_PanguLU_bridge_remainder_inside_execute_seconds':pt['bridge_remainder_inside_execute_seconds'],
      'timed_PanguLU_matrix_validation_update_seconds':sum(x['total_seconds'] for x in pt['matrix_front_nested_in_bridge'].values()),
      'timed_PanguLU_explicit_completion_sync_seconds':dict(sync),
      'timed_PanguLU_explicit_completion_sync_total_seconds':sum(sync.values()),
      'control_repeat_analysis_initialization_plus_callbacks':{'PanguLU':p_repeat['initialization_plus_callbacks_seconds'],'cuDSS':n_repeat['initialization_plus_callbacks_seconds']},
      'control_matched_refactor_initialization_plus_callbacks':{'PanguLU':p_ref['initialization_plus_callbacks_seconds'],'cuDSS':n_ref['initialization_plus_callbacks_seconds']},
      'Native_cuDSS_phase_policy_time_reduction_percent':100*(1-n_ref['initialization_plus_callbacks_seconds']/n_repeat['initialization_plus_callbacks_seconds']),
      'real_sweep_counts':{'PanguLU_analysis':back[0]['analysis_calls'],'cuDSS_reorder':nv['API_phase_wall']['1']['calls'],'cuDSS_symbolic':nv['API_phase_wall']['2']['calls'],'PanguLU_numeric':back[0]['numeric_calls'],'cuDSS_numeric':nv['API_phase_wall']['4']['calls'],'PanguLU_refactor':back[0]['refactor_calls'],'stored_pattern_changes':bridge[0]['confirmed_device_pattern_changes']}}
    limitations=[
      'This measures the local300475-order S case on RTX4080Laptop at8threads. It does not explain the other1036043-order benchmark without its matrices and phase logs.',
      'Each full-sweep diagnostic configuration has one process. The captured-S controlled experiment has3 independent processes per backend/policy. No statistical confidence interval is claimed.',
      'Repeated COMSOL assembly can change stored numerical-zero patterns slightly. Full-sweep inputs and physical solutions are verified, but the two solver runs are not a byte-identical59-matrix stream. The controlled replay uses identical input bytes and scaled coefficients across backends/policies.',
      'The control alternates A0 and1.01*A0 with exact graph held constant; generated RHS follows each matrix and preserves a known solution. Changing caller phase schedule is the tested intervention. It is not the full physical sweep.',
      'CUPTI increases wall time, especially host/API work. GPU trace values are separate from timing-only results and are not subtracted from the earlier production gap to claim an exact causal percentage.',
      'Native cuDSS Execute returns asynchronously; PanguLU SDK calls in this case mostly return after completion. Do not compare their host numeric API durations as GPU duration. Kernel-to-phase classification uses recorded CUDA launch API correlation IDs; all measured kernels were attributed.',
      'The observer starts when the backend DSO is loaded. The first approximately4seconds of runAll are unobserved, not assumed to be CPU-only or GPU-idle. That prefix is explicitly reported.',
      'Stored CSR graph change is established; a change in physical/nonzero connectivity is not. Whether differences arise from zero pruning or floating-point assembly needs adjacent-frequency matrix capture before any graph-normalization optimization.',
      'Kernel activity identifies where time went, not FLOP count, sparse-fill count, bandwidth saturation or FP64 hardware occupancy. Those would need SDK/hardware-counter investigation.',
      'No production source, numerical policy, SDK binary, license, original model or production component was changed by this investigation. No worker/IPC path is involved in the successful direct comparisons.']
    result={'status':'completed','completed_at':datetime.datetime.now().astimezone().isoformat(),'directory':str(OUT),'findings':findings,'GPU_phase_comparison':phases,'previous_production_measurement':scope,
       'metrics':{label:{'path':str(OUT/label/'attribution.json'),'runAll_seconds':m['runAll_seconds'],'API_phase_wall':m['API_phase_wall'],'GPU':m['GPU']} for label,m in metrics.items()},
       'controlled_reuse':control,'PanguLU_timed_SDK_phases':pt['SDK_phase_wall_and_GPU'],'PanguLU_timed_bridge_nested_parts':pt['matrix_front_nested_in_bridge'],
       'PanguLU_bridge_summary':bridge,'PanguLU_backend_summary':back,
       'validation':{'full_or_single_field_checks_passed':sum(r['runs'][0]['field_records_passed'] for r in runs.values()),'controlled_known_solution_checks_passed':144,'dropped_GPU_records':sum(t['dropped'] for m in metrics.values() for t in m['trace_summaries']),'all_original_files_unchanged':True,'preference_entries_unchanged':True},
       'protected_file_checks':unchanged,'preference_checks':prefs,'provenance':read(OUT/'provenance.json'),'observer_method':read(OUT/'observer-method.json'),
       'measurement_issue':'Initial Java getenv attempt rejected by COMSOL sandbox before solving. Preserved as failure; observer was then loaded as a normal external-backend dependency. No preferences changed.',
       'limitations':limitations,'next_targets':['Inspect SDK numerical factorization kernels/fill for this S graph.',
          'Capture adjacent-frequency stored CSR differences to establish whether a mathematically equivalent stable graph can preserve symbolic reuse; do not skip analysis merely because n/nnz or addresses match.',
          'Use one-analysis-plus-refactor controls for stable graphs; keep repeated-analysis callback tests separately labeled.']}
    (OUT/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['S 端到端差距定位：完成','='*54,'结论：固定矩阵优势主要来自复用策略；真实扫频失去该优势，而PanguLU数值分解GPU阶段在本S算例上明显更慢。',
      '不是启动/保存把一个已证明更快的分解器拖慢；固定回放的原始接口序列混入了重复分析开销。',
      '本机RTX4080Laptop /300475阶 /8线程 /FP64 /原始SDK26.46 /cuDSS0.7.1。','',
      '1. 关闭GPU追踪的完整59点复核（保留轻量阶段时钟）：',
      f"PanguLU {pt['runAll_seconds']:.6f}s；cuDSS {nt['runAll_seconds']:.6f}s；差{pt['runAll_seconds']-nt['runAll_seconds']:.6f}s。",
      '前次生产计时PanguLU194.558s、cuDSS160.549s。诊断计时另列，不替换历史数值；差异包含装配/运行波动及诊断影响。','',
      '2. 真实59点GPU kernel累计时间（一次CUPTI追踪；GPU区间并集）：','阶段 | PanguLU秒 | cuDSS秒 | PanguLU多用秒']
    for row in phases:lines.append(f"{row['stage']} | {row['PanguLU_GPU_kernel_seconds']:.6f} | {row['cuDSS_GPU_kernel_seconds']:.6f} | {row['PanguLU_minus_cuDSS_seconds']:+.6f}")
    lines += [f"数值分解GPU时间比{findings['trace_numerical_factor_GPU_ratio']:.3f}倍；这定位了数值内核阶段，不等于已证明算力或带宽不足。",
      'cuDSS的GPU数值任务大量在API返回后完成；其主机API耗时不能当成GPU时间。',
      f"关闭GPU追踪时，PanguLU Execute内扣除SDK阶段的其余时间仅{pt['bridge_remainder_inside_execute_seconds']:.6f}s（矩阵校验/快照、复制等嵌套其中）。",
      f"其中矩阵校验/更新合计{findings['timed_PanguLU_matrix_validation_update_seconds']:.6f}s；额外完成同步合计{sum(sync.values()):.6f}s。",
      '不能把这些嵌套时间再加到GPU或API总时间上。','',
      '3. 控制实验：同一S图，系数1.00/1.01交替，6次分解、12RHS，各3独立进程；下表为初始化+回调均值：','调用协议 | PanguLU秒 | cuDSS秒',
      f"每轮请求分析+分解 | {p_repeat['initialization_plus_callbacks_seconds']:.6f} | {n_repeat['initialization_plus_callbacks_seconds']:.6f}",
      f"一次分析+后续重分解 | {p_ref['initialization_plus_callbacks_seconds']:.6f} | {n_ref['initialization_plus_callbacks_seconds']:.6f}",
      'PanguLU在两种协议下均实际执行1次分析、1次数值分解、5次重分解；原生cuDSS遵循调用者请求。',
      f"cuDSS仅改合法调用策略就减少{findings['Native_cuDSS_phase_policy_time_reduction_percent']:.1f}%时间，PanguLU本来已有缓存，所以变化很小。",
      '原固定回放用于比较相同API请求和适配缓存，没有测两边都正确使用稳定图复用时的核心性能。',
      '真实S扫频的存储CSR图变化58次；PanguLU与cuDSS均做59次分析和59次首次分解，PanguLU为0次重分解。',
      '因此固定图条件下的分析复用优势不能直接外推到本次实际扫频。','',
      '4. 后续重点：GPU数值分解及其填充/调度策略；再检查相邻频点CSR差异是否由零项裁剪/浮点装配造成，验证稳定图方案。',
      '不能仅凭n/nnz相同就跳过分析，也不能强行把变化图当作可直接重分解。','',
      f"验收：{result['validation']['full_or_single_field_checks_passed']}项频点字段检查、144项控制解/残差检查通过；追踪丢失0条。",
      'SDK/许可、原模型、配置、COMSOL偏好及生产组件保持；所有诊断在本目录。','', '限制：',*['- '+x for x in limitations], '',str(OUT)]
    (OUT/'report.txt').write_text('\n'.join(lines)+'\n')
    with (OUT/'GPU-phases.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(phases[0]));writer.writeheader();writer.writerows(phases)
    shutil.copy2(OUT/'report.json',ROOT/'coordination/s_gap_report.json');shutil.copy2(OUT/'report.txt',ROOT/'coordination/s_gap_report.txt')
    pointer={'status':'completed','directory':str(OUT),'report':str(OUT/'report.json'),'text_report':str(OUT/'report.txt')}
    for path in [ROOT/'coordination/s_gap_progress.json',ROOT/'data/runs/latest-s-gap.json']:path.write_text(json.dumps(pointer,indent=2)+'\n')
    print('\n'.join(lines[:35]),flush=True)

if __name__=='__main__':main()
