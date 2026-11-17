#!/usr/bin/env python3
"""Write the final local Study3 CPU/GPU investigation report."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(json.loads((ROOT/'data/runs/latest-bottleneck.json').read_text())['directory'])
labels=['pangu-traced-all-mono','cudss-traced-all-mono','pangu-timed-all','cudss-timed-all']
metrics={label:json.loads((OUT/label/'bottleneck.json').read_text()) for label in labels}
runs={label:json.loads((OUT/(label+'.json')).read_text()) for label in labels}
for label,r in runs.items():
    assert r['status']=='completed' and r['inputs_unchanged'] and r['production_files_unchanged']
    assert r['runs'][0]['field_records_passed']==236 and all(r['runs'][0]['checks'].values())
    for trace in r.get('CUPTI',[]):assert trace['dropped']==0

p,n=metrics[labels[0]],metrics[labels[1]]
pc,nc=metrics[labels[2]],metrics[labels[3]]
assert not p.get('GPU_work_after_runAll_end') and not n.get('GPU_work_after_runAll_end')
front={label:json.loads((OUT/label/'front-stage-summary.json').read_text()) for label in ['pangu-traced-all-mono','pangu-timed-all']}
matrix_prep={label:sum(row['total_seconds'] for row in front[label].values())+sum(metrics[label]['measured_format_parts'].values()) for label in front}
findings={
    'answer':'Mixed host/adapter and GPU cost; the observed PanguLU/cuDSS gap is not purely GPU execution.',
    'Pangu_matrix_preparation_and_transport_seconds':matrix_prep,
    'narrow_CSR_to_CSC_fraction':p['measured_format_parts']['conversion_seconds']/p['runAll_seconds'],
    'numeric_factorization_GPU_active_fraction':p['SDK_phase_GPU']['4']['GPU_active_union_seconds']/p['SDK_phase_GPU']['4']['scope_seconds'],
    'analysis_GPU_active_fraction':p['SDK_phase_GPU']['1027']['GPU_active_union_seconds']/p['SDK_phase_GPU']['1027']['scope_seconds'],
    'reuse_observation':'59 analysis phases and 59 fresh numeric factorizations, zero refactorization phases. Strict-pattern logs show changed stored sparsity across frequencies.',
    'optimization_targets':['Host-side matrix preparation/transport and buffer lifecycle in bridge.cpp and worker.c',
                            'Validated cross-frequency analysis reuse under the SDK numerical/structure contract',
                            'PanguLU GPU numerical factorization path; kernel activity alone does not establish arithmetic vs bandwidth vs algorithm-work cause'],
}
comparison={
    'trace_elapsed_gap_seconds':p['runAll_seconds']-n['runAll_seconds'],
    'trace_GPU_active_union_gap_seconds':p['GPU']['active_union_seconds']-n['GPU']['active_union_seconds'],
    'trace_kernel_union_gap_seconds':p['GPU']['kernel_union_seconds']-n['GPU']['kernel_union_seconds'],
    'trace_no_GPU_activity_wall_gap_seconds':p['GPU']['host_time_without_traced_GPU_work_seconds']-n['GPU']['host_time_without_traced_GPU_work_seconds'],
    'control_elapsed_gap_seconds':pc['runAll_seconds']-nc['runAll_seconds'],
    'control_Pangu_extra_percent':100*(pc['runAll_seconds']/nc['runAll_seconds']-1),
    'Pangu_trace_vs_control_extra_seconds':p['runAll_seconds']-pc['runAll_seconds'],
    'NVIDIA_trace_vs_control_extra_seconds':n['runAll_seconds']-nc['runAll_seconds'],
}
phase_rows=[]
for phase,name in [('1027','分析'),('4','数值分解'),('8','重分解'),('1008','回代')]:
    wall=p['worker_phases'].get(phase,{});gpu=p.get('SDK_phase_GPU',{}).get(phase,{})
    phase_rows.append({'phase':name,'calls':wall.get('calls',0),'wall_seconds':wall.get('seconds',0),
                       'GPU_active_union_seconds':gpu.get('GPU_active_union_seconds',0),
                       'kernel_union_seconds':gpu.get('kernel_union_seconds',0)})
baseline=json.loads((OUT/'baseline.json').read_text())
hashes={path:hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in baseline}
assert hashes==baseline
limitations=[
    'Each backend/configuration has one full accepted run; trace/control differences include run-to-run variation and are not a statistical confidence interval.',
    'All 59 corresponding frequencies had slightly different stored nonzero counts between repeated PanguLU runs. The same physical model is verified, but this is not a byte-identical captured A/b replay; the timing difference does not isolate tracing overhead.',
    'GPU active time is the union of this process GPU kernel/copy/memset intervals. It overlaps CPU execution and cannot be added to CPU API inclusive time.',
    'Wall time without traced GPU work includes COMSOL work, host analysis, driver work, scheduling and waits; it is not a measurement of pure CPU arithmetic.',
    'The interval partition describes where the measured gap occurred; it is not proof that removing a category saves exactly that much end-to-end time.',
    'Kernel activity tracing cannot identify FP64 throughput saturation, cache/memory-bandwidth limits, or optimal sparse factorization work without additional hardware-counter analysis.',
    'The first default-clock full trace passed numerical checks but was excluded from GPU attribution after realtime-to-monotonic clock jumps were detected. Repeated traces register CLOCK_MONOTONIC before enabling CUPTI.',
    'Historical 225.11/189.92-second results are retained separately and not replaced with diagnostic measurements.',
    'Current PanguLU format counters include measured CSR-to-CSC allocation/conversion, pattern mapping/comparison and value scatter. Small untimed checks remain inside the remaining-bridge category.',
]
report={'status':'completed','completed_at':datetime.datetime.now().astimezone().isoformat(),
        'scope':'S Study3 only; 300475-order systems, 59 frequencies, 8 CPU threads, FP64, hybrid memory never; stored CFD/Mapping unchanged',
        'source_inputs':runs[labels[0]]['inputs'],'SDK':runs[labels[0]]['sdk'],
        'profiling_provenance':json.loads((OUT/'profiling-provenance.json').read_text()),
        'repeated_assembly_pattern_check':json.loads((OUT/'pattern-repeat-check.json').read_text()),
        'CUPTI_provenance':json.loads((OUT/'tools/cupti-provenance.json').read_text()),
        'comparison':comparison,'Pangu_phase_GPU':phase_rows,
        'findings':findings,'front_stage_summaries_nested_in_bridge_time':front,
        'runs':{label:{'runAll_seconds':m['runAll_seconds'],'metrics':str(OUT/label/'bottleneck.json'),
                       'acceptance':str(OUT/(label+'.json')),'GPU':m.get('GPU'),'fields_passed':236,
                       'peak_sampled_whole_GPU_MiB':runs[label]['runs'][0]['gpu_peak_device_used_MiB']} for label,m in metrics.items()},
        'Pangu_host_stages':p['disjoint_host_stages'],'Pangu_control_host_stages':pc['disjoint_host_stages'],
        'measured_format_parts':p['measured_format_parts'],'original_production_files_sha256':hashes,
        'production_files_unchanged':True,'all_numerical_checks_passed':True,'full_run_field_checks_passed':944,
        'limitations':limitations,'issues':json.loads((OUT/'investigation-notes.json').read_text())['measurement_issues'],
        'official_method_sources':['https://docs.nvidia.com/cupti/12.9/main/main.html','https://docs.nvidia.com/cupti/12.9/api/group__CUPTI__ACTIVITY__API.html']}
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
lines=['S Study3 CPU/GPU 瓶颈排查：完成','',
       '定位结论：主机/适配路径与GPU执行都有显著成本，不能归为纯GPU慢。',
       f"追踪版总差 {comparison['trace_elapsed_gap_seconds']:.3f}s，其中GPU活动区间差 {comparison['trace_GPU_active_union_gap_seconds']:.3f}s，无本进程GPU活动的区间差 {comparison['trace_no_GPU_activity_wall_gap_seconds']:.3f}s。",'',report['scope'],'',
       '关闭 CUPTI 的对照（PanguLU 保留分段计时，单位秒）：',
       f"PanguLU 26.46: {pc['runAll_seconds']:.6f}",f"NVIDIA cuDSS: {nc['runAll_seconds']:.6f}",
       f"差距: {comparison['control_elapsed_gap_seconds']:.6f} 秒 / {comparison['control_Pangu_extra_percent']:.3f}%",'',
       '单调时钟 CUPTI 活动追踪（GPU 时间按区间并集）：']
for name,m in [('PanguLU',p),('NVIDIA cuDSS',n)]:
    lines.append(f"{name}: 主计时 {m['runAll_seconds']:.6f}s；GPU活动 {m['GPU']['active_union_seconds']:.6f}s；其中kernel {m['GPU']['kernel_union_seconds']:.6f}s；无本进程GPU活动的墙钟 {m['GPU']['host_time_without_traced_GPU_work_seconds']:.6f}s")
lines+=['', 'PanguLU 阶段：']
for row in phase_rows:lines.append(f"{row['phase']}: {row['calls']} 次；墙钟 {row['wall_seconds']:.6f}s；GPU活动 {row['GPU_active_union_seconds']:.6f}s")
lines+=['','PanguLU 主机分段（互不重叠，含CPU/GPU等待的墙钟，不能再次加GPU时间）：']
for row in p['disjoint_host_stages']:lines.append(f"{row['stage']}: {row['seconds']:.6f}s")
lines+=['','格式分项：']
for key,value in p['measured_format_parts'].items():lines.append(f'{key}: {value:.6f}s')
lines += [f"前端准备（嵌套在上述bridge其余时间内，不可重复相加）：{sum(row['total_seconds'] for row in front[labels[0]].values()):.6f}s。",
          f"前端矩阵准备与worker格式处理合计：追踪版 {matrix_prep[labels[0]]:.6f}s；关闭追踪版 {matrix_prep[labels[2]]:.6f}s。",
          '当前严格结构策略下59次分析、59次数值分解、0次重分解；跨频点结构复用没有命中。',
          'CSR→CSC本体约占整段2%；单独并行化这一部分不能消除主要差距。',
          '后续定位优先关注适配层缓冲区/搬运、合法的分析复用，以及GPU数值分解。尚未用硬件计数器区分GPU算力、访存或算法工作量。']
lines+=['',f"CUPTI追踪与关闭追踪的单次差：PanguLU {comparison['Pangu_trace_vs_control_extra_seconds']:.6f}s；cuDSS {comparison['NVIDIA_trace_vs_control_extra_seconds']:.6f}s。该差值包含运行波动。",'',
        '验收：四次完整59频点均236/236通过，共944项；有限性、CFD/Mapping、物理DOF、实际SDK/worker/库映射及原件哈希检查通过。',
        '生产代码和组件、SDK、许可及本机配置未修改。所有诊断组件与数据位于本目录；开发驱动在忽略的dev/benchmarks/。',
        '原始日志、逐频点结果、资源采样、CUPTI活动轨迹和库映射见每个运行子目录。','', '限制：',*limitations]
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
pointer=ROOT/'data/runs/latest-bottleneck.json';pointer.write_text(json.dumps({'directory':str(OUT),'status':'completed','report':str(OUT/'report.json'),'text_report':str(OUT/'report.txt')},indent=2)+'\n')
print('\n'.join(lines[:24]))
