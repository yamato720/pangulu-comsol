#!/usr/bin/env python3
"""Portable native-matrix replay; Python standard library only."""
import argparse
import array
import csv
import datetime
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'config.local.json'
BACKENDS = ('pangu-L0', 'pangu-L1', 'mycudss', 'cudss')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def read(path):
    return json.loads(Path(path).read_text())


def resolve(value):
    return str(Path(value).expanduser().resolve(strict=True))


def libdir(root, filenames):
    root = Path(root)
    for candidate in (root / 'lib64', root / 'lib', root / 'targets/x86_64-linux/lib'):
        if any((candidate / filename).exists() for filename in filenames):
            return str(candidate.resolve())
    raise ValueError('Library directory not found under ' + str(root))


def load_config():
    if not CONFIG.is_file():
        raise ValueError('Run configure first; see README-REPLAY.txt')
    return read(CONFIG)


def verify(which='all'):
    names = ['code-files.json', 'data-files.json'] if which == 'all' else [which + '-files.json']
    total = 0
    for name in names:
        path = ROOT / 'manifests' / name
        if not path.is_file():
            raise ValueError('Missing manifest: extract both code and data archives together')
        for relative, expected in read(path)['sha256'].items():
            target = ROOT / relative
            if not target.is_file() or digest(target) != expected:
                raise ValueError('Package file missing or changed: ' + relative)
            total += 1
    print('VERIFIED', total, 'package files', flush=True)


def configure(args):
    if sys.platform != 'linux' or sys.byteorder != 'little':
        raise ValueError('This replay targets little-endian Linux x86_64 with NVIDIA CUDA')
    value = {'sdk': resolve(args.sdk), 'cuda': resolve(args.cuda),
             'cudss_library': resolve(args.cudss_library), 'cudss_include': resolve(args.cudss_include),
             'licence': [resolve(x) for x in args.licence], 'threads': args.threads,
             'cxx': shlex.split(args.cxx), 'library_paths': [resolve(x) for x in args.library_path],
             'cupti': resolve(args.cupti) if args.cupti else None}
    if args.threads < 1 or (hasattr(os, 'sched_getaffinity') and args.threads > len(os.sched_getaffinity(0))):
        raise ValueError('Invalid thread count for current CPU affinity')
    header = Path(value['sdk']) / 'include/pangulu.h'
    if not header.is_file() or not (Path(value['sdk']) / 'lib/libpangulu.so').is_file():
        raise ValueError('--sdk must be the extracted original SDK root containing include/ and lib/')
    text = header.read_text()
    abi = re.search(r'PANGULU_ABI_VERSION\s+(\d+)', text)
    if not abi or int(abi[1]) != 2:
        raise ValueError('This harness requires PanguLU public ABI 2')
    version = re.search(r'PANGULU_VERSION_STRING\s+"([^"]+)"', text)
    value['sdk_version'] = version[1] if version else 'unknown'
    value['sdk_abi'] = int(abi[1])
    native_header = Path(value['cudss_include']) / 'cudss.h'
    text = native_header.read_text()
    version = tuple(int(re.search(r'CUDSS_VERSION_' + part + r'\s+(\d+)', text)[1]) for part in ['MAJOR', 'MINOR', 'PATCH'])
    if version != (0, 7, 1):
        raise ValueError('Use matching NVIDIA cuDSS 0.7.1 headers for this exact experiment')
    value['cudss_header_version'] = '.'.join(map(str, version))
    if not (Path(value['cuda']) / 'include/cuda_runtime_api.h').is_file():
        raise ValueError('CUDA headers missing under --cuda/include')
    value['cuda_lib'] = libdir(value['cuda'], ['libcudart.so', 'libcudart.so.12'])
    if value['cupti']:
        if not (Path(value['cupti']) / 'include/cupti.h').is_file():
            raise ValueError('CUPTI headers missing under --cupti/include')
        value['cupti_lib'] = libdir(value['cupti'], ['libcupti.so', 'libcupti.so.12'])
    value['dependency_sha256'] = {'pangu_core': digest(Path(value['sdk']) / 'lib/libpangulu.so'),
                                  'pangu_header': digest(header), 'cudss': digest(value['cudss_library']),
                                  'cudss_header': digest(native_header),
                                  'licences': {x: digest(x) for x in value['licence']}}
    if not shutil.which(value['cxx'][0]):
        raise ValueError('C++ compiler not found: ' + value['cxx'][0])
    write(CONFIG, value)
    print('CONFIGURED', CONFIG, '\nSDK', value['sdk_version'], 'ABI', value['sdk_abi'], flush=True)


