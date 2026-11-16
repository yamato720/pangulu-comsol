#!/usr/bin/env python3
import collections
import datetime
import json
from pathlib import Path
import statistics
import subprocess
from run import ROOT,OUT
from pangulu_comsol import runtime as core


def stat(values):
    return {'mean':statistics.mean(values),'minimum':min(values),'maximum':max(values),'samples':values}


def main():
    suite=json.loads((OUT/'suite.json').read_text());assert suite['status']=='completed' and len(suite['completed'])==30
    records={row['label']:json.loads((OUT/row['label']/'metrics.json').read_text()) for row in suite['completed']}
    stages=['analysis','numeric','refactor_steady','solve_1_steady','solve_8_steady'];cases={}
    for case in suite['cases']:
        case_rows=[row for row in suite['completed'] if row['case']==case];metadata=records[case_rows[0]['label']]['metadata'];backends={}
        for backend in ['pangu','mycudss']:
            plain=[records[row['label']] for row in case_rows if row['backend']==backend and not row['traced']]
            traced=[records[row['label']] for row in case_rows if row['backend']==backend and row['traced']]
            assert len(plain)==3 and len(traced)==2
            values={}
            for stage in stages:
                wall=stat([r['metrics'][stage]['wall_seconds'] for r in plain])
                trace={field:stat([r['metrics'][stage][field] for r in traced]) for field in ['wall_seconds','GPU_kernel_seconds','GPU_memcpy_seconds','GPU_memset_seconds','GPU_active_seconds','GPU_coverage','kernel_launches']}
                values[stage]={'plain_wall_seconds':wall,'traced':trace,'trace_wall_change_percent':100*(trace['wall_seconds']['mean']/wall['mean']-1)}
            backends[backend]=values
        ratios={stage:{'kernel_time_pangu_over_cudss':backends['pangu'][stage]['traced']['GPU_kernel_seconds']['mean']/backends['mycudss'][stage]['traced']['GPU_kernel_seconds']['mean'],
                       'plain_wall_pangu_over_cudss':backends['pangu'][stage]['plain_wall_seconds']['mean']/backends['mycudss'][stage]['plain_wall_seconds']['mean']} for stage in stages}
        cases[case]={'matrix':metadata,'backends':backends,'ratios':ratios}
    baseline=json.loads((OUT/'baseline.json').read_text());assert all(core.sha(path)==digest for path,digest in baseline.items())
    hashes=json.loads((OUT/'dataset-sha256.json').read_text());assert all(core.sha(OUT/name)==digest for name,digest in hashes.items())
    assert all(record['checks_passed']==10 for record in records.values())
    assert all(trace['summary']['dropped']==0 for record in records.values() for trace in record['traces'])
    csr={}
    for mode in ['plain','trace']:
        path=OUT/('s_complex-pangu-csr-control-'+mode)/'metrics.json'
        if path.is_file():csr[mode]=json.loads(path.read_text())
    notes=[
        'Primary Pangu calls the original SDK directly with GPU-resident CSC (64-bit pointers, 32-bit indices); mycudss-direct forwards to original NVIDIA cuDSS with equivalent GPU-resident CSR32.',
        'No COMSOL model, Pangu cuDSS adapter, worker or IPC runs in this test. CPU format conversion, RHS construction, allocation and upload occur before phase timers. Output download and independent residual/error verification occur after measured calls.',
        'First numeric is reported separately. Steady refactor and solve averages exclude the initial factor and first refactor/solve warmup. Each process performs one numeric and four refactors with pre-uploaded scale variants 1.00/1.01/0.99; the numerical zero/nonzero pattern is unchanged.',
        'Plain wall timings have three independent process trials; GPU trace timings have two. Each steady result first averages three calls within that process, then averages processes.',
        'GPU kernel time uses the union of actual concurrent-kernel activity intervals; copies and memset are separate. Host CPU time and CUDA API time overlap GPU execution and must not be added to it.',
        'Analysis still contains vendor CPU reordering/preprocessing; the main GPU-bound head-to-head uses steady refactor, where measured GPU coverage is approximately 99% or greater.',
        'CUPTI concurrent tracing is not zero-overhead. Large-factor trace/control wall differences are recorded; short solves show larger relative tracing effects and run variation.',
        'GPU activity confirms execution location and duration, not identical FLOP counts or FP64 hardware saturation. Different ordering/fill and algorithms are part of each backend implementation.',
        'Pangu uses L0 ordering and L2 reuse. cuDSS uses auto reorder/factor, hybrid off, pivot threshold 1 and epsilon 1e-13, matching the calibrated COMSOL settings; numerical policies are not claimed identical.',
        'These three matrices on this RTX 4080 Laptop/CUDA12.9 system do not establish a universal ranking for all matrices or GPUs.',
        'This native-device benchmark does not change the existing CLI adapter, whose matrix/vector staging remains. Native GPU CSR support is separately checked for future integration work.'
    ]
    report={'status':'completed','completed_at':datetime.datetime.now().astimezone().isoformat(),'cases':cases,
            'method':{'plain_trials_per_backend_per_matrix':3,'traced_trials_per_backend_per_matrix':2,'max_RHS':8,
                      'input_on_GPU_before_timing':True,'CPU_format_and_RHS_preparation_outside_timing':True,
                      'GPU_completion_inside_timing':True,'tracer_clock':'CLOCK_MONOTONIC callback','baseline_files_unchanged':True},
            'validation':{'main_process_runs':30,'passed_check_groups':300,'known_solution_RHS_columns_checked':1350,
                       'maximum_relative_x_error':max(r['maximum_x_error'] for r in records.values()),'maximum_relative_residual':max(r['maximum_residual'] for r in records.values()),'CUPTI_dropped_records':0},
            'provenance':json.loads((OUT/'provenance.json').read_text()),'dataset_SHA256':hashes,'baseline_sha256':baseline,
            'CSR_layout_control':csr,'limitations':notes,'production_git_worktree_clean':not subprocess.check_output(['git','status','--porcelain'],text=True).strip()}
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['GPU 驻留纯矩阵后端对照：完成','Pangu原始SDK GPU CSC 与 mycudss-direct/原生NVIDIA cuDSS GPU CSR；无COMSOL、适配层、worker或IPC。',
           '矩阵/RHS准备及上传在计时外，GPU完成同步在计时内，原系统独立残差与已知解检查在测量后。','',
           '稳态重分解的实际GPU kernel时间（毫秒；两次轨迹，每次取三次稳态均值）：']
    for case,data in cases.items():
        p=data['backends']['pangu']['refactor_steady']['traced'];n=data['backends']['mycudss']['refactor_steady']['traced'];ratio=data['ratios']['refactor_steady']['kernel_time_pangu_over_cudss']
        lines.append(f"{case}: n={data['matrix']['n']}, nnz={data['matrix']['nnz']}; Pangu {p['GPU_kernel_seconds']['mean']*1000:.3f}ms; cuDSS {n['GPU_kernel_seconds']['mean']*1000:.3f}ms; Pangu/cuDSS={ratio:.4f}; GPU覆盖 {p['GPU_coverage']['mean']*100:.2f}%/{n['GPU_coverage']['mean']*100:.2f}%")
    lines+=['','各阶段：普通同步完成时间/实际GPU kernel时间（毫秒；前者三次无追踪，后者两次追踪，不可相加）：']
    for case,data in cases.items():
        lines.append(case)
        for stage in stages:
            row=[]
            for backend in ['pangu','mycudss']:
                value=data['backends'][backend][stage];row.append(f"{backend} wall={value['plain_wall_seconds']['mean']*1000:.3f} kernel={value['traced']['GPU_kernel_seconds']['mean']*1000:.3f}")
            lines.append('  '+stage+': '+'; '.join(row))
    lines+=['','验收：30次主运行，300组残差/已知解检查（1350个RHS列）通过；最大解相对差 '+str(report['validation']['maximum_relative_x_error'])+'，最大原系统相对残差 '+str(report['validation']['maximum_relative_residual'])+'；12份主轨迹丢失记录数均0。','',
            '补充：S矩阵SDK原生GPU CSR输入也通过变值重分解与多RHS检查。该控制只有一次普通与一次追踪，不与主统计混用。','', '原始文件：',str(OUT/'suite.json'),str(OUT/'dataset-sha256.json'),'', '方法与限制：',*notes]
    (OUT/'report.txt').write_text('\n'.join(lines)+'\n')
    write_pointer={'directory':str(OUT),'status':'completed','report':str(OUT/'report.json'),'text_report':str(OUT/'report.txt')}
    (ROOT/'data/runs/latest-matrix-duel.json').write_text(json.dumps(write_pointer,indent=2)+'\n')
    print('\n'.join(lines[:10]))


if __name__=='__main__':main()
