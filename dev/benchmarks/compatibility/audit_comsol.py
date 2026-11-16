#!/usr/bin/env python3
"""Inventory the binary interface needed by this COMSOL cuDSS integration."""
import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
GPU_SYMBOL = re.compile(r'\b(?:U|w)\s+((?:__cuda|cuda|cudss|cublas|cusparse|cusolver|nvml|nccl|cu[A-Z])\w*)(?:@([^\s]+))?\s*$')


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def run(*args):
    return subprocess.check_output(list(map(str, args)), text=True, stderr=subprocess.DEVNULL)


def inspect(path, raw):
    undefined = run('nm', '-D', '--undefined-only', path)
    dynamic = run('readelf', '-d', path)
    sections = run('readelf', '-S', '--wide', path)
    imports = []
    for line in undefined.splitlines():
        m = GPU_SYMBOL.search(line)
        if m:
            imports.append(dict(name=m[1], version=m[2]))
    for suffix, text in [('undefined.txt', undefined), ('dynamic.txt', dynamic), ('sections.txt', sections)]:
        (raw / (path.name + '.' + suffix)).write_text(text)
    return dict(path=str(path), sha256=sha(path), imports=imports, needed=re.findall(r'Shared library: \[([^]]+)\]', dynamic), device_code_sections=[s for s in sections.splitlines() if 'nv_fatbin' in s or '__nv_relfatbin' in s])


def symbols(info):
    return {entry['name'] for entry in info['imports']}


def exports(path):
    return set(re.findall(r'\b(?:T|W)\s+(\w+)(?:@@?[^\s]+)?\s*$', run('nm', '-D', '--defined-only', path), re.M))