def build():
    verify('code')
    c = load_config()
    target = ROOT / 'build'
    target.mkdir(exist_ok=True)
    direct = target / 'direct'
    direct.mkdir(exist_ok=True)
    commands = []
    def compile(arguments):
        command = c['cxx'] + list(map(str, arguments))
        commands.append(command)
        with (target / 'build.log').open('a') as log:
            log.write(shlex.join(command) + '\n')
            log.flush()
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    flags = ['-O2', '-std=c++17', '-I' + str(ROOT / 'src'), '-I' + c['cudss_include'], '-I' + c['cuda'] + '/include']
    compile(flags + ['-fPIC', '-shared', ROOT / 'src/mycudss-direct.cpp', '-ldl', '-Wl,-soname,libcudss.so.0', '-o', direct / 'libcudss.so.0.7.1'])
    for name in ['libcudss.so', 'libcudss.so.0']:
        path = direct / name
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            raise ValueError('Refusing to replace a non-symlink: ' + str(path))
        path.symlink_to('libcudss.so.0.7.1')
    if c['cupti']:
        compile(flags + ['-DPGL_AUDIT_CUPTI=1', '-I' + c['cupti'] + '/include', '-c', ROOT / 'src/activity.cpp', '-o', target / 'activity.o'])
    for backend in ['pangu', 'mycudss', 'cudss']:
        for traced in ([False, True] if c['cupti'] else [False]):
            binary = target / (backend + ('-traced' if traced else '-plain'))
            arguments = flags + ['-DBENCH_PANGU=' + str(int(backend == 'pangu')), '-DPGL_AUDIT_CUPTI=' + str(int(traced)),
                                 '-I' + c['sdk'] + '/include', ROOT / 'src/duel.cpp']
            if traced:
                arguments += [target / 'activity.o', '-L' + c['cupti_lib'], '-Wl,-rpath,' + c['cupti_lib'], '-l:libcupti.so.12']
            if backend == 'pangu':
                arguments += ['-L' + c['sdk'] + '/lib', '-Wl,-rpath,' + c['sdk'] + '/lib', '-Wl,--no-as-needed', '-lpangulu', '-lcublas', '-lcublasLt']
            elif backend == 'mycudss':
                arguments += ['-L' + str(direct), '-Wl,-rpath,$ORIGIN/direct', '-l:libcudss.so.0.7.1']
            else:
                native = Path(c['cudss_library'])
                arguments += ['-L' + str(native.parent), '-Wl,-rpath,' + str(native.parent), '-l:' + native.name]
            arguments += ['-L' + c['cuda_lib'], '-Wl,-rpath,' + c['cuda_lib'], '-lcudart', '-ldl', '-lm', '-pthread', '-o', binary]
            compile(arguments)
    compile(flags + ['-I' + str(ROOT / 'upstream/MOOSE-with-Solved-Tech/adapter'), '-I' + c['sdk'] + '/include',
            ROOT / 'src/original-host.cpp', '-L' + c['sdk'] + '/lib', '-Wl,-rpath,' + c['sdk'] + '/lib',
            '-L' + c['cuda_lib'], '-Wl,-rpath,' + c['cuda_lib'], '-Wl,--no-as-needed', '-lpangulu', '-lpangulu_platform',
            '-lcudart', '-lcublas', '-lcublasLt', '-ldl', '-pthread', '-o', target / 'pangu-original-host'])
    write(target / 'manifest.json', {'configured_dependencies': c['dependency_sha256'], 'commands': commands,
           'config_sha256': digest(CONFIG), 'binaries': {str(p.relative_to(target)): digest(p) for p in target.rglob('*') if p.is_file() and not p.is_symlink() and p.name not in ['build.log', 'manifest.json']}})
    print('BUILT', target, flush=True)


