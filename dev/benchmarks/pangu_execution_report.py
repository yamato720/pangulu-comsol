#!/usr/bin/env python3
"""Final report for the Pangu execution switch and measured comparison."""
import datetime
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core
OUT=Path(json.loads((ROOT/'data/runs/latest-pangu-execution.json').read_text())['directory'])


def main():
    suite=json.loads((OUT/'suite.json').read_text());fixed=json.loads((OUT/'fixed/report.json').read_text());gui=json.loads((OUT/'gui-verification.json').read_text())
    assert suite['status']==fixed['status']==gui['status']=='completed'
    assert suite['field_comparison']['fields_passed']==708 and fixed['checks_passed']==96
    assert all(row['fields_passed']==236 for row in suite['completed'])
    baseline=json.loads((OUT/'runtime-baseline-0.4.json').read_text());assert all(core.sha(path)==digest for path,digest in baseline.items())
    for mode in ['worker','direct']:
        assert (OUT/('adapter-'+mode+'.stdout.log')).read_text().count('ADAPTER_CASE')==8
        assert 'ADAPTER_TEST_PASS' in (OUT/('adapter-'+mode+'.stdout.log')).read_text()
        text=(OUT/('integration-'+mode)/'stdout.log').read_text();assert text.count('MODEL_PASS')==4 and 'MODULE_INTEGRATION_PASS' in text
    worker=suite['summary']['worker'];direct=suite['summary']['direct'];fw=fixed['summary']['worker'];fd=fixed['summary']['direct']
    comparison={'study3_worker_mean_seconds':worker['mean_seconds'],'study3_direct_mean_seconds':direct['mean_seconds'],
       'study3_direct_extra_percent':100*(direct['mean_seconds']/worker['mean_seconds']-1),
       'fixed_worker_mean_seconds':fw['mean_seconds'],'fixed_direct_mean_seconds':fd['mean_seconds'],
       'fixed_direct_time_reduction_percent':100*(1-fd['mean_seconds']/fw['mean_seconds']),
       'worker_mean_sampled_GPU_peak_MiB':statistics.mean(worker['GPU_peak_MiB']),
       'direct_mean_sampled_GPU_peak_MiB':statistics.mean(direct['GPU_peak_MiB']),
       'sampled_peak_reduction_MiB':statistics.mean(worker['GPU_peak_MiB'])-statistics.mean(direct['GPU_peak_MiB'])}
    report={'status':'completed','application_version':'0.6.0','module_version':'0.4.0','completed_at':datetime.datetime.now().astimezone().isoformat(),
       'default_execution':'worker','comparison':comparison,'S_suite':suite,'fixed_replay':fixed,'GUI':gui,
       'SDK':core.sdk_info(),'licences_sha256':{str(p):core.sha(p) for p in core.licence_paths()},
       'runtime_baseline_sha256':baseline,'runtime_unchanged_during_measurements':True,
       'source_scope':'Current worker numeric code moved into shared backend.c; worker and direct use the same libpangu_backend.so. Direct changes execution location and private-buffer reuse; CSR-to-CSC and staged vectors remain.',
       'startup_compatibility':{'normal_SDK_preflight_passed':True,'SDK_with_vendor_libjsig_preload_rejected':True,
              'observed_error':'license runtime integrity check failed, status -3','logs':[str(OUT/'normal.stdout.log'),str(OUT/'jsig.stdout.log')],
              'direct_startup':'Generated vendor script copy omits only libjsig preload; FLROOT points to configured installation. Original SDK/licence/COMSOL script unchanged.'},
       'validation':{'unit_cases':36,'full_s_field_checks_passed':944,'cross_execution_saved_field_checks_passed':708,
              'fixed_known_solution_checks_passed':96,'batch_numeric_cases':['real','complex','nonlinear','transient'],
              'adapter_cases':['real-general','complex-general','SPD-lower','HPD-lower','two RHS','refactor','transpose rejection'],
              'GUI_modes':['worker','direct'],'GUI_test_rendering':'software; isolated copies of preferences and Java user.home, no production overrides'},
       'limitations':['Two full S runs per mode and four fixed replay blocks are local observations; not a broad performance guarantee.',
              'Full S inputs and CPU reference are identical, but live floating-point assembly and stored sparsity can vary between runs.',
              'Fixed replay repeats the same captured A0 six times with two manufactured RHS per iteration. CUDA completion is inside the timed calls; independent verification is afterward.',
              'Both timings are completion wall times including relevant host/bridge/backend work, not GPU kernel-only durations.',
              'Direct keeps CSR-to-CSC, strict structure policy, L0/L2, vector staging and finite-output checking. It is not an all-GPU zero-copy path.',
              'Direct omits libjsig preloading to allow genuine SDK initialization; arbitrary native plugins, concurrent model solving and fault/signal-chain equivalence are outside this acceptance scope.',
              'GPU memory is sampled whole-device use, including desktop and other processes. Small differences are not exact per-process allocations.',
              'Original input models, SDK and licence remain intact. No customer release archive generated.']}
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['Pangu worker/direct 执行方式：实现及验收完成','默认 worker；CLI 使用 --execution worker/direct，运行页和测试页可选择。','',
       f"完整 S Study3 59频点，两次均值：worker {worker['mean_seconds']:.6f}s；direct {direct['mean_seconds']:.6f}s。",
       f"原始值：worker {worker['seconds']}；direct {direct['seconds']}。",
       f"direct 均值耗时变化 {comparison['study3_direct_extra_percent']:+.3f}%；本轮未显示完整流程明显提速。",'',
       f"固定 A0 回放（6轮/12RHS，四次交错均值）：worker {fw['mean_seconds']:.6f}s；direct {fd['mean_seconds']:.6f}s。",
       f"direct 同步完成时间减少 {comparison['fixed_direct_time_reduction_percent']:.3f}%；不能直接外推完整Study。",'',
       f"整卡显存采样峰值均值：worker {comparison['worker_mean_sampled_GPU_peak_MiB']:.1f}MiB；direct {comparison['direct_mean_sampled_GPU_peak_MiB']:.1f}MiB。",
       f"direct 低约 {comparison['sampled_peak_reduction_MiB']:.1f}MiB，采样含桌面及其他进程。",'',
       '验收：4×236=944项S档CPU参考；3×236=708项跨执行方式保存结果比较；96项固定已知解检查；一般/实复数/SPD/HPD/双RHS/重分解适配测试；实数/复数/非线性/瞬态批处理及真实GUI两种方式均通过。',
       '数值后端在 src/backend.c，两种方式共用。直接方式省去socket/memfd并复用私有暂存区；矩阵转换与向量暂存仍保留。',
       '兼容限制：原SDK遇到libjsig预加载会报告完整性检查失败。direct使用当前厂商脚本私有副本省略此预加载，真实授权仍必须通过；原COMSOL脚本、SDK和许可未修改。',
       'GUI验收采用测试专用偏好副本及软件渲染；正常产品启动不传这些测试覆盖。真实用户偏好未修改。',
       '', '原始证据：',str(OUT/'suite.json'),str(OUT/'fixed/report.json'),str(OUT/'gui-verification.json'),'', '限制：',*report['limitations']]
    (OUT/'report.txt').write_text('\n'.join(lines)+'\n')
    (ROOT/'data/runs/latest-pangu-execution.json').write_text(json.dumps({'directory':str(OUT),'status':'completed','report':str(OUT/'report.json'),'text_report':str(OUT/'report.txt')},indent=2)+'\n')
    print('\n'.join(lines[:14]))


if __name__=='__main__':main()
