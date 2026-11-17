#!/usr/bin/env python3
"""Report the original single-load case, keeping preparation and GPU work distinct."""
from pathlib import Path
import collections
import csv
import datetime
import hashlib
import json
import re
import statistics
import subprocess
import numpy as np

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'data/runs/pinched-cylinder_20261116T184505'
def read(p):return json.loads(p.read_text())
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as s:
        for b in iter(lambda:s.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()

def main():
    trials=read(OUT/'suite.json');assert len(trials) in [8,9]
    failed=[x for x in trials if x['returncode']!=0]
    completed=[x for x in trials if x['returncode']==0]
    groups=collections.defaultdict(list);checks=[];physical=[];dropped=0
    reference=np.fromfile(OUT/'datasets/million/application_linear_solution.bin',dtype='<f8')
    for trial in completed:
        folder=OUT/trial['label'];result=read(folder/'result.json');assert result['status']=='completed'
        metrics=read(folder/'metrics.json');check=[e for e in result['events'] if e['tag']=='CHECK']
        assert len(check)==2 and all(e['passed'] for e in check);checks+=check
        phases=metrics['metrics'];core_names=['analysis','numeric','physical_solve']
        cold=sum(phases[k]['wall_seconds'] for k in core_names)
        x=np.fromfile(folder/'physical_solution.bin',dtype='<f8');assert x.size==reference.size and np.isfinite(x).all()
        error=float(np.linalg.norm(x-reference)/np.linalg.norm(reference));assert error<=1e-6,error
        physical.append({'trial':trial['label'],'relative_l2_vs_MUMPS':error})
        loss=sum(t['summary']['dropped'] for t in metrics['traces']);assert loss==0;dropped+=loss
        backend=trial['backend']+('-'+trial['reorder'] if trial['backend']=='pangu' else '')
        record={'trial':trial['label'],'cold_core_wall_seconds':cold,'metrics':phases,
                'physical_residual':next(c['relative_residual'] for c in check if c.get('kind')=='physical_rhs'),
                'physical_solution_relative_error_vs_MUMPS':error,'process_wall_seconds':result['process_wall_seconds']}
        peaks=[float(v['data'].split(',')[3]) for v in result.get('telemetry',[]) if v.get('data')]
        record['observed_whole_GPU_peak_MiB']=max(peaks,default=None)
        if trial['traced']:record['cold_core_GPU_kernel_seconds']=sum(phases[k]['GPU_kernel_seconds'] for k in core_names)
        groups[(backend,'traced' if trial['traced'] else 'plain')].append(record)
    stats={}
    for (backend,kind),runs in groups.items():
        cold=[r['cold_core_wall_seconds'] for r in runs]
        phases={name:{field:statistics.mean([r['metrics'][name][field] for r in runs]) for field in runs[0]['metrics'][name]} for name in runs[0]['metrics']}
        d={'processes':len(runs),'cold_core_mean_seconds':statistics.mean(cold),'cold_core_range_seconds':[min(cold),max(cold)],'phases':phases,'runs':runs}
        if kind=='traced':d['cold_core_GPU_kernel_seconds']=statistics.mean(r['cold_core_GPU_kernel_seconds'] for r in runs)
        stats.setdefault(backend,{})[kind]=d
    for backend in ['pangu-L0','mycudss']:
        assert stats[backend]['plain']['processes']==2 and stats[backend]['traced']['processes']==1
    assert stats['pangu-L1']['plain']['processes']==2
    host=read(OUT/'original-host-result.json');assert host['passed']
    host_text=(OUT/'original-host.log').read_text()
    host['native_phase_seconds']={name:float(value) for name,value in re.findall(r'MOOSE_PANGULU_NATIVE phase=(\S+) seconds=(\S+) status=0',host_text)}
    host['native_core_seconds']=sum(host['native_phase_seconds'].values())
    host['relative_l2_vs_MUMPS']=float(re.search(r'HOST_RESULT passed=true relative_l2_vs_MUMPS=(\S+)',host_text)[1])
    baseline=read(OUT/'baseline.json');unchanged={name:sha(Path(name))==value for name,value in baseline.items()};assert all(unchanged.values())
    source=read(OUT/'source-hashes.json');assert all(sha(OUT/'sources'/x['path'])==x['sha256'] for x in source['files'])
    matrix_match={name:sha(OUT/'million-reference-app'/name)==sha(OUT/'million-pangulu-app'/name) for name in ['A.petsc','b.petsc']};assert all(matrix_match.values())
    for case in ['small','million']:
        directory=OUT/'datasets'/case;manifest=read(directory/'sha256.json')
        # Only test configuration metadata was intentionally extended after capture.
        manifest[str((directory/'metadata.json').relative_to(OUT))]=sha(directory/'metadata.json')
        (directory/'sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
        assert all(sha(OUT/name)==value for name,value in manifest.items())
    git=subprocess.run(['git','status','--short'],cwd=ROOT,capture_output=True,text=True,check=True).stdout;assert not git.strip(),git
    ratios={}
    for p in ['pangu-L0','pangu-L1']:
        if p not in stats:continue
        ratios[p]={}
        if 'plain' in stats[p]:ratios[p]['cuDSS_over_Pangu_cold_core_wall']=stats['mycudss']['plain']['cold_core_mean_seconds']/stats[p]['plain']['cold_core_mean_seconds']
        if 'traced' in stats[p]:ratios[p]['cuDSS_over_Pangu_numeric_GPU']=stats['mycudss']['traced']['phases']['numeric']['GPU_kernel_seconds']/stats[p]['traced']['phases']['numeric']['GPU_kernel_seconds']
    limitations=[
       'Official MOOSE binary commit 08315af82e (2026-11-13), PETSc3.26.0 differs from public case lock 07b49de938ed357c136b487078065406d838aa26 / documented PETSc3.25.4. The original public input, seed mesh and factor45 refinement are retained; matrix shape is checked exactly.',
       'The local original Pangu26.46 SDK package hash differs from the case documentation. Vendor binaries and license were neither patched nor replaced.',
       'The public repository does not supply the chart raw benchmark matrix, original hardware or full comparison configuration; this is a local rerun of the public model, not strict reproduction of the chart.',
       'Native GPU CSC PanguLU L1/L2 and L0/L2 versus GPU CSR NVIDIA cuDSS0.7.1 via calibrated mycudss-direct, all FP64 general LU. All host conversion, upload, RHS preparation and result checks excluded from phase timers. Analysis includes vendor CPU work and is separately listed.',
       'Final default PanguLU L0 and native cuDSS each have two plain runs and one CUPTI trace, using the original single-RHS workload and first factor. Expensive L1 is separately bounded; it is not mixed with successful default measurements.',
       'GPU times are monotonic CUPTI activity-interval unions. Trace overhead may be substantial near full VRAM; plain wall results and traced GPU times are both shown. Whole-device sampled memory cannot establish paging or a per-solver exact memory peak.',
       'An earlier extra-eight-RHS GPU trace was intentionally stopped in numeric after about200s near full VRAM. Single-RHS L1 profiling also did not finish within300s and was bounded; no completed factor or GPU time is claimed for either. Two plain L1 replays and an independent original HOST adapter replay completed correctly, so the profiling waits are not normal solver time.',
       'Full MOOSE HOST L1 numeric80.7138s is a separate observation. The identical original HOST adapter, replayed standalone without MOOSE, numerically completed in about1.92s. This rules out the HOST API alone as a sufficient explanation; MOOSE environment/available VRAM/context or load remains unisolated. No proven paging cause is claimed.',
       'L1 enables MC64 matching/scaling per the SDK sample guide; L0 omits it. Runtime and memory sensitivity is observed; exact fill count or a paging cause is not exposed by this public API and is not claimed.']
    report={'status':'completed_with_limits' if failed else 'completed','completed_at':datetime.datetime.now().astimezone().isoformat(),'directory':str(OUT),
       'case':read(OUT/'datasets/million/metadata.json'),'statistics':stats,'ratios':ratios,'completed_matrix_processes':len(completed),'failed_attempts':failed,
       'check_groups':len(checks),'checked_RHS_columns':sum(c['nrhs'] for c in checks),
       'maximum_original_system_relative_residual':max(c['relative_residual'] for c in checks),
       'maximum_known_solution_relative_error':max(c['relative_x_error'] for c in checks if c.get('kind')!='physical_rhs'),
       'physical_vs_MUMPS':physical,'maximum_physical_solution_relative_error_vs_MUMPS':max(c['relative_l2_vs_MUMPS'] for c in physical),
       'GPU_trace_dropped_records':dropped,'small_fields_comparison':read(OUT/'small-fields-comparison.json'),
       'million_fields_comparison':read(OUT/'million-fields-comparison.json'),
       'application_matrix_and_rhs_byte_identical':matrix_match,'original_files_unchanged':unchanged,
       'source_files_and_datasets_hash_verified':True,'production_git_clean':True,
       'provenance':read(OUT/'benchmark-provenance.json'),'diagnostic_failure':read(OUT/'million-pangu-L1-trace-probe/stop-note.json'),
       'L1_full_application':read(OUT/'million-pangulu-app/validation.json'),
       'original_HOST_adapter_standalone_control':host,
       'limitations':limitations}
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['Pinched cylinder / 圆柱薄壳纯矩阵复测','='*52,
       '矩阵1017005阶 / 45630025存储项 / FP64实数通用LU / 原始点载荷单RHS。',
       'RTX4080Laptop12GiB，原始Pangu26.46及原生NVIDIAcuDSS0.7.1，8个CPU线程。',
       '先完成完整MOOSE模型的CPU MUMPS与PanguLU物理场核对，再独立重放字节相同的矩阵和RHS。',
       '转换/上传/校验在计时外；分析包含厂商CPU预处理，不称作纯GPU。',
       '默认PanguLU L0及cuDSS各2次无追踪取均值、1次GPU追踪；L1限制单独列出。首次分解之后首先求解原始物理载荷。','',
       '无追踪墙钟秒：后端 | 分析 | 首次数值分解 | 原载荷求解 | 核心合计 | 合计范围']
    for backend in ['pangu-L1','pangu-L0','mycudss']:
        label=backend.replace('pangu-','PanguLU ') if backend.startswith('pangu-') else 'cuDSS'
        if backend not in stats or 'plain' not in stats[backend]:
            lines.append(label+' | GPU CSC无追踪未完成；不报成功时间');continue
        d=stats[backend]['plain'];v=d['phases'];lines.append(f"{label} | {v['analysis']['wall_seconds']:.6f} | {v['numeric']['wall_seconds']:.6f} | {v['physical_solve']['wall_seconds']:.6f} | {d['cold_core_mean_seconds']:.6f} | {d['cold_core_range_seconds']}")
    lines+=['','单次追踪GPU kernel秒：后端 | 分析 | 首次数值分解 | 原载荷求解 | 分解GPU活动覆盖率']
    for backend in ['pangu-L1','pangu-L0','mycudss']:
        label=backend.replace('pangu-','PanguLU ') if backend.startswith('pangu-') else 'cuDSS'
        if backend not in stats or 'traced' not in stats[backend]:
            lines.append(label+' | GPU追踪未在300秒内完成；不报GPU成功时间');continue
        v=stats[backend]['traced']['phases'];lines.append(f"{label} | {v['analysis']['GPU_kernel_seconds']:.6f} | {v['numeric']['GPU_kernel_seconds']:.6f} | {v['physical_solve']['GPU_kernel_seconds']:.6f} | {v['numeric']['GPU_coverage']:.4%}")
    lines+=['','cuDSS/PanguLU时间比，>1表示PanguLU更快：',json.dumps(ratios,indent=2),
       f"{len(checks)}组原系统数值检查全部通过；最大相对残差 {report['maximum_original_system_relative_residual']:.12g}",
       f"最大已知解相对误差 {report['maximum_known_solution_relative_error']:.12g}",
       f"原载荷解与MUMPS最大相对L2差 {report['maximum_physical_solution_relative_error_vs_MUMPS']:.12g}",
       '小网格和百万阶完整五物理场/后处理量均与MUMPS比较通过。PanguLU/MUMPS应用导出A/b字节相同。',
       f"完成的{sum(x['traced'] for x in completed)}条主GPU追踪丢失0条；L1未完成尝试保留失败记录。",
       '完整MOOSE HOST CSC L1对照：分析3.43800748s，数值分解80.713801627s，原载荷求解0.034638447s，五物理场通过MUMPS核对；此时间不混入独立GPU CSC统计。',
       f"未修改原HOST适配器独立重放：阶段{host['native_phase_seconds']}，核心合计{host['native_core_seconds']:.6f}s，MUMPS解相对L2差{host['relative_l2_vs_MUMPS']:.12g}。HOST接口本身不足以解释完整应用80s观察。",
       '输入/SDK/许可/配置原始哈希保持，正式Git工作树干净。','','限制：']
    lines+=['- '+x for x in limitations];lines+=['','复跑说明：dev/benchmarks/pinched_cylinder/README.txt',str(OUT)]
    (OUT/'report.txt').write_text('\n'.join(lines)+'\n')
    with (OUT/'comparison.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['backend','analysis_wall_s','numeric_wall_s','physical_solve_wall_s','core_wall_s','numeric_GPU_kernel_s','physical_solve_GPU_kernel_s'])
        for backend,d in stats.items():
            if 'plain' not in d:continue
            v=d['plain']['phases'];g=d.get('traced',{}).get('phases',{});writer.writerow([backend,v['analysis']['wall_seconds'],v['numeric']['wall_seconds'],v['physical_solve']['wall_seconds'],d['plain']['cold_core_mean_seconds'],g.get('numeric',{}).get('GPU_kernel_seconds'),g.get('physical_solve',{}).get('GPU_kernel_seconds')])
    pointer={'status':report['status'],'directory':str(OUT),'report':str(OUT/'report.json'),'text_report':str(OUT/'report.txt'),'case':'pinched-cylinder','completed_at':report['completed_at']}
    for p in [ROOT/'coordination/pinched_cylinder_progress.json',ROOT/'data/runs/latest-pinched-cylinder.json']:
        p.write_text(json.dumps(pointer,indent=2)+'\n')
    print('\n'.join(lines),flush=True)

if __name__=='__main__':main()