def clean_env(c, backend):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PC_', 'PGL_', 'PANGULU_', 'MYCUDSS_', 'LD_'))}
    env.update(OMP_NUM_THREADS=str(c['threads']), OMP_DYNAMIC='FALSE', MKL_NUM_THREADS=str(c['threads']), OPENBLAS_NUM_THREADS=str(c['threads']))
    native = str(Path(c['cudss_library']).parent)
    paths = ([c['sdk'] + '/lib'] if backend == 'pangu' else [str(ROOT / 'build/direct'), native] if backend == 'mycudss' else [native])
    paths += [c['cuda_lib']] + c['library_paths']
    if c['cupti']:
        paths.append(c['cupti_lib'])
    env['LD_LIBRARY_PATH'] = os.pathsep.join(paths)
    env['PANGULU_LICENSE_PATHS'] = ','.join(c['licence'])
    env['MYCUDSS_LIBRARY'] = c['cudss_library']
    return env


def vector(path):
    values = array.array('d')
    with Path(path).open('rb') as stream:
        values.fromfile(stream, Path(path).stat().st_size // 8)
    if sys.byteorder != 'little':
        values.byteswap()
    return values


def compare_reference(dataset, folder):
    reference = vector(dataset / 'application_linear_solution.bin')
    solution = vector(folder / 'physical_solution.bin')
    if len(reference) != len(solution) or not all(math.isfinite(v) for v in solution):
        raise ValueError('Physical solution shape or finiteness check failed')
    error = math.sqrt(math.fsum((a-b)**2 for a, b in zip(solution, reference)) / math.fsum(x*x for x in reference))
    if not math.isfinite(error) or error > 1e-6:
        raise ValueError('Physical solution differs from CPU MUMPS: ' + str(error))
    return error


def analyzer():
    spec = importlib.util.spec_from_file_location('replay_analyzer', ROOT / 'src/analyze.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def trial(c, dataset, case, backend_name, traced, folder, timeout):
    folder.mkdir()
    backend = 'pangu' if backend_name.startswith('pangu-') else backend_name
    reorder = backend_name.split('-')[-1] if backend == 'pangu' else 'native_auto'
    layout = 'csc' if backend == 'pangu' else 'csr'
    env = clean_env(c, backend)
    env.update(PC_TRACE_DIR=str(folder), PC_MATRIX_REORDER=reorder, PC_MATRIX_SINGLE_VALUES='1', PC_MATRIX_MAX_RHS='1')
    metadata = read(dataset / 'metadata.json')
    command = [str(ROOT / 'build' / (backend + ('-traced' if traced else '-plain'))), str(dataset), metadata['dtype'], metadata['type'], '0', layout]
    write(folder / 'command.json', {'command': command, 'backend': backend_name, 'traced': traced,
          'threads': c['threads'], 'CUDA_VISIBLE_DEVICES': env.get('CUDA_VISIBLE_DEVICES'), 'timeout_seconds': timeout})
    state = {'status': 'running', 'label': folder.name, 'backend': backend_name, 'case': case, 'traced': traced,
             'layout': layout, 'metadata': metadata, 'maps': {}, 'telemetry': [], 'started_at': datetime.datetime.now().astimezone().isoformat()}
    process = None
    started = time.monotonic()
    sampled = -100
    try:
        with (folder / 'stdout.log').open('w') as stdout, (folder / 'stderr.log').open('w') as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=env, start_new_session=True)
            while process.poll() is None:
                elapsed = time.monotonic() - started
                state['process_wall_seconds'] = elapsed
                try:
                    mappings = Path('/proc') / str(process.pid) / 'maps'
                    libraries = sorted({line.split()[-1] for line in mappings.read_text().splitlines() if len(line.split()) >= 6 and any(x in line for x in ['libpangulu', 'libcudss', 'libcudart', 'libcublas', 'libcupti'])})
                    if libraries:
                        state['maps'][str(process.pid)] = libraries
                except OSError:
                    pass
                if elapsed-sampled >= 1 and shutil.which('nvidia-smi'):
                    query = subprocess.run(['nvidia-smi', '--query-gpu=name,uuid,driver_version,memory.used,memory.free,utilization.gpu,temperature.gpu,clocks.current.sm', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5)
                    state['telemetry'].append({'elapsed_seconds': elapsed, 'data': query.stdout.strip(), 'returncode': query.returncode})
                    sampled = elapsed
                write(folder / 'result.json', state)
                if elapsed > timeout:
                    raise TimeoutError('Trial exceeded ' + str(timeout) + ' seconds; not counted as success')
                time.sleep(.15)
        state['exit_code'] = process.returncode
        events = []
        for line in (folder / 'stdout.log').read_text().splitlines():
            tag, _, body = line.partition(' ')
            if tag in ['MATRIX', 'CONFIG', 'DEVICE', 'PHASE', 'MEMORY', 'CHECK', 'RESULT']:
                events.append({'tag': tag, **json.loads(body)})
        state['events'] = events
        device = next((e for e in events if e['tag'] == 'DEVICE'), None)
        if not device or len(device.get('uuid_hex', '')) != 32:
            raise ValueError('Missing actual CUDA device UUID evidence')
        state['device'] = device
        checks = [x for x in events if x['tag'] == 'CHECK']
        if process.returncode != 0 or len(checks) != 2 or not all(x['passed'] for x in checks) or not any(x['tag'] == 'RESULT' and x['passed'] for x in events):
            raise ValueError('Missing completion marker, failed API, or failed numerical checks')
        physical_error = compare_reference(dataset, folder)
        state['physical_solution_relative_l2_vs_MUMPS'] = physical_error
        mapped = {str(Path(x).resolve()) for libraries in state['maps'].values() for x in libraries}
        inventory = {str(Path(line.removeprefix('LOADED ')).resolve()) for line in (folder / 'stdout.log').read_text().splitlines() if line.startswith('LOADED ')}
        state['self_mapped_libraries'] = sorted(inventory)
        mapped |= inventory
        if backend == 'pangu':
            if str((Path(c['sdk']) / 'lib/libpangulu.so').resolve()) not in mapped or any('libcudss' in Path(x).name for x in mapped):
                raise ValueError('Unexpected or missing PanguLU library mapping')
        else:
            if c['cudss_library'] not in mapped or any('libpangulu' in Path(x).name for x in mapped):
                raise ValueError('Unexpected or missing original NVIDIA cuDSS library mapping')
            shim = str((ROOT / 'build/direct/libcudss.so.0.7.1').resolve())
            if (shim in mapped) != (backend == 'mycudss'):
                raise ValueError('Native/forwarding cuDSS path mismatch')
        if traced:
            summaries = []
            for path in folder.glob('activities-*.jsonl'):
                tail = path.read_bytes()[-4096:].decode().splitlines()
                summaries.extend(json.loads(line) for line in tail if line.startswith('{"type":"summary"'))
            if not summaries or any(x['dropped'] for x in summaries):
                raise ValueError('GPU trace incomplete or dropped records')
            state['trace_summaries'] = summaries
        state['status'] = 'completed'
    except Exception as error:
        state.update(status='failed', error=type(error).__name__ + ': ' + str(error))
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        state['process_wall_seconds'] = time.monotonic() - started
        if process is not None:
            state['exit_code'] = process.returncode
        write(folder / 'result.json', state)
    if state['status'] == 'completed':
        analyzer().analyze(folder)
    print('TRIAL', backend_name, folder.name, state['status'], state.get('error', ''), flush=True)
    return state


def summarize(output):
    states = [read(p) for p in sorted(output.glob('*/result.json'))]
    grouped = {}
    for state in states:
        if state['status'] != 'completed':
            continue
        metrics = read(output / state['label'] / 'metrics.json')['metrics']
        core = sum(metrics[name]['wall_seconds'] for name in ['analysis', 'numeric', 'physical_solve'])
        key = state['backend'] + ('/traced' if state['traced'] else '/plain')
        row = {'trial': state['label'], 'analysis_s': metrics['analysis']['wall_seconds'], 'numeric_s': metrics['numeric']['wall_seconds'],
               'physical_solve_s': metrics['physical_solve']['wall_seconds'], 'core_s': core,
               'physical_solution_relative_l2_vs_MUMPS': state['physical_solution_relative_l2_vs_MUMPS']}
        if state['traced']:
            row['numeric_GPU_kernel_s'] = metrics['numeric']['GPU_kernel_seconds']
        grouped.setdefault(key, []).append(row)
    plan = read(output / 'plan.json')
    value = {'status': 'completed' if len(states) == len(plan) and all(s['status'] == 'completed' for s in states) else 'completed_with_failures',
             'planned_attempts': len(plan),
             'successful': sum(s['status'] == 'completed' for s in states), 'attempts': len(states),
             'failed': [{'trial': s['label'], 'error': s.get('error')} for s in states if s['status'] != 'completed'], 'groups': {}}
    value['CUDA_device_UUIDs'] = sorted({s['device']['uuid_hex'] for s in states if s['status'] == 'completed'})
    if len(value['CUDA_device_UUIDs']) != 1:
        value['status'] = 'failed_device_mismatch_or_no_success'
    for key, rows in grouped.items():
        means = {name: statistics.mean(r[name] for r in rows) for name in rows[0] if name != 'trial'}
        value['groups'][key] = {'processes': len(rows), 'mean': means, 'runs': rows}
    write(output / 'summary.json', value)
    lines = ['Pinched cylinder replay; seconds. Plain and traced measurements are separate.',
             'backend | repetitions | analysis | numeric | physical RHS solve | core total | numeric GPU kernel']
    with (output / 'comparison.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['backend', 'processes', 'analysis_s', 'numeric_s', 'physical_solve_s', 'core_s', 'numeric_GPU_kernel_s'])
        for key, group in value['groups'].items():
            m = group['mean']
            writer.writerow([key, group['processes'], m['analysis_s'], m['numeric_s'], m['physical_solve_s'], m['core_s'], m.get('numeric_GPU_kernel_s')])
            label = key.replace('pangu-', 'PanguLU ')
            lines.append(f"{label} | {group['processes']} | {m['analysis_s']:.6f} | {m['numeric_s']:.6f} | {m['physical_solve_s']:.6f} | {m['core_s']:.6f} | {m.get('numeric_GPU_kernel_s', 'untraced')}")
    lines += ['status: ' + value['status'], 'failed trials: ' + str(len(value['failed']))]
    (output / 'summary.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines), flush=True)
    return value


def run(args):
    verify()
    c = load_config()
    manifest = read(ROOT / 'build/manifest.json')
    if manifest['config_sha256'] != digest(CONFIG):
        raise ValueError('Configuration changed after build; rebuild first')
    for relative, expected in manifest['binaries'].items():
        if digest(ROOT / 'build' / relative) != expected:
            raise ValueError('Build file changed: ' + relative)
    for name, path in [('pangu_core', Path(c['sdk']) / 'lib/libpangulu.so'), ('cudss', Path(c['cudss_library']))]:
        if digest(path) != c['dependency_sha256'][name]:
            raise ValueError('Configured dependency changed; configure and rebuild: ' + name)
    for path in c['licence']:
        if digest(path) != c['dependency_sha256']['licences'][path]:
            raise ValueError('Configured licence file changed; configure again')
    if args.trace and not c['cupti']:
        raise ValueError('Trace requested without a configured CUPTI SDK; configure --cupti and rebuild')
    if not 1 <= args.repeat <= 20:
        raise ValueError('--repeat must be 1..20')
    if len(set(args.backends)) != len(args.backends):
        raise ValueError('Specify each backend only once')
    dataset = (ROOT / 'data/matrices' / args.case).resolve(strict=True)
    label = args.label or datetime.datetime.now().strftime('%Y%m%dT%H%M%S_%f')
    if not label or Path(label).name != label or label in ['.', '..']:
        raise ValueError('--label must be a new single directory name')
    output = ROOT / 'runs' / label
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'provenance.json', {'config': c, 'build': manifest,
          'data_manifest_sha256': digest(ROOT / 'manifests/data-files.json'), 'case': args.case,
          'method': 'Same matrix and physical point load. Host conversion/upload and checks excluded; analysis includes vendor CPU work. No COMSOL/MOOSE/worker process. First physical solve after first factor; one manufactured known-solution RHS afterwards.',
          'package_source_hashes': read(ROOT / 'manifests/code-files.json')['sha256']})
    write(output / 'plan.json', [{'backend': b, 'traced': args.trace, 'repetition': r} for r in range(args.repeat) for b in args.backends])
    try:
        for repetition in range(args.repeat):
            ordered = args.backends if repetition % 2 == 0 else list(reversed(args.backends))
            for backend in ordered:
                folder = output / (backend + ('-traced' if args.trace else '-plain') + '-r' + str(repetition))
                trial(c, dataset, args.case, backend, args.trace, folder, args.trace_timeout if args.trace else args.timeout)
    finally:
        result = summarize(output)
    print('OUTPUT', output, flush=True)
    return 0 if result['status'] == 'completed' else 1


def host_control(args):
    verify()
    c = load_config()
    build_manifest = read(ROOT / 'build/manifest.json')
    if build_manifest['config_sha256'] != digest(CONFIG):
        raise ValueError('Configuration changed after build; rebuild first')
    binary = ROOT / 'build/pangu-original-host'
    if digest(binary) != build_manifest['binaries']['pangu-original-host']:
        raise ValueError('HOST control binary changed; rebuild')
    label = 'host-' + datetime.datetime.now().strftime('%Y%m%dT%H%M%S_%f')
    output = ROOT / 'runs' / label
    output.mkdir(parents=True)
    command = [str(binary), str(ROOT / 'data/matrices' / args.case)]
    value = {'command': command, 'config': c, 'status': 'running'}
    started = time.monotonic()
    try:
        with (output / 'stdout.log').open('w') as log:
            result = subprocess.run(command, env=clean_env(c, 'pangu'), stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
        text = (output / 'stdout.log').read_text()
        marker = re.search(r'HOST_RESULT passed=true relative_l2_vs_MUMPS=(\S+)', text)
        if result.returncode or not marker:
            raise ValueError('Original HOST adapter failed or completion marker is missing')
        expected = str((Path(c['sdk']) / 'lib/libpangulu.so').resolve())
        mappings = {str(Path(line.removeprefix('LOADED ')).resolve()) for line in text.splitlines() if line.startswith('LOADED ')}
        if expected not in mappings:
            raise ValueError('Missing expected original PanguLU library mapping')
        value.update(status='completed', relative_l2_vs_MUMPS=float(marker[1]),
                     native_phase_seconds={k: float(v) for k, v in re.findall(r'MOOSE_PANGULU_NATIVE phase=(\S+) seconds=(\S+) status=0', text)})
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        value.update(status='failed', error=str(error))
    finally:
        value['process_wall_seconds'] = time.monotonic() - started
        write(output / 'result.json', value)
    print(json.dumps(value, ensure_ascii=False, indent=2), '\nOUTPUT', output, flush=True)
    return 0 if value['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    config = commands.add_parser('configure', help='Set target-machine dependency paths; no downloads or installs')
    config.add_argument('--sdk', required=True)
    config.add_argument('--cuda', required=True)
    config.add_argument('--cudss-library', required=True)
    config.add_argument('--cudss-include', required=True)
    config.add_argument('--licence', action='append', required=True)
    config.add_argument('--threads', type=int, default=8)
    config.add_argument('--cxx', default='g++')
    config.add_argument('--cupti')
    config.add_argument('--library-path', action='append', default=[])
    commands.add_parser('build')
    commands.add_parser('verify')
    host = commands.add_parser('host-control', help='Replay the unmodified original HOST adapter without MOOSE')
    host.add_argument('--case', choices=['small', 'million'], default='million')
    host.add_argument('--timeout', type=float, default=180)
    replay = commands.add_parser('run')
    replay.add_argument('--case', choices=['small', 'million'], default='small')
    replay.add_argument('--backends', nargs='+', choices=BACKENDS, default=['pangu-L0', 'mycudss'])
    replay.add_argument('--repeat', type=int, default=2)
    replay.add_argument('--label')
    replay.add_argument('--trace', action='store_true')
    replay.add_argument('--timeout', type=float, default=180)
    replay.add_argument('--trace-timeout', type=float, default=300)
    args = parser.parse_args()
    try:
        if args.action == 'configure': configure(args)
        elif args.action == 'build': build()
        elif args.action == 'verify': verify()
        elif args.action == 'host-control': return host_control(args)
        else: return run(args)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('REPLAY_ERROR:', error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
