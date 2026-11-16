#!/usr/bin/env python3
"""Finalize CLI integration and native calibration evidence."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core, testing
OUT=Path(json.loads((ROOT/'data/runs/latest-mycudss.json').read_text())['directory'])


def main():
    suite=json.loads((OUT/'suite.json').read_text());fixed=json.loads((OUT/'fixed/suite.json').read_text())
    assert suite['status']=='completed' and fixed['status']=='completed'
    assert len(suite['completed'])==6 and suite['native_comparison']['field_records_passed']==1180 and fixed['checks_passed']==108
    smoke=OUT/'smoke';smoke.mkdir(exist_ok=True);cuda=Path(core.cfg()['cuda_root']);native=testing.native_library()
    original_source=Path(__file__).with_name('device_smoke.cpp');source=smoke/'complex.cpp';real=smoke/'real.cpp'
    checked=original_source.read_text().replace('double r2=0,b2=0;',
        'for(auto z:got)if(!std::isfinite(std::abs(z))){fprintf(stderr,"nonfinite smoke output\\n");exit(7);} double r2=0,b2=0;')
    source.write_text(checked)
    text=checked.replace('using Z=std::complex<double>;','using Z=double;').replace('CUDA_C_64F','CUDA_R_64F')
    real.write_text(re.sub(r'Z\(([^,()]+),([^()]+)\)',r'Z(\1)',text))
    smoke_rows=[]
    for dtype,src in [('complex',source),('real',real)]:
        binary=smoke/dtype;command=['g++','-O2','-std=c++17','-I'+str(core.production_sources()/'include/cudss'),'-I'+str(cuda/'include'),str(src),
            '-L'+str(native.parent),'-l:libcudss.so.0.7.1','-L'+str(cuda/'lib64'),'-Wl,-rpath-link,'+str(cuda/'lib64'),'-lcudart','-o',str(binary)]
        with (smoke/(dtype+'-build.log')).open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
        for backend in ['cudss','mycudss-direct','mycudss-worker']:
            if backend=='cudss':libroot=native.parent;env=core.base_env(8)
            else:libroot,env,_=testing.environment(backend,8)
            env['LD_LIBRARY_PATH']=str(libroot)+os.pathsep+str(cuda/'lib64')
            result=subprocess.run([str(binary)],env=env,capture_output=True,text=True,timeout=60)
            (smoke/(dtype+'-'+backend+'.stdout.log')).write_text(result.stdout)
            (smoke/(dtype+'-'+backend+'.stderr.log')).write_text(result.stderr)
            lines=[json.loads(line.split(' ',1)[1]) for line in result.stdout.splitlines() if line.startswith('ABI_SMOKE_RESULT ')]
            assert result.returncode==0 and len(lines)==1 and lines[0]['passed'],(dtype,backend,result.stdout,result.stderr)
            smoke_rows.append({'dtype':dtype,'backend':backend,**lines[0]})
    baseline=json.loads((OUT/'baseline.json').read_text());unchanged={path:core.sha(path)==digest for path,digest in baseline.items()}
    assert all(ok for path,ok in unchanged.items() if not path.endswith('.prefs')),unchanged
    preference_audit=json.loads((OUT/'preference-audit.json').read_text())
    assert all(row['preference_entries_unchanged_proven_by_exact_hash'] and hashlib.sha256(Path(path).read_bytes().partition(b'\n')[2]).hexdigest()==row['entries_sha256'] for path,row in preference_audit.items())
    original=json.loads((OUT/'imported-sources.json').read_text());assert all(core.sha(item['source'])==item['sha256'] for item in original.values())
    comparisons={}
    for name,summary in [('full_59',suite['summary']),('fixed_6_systems_12_RHS',fixed['summary'])]:
        reference=summary['cudss']['mean_seconds'];comparisons[name]={backend:{'mean_seconds':row['mean_seconds'],
             'samples':row['seconds'],'extra_seconds_vs_native':row['mean_seconds']-reference,'extra_percent_vs_native':100*(row['mean_seconds']/reference-1)} for backend,row in summary.items()}
    report={'status':'completed','version':'0.5.0','completed_at':datetime.datetime.now().astimezone().isoformat(),
            'features':{'menu':'F8 测试','backends':testing.BACKENDS,'CLI':['test status','test build','test run','test benchmark'],
                        'optional_source_directory':str(ROOT/'dev/benchmarks/mycudss'),'components':str(testing.directory()),
                        'calibration_sources_excluded_from_production_Git_and_wheel':True},
            'calibrated_source_provenance':json.loads((OUT/'calibrated-source-check.json').read_text()),
            'comparison':comparisons,'full_suite':suite,'fixed_suite':fixed,'smoke':smoke_rows,
            'UI':json.loads((OUT/'ui-verification.json').read_text()),'wheel':json.loads((OUT/'wheel-audit.json').read_text()),
            'unit_tests':{'passed':32,'log':str(OUT/'unit-tests-synchronous-focus.log')},
            'baseline_files_unchanged':unchanged,'preference_audit':preference_audit,'preference_entries_unchanged':True,'original_test_source_unchanged':True,
            'native_library':str(native),'native_library_sha256':core.sha(native),
            'CUDA_sha256':{name:core.sha(cuda/'lib64'/name) for name in ['libcudart.so.12','libcublas.so.12','libcublasLt.so.12']},
            'component_manifest':core.read(testing.directory()/'manifest.json'),
            'migration_issues':[{'issue':'Linker could not locate transitive cuBLAS dependency while linking the native worker.',
                  'fix':'Use configured CUDA library directory as link-time rpath-link; no runtime vendor binaries modified.','log':str(OUT/'worker-link-failure.log')},
                 {'issue':'Fast keyboard tab switching could leave focus waiting on a deferred callback.',
                  'fix':'Focus the selected pane synchronously; all keyboard regression cases and real PTY checks pass.'},
                 {'issue':'Raw preference file hashes changed during COMSOL shutdown.',
                  'fix':'Exact baseline hash reconstruction proves only timestamp comments changed. All setting entries are unchanged; no preference files restored or rewritten.'}],
            'limitations':['Full S timings include assembly, solver preparation and bridge work; loading, validation and saving are excluded.',
                'Two full runs per variant and three fixed replay blocks are local observations, not a broad performance guarantee.',
                'Full model assemblies can differ at floating-point/sparsity details. Fixed replay controls byte-identical A0/A1 and the same manufactured RHS, with GPU completion before stopping timers.',
                'Fixed replay reports synchronous ABI completion time, including bridge and backend; it is not pure GPU kernel timing.',
                'GPU memory values are sampled whole-device use, including Windows/desktop and other processes.',
                'Worker calibration covers single GPU, general full CSR, FP64 real/complex, single RHS and phase-boundary cancellation; advanced cuDSS modes are outside this acceptance scope.',
                'The optional testing backend sources, models and raw reports remain outside the production source repository and application wheel. No customer release archive generated.']}
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['cuDSS 可选测试入口与原生校准：完成','应用版本 0.5.0；F8 测试页可手动选择原生/直接封装/worker封装。','',
           '完整 S Study3：59频点，8线程，CUDA12.9，FP64，hybrid memory never；每方案两次：']
    for backend,row in comparisons['full_59'].items():lines.append(f"{backend}: 均值 {row['mean_seconds']:.6f}s；原始值 {row['samples']}；较原生 {row['extra_percent_vs_native']:+.3f}%")
    lines+=['','严格固定A/b回放：每次6系统/12RHS，三组交错重复，同步完成后停表：']
    for backend,row in comparisons['fixed_6_systems_12_RHS'].items():lines.append(f"{backend}: 均值 {row['mean_seconds']:.6f}s；原始值 {row['samples']}；较原生 {row['extra_percent_vs_native']:+.3f}%")
    lines+=['','验收：完整扫描6×236=1416项CPU参考通过；对原生保存结果直接比较5×236=1180项通过；固定回放108项已知解检查通过；实数/复数、重分解和零RHS六组smoke通过。',
            '32项测试、安装版构建与F8键盘操作、真实PTY退出检查通过。',
            '校准C++源码与v1_native_equivalent完全一致起步；仅移除硬编码安装路径，改用本模组配置及CUDA12.9构建。',
            '正式Pangu组件、原SDK/许可、本机配置和原工程保持；测试组件位于runtime/tests/mycudss，源码位于忽略的dev/benchmarks/mycudss。',
            'COMSOL自行刷新两份偏好文件的时间注释；已通过原始哈希精确重构证明所有设置键值保持，模组未恢复或改写用户偏好文件。',
            '', '结果路径：',str(OUT/'suite.json'),str(OUT/'fixed/suite.json'),str(OUT/'native-comparison'),'', '限制：',*report['limitations']]
    (OUT/'report.txt').write_text('\n'.join(lines)+'\n')
    (ROOT/'data/runs/latest-mycudss.json').write_text(json.dumps({'directory':str(OUT),'status':'completed','report':str(OUT/'report.json'),'text_report':str(OUT/'report.txt')},indent=2)+'\n')
    print('\n'.join(lines[:16]))


if __name__=='__main__':main()
