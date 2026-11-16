#!/usr/bin/env python3
"""Read and validate a saved S result without recomputing or changing the model."""
import argparse
import csv
import datetime
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
sys.path.insert(0,str(ROOT/"src/python"))
from pangulu_comsol import runtime as pc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path)
    parser.add_argument('--reference', type=Path, default=Path('/home/lwf/comsol-projects/study3-pangulu-s/models/S_full59_solved.mph'))
    parser.add_argument('--backend-observation', type=Path, help='Optional separate backend observation to associate with this saved result')
    args = parser.parse_args()
    source = args.model.resolve(strict=True)
    reference = args.reference.resolve(strict=True)
    out = ROOT/'data/runs'/('gui-result-check_'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S_%f'))
    out.mkdir()
    state = dict(status='preparing', read_only=True, recomputed=False, directory=str(out), model=str(source), reference=str(reference), started_at=datetime.datetime.now().astimezone().isoformat())
    start = time.monotonic()
    snapshot = out/'saved_snapshot.mph'

    def publish():
        pc.write(out/'result.json', state)
        pc.write(ROOT/'data/runs/latest-gui-check.json', state)

    publish()
    print('SAVED_RESULT_CHECK '+str(out), flush=True)
    try:
        before = source.stat()
        state.update(model_sha256=pc.sha(source), model_saved_at=datetime.datetime.fromtimestamp(before.st_mtime).astimezone().isoformat(), reference_sha256=pc.sha(reference))
        shutil.copy2(source, snapshot)
        state['snapshot_sha256'] = pc.sha(snapshot)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or state['snapshot_sha256'] != state['model_sha256']:
            raise RuntimeError('Model changed while creating the read-only snapshot; save and retry')
        command, env, build = pc.comsol_command(out)
        comsol = Path(pc.cfg()['comsol_root'])
        classes = out/'java'
        classes.mkdir()
        java_source = ROOT/'dev/benchmarks/s/SavedSResultCheck.java'
        code = java_source.read_text()
        if any(token in code for token in ('.runAll(', '.save(', '.apply(')):
            raise RuntimeError('Result checker must not execute a solve or save a model')
        javac = [str(comsol/'java/glnxa64/jre/bin/javac'), '-proc:none', '-classpath', str(comsol/'plugins/*')+os.pathsep+str(build/'java'), '-d', str(classes), str(java_source)]
        with (out/'javac.log').open('w') as log:
            subprocess.run(javac, stdout=log, stderr=subprocess.STDOUT, check=True)
        command += ['batch', '-classpathadd', str(classes)+os.pathsep+str(build/'java'), '-nosave', '-batchlog', str(out/'batch.log'), '-inputfile', str(classes/'SavedSResultCheck.class'), str(snapshot), str(reference)]
        state.update(status='checking', command=command, helper_sha256=pc.sha(java_source))
        publish()
        with (out/'stdout.log').open('w') as stdout, (out/'stderr.log').open('w') as stderr:
            result = subprocess.run(command, cwd=out, env=env, stdout=stdout, stderr=stderr, timeout=600)
        state['exit_code'] = result.returncode
        text = (out/'stdout.log').read_text(errors='replace')
        report = re.search(r'^SAVED_S_REPORT_JSON (.+)$', text, re.M)
        fields = re.search(r'^SAVED_S_FIELDS_CSV_BEGIN\n(.*?)^SAVED_S_FIELDS_CSV_END', text, re.M|re.S)
        if result.returncode or not report or not fields or 'SAVED_S_CHECK_COMPLETE' not in text:
            raise RuntimeError('Read-only result check did not complete; inspect batch.log/stdout.log/stderr.log')
        state['validation'] = json.loads(report[1])
        (out/'frequency_fields.csv').write_text(fields[1])
        rows = list(csv.DictReader(io.StringIO(fields[1])))
        if len(rows) != 236 or any(row['passed'] != 'true' for row in rows) or state['validation']['status'] != 'completed':
            raise RuntimeError('Saved result did not pass the full 59-frequency, 236-field acceptance')
        state['status'] = 'completed'
    except Exception as ex:
        state.update(status='failed', error=str(ex))
        print(type(ex).__name__+': '+str(ex), file=sys.stderr, flush=True)
    finally:
        state.update(wall_seconds=time.monotonic()-start, finished_at=datetime.datetime.now().astimezone().isoformat(), source_unchanged_during_check=pc.sha(source)==state.get('model_sha256'), reference_unchanged=pc.sha(reference)==state.get('reference_sha256'))
        if state['status'] == 'completed' and not state['reference_unchanged']:
            state.update(status='failed', error='Reference model changed during the check')
        if state['status'] == 'completed':
            snapshot.unlink()
        state['snapshot_retained'] = snapshot.exists()
        observation = args.backend_observation
        if observation is not None and observation.is_file():
            state['separate_GUI_backend_observation'] = str(observation)
        publish()
        v = state.get('validation', {})
        lines = ['Saved GUI S result: '+state['status'], 'Model: '+str(source), 'Model SHA256: '+str(state.get('model_sha256')), 'No recomputation; existing saved solutions read from an isolated snapshot.', 'Frequencies: '+str(v.get('frequency_count')), 'Fields passed: '+str(v.get('fields_passed'))+'/'+str(v.get('field_records')), 'Worst relative L2: '+str(v.get('worst_relative_L2')), 'Worst relative max: '+str(v.get('worst_relative_max')), 'Source unchanged: '+str(state['source_unchanged_during_check']), 'Reference unchanged: '+str(state['reference_unchanged'])]
        if state.get('error'):
            lines.append('Error: '+state['error'])
        (out/'report.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(status=state['status'], result=str(out/'result.json')), ensure_ascii=False), flush=True)
    return 0 if state['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
