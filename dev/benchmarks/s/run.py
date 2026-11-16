#!/usr/bin/env python3
"""Run S through Pangu or NVIDIA cuDSS, with the same CPU field validation."""
import argparse
import csv
import datetime
import fcntl
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
sys.path.insert(0,str(ROOT/"src/python"))
from pangulu_comsol import runtime as pc

ORIGINAL = list(range(20000, 80001, 5000)) + list(range(82000, 120001, 2000)) + list(range(125000, 250001, 5000))
FIELDS = {'comp1.p2', 'comp1.u2', 'comp1.v2', 'comp1.T2'}
LATEST = ROOT / 'data/runs/latest-s'


def now():
    return datetime.datetime.now().astimezone().isoformat()


def write(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def gpu():
    result = subprocess.run(['nvidia-smi', '--query-gpu=name,uuid,driver_version,memory.total,memory.used,memory.free,utilization.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError('nvidia-smi failed: ' + result.stderr.strip())
    values = [x.strip() for x in result.stdout.strip().splitlines()[0].split(',')]
    return dict(name=values[0], uuid=values[1], driver=values[2], total_MiB=float(values[3]), used_MiB=float(values[4]), free_MiB=float(values[5]), utilization_percent=float(values[6]))


def run_phase(trial, label, frequencies, args, publish):
    directory = Path(trial['directory']) / label
    directory.mkdir()
    command, env, library_root = pc.comsol_command(directory, args.threads, args.backend)
    build = pc.module_dir()
    classes = Path(trial['directory']) / 'java'
    command += ['batch', '-classpathadd', str(classes) + os.pathsep + str(build / 'java'), '-nosave', '-batchlog', str(directory / 'batch.log'), '-inputfile', str(classes / 'SStudy3.class'), trial['inputs']['source']['path'], str(directory), trial['inputs']['reference']['path'], ','.join(map(str, frequencies)), '0', str(library_root), pc.cfg()['cuda_root'], args.backend]
    state = dict(label=label, status='running', phase='loading', directory=str(directory), requested_frequencies_Hz=frequencies, expected_field_records=4 * len(frequencies), command=command, environment={k: v for k, v in env.items() if k.startswith(('PC_', 'PANGULU_', 'OMP_', 'MKL_', 'OPENBLAS_', 'LD_'))}, library_evidence={}, started_at=now())
    trial['runs'].append(state)
    write(directory / 'command.json', dict(command=command, environment=state['environment']))
    publish()
    start = time.monotonic()
    proc = None
    resources = []
    telemetry = []
    ticks = os.sysconf('SC_CLK_TCK')
    pages = os.sysconf('SC_PAGE_SIZE')
    seen_cpu = {}
    seen_pids = set()
    last_gpu = 0
    try:
        with (directory / 'stdout.log').open('w') as out, (directory / 'stderr.log').open('w') as err:
            proc = subprocess.Popen(command, cwd=directory, env=env, stdout=out, stderr=err, start_new_session=True)
            state['launcher_pid'] = proc.pid
            while True:
                elapsed = time.monotonic() - start
                rss = 0
                processes = {}
                for path in Path('/proc').iterdir():
                    if not path.name.isdigit():
                        continue
                    try:
                        st = (path / 'stat').read_text().rsplit(')', 1)[1].split()
                        processes[int(path.name)] = (path, st)
                    except (OSError, ValueError, IndexError):
                        continue
                selected = {pid for pid, (_, st) in processes.items() if int(st[3]) == proc.pid or pid in seen_pids}
                # Include descendants even if a launcher changes its session.
                while True:
                    children = {pid for pid, (_, st) in processes.items() if int(st[1]) in selected}
                    if children <= selected:
                        break
                    selected |= children
                seen_pids |= selected
                for pid in selected:
                    if pid not in processes:
                        continue
                    path, st = processes[pid]
                    try:
                        seen_cpu[pid] = (int(st[11]) + int(st[12])) / ticks
                        rss += int(st[21]) * pages
                        maps = (path / 'maps').read_text()
                        libraries = sorted({line.split()[-1] for line in maps.splitlines() if len(line.split()) >= 6 and any(x in line for x in ('libcudss', 'libcscudss', 'libpangulu', 'libcuda.', 'libcudart', 'libcublas', 'libnvidia-ml'))})
                        if not libraries:
                            continue
                        old = state['library_evidence'].get(str(pid), {})
                        if libraries != old.get('mapped_libraries'):
                            snapshot = directory / ('maps-' + str(pid) + '.txt')
                            snapshot.write_text(maps)
                            state['library_evidence'][str(pid)] = dict(executable=str((path / 'exe').resolve()), command=(path / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip(), mapped_libraries=libraries, maps_file=str(snapshot), observed_at=now())
                    except (OSError, ValueError, IndexError):
                        continue
                text = (directory / 'stdout.log').read_text(errors='replace')
                state['phase'] = 'validation_and_save' if 'BRIDGE_SWEEP_SOLVE_COMPLETE' in text else 'study3' if 'BRIDGE_SWEEP_BEGIN' in text else 'loading'
                log = directory / 'batch.log'
                if log.exists():
                    started = re.findall(r'Parameter freq = ([0-9.eE+\-]+)', log.read_text(errors='replace'))
                    if started:
                        state.update(current_frequency_Hz=float(started[-1]), frequency_steps_started=len(started))
                mem = {line.split(':')[0]: int(line.split(':')[1].split()[0]) for line in Path('/proc/meminfo').read_text().splitlines()}
                resources.append(dict(elapsed_seconds=elapsed, phase=state['phase'], process_RSS_MiB=rss / 2**20, process_cpu_seconds=sum(seen_cpu.values()), system_available_MiB=mem['MemAvailable'] / 1024, system_swap_used_MiB=(mem['SwapTotal'] - mem['SwapFree']) / 1024))
                if elapsed - last_gpu >= 2 or not telemetry:
                    try:
                        telemetry.append(dict(elapsed_seconds=elapsed, **gpu()))
                    except Exception as ex:
                        state['telemetry_error'] = str(ex)
                    last_gpu = elapsed
                state.update(elapsed_seconds=round(elapsed, 2), latest_resources=resources[-1], latest_gpu=telemetry[-1] if telemetry else None)
                write(directory / 'result.json', state)
                publish()
                if proc.poll() is not None:
                    break
                if elapsed > args.timeout:
                    raise TimeoutError('COMSOL exceeded --timeout seconds')
                time.sleep(1)
            state['exit_code'] = proc.wait()
        text = (directory / 'stdout.log').read_text(errors='replace')
        stderr = (directory / 'stderr.log').read_text(errors='replace')
        match = re.search(r'^BRIDGE_SWEEP_REPORT_JSON (.+)$', text, re.M)
        fields = re.search(r'^BRIDGE_SWEEP_FIELDS_CSV_BEGIN\n(.*?)^BRIDGE_SWEEP_FIELDS_CSV_END', text, re.M | re.S)
        state['completion_marker'] = 'BRIDGE_SWEEP_COMPLETE' in text
        if not match or not fields or not state['completion_marker']:
            raise RuntimeError('Missing report/field CSV/completion marker; inspect stdout.log, stderr.log and batch.log')
        state['validation'] = validation = json.loads(match[1])
        (directory / 'frequency_fields.csv').write_text(fields[1])
        rows = list(csv.DictReader(io.StringIO(fields[1])))
        state.update(field_records=len(rows), field_records_passed=sum(r['passed'] == 'true' for r in rows))
        pairs = {(float(r['frequency_Hz']), r['field']) for r in rows}
        paths = {x for ev in state['library_evidence'].values() for x in ev['mapped_libraries']}
        sdk = Path(trial['sdk']['path'])
        worker = str((build / 'pangulu_worker').resolve())
        worker_evidence = {pid: ev for pid, ev in state['library_evidence'].items() if ev['executable'] == worker and str(sdk / 'lib/libpangulu.so') in ev['mapped_libraries']}
        state['worker_evidence'] = worker_evidence
        props = validation['gpu_properties']
        hybrid = props.get('cudsshybridmemory', props.get('cudssusehybridmemory'))
        checks = dict(exit_zero=state['exit_code'] == 0, completed=state['completion_marker'], frequency_count=validation['frequency_count'] == len(frequencies), frequencies=validation['frequencies_Hz'] == frequencies, finite=validation['all_solutions_finite'] is True, CFD_unchanged=validation['CFD_unchanged'] is True, Mapping_unchanged=validation['Mapping_unchanged'] is True, CPU_reference=validation['reference_compared'] is True and validation['reference_all_fields_passed'] is True, field_records=len(rows) == 4 * len(frequencies) and state['field_records_passed'] == len(rows), field_pairs=pairs == {(float(f), name) for f in frequencies for name in FIELDS}, field_values_finite=all(math.isfinite(float(r[k])) for r in rows for k in ('solution_L2', 'peak_absolute', 'relative_L2_to_reference', 'relative_max_to_reference', 'max_absolute_error')), field_thresholds=all(float(r['relative_L2_to_reference']) <= 1e-6 and float(r['relative_max_to_reference']) <= 1e-5 for r in rows), FP64=props['cudssprecision'] == 'double', hybrid_never=hybrid == 'never', adapter_mapped=str((build / 'libcudss.so.0.7.1').resolve()) in paths, SDK_in_worker=bool(worker_evidence), worker_started='worker_started' in stderr, inputs_unchanged=all(pc.sha(item['path']) == item['sha256'] for item in trial['inputs'].values()))
        checks['backend_library_mapped'] = str((library_root / 'libcudss.so.0.7.1').resolve()) in paths
        if args.backend == 'cudss':
            for name in ('adapter_mapped', 'SDK_in_worker', 'worker_started'):
                checks.pop(name)
            checks['Pangu_library_absent'] = not any('libpangulu' in p for p in paths)
            checks['Pangu_worker_absent'] = not any(ev['executable'] == worker for ev in state['library_evidence'].values())
            checks['only_original_cudss_mapped'] = {str(Path(p).resolve()) for p in paths if Path(p).name.startswith('libcudss.so')} == {str((library_root / 'libcudss.so.0.7.1').resolve())}
        state['checks'] = checks
        for name in ('libcudart.so.12', 'libcublas.so.12', 'libcublasLt.so.12'):
            expected = str((Path(pc.cfg()['cuda_root']) / 'lib64' / name).resolve())
            mapped = {str(Path(p).resolve()) for p in paths if Path(p).name.startswith(name)}
            checks['CUDA_12_9_' + name] = mapped == {expected}
        if not all(checks.values()):
            raise RuntimeError('Acceptance failed: ' + ', '.join(k for k, v in checks.items() if not v))
        solved = directory / 'Bridge_solved.mph'
        state['solved_model'] = dict(path=str(solved), sha256=pc.sha(solved), bytes=solved.stat().st_size)
        state['status'] = 'completed'
    except BaseException as ex:
        state.update(status='failed', error=str(ex))
        raise
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        state.update(process_wall_seconds=time.monotonic() - start, finished_at=now())
        for filename, rows in [('resources.csv', resources), ('gpu_telemetry.csv', telemetry)]:
            if rows:
                with (directory / filename).open('w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
        if resources:
            state['resources_summary'] = dict(peak_process_RSS_MiB=max(x['process_RSS_MiB'] for x in resources), max_system_swap_used_MiB=max(x['system_swap_used_MiB'] for x in resources), process_cpu_seconds=resources[-1]['process_cpu_seconds'])
        if telemetry:
            state['gpu_peak_device_used_MiB'] = max(x['used_MiB'] for x in telemetry)
        write(directory / 'result.json', state)
        publish()


def main():
    global LATEST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path('/home/lwf/comsol-projects/study3-pangulu-s'))
    parser.add_argument('--frequencies', choices=['trial', '20000', 'all'], default='trial', help='trial: accept 20 kHz before running the original 59 frequencies')
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--backend', choices=['pangu', 'cudss'], default='pangu')
    parser.add_argument('--timeout', type=float, default=10800, help='Maximum seconds per COMSOL process')
    args = parser.parse_args()
    if args.backend == 'cudss':
        LATEST = ROOT / 'data/runs/latest-cudss'
    if not 1 <= args.threads <= len(os.sched_getaffinity(0)):
        parser.error('--threads exceeds available CPU affinity')
    LATEST.mkdir(parents=True, exist_ok=True)

    def interrupted(signum, frame):
        raise InterruptedError('Received signal ' + str(signum))

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    lock = (ROOT / 'data/runs/.s-gpu.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    directory = ROOT / 'data/runs' / ('s_' + datetime.datetime.now().strftime('%Y%m%dT%H%M%S_%f'))
    directory.mkdir()
    trial = dict(status='preparing', directory=str(directory), started_at=now(), requested_mode=args.frequencies, threads=args.threads, runs=[], scope='S Study 3; stored CFD/Mapping; COMSOL assembly; Pangu native licensed SDK; same physical DOFs vs saved CPU reference')
    trial['solver_backend'] = args.backend
    if args.backend == 'cudss':
        trial['scope'] = 'S Study 3; stored CFD/Mapping; original NVIDIA cuDSS 0.7.1; same physical DOFs vs saved CPU reference'
    start = time.monotonic()

    def publish():
        trial['updated_at'] = now()
        write(directory / 'result.json', trial)
        write(LATEST / 'progress.json', trial)

    publish()
    print('S_TRIAL ' + str(directory), flush=True)
    try:
        sdk = pc.sdk()
        pc.verify(sdk)
        build = pc.module_dir()
        trial['inputs'] = {name: dict(path=str(path.resolve(strict=True)), sha256=pc.sha(path)) for name, path in [('source', args.project / 'models/S_after_mapping.mph'), ('reference', args.project / 'models/S_full59_solved.mph')]}
        trial['sdk'] = pc.sdk_info(sdk)
        key = pc.licence_paths()[0]
        trial['license'] = dict(source=str(key), registered_path=str(key), sha256=pc.sha(key), original_sha256=pc.sha(key), registered_sha256=pc.sha(key))
        trial['backend'] = {name: dict(path=str(build / name), sha256=pc.sha(build / name)) for name in ['pangulu_worker', 'libcudss.so.0.7.1']}
        if args.backend == 'cudss':
            library = Path(pc.cfg()['comsol_root']) / 'ext/cudss/glnxa64/libcudss.so.0.7.1'
            trial['backend'] = {'libcudss.so.0.7.1': dict(path=str(library), sha256=pc.sha(library))}
            trial.pop('license')
            trial['sdk_role'] = 'Java helper classpath only; native run does not load the Pangu core'
        snapshot = directory / 'source_snapshot'
        for relative in ['dev/benchmarks/s/run.py', 'dev/benchmarks/s/SStudy3.java', 'src/java/PanguModule.java', 'src/python/pangulu_comsol/runtime.py', 'src/worker.c', 'src/bridge.cpp', 'src/protocol.h', 'src/pattern_cache.h', 'config/runtime.json']:
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        trial['configuration'] = dict(ordering='L0', reuse='L2/refactor', pattern_policy='strict', hybrid_memory='never', hybrid_compute='off', precision='double', cuda_root=pc.cfg()['cuda_root'], comsol_root=pc.cfg()['comsol_root'])
        if args.backend == 'cudss':
            trial['configuration'].update(ordering='cuDSS automatic', reuse='cuDSS reuse preordering', pattern_policy='native cuDSS')
        if args.backend == 'pangu' and pc.cfg()['ordering'] != 0:
            raise RuntimeError('This trial requires configured ordering=0 (L0)')
        trial['gpu_before'] = gpu()
        trial['global_preferences_before'] = {str(p): pc.sha(p) for p in (Path.home() / '.comsol/v64').glob('*.prefs')}
        classes = directory / 'java'
        classes.mkdir()
        comsol = Path(pc.cfg()['comsol_root'])
        javac = [str(comsol / 'java/glnxa64/jre/bin/javac'), '-proc:none', '-classpath', str(comsol / 'plugins/*') + os.pathsep + str(build / 'java'), '-d', str(classes), str(ROOT / 'dev/benchmarks/s/SStudy3.java')]
        trial['javac_command'] = javac
        with (directory / 'javac.log').open('w') as log:
            subprocess.run(javac, stdout=log, stderr=subprocess.STDOUT, check=True)
        trial['code_sha256'] = {str(p.relative_to(ROOT)): pc.sha(p) for p in [ROOT / 'dev/benchmarks/s/run.py', ROOT / 'dev/benchmarks/s/SStudy3.java', ROOT / 'src/java/PanguModule.java', ROOT / 'src/python/pangulu_comsol/runtime.py']}
        trial['helper_class_sha256'] = pc.sha(classes / 'SStudy3.class')
        if args.backend == 'pangu':
            env = pc.clean_env(sdk, args.threads)
            with (directory / 'native_license.log').open('w') as log:
                subprocess.run([str(build / 'native_license')], env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
            if 'NATIVE_LICENSE_OK' not in (directory / 'native_license.log').read_text():
                raise RuntimeError('Native licence completion marker missing')
        trial['status'] = 'running'
        publish()
        if args.frequencies in ('trial', '20000'):
            run_phase(trial, 'single_20kHz', [20000], args, publish)
        if args.frequencies in ('trial', 'all'):
            run_phase(trial, 'full_59', ORIGINAL, args, publish)
        trial['status'] = 'completed'
    except BaseException as ex:
        trial.update(status='failed', error=str(ex))
        print(type(ex).__name__ + ': ' + str(ex), file=sys.stderr, flush=True)
    finally:
        if 'inputs' in trial:
            trial['inputs_after'] = {name: dict(path=item['path'], sha256=pc.sha(item['path'])) for name, item in trial['inputs'].items()}
            trial['inputs_unchanged'] = trial['inputs_after'] == trial['inputs']
            if not trial['inputs_unchanged']:
                trial.update(status='failed', error='Input model SHA256 changed')
        if {'sdk', 'license', 'backend'} <= trial.keys():
            trial['integrity_after'] = dict(core_sha256=pc.sha(Path(trial['sdk']['path']) / 'lib/libpangulu.so'), original_license_sha256=pc.sha(trial['license']['source']), registered_license_sha256=pc.sha(trial['license']['registered_path']), backend={name: pc.sha(item['path']) for name, item in trial['backend'].items()})
            trial['runtime_integrity_unchanged'] = trial['integrity_after']['core_sha256'] == trial['sdk']['core_sha256'] and trial['integrity_after']['original_license_sha256'] == trial['license']['sha256'] and trial['integrity_after']['registered_license_sha256'] == trial['license']['sha256'] and all(trial['integrity_after']['backend'][name] == item['sha256'] for name, item in trial['backend'].items())
            if not trial['runtime_integrity_unchanged']:
                trial.update(status='failed', error='SDK, licence or backend SHA256 changed')
        if 'global_preferences_before' in trial:
            trial['global_preferences_unchanged'] = all(Path(p).exists() and pc.sha(p) == digest for p, digest in trial['global_preferences_before'].items())
        if 'backend' in trial:
            trial['backend_integrity_unchanged'] = all(pc.sha(item['path']) == item['sha256'] for item in trial['backend'].values())
            if not trial['backend_integrity_unchanged']:
                trial.update(status='failed', error='Backend library or executable SHA256 changed')
        trial.update(finished_at=now(), wall_seconds=time.monotonic() - start)
        publish()
        write(LATEST / 'report.json', trial)
        lines = ['S trial: ' + trial['status'], 'Directory: ' + str(directory), 'Wall seconds: ' + str(trial['wall_seconds'])]
        if trial.get('error'):
            lines.append('Error: ' + trial['error'])
        for run in trial['runs']:
            v = run.get('validation', {})
            lines += [run['label'] + ': ' + run['status'], '  Directory: ' + run['directory'], '  Requested frequencies / passed fields: ' + str(len(run['requested_frequencies_Hz'])) + ' / ' + str(run.get('field_records_passed', 0)), '  COMSOL process / runAll seconds: ' + str(run.get('process_wall_seconds')) + ' / ' + str(v.get('runAll_seconds')), '  Worst relative L2 / max: ' + str(v.get('reference_worst_relative_L2')) + ' / ' + str(v.get('reference_worst_relative_max'))]
        lines += ['Input hashes: ' + json.dumps(trial.get('inputs', {}), ensure_ascii=False), 'SDK: ' + json.dumps(trial.get('sdk', {}), ensure_ascii=False), 'Licence metadata: ' + json.dumps(trial.get('license', {}), ensure_ascii=False)]
        lines += ['GPU: ' + json.dumps(trial.get('gpu_before', {}), ensure_ascii=False), 'Backend: ' + json.dumps(trial.get('backend', {}), ensure_ascii=False), 'Inputs unchanged: ' + str(trial.get('inputs_unchanged')), 'Runtime integrity unchanged: ' + str(trial.get('runtime_integrity_unchanged')), 'Global preferences unchanged: ' + str(trial.get('global_preferences_unchanged'))]
        (LATEST / 'report.txt').write_text('\n'.join(lines) + '\n')
    print(json.dumps(dict(status=trial['status'], result=str(directory / 'result.json')), ensure_ascii=False), flush=True)
    return 0 if trial['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
