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
from pangulu_comsol import testing

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
    execution=getattr(args,'execution',None)
    if execution is None:command,env,library_root=pc.comsol_command(directory,args.threads,args.backend)
    else:command,env,library_root=pc.comsol_command(directory,args.threads,args.backend,execution)
    library_root = Path(command[command.index("-cudssroot") + 1])
    build = pc.module_dir()
    classes = Path(trial['directory']) / 'java'
    command += ['batch', '-classpathadd', str(classes) + os.pathsep + str(build / 'java'), '-nosave', '-batchlog', str(directory / 'batch.log'), '-inputfile', str(classes / 'SStudy3.class'), trial['inputs']['source']['path'], str(directory), trial['inputs']['reference']['path'], ','.join(map(str, frequencies)), '0', str(library_root), pc.cfg()['cuda_root'], 'pangu' if args.backend == 'pangu' else 'cudss']
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
                        libraries = sorted({line.split()[-1] for line in maps.splitlines() if len(line.split()) >= 6 and any(x in line for x in ('libcudss', 'libcscudss', 'libpangulu', 'libcuda.', 'libcudart', 'libcublas', 'libnvidia-ml', 'libpangu_backend'))})
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
        if args.backend=='pangu' and execution=='direct':
            for name in ('SDK_in_worker','worker_started'):checks.pop(name)
            checks['SDK_in_COMSOL']=any(Path(ev['executable']).name=='comsollauncher' and str(sdk/'lib/libpangulu.so') in ev['mapped_libraries'] for ev in state['library_evidence'].values())
            checks['Pangu_worker_absent']=not any(ev['executable']==worker for ev in state['library_evidence'].values())
            checks['direct_started']='direct_started' in stderr
            checks['shared_backend_mapped']=str((build/'libpangu_backend.so').resolve()) in paths
        checks['backend_library_mapped'] = str((library_root / 'libcudss.so.0.7.1').resolve()) in paths
        if args.backend != 'pangu':
            for name in ('adapter_mapped', 'SDK_in_worker', 'worker_started'):
                checks.pop(name)
            checks['Pangu_library_absent'] = not any('libpangulu' in p for p in paths)
            checks['Pangu_worker_absent'] = not any(ev['executable'] == worker for ev in state['library_evidence'].values())
            native = str(testing.native_library())
            expected = {native}
            if args.backend != 'cudss':
                expected.add(str((library_root / 'libcudss.so.0.7.1').resolve()))
            checks['only_expected_cudss_libraries_mapped'] = {str(Path(p).resolve()) for p in paths if Path(p).name.startswith('libcudss.so')} == expected
            test_worker = str((testing.directory() / 'worker/mycudss_worker').resolve())
            test_workers = {pid: ev for pid, ev in state['library_evidence'].items() if ev['executable'] == test_worker and native in ev['mapped_libraries']}
            state['mycudss_worker_evidence'] = test_workers
            checks['test_worker_expected'] = bool(test_workers) if args.backend == 'mycudss-worker' else not test_workers
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