def main():
    config = json.loads((ROOT / 'config/runtime.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comsol-root', type=Path, default=Path(config['comsol_root']))
    parser.add_argument('--adapter', type=Path, default=ROOT / 'runtime/module/libcudss.so.0.7.1')
    parser.add_argument('--candidate-runtime', type=Path, help='Optional DCU CUDA-compatibility libcudart for symbol coverage comparison')
    parser.add_argument('--out', type=Path, default=ROOT / 'data/runs/audits/comsol-dcu')
    parser.add_argument('--maps', type=Path, default=ROOT / 'docs/validation/runtime-maps.txt.gz', help='Optional captured process maps; plain text or gzip')
    args = parser.parse_args()
    out = args.out.resolve()
    raw = out / 'raw'
    raw.mkdir(parents=True, exist_ok=True)
    lib = args.comsol_root.resolve() / 'lib/glnxa64'
    all_libraries = {}
    for p in sorted(lib.glob('libcs*.so')):
        undefined = run('nm', '-D', '--undefined-only', p)
        if any(GPU_SYMBOL.search(line) for line in undefined.splitlines()):
            all_libraries[p.name] = inspect(p, raw)
    core = ['libcscudss.so', 'libcscudautil.so']
    core_symbols = set().union(*(symbols(all_libraries[name]) for name in core))
    adapter = inspect(args.adapter.resolve(strict=True), raw)
    adapter_exports = exports(args.adapter.resolve())
    cuDSS = sorted(s for s in core_symbols if s.startswith('cudss'))
    runtime = sorted(s for s in core_symbols if s.startswith('cuda'))
    adapter_runtime = sorted(s for s in symbols(adapter) if s.startswith('cuda'))
    runtime_with_adapter = sorted(set(runtime) | set(adapter_runtime))
    internals = sorted(s for s in core_symbols if s.startswith('__cuda'))
    report = dict(generated_at=datetime.datetime.now().astimezone().isoformat(), scope='COMSOL 6.4.0.293 Linux x86_64 direct sparse cuDSS path, based on local ELF imports, call-site disassembly, and optional captured process maps. Not a claim of all runtime paths or all COMSOL GPU features.', comsol_root=str(args.comsol_root.resolve()), scanned_comsol_library_count=len(list(lib.glob('libcs*.so'))), libraries=all_libraries, adapter=adapter, core_library_names=core, cudss_direct_imports=cuDSS, cudss_direct_import_count=len(cuDSS), adapter_cudss_exports=sorted(s for s in adapter_exports if s.startswith('cudss')), missing_cudss_symbols=sorted(set(cuDSS)-adapter_exports), comsol_cuda_runtime_imports=runtime, cuda_runtime_with_current_adapter=runtime_with_adapter, compiler_runtime_imports=internals, core_direct_cublas_imports=sorted(s for s in core_symbols if s.startswith('cublas')), notes=[])
    wrapper = lib / 'libcscudss.so'
    util = lib / 'libcscudautil.so'
    for p in (wrapper, util):
        (raw / (p.name + '.disassembly.txt')).write_text(run('objdump', '-d', '-C', p))
    report['cudss_parameters'] = {
        'evidence': 'Constants at cudssConfigSet/DataGet/DataSet and cudssExecute call sites in libcscudss.so disassembly, interpreted with the bundled 0.7.1 header. These are static paths; not every path is exercised by S.',
        'ConfigSet': {str(k): v for k, v in {0:'CUDSS_CONFIG_REORDERING_ALG',1:'CUDSS_CONFIG_FACTORIZATION_ALG',3:'CUDSS_CONFIG_USE_MATCHING',4:'CUDSS_CONFIG_MATCHING_ALG',6:'CUDSS_CONFIG_IR_N_STEPS',9:'CUDSS_CONFIG_PIVOT_THRESHOLD',10:'CUDSS_CONFIG_PIVOT_EPSILON',12:'CUDSS_CONFIG_HYBRID_MODE',13:'CUDSS_CONFIG_HYBRID_DEVICE_MEMORY_LIMIT',16:'CUDSS_CONFIG_HYBRID_EXECUTE_MODE',22:'CUDSS_CONFIG_DEVICE_COUNT',23:'CUDSS_CONFIG_DEVICE_INDICES'}.items()},
        'DataGet': {'2':'CUDSS_DATA_NPIVOTS','10':'CUDSS_DATA_HYBRID_DEVICE_MEMORY_MIN','12':'CUDSS_DATA_MEMORY_ESTIMATES'},
        'DataSet': {'22':'CUDSS_DATA_USER_HOST_INTERRUPT'},
        'Execute': {'1':'REORDERING','2':'SYMBOLIC_FACTORIZATION','4':'FACTORIZATION','1008':'SOLVE (0x3f0)'},
        'current_semantic_gaps': [
            'ConfigSet stores/retrieves most values without translating their numerical policy. Ordering/reuse come from module settings; GUI hybrid memory and compute are disabled by PanguModule.',
            'HYBRID_DEVICE_MEMORY_MIN is not implemented; the original COMSOL wrapper contains an 8-byte DataGet request in its hybrid path.',
            'MEMORY_ESTIMATES returns only the known RHS/X buffer budget, not full factorization estimates.',
            'NPIVOTS returns -1. COMSOL requests 4 bytes at its call site; the adapter chooses required size from the sparse index width, which needs a separate ABI-size check for wider indices.',
            'USER_HOST_INTERRUPT has both (pointer,4) and (NULL,0) call paths in COMSOL. The adapter currently rejects (NULL,0), so clearing the callback is not compatible.',
            'The wrapper contains single/multi-device, single/double precision, hybrid and matching policy branches. Current verified single-GPU FP64 policy is a subset.'
        ]}
    report['device_probe'] = dict(library=str(util), jni_entry='Java_com_comsol_nativejni_gpu_GpuUtil_cudaCheckDeviceIdNative', check_entry_address='0x1c00', probe_call_address='0x1c66 -> 0x19d0', launch_call_address='0x1a5c -> cudaLaunchKernel@plt', additional_set_device_probe='cudaSetDeviceIdNative also calls 0x19d0 at 0x1dfc', finding='A real CUDA dummy-kernel launch path exists during device checking/selection. Providing only cudaGetDeviceCount and allocation functions does not cover it. DCU compatibility must execute/map the probe meaningfully or provide a different supported device-integration path.')
    maps = args.maps.resolve()
    if maps.is_file():
        content = gzip.decompress(maps.read_bytes()).decode() if maps.suffix == '.gz' else maps.read_text()
        report['existing_runtime_evidence'] = dict(path=str(maps), sha256=sha(maps), mapped_comsol_gpu_libraries=sorted({line.split()[-1] for line in content.splitlines() if any(name in line for name in all_libraries)}), interpretation='Maps prove library loading, not the execution of every imported function or numerical option.')
    else:
        report['existing_runtime_evidence'] = dict(path=str(maps), available=False, interpretation='No maps snapshot supplied; this report contains static inspection only.')
    broad = all_libraries.get('libcscuda.so')
    if broad:
        report['additional_comsol_gpu_wrapper'] = dict(library=broad['path'], runtime_imports=sorted(s for s in symbols(broad) if s.startswith('cuda')), cublas_imports=sorted(s for s in symbols(broad) if s.startswith('cublas')), note='Separate broader GPU wrapper, not observed loaded in the accepted S direct-solver process. Include it if the supported product scope uses these other GPU paths.')
    report['notes'] = [
        'Both libcscudss.so and libcscudautil.so have no directly imported cublas functions. libcscudss.so nevertheless has DT_NEEDED libcublas.so.12, which must be loadable. This is a loader dependency, not evidence that complete cuBLAS operation translation is necessary for this direct-solver path.',
        'No direct libcuda driver-API or NVML imports were found in the scanned COMSOL GPU wrappers. A vendor runtime may internally need its own driver API. Do not infer a need to emulate all libcuda/NVML from NVIDIA runtime transitive dependencies.',
        'The DCU Pangu SDK is available to the user but not inspected here. Its API/ABI, dependencies, DTK version and matrix/vector memory rules still need comparison with the current worker.',
        'Preserve CUDA 12 SONAME/versioned-symbol expectations, cudaDeviceProp_v2/pointer-attribute layouts, memory ownership, copy directions, synchronization, and error semantics.',
        'Core solver replacement does not by itself cover COMSOL rendering, DNN training, or time-explicit GPU formulations.',
        'Static undefined imports do not find every possible dlsym-resolved dependency and do not prove runtime call coverage. Expand execution tracing across intended model classes before claiming comprehensive replacement.'
    ]
    if args.candidate_runtime:
        candidate = args.candidate_runtime.resolve(strict=True)
        available = exports(candidate)
        required = set(runtime_with_adapter) | set(internals)
        report['candidate_runtime'] = dict(path=str(candidate), sha256=sha(candidate), required_symbols=sorted(required), missing_symbols=sorted(required-available), note='Name coverage only. Successful symbol lookup does not validate ABI, device kernels, or numerical/runtime semantics.')
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    lines = ['COMSOL -> 海光 Pangu 接入依赖核对', '目标：COMSOL 6.4.0.293 / Linux x86_64 / cuDSS 0.7.1 直接求解路径。', '依据：本机 ELF 未定义符号、DT_NEEDED、调用点反汇编和既有 S 扫描 maps。静态清单不是所有动态调用的执行证明。', '', f'COMSOL 包装库导入 cuDSS 函数 {len(cuDSS)} 个；当前适配库全部导出；缺失符号 {len(report["missing_cudss_symbols"])} 个。']
    lines += ['  '+s for s in cuDSS]
    lines += ['', f'COMSOL 核心两库的 CUDA Runtime 普通函数：{len(runtime)} 个。'] + ['  '+s for s in runtime]
    lines += ['', '保留当前适配层时额外需要：'] + ['  '+s for s in sorted(set(adapter_runtime)-set(runtime))]
    lines += ['', f'编译器 Runtime / 内核注册接口：{len(internals)} 个。'] + ['  '+s for s in internals]
    lines += ['', '重要：libcscudautil.so 的 GPU 检查/选择函数会走到 dummy kernel 启动路径。', '仅提供设备枚举与显存 malloc/memcpy 不足以覆盖该路径。', '证据：raw/libcscudautil.so.disassembly.txt，0x1c66 -> 0x19d0 -> 0x1a5c cudaLaunchKernel。', '', '当前 cuDSS 参数语义缺口：']
    lines += ['  '+s for s in report['cudss_parameters']['current_semantic_gaps']]
    lines += ['', 'COMSOL 实际设置的配置项：'] + ['  '+k+' = '+v for k,v in report['cudss_parameters']['ConfigSet'].items()]
    lines += ['', 'cuBLAS：核心两库没有直接函数导入，但 libcscudss.so 的 libcublas.so.12 装载依赖必须满足。', '若扩大到 libcscuda.so 的其他 GPU 计算路径，还需它导入的 cuBLAS 操作和更多 CUDA/内核支持；详见 JSON。', '', '限制与后续：'] + ['  '+s for s in report['notes']]
    lines += ['', '海光侧核对方式：python3 dev/benchmarks/compatibility/audit_comsol.py --candidate-runtime /path/to/DCU/libcudart.so.12', '候选 Runtime 检查只比较导出名称，不证明 ABI 或实际设备行为兼容。', '完整清单及逐库 SHA256：report.json；nm/readelf/objdump 原始证据：raw/。']
    (out / 'report.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(report=str(out / 'report.json'), cudss_imports=len(cuDSS), missing_cudss=report['missing_cudss_symbols'], COMSOL_runtime_functions=len(runtime), runtime_with_adapter=len(runtime_with_adapter), compiler_runtime_functions=len(internals)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
